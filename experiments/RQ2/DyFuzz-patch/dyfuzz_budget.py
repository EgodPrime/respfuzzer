"""
DyFuzz 单种子变异预算：``-lmutant N`` / ``-ltime S``。

DyFuzz 是传统 Fuzzer、没有 LLM 调用，"变异次数"即实际执行次数，等价于
miniFuzz4All 的 LLM 调用预算口径。两个预算同时生效、先耗尽者停；
只给 ``-ltime`` 时不限次数（时间内尽可能多变异）；两个都不给时报错退出。

独立成模块是为了可单测（``run_respfuzzer.py`` 是顶层执行的脚本）：
uv run pytest -q experiments/RQ2/DyFuzz-patch/test_budget.py
"""

import sys
from dataclasses import dataclass


def _die(message: str) -> None:
    """打印错误并退出。

    ``run_respfuzzer.py`` 在模块级把 ``sys.stdout/stderr`` 重定向到 StringIO，
    而 ``SystemExit`` 的消息走 ``sys.stderr``，会被吞掉；这里绕道
    ``sys.__stderr__`` 保证报错可见。
    """
    print(message, file=sys.__stderr__)
    raise SystemExit(1)


@dataclass(frozen=True)
class Budget:
    """单个种子（函数/API）的变异预算。"""

    mutant_budget: int | None = None  # -lmutant：变异次数上限
    time_budget: float | None = None  # -ltime：总测试时间（秒）

    @property
    def mutant_limit(self) -> int | None:
        """生效的变异次数上限；只给 ``-ltime`` 时不限次数。"""
        return self.mutant_budget

    @property
    def time_limit(self) -> float | None:
        return self.time_budget

    def describe(self) -> str:
        m = self.mutant_limit
        t = self.time_limit
        return (
            f"mutant_budget={'unlimited' if m is None else m}, "
            f"time_budget={'unlimited' if t is None else f'{t:.1f}s'}"
        )


_BUDGET_ALIASES = {
    "-lmutant": "--mutant-budget",
    "-ltime": "--time-budget",
}
_USAGE = (
    "Usage: python run_respfuzzer.py <data_path> "
    "[--seed N] [--run-index N] [-lmutant N] [-ltime S]"
)


def _normalize_budget_args(argv: list[str]) -> list[str]:
    """``-lmutant 500`` / ``-ltime 60`` / ``-ltime=60`` → 标准写法。"""
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _BUDGET_ALIASES:
            if i + 1 >= len(argv):
                _die(f"error: {tok} requires a value")
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


def _parse_cli(argv: list[str]) -> tuple[str, dict[str, int], Budget]:
    """解析 ``<data_path> [--seed N] [--run-index N] [-lmutant N] [-ltime S]``。

    两个预算参数至少给一个，否则报错退出。
    """
    argv = _normalize_budget_args(argv)
    if not argv:
        _die(_USAGE)
    data_path = argv[0]
    opts = argv[1:]
    if len(opts) % 2:
        _die(_USAGE)
    cli: dict[str, int] = {}
    mutant_budget: int | None = None
    time_budget: float | None = None
    for i in range(0, len(opts), 2):
        key, value = opts[i], opts[i + 1]
        if key in ("--seed", "--run-index"):
            cli[key] = int(value)
        elif key == "--mutant-budget":
            mutant_budget = int(value)
        elif key == "--time-budget":
            time_budget = float(value)
        else:
            _die(f"unknown option: {key}")
    if mutant_budget is None and time_budget is None:
        _die(
            "error: a budget is required: pass -lmutant N and/or -ltime S "
            "(per-seed mutant count / total test seconds)"
        )
    if mutant_budget is not None and mutant_budget <= 0:
        _die(f"error: -lmutant must be positive, got {mutant_budget}")
    if time_budget is not None and time_budget <= 0:
        _die(f"error: -ltime must be positive, got {time_budget}")
    return data_path, cli, Budget(mutant_budget=mutant_budget, time_budget=time_budget)
