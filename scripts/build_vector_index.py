#!/usr/bin/env python3
"""为 data/<lib>_source 构建持久化向量索引 → data/<lib>_vector_storage。

用法: uv run python scripts/build_vector_index.py <lib>

与 respfuzzer.lib.retriever 的默认构建等价，但为长期批量构建补了护栏：
  * 只读白名单扩展名——torch/scipy 源码树里的二进制测试数据会炸掉解码；
  * 显式 timeout=180 / max_retries=1——OpenAILikeEmbedding 默认 60s×10 重试，
    单个卡住的请求最坏吃 10 分钟；
  * batch=8 + num_workers=4 + use_async（部署方 4 路并发要求）；
  * show_progress=True 原生 tqdm + 逐请求日志（钩子挂在真正发请求的
    _get/_aget_text_embeddings 上）+ 单请求失败退化逐条、仍失败用零向量兜底；
  * 半成品目录（vector_store 0 字节）先删再重建。
"""

import shutil
import sys
import time
from pathlib import Path
from typing import ClassVar

from llama_index.core import (
    Settings,
    SimpleDirectoryReader,
    StorageContext,
    VectorStoreIndex,
    load_index_from_storage,
)
from llama_index.embeddings.openai_like import OpenAILikeEmbedding

import respfuzzer.lib.retriever  # noqa: F401  以 cfg 的 api_base/api_key 初始化 Settings
from respfuzzer.utils.config import get_config
from respfuzzer.utils.paths import DATA_DIR

cfg = get_config("embedding")
BATCH = 8
WORKERS = 4
DIMS = 4096  # Qwen3-Embedding-8B

# 源码文本白名单：其余（.so/.png/.bin/…）不进语料
REQUIRED_EXTS = [
    ".py", ".pyx", ".pxd", ".pxi", ".pyi", ".typed",
    ".c", ".cc", ".cpp", ".h", ".hh", ".hpp",
    ".md", ".rst", ".txt", ".cfg", ".toml", ".yaml", ".yml", ".in", ".jinja",
]


class LoggedEmbedding(OpenAILikeEmbedding):
    """带进度日志与失败兜底的 embed model（同步/异步两条路径都覆盖）。"""

    req_done: ClassVar[int] = 0
    texts_done: ClassVar[int] = 0
    failed_reqs: ClassVar[int] = 0
    zero_fallbacks: ClassVar[int] = 0
    t0: ClassVar[float] = time.time()

    def _log(self, ok: bool, n: int, dt: float) -> None:
        cls = type(self)
        cls.req_done += 1
        if ok:
            cls.texts_done += n
        print(
            f"[req#{cls.req_done}] {'ok ' if ok else 'ERR'} n={n} {dt:.1f}s "
            f"done={cls.texts_done} elapsed={time.time() - cls.t0:.0f}s",
            flush=True,
        )

    def _get_text_embeddings(self, texts):
        start = time.time()
        try:
            out = super()._get_text_embeddings(texts)
        except Exception as exc:
            type(self).failed_reqs += 1
            self._log(False, len(texts), time.time() - start)
            print(f"[warn] sync req failed: {type(exc).__name__}: {str(exc)[:140]}", flush=True)
            out = []
            for text in texts:
                try:
                    out.append(super()._get_text_embeddings([text])[0])
                except Exception as exc2:
                    type(self).zero_fallbacks += 1
                    print(f"[warn] single text failed -> zero vector ({len(text)} chars): {type(exc2).__name__}", flush=True)
                    out.append([0.0] * DIMS)
        else:
            self._log(True, len(texts), time.time() - start)
        return out

    async def _aget_text_embeddings(self, texts):
        start = time.time()
        try:
            out = await super()._aget_text_embeddings(texts)
        except Exception as exc:
            type(self).failed_reqs += 1
            self._log(False, len(texts), time.time() - start)
            print(f"[warn] async req failed: {type(exc).__name__}: {str(exc)[:140]}", flush=True)
            out = []
            for text in texts:
                try:
                    out.append((await super()._aget_text_embeddings([text]))[0])
                except Exception as exc2:
                    type(self).zero_fallbacks += 1
                    print(f"[warn] single text failed -> zero vector ({len(text)} chars): {type(exc2).__name__}", flush=True)
                    out.append([0.0] * DIMS)
        else:
            self._log(True, len(texts), time.time() - start)
        return out


def build(lib: str) -> int:
    vector_path = DATA_DIR / f"{lib}_vector_storage"
    source_path = DATA_DIR / f"{lib}_source"
    store_file = vector_path / "default__vector_store.json"

    if store_file.is_file() and store_file.stat().st_size > 0:
        print(f"[skip ] {lib}: index already built ({store_file.stat().st_size} bytes)", flush=True)
        return 0
    if vector_path.exists():  # 0 字节半成品
        print(f"[clean] {lib}: removing stale dir {vector_path}", flush=True)
        shutil.rmtree(vector_path)
    if not source_path.is_dir():
        print(f"[FAIL ] {lib}: source {source_path} missing", flush=True)
        return 2

    Settings.embed_model = LoggedEmbedding(
        model_name=cfg.get("model_name"),
        api_base=cfg.get("base_url"),
        api_key=cfg.get("api_key"),
        embed_batch_size=BATCH,
        timeout=180.0,
        max_retries=1,
        num_workers=WORKERS,
    )

    t0 = time.time()
    documents = SimpleDirectoryReader(
        str(source_path), recursive=True, required_exts=REQUIRED_EXTS
    ).load_data()
    chars = sum(len(d.text) for d in documents)
    print(f"[info] {lib}: {len(documents)} docs, {chars / 1e6:.1f}M chars loaded in {time.time() - t0:.1f}s", flush=True)

    storage_context = StorageContext.from_defaults()
    index = VectorStoreIndex.from_documents(
        documents,
        storage_context=storage_context,
        show_progress=True,   # 原生 tqdm：分块 + embedding 批次进度
        use_async=True,       # aget_text_embedding_batch + num_workers=4
        insert_batch_size=2048,
    )
    print(f"[info] {lib}: index built in {time.time() - t0:.0f}s", flush=True)

    t1 = time.time()
    index.storage_context.persist(persist_dir=str(vector_path))
    print(f"[info] {lib}: persisted in {time.time() - t1:.0f}s -> {vector_path}", flush=True)
    print(
        f"[info] {lib}: nodes={len(index.docstore.docs)} requests={LoggedEmbedding.req_done} "
        f"texts={LoggedEmbedding.texts_done} failed_reqs={LoggedEmbedding.failed_reqs} "
        f"zero_fallbacks={LoggedEmbedding.zero_fallbacks}",
        flush=True,
    )

    reloaded = load_index_from_storage(StorageContext.from_defaults(persist_dir=str(vector_path)))
    hits = reloaded.as_retriever(similarity_top_k=1).retrieve(f"{lib} API usage example")
    snippet = hits[0].node.get_content()[:120] if hits else "<no hits>"
    print(f"[check] {lib}: reload ok, nodes={len(reloaded.docstore.docs)}, sample={snippet!r}", flush=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: build_vector_index.py <lib>", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(build(sys.argv[1]))
