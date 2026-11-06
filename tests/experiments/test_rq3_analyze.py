"""MC3-3/MC3-4：RQ3 消融的函数级配对分析（``experiments/RQ3/analyze.py``）。

- ut-MC3-3-RQ3-analyze-A 载入：按 checkpoint 顶层 ``mode`` 分组；逐函数轨迹只认
  ``event == "seed_fuzzed"``（mutant 级事件不参与）；缺 run_index / 轨迹不全 /
  未识别 mode / 非 completed 一律进 Notes 而非静默丢弃。
- ut-MC3-3-RQ3-analyze-B 配对表：每个消融 vs Full 的 Δ / 95% CI / p / sig /
  Cohen's h(+CI) / d，以及 micro、macro 总体行；同一 seed 输出逐字节可复现。
- ut-MC3-4-RQ3-analyze-C ``--bootstrap hierarchical``（函数+重复两阶段）同样产出，
  且 ``--mode`` 过滤只保留目标配置的配对行。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments.RQ3 import analyze as rq3a

LIBS = ["nltk", "numpy"]
FUNCS = [f"lib.mod.func_{i}" for i in range(10)]
#: 每个配置的每函数固定增益（%）——常数差值让 Δ 与 CI 可以精确断言
GAIN_BY_MODE = {"full": 1.0, "nl": 0.5, "np": 1.5, "nsf": 1.0, "ncf": 0.0}


def _write_ckpt(
    directory: Path,
    *,
    mode: str,
    library: str,
    run_index: int,
    gain: float,
    funcs: list[str] = FUNCS,
    seed_count: int | None = None,
    with_run_index: bool = True,
    status: str = "completed",
    extra_events: int = 0,
    request_budget: int | None = None,
    time_budget: int | None = None,
    stamp: str | None = None,
) -> Path:
    """写一个形态与 experiment_recorder 一致的 checkpoint（预算字段/时间戳可注入）。"""
    init = 10_000.0
    events = [
        {
            "event": "seed_fuzzed",
            "func_name": f,
            "coverage": init * (1.0 + gain / 100.0),
        }
        for f in funcs
    ]
    # 同一次运行里可能记录的非 seed_fuzzed 事件（mutant 级），不应进轨迹
    events += [{"event": "mutant_executed", "func_name": "x", "coverage": 1.0}] * (
        extra_events
    )
    stamp = stamp or f"20260101T00000{run_index}"
    suffix = f"_mode-{mode.lower()}" if mode.lower() != "full" else ""
    path = directory / (
        f"rq3_respfuzzer_{library}_{stamp}{suffix}_checkpoint.json"
    )
    meta: dict = (
        {"run_index": run_index, "seed": 1000 + run_index, "mode": mode}
        if with_run_index
        else {"mode": mode}
    )
    # RQ2 的 src 入口会写两个预算字段（未用的那个为 null），RQ3 入口则不写
    if request_budget is not None:
        meta["request_budget"] = request_budget
    if time_budget is not None:
        meta["time_budget"] = time_budget
    payload = {
        "status": status,
        "rq": "RQ3",
        "group": "respfuzzer",
        "library": library,
        "mode": mode,
        "metadata": meta,
        "results": {
            "initial_coverage": init,
            "final_coverage": init * (1.0 + gain / 100.0),
            "seed_count": seed_count if seed_count is not None else len(funcs),
        },
        "events": events,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _fixture(directory: Path) -> None:
    """2 库 × 5 配置 × 2 次重复的完整配对样本（无随机）。"""
    for lib in LIBS:
        for mode, gain in GAIN_BY_MODE.items():
            for run_index in (1, 2):
                _write_ckpt(
                    directory,
                    mode=mode,
                    library=lib,
                    run_index=run_index,
                    gain=gain,
                    extra_events=3,
                )


def _rows(table: str) -> list[dict[str, str]]:
    """markdown 表 → 单元格字典（表头 + 各行）。"""
    lines = [
        line for line in table.splitlines()
        if line.startswith("|") and not line.startswith("|---")
    ]
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    return [
        dict(zip(header, [c.strip() for c in line.strip("|").split("|")]))
        for line in lines[1:]
    ]


def test_inventory_groups_by_mode_and_filters_events(tmp_path):
    """ut-MC3-3-RQ3-analyze-A1：mode 分组 + 只认 seed_fuzzed 事件。"""
    _fixture(tmp_path)

    inv = rq3a.load_inventory(tmp_path)

    assert set(inv.runs) == {
        (mode, lib) for mode in GAIN_BY_MODE for lib in LIBS
    }
    runs = inv.runs[("nl", "nltk")]
    assert sorted(runs) == [1, 2]
    # mutant 级事件（extra_events=3）被过滤，轨迹仍与 seed_count 对齐
    assert len(runs[1].gains) == len(FUNCS)
    assert runs[1].gains[FUNCS[0]] == pytest.approx(0.5)
    assert not [n for n in inv.notes if "轨迹" in n]


def test_bad_checkpoints_land_in_notes(tmp_path):
    """ut-MC3-3-RQ3-analyze-A2：四类坏件都显式进 Notes，不静默。"""
    _write_ckpt(
        tmp_path, mode="NL", library="nltk", run_index=1, gain=0.5,
        with_run_index=False,
    )
    _write_ckpt(
        tmp_path, mode="NP", library="nltk", run_index=2, gain=1.5,
        seed_count=99,
    )
    _write_ckpt(
        tmp_path, mode="NSF", library="nltk", run_index=3, gain=1.0,
        status="failed",
    )
    _write_ckpt(
        tmp_path, mode="NL,NP", library="nltk", run_index=4, gain=0.5,
    )

    inv = rq3a.load_inventory(tmp_path)

    notes = "\n".join(inv.notes)
    assert "无 run_index 配对键" in notes
    assert "seed_fuzzed 轨迹" in notes
    assert "status=failed" in notes
    assert "无法识别消融配置" in notes
    # 四个坏件一个都没进 runs
    assert inv.runs == {}


def test_paired_table_rows_and_reproducible(tmp_path, capsys):
    """ut-MC3-3-RQ3-analyze-B：Δ/CI/sig/h 与 micro/macro 行，同 seed 可复现。"""
    _fixture(tmp_path)
    inv = rq3a.load_inventory(tmp_path)

    rq3a.table_paired(inv, boot=1000, seed=42, hierarchical=False)
    first = capsys.readouterr().out
    rq3a.table_paired(inv, boot=1000, seed=42, hierarchical=False)
    second = capsys.readouterr().out
    assert first == second  # 固定 seed → 逐字节可复现

    rows = _rows(first)
    by_key = {(r["pair (A−B)"], r["lib"]): r for r in rows}

    nl_nltk = by_key[("NL−Full", "nltk")]
    assert nl_nltk["mode"] == "NL"
    assert nl_nltk["n func"] == str(len(FUNCS))
    assert nl_nltk["n run"] == "2 (1,2)"
    assert nl_nltk["gain_A %"] == "+0.50"
    assert nl_nltk["gain_B %"] == "+1.00"
    assert nl_nltk["Δ pp"] == "-0.50"
    # 常数差值 → bootstrap CI 塌缩成同一点
    low, high = nl_nltk["Δ 95% CI"].strip("[]").split(", ")
    assert float(low) == float(high) == -0.50
    assert nl_nltk["sig"] == "*"
    assert nl_nltk["p"] == "<0.001" or float(nl_nltk["p"]) < 0.05
    assert nl_nltk["Cohen's h"].startswith("-")
    assert nl_nltk["h 幅度"] in {"small", "medium", "large"}

    # NP 增益高于 Full → Δ 为正；NCF 低于 Full → 为负
    assert by_key[("NP−Full", "numpy")]["Δ pp"] == "+0.50"
    assert by_key[("NCF−Full", "numpy")]["Δ pp"] == "-1.00"

    # micro / macro 总体行
    micro = by_key[("NL−Full", "micro (2 libs)")]
    macro = by_key[("NL−Full", "macro (2 libs)")]
    assert micro["Δ pp"] == "-0.50"
    assert macro["Δ pp"] == "-0.50"
    assert micro["sig"] == "*"


def test_hierarchical_and_mode_filter(tmp_path, capsys):
    """ut-MC3-4-RQ3-analyze-C：两阶段重采样可用；--mode 只留目标配对行。"""
    _fixture(tmp_path)
    inv = rq3a.load_inventory(tmp_path)

    rq3a.table_paired(inv, boot=500, seed=7, hierarchical=True)
    hierarchical = capsys.readouterr().out
    assert "函数+重复两阶段" in hierarchical
    assert _rows(hierarchical)  # 行照常产出

    rq3a.table_paired(inv, boot=500, seed=7, hierarchical=False, only="nl")
    filtered = capsys.readouterr().out

    pairs = {r["pair (A−B)"] for r in _rows(filtered)}
    assert pairs == {"NL−Full"}


def test_descriptive_table_reports_every_mode(tmp_path, capsys):
    """表 1：每个 (mode, lib) 一行，含 n runs / final cov / gain %。"""
    _fixture(tmp_path)
    inv = rq3a.load_inventory(tmp_path)

    rq3a.table_descriptive(inv)
    out = capsys.readouterr().out

    rows = _rows(out)
    assert len(rows) == len(GAIN_BY_MODE) * len(LIBS)
    nl = next(r for r in rows if r["mode"] == "NL" and r["lib"] == "nltk")
    assert nl["n runs"] == "2"
    assert nl["gain % (mean±sd)"] == "+0.50±0.00"




def test_duplicate_run_index_prefers_count_budget(tmp_path):
    """ut-MC3-3-RQ3-analyze-D：同键重复按预算择一——count/无预算让位给谁要对。"""
    # Full 组：t60 件文件名更晚（模拟 RQ2 目录里后跑的 -ltime campaign），仍须让位
    _write_ckpt(
        tmp_path, mode="Full", library="nltk", run_index=1, gain=1.0,
        request_budget=10, stamp="20260101T070000",
    )
    _write_ckpt(
        tmp_path, mode="Full", library="nltk", run_index=1, gain=9.0,
        time_budget=60, stamp="20260101T090000",
    )
    # NL 组：RQ3 自己的入口（无预算字段，rank=1）也不该被 t60 件（rank=2）挤掉
    _write_ckpt(
        tmp_path, mode="NL", library="nltk", run_index=1, gain=0.5,
        stamp="20260101T060000",
    )
    _write_ckpt(
        tmp_path, mode="NL", library="nltk", run_index=1, gain=0.7,
        time_budget=60, stamp="20260101T100000",
    )

    inv = rq3a.load_inventory(tmp_path)

    assert inv.runs[("full", "nltk")][1].gains[FUNCS[0]] == pytest.approx(1.0)
    assert inv.runs[("nl", "nltk")][1].gains[FUNCS[0]] == pytest.approx(0.5)
    notes = "\n".join(inv.notes)
    assert "duplicate ('full', 'nltk') run 1" in notes
    assert "request=10" in notes
    assert "time 预算（无 request）" in notes
    assert "duplicate ('nl', 'nltk') run 1" in notes
    # 择一之后每键只剩一个 run，不会把两次运行当成两次重复
    assert sorted(inv.runs[("full", "nltk")]) == [1]




def test_load_inventory_merges_multiple_dirs(tmp_path, capsys):
    """ut-MC3-3-RQ3-analyze-E：多目录装载——Full 臂在 RQ2 目录、消融臂在本目录，能配对。"""
    rq2_dir = tmp_path / "rq2"
    rq3_dir = tmp_path / "rq3"
    rq2_dir.mkdir()
    rq3_dir.mkdir()
    for run_index in (1, 2):
        _write_ckpt(
            rq2_dir, mode="Full", library="nltk", run_index=run_index, gain=1.0,
            request_budget=10, stamp=f"20260101T07000{run_index}",
        )
        _write_ckpt(
            rq3_dir, mode="NL", library="nltk", run_index=run_index, gain=0.5,
            stamp=f"20260101T08000{run_index}",
        )

    inv = rq3a.load_inventory([rq2_dir, rq3_dir])

    assert set(inv.runs) == {("full", "nltk"), ("nl", "nltk")}
    assert sorted(inv.runs[("full", "nltk")]) == [1, 2]

    rq3a.table_paired(inv, boot=200, seed=42, hierarchical=False)
    out = capsys.readouterr().out
    rows = {(r["pair (A−B)"], r["lib"]): r for r in _rows(out)}
    nl = rows[("NL−Full", "nltk")]
    assert nl["n run"] == "2 (1,2)"  # 两个目录的运行按 run_index 对齐成一对
    assert nl["Δ pp"] == "-0.50"
