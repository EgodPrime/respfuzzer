# pytest Plan for C-7: Runtime-Exception Negative Feedback

Design targets the code as it stands (verified by reading the source):
- `src/respfuzzer/lib/fuzz/llm_mutator.py` — `LLMMutator` class: `.mu`, `.update_reward(mutation_type, reward)`,
  `calculate_reward(has_syntax_error, coverage_gain)` -> [0,1], `select_mutation_type()`,
  `random_llm_mutate(no_check_semantic=False, max_retries=3, retry_delay=5.0)`.
- `src/respfuzzer/lib/fuzz/fuzz_exp.py` — parent reward wiring in `fuzz_single_seed` (live/only entry; the abandoned `fuzz_exp_new.py` was deleted):
  - cov_after > cov_before -> `Mutator.update_reward(mt, calculate_reward(False, 1.0))`  (raw reward 1.0; post-EWM mu 0.55)
  - else                   -> `Mutator.update_reward(mt, calculate_reward(False, 0.0))`  (raw reward 0.5; post-EWM mu 0.5)
  - `coverage_worker` swallows exceptions on both "execute" and "fuzz": `except Exception: pass` and always
    replies "done". `calculate_reward(True, 0.0)` is already used inside `random_llm_mutate` when
    `is_valid_semantic` returns None (compile/structural-invalid penalty).
- `tests/fuzz/test_llm_mutator_semantic.py` — C-6 style reference (10 cases, all pass; helper `_mutant(...)`).

## C-7 semantic target

A mutant that is *semantically valid* (passes `is_valid_semantic`) but whose *execution raises a runtime
exception* must be rewarded LOWER than a valid mutant that runs cleanly with no coverage gain. Concretely the
reward must satisfy (for exponent-weighted update with alpha=0.1):

    R(clean, no cov)  = 0.5  : calculate_reward(False, 0.0) = 0.5*1 + 0.5*0 = 0.5   (current / neutral)
    R(exec_error)     < 0.5  (C-7 negative feedback)   -- raw BASE = calculate_reward(True, 0.0) = 0.0
    R(exec_error BEFORE target called) == R(exec_error AFTER target called)   -- same penalty (no double-penalize)
    R(timeout)        == no reward update at all        -- timeout already kills+respawns worker; do NOT feed it
                                                        into update_reward
    R(cov gain)       = 1.0  : calculate_reward(False, 1.0) = 0.5 + 0.5 = 1.0   (unchanged positive)
    R(is_valid_semantic=False) = 0.0 (already penalized in random_llm_mutate via calculate_reward(True, 0.0))

