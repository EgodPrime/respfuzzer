"""MC6-5：先跑 RespFuzzer 记录它用了几轮，再让其余对照组尝试相同轮次。

**口径（作者拍板）**：对一个 API，其他对照组的尝试次数 = ours 成功时所用的自省轮次；
ours 耗尽预算时 = ``--k``。**RQ1 不对齐 LLM 调用次数**（一轮自省含多次调用），
调用数/token 只作资源记录。

覆盖：
  1. **轮次记账**：每次 run 落 ``budget.json``（``unit: attempts``），含每函数
     ``attempts`` + 资源明细，且 LLM 调用数与 run 的 usage 对账。
  2. **归因**：`usage_bucket()` 把调用记到当前函数头上，串行/并发都精确。
  3. **等轮次驱动**：对照组逐函数拿到与 ours 相同的尝试轮次；0 轮落占位行
     （配对完整性）；预算缺函数回退全局 k 并计 miss。
  4. **编排**：`--mc6-5` 先 ours、后对照组，预算从 ours 的 budget.json 读出并传递，
     最后写出等预算计划 json（unit=attempts）。
"""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from experiments.RQ1 import rq1_seed
from experiments.RQ1.engine import driver as driver_mod
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


def _math_funcs() -> list[Function]:
    """可导入的目标函数（ours 路径要过 _target_module_importable）。"""
    names = ["math.sqrt", "math.cos", "math.sin", "math.tan"]
    return [
        Function(func_name=nm, library_name="math", id=i + 1, source="", args=[])
        for i, nm in enumerate(names)
    ]


def _run(
    tmp_path,
    mode,
    *,
    runs=1,
    k=3,
    seed=1,
    concurrency=1,
    budget=None,
    limit=None,
    functions=None,
):
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
        functions=functions if functions is not None else _funcs(),
        now=_NOW,
        warmup=False,
        concurrency=concurrency,
        function_budget=budget,
    )
    return out, llm


