"""RQ1 F10：配对聚合器——per-function 结果 → function×run×mode 宽表 → d(f)。

配对语义（ED-4）：同一函数 × 同一 run（以 run_id 为键）跨模式对齐；
pass@k 下函数 covered = 任一候选 covered（1/0）。推断总体 = 成功函数池
（data/*_functions.json 提取出的全部函数）。

缺失处理（ut-F10-2）：仅接受在「所有待比模式×至少一个 run」都有记录的
函数（配对完整性）；缺失函数返回 removed 列表供记录，不静默填 0。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from respfuzzer.lib.rq1.driver import merge_results

_RUN_ID_RE = re.compile(r"^.*_r(\d+)_s(\d+)$")


def parse_run_index(run_id: str) -> int:
    """从 run_id 提取 run 序号（{mode}_{lib}_r<N>_s<seed>）；无法解析 → 0。"""
    m = _RUN_ID_RE.search(run_id)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


@dataclass
class WideTable:
    """function×run×mode 的 0/1 覆盖宽表（函数层配对底座，ut-F10-4 reshape）。"""

    funcs: list[str] = field(default_factory=list)
    runs: list[int] = field(default_factory=list)
    modes: list[str] = field(default_factory=list)
    values: dict[tuple[str, int, str], int] = field(default_factory=dict)

    def get(self, func: str, run: int, mode: str) -> int:
        return self.values.get((func, run, mode), 0)


def function_covered(
    rows: list[dict[str, Any]], mode: str
) -> dict[tuple[str, int], int]:
    """把 per-candidate 行聚合成 (func_path, run_index) 的 0/1（pass@k：任一候选覆盖）。"""
    out: dict[tuple[str, int], int] = {}
    for row in rows:
        if row.get("mode") != mode:
            continue
        key = (row["func_path"], parse_run_index(str(row.get("run_id", ""))))
        if row.get("covered"):
            out[key] = 1
        else:
            out.setdefault(key, 0)
    return out


def build_wide(outdir: str, modes: list[str], library: str | None) -> WideTable:
    """读各 mode 的合并结果，构建 function×run×mode 宽表。"""
    per_mode: dict[str, dict[tuple[str, int], int]] = {}
    for mode in modes:
        rows = merge_results(outdir, mode, library)
        per_mode[mode] = function_covered(rows, mode)
    funcs = sorted({f for m in per_mode.values() for f, _ in m})
    runs = sorted({r for m in per_mode.values() for _, r in m})
    wide = WideTable(funcs=funcs, runs=runs, modes=list(modes))
    for mode, table in per_mode.items():
        for (func, run), covered in table.items():
            wide.values[(func, run, mode)] = covered
    return wide


def complete_functions(
    wide: WideTable, modes: list[str] | None = None
) -> tuple[list[str], list[str]]:
    """返回 (完整配对函数, 缺失函数)——缺失任一模的所有 run 都被剔除。

    配对完整性（ED-4）：函数必须在每个待比模式至少出现一次（跨 run 取并）。
    """
    modes = modes or wide.modes
    complete, removed = [], []
    for func in wide.funcs:
        present = {
            m: any((func, run, m) in wide.values for run in wide.runs) for m in modes
        }
        if all(present.values()):
            complete.append(func)
        else:
            removed.append(func)
    return complete, removed


def function_fraction(wide: WideTable, func: str, mode: str) -> float:
    """单函数单模式的跨 run 覆盖比例 p(f)（未收录 run 不计入分母）。"""
    scored = [
        wide.values[(func, run, mode)]
        for run in wide.runs
        if (func, run, mode) in wide.values
    ]
    return sum(scored) / len(scored) if scored else 0.0


def paired_diffs(
    wide: WideTable, base: str = "ours", modes: list[str] | None = None
) -> dict[str, dict[str, float]]:
    """每函数 d(f)=p_base(f) − p_x(f)：仅对配对完整函数计算（ut-F10-3）。"""
    modes = modes or [m for m in wide.modes if m != base]
    complete, _ = complete_functions(wide, [base] + modes)
    diffs: dict[str, dict[str, float]] = {}
    for func in complete:
        pb = function_fraction(wide, func, base)
        diffs[func] = {
            m: round(pb - function_fraction(wide, func, m), 6) for m in modes
        }
    return diffs
