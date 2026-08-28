"""RQ1 F3：manifest / decode_config / git commit 快照单元测试。

- ut-F3-1 manifest 含全部键（模型/checkpoint/config 快照/commit/时间/prompt 模板/decode_config）。
- ut-F3-2 decode_config 含 temperature=0.7 + top_p + max_tokens + chat template + revision。
- ut-F3-3 config 快照为深拷贝（改源不影响 manifest）。
- ut-F3-4 git commit 获取失败时字段=None 优雅降级。
"""

from __future__ import annotations

import subprocess

from respfuzzer.lib.rq1 import manifest as m

LLM_CFG = {
    "model_name": "Qwen3.6-35B-A3B",
    "temperature": 0.7,
    "top_p": 1.0,
    "max_tokens": 32768,
    "timeout": 600,
}


def test_f3_manifest_has_all_keys():
    """ut-F3-1：manifest 全键存在且类型正确。"""
    man = m.build_manifest(
        mode="normal",
        library="numpy",
        runs=5,
        run_seed=42,
        k=10,
        llm_cfg=LLM_CFG,
        now="2026-08-28T00:00:00",
    )
    for key in (
        "experiment",
        "mode",
        "library",
        "runs",
        "k",
        "run_seed",
        "model",
        "revision",
        "commit",
        "created_at",
        "decode_config",
        "config_snapshot",
        "prompt_templates",
    ):
        assert key in man, f"manifest 缺键: {key}"
    assert man["experiment"] == "RQ1"
    assert man["model"] == "Qwen3.6-35B-A3B"
    assert man["revision"] == "Qwen/Qwen3.6-35B-A3B-FP8"  # ED-20 checkpoint 映射
    assert man["created_at"] == "2026-08-28T00:00:00"  # 显式时间可复现
    assert isinstance(man["config_snapshot"], dict)


def test_f3_decode_config_fields():
    """ut-F3-2：decode_config 快照全字段 + 显式温度 0.7 + revision。"""
    dec = m.build_decode_config(LLM_CFG)
    assert dec["temperature"] == 0.7
    assert dec["top_p"] == 1.0
    assert dec["max_tokens"] == 32768
    assert dec["model"] == "Qwen3.6-35B-A3B"
    assert dec["revision"] == "Qwen/Qwen3.6-35B-A3B-FP8"
    assert dec["chat_template_kwargs"] == {"enable_thinking": False}
    assert dec["reasoning"] == {"enabled": False}


def test_f3_decode_config_temperature_default_overridden():
    """ut-F3-2b：temperature 显式取自配置而不依赖服务端默认。"""
    dec = m.build_decode_config({**LLM_CFG, "temperature": 0.7})
    assert dec["temperature"] == 0.7
    # 配置异常值不崩溃（健壮性），回退 0.7
    dec2 = m.build_decode_config({"temperature": "invalid"})
    assert dec2["temperature"] == 0.7


def test_f3_config_snapshot_deep_copy():
    """ut-F3-3：改源 config 不影响已建 manifest 的深拷贝快照。"""
    src = dict(LLM_CFG)
    man = m.build_manifest(
        mode="ours",
        library="numpy",
        runs=5,
        run_seed=1,
        k=10,
        llm_cfg=LLM_CFG,
        config_snapshot=src,
    )
    src["temperature"] = 0.0  # 事后污染源
    assert man["config_snapshot"]["temperature"] == 0.7
    assert man["decode_config"]["temperature"] == 0.7
    man["config_snapshot"]["temperature"] = 999.0  # 改快照不影响其它字段
    assert man["decode_config"]["temperature"] == 0.7


def test_f3_git_commit_failure_returns_none(monkeypatch):
    """ut-F3-4：git 不可用 → commit=None 优雅降级。"""

    def _boom(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(m.subprocess, "run", _boom)
    assert m.git_head_commit() is None
    # 非零返回码同样返回 None
    monkeypatch.setattr(
        m.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="x"),
    )
    assert m.git_head_commit() is None