(These are RAW calculate_reward returns. The update_reward EWM step (alpha=0.1) from a fresh mu=0.5
yields mu'=0.1*reward+0.9*0.5, i.e.: raw 1.0 -> mu 0.55 ; raw 0.5 -> mu 0.5 ; raw 0.0 -> mu 0.45.)
CORRECTED vs initial draft: do NOT use 0.55/0.45/0.05 as reward returns; those are EWM-mu/typo mixes.
Always assert on the RAW calculate_reward return unless deliberately testing the EWM transition (then
assert mu per the formula above).

---

## 1. Unit tests for the new reward helper

### 1a. If we add a dedicated helper (RECOMMENDED for unit-testability)

Recommend a pure function in `llm_mutator.py`, e.g.:

    def compute_semantic_reward(params: dict) -> float
        # params keys: exec_error:bool, target_called:bool, cov_gain:float|None, timeout:bool
    # Alternative that stays signature-compatible: extend calculate_reward with keyword args
    # calculate_reward(has_syntax_error, coverage_gain, /, *, exec_error=False, timeout=False, target_called=True).
    # The case table below applies unchanged to either structure.

**Case table (expected reward values, alpha=0.1, fresh `.mu[mt]=0.5`):**

| # | exec_error | target_called | cov_gain | timeout | expected reward | resulting mu[mt] |
|---|-----------|--------------|----------|---------|-----------------|------------------|
| 1 | True  | False | (error path: gain not accrued) | False | **0.0**   calc_reward(True,0.0)  | mu 0.45 |
| 2 | True  | True  | (error path: gain not accrued) | False | **0.0**   calc_reward(True,0.0)  | mu 0.45 |
| 3 | False | True  | 1.0 | False | **1.0**   calc_reward(False,1.0) | mu 0.55 |
| 4 | False | True  | 0.0 | False | **0.5**   calc_reward(False,0.0) | mu 0.5  |
| 5 | False | False | any | False | **0.5** neutral (no target call, no error) - E-15 neutral, NOT penalized | mu 0.5 |
| 6 | (any) | (any) | (any) | True | helper NOT called; caller must skip update_reward entirely | - |
| 7 | True  | True  | 1.0 (coverage before raise) | False | **0.5**   calc_reward(True,1.0) - G1: boundary mutant rescued, strictly below clean same-gain 1.0 | mu 0.5 |

Assertions per case: (a) reward return exact; (b) when fed through `update_reward`/EWM formula, `mu` moves in
the expected direction; (c) penalty ordering R(exec_error) < R(clean no-cov) < R(cov gain);
(d) purity: same input twice -> same value.

**Edge cases explicitly asserted:**
- Case 1 vs 2: identical penalty — target_called does NOT change the exec_error reward (no double-penalty, no cov confusion).
- Case 3 vs 4: clean cov-gain strictly greater than clean no-cov (positive feedback preserved).
- Case 6: timeout is NOT expressible as a reward; correct behavior is to NOT call update_reward. Assert by
  regression in section 2 (caller-level), not as a helper return.

### 1b. Minimal-signature change (no new helper) — fallback

If the team prefers touching only `calculate_reward`, add `has_exec_error: bool = False` keyword and subtract a
weight (e.g. `w_exec = 0.5`) against coverage/syntax so exec_error=True forces reward 0.0 regardless of cov.
Apply the same case table (rows 1,2,7 -> 0.0; rows 3,4,5 unchanged). Keep timeout out of the helper.

---

## 2. Regression test proving the BEFORE/AFTER reward differs (fails on old code)

File: `tests/fuzz/test_llm_mutator_c7_exec_feedback.py`.

    def test_exec_error_reward_differs_from_clean_no_cov_regression():
        # mut_error raises under exec (valid semantics), mut_clean runs clean;
        # monkeypatch random_llm_mutate to return a chosen (mutant, mutation_type) to control the path.
        mut_error = Mutant(func_id=1, seed_id=1, library_name="numpy", func_name="numpy.asarray",
                           args=[], function_call="import numpy\nresult = numpy.asarray(None)\n")
        mut_clean = Mutant(..., func_name="numpy.asarray", function_call="import numpy\nresult = numpy.asarray([1])\n")
        m = LLMMutator(seed)
        # Simulate parent wiring from fuzz_single_seed:
        # BEFORE patch: exec_error-mutant and clean-no-cov both -> update_reward(mt, calculate_reward(False, 0.0))
        #               => identical raw reward 0.5  (this test FAILS on old code: equality holds)
        # AFTER  patch: exec_error -> update_reward(mt, calculate_reward(True, 0.0)) raw reward 0.0 (mu 0.45)
        assert exec_error_reward < clean_no_cov_reward
        assert not (exec_error_reward == clean_no_cov_reward)

    def test_timeout_not_double_penalized():
        # On timeout, fuzz_single_seed kills+respawns worker and `continue`;
        # it must NOT call update_reward. Monkeypatch update_reward and assert it is never invoked for a
        # timeout mutant.

---

## 3. Worker / integration level test for the exception signal

### Current reality (read from `fuzz_exp.py`) — DONE: real worker integration tests added in `tests/fuzz/test_c7_worker_integration.py`
`coverage_worker` wraps both "execute" and "fuzz" exec in `try/except Exception: pass` and unconditionally
replies "done". The parent CANNOT today distinguish "clean no-cov" from "exec raised" — exactly the gap C-7
closes. A full worker test needs multiprocessing + coverage + instrument_function_via_path_ctx +
a real library package.

### Recommendation — extract a testable exception-detection seam (lighter than full worker)
Refactor the "fuzz" command body into a pure-ish helper (in `llm_mutator.py` or `fuzz_exp.py`):

    def execute_mutant(mutant, data_fuzz_per_seed) -> dict:
        """exec mutant.function_call under instrument; returns
           {'exec_error': bool, 'target_called': bool, 'exc_type': str|None}."""
        try:
            with instrument_function_via_path_ctx(mutant.func_name, data_fuzz_per_seed):
                exec(mutant.function_call)
            return {"exec_error": False, "target_called": True, "exc_type": None}
        except Exception as e:
            return {"exec_error": True, "target_called": True, "exc_type": type(e).__name__}

**`execute_mutant` is the seam to extract.** It does the real exec under instrumentation but is callable
in-process in a test with a trivial fake target — no pipe, no worker PID.

Unit tests on the seam (in-process, no worker):
- test_execute_mutant_clean: out["exec_error"] is False
- test_execute_mutant_raises: out["exec_error"] is True and out["exc_type"] == "ZeroDivisionError"
- test_execute_mutant_target_called_tracking (if seam tracks target_called): NO-call mutant -> target_called False

### Full worker test (optional, heavier)
If productivity allows, drive the REAL `coverage_worker` through `Pipe`: patch `instrument_function_via_path_ctx`
to a no-op context manager and `get_config` to a stub so no real coverage/network needed; send ("fuzz", raising_mutant)
and assert the reply carries exec_error, then a clean mutant does not. Otherwise keep ONLY the `execute_mutant`
seam test (recommended) and skip the full worker test.

---

## 4. Exact test files

| Action | File |
|--------|------|
| CREATE | tests/fuzz/test_llm_mutator_c7_exec_feedback.py — all C-7 tests (reward helper cases, regression, timeout guard). |
| CREATE (optional) | tests/fuzz/test_execute_mutant_seam.py — unit tests for extracted execute_mutant seam. |
| EXTEND | tests/fuzz/test_llm_mutator_semantic.py — only if helper touches is_valid_semantic guards; else leave untouched. |

---

## 5. Tests that might BREAK from the C-7 behavior change

Full-tree search of tests/ for calculate_reward | update_reward | random_llm_mutate | coverage_worker | LLMMutator:
**no existing test references any of them.** Findings:

- `tests/fuzz/test_llm_mutator_semantic.py` (10 cases) — only tests `is_valid_semantic`/ `_contains_target_call`;
  C-7 does NOT change `is_valid_semantic`, so these stay green. **Baseline: rerun to confirm.**
- `tests/fuzz/test_mutate.py` — classic value mutators, unrelated. Safe.
- `tests/fuzz/test_fuzz_function.py` — fuzz_function/Redis path, unrelated to reward wiring. Safe.
- `tests/fuzz/test_instrument.py` — safe as long as C-7 does NOT change `instrument_function_via_path_ctx`'s
  signature (only wraps its use).
- `tests/fuzz/test_fuzz_function.py` uses a real get_redis_client() fixture — if the worker integration test
  needs Redis, mirror that fixture or avoid Redis entirely via the seam approach.

**Guardrails:**
1. Keep `is_valid_semantic` byte-identical (C-6 tests are the regression net).
2. Keep calculate_reward(False,1.0)/(False,0.0) returns unchanged (raw 1.0 / 0.5; post-EWM mu 0.55 / 0.5) — only the exec_error path
   introduces a new lower value (raw 0.0 with no coverage gain).
3. Do NOT change update_reward's exponent-weighted formula.
4. Do NOT alter the timeout path (kill+respawn+continue) except to ADD a guard ensuring update_reward is never
   called on timeout; that guard cannot be relied upon by any existing test, so nothing breaks.
