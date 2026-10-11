"""
打印 RQ2 FCR 结果表格

通过 `get_data_for_view` 函数获取每个数据库的结果数据，
以 markdown 表格形式打印各配置下各库的 tf_solved_percent。

使用方式: uv run report.py <folder_prefix>
Example:    uv run report.py RQ2_data_llama
"""

import json
import sys
from collections.abc import Mapping
from pathlib import Path

#: 以脚本方式直接运行时补齐搜索路径（与 ``experiments/RQ1`` 各入口一致）：仓库根 →
#: ``experiments.common``，``src`` → ``respfuzzer``；pytest 侧由 pyproject 的 pythonpath 提供。
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from experiments.common.stats import (
    DEFAULT_N_BOOT,
    format_stats_table,
    hierarchical_bootstrap_ci,
    paired_bootstrap_ci,
    stats_rows_for,
)
from respfuzzer.models import Function, Seed
from respfuzzer.utils.db_tools import get_data_for_view
from respfuzzer.utils.paths import PROJECT_DIR

library_map = {
    "nltk": "NLTK",
    "dask": "Dask",
    "yaml": "PyYAML",
    "prophet": "Prophet",
    "numpy": "Numpy",
    "pandas": "Pandas",
    "sklearn": "Scikit-learn",
    "scipy": "Scipy",
    "requests": "Requests",
    "spacy": "spaCy",
    "torch": "PyTorch",
    "paddle": "Paddle",
}


def print_table(data_results: dict):
    """打印 markdown 表格

    Args:
        data_results: 配置名 -> get_data_for_view() 返回的数据字典
    """
    libs = list(library_map.keys())
    header_libs = [library_map[lib] for lib in libs]

    # 计算每列最大宽度（表头 + 各行数据）
    col_widths = [len("Configuration")]
    for lib in header_libs:
        col_widths.append(len(lib))
    col_widths.append(len("Average"))

    rows = []
    for cfg, label in [
        ("Full", "Full"),
        ("W/O RCM", "W/O RCM"),
        ("W/O SCE", "W/O SCE"),
        ("W/O All", "W/O All"),
    ]:
        data = data_results[label]
        vals = [data[lib]["tf_solved_percent"] for lib in libs]
        avg = sum(float(v.strip("%")) for v in vals) / len(vals)
        vals.append(f"{avg:.2f}%")
        rows.append((cfg, vals))

        # 更新列宽
        for i, v in enumerate(vals):
            if len(v) > col_widths[i + 1]:
                col_widths[i + 1] = len(v)

    # 打印表头
    header_line = "| " + " | ".join(
        ["Configuration"]
        + header_libs
        + ["Average"]
    ) + " |"
    print(header_line)

    # 打印分隔线
    sep_cells = " | ".join("-" * w for w in col_widths)
    print("|" + sep_cells + "|")

    # 打印数据行
    for cfg, vals in rows:
        cells = [cfg] + vals
        line = "| " + " | ".join(cells[i].rjust(col_widths[i]) for i in range(len(cells))) + " |"
        print(line)


#: 效应量与配对差值的对照组（反射式种子生成的完整配置）
REFERENCE_CONFIG = "Full"

#: 配置顺序（与 ``print_table`` 的行序一致）
CONFIG_ORDER = ["Full", "W/O RCM", "W/O SCE", "W/O All"]

#: 统计表脚注
_STATS_NOTE = (
    "> `n` = 参与统计的**共同函数数**（各配置函数池的交集）；每个函数记 100/0"
    "（是否生成了可用种子），`mean±sd` 即 FC% 与该 0/1 指示的标准差，"
    "`95% CI` 为 percentile bootstrap（B=2000，固定 seed）。"
    "`Δ vs Full` 是**同一批函数的配对差值**均值及其 95% CI；`Cliff's δ` 是配置间效应量"
    "（>0 表示该配置更高；|δ|<0.147 negligible、<0.33 small、<0.474 medium、其余 large）。"
    "`Average` 行只取**各配置都有数据**的库的库均值（未配对汇总）。"
)


