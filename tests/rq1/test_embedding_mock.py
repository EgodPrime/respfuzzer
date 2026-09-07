"""RQ1 F8：mock embedding 服务 + HTTPEmbeddingProvider 端到端测试。

- 本地 EmbeddingMockServer（OpenAI 兼容 /v1/embeddings）可编程启动/停止；
- HTTPEmbeddingProvider 经 HTTP 获取确定性向量，与 FakeEmbeddingProvider 同算法比对；
- 端到端：mock server → HTTP provider → RAGIndex → search 闭环；
- config [embedding] 指向 mock 时 make_embedding_provider 返回 HTTP provider；
- 服务不可达 → HTTP provider 抛异常（上层 search_text 退化空注入）。
"""

from __future__ import annotations

import pytest
import openai

from respfuzzer.lib.rq1.embedding_mock import EmbeddingMockServer
from respfuzzer.lib.rq1.rag_index import (
    FakeEmbeddingProvider,
    HTTPEmbeddingProvider,
    RAGIndex,
    make_embedding_provider,
)
from respfuzzer.models import Function


@pytest.fixture
def server():
    srv = EmbeddingMockServer(port=0)
    srv.start()
    yield srv
    srv.stop()


def _provider(server, timeout=10.0) -> HTTPEmbeddingProvider:
    return HTTPEmbeddingProvider(
        base_url=server.base_url(),
        model_name="mock-embedding",
        timeout=timeout,
    )


def test_mock_server_returns_vectors(server):
    """HTTP provider 对 mock server 请求 → 向量数与维度正确。"""
    p = _provider(server)
    vecs = p.embed(["hello world", "another text"])
    assert len(vecs) == 2
    assert all(len(v) == 64 for v in vecs)


def test_http_provider_deterministic(server):
    """同文本 → 同向量（确定性，可复现）。"""
    p = _provider(server)
    assert p.embed(["same text"]) == p.embed(["same text"])


def test_http_provider_matches_fake(server):
    """mock 与 fake 同算法 → 向量一致（交叉验证）。"""
    http_p = _provider(server)
    fake_p = FakeEmbeddingProvider()
    text = "def f(x): return x + numpy\n矩阵分解 det"
    a = http_p.embed([text])[0]
    b = fake_p.embed([text])[0]
    assert pytest.approx(a, abs=1e-5) == b


def test_http_to_rag_search_end_to_end(server):
    """端到端：mock → provider → 索引 → 检索闭环。"""
    funcs = [
        Function(func_path=f"lib.f{i}", library_name="lib", args=[], source=src)
        for i, src in enumerate(
            [
                "def f0(x): return x + 1",
                "def f1(x): return x * 2",
                "def f2(x): return x - 1",
            ]
        )
    ]
    idx = RAGIndex.from_functions(funcs, _provider(server))
    res = idx.search("lib.f0 的源码:\ndef f0(x): return x + 1", top_k=2)
    assert len(res) == 2
    text = idx.search_text("lib.f0 的源码:\ndef f0(x): return x + 1", top_k=1)
    assert "lib.f0 的源码" in text


def test_make_provider_http_when_configured(server):
    """config [embedding] 指向 mock → HTTP provider（非 fake 降级）。"""
    p = make_embedding_provider(
        cfg={"base_url": server.base_url(), "model_name": "mock"}, allow_fake=False
    )
    assert isinstance(p, HTTPEmbeddingProvider)
    assert len(p.embed(["x"])) == 1


def test_http_provider_unreachable_raises():
    """服务不可达 → HTTP provider 抛异常（上层 search_text 会退化）。"""
    p = HTTPEmbeddingProvider(
        base_url="http://127.0.0.1:1", model_name="mock", timeout=0.5
    )
    with pytest.raises(openai.OpenAIError):  # openai 网络错误家族（连接被拒）
        p.embed(["x"])


def test_driver_rag_end_to_end_with_mock(server, tmp_path):
    """driver rag 模式注入 mock provider → 完整 run 产物（冒烟级端到端）。"""
    from respfuzzer.lib.rq1.driver import merge_results, run_experiment
    from respfuzzer.lib.rq1.table import check_artifacts

    funcs = [
        Function(
            func_path=f"lib.f{i}",
            library_name="lib",
            id=i + 1,
            source=f"def f{i}(): pass",
            args=[],
        )
        for i in range(3)
    ]

    class FakeLLM:
        def chat(self, messages, **kwargs):
            return "<code>x=1</code>"

    class FakeExecutor:
        def covered(self, function, code):
            return function.func_path == "lib.f1"

    run_experiment(
        "rag",
        "lib",
        runs=1,
        k=3,
        base_seed=1,
        outdir=tmp_path,
        llm=FakeLLM(),
        executor=FakeExecutor(),
        functions=funcs,
        embedding_provider=_provider(server),
        now="2026-08-29T00:00:00",
        warmup=False,  # 测试零网络 fake
    )
    assert check_artifacts(str(tmp_path), "rag", "lib") == []
    rows = merge_results(str(tmp_path), "rag", "lib")
    assert rows and rows[0]["mode"] == "rag"
    assert "covered" in rows[0]  # 经 mock 检索注入后的 runner 落盘
