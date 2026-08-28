"""RQ1 F8：RAG 索引与检索测试。

- ut-F8-1 索引构建（每库一个；语料=函数源码，跳过空源）。
- ut-F8-2 检索工具 top-k 返回片段（score 降序、id 合法）。
- ut-F8-3 LLM 检索接线：search_text 可注入 prompt <retrieval>。
- ut-F8-4 检索失败（embedding 抖动）→ 优雅退化空注入（不崩）。
- ut-F8-5 纯 Python 库同建索引（不退化 prompted）。
- it-F8 allow_fake=False 且 [embedding] 未配置 → 显式 RuntimeError（非静默）。
"""

from __future__ import annotations

import pytest

from respfuzzer.lib.rq1.rag_index import (
    FakeEmbeddingProvider,
    HTTPEmbeddingProvider,
    RAGIndex,
    make_embedding_provider,
)
from respfuzzer.models import Function


def _fns() -> list[Function]:
    return [
        Function(func_path=f"lib.mod.f{i}", library_name="lib", args=[], source=src)
        for i, src in enumerate(
            [
                "def f0(x):\n    return x + 1",
                "def f1(x):\n    return x * 2",
                "def f2(x):\n    return x - 1",
                "",  # 空源应被跳过
            ]
        )
    ]


def test_f8_index_built_per_library():
    """ut-F8-1：每库一索引，语料=非空函数源码（空源跳过）。"""
    idx = RAGIndex.from_functions(_fns(), FakeEmbeddingProvider())
    assert len(idx) == 3
    for fn in _fns()[:3]:
        assert fn.func_path in idx._ids


def test_f8_search_topk_sorted():
    """ut-F8-2：top-k 检索按 score 降序、id 合法。"""
    idx = RAGIndex.from_functions(_fns(), FakeEmbeddingProvider())
    res = idx.search("lib.mod.f0 的源码:\ndef f0(x):\n    return x + 1", top_k=2)
    assert len(res) == 2
    scores = [r["score"] for r in res]
    assert scores == sorted(scores, reverse=True)
    assert res[0]["id"] == "lib.mod.f0"  # 与 query 最相似的 chunk 命中最前
    for r in res:
        assert r["id"] in idx._ids


def test_f8_search_text_injectable():
    """ut-F8-3：search_text 产出可注入 <retrieval> 的文本片段（接线）。"""
    idx = RAGIndex.from_functions(_fns(), FakeEmbeddingProvider())
    text = idx.search_text("lib.mod.f0 的源码:\ndef f0(x):\n    return x + 1", top_k=1)
    assert "lib.mod.f0 的源码" in text
    assert "return x + 1" in text


def test_f8_search_failure_degrades_to_empty():
    """ut-F8-4：检索异常（embedding 抖动）→ 空串注入，不崩。"""

    class FlakyProvider:
        def __init__(self):
            self.fail_query = False

        def embed(self, texts):
            if self.fail_query and len(texts) == 1:  # 仅查询向量触发失败
                raise RuntimeError("embedding 服务不可用")
            return FakeEmbeddingProvider().embed(texts)

    flaky = FlakyProvider()
    idx = RAGIndex.from_functions(_fns(), flaky)
    flaky.fail_query = True
    assert idx.search_text("anything") == ""


def test_f8_pure_python_library_indexed_not_degraded():
    """ut-F8-5：纯 Python 库同样建索引（不退化 prompted）。"""
    idx = RAGIndex.from_functions(_fns(), FakeEmbeddingProvider())
    assert len(idx) > 0  # 有源码即建索引，无"纯 Python 退化"分支


def test_f8_missing_embedding_config_raises():
    """it-F8：正式运行（allow_fake=False）且 [embedding] 未配置 → 显式报错。"""
    with pytest.raises(RuntimeError, match="未配置"):
        make_embedding_provider(cfg={}, allow_fake=False)


def test_f8_configured_embedding_ok_even_no_fake():
    """allow_fake=False 但 cfg 已配置 → 返回 HTTP provider（带真实/mock 接线，不抛）。"""
    provider = make_embedding_provider(
        cfg={"base_url": "http://x", "model_name": "m"}, allow_fake=False
    )
    assert isinstance(provider, HTTPEmbeddingProvider)  # 不再降级 fake


def test_f8_dev_default_fake(monkeypatch):
    """开发默认（allow_fake=True）且 embedding 未配置 → fake provider + 不抛。"""
    monkeypatch.setattr(
        "respfuzzer.lib.rq1.rag_index.get_config", lambda section: {}
    )
    assert isinstance(
        make_embedding_provider(cfg=None, allow_fake=True), FakeEmbeddingProvider
    )
