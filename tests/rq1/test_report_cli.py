"""RQ1 MC6-1 报告入口（``rq1_report.py``）测试。

- ut-MC6-1-F 多角度报表 CLI：落 json 报表 + ``rq1_report_*`` .log/checkpoint【MC3-1】，
  并按 ``--dimensions`` 只报选定维度。
- ut-MC6-1-G 无任何 run 数据时非零退出，报表里标注 ``modes_without_data``。
"""

from __future__ import annotations

import json

from experiments.RQ1 import rq1_report
from experiments.RQ1.engine.driver import run_experiment
from respfuzzer.models import Function


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return "<code>x=1</code>"


class FakeExecutor:
    def covered(self, function, code):
        return function.func_name in ("lib.f0", "lib.f2")


def _func(i: int, **tags) -> Function:
    fn = Function(
        func_name=f"lib.f{i}",
        library_name="lib",
        id=i + 1,
        source=f"def f{i}(): pass",
        args=[],
    )
    for key, value in tags.items():
        setattr(fn, key, value)
    return fn


def _funcs() -> list[Function]:
    return [
        _func(
            0,
            implementation_type="pure-python",
            docstring_quality="with-docstring",
            type_annotation_completeness="完整注解",
        ),
        _func(
            1,
            implementation_type="extended",
            docstring_quality="without-docstring",
            type_annotation_completeness="不完整注解",
        ),
    ]


def _gen(tmp_path, mode: str) -> None:
    run_experiment(
        mode,
        "lib",
        runs=1,
        k=3,
        base_seed=1,
        outdir=tmp_path,
        limit=2,
        llm=FakeLLM(),
        executor=FakeExecutor(),
        functions=_funcs(),
        now="2026-08-29T00:00:00",
        allow_fake_embedding=True,
        warmup=False,
    )


def _run(outdir, monkeypatch, *extra) -> int:
    """跑一次报告入口：日志目录改到 tmp，outdir 由调用方给定。"""
    monkeypatch.setattr(rq1_report, "RQ1_DIR", outdir / "logs")
    return rq1_report.main(["--outdir", str(outdir), "--library", "lib", *extra])

def test_mc6_1_report_cli_writes_json_and_checkpoint(tmp_path, monkeypatch):
    """ut-MC6-1-F：报表 json + .log + checkpoint 齐全，分层行按维度产出。"""
    runs = tmp_path / "runs"
    _gen(runs, "ours")
    _gen(runs, "normal")

    assert _run(runs, monkeypatch, "--modes", "normal") == 0

    logs = list((runs / "logs").glob("rq1_report_lib_*.log"))
    checkpoints = list((runs / "logs").glob("rq1_report_lib_*_checkpoint.json"))
    assert len(logs) == 1 and len(checkpoints) == 1
    cp = json.loads(checkpoints[0].read_text(encoding="utf-8"))
    assert cp["rq"] == "RQ1" and cp["group"] == "report"
    assert cp["status"] == "completed"
    assert cp["results"]["n_layer_rows"] > 0
    assert cp["results"]["n_labeled_functions"] == 2

    reports = list(runs.glob("rq1_report_lib_*.json"))
    assert len(reports) == 1
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    report = payload["report"]
    assert report["comparison"] == "ours_vs_normal"
    dims = {row["dimension"] for row in report["layer_rows"]}
    assert dims == {
        "implementation_type",
        "docstring_quality",
        "type_annotation_completeness",
    }
    assert report["layer_composition"]["implementation_type"]["counts"]["pure-python"] == 1


def test_mc6_1_report_cli_dimension_filter(tmp_path, monkeypatch):
    """ut-MC6-1-F：``--dimensions`` 只报选定维度。"""
    runs = tmp_path / "runs"
    _gen(runs, "ours")
    _gen(runs, "normal")
    assert (
        _run(
            runs,
            monkeypatch,
            "--modes",
            "normal",
            "--dimensions",
            "docstring_quality",
        )
        == 0
    )
    report = json.loads(
        next((runs).glob("rq1_report_lib_*.json")).read_text(encoding="utf-8")
    )["report"]
    assert {row["dimension"] for row in report["layer_rows"]} == {"docstring_quality"}


def test_mc6_1_report_cli_no_runs_exits_nonzero(tmp_path, monkeypatch):
    """ut-MC6-1-G：没有任何 run 数据 → 非零退出 + modes_without_data 留痕。"""
    assert _run(tmp_path / "runs", monkeypatch, "--modes", "normal") == 1
    report = json.loads(
        next((tmp_path / "runs").glob("rq1_report_lib_*.json")).read_text(
            encoding="utf-8"
        )
    )["report"]
    assert set(report["modes_without_data"]) == {"ours", "normal"}
    assert report["rows"][0]["n_functions"] == 0


def test_mc6_1_report_cli_rebuilds_missing_labels(tmp_path, monkeypatch, capsys):
    """标签数据缺失时入口先补生成（data/ 无 lib 库 → 说明一句、不抛）。"""
    assert _run(tmp_path / "runs", monkeypatch, "--modes", "normal") == 1
    assert "函数池为空" in capsys.readouterr().out
