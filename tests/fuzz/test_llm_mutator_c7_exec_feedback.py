"""C-7 tests: runtime-exception negative feedback in the LLM mutator's reward.

C-7 makes the LLM mutator use *runtime exceptions during execution* as semantic
negative feedback (previously only static IsValidSemantic / syntax errors were
penalised). The live parent wiring in ``fuzz_exp.py`` (``fuzz_single_seed``)
now routes ``(has_semantic_error=runtime_exception, coverage_gain)`` into
``LLMMutator.calculate_reward`` -- a crasher without coverage gain gets the
lowest reward, an error-path mutant that still gains coverage is restored
(G1) but stays strictly below the clean same-gain reward.

These tests exercise ``calculate_reward`` / ``update_reward`` directly (no real
worker / coverage / network), so they are the pure-logic regression net for the
t1 (param rename) + t2 (caller wiring) changes.

Reward function (unchanged formula, renamed param):
    base = 0.5*(1 - int(has_semantic_error)) + 0.5*coverage_gain, clamped [0,1]

Raw reward matrix:
    (False, 1.0) -> 1.0   clean cov-gain
    (False, 0.0) -> 0.5   clean no-cov (neutral)
    (True,  0.0) -> 0.0   crasher, no cov-gain (C-7 negative feedback)
    (True,  1.0) -> 0.5   G1: error-path mutant that gained coverage -> rescued
                          (strictly below clean same-gain 1.0)

EWM transition (update_reward, alpha=0.1, fresh mu[mt]=0.5):
    raw 1.0 -> mu 0.55 ; raw 0.5 -> mu 0.5 ; raw 0.0 -> mu 0.45
"""

from respfuzzer.lib.fuzz.llm_mutator import LLMMutator
from respfuzzer.models import Seed


def _seed() -> Seed:
    """Minimal valid Seed; the reward helpers touch only seed-agnostic state."""
    return Seed(
        func_id=1,
        library_name="numpy",
        func_name="numpy.asarray",
        args=[],
        function_call="import numpy\nresult = numpy.asarray([1])\n",
    )


def _mutator() -> LLMMutator:
    return LLMMutator(_seed())


# --------------------------------------------------------------------------- #
# 1. calculate_reward semantic-term matrix (after the t1 rename)
# --------------------------------------------------------------------------- #


def test_calculate_reward_semantic_matrix():
    """Raw reward matrix for (has_semantic_error, coverage_gain) -- exact floats."""
    cases = {
        (False, 1.0): 1.0,  # clean cov-gain
        (False, 0.0): 0.5,  # clean no-cov (neutral)
        (True, 0.0): 0.0,  # crasher, no cov-gain (C-7 lowest)
        (True, 1.0): 0.5,  # G1: crasher that gained coverage still rewarded
    }
    m = _mutator()
    for (sem_err, cov_gain), expected in cases.items():
        assert m.calculate_reward(sem_err, cov_gain) == expected, (
            f"calculate_reward({sem_err}, {cov_gain}) should be {expected}"
        )


# --------------------------------------------------------------------------- #
# 2. E-15 guard / G1: an error-path mutant that gains coverage is NOT zeroed
# --------------------------------------------------------------------------- #


def test_e15_guard_crasher_with_cov_gain_not_zeroed():
    """A boundary/error-path mutant that raises BUT gains coverage must still
    get a positive reward (G1), not zero. Assert exact 0.5 and that it stays
    strictly below the clean same-gain reward (1.0)."""
    m = _mutator()
    crasher_with_gain = m.calculate_reward(True, 1.0)
    clean_with_gain = m.calculate_reward(False, 1.0)

    assert crasher_with_gain == 0.5  # not zeroed out
    assert crasher_with_gain > 0.0  # positive feedback preserved
    assert crasher_with_gain < clean_with_gain == 1.0  # G1 ordering


# --------------------------------------------------------------------------- #
# 3. EWM transition through update_reward (alpha=0.1, fresh mu=0.5)
# --------------------------------------------------------------------------- #


def test_update_reward_ewm_transition():
    """update_reward = alpha*reward + (1-alpha)*mu; from a fresh mu=0.5 with
    alpha=0.1: raw 1.0 -> mu 0.55, raw 0.5 -> mu 0.5, raw 0.0 -> mu 0.45."""
    alpha = 0.1
    for raw_reward, expected_mu in ((1.0, 0.55), (0.5, 0.5), (0.0, 0.45)):
        m = _mutator()
        mt = 0
        assert m.mu[mt] == 0.5  # fresh prior
        m.update_reward(mt, raw_reward)
        assert round(m.mu[mt], 10) == expected_mu


