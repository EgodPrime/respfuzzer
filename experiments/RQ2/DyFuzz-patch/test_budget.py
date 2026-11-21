"""
预算模式（-lmutant / -ltime）单元测试。

uv run pytest -q experiments/RQ2/DyFuzz-patch/test_budget.py
"""

import pytest

import dyfuzz_budget as db


# -- _normalize_budget_args: 单横线长参数 shim ------------------------------


def test_normalize_space_separated():
    out = db._normalize_budget_args(
        ["seeds.json", "-lmutant", "500", "-ltime", "60"]
    )
    assert out == [
        "seeds.json",
        "--mutant-budget",
        "500",
        "--time-budget",
        "60",
    ]


def test_normalize_equals_form():
    out = db._normalize_budget_args(["-ltime=60.5", "-lmutant=3"])
    assert out == ["--time-budget=60.5", "--mutant-budget=3"]


def test_normalize_mixed_with_standard_flags():
    out = db._normalize_budget_args(
        ["d.json", "--mutant-budget", "10", "-ltime", "5", "--seed", "42"]
    )
    assert out == [
        "d.json",
        "--mutant-budget",
        "10",
        "--time-budget",
        "5",
        "--seed",
        "42",
    ]


def test_normalize_passthrough_untouched():
    args = ["d.json", "--seed", "7", "--run-index", "2"]
    assert db._normalize_budget_args(args) == args


def test_normalize_missing_value_raises():
    with pytest.raises(SystemExit):
        db._normalize_budget_args(["-lmutant"])


# -- Budget 语义 -------------------------------------------------------------


def test_budget_mutant_only():
    b = db.Budget(mutant_budget=500, time_budget=None)
    assert b.mutant_limit == 500
    assert b.time_limit is None


def test_budget_time_only_unlimited_mutants():
    """只给 -ltime：不限次数，时间内尽可能多变异。"""
    b = db.Budget(mutant_budget=None, time_budget=60)
    assert b.mutant_limit is None
    assert b.time_limit == 60


def test_budget_both_limits():
    b = db.Budget(mutant_budget=500, time_budget=60)
    assert b.mutant_limit == 500
    assert b.time_limit == 60


def test_budget_describe_mentions_both():
    b = db.Budget(mutant_budget=500, time_budget=60)
    text = b.describe()
    assert "500" in text and "60" in text


def test_budget_describe_unlimited_time():
    b = db.Budget(mutant_budget=10)
    assert "unlimited" in b.describe()


# -- _parse_cli --------------------------------------------------------------


def test_parse_cli_minimal_mutant_only():
    data_path, cli, budget = db._parse_cli(["seeds.json", "-lmutant", "500"])
    assert data_path == "seeds.json"
    assert cli == {}
    assert budget.mutant_limit == 500
    assert budget.time_limit is None


def test_parse_cli_both_budgets():
    data_path, cli, budget = db._parse_cli(
        ["seeds.json", "-lmutant", "500", "-ltime", "60"]
    )
    assert data_path == "seeds.json"
    assert budget.mutant_limit == 500
    assert budget.time_limit == 60


def test_parse_cli_time_only():
    _, _, budget = db._parse_cli(["seeds.json", "-ltime", "60"])
    assert budget.mutant_limit is None
    assert budget.time_limit == 60


def test_parse_cli_with_seed_and_run_index():
    data_path, cli, budget = db._parse_cli(
        ["seeds.json", "--seed", "42", "--run-index", "3", "-lmutant", "10"]
    )
    assert data_path == "seeds.json"
    assert cli == {"--seed": 42, "--run-index": 3}
    assert budget.mutant_limit == 10


def test_parse_cli_requires_budget():
    """两个预算都不给 → 报错退出（不回退 config）。"""
    with pytest.raises(SystemExit):
        db._parse_cli(["seeds.json"])


def test_parse_cli_requires_data_path():
    with pytest.raises(SystemExit):
        db._parse_cli([])


def test_parse_cli_odd_opts_raise():
    with pytest.raises(SystemExit):
        db._parse_cli(["seeds.json", "--seed"])


def test_parse_cli_unknown_option_raises():
    with pytest.raises(SystemExit):
        db._parse_cli(["seeds.json", "--verbose", "1", "-lmutant", "5"])


def test_parse_cli_non_positive_mutant_raises():
    with pytest.raises(SystemExit):
        db._parse_cli(["seeds.json", "-lmutant", "0"])


def test_parse_cli_non_positive_time_raises():
    with pytest.raises(SystemExit):
        db._parse_cli(["seeds.json", "-ltime", "-5"])


def test_parse_cli_float_time():
    _, _, budget = db._parse_cli(["seeds.json", "-ltime", "12.5"])
    assert budget.time_limit == 12.5
