"""RQ1 F4：温度统一 0.7 的静态回归闸 + llm_helper 默认值测试。

- ut-F4-1 静态扫描：RQ1 种子生成链路的所有 `chat.completions.create` 调用点
  都必须显式携带 `temperature`（缺省即失败）。范围：agentic_function_resolver
  （RSG 的 Attempter/Reasoner/Judger）+ RQ1/LLM_testcases 旧脚本 + 未来的
  `respfuzzer/lib/rq1/*`。RQ3/RQ4 的 fuzz 段（llm_mutator）不在本闸范围
  （属 ER-21 重跑批次，评论见 llm_mutator.py F4 说明）。
- ut-F4-2 llm_helper.SimpleLLMClient 默认 temperature=0.7，chat() 缺省时注入。
- ut-F4-5 调用方显式传非 0.7 时被尊重（不覆盖自定义 kwargs）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from respfuzzer.utils.llm_helper import SimpleLLMClient

_REPO_ROOT = Path(__file__).resolve().parents[2]

# RQ1 种子生成链路文件（含未来的 lib/rq1/*，新增模块时追加）。
RQ1_SOURCE_FILES = [
    _REPO_ROOT / "src/respfuzzer/lib/agentic_function_resolver.py",
    _REPO_ROOT / "experiments/RQ1/LLM_testcases/generate_tests_from_apis.py",
    _REPO_ROOT / "experiments/RQ1/LLM_testcases/generate_tests_from_apis_by_api.py",
]


def _is_chat_completions_create(call: ast.Call) -> bool:
    """匹配 `xxx.chat.completions.create(...)` 调用。"""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "create":
        return False
    parent = func.value
    if not isinstance(parent, ast.Attribute) or parent.attr != "completions":
        return False
    return True


def _call_exposes_temperature(call: ast.Call) -> bool:
    return any(kw.arg == "temperature" for kw in call.keywords)


def test_f4_static_all_rq1_call_sites_explicit_temperature():
    """ut-F4-1：RQ1 链路所有 create 调用必须显式 temperature。"""
    checked = 0
    for path in RQ1_SOURCE_FILES:
        assert path.exists(), f"扫描文件缺失: {path}"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_chat_completions_create(node):
                checked += 1
                assert _call_exposes_temperature(node), (
                    f"{path.name}:{node.lineno} "
                    "chat.completions.create 未显式 temperature（RQ1 F4 要求 0.7）"
                )
    # 至少扫到 3 处（agentic 的 Attempter/Reasoner/Judger），防止路径失效静默通过
    assert checked >= 3, f"扫描到的 create 调用过少（{checked}），检查 RQ1_SOURCE_FILES"


class _FakeCompletions:
    """模拟 client.chat.completions：.create(**kwargs) 记录调用。"""

    def __init__(self, captured: list):
        self._captured = captured

    def create(self, **kwargs):
        self._captured.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
        )


class _FakeOpenAI:
    """模拟 openai.OpenAI：client.chat.completions.create。"""

    def __init__(self, captured: list):
        self.chat = SimpleNamespace(completions=_FakeCompletions(captured))


@pytest.fixture
def fake_client():
    captured: list = []
    client = SimpleLLMClient(
        api_key="test-key", base_url="http://fake.local/v1", model_name="test-model"
    )
    client.client = _FakeOpenAI(captured)  # 替换真实客户端，零网络
    return client, captured


def test_f4_llmhelper_default_temperature(fake_client):
    """ut-F4-2：SimpleLLMClient 默认 temperature=0.7，chat() 缺省注入。"""
    client, captured = fake_client
    client.chat([{"role": "user", "content": "hi"}])
    assert captured and captured[0]["temperature"] == 0.7


def test_f4_llmhelper_explicit_temperature_respected(fake_client):
    """ut-F4-5：调用方显式传非 0.7 时被尊重（不覆盖自定义 kwargs）。"""
    client, captured = fake_client
    client.chat([{"role": "user", "content": "hi"}], temperature=0.0)
    assert captured and captured[0]["temperature"] == 0.0


def test_f4_legacy_scripts_no_nonzero_temperature_literal():
    """ut-F4-4：旧 LLM_testcases 脚本硬编码温度已统一为 0.7（不允许 0.0/0.2 字面量）。"""
    scripts = [
        _REPO_ROOT / "experiments/RQ1/LLM_testcases/generate_tests_from_apis.py",
        _REPO_ROOT / "experiments/RQ1/LLM_testcases/generate_tests_from_apis_by_api.py",
    ]
    for path in scripts:
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r'"temperature"\s*:\s*([\d.]+)', text):
            assert float(m.group(1)) == 0.7, (
                f"{path.name} temperature={m.group(1)} 应为 0.7"
            )