def _budget_payload(out: Path) -> dict:
    run_dir = next(out.glob("r*_s*"))
    return json.loads((run_dir / "budget.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------- 1. 轮次记账


def test_budget_json_records_per_function_attempts(tmp_path):
    """每次 run 落 budget.json（unit=attempts）：每函数 attempts == k == 调用数。"""
    out, _ = _run(tmp_path, "normal", runs=2, k=3, seed=1)
    run_dirs = sorted(out.glob("r*_s*"))
    assert len(run_dirs) == 2
    for run_dir in run_dirs:
        payload = json.loads((run_dir / "budget.json").read_text(encoding="utf-8"))
        assert payload["mode"] == "normal"
        assert payload["unit"] == "attempts"  # 单位是轮次，不是调用次数
        assert payload["budget_source"] == "k"
        assert payload["budget_misses"] == 0
        assert len(payload["per_function"]) == 4
        assert payload["assigned_attempts"] == 4 * 3
        assert payload["total_attempts"] == 4 * 3
        assert payload["total_llm_calls"] == 4 * 3
        for func_name, rec in payload["per_function"].items():
            assert rec["attempts"] == 3, func_name
            assert rec["calls"] == 3, func_name  # 对照组：一轮候选 = 一次调用
            assert rec["total_tokens"] == 3 * 18

        # 资源对账：每函数调用数之和 == manifest 的 run 内 usage 增量
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["usage"]["calls"] == payload["total_llm_calls"]
        assert manifest["budget"]["unit"] == "attempts"
        assert manifest["budget"]["total_attempts"] == payload["total_attempts"]
        assert manifest["budget"]["assigned_attempts"] == payload["assigned_attempts"]


def test_budget_attribution_is_exact_under_concurrency(tmp_path):
    """并发跑时每函数归因不串账（ContextVar 按线程隔离），总额仍与 usage 对账。"""
    out, llm = _run(tmp_path, "normal", runs=1, k=4, seed=2, concurrency=3)
    payload = _budget_payload(out)
    assert {rec["attempts"] for rec in payload["per_function"].values()} == {4}
    assert payload["total_attempts"] == 16
    assert payload["total_llm_calls"] == 16
    assert llm.counter.calls == 16


# --------------------------------------------------- 2. ours 轮次驱动对照组


def test_ours_rounds_drive_controls_not_llm_calls(tmp_path, monkeypatch):
    """核心口径：对照组的尝试次数 = ours 的自省轮次（成功轮 / 耗尽预算轮）。

    用假 solve 制造两种情形：f0 第 2 轮成功、其余函数耗尽预算（==k）。
    ours 全程不发真实 LLM 调用（calls==0），预算却按 attempts 传递——
    证明驱动量是**轮次**而不是调用次数【MC6-5 口径修正】。
    """

    def fake_solve(function, budget=10, stats=None):
        rounds = 2 if function.func_name == "math.sqrt" else budget
        if stats is not None:
            stats.update(
                {
                    "rounds": rounds,
                    "budget": budget,
                    "budget_left": budget - rounds,
                    "solved": True,
                    "exhausted": rounds == budget,
                }
            )
        return "<code>x=1</code>"

    monkeypatch.setattr(driver_mod.agentic, "solve", fake_solve)
    funcs = _math_funcs()

    # 1) 跑 ours（k=5）
    out_ours, _ = _run(tmp_path, "ours", runs=1, k=5, seed=5, functions=funcs)
    ours = _budget_payload(out_ours)
    attempts = {f: rec["attempts"] for f, rec in ours["per_function"].items()}
    assert attempts["math.sqrt"] == 2  # 成功 → 成功那一轮
    assert all(attempts[f] == 5 for f in ("math.cos", "math.sin", "math.tan"))
    assert ours["total_attempts"] == 2 + 5 * 3
    # ours 没有真实调用：预算不是按调用数算出来的
    assert ours["total_llm_calls"] == 0

    # 2) 读预算 → 驱动对照组（k=99 不得生效）
    budget = rq1_seed._load_function_budget(str(tmp_path), "ours", "lib")
    assert budget == {
        str(derive_run_seed(5, 1)): {
            "math.sqrt": 2,
            "math.cos": 5,
            "math.sin": 5,
            "math.tan": 5,
        }
    }
    out_ctl, _ = _run(
        tmp_path, "prompted", runs=1, k=99, seed=5, budget=budget, functions=funcs
    )
    rows = merge_results(tmp_path, "prompted", "lib")
    counts = Counter(r["func_name"] for r in rows)
    assert counts["math.sqrt"] == 2  # 成功轮次
    assert all(counts[f] == 5 for f in ("math.cos", "math.sin", "math.tan"))

    ctl = _budget_payload(out_ctl)
    assert ctl["unit"] == "attempts"
    assert ctl["budget_source"] == "function"
    assert ctl["assigned_attempts"] == ours["total_attempts"]  # 分配 == ours 轮次
    assert ctl["total_attempts"] <= ctl["assigned_attempts"]  # 短路可少用
    assert ctl["budget_misses"] == 0


def test_zero_budget_keeps_pairing(tmp_path):
    """ours 该函数 0 轮（如目标不可导入）→ 对照组也 0 轮，落占位行不丢分母。"""
    budget = {str(derive_run_seed(7, 1)): {"lib.mod.f0": 0, "lib.mod.f1": 2}}
    out, _ = _run(tmp_path, "normal", runs=1, k=9, seed=7, budget=budget)
    payload = _budget_payload(out)
    assert payload["per_function"]["lib.mod.f0"]["attempts"] == 0
    assert payload["per_function"]["lib.mod.f1"]["attempts"] == 2
    assert payload["per_function"]["lib.mod.f2"]["attempts"] == 9  # 缺预算 → 回退 k
    assert payload["budget_misses"] == 2  # f0 命中 0、f2/f3 未命中 → 只有 f2/f3 miss

    rows = merge_results(tmp_path, "normal", "lib")
    f0_rows = [r for r in rows if r["func_name"] == "lib.mod.f0"]
    assert len(f0_rows) == 1  # 占位行存在（配对不破）
    assert f0_rows[0]["covered"] is False


def test_budget_miss_falls_back_to_global_k(tmp_path):
    """预算缺该函数 → 回退全局 k 并计 miss（不静默、不把函数剔出分母）。"""
    budget = {str(derive_run_seed(3, 1)): {"lib.mod.f0": 2}}
    out, _ = _run(tmp_path, "normal", runs=1, k=5, seed=3, budget=budget)
    payload = _budget_payload(out)
    assert payload["budget_misses"] == 3  # f1/f2/f3 未命中
    counts = {f: rec["attempts"] for f, rec in payload["per_function"].items()}
    assert counts["lib.mod.f0"] == 2
    assert counts["lib.mod.f1"] == 5 and counts["lib.mod.f3"] == 5  # 回退全局 k
    assert payload["assigned_attempts"] == 2  # 分配只算命中的部分


def test_load_function_budget_roundtrip(tmp_path):
    """从落盘的 budget.json 还原 {run_seed: {func: attempts}}（跨模式按 run_seed 配对）。"""
    _run(tmp_path, "normal", runs=2, k=2, seed=11)
    budget = rq1_seed._load_function_budget(str(tmp_path), "normal", "lib")
    assert len(budget) == 2
    for run_index in (1, 2):
        key = str(derive_run_seed(11, run_index))
        assert key in budget
        assert budget[key] == {f"lib.mod.f{i}": 2 for i in range(4)}
    assert rq1_seed._attempts_total(str(tmp_path), "normal", "lib") == 2 * 4 * 2
    assert rq1_seed._assigned_attempts_total(str(tmp_path), "normal", "lib") == 16


# ---------------------------------------------------------------- 3. 编排


def test_run_mc6_5_order_and_budget_propagation(tmp_path, monkeypatch):
    """--mc6-5：ours 先跑，读它的 budget.json 后再把轮次预算传给每个对照组。"""
    seen: list[tuple] = []

    def fake_run_mode(args, mode, *, function_budget=None, budget_source=None):
        seen.append((mode, budget_source, function_budget))
        if mode == "ours":
            run_dir = Path(args.outdir) / "ours" / "lib" / "r1_s123"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "budget.json").write_text(
                json.dumps(
                    {
                        "unit": "attempts",
                        "run_seed": 123,
                        "per_function": {"lib.f0": {"attempts": 3, "calls": 7}},
                        "total_attempts": 3,
                    }
                ),
                encoding="utf-8",
            )
        return {
            "mode": mode,
            "llm_calls": 7,
            "attempts": 3,
            "assigned_attempts": 3,
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
        assert entry[2] == {"123": {"lib.f0": 3}}  # 传的是轮次，不是 calls(7)
    assert plan["unit"] == "attempts"
    assert plan["budget_total_attempts"] == 3
    assert plan["budget_per_run"]["123"]["attempts"] == 3
    assert plan["equal_budget"] is True

    plan_files = list(Path(args.outdir).glob("mc65_plan_lib_*.json"))
    assert len(plan_files) == 1
    on_disk = json.loads(plan_files[0].read_text(encoding="utf-8"))
    assert on_disk["budget_total_attempts"] == 3
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
    """单模式 + --budget-from：从 ours 的 budget.json 取轮次预算，并写进 checkpoint。"""
    run_dir = tmp_path / "runs" / "ours" / "lib" / "r1_s1"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "budget.json").write_text(
        json.dumps(
            {"unit": "attempts", "run_seed": 123, "per_function": {"lib.f0": {"attempts": 3}}}
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
