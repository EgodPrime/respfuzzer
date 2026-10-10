"""MC6-5：先跑 RespFuzzer 记录 LLM 调用次数，再以相同次数驱动其余对照组。

覆盖四件事：
  1. **每函数归因**：`usage_bucket()` 把调用记到当前函数头上，串行/并发都精确，
     且各函数之和 == run 的 usage（对账恒等式）。
  2. **budget.json 落盘**：每次 run 写每函数调用明细 + 汇总，manifest 同步 `budget`。
  3. **等预算驱动**：对照组按 `function_budget` 逐函数拿到与 ours 相同的 k；
     预算为 0 的函数落占位行（配对完整性不被破坏）；预算缺函数时回退全局 k 并计 miss。
  4. **编排**：`--mc6-5` 先 ours、后对照组，预算从 ours 的 budget.json 读出并传递，
     最后写出等预算计划 json。
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from experiments.RQ1 import rq1_seed
from experiments.RQ1.engine.counting import UsageCounter
from experiments.RQ1.engine.driver import merge_results, run_experiment
from experiments.RQ1.engine.seeding import derive_run_seed
from respfuzzer.models import Function

_NOW = "2026-08-29T00:00:00"


class _FakeLLM:
    """每次 chat 都记账的假 LLM（模拟 CountingLLM 的 usage 上报）。"""

    def __init__(self):
        self.counter = UsageCounter()
        self.prompts: list[str] = []

    def chat(self, messages, **kwargs):
        self.prompts.append(messages[-1]["content"])
        self.counter.record(
            SimpleNamespace(
                usage=SimpleNamespace(
                    prompt_tokens=11, completion_tokens=7, total_tokens=18
                )
            )
        )
        return "<code>y=1</code>"


class _FakeExecutor:
    def __init__(self, covered_sequence=None):
        self.seq = list(covered_sequence or [])
        self.judged = []

    def covered(self, function, code):
        self.judged.append(code)
        return bool(self.seq.pop(0)) if self.seq else False  # 默认不覆盖 → 跑满 k


def _funcs() -> list[Function]:
    return [
        Function(
            func_name=f"lib.mod.f{i}",
            library_name="lib",
            id=i + 1,
            source=f"def f{i}(): pass",
            args=[],
        )
        for i in range(4)
    ]


def _run(tmp_path, mode, *, runs=1, k=3, seed=1, concurrency=1, budget=None, limit=None):
    llm = _FakeLLM()
    ex = _FakeExecutor()
    out = run_experiment(
        mode,
        "lib",
        runs=runs,
        k=k,
        base_seed=seed,
        outdir=tmp_path,
        limit=limit,
        llm=llm,
        executor=ex,
        functions=_funcs(),
        now=_NOW,
        warmup=False,
        concurrency=concurrency,
        function_budget=budget,
    )
    return out, llm


# ---------------------------------------------------------------- 1. 归因


def test_budget_json_records_per_function_calls(tmp_path):
    """每次 run 落 budget.json：每函数 calls == k，总额 == k×函数数 == usage。"""
    out, _ = _run(tmp_path, "normal", runs=2, k=3, seed=1)
    run_dirs = sorted(out.glob("r*_s*"))
    assert len(run_dirs) == 2
    for run_dir in run_dirs:
        payload = json.loads((run_dir / "budget.json").read_text(encoding="utf-8"))
        assert payload["mode"] == "normal"
        assert payload["budget_source"] == "k"
        assert payload["budget_misses"] == 0
        assert len(payload["per_function"]) == 4
        assert payload["total_llm_calls"] == 4 * 3
        for func_name, usage in payload["per_function"].items():
            assert usage["calls"] == 3, func_name
            assert usage["total_tokens"] == 3 * 18

        # 对账：budget 明细之和 == manifest 的 run 内 usage 增量
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["usage"]["calls"] == payload["total_llm_calls"]
        assert manifest["budget"]["total_llm_calls"] == payload["total_llm_calls"]
        assert manifest["budget"]["source"] == "k"


def test_budget_attribution_is_exact_under_concurrency(tmp_path):
    """并发跑时每函数归因不串账（ContextVar 按线程隔离），且总额仍与 usage 对账。"""
    out, llm = _run(tmp_path, "normal", runs=1, k=4, seed=2, concurrency=3)
    run_dir = next(out.glob("r*_s*"))
    payload = json.loads((run_dir / "budget.json").read_text(encoding="utf-8"))
    assert {u["calls"] for u in payload["per_function"].values()} == {4}
    assert payload["total_llm_calls"] == 16
    assert llm.counter.calls == 16


# ------------------------------------------------------------ 2. 等预算驱动


def test_control_driven_by_recorded_budget(tmp_path):
    """对照组逐函数拿到与 ours 相同的 k（0 预算落占位行，配对不丢）。"""
    expected = {"lib.mod.f0": 2, "lib.mod.f1": 1, "lib.mod.f2": 0, "lib.mod.f3": 3}
    budget = {
        str(derive_run_seed(7, run_index)): dict(expected) for run_index in (1, 2)
    }
    out, _ = _run(tmp_path, "prompted", runs=2, k=99, seed=7, budget=budget)

    rows = merge_results(tmp_path, "prompted", "lib")
    per_run_func = Counter((r["run_id"], r["func_name"]) for r in rows)
    # 每个 run × 每个函数的候选数 == 预算（k=99 不生效，证明被逐函数覆盖）
    for (run_id, func_name), count in per_run_func.items():
        assert count == max(expected[func_name], 1), (run_id, func_name, count)

    # 零预算函数：占位行（covered=False）而不是从分母里消失
    f2_rows = [r for r in rows if r["func_name"] == "lib.mod.f2"]
    assert len(f2_rows) == 2  # 两个 run 各一行占位
    assert all(r["covered"] is False for r in f2_rows)

    for run_dir in sorted(out.glob("r*_s*")):
        payload = json.loads((run_dir / "budget.json").read_text(encoding="utf-8"))
        assert payload["budget_source"] == "function"
        assert payload["budget_misses"] == 0
        assert payload["total_llm_calls"] == sum(expected.values())  # 6


def test_budget_miss_falls_back_to_global_k(tmp_path):
    """预算缺该函数 → 回退全局 k 并计 miss（不静默、不把函数剔出分母）。"""
    budget = {str(derive_run_seed(3, 1)): {"lib.mod.f0": 2}}
    out, _ = _run(tmp_path, "normal", runs=1, k=5, seed=3, budget=budget)
    run_dir = next(out.glob("r*_s*"))
    payload = json.loads((run_dir / "budget.json").read_text(encoding="utf-8"))
    assert payload["budget_misses"] == 3  # f1/f2/f3 未命中
    counts = {f: u["calls"] for f, u in payload["per_function"].items()}
    assert counts["lib.mod.f0"] == 2
    assert counts["lib.mod.f1"] == 5 and counts["lib.mod.f3"] == 5  # 回退全局 k


# -------------------------------------------------------------- 3. 预算读取


def test_load_function_budget_roundtrip(tmp_path):
    """从落盘的 budget.json 还原 {run_seed: {func: calls}}（按 run_seed 跨模式配对）。"""
    _run(tmp_path, "normal", runs=2, k=2, seed=11)
    budget = rq1_seed._load_function_budget(str(tmp_path), "normal", "lib")
    assert len(budget) == 2
    for run_index in (1, 2):
        key = str(derive_run_seed(11, run_index))
        assert key in budget
        assert budget[key] == {f"lib.mod.f{i}": 2 for i in range(4)}
    assert rq1_seed._budget_total(str(tmp_path), "normal", "lib") == 2 * 4 * 2


# ---------------------------------------------------------------- 4. 编排


def test_run_mc6_5_order_and_budget_propagation(tmp_path, monkeypatch):
    """--mc6-5：ours 先跑，读它的 budget.json 后再把预算传给每个对照组。"""
    seen: list[tuple] = []

    def fake_run_mode(args, mode, *, function_budget=None, budget_source=None):
        seen.append((mode, budget_source, function_budget))
        if mode == "ours":
            run_dir = Path(args.outdir) / "ours" / "lib" / "r1_s123"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "budget.json").write_text(
                json.dumps(
                    {
                        "run_seed": 123,
                        "per_function": {"lib.f0": {"calls": 4}},
                        "total_llm_calls": 4,
                    }
                ),
                encoding="utf-8",
            )
        return {
            "mode": mode,
            "llm_calls": 4,
            "attributed_llm_calls": 4,
            "assigned_llm_calls": 4,
            "fc_passk": 0.5,
        }

    monkeypatch.setattr(rq1_seed, "_run_mode", fake_run_mode)
    args = Namespace(
        controls="normal,rag",
        library="lib",
        outdir=str(tmp_path),
        runs=1,
        seed=42,
        limit=None,
        k=10,
        concurrency=4,
    )

    plan = rq1_seed.run_mc6_5(args)

    assert [entry[0] for entry in seen] == ["ours", "normal", "rag"]
    assert seen[0][1] is None and seen[0][2] is None  # ours 不吃预算
    for entry in seen[1:]:
        assert entry[1] == "ours"
        assert entry[2] == {"123": {"lib.f0": 4}}
    assert plan["budget_total_llm_calls"] == 4
    assert plan["equal_budget"] is True
    assert plan["budget_per_run"]["123"]["llm_calls"] == 4

    plan_files = list(Path(args.outdir).glob("mc65_plan_lib_*.json"))
    assert len(plan_files) == 1
    on_disk = json.loads(plan_files[0].read_text(encoding="utf-8"))
    assert on_disk["budget_total_llm_calls"] == 4
    assert [m["mode"] for m in on_disk["modes"]] == ["ours", "normal", "rag"]


def test_parse_controls_rejects_bad_modes():
    assert rq1_seed._parse_controls("normal, rag") == ("normal", "rag")
    for bad in ("ours", "nope", ""):
        try:
            rq1_seed._parse_controls(bad)
        except SystemExit:
            continue
        raise AssertionError(f"应拒绝 controls={bad!r}")


def test_main_budget_from_reads_recorded_budget(tmp_path, monkeypatch):
    """单模式 + --budget-from：从 ours 的 budget.json 取预算，并写进 checkpoint。"""
    run_dir = tmp_path / "runs" / "ours" / "lib" / "r1_s1"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "budget.json").write_text(
        json.dumps(
            {"run_seed": 123, "per_function": {"lib.f0": {"calls": 3}}}
        ),
        encoding="utf-8",
    )

    captured: dict = {}

    def fake_run_experiment(mode, library, **kwargs):
        captured.update(kwargs)
        captured["mode"] = mode
        return Path(kwargs["outdir"]) / mode / "lib"

    monkeypatch.setattr(rq1_seed, "run_experiment", fake_run_experiment)
    monkeypatch.setattr(rq1_seed, "merge_results", lambda *a, **k: [])
    monkeypatch.setattr(rq1_seed, "RQ1_DIR", tmp_path / "logs")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rq1seed",
            "--mode",
            "prompted",
            "--library",
            "lib",
            "--outdir",
            str(tmp_path / "runs"),
            "--budget-from",
            "ours",
            "--runs",
            "1",
            "--k",
            "9",
        ],
    )

    rq1_seed.main()

    assert captured["mode"] == "prompted"
    assert captured["function_budget"] == {"123": {"lib.f0": 3}}
    checkpoints = list((tmp_path / "logs").glob("rq1_prompted_lib_*_checkpoint.json"))
    assert len(checkpoints) == 1
    cp = json.loads(checkpoints[0].read_text(encoding="utf-8"))
    assert cp["metadata"]["budget_source"] == "ours"
    assert cp["results"]["budget_source"] == "ours"
