"""RQ1 MC6-1：函数分层标签数据集 + 多角度报表测试。

- ut-MC6-1-A 标签读取与数据集生成（维度计数/占比/交叉表/逐函数标签）。
- ut-MC6-1-B 标签数据集落盘与读回（``labels_<lib>.json``）+ 缺失回退函数池。
- ut-MC6-1-C 切层：``layer_meta`` 把维度摊成 ``{func: 层}``，未知取值进 ``unknown``。
- ut-MC6-1-D 驱动在采样前函数池上落标签数据，manifest 留痕；``--limit`` 不缩小标签口径。
- ut-MC6-1-E 分层报表：``build_report`` 出 ``layer_rows``，每层 n 之和 = 总体 n。
"""

from __future__ import annotations

import json

from experiments.RQ1.engine.driver import run_experiment
from experiments.RQ1.engine.labels import (
    DIMENSION_NAMES,
    UNKNOWN_LAYER,
    build_label_dataset,
    labels_for_functions,
    labels_from_dataset,
    labels_of,
    layer_meta,
    layer_values,
    load_label_dataset,
    read_labels,
    write_label_dataset,
)
from experiments.RQ1.engine.table import build_report
from respfuzzer.models import Function


def _func(i: int, **tags) -> Function:
    fn = Function(
        func_name=f"lib.f{i}",
        library_name="lib",
        id=i + 1,
        source=f"def f{i}(): pass",
        args=[],
    )
    for key, value in tags.items():
        setattr(fn, key, value)
    return fn


def _funcs() -> list[Function]:
    """2 个「纯 Python + 有 docstring + 完整注解」+ 2 个「扩展 + 无 docstring + 不完整」。"""
    good = {
        "implementation_type": "pure-python",
        "docstring_quality": "with-docstring",
        "type_annotation_completeness": "完整注解",
    }
    bad = {
        "implementation_type": "extended",
        "docstring_quality": "without-docstring",
        "type_annotation_completeness": "不完整注解",
    }
    return [_func(0, **good), _func(1, **good), _func(2, **bad), _func(3, **bad)]


def test_mc6_1_labels_of_marks_unknown():
    """ut-MC6-1-A：缺字段/非法取值统一记 unknown，合法取值原样保留。"""
    fn = _func(9)
    fn.implementation_type = "builtin???"  # 非法取值
    labels = labels_from_dataset({"functions": {"lib.f9": {"implementation_type": "x"}}})
    assert labels["lib.f9"]["implementation_type"] == "x"  # 数据集原样读回

    got = labels_of(fn)
    assert got["implementation_type"] == UNKNOWN_LAYER
    assert got["docstring_quality"] == "without-docstring"  # 默认值合法
    assert got["type_annotation_completeness"] == "不完整注解"
    assert set(got) == set(DIMENSION_NAMES)


def test_mc6_1_dataset_counts_share_and_cross_tab():
    """ut-MC6-1-A：数据集含维度计数/占比 + 两两交叉表 + 逐函数标签。"""
    dataset = build_label_dataset(_funcs(), "lib", now="2026-01-01T00:00:00")
    assert dataset["library"] == "lib"
    assert dataset["n_functions"] == 4
    assert dataset["generated_at"] == "2026-01-01T00:00:00"
    counts = dataset["dimensions"]["implementation_type"]["counts"]
    assert counts == {"pure-python": 2, "extended": 2, UNKNOWN_LAYER: 0}
    share = dataset["dimensions"]["docstring_quality"]["share"]
    assert share["with-docstring"] == 0.5
    assert sum(share.values()) == 1.0  # 含 unknown 层后占比归一
    cross = dataset["cross_tab"]["implementation_type|type_annotation_completeness"]
    assert cross == {"pure-python": {"完整注解": 2}, "extended": {"不完整注解": 2}}
    assert len(dataset["functions"]) == 4
    assert dataset["functions"]["lib.f0"]["implementation_type"] == "pure-python"


