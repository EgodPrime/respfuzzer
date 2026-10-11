"""MC3-3：运行级随机种子（``src/respfuzzer/utils/run_seed.py``）。

- ut-MC3-3-S-A ``derive_run_seed`` 与 RQ1 引擎是**同一个** re-export 实现。
- ut-MC3-3-S-B ``resolve_run_seed`` 优先级：显式 > 进程内 > 环境变量 > 默认。
- ut-MC3-3-S-C ``prepare_run_seed``：派生 + ``random.seed`` + 记录生效值。
- ut-MC3-3-S-D ``stable_random_state``：稳定、可区分、落在 63 位内。
"""

from __future__ import annotations

import random

import pytest

from respfuzzer.utils import run_seed
from respfuzzer.utils.run_seed import (
    derive_run_seed,
    prepare_run_seed,
    resolve_run_seed,
    stable_random_state,
)


@pytest.fixture(autouse=True)
def _isolate_run_seed(monkeypatch):
    """每个用例都从「未设置运行种子、无环境变量、无配置缓存」开始。"""
    monkeypatch.delenv(run_seed.ENV_RUN_SEED, raising=False)
    monkeypatch.setattr(run_seed, "_current", None)
    monkeypatch.setattr(run_seed, "_config_cache", {})
    yield


def test_derive_run_seed_is_the_rq1_reexport():
    """ut-MC3-3-S-A：RQ1 引擎与共享层是同一个实现（跨 RQ 配对键不漂移）。"""
    from experiments.RQ1.engine.seeding import derive_run_seed as rq1_derive

    assert rq1_derive is derive_run_seed
    assert derive_run_seed(7, 2) == (7 * 1000003 + 2) % (2**31 - 1)


def test_resolve_run_seed_precedence(monkeypatch):
    """ut-MC3-3-S-B：显式 > 进程内已设置 > 环境变量 > 默认（配置缺 seed 时）。"""
    # 都没设 → 默认种子（与改动前 llm_mutator 的硬编码 4399 一致）
    assert resolve_run_seed() == run_seed.DEFAULT_RUN_SEED

    monkeypatch.setenv(run_seed.ENV_RUN_SEED, "11")
    assert resolve_run_seed() == 11

    run_seed.set_run_seed(99)
    assert resolve_run_seed() == 99
    # 显式参数仍最高优先级
    assert resolve_run_seed(42) == 42
    # 跳过进程内值（入口按 run_index 派生基础种子时用）
    assert resolve_run_seed(include_current=False) == 11
    assert run_seed.current_run_seed() == 99


def test_prepare_run_seed_derives_seeds_and_replays():
    """ut-MC3-3-S-C：同参数 → 同生效种子与同随机序列；不同 run_index → 不同种子。"""
    first = prepare_run_seed(7, 1, section="fuzz")
    assert first == derive_run_seed(7, 1)
    assert run_seed.current_run_seed() == first
    first_draws = [random.random() for _ in range(3)]

    # 同一对 (seed, run_index) 再来一次：种子与后续随机序列逐位一致
    assert prepare_run_seed(7, 1, section="fuzz") == first
    assert [random.random() for _ in range(3)] == first_draws

    # 不同 run_index → 不同种子（配对的“同一轮”才有区分度）
    assert prepare_run_seed(7, 2, section="fuzz") == derive_run_seed(7, 2)

    # 不带 run_index → 基础种子直接生效
    assert prepare_run_seed(7, None, section="fuzz") == 7


def test_prepare_run_seed_uses_config_section(monkeypatch):
    """ut-MC3-3-S-C：``[<section>].seed`` 缺失时回退默认（配置口径不炸）。"""
    assert prepare_run_seed(None, None, section="dyfuzz") == run_seed.DEFAULT_RUN_SEED


def test_stable_random_state_is_stable_and_distinct():
    """ut-MC3-3-S-D：同部件恒同值、不同部件不同值、落在 63 位内。"""
    state = stable_random_state(4399, "nltk.f")
    assert state == stable_random_state(4399, "nltk.f")
    assert state != stable_random_state(4400, "nltk.f")
    assert state != stable_random_state(4399, "dask.f")
    assert 0 <= state < 2**63
