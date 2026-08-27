"""C-8 real-worker integration tests (dcov + Redis at 127.0.0.1:6379).

These run the REAL fuzz_single_seed against the installed environment: real
dcov BitmapManager/LoaderWrapper (shared-memory coverage) + real Redis + real
multiprocessing worker (continue_safe_execute).  We only replace the LLMMutator
with a controllable fake so no real LLM / no network is needed, while the
C-8 last_valid budget semantics are exercised on the genuine worker path:

  * a VALID round must be written to mutants.data and rewarded;
  * an INVALID round (last_valid=False, placeholder) must be skipped (no write,
    no execute, no reward), and the placeholder must never reach mutants.data.

Shared-memory indices are kept high (>=4999) to avoid colliding with the real
parent bitmap (4398), real fuzz runs (4399+), or C-7 worker tests (4399/4499).
"""

import os

import pytest

from respfuzzer.lib.fuzz.fuzz_exp import fuzz_single_seed
from respfuzzer.models import Mutant, Seed

PROC_INDEX = 4999  # high index, avoids 4398/4399/4499 regions

VALID_CODE = "import statistics\nstatistics.median([1, 2, 3])\n"
PLACEHOLDER_FUNCTION_CALL = ""


def _seed() -> Seed:
    return Seed(
        func_id=1,
        library_name="statistics",
        func_name="statistics.median",
        args=[],
        function_call="unused",
    )


def _mutant(code: str) -> Mutant:
    return Mutant(
        func_id=1,
        seed_id=1,
        library_name="statistics",
        func_name="statistics.median",
        args=[],
        function_call=code,
    )


class _CtrlMutator:
    """Controllable mutator that mimics the C-8 contract (last_valid / budget).

    last_valid_override forces the round outcome: True -> emit a valid mutant
    that the REAL worker executes; False -> emit a placeholder (function_call="")
    that fuzz_single_seed must skip.
    """

    def __init__(self, seed: Seed, *, last_valid_override: bool):
        self.seed = seed
        self._override = last_valid_override
        self.last_valid = False
        self.attempts_total = 0
        self.valid_count = 0
        self.invalid_count = 0
        self.reward_calls = []

    def random_llm_mutate(self, *args, **kwargs):
        self.last_valid = self._override
        self.attempts_total += 1
        if self._override:
            self.valid_count += 1
            return _mutant(VALID_CODE), 0
        self.invalid_count += 1
        return _mutant(PLACEHOLDER_FUNCTION_CALL), 0

    def update_reward(self, mutation_type: int, reward: float) -> None:
        self.reward_calls.append((mutation_type, reward))

    def calculate_reward(self, has_semantic_error: bool, coverage_gain: float) -> float:
        """Real LLMMutator reward formula, so the REAL worker reward path works.
        Kept identical to LLMMutator.calculate_reward (0.5*(1-int(err)) + 0.5*gain, [0,1])."""
        base = 0.5 * (1 - int(has_semantic_error)) + 0.5 * coverage_gain
        return min(max(base, 0), 1)

    def budget_report(self) -> dict:
        return {
            "budget": self.attempts_total,
            "valid": self.valid_count,
            "invalid": self.invalid_count,
            "invalid_ratio": (
                self.invalid_count / self.attempts_total
                if self.attempts_total
                else 0.0,
            ),
        }


def _run(monkeypatch, tmp_path, *, last_valid_override: bool) -> _CtrlMutator:
    """Run the REAL fuzz_single_seed, replacing only LLMMutator + DATA_DIR."""
    mut = _CtrlMutator(_seed(), last_valid_override=last_valid_override)
    monkeypatch.setattr("respfuzzer.lib.fuzz.fuzz_exp.LLMMutator", lambda s: mut)
    monkeypatch.setattr("respfuzzer.lib.fuzz.fuzz_exp.DATA_DIR", tmp_path)
    # Real Queue/Process/BitmapManager/get_redis_client are left untouched.
    fuzz_single_seed(_seed(), process_index=PROC_INDEX)
    return mut


def _read_mutants(tmp_path) -> str:
    p = tmp_path / "mutants.data"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def test_real_worker_valid_round_written_and_rewarded(monkeypatch, tmp_path):
    """Every valid round (last_valid=True) is executed by the REAL worker, its
    mutant written to mutants.data, and update_reward called (reward path)."""
    mut = _run(monkeypatch, tmp_path, last_valid_override=True)

    content = _read_mutants(tmp_path)
    assert content, "no valid mutant was written to mutants.data"
    n_written = content.strip().count("\n") + 1 if content.strip() else 0
    assert n_written >= 1, "valid rounds did not write mutants"
    assert mut.valid_count >= 1, "valid_count not incremented"
    assert mut.reward_calls, "update_reward not called on valid rounds"
    # No placeholder ever leaked into the file.
    assert '"function_call": ""' not in content, "placeholder leaked into mutants.data"


def test_real_worker_invalid_round_skipped(monkeypatch, tmp_path):
    """A round that yields last_valid=False (placeholder) must be skipped on the
    REAL worker path: nothing written to mutants.data, no reward given."""
    mut = _run(monkeypatch, tmp_path, last_valid_override=False)

    assert _read_mutants(tmp_path) == "", "invalid rounds must not write mutants.data"
    assert mut.invalid_count >= 1, "invalid_count not incremented"
    assert mut.reward_calls == [], "update_reward must not fire on invalid rounds"
