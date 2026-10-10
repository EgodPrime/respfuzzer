"""RQ1 多角度报告入口【MC6-1】：总体表 + 按函数分层标签的分维度报告。

评审「主要意见 6」要求把函数按实现类型 / 文档质量 / 类型注解完整性分层，**用一次
实验从多个角度报告结果**。引擎侧契约：

- 标签数据：``runs/RQ1/labels_<library|all>.json``（``engine/labels.py`` 生成；
  驱动 ``run_experiment`` 在采样前函数池上自动落盘，本入口在缺失时补生成并留痕）；
- 分层统计：``engine/table.py`` 的 ``layered_rows`` 逐 (维度 × 层 × 对照) 给出
  n / FC(base) / FC(ref) / Δ±95%CI / Cliff's delta，与总体行同一配对分母。

用法（仓库根执行）::

    python experiments/RQ1/rq1_report.py --library numpy                       # 总体 + 3 维度
    python experiments/RQ1/rq1_report.py --library numpy --write-labels        # 先重生成标签数据
    python experiments/RQ1/rq1_report.py --library numpy --dimensions docstring_quality
    python experiments/RQ1/rq1_report.py                                      # 全库（labels_all.json）

产物【MC3-1】：``experiments/RQ1/rq1_report_<library|all>_<ts>.log`` + 同 stem 的
``*_checkpoint.json``，以及 json 报表 ``<outdir>/rq1_report_<library|all>_<ts>.json``。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Any

# 以脚本方式直接运行时补齐搜索路径（与 rq1_seed.py 一致）：仓库根 → ``experiments.*``，
# ``src`` → ``respfuzzer.*``；pytest 侧由 pyproject 的 pythonpath 提供。
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_REPO_ROOT), str(_REPO_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from experiments.RQ1.engine.labels import (
    DIMENSION_NAMES,
    LABEL_DIMENSIONS,
    load_label_dataset,
    load_pool,
    read_labels,
    write_label_dataset,
)
from experiments.RQ1.engine.table import build_report, check_artifacts
from respfuzzer.utils.experiment_recorder import start_run
from respfuzzer.utils.paths import PROJECT_DIR

#: RQ1 的 .log / *_checkpoint.json 落盘目录（与 rq1_seed.py 同目录）
RQ1_DIR = PROJECT_DIR / "experiments" / "RQ1"

#: 默认对照组（base 之外的四个 pass@k 模式）
DEFAULT_MODES = ("normal", "prompted", "extended", "rag")

#: 报表 json 文件名前缀
REPORT_PREFIX = "rq1_report_"


# ---------------------------------------------------------------------------
# 表格渲染
# ---------------------------------------------------------------------------


def _cell(value: Any) -> str:
    """报表值 → 右对齐安全的字符串（``None`` 记 ``-``，浮点 4 位小数）。"""
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _render_table(headers: list[str], rows: list[list[Any]]) -> str:
    """等宽 ASCII 表：列宽按内容自适应，数值列右对齐。"""
    cells = [[_cell(v) for v in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))
    lines = [
        "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)),
        "  ".join("-" * w for w in widths),
    ]
    for row in cells:
        lines.append("  ".join(v.rjust(widths[i]) for i, v in enumerate(row)))
    return "\n".join(lines)


def _delta_cell(row: dict[str, Any]) -> str:
    """Δ±95%CI 合成一列（``delta (ci_low, ci_high)``），缺失记 ``-``。"""
    if row.get("delta") is None:
        return "-"
    lo, hi = row.get("delta_ci_low"), row.get("delta_ci_high")
    if lo is None or hi is None:
        return _cell(row["delta"])
    return f"{row['delta']:+.4f} ({lo:+.4f}, {hi:+.4f})"


_OVERALL_HEADERS = [
    "comparison",
    "n",
    "n_removed",
    "fc_base",
    "fc_ref",
    "delta (95% CI)",
    "cliffs_delta",
]

_LAYER_HEADERS = [
    "layer",
    "comparison",
    "n",
    "fc_base",
    "fc_ref",
    "delta (95% CI)",
    "cliffs_delta",
]


def render_report(report: dict[str, Any]) -> str:
    """把 ``table.build_report`` 的结果渲染成可读报告（总体 → 分维度 → 构成）。"""
    base = (report.get("modes_compared") or ["ours"])[0]
    out: list[str] = []
    out.append(f"# RQ1 分层多角度报告【MC6-1】  library={report.get('library')}")
    out.append(f"对照：{report.get('comparison', base + '_vs_*')}；"
               f"标签函数数 n_labeled={report.get('n_labeled_functions')}")

    missing = report.get("modes_without_data") or []
    if missing:
        out.append(f"!! 无数据的模式（未跑/未合并，不参与判定）：{', '.join(missing)}")

    out.append("")
    out.append("## 总体（全函数）")
    out.append(
        _render_table(
            _OVERALL_HEADERS,
            [
                [
                    row.get("comparison"),
                    row.get("n_functions"),
                    row.get("n_removed"),
                    row.get("fc_base"),
                    row.get("fc_ref"),
                    _delta_cell(row),
                    row.get("cliffs_delta"),
                ]
                for row in report.get("rows", [])
            ],
        )
    )

    layer_rows = report.get("layer_rows") or []
    for name, dim in LABEL_DIMENSIONS.items():
        rows = [r for r in layer_rows if r.get("dimension") == name]
        if not rows:
            continue
        out.append("")
        out.append(f"## 分层角度：{dim.label}（{name}）")
        out.append(
            _render_table(
                _LAYER_HEADERS,
                [
                    [
                        row.get("layer"),
                        row.get("comparison"),
                        row.get("n_functions"),
                        row.get("fc_base"),
                        row.get("fc_ref"),
                        _delta_cell(row),
                        row.get("cliffs_delta"),
                    ]
                    for row in rows
                ],
            )
        )

    composition = report.get("layer_composition") or {}
    if composition:
        out.append("")
        out.append("## 分层构成（标签数据集）")
        for name, info in composition.items():
            counts = info.get("counts") or {}
            share = info.get("share") or {}
            summary = ", ".join(
                f"{key}={counts.get(key, 0)}（{share.get(key, 0):.1%}）"
                for key in info.get("values", [])
            )
            out.append(f"- {info.get('label', name)}：{summary}")

    out.append("")
    out.append(f"## 解码设置（F20）：{report.get('decode_config')}")
    return "\n".join(str(line) for line in out)


# ---------------------------------------------------------------------------
# 标签数据与报表 json
# ---------------------------------------------------------------------------


def ensure_label_dataset(outdir: str, library: str | None, *, force: bool) -> str | None:
    """保证 ``labels_<library>.json`` 存在；返回一句人话说明（无动作 → ``None``）。"""
    if load_label_dataset(outdir, library) is None or force:
        pool = load_pool(library)
        if not pool:
            return f"函数池为空（data/ 无 {library or 'all'} 数据），未生成标签数据集"
        path = write_label_dataset(outdir, pool, library)
        return f"标签数据集{'重生成' if force else '缺失已补'}：{path}"
    return None


def report_path(outdir: str, library: str | None, now: str) -> Path:
    """报表 json 落盘路径 ``<outdir>/rq1_report_<library|all>_<ts>.json``。"""
    return Path(outdir) / f"{REPORT_PREFIX}{library or 'all'}_{now}.json"


def build_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RQ1 多角度报告：总体表 + 函数分层（实现类型/文档质量/注解完整性）",
    )
    parser.add_argument("--outdir", default="runs/RQ1", help="runs 根目录")
    parser.add_argument("--library", default=None, help="目标库；缺省=全部库合并")
    parser.add_argument("--base", default="ours", help="基准模式（默认 ours）")
    parser.add_argument(
        "--modes",
        nargs="+",
        default=list(DEFAULT_MODES),
        help="对照模式（默认 normal prompted extended rag）",
    )
    parser.add_argument(
        "--dimensions",
        nargs="+",
        choices=list(DIMENSION_NAMES),
        default=None,
        help="只报告指定分层维度（缺省=全部三维）",
    )
    parser.add_argument("--n-boot", type=int, default=200, help="bootstrap 次数")
    parser.add_argument(
        "--write-labels",
        action="store_true",
        help="先用 data/ 函数池重生成 labels_<library>.json 再出报告",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="报表 json 路径（缺省 <outdir>/rq1_report_<library|all>_<ts>.json）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = build_args(argv)
    now = _timestamp()

    with start_run(
        "RQ1",
        group="report",
        library=args.library,
        out_dir=RQ1_DIR,
        metadata={
            "entry": "experiments/RQ1/rq1_report.py",
            "outdir": args.outdir,
            "library": args.library,
            "base": args.base,
            "modes": list(args.modes),
            "dimensions": list(args.dimensions) if args.dimensions else None,
            "n_boot": args.n_boot,
            "write_labels": bool(args.write_labels),
        },
    ) as recorder:
        note = ensure_label_dataset(args.outdir, args.library, force=args.write_labels)
        if note:
            print(note)
        recorder.record_event("label_dataset_ready", note=note)

        report = build_report(
            args.outdir,
            args.library,
            args.modes,
            base=args.base,
            dimensions=args.dimensions,
            labels=read_labels(args.outdir, args.library),
            n_boot=args.n_boot,
        )
        print(render_report(report))

        out_json = Path(args.json_out) if args.json_out else report_path(
            args.outdir, args.library, now
        )
        out_json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "entry": "experiments/RQ1/rq1_report.py",
            "generated_at": now,
            "n_boot": args.n_boot,
            "report": report,
        }
        out_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n报表 json：{out_json}")

        # artifact 三件套校验：任何缺件都让 checkpoint 留痕（可复现性【MC3-3】）
        missing = {
            mode: check_artifacts(args.outdir, mode, args.library)
            for mode in [args.base, *args.modes]
        }
        missing = {mode: files for mode, files in missing.items() if files}
        if missing:
            print(f"\n!! 缺失 artifact：{json.dumps(missing, ensure_ascii=False)}")
        recorder.record_results(
            {
                "report_json": str(out_json),
                "library": args.library,
                "n_rows": len(report.get("rows", [])),
                "n_layer_rows": len(report.get("layer_rows", [])),
                "n_labeled_functions": report.get("n_labeled_functions"),
                "modes_without_data": report.get("modes_without_data"),
                "missing_artifacts": {k: len(v) for k, v in missing.items()},
            }
        )
        recorder.record_event(
            "report_completed", report_json=str(out_json), mode=args.base
        )

    # 一条可比较的行都没出（模式未跑）→ 非零退出，避免脚本静默通过
    any_row = any(row.get("n_functions") for row in report.get("rows", []))
    return 0 if any_row else 1


def _timestamp() -> str:
    """报表文件名用的时间戳（精确到秒，与 checkpoint 同一时刻）。"""
    return _dt.datetime.now().strftime("%Y%m%dT%H%M%S")  # noqa: DTZ005


if __name__ == "__main__":
    raise SystemExit(main())
