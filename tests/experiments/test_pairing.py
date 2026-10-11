"""MC3-3：同函数、相同随机种子的配对比较（``experiments/common/pairing.py``）。

- ut-MC3-3-P-A ``checkpoint_pair_key``：配对键来自同 stem checkpoint 的
  ``metadata.run_index``（缺则 ``seed``），缺件/损坏 → ``None``。
- ut-MC3-3-P-B ``align_paired``：按键交集对齐成等长样本，缺对照组 / 键重复 /
  无交集 / 只有对照组都被剔除并给出原因。
"""

from __future__ import annotations

import json

from experiments.common.pairing import align_paired, checkpoint_pair_key

RUNS = {
    "RespFuzzer": {"NLTK": {"run_index=1": [7000], "run_index=2": [8000]}},
    "DyFuzz": {
        "NLTK": {"run_index=1": [1000], "run_index=2": [2000], "run_index=3": [3000]}
    },
}


def _write_checkpoint(log_path, metadata) -> None:
    checkpoint = log_path.with_name(log_path.stem + "_checkpoint.json")
    checkpoint.write_text(json.dumps({"metadata": metadata}), encoding="utf-8")


def test_checkpoint_pair_key_prefers_run_index_then_seed(tmp_path):
    """ut-MC3-3-P-A：键优先取 ``run_index``，缺则 ``seed``，两者皆缺 → ``None``。"""
    log = tmp_path / "rq3_respfuzzer_nltk_20260101T000000.log"
    log.write_text("log", encoding="utf-8")

    # 没有 checkpoint
    assert checkpoint_pair_key(log) is None

    # 两者都有 → run_index 优先
    _write_checkpoint(log, {"run_index": 2, "seed": 7000023})
    assert checkpoint_pair_key(log) == "run_index=2"

    # 只有 seed
    _write_checkpoint(log, {"seed": 4399})
    assert checkpoint_pair_key(log) == "seed=4399"

    # 都没有
    _write_checkpoint(log, {"entry": "x"})
    assert checkpoint_pair_key(log) is None

    # checkpoint 损坏 → None（不抛异常）
    log.with_name(log.stem + "_checkpoint.json").write_text("{oops", encoding="utf-8")
    assert checkpoint_pair_key(log) is None


def test_checkpoint_pair_key_handles_missing_metadata(tmp_path):
    """ut-MC3-3-P-A：checkpoint 没有 metadata 字段 → ``None``。"""
    log = tmp_path / "rq4_respfuzzer_nltk_20260101T000000.log"
    log.write_text("log", encoding="utf-8")
    log.with_name(log.stem + "_checkpoint.json").write_text(
        json.dumps({"status": "completed"}), encoding="utf-8"
    )
    assert checkpoint_pair_key(log) is None


def test_align_paired_intersects_keys_and_keeps_order():
    """ut-MC3-3-P-B：取键交集、按键升序对齐，各组等长。"""
    aligned, notes = align_paired(
        RUNS, reference="RespFuzzer", groups=["DyFuzz", "RespFuzzer"]
    )

    assert notes == []
    assert aligned == {
        "NLTK": {
            # 交集 = {run_index=1, run_index=2}；DyFuzz 的 run_index=3 被丢掉
            "RespFuzzer": [7000.0, 8000.0],
            "DyFuzz": [1000.0, 2000.0],
        }
    }
    # 等长：这是 stats_rows_for(paired=True) 走配对差值的前提
    lengths = {len(values) for values in aligned["NLTK"].values()}
    assert lengths == {2}


def test_align_paired_reports_missing_reference_and_single_group():
    """ut-MC3-3-P-B：缺对照组 / 只有对照组 → 剔除并写明原因。"""
    aligned, notes = align_paired(
        {"DyFuzz": RUNS["DyFuzz"]}, reference="RespFuzzer", groups=["RespFuzzer"]
    )
    assert aligned == {}
    assert notes and "缺对照组 RespFuzzer" in notes[0]

    aligned, notes = align_paired(
        {"RespFuzzer": RUNS["RespFuzzer"]},
        reference="RespFuzzer",
        groups=["RespFuzzer"],
    )
    assert aligned == {}
    assert notes and "只有对照组" in notes[0]


def test_align_paired_rejects_duplicate_keys_and_empty_intersection():
    """ut-MC3-3-P-B：同键跑多次（漏传 RUN_INDEX）与无共同键都剔除。"""
    duplicated = {
        "RespFuzzer": {"NLTK": {"run_index=1": [1.0, 2.0]}},
        "DyFuzz": {"NLTK": {"run_index=1": [3.0, 4.0]}},
    }
    aligned, notes = align_paired(duplicated, reference="RespFuzzer")
    assert aligned == {}
    assert notes and "重复配对键" in notes[0]

    disjoint = {
        "RespFuzzer": {"NLTK": {"run_index=1": [1.0]}},
        "DyFuzz": {"NLTK": {"run_index=2": [2.0]}},
    }
    aligned, notes = align_paired(disjoint, reference="RespFuzzer")
    assert aligned == {}
    assert notes and "没有共同的随机种子键" in notes[0]
