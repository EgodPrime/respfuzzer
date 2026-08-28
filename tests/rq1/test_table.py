"""RQ1 F13：表格 + artifact 清单测试。

- ut-F13-1 打包清单文件齐全（manifest/decode_config/results）。
- ut-F13-2 表格脚本读聚合结果生成表（含 FC±CI + 效应量列）。
- ut-F13-3 解码设置表从 decode_config 生成（0.7/top_p/…）。
"""

from __future__ import annotations

from respfuzzer.lib.rq1.driver import run_experiment
from respfuzzer.lib.rq1.table import (
    build_report,
    check_artifacts,
)
from respfuzzer.models import Function


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return "<code>x=1</code>"


class FakeExecutor:
    def covered(self, function, code):
        return function.func_path in ("lib.f0", "lib.f2")


def _funcs() -> list[Function]:
    return [
        Function(
            func_path=f"lib.f{i}",
            library_name="lib",
            id=i + 1,
            source=f"def f{i}(): pass",
            args=[],
        )
        for i in range(4)
    ]


def _gen(tmp_path, mode: str) -> None:
    run_experiment(
        mode,
        "lib",
        runs=1,
        k=3,
        base_seed=1,
        outdir=tmp_path,
        limit=4,
        llm=FakeLLM(),
        executor=FakeExecutor(),
        functions=_funcs(),
        now="2026-08-29T00:00:00",
        allow_fake_embedding=True,
    )


def test_f13_artifacts_complete(tmp_path):
    """ut-F13-1：manifest/decode_config/results 三件套齐全（无缺失行）。"""
    _gen(tmp_path, "normal")
    assert check_artifacts(str(tmp_path), "normal", "lib") == []


def test_f13_library_row_has_ci_and_effect(tmp_path):
    """ut-F13-2：表格行含 FC / Δ±CI / Cliff's delta。"""
    _gen(tmp_path, "normal")
    _gen(tmp_path, "prompted")
    report = build_report(str(tmp_path), "lib", ["prompted"], base="normal")
    assert len(report["rows"]) == 1
    row = report["rows"][0]
    assert row["comparison"] == "normal_vs_prompted"
    for key in (
        "n_functions",
        "fc_base",
        "fc_ref",
        "delta",
        "delta_ci_low",
        "delta_ci_high",
        "cliffs_delta",
    ):
        assert key in row, f"缺键 {key}"
    assert isinstance(row["cliffs_delta"], float)
    assert row["n_functions"] >= 2


def test_f13_decode_table(tmp_path):
    """ut-F13-3：解码设置表从 decode_config 生成（temperature=0.7 + revision）。"""
    _gen(tmp_path, "normal")
    report = build_report(str(tmp_path), "lib", ["prompted"], base="normal")
    dec = report["decode_config"]
    assert dec["temperature"] == 0.7
    assert "top_p" in dec and "max_tokens" in dec
    assert dec["revision"]  # ED-20 checkpoint 映射非空


def test_f13_missing_artifact_detected(tmp_path):
    """check_artifacts 能检出缺失（人为删一个文件）。"""

    _gen(tmp_path, "normal")
    base = tmp_path / "normal" / "lib"
    run_dir = next(base.glob("r*_s*"))
    (run_dir / "results.jsonl").unlink()
    missing = check_artifacts(str(tmp_path), "normal", "lib")
    assert len(missing) == 1 and missing[0].endswith("results.jsonl")