def test_mc6_1_write_and_read_label_dataset(tmp_path):
    """ut-MC6-1-B：落盘 → 读回一致；缺失文件 → None，回退函数池重算。"""
    path = write_label_dataset(tmp_path, _funcs(), "lib", now="2026-01-01T00:00:00")
    assert path.name == "labels_lib.json"
    dataset = load_label_dataset(tmp_path, "lib")
    assert dataset is not None and dataset["n_functions"] == 4

    assert load_label_dataset(tmp_path, "absent") is None
    # 没有该库数据集时回退函数池（data/ 无 absent 库 → 空 dict，不抛）
    assert read_labels(tmp_path, "absent") == {}
    # 读到数据集时按数据集返回
    assert read_labels(tmp_path, "lib", allow_pool_fallback=False) == labels_for_functions(
        _funcs()
    )


def test_mc6_1_layer_meta_buckets_unknown():
    """ut-MC6-1-C：切层把维度摊成 {func: 层}；标签缺失的函数进 unknown 层。"""
    labels = labels_for_functions(_funcs())
    meta = layer_meta(labels, "implementation_type")
    assert meta["lib.f0"] == "pure-python"
    assert meta["lib.f2"] == "extended"
    assert layer_meta({"lib.ghost": {}}, "docstring_quality")["lib.ghost"] == UNKNOWN_LAYER
    assert layer_meta({}, "docstring_quality").get("lib.f0") is None  # 不在标签里 → 不出层
    assert layer_values("docstring_quality")[-1] == UNKNOWN_LAYER


class FakeLLM:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return "<code>x=1</code>"


class FakeExecutor:
    def covered(self, function, code):
        return function.func_name in ("lib.f0", "lib.f2")


def test_mc6_1_driver_writes_labels_before_limit(tmp_path):
    """ut-MC6-1-D：驱动落标签数据集，口径 = 采样前整库函数池，manifest 留痕。"""
    run_experiment(
        "normal",
        "lib",
        runs=1,
        k=3,
        base_seed=1,
        outdir=tmp_path,
        limit=2,  # 只评测 2 个函数
        llm=FakeLLM(),
        executor=FakeExecutor(),
        functions=_funcs(),
        now="2026-08-29T00:00:00",
        allow_fake_embedding=True,
        warmup=False,
    )
    dataset = load_label_dataset(tmp_path, "lib")
    assert dataset is not None
    assert dataset["n_functions"] == 4  # 标签口径不被 --limit 缩小
    run_dir = next((tmp_path / "normal" / "lib").glob("r*_s*"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["label_dataset"] == "labels_lib.json"


def test_mc6_1_build_report_layers(tmp_path):
    """ut-MC6-1-E：分层行齐全、维度/层字段正确、每层 n 之和 = 总体 n。"""
    labels = labels_for_functions(_funcs())
    for mode in ("ours", "normal"):
        run_experiment(
            mode,
            "lib",
            runs=1,
            k=3,
            base_seed=1,
            outdir=tmp_path,
            limit=4,
            llm=FakeLLM(),
            executor=FakeExecutor(),
            functions=_funcs(),
            now="2026-08-29T00:00:00",
            allow_fake_embedding=True,
            warmup=False,
        )
    report = build_report(
        str(tmp_path),
        "lib",
        ["normal"],
        base="ours",
        labels=labels,
        n_boot=200,
    )
    assert report["n_labeled_functions"] == 4
    assert report["comparison"] == "ours_vs_normal"
    overall_n = report["rows"][0]["n_functions"]
    layer_rows = report["layer_rows"]
    assert layer_rows, "应产出分层行"
    for row in layer_rows:
        assert {"dimension", "dimension_label", "layer", "comparison", "ref"} <= set(row)
        assert row["comparison"] == "ours_vs_normal"
        for key in ("delta", "delta_ci_low", "delta_ci_high", "cliffs_delta"):
            assert key in row
    for dimension in DIMENSION_NAMES:
        rows = [r for r in layer_rows if r["dimension"] == dimension]
        assert sum(r["n_functions"] for r in rows) == overall_n
    # 只报一个维度时，其它维度不出行
    only = build_report(
        str(tmp_path), "lib", ["normal"], base="ours",
        dimensions=["docstring_quality"], labels=labels, n_boot=50,
    )
    assert {r["dimension"] for r in only["layer_rows"]} == {"docstring_quality"}
    # 分层构成来自数据集（驱动已落盘）
    assert only["layer_composition"]["implementation_type"]["counts"]["pure-python"] == 2
