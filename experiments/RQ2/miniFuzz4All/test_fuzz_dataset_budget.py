"""
预算模式（-lrequest / -ltime）单元测试。

uv run pytest -q experiments/RQ2/miniFuzz4All/test_fuzz_dataset_budget.py
"""

import pytest

import fuzz_dataset as fd


# -- _normalize_budget_args: Fire 单横线长参数 shim -------------------------


def test_normalize_space_separated():
    out = fd._normalize_budget_args(
        ["normal", "seeds.json", "-lrequest", "500", "-ltime", "60"]
    )
    assert out == [
        "normal",
        "seeds.json",
        "--request-budget",
        "500",
        "--time-budget",
        "60",
    ]


def test_normalize_equals_form():
    out = fd._normalize_budget_args(["-ltime=60.5", "-lrequest=3"])
    assert out == ["--time-budget=60.5", "--request-budget=3"]


def test_normalize_mixed_with_standard_flags():
    out = fd._normalize_budget_args(
        ["normal", "d.json", "--request-budget", "10", "-ltime", "5", "--rq", "RQ2"]
    )
    assert out == [
        "normal",
        "d.json",
        "--request-budget",
        "10",
        "--time-budget",
        "5",
        "--rq",
        "RQ2",
    ]


def test_normalize_passthrough_untouched():
    args = ["normal", "d.json", "--rq", "RQ3", "-x", "--verbose"]
    assert fd._normalize_budget_args(args) == args


def test_normalize_missing_value_raises():
    with pytest.raises(SystemExit):
        fd._normalize_budget_args(["-lrequest"])


# -- Budget 语义 ------------------------------------------------------------


def test_budget_request_only():
    b = fd.Budget(request_budget=500, time_budget=None, fallback_requests=100)
    assert b.request_limit == 500
    assert b.time_limit is None


def test_budget_time_only_unlimited_requests():
    """只给 -ltime：不限次数，时间内尽可能多变异。"""
    b = fd.Budget(request_budget=None, time_budget=60, fallback_requests=100)
    assert b.request_limit is None
    assert b.time_limit == 60


def test_budget_both_limits():
    b = fd.Budget(request_budget=500, time_budget=60, fallback_requests=100)
    assert b.request_limit == 500
    assert b.time_limit == 60


def test_budget_fallback_when_nothing_given():
    """两个参数都不给时回退 config 的 llm_fuzz_per_seed。"""
    b = fd.Budget(request_budget=None, time_budget=None, fallback_requests=100)
    assert b.request_limit == 100
    assert b.time_limit is None


def test_budget_describe_mentions_both():
    b = fd.Budget(request_budget=500, time_budget=60)
    text = b.describe()
    assert "500" in text and "60" in text


def test_resolve_budget_falls_back_to_config():
    """都不传时回退 config llm_fuzz_per_seed（当前 config=100）。"""
    b = fd._resolve_budget(None, None)
    assert b.request_budget is None and b.time_budget is None
    from respfuzzer.utils.config import get_config

    assert b.request_limit == int(get_config("fuzz4all").get("llm_fuzz_per_seed"))


def test_resolve_budget_passes_cli_values():
    b = fd._resolve_budget(500, 60)
    assert b.request_limit == 500
    assert b.time_limit == 60
