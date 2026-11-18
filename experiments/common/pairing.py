"""同函数、相同随机种子的**配对**比较。

评审主要意见 3 第 3 条要求「使用相同函数和随机种子的配对比较」：
`experiments/common/stats.py`给出的是**运行级、组间非配对**统计，
本模块补上配对所需的两件事：

1. :func:`checkpoint_pair_key` —— 从与日志同 stem 的 ``*_checkpoint.json`` 读出
   本次运行的**配对键**（``metadata.run_index`` 优先，缺则 ``metadata.seed``）。
   配对键由运行入口写入（``prepare_run_seed`` + ``start_run(metadata=...)``），
   批量脚本用同一个 ``RUN_INDEX`` 驱动所有实验组 → 同键即同种子。
2. :func:`align_paired` —— 把 ``{组: {库: {键: [值]}}}`` 按**键的交集**对齐成
   ``{库: {组: [等长的值]}}``，等长后 ``stats_rows_for(..., paired=True)`` 才是
   真正的配对差值 bootstrap；缺对照组 / 键重复 / 无交集的库被剔除并给出原因。

**边界**：种子只控制本地随机性（参数变异、变异算子采样、策略、数据集采样），
LLM 采样与线程调度仍非确定 —— 配对是「共同随机数」式的实验设计，不承诺逐位复现。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

#: ``<stem>.log`` 的同 stem checkpoint 后缀（见 experiment_recorder）
CHECKPOINT_SUFFIX = "_checkpoint.json"


def checkpoint_pair_key(log_path: str | Path) -> str | None:
    """读日志同 stem 的 ``*_checkpoint.json`` → 配对键；取不到 → ``None``。

    键取 ``metadata.run_index``（轮次序号，首选），否则 ``metadata.seed``
    （本次运行的生效种子）。没有 checkpoint、没有 metadata、两者皆缺 → ``None``，
    该次运行**不参与配对**（报表侧会列出来，而不是静默当成同一键）。
    """
    path = Path(log_path)
    checkpoint = path.with_name(path.stem + CHECKPOINT_SUFFIX)
    if not checkpoint.is_file():
        return None
    try:
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    metadata = payload.get("metadata") if isinstance(payload, dict) else None
    if not isinstance(metadata, dict):
        return None
    run_index = metadata.get("run_index")
    if run_index is not None:
        return f"run_index={run_index}"
    seed = metadata.get("seed")
    if seed is not None:
        return f"seed={seed}"
    return None


def align_paired(
    runs: Mapping[str, Mapping[str, Mapping[str, Sequence[float]]]],
    *,
    reference: str,
    groups: Sequence[str] | None = None,
) -> tuple[dict[str, dict[str, list[float]]], list[str]]:
    """把 ``{组: {库: {配对键: [值, ...]}}}`` 对齐成 ``{库: {组: [等长的值]}}``。

    对齐规则：

    - 每个库取**出现在该库的所有组**的键交集，交集内按键升序排列 → 各组等长；
    - 同一 (组, 库) 下同一键出现多次（没用 ``RUN_INDEX`` 跑的重复运行）→ 该库剔除；
    - 缺 ``reference`` 组、只有 ``reference`` 一个组、键交集为空 → 该库剔除。

    Returns ``(aligned, notes)``：``notes`` 是被剔除库的原因，报表原样打印。
    """
    libraries = sorted({lib for groups_map in runs.values() for lib in groups_map})
    declared = list(groups or [])
    ordered_groups = [g for g in declared if g in runs]
    ordered_groups += sorted(set(runs) - set(ordered_groups))

    aligned: dict[str, dict[str, list[float]]] = {}
    notes: list[str] = []
    for library in libraries:
        present = [g for g in ordered_groups if library in runs.get(g, {})]
        if reference not in present:
            notes.append(f"{library}：缺对照组 {reference}")
            continue
        if len(present) < 2:
            notes.append(f"{library}：只有对照组 {reference}，没有可配对的其它组")
            continue
        duplicated = [
            g for g in present if any(len(v) > 1 for v in runs[g][library].values())
        ]
        if duplicated:
            notes.append(
                f"{library}：组 {'/'.join(duplicated)} 存在重复配对键"
                "（是否漏传 RUN_INDEX？），未配对"
            )
            continue
        common = set(runs[present[0]][library])
        for group in present[1:]:
            common &= set(runs[group][library])
        if not common:
            notes.append(f"{library}：各组没有共同的随机种子键，未配对")
            continue
        aligned[library] = {
            group: [float(runs[group][library][key][0]) for key in sorted(common)]
            for group in present
        }
    return aligned, notes
