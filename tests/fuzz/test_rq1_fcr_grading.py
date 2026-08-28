"""RQ1 fcr 覆盖度精分测试（E-15）。

驱动 `experiments/RQ1/coverage/instrument.py` 的 `instrument_function_fcr`，
验证 E-15 异常精分语义：
  1. 领域级异常（进了目标函数体，如 `1 // 0`）仍记 covered（预期行为）。
  2. 构造/绑定级错误（缺参 TypeError，未进函数体）不记 covered。
  3. 正常返回记 covered。

纯单元，无需 dcov/Redis/LLM。
"""

import importlib.util
from pathlib import Path

import pytest

from respfuzzer.lib.fuzz.semantic_oracle import entered_function_body  # noqa: F401

_INSTRUMENT_PATH = (
    Path(__file__).resolve().parents[2]
    / "experiments"
    / "RQ1"
    / "coverage"
    / "instrument.py"
)


@pytest.fixture(scope="module")
def instrument_mod():
    spec = importlib.util.spec_from_file_location("rq1_instrument", _INSTRUMENT_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load instrument module from {_INSTRUMENT_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def covered_set(instrument_mod):
    instrument_mod.covered_functions = set()
    return instrument_mod.covered_functions


def _fq(func):
    return f"{func.__module__}.{func.__name__}"


def test_domain_exception_still_covered(instrument_mod, covered_set):
    """目标函数体内 ZeroDivisionError（领域级）-> 仍记 covered，异常继续抛出。"""

    def target(x):
        return 1 // x

    w = instrument_mod.instrument_function_fcr(target)
    fq = _fq(target)
    with pytest.raises(ZeroDivisionError):
        w(0)
    assert fq in covered_set


def test_construction_error_not_covered(instrument_mod, covered_set):
    """缺必选参数 TypeError（绑定层，未进函数体）-> 不记 covered，异常抛出。"""

    def target(a, b):
        return a + b

    w = instrument_mod.instrument_function_fcr(target)
    fq = _fq(target)
    with pytest.raises(TypeError):
        w(1)
    assert fq not in covered_set


def test_normal_return_covered(instrument_mod, covered_set, monkeypatch):
    """正常返回 -> 记 covered，且返回原值。"""
    import respfuzzer.lib.fuzz.fuzz_function as ff

    monkeypatch.setattr(ff, "fuzz_function", lambda *a, **k: None)

    def target(a, b):
        return a + b

    w = instrument_mod.instrument_function_fcr(target)
    fq = _fq(target)
    assert w(1, 2) == 3
    assert fq in covered_set
