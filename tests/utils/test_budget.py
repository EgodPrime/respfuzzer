"""
预算模式（-lrequest / -ltime）单元测试，与 miniFuzz4All 同语义、强制 CLI。

uv run pytest -q tests/utils/test_budget.py
"""

import pytest

from respfuzzer.utils.budget import (
    Budget,
    normalize_budget_args,
    resolve_budget,
)

# -- normalize_budget_args: 单横线长参数 shim -------------------------------


def test_normalize_space_separated():
    out = normalize_budget_args(
        ["seeds.json", "-lrequest", "500", "-ltime", "60"]
    )
    assert out == [
        "seeds.json",
        "--request-budget",
        "500",
        "--time-budget",
        "60",
    ]


def test_normalize_equals_form():
    out = normalize_budget_args(["-ltime=60.5", "-lrequest=3"])
    assert out == ["--time-budget=60.5", "--request-budget=3"]


def test_normalize_mixed_with_standard_flags():
    out = normalize_budget_args(
        ["d.json", "--request-budget", "10", "-ltime", "5", "--seed", "42"]
    )
    assert out == [
        "d.json",
        "--request-budget",
        "10",
        "--time-budget",
        "5",
        "--seed",
        "42",
    ]


def test_normalize_passthrough_untouched():
    args = ["d.json", "--rq", "RQ3", "--seed", "7", "--run-index", "2"]
    assert normalize_budget_args(args) == args


def test_normalize_missing_value_raises():
    with pytest.raises(SystemExit):
        normalize_budget_args(["-lrequest"])


# -- Budget 语义 -------------------------------------------------------------


def test_budget_request_only():
    b = Budget(request_budget=500, time_budget=None)
    assert b.request_limit == 500
    assert b.time_limit is None


def test_budget_time_only_unlimited_requests():
    """只给 -ltime：不限次数，时间内尽可能多变异。"""
    b = Budget(request_budget=None, time_budget=60)
    assert b.request_limit is None
    assert b.time_limit == 60


def test_budget_both_limits():
    b = Budget(request_budget=500, time_budget=60)
    assert b.request_limit == 500
    assert b.time_limit == 60


def test_budget_describe_mentions_both():
    b = Budget(request_budget=500, time_budget=60)
    text = b.describe()
    assert "500" in text and "60" in text


def test_budget_describe_unlimited_time():
    b = Budget(request_budget=10)
    assert "unlimited" in b.describe()


# -- resolve_budget: 强制 CLI（同 DyFuzz，无 config 兜底）---------------------


def test_resolve_requires_budget():
    """两个都不给 → 报错退出。"""
    with pytest.raises(SystemExit):
        resolve_budget(None, None)


def test_resolve_passes_values():
    b = resolve_budget(500, 60)
    assert b.request_limit == 500
    assert b.time_limit == 60


def test_resolve_time_only():
    b = resolve_budget(None, 60)
    assert b.request_limit is None
    assert b.time_limit == 60


def test_resolve_non_positive_request_raises():
    with pytest.raises(SystemExit):
        resolve_budget(0, None)


def test_resolve_non_positive_time_raises():
    with pytest.raises(SystemExit):
        resolve_budget(None, -5)


def test_resolve_float_time():
    b = resolve_budget(None, 12.5)
    assert b.time_limit == 12.5
