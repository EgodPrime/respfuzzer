"""
单种子变异预算：``-lrequest`` / ``-ltime``（与 miniFuzz4All 同语义）。

RespFuzzer 是基于 LLM 的 fuzzer：一次预算槽 ≈ 一次 LLM 请求（语义无效的
输出同样消耗一个槽，见 ``tests/fuzz/test_mutation_budget.py``）。两个预算
同时生效、先耗尽者停；只给 ``-ltime`` 时不限次数（时间内尽可能多变异）；
两个都不给时报错退出（强制 CLI，与 DyFuzz 一致，无 config 兜底）。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Budget:
    """单个种子的变异预算。"""

    request_budget: int | None = None  # -lrequest：LLM 请求次数上限
    time_budget: float | None = None  # -ltime：单种子总变异时间（秒）

    @property
    def request_limit(self) -> int | None:
        """生效的 LLM 请求上限；只给 ``-ltime`` 时不限次数。"""
        return self.request_budget

    @property
    def time_limit(self) -> float | None:
        return self.time_budget

    def describe(self) -> str:
        r = self.request_limit
        t = self.time_limit
        return (
            f"request_budget={'unlimited' if r is None else r}, "
            f"time_budget={'unlimited' if t is None else f'{t:.1f}s'}"
        )


_BUDGET_ALIASES = {
    "-lrequest": "--request-budget",
    "-ltime": "--time-budget",
}
_USAGE = (
    "Usage: fuzz fuzz_dataset <dataset_path> "
    "[--rq RQ] [--group G] [--seed N] [--run-index N] "
    "[-lrequest N] [-ltime S]"
)


def resolve_budget(
    request_budget: int | None, time_budget: float | None
) -> Budget:
    """CLI 参数 → ``Budget``；两个都不给时报错退出（无 config 兜底）。"""
    if request_budget is None and time_budget is None:
        raise SystemExit(
            "error: a budget is required: pass -lrequest N and/or -ltime S "
            "(per-seed LLM request count / total mutation seconds)"
        )
    if request_budget is not None and request_budget <= 0:
        raise SystemExit(f"error: -lrequest must be positive, got {request_budget}")
    if time_budget is not None and time_budget <= 0:
        raise SystemExit(f"error: -ltime must be positive, got {time_budget}")
    return Budget(request_budget=request_budget, time_budget=time_budget)


def normalize_budget_args(argv: list[str]) -> list[str]:
    """``-lrequest 500`` / ``-ltime 60`` / ``-ltime=60`` → Fire 标准写法。

    Fire 只认 ``--long-param``，单横线长参数（-lrequest/-ltime）会被静默丢弃，
    入口（``respfuzzer.cmd.fuzz``）在调用 Fire 前用本函数重写 ``sys.argv``。
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _BUDGET_ALIASES:
            if i + 1 >= len(argv):
                raise SystemExit(f"error: {tok} requires a value")
            out += [_BUDGET_ALIASES[tok], argv[i + 1]]
            i += 2
            continue
        key, sep, value = tok.partition("=")
        if sep and key in _BUDGET_ALIASES:
            out.append(f"{_BUDGET_ALIASES[key]}={value}")
        else:
            out.append(tok)
        i += 1
    return out