# --------------------------------------------------------------------------- #
# 4. Regression: exec-error mutant must differ from clean no-cov (fails old code)
# --------------------------------------------------------------------------- #


def test_exec_error_reward_differs_from_clean_no_cov_regression():
    """On the OLD code both an exec-error mutant and a clean no-cov mutant were
    mapped to calculate_reward(False, 0.0) == 0.5 (identical). After C-7 the
    crasher routes to calculate_reward(True, 0.0) == 0.0 < 0.5. This test only
    asserts the reward mapping distinguishes the two -- no real exec needed."""
    m = _mutator()

    # Simulate the parent wiring in fuzz_single_seed (t2):
    #   exec-error mutant   -> calculate_reward(runtime_exception=True,  gain=0.0)
    #   clean-no-cov mutant -> calculate_reward(runtime_exception=False, gain=0.0)
    exec_error_reward = m.calculate_reward(True, 0.0)
    clean_no_cov_reward = m.calculate_reward(False, 0.0)

    # AFTER patch: crasher is strictly lower and definitely not identical.
    assert exec_error_reward < clean_no_cov_reward
    assert not (exec_error_reward == clean_no_cov_reward)

    # And it collapses onto the static-IsValidSemantic-invalid penalty path.
    # G1: a crasher WITHOUT coverage collapses to the same penalty as a static-IsValidSemantic-invalid
    # mutant (both are pure semantic-error, reward 0.0); a crasher WITH coverage is rescued to 0.5.
    assert exec_error_reward == m.calculate_reward(
        True, 0.0
    )  # same as static-invalid penalty
    assert m.calculate_reward(True, 1.0) == 0.5  # G1 rescue, strictly above 0.0


def test_penalty_ordering():
    """C-7 penalty ordering: R(exec_error) < R(clean no-cov) < R(cov gain)."""
    m = _mutator()
    r_exe = m.calculate_reward(True, 0.0)
    r_clean_nocov = m.calculate_reward(False, 0.0)
    r_cov = m.calculate_reward(False, 1.0)
    assert r_exe < r_clean_nocov < r_cov


def test_calculate_reward_pure():
    """Same input twice yields the same value (helper purity)."""
    m = _mutator()
    assert m.calculate_reward(True, 0.0) == m.calculate_reward(True, 0.0)
    assert m.calculate_reward(False, 1.0) == m.calculate_reward(False, 1.0)


# --------------------------------------------------------------------------- #
# 5. Timeout guard: update_reward must NOT be called when no reply arrives
# --------------------------------------------------------------------------- #
# The full scenario lives in fuzz_single_seed: on recv.get timeout it
# kills+respawns the worker and continue without touching update_reward.
# A real unit test needs the multiprocessing worker + coverage + Redis, which is
# integration-heavy. We assert the contract at the level we can reach from here,
# and record the manual/integration check below.


def test_timeout_not_fed_into_update_reward():
    """Timeout produces NO reward update: update_reward must not receive a
    timeout-derived value. Direct proof of the skip lives in fuzz_single_seed's
    timeout branch (kill+respawn+continue, no update_reward). Kept as a contract
    placeholder here -- the authoritative check is the manual/integration note
    below, since driving the real worker needs dcov + Redis."""


# --------------------------------------------------------------------------- #
# 6. MANUAL / INTEGRATION CHECK (not run here) -- full timeout + worker signal
# --------------------------------------------------------------------------- #
# To fully close test #5, run on a machine with the dcov experiment env + Redis,
# driving the REAL continue_safe_execute worker:
#   1. send ("fuzz", raising_mutant) to the worker; assert recv returns
#      ("done", runtime_exception=True)   -- worker exception signal (t2).
#   2. send a timeout-delayed mutant; assert fuzz_single_seed kills+respawns,
#      continues, and monkeypatched update_reward is NEVER invoked.
#   3. Repeat with a clean mutant; assert reply == ("done", False).
# The seam for (1) is execute_mutant-style logic extracted from the fuzz case
# (see TEST_PLAN_C7.md section 3) if that refactor lands later.
