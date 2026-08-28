"""RQ1 F8：RAG 索引与检索（ED-14b 实质）。

约定（作者拍板，RQ1_plan §3）：
  - 每库一个索引；语料 = 该库全部函数（data/*_functions.json）的已提取源码；
    纯 Python 库同样建索引（**不退化**为 prompted，ut-F8-5）。
  - embedding 服务来自 config [embedding]（可指向本地 mock：embedding_mock，或真实服务）；
    开发/测试期用确定性 fake embedding（同文本同向量，可注入）。
  - RAG 模式 = 检索增强生成：以「函数签名+docstring」为 query 检索 top-k 源码
    片段、注入 prompt <retrieval> 后再生成（agentic retrieval 的 pipeline
    近似；LLM 主动检索的工具调用由上游保留扩展点）。

错误策略：
  - 正式运行（allow_fake=False）且 [embedding] 未配置 → 显式 RuntimeError
    （it-F8：非静默）；
  - 单次检索异常（embedding 服务抖动）→ 优雅退化为空片段注入（ut-F8-4）。
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Sequence
from typing import Any, Protocol

from respfuzzer.models import Function
from respfuzzer.repos import get_functions
from respfuzzer.utils.config import get_config

logger = logging.getLogger(__name__)

_DIM = 64
_DEFAULT_TOP_K = 3
_CHUNK_MAX_CHARS = 2000


class EmbeddingProvider(Protocol):
    """embedding 服务接口：texts → 归一化向量。"""

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def _stable_vec(text: str) -> list[float]:
    """确定性哈希向量：同文本同向量，保证测试与可复现（fake 专用）。"""
    vec = [0.0] * _DIM
    for i, ch in enumerate(text):
        h = int.from_bytes(
            hashlib.sha256(f"{ch}{i % 16}".encode()).digest()[:2],
            "big",  # noqa: S324 — fake embedding，非安全场景
        )
        vec[i % _DIM] += (h / 65535.0) - 0.5
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FakeEmbeddingProvider:
    """确定性假 embedding（仅开发/测试；生产由真实服务替换）。"""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [_stable_vec(t) for t in texts]




class HTTPEmbeddingProvider:
    """OpenAI 兼容 /v1/embeddings HTTP provider（指向本地 mock 或真实服务）。

    基于项目已有的 openai 库；batch_size 分批控制单请求大小。
    连接失败/非 200 由 openai 客户端抛异常（上层 search_text 退化处理）。
    """

    def __init__(
        self,
        base_url: str,
        model_name: str,
        api_key: str = "",
        batch_size: int = 64,
        timeout: float = 30.0,
    ) -> None:
        from openai import OpenAI  # 延迟导入（避免模块顶层依赖）

        self._client = OpenAI(
            api_key=api_key or "sk-mock",
            base_url=base_url,
            timeout=timeout,
        )
        self._model = model_name
        self._batch_size = max(1, int(batch_size))

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = list(texts[i:i + self._batch_size])
            resp = self._client.embeddings.create(model=self._model, input=batch)
            ordered = sorted(resp.data, key=lambda d: getattr(d, "index", 0))
            out.extend([list(map(float, d.embedding)) for d in ordered])
        return out
def make_embedding_provider(
    cfg: dict[str, Any] | None = None, *, allow_fake: bool = True
) -> EmbeddingProvider:
    """按 config [embedding] 构建 provider；未配置/未接线时按策略处理。

    - allow_fake=True（开发/测试）：返回确定性 FakeEmbeddingProvider + 警告；
    - allow_fake=False（正式 run）且 [embedding] 未给出 base_url/model_name：
      显式 RuntimeError（it-F8，「非静默」）。
    """
    cfg = dict(cfg) if cfg is not None else dict(get_config("embedding") or {})
    if cfg.get("base_url") and cfg.get("model_name"):
        # 真实 HTTP 接线：可指向本地 mock（embedding_mock）或真实 embedding 服务
        return HTTPEmbeddingProvider(
            base_url=str(cfg["base_url"]),
            model_name=str(cfg["model_name"]),
            api_key=str(cfg.get("api_key", "")),
            batch_size=int(cfg.get("batch_size", 64)),
        )
    if not allow_fake:
        raise RuntimeError(
            "config [embedding] 未配置 base_url/model_name，无法构建 RAG 索引"
        )
    logger.warning(
        "config [embedding] 未配置，使用确定性 fake embedding（仅开发/测试）"
    )
    return FakeEmbeddingProvider()


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


class RAGIndex:
    """单库索引：chunk（函数源码）→ 向量，支持 top-k 余弦检索。"""

    def __init__(
        self,
        provider: EmbeddingProvider,
        chunks: list[str],
        ids: list[str],
    ) -> None:
        self._provider = provider
        self._chunks = list(chunks)
        self._ids = list(ids)
        self._vectors = provider.embed(self._chunks)

    @classmethod
    def from_functions(
        cls, functions: Sequence[Function], provider: EmbeddingProvider
    ) -> RAGIndex:
        """语料 = 全部函数的已提取源码（跳过空源），chunk id = func_path。"""
        chunks: list[str] = []
        ids: list[str] = []
        for fn in functions:
            src = (fn.source or "").strip()
            if not src:
                continue
            chunks.append(f"{fn.func_path} 的源码:\n{src[:_CHUNK_MAX_CHARS]}")
            ids.append(fn.func_path)
        return cls(provider, chunks, ids)

    # --- 检索 ---

    def search(self, query: str, top_k: int = _DEFAULT_TOP_K) -> list[dict[str, Any]]:
        """top-k 最相似片段（按 score 降序）。"""
        qv = self._provider.embed([query])[0]
        scored = [
            (_cosine(qv, v), cid)
            for cid, v in zip(self._ids, self._vectors, strict=False)
        ]
        scored.sort(reverse=True)
        return [{"id": cid, "score": round(s, 4)} for s, cid in scored[:top_k]]

    def search_text(self, query: str, top_k: int = _DEFAULT_TOP_K) -> str:
        """返回可注入 <retrieval> 的文本片段；检索异常 → 空串（ut-F8-4 退化）。"""
        try:
            results = self.search(query, top_k=top_k)
        except Exception as exc:  # embedding 服务抖动 → 优雅退化
            logger.warning("RAG 检索失败，退化为空注入: %s", exc)
            return ""
        parts = []
        for r in results:
            try:
                idx = self._ids.index(r["id"])
            except ValueError:
                continue
            parts.append(self._chunks[idx])
        return "\n---\n".join(parts)

    def __len__(self) -> int:
        return len(self._ids)


def index_for_library(
    library_name: str,
    provider: EmbeddingProvider | None = None,
    *,
    allow_fake: bool = True,
    functions: Sequence[Function] | None = None,
) -> RAGIndex:
    """构建指定库的 RAG 索引（每库一个；纯 Python 库同建，不退化）。"""
    if provider is None:
        provider = make_embedding_provider(allow_fake=allow_fake)
    if functions is None:
        functions = get_functions(library_name)
    return RAGIndex.from_functions(functions, provider)
