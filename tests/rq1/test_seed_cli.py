"""RQ1 CLI（``rq1seed``）产物契约与资源汇总测试。

- ut-MC3-1：一次运行必须落 ``rq1_<mode>_<library>_<ts>.log`` + 同 stem 的
  ``*_checkpoint.json``，且 checkpoint 带 status / metadata / results。
- ut-MC4-3：各 run manifest 的 usage（调用次数 + token）汇总进 checkpoint。
- fc_passk：pass@k 口径的 FC（任一候选覆盖记 1，再对函数×run 取均值）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from respfuzzer.cmd import rq1_seed


def _write_manifest(run_dir: Path, usage: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"mode": "rag", "library": "lib", "usage": usage}),
        encoding="utf-8",
    )


def _row(func_name: str, candidate_index: int, covered: bool) -> dict:
    return {
        "run_id": "rag_lib_r1_s1",
        "mode": "rag",
        "library": "lib",
        "func_name": func_name,
        "seed_id": 1,
        "candidate_index": candidate_index,
        "code": "pass" if covered else None,
        "covered": covered,
    }


def test_usage_totals_sums_run_manifests(tmp_path):
    """MC4-3：跨 run 汇总 calls / tokens，并保留 per_run 明细。"""
    _write_manifest(
        tmp_path / "rag" / "lib" / "r1_s1",
        {
            "calls": 4,
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "total_tokens": 140,
        },
    )
    _write_manifest(
        tmp_path / "rag" / "lib" / "r2_s2",
        {
            "calls": 6,
            "prompt_tokens": 120,
            "completion_tokens": 50,
            "total_tokens": 170,
        },
    )
    out = rq1_seed._usage_totals(str(tmp_path), "rag", "lib")
    assert out["total"]["calls"] == 10
    assert out["total"]["total_tokens"] == 310
    assert [row["run"] for row in out["per_run"]] == ["r1_s1", "r2_s2"]


def test_usage_totals_without_manifests_is_zero(tmp_path):
    """run 未跑完（无 manifest）→ 全 0，不抛。"""
    out = rq1_seed._usage_totals(str(tmp_path), "rag", "lib")
    assert out["total"] == {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    assert out["per_run"] == []


def test_main_writes_log_and_checkpoint(tmp_path, monkeypatch):
    """MC3-1：rq1seed 主流程落 .log + checkpoint，且写入 FC / 并发 / 解码设置。"""
    rows = [
        _row("lib.f0", 1, True),
        _row("lib.f0", 2, False),
        _row("lib.f1", 1, False),
    ]
    monkeypatch.setattr(rq1_seed, "RQ1_DIR", tmp_path / "logs")
    monkeypatch.setattr(
        rq1_seed, "run_experiment", lambda *a, **k: tmp_path / "runs"
    )
    monkeypatch.setattr(rq1_seed, "merge_results", lambda *a, **k: rows)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rq1seed",
            "--mode",
            "rag",
            "--library",
            "lib",
            "--runs",
            "1",
            "--k",
            "2",
            "--outdir",
            str(tmp_path / "runs"),
        ],
    )

    rq1_seed.main()

    logs = sorted((tmp_path / "logs").glob("rq1_rag_lib_*.log"))
    checkpoints = sorted((tmp_path / "logs").glob("rq1_rag_lib_*_checkpoint.json"))
    assert len(logs) == 1, f"应产出唯一 .log：{logs}"
    assert len(checkpoints) == 1, f"应产出唯一 checkpoint：{checkpoints}"

    cp = json.loads(checkpoints[0].read_text(encoding="utf-8"))
    assert cp["rq"] == "RQ1"
    assert cp["group"] == "rag"
    assert cp["library"] == "lib"
    assert cp["status"] == "completed"
    assert cp["results"]["row_count"] == 3
    assert cp["results"]["function_count"] == 2
    assert cp["results"]["fc_passk"] == 0.5  # f0 覆盖 1，f1 未覆盖 0
    assert cp["results"]["concurrency"] == 32
    assert cp["metadata"]["k"] == 2  # 等预算入参留痕【MC6-5】
    assert cp["metadata"]["mode"] == "rag"
    assert "temperature" in cp["metadata"]["decode_config"]  # 解码设置固化【MC3-5】
    assert cp["event_counts"].get("run_completed") == 1