def _read_json(path: Path) -> list:
    """读 json 数组文件；缺失或损坏时返回空列表（与 ``get_data_for_view`` 的兜底一致）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def load_function_outcomes(data_dir: str | Path) -> dict[str, dict[str, bool]]:
    """读一个配置目录 → ``{库: {函数名: 是否生成了可用种子}}``【MC3-6】。

    与 ``get_data_for_view`` 同源同判定（``seed.function_call`` 非空即 solved），
    但**不聚合成计数**，逐函数结果留给置信区间与效应量计算。
    """
    data_path = Path(data_dir)
    outcomes: dict[str, dict[str, bool]] = {}
    for functions_file in sorted(data_path.glob("*_functions.json")):
        library = functions_file.name[: -len("_functions.json")]
        functions = [
            Function.model_validate(item) for item in _read_json(functions_file)
        ]
        seeds: dict[str, Seed] = {}
        seeds_file = data_path / f"{library}_seeds.json"
        if seeds_file.exists():
            seeds = {
                seed.func_name: seed
                for seed in (
                    Seed.model_validate(item) for item in _read_json(seeds_file)
                )
            }
        solved: dict[str, bool] = {}
        for function in functions:
            seed = seeds.get(function.func_name)
            solved[function.func_name] = bool(seed and seed.function_call)
        outcomes[library] = solved
    return outcomes


def align_outcomes(
    outcomes_by_config: Mapping[str, Mapping[str, Mapping[str, bool]]],
    library: str,
) -> tuple[list[str], dict[str, list[float]]]:
    """取各配置函数池的**交集**（保持对照组的函数顺序）。

    Returns:
        (共同函数名列表, {配置: [100.0/0.0 逐函数结果]})；该库在任何配置缺失 → ``([], {})``。
    """
    pools = [
        list(outcomes[library])
        for outcomes in outcomes_by_config.values()
        if library in outcomes
    ]
    if not pools:
        return [], {}
    common = set(pools[0])
    for names in pools[1:]:
        common &= set(names)
    ordered = [name for name in pools[0] if name in common]
    vectors = {
        config: [100.0 if outcomes[library].get(name) else 0.0 for name in ordered]
        for config, outcomes in outcomes_by_config.items()
        if library in outcomes
    }
    return ordered, vectors



def _mc34_bootstrap_analysis(
    outcomes: dict[str, dict[str, dict[str, bool]]],
) -> str:
    """【MC3-4】RQ2 分层/配对 Bootstrap 分析：函数层 + 库层。

    对每对配置（对照 vs 消融），报告：
    - 函数层配对 bootstrap CI（全库函数 pooled 的 Δ 值重采样）
    - 库层分层 bootstrap CI（先抽库再抽函数）
    """
    lines: list[str] = ["## Paired & Hierarchical Bootstrap Analysis 【MC3-4】"]
    lines.append(
        "> 函数层：对全库配对完整函数的 Δ 做 percentile bootstrap（B=2000）；"
        "库层：对逐库 Δ 做分层 bootstrap（先抽库、再库内抽函数）。"
    )
    lines.append("")

    ablations = [c for c in CONFIG_ORDER if c != REFERENCE_CONFIG]
    for abl in ablations:
        # 收集该消融配置 vs Full 的逐函数 Δ
        func_diffs: list[float] = []
        lib_deltas: dict[str, float] = {}

        for lib_name in library_map:
            full_outcomes = outcomes.get(REFERENCE_CONFIG, {}).get(lib_name, {})
            abl_outcomes = outcomes.get(abl, {}).get(lib_name, {})
            common_funcs = set(full_outcomes) & set(abl_outcomes)
            lib_full: list[float] = []
            lib_abl: list[float] = []
            for func in common_funcs:
                f_val = 100.0 if full_outcomes[func] else 0.0
                a_val = 100.0 if abl_outcomes[func] else 0.0
                func_diffs.append(f_val - a_val)
                lib_full.append(f_val)
                lib_abl.append(a_val)
            if lib_full and lib_abl:
                delta = sum(lib_full) / len(lib_full) - sum(lib_abl) / len(lib_abl)
                lib_deltas[library_map[lib_name]] = delta

        if not func_diffs:
            lines.append(f"### {abl} vs {REFERENCE_CONFIG}")
            lines.append("> 无配对完整函数，bootstrap 无法计算。")
            lines.append("")
            continue

        # 函数层配对 bootstrap
        fl_lo, fl_hi, fl_point = paired_bootstrap_ci(
            func_diffs, n_boot=DEFAULT_N_BOOT, alpha=0.05
        )
        lines.append(f"### {abl} vs {REFERENCE_CONFIG}")
        fl_ci_str = (
            f"[{fl_lo:+.2f}, {fl_hi:+.2f}]" if fl_lo is not None else "(n/a)"
        )
        lines.append(f"- 函数层: Δ={fl_point:+.2f}% 95% CI={fl_ci_str} (n={len(func_diffs)})")

        # 库层分层 bootstrap
        if len(lib_deltas) >= 2:
            hl_lo, hl_hi, hl_point = hierarchical_bootstrap_ci(
                {lib: [d] for lib, d in lib_deltas.items()},
                n_boot=DEFAULT_N_BOOT,
                alpha=0.05,
            )
            hl_ci_str = (
                f"[{hl_lo:+.2f}, {hl_hi:+.2f}]" if hl_lo is not None else "(n/a)"
            )
            lines.append(f"- 库层: Δ={hl_point:+.2f}% 95% CI={hl_ci_str} ({len(lib_deltas)} 库)")
        else:
            lines.append(f"- 库层: 仅 {len(lib_deltas)} 库，退化为函数层 bootstrap")
        lines.append("")

    return "\n".join(lines)
def gen_stats_tables(dirs: Mapping[str, str | Path]) -> str:
    """【MC3-2/MC3-6】逐库 + Average 的函数级统计表：FC% 的 95% CI、配对 Δ、Cliff's δ。

    Args:
        dirs: ``{配置名: 该配置的数据目录}``（顺序即表内配置顺序，对照组须在其中）。
    """
    outcomes = {config: load_function_outcomes(path) for config, path in dirs.items()}
    order = [lib for lib in library_map if any(lib in o for o in outcomes.values())]
    extra = sorted({lib for o in outcomes.values() for lib in o} - set(order))

    data: dict[str, dict[str, list[float]]] = {}
    skipped: list[str] = []
    dropped: dict[str, int] = {}
    for library in order + extra:
        names, vectors = align_outcomes(outcomes, library)
        if not names:
            continue
        label = library_map.get(library, library)
        if REFERENCE_CONFIG not in vectors:
            skipped.append(label)
            continue
        data[label] = vectors
        pool_sizes = {len(o.get(library, {})) for o in outcomes.values()}
        if len(pool_sizes) > 1:
            dropped[label] = max(pool_sizes) - len(names)

    if not data:
        return (
            f"（缺少对照组 {REFERENCE_CONFIG} 的函数池或没有任何 *_functions.json，"
            "函数级统计已跳过）"
        )

    rows = stats_rows_for(
        data,
        REFERENCE_CONFIG,
        labels=list(data),
        groups=list(dirs),
        paired=True,
    )
    table = format_stats_table(
        rows,
        label_header="Library",
        group_header="Configuration",
        unit="%",
        reference=REFERENCE_CONFIG,
        with_diff=True,
    )
    mc34_section = _mc34_bootstrap_analysis(outcomes)
    notes = [f"\n\n{_STATS_NOTE}", f"\n{mc34_section}"]
    if skipped:
        notes.append(
            f"\n> 缺少 `{REFERENCE_CONFIG}` 函数池、未列入的库：{', '.join(skipped)}。"
        )
    if dropped:
        detail = "、".join(f"{lib}(-{n})" for lib, n in dropped.items())
        notes.append(f"\n> 函数池不一致、只统计交集的库：{detail}。")
    return table + "".join(notes)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: uv run report.py <folder_prefix>")
        print("Example: uv run report.py RQ2_data_llama")
        sys.exit(1)

    prefix = sys.argv[1]
    db_names = {
        "Full": f"{prefix}_111",
        "W/O RCM": f"{prefix}_110",
        "W/O SCE": f"{prefix}_101",
        "W/O All": f"{prefix}_100",
    }

    data_results = {}
    for label, db_file in db_names.items():
        data_results[label] = get_data_for_view(db_file)

    print_table(data_results)

    print()
    print(
        gen_stats_tables(
            {label: PROJECT_DIR / db_file for label, db_file in db_names.items()}
        )
    )
