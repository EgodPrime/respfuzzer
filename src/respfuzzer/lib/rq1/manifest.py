"""RQ1 F3：run 级 manifest / decode_config / git commit 快照。

等比预算实验（ED-4）要求每次 run 的元数据可复现：模型的 checkpoint 修订版本
（ED-20）、decode 设置（ED-13/15，temperature=0.7 + chat template + revision）、
config 全量深拷贝快照、git commit 标注（G24）与随机种子（F2）。
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import subprocess
from pathlib import Path
from typing import Any

from respfuzzer.utils.config import get_config

# ED-20 定稿：三模型 checkpoint 修订版本（ER-21 依 config [llm].model_name 匹配）。
MODEL_CHECKPOINTS: dict[str, str] = {
    "Llama-3.1-8B-Instruct": "RedHatAI/Meta-Llama-3.1-8B-Instruct-FP8",
    "Qwen3.6-35B-A3B": "Qwen/Qwen3.6-35B-A3B-FP8",
    "gpt-oss-120b": "openai-mirror/gpt-oss-120b",
}

# decode_config 固定项：关闭 thinking 的 chat template、禁推理（与各 LLM 调用点一致）。
_CHAT_TEMPLATE_KWARGS: dict[str, Any] = {"enable_thinking": False}
_REASONING: dict[str, Any] = {"enabled": False}
_TOP_P_DEFAULT = 1.0


def _as_float(value: Any, default: float) -> float:
    """配置值转 float；无效/缺失时回退默认（build_decode_config 健壮性）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    """配置值转 int；无效/缺失时回退默认。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def git_head_commit(repo_root: str | Path | None = None) -> str | None:
    """当前 git commit 短号；git 不可用/无仓库时返回 None（优雅降级，ut-F3-4）。"""
    try:
        if repo_root is not None:
            out = subprocess.run(
                ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        else:
            out = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=30,
            )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def build_decode_config(llm_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """decode_config 快照：model/temperature/top_p/max_tokens/chat template/revision。

    temperature 显式取 0.7（RQ1 F4 决策），不依赖服务端默认（ut-F3-2）。
    """
    cfg = dict(llm_cfg) if llm_cfg else dict(get_config("llm") or {})
    model = str(cfg.get("model_name") or "")
    return {
        "model": model,
        "temperature": _as_float(cfg.get("temperature", 0.7), 0.7),
        "top_p": _as_float(cfg.get("top_p", _TOP_P_DEFAULT), _TOP_P_DEFAULT),
        "max_tokens": _as_int(cfg.get("max_tokens", 32768), 32768),
        "chat_template_kwargs": dict(_CHAT_TEMPLATE_KWARGS),
        "reasoning": dict(_REASONING),
        "revision": MODEL_CHECKPOINTS.get(model, model),
    }


def build_manifest(
    *,
    mode: str,
    library: str | None,
    runs: int,
    run_seed: int,
    k: int,
    llm_cfg: dict[str, Any] | None = None,
    config_snapshot: dict[str, Any] | None = None,
    prompt_templates: dict[str, str] | None = None,
    repo_root: str | Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """构建 run 级 manifest（全键，ut-F3-1）；config 快照为深拷贝（ut-F3-3）。"""
    cfg = dict(llm_cfg) if llm_cfg else dict(get_config("llm") or {})
    decode = build_decode_config(cfg)
    base_snapshot = (
        config_snapshot
        if config_snapshot is not None
        else dict(get_config("llm") or {})
    )
    return {
        "experiment": "RQ1",
        "mode": mode,
        "library": library,
        "runs": runs,
        "k": k,
        "run_seed": run_seed,
        "model": cfg.get("model_name", ""),
        "revision": decode["revision"],
        "commit": git_head_commit(repo_root),
        "created_at": now or _dt.datetime.now().isoformat(timespec="seconds"),
        "decode_config": decode,
        "config_snapshot": copy.deepcopy(base_snapshot),
        "prompt_templates": dict(prompt_templates or {}),
    }


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    """写 manifest JSON（build_manifest 的伴生落盘，供 driver 调用）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
