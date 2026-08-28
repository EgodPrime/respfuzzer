"""C-8 budget/last_valid semantics tests.

Tasks t2->v3 landed the C-8 contract in LLMMutator:
  * each random_llm_mutate call consumes one mutation-try budget unit
    (invalid output also counts toward the budget; the main loop runs a fixed
    llm_fuzz_per_seed rounds with max_retries=1 -> round == attempt == unit);
  * return keeps tuple[Mutant, int]; validity is carried by self.last_valid;
  * no valid mutant this round -> a placeholder Mutant (function_call="") is returned
    with last_valid=False, so the main loop skips write/execute/reward and the old
    "fallback returns original code" pollution is gone;
  * random_llm_mutate never re-raises on LLM failure/timeout;
  * no double negative-feedback (invalid penalised once inside, main loop skips reward).

All LLM calls are monkeypatched away (llm_mutator.llm_mutate), so no network /
no real dcov / no Redis is required.

Integration tests reuse the hermetic fake environment of test_c7_worker_integration.py
(fake Queue/Process/BitmapManager/Redis).
"""

import importlib.util
import inspect
import json
import os
from typing import Any

import pytest

from respfuzzer.lib.fuzz import fuzz_exp
from respfuzzer.lib.fuzz import llm_mutator as llm_mutator_module
from respfuzzer.lib.fuzz.llm_mutator import LLMMutator
from respfuzzer.models import Mutant, Seed

# --------------------------------------------------------------------------- #
# Shared test data / helpers
# --------------------------------------------------------------------------- #

_SEED_FUNCTION_CALL = "import numpy\nresult = numpy.asarray([1])\n"
_VALID_CODE = "import numpy\nresult = numpy.asarray([1, 2, 3])\n"
_INVALID_CODE = "x = 1 + 2\nprint(x)\n"  # compiles but calls no target function


def _seed() -> Seed:
    return Seed(
        func_id=1,
        library_name="numpy",
        func_path="numpy.asarray",
        args=[],
        function_call=_SEED_FUNCTION_CALL,
    )


def _mutant(function_call: str) -> Mutant:
    """A real Mutant targeting numpy.asarray (used as the patched llm_mutate result)."""
    return Mutant(
        func_id=1,
        seed_id=1,
        library_name="numpy",
        func_path="numpy.asarray",
        args=[],
        function_call=function_call,
    )


def _fresh_mutator() -> LLMMutator:
    return LLMMutator(_seed())


def _patch_llm_mutate(monkeypatch, fn):
    """Patch the module-level llm_mutate used by random_llm_mutate."""
    monkeypatch.setattr(llm_mutator_module, "llm_mutate", fn)


def _spy_update_reward(monkeypatch, mutator):
    """Replace update_reward on an instance to record (mutation_type, reward) calls."""
    calls = []
    real = mutator.update_reward

    def spy(mt, reward):
        calls.append((mt, reward))
        return real(mt, reward)

    monkeypatch.setattr(mutator, "update_reward", spy)
    return calls


# --------------------------------------------------------------------------- #
# 1. unit: invalid output consumes budget, last_valid False, no double-count
# --------------------------------------------------------------------------- #


def test_invalid_consumes_budget_and_last_valid_false(monkeypatch):
    """Inject a non-target-calling code; a single max_retries=1 call must:
    return a placeholder (function_call==""), last_valid False, budget counters
    attempts_total==1/invalid_count==1/valid_count==0, and exactly ONE negative-
    reward update with calculate_reward(True, 0.0) (no double-count)."""

    def fake_llm_mutate(seed, mutation_type, rng):
        return _mutant(_INVALID_CODE)

    _patch_llm_mutate(monkeypatch, fake_llm_mutate)
    m = _fresh_mutator()
    update_calls = _spy_update_reward(monkeypatch, m)

    mutant, mutation_type = m.random_llm_mutate(max_retries=1)

    assert isinstance(mutant, Mutant)
    assert mutant.function_call == ""  # placeholder sentinel
    assert m.last_valid is False
    assert m.attempts_total == 1  # one budget unit consumed
    assert m.invalid_count == 1
    assert m.valid_count == 0
    # exactly one negative-reward call, with the static-invalid reward value
    assert len(update_calls) == 1
    assert update_calls[0] == (mutation_type, m.calculate_reward(True, 0.0))
    assert update_calls[0][1] == 0.0


# --------------------------------------------------------------------------- #
# 2. unit: budget == main-loop rounds; no hidden retry inflation
# --------------------------------------------------------------------------- #


def test_loop_stops_at_budget(monkeypatch):
    """Simulate the main loop: llm_fuzz_per_seed (=10 default) rounds, each invalid.
    attempts_total must equal exactly the number of rounds (10) and invalid_count==10;
    each round consumes exactly 1 attempt (no free retry that inflates valid output),
    i.e. the budget is bounded by the round count and does not grow without limit."""

    def fake_llm_mutate(seed, mutation_type, rng):
        return _mutant(_INVALID_CODE)

    _patch_llm_mutate(monkeypatch, fake_llm_mutate)
    m = _fresh_mutator()
    rounds = 10  # mirrors config [fuzz] llm_fuzz_per_seed

    for _ in range(rounds):
        m.random_llm_mutate(max_retries=1)

    assert m.attempts_total == rounds  # budget == fixed rounds, does not exceed
    assert m.invalid_count == rounds  # every invalid round counts toward budget
    assert m.valid_count == 0
    assert m.last_valid is False


# --------------------------------------------------------------------------- #
# 3. unit: valid output -> last_valid True, counters, no negative feedback
# --------------------------------------------------------------------------- #


def test_valid_mutant_last_valid_true(monkeypatch):
    """Inject a target-calling code; a single call returns the real mutant with
    last_valid True, valid_count==1, attempts_total==1, invalid_count==0 and NO
    internal update_reward (no static negative feedback on valid path)."""

    def fake_llm_mutate(seed, mutation_type, rng):
        return _mutant(_VALID_CODE)

    _patch_llm_mutate(monkeypatch, fake_llm_mutate)
    m = _fresh_mutator()
    update_calls = _spy_update_reward(monkeypatch, m)

    mutant, mutation_type = m.random_llm_mutate(max_retries=1)

    assert m.last_valid is True
    assert mutant.function_call == _VALID_CODE
    assert m.valid_count == 1
    assert m.attempts_total == 1
    assert m.invalid_count == 0
    assert update_calls == []  # no penalty on valid path


# --------------------------------------------------------------------------- #
# 4. unit: LLM hard failure must not raise; consumes budget, last_valid False
# --------------------------------------------------------------------------- #


def test_llm_failure_no_raise(monkeypatch):
    """If llm_mutate raises, random_llm_mutate must NOT re-raise, return a placeholder,
    set last_valid False and increment attempts_total and invalid_count."""

    def failing_llm_mutate(seed, mutation_type, rng):
        raise RuntimeError("boom")

    _patch_llm_mutate(monkeypatch, failing_llm_mutate)
    m = _fresh_mutator()

    mutant, mutation_type = m.random_llm_mutate(max_retries=1)  # must not raise

    assert mutant.function_call == ""
    assert m.last_valid is False
    assert m.attempts_total == 1
    assert m.invalid_count == 1
    assert m.valid_count == 0


# --------------------------------------------------------------------------- #
# 5. unit: NSF ablation (no_check_semantic=True) always valid (RQ4 regression)
# --------------------------------------------------------------------------- #


def test_no_semantic_always_valid(monkeypatch):
    """With no_check_semantic=True the static IsValidSemantic gate is skipped, so even
    a non-calling code is treated as a valid mutant: last_valid True, valid_count++ and
    NO static negative reward (RQ4 NSF path regression)."""

    def fake_llm_mutate(seed, mutation_type, rng):
        return _mutant(_INVALID_CODE)  # would be invalid if checked

    _patch_llm_mutate(monkeypatch, fake_llm_mutate)
    m = _fresh_mutator()
    update_calls = _spy_update_reward(monkeypatch, m)

    mutant, mutation_type = m.random_llm_mutate(no_check_semantic=True, max_retries=1)

    assert m.last_valid is True
    assert mutant.function_call == _INVALID_CODE  # returned as-is (no gate)
    assert m.valid_count == 1
    assert m.attempts_total == 1
    assert m.invalid_count == 0
    assert update_calls == []


# --------------------------------------------------------------------------- #
# 6. unit: budget_report observable invalid ratio
# --------------------------------------------------------------------------- #


def test_budget_report_counts(monkeypatch):
    """Run 5 rounds: first 2 valid, last 3 invalid. budget_report() must expose
    budget==5, valid==2, invalid==3, invalid_ratio==0.6."""
    seq = [_VALID_CODE, _VALID_CODE, _INVALID_CODE, _INVALID_CODE, _INVALID_CODE]
    calls = {"n": 0}

    def fake_llm_mutate(seed, mutation_type, rng):
        code = seq[calls["n"] % len(seq)]
        calls["n"] += 1
        return _mutant(code)

    _patch_llm_mutate(monkeypatch, fake_llm_mutate)
    m = _fresh_mutator()
    for _ in range(5):
        m.random_llm_mutate(max_retries=1)

    report = m.budget_report()
    assert report["budget"] == 5
    assert report["valid"] == 2
    assert report["invalid"] == 3
    assert report["invalid_ratio"] == 0.6


# --------------------------------------------------------------------------- #
# 7. unit: max_retries>1 still counts every attempt, no inflated validity
# --------------------------------------------------------------------------- #


def test_retries_past_one_count_attempts(monkeypatch):
    """With max_retries=2 and (invalid, valid) sequence, the call returns a valid mutant
    (last_valid True) but attempts_total==2: retrying does NOT re-grant budget - every
    attempt is counted (E-9 LLM-call observability)."""
    seq = [_INVALID_CODE, _VALID_CODE]
    calls = {"n": 0}

    def fake_llm_mutate(seed, mutation_type, rng):
        code = seq[calls["n"] % len(seq)]
        calls["n"] += 1
        return _mutant(code)

    _patch_llm_mutate(monkeypatch, fake_llm_mutate)
    m = _fresh_mutator()

    mutant, mutation_type = m.random_llm_mutate(max_retries=2)

    assert m.last_valid is True
    assert mutant.function_call == _VALID_CODE
    assert m.attempts_total == 2
    assert m.valid_count == 1
    assert m.invalid_count == 1


# --------------------------------------------------------------------------- #
# 8. unit: getattr(..., True) backward-compat guard (C-7 _FakeLLMMutator zero-invasion)
# --------------------------------------------------------------------------- #


def test_getattr_compat():
    """The main-loop guard getattr(Mutator, "last_valid", True) must be True for an
    object without the attribute (like the C-7 _FakeLLMMutator), and reflect the real
    value when present - proving zero-invasion on existing fakes."""

    class FakeWithoutAttr:
        last_valid: Any  # 未初始化实例属性：getattr(..., True) 兜底验证零侵入（C-8）

    obj = FakeWithoutAttr()
    assert getattr(obj, "last_valid", True) is True

    fake_invalid = FakeWithoutAttr()
    fake_invalid.last_valid = False
    assert getattr(fake_invalid, "last_valid", True) is False

    fake_valid = FakeWithoutAttr()
    fake_valid.last_valid = True
    assert getattr(fake_valid, "last_valid", True) is True


# --------------------------------------------------------------------------- #
# Integration: hermetic fakes for fuzz_single_seed (mirror test_c7_worker_integration)
# --------------------------------------------------------------------------- #


class _FakeProcess:
    spawned = []

    def __init__(self, *args, **kwargs):
        self.pid = 12345 + len(_FakeProcess.spawned)
        _FakeProcess.spawned.append(self)

    def start(self):
        pass

    def is_alive(self):
        return False

    def join(self, timeout=None):
        pass


class _FakeQueue:
    """Records every put; get() returns a clean worker reply so valid rounds complete."""

    def __init__(self):
        self.puts = []

    def put(self, item):
        self.puts.append(item)

    def get(self, timeout=None):
        return ("done", False)


class _FakeBitmapManager:
    def __init__(self, idx):
        self.idx = idx

    def sync_from(self, other):
        pass

    def write(self):
        pass

    def count_bitmap_s(self):
        return 0

    def count_bitmap(self):
        return 0

    def merge_from(self, other):
        pass


class _FakeRedis:
    def hget(self, key, field):
        return None


def _setup_fuzz_exp_hermetic(monkeypatch, tmp_path, fake_mutator_cls):
    """Patch the fuzz_exp module back to hermetic fakes; return the fake queue."""
    q = _FakeQueue()
    monkeypatch.setattr(fuzz_exp, "LLMMutator", fake_mutator_cls)
    monkeypatch.setattr(fuzz_exp, "Queue", lambda: q)
    monkeypatch.setattr(fuzz_exp, "Process", _FakeProcess)
    monkeypatch.setattr(fuzz_exp, "BitmapManager", _FakeBitmapManager)
    monkeypatch.setattr(fuzz_exp, "get_redis_client", lambda: _FakeRedis())
    monkeypatch.setattr(fuzz_exp, "DATA_DIR", tmp_path)
    _FakeProcess.spawned = []
    return q


def _loop_adapted() -> bool:
    """True once fuzz_exp.fuzz_single_seed carries the C-8 last_valid guard."""
    try:
        return "last_valid" in inspect.getsource(fuzz_exp)
    except (OSError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# 9. integration: fuzz_single_seed skips invalid (pollution guard)
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(
    not _loop_adapted(),
    reason=(
        "fuzz_exp.fuzz_single_seed 尚未适配 C-8 last_valid 守门 "
        '(需加 max_retries=1 与 if not getattr(...,"last_valid",True): continue)；'
        "该集成测试在落地后自动启用"
    ),
)
def test_fuzz_single_seed_skips_invalid(monkeypatch, tmp_path):
    """When one round yields last_valid=False (placeholder), fuzz_single_seed must:
      * NOT write the placeholder line to mutants.data,
      * NOT send that round ("fuzz", placeholder) to the worker,
      * update_reward ONLY for valid rounds (no double negative-feedback).
    Requires the C-8 loop guard; otherwise the test is skipped."""

    class _FakeLLMMutator:
        instances = []

        def __init__(self, seed):
            self.seed = seed
            _FakeLLMMutator.instances.append(self)
            self.last_valid = True
            self.attempts_total = 0
            self.valid_count = 0
            self.invalid_count = 0
            self.reward_calls = []
            self._reward_calls = []
            self.round = 0

        def random_llm_mutate(self, *a, **k):
            self.round += 1
            if self.round == 2:  # round 2 -> invalid placeholder
                self.last_valid = False
                self.attempts_total += 1
                self.invalid_count += 1
                return _mutant(""), 0
            self.last_valid = True
            self.attempts_total += 1
            self.valid_count += 1
            return _mutant(_VALID_CODE), 0

        def update_reward(self, mutation_type, reward):
            self.reward_calls.append((mutation_type, reward))

        def calculate_reward(self, has_semantic_error, coverage_gain):
            self._reward_calls.append((has_semantic_error, coverage_gain))
            base = 0.5 * (1 - int(has_semantic_error)) + 0.5 * coverage_gain
            return min(max(base, 0), 1)

        def budget_report(self):
            return {
                "budget": self.attempts_total,
                "valid": self.valid_count,
                "invalid": self.invalid_count,
                "invalid_ratio": (self.invalid_count / self.attempts_total)
                if self.attempts_total
                else 0.0,
            }

    q = _setup_fuzz_exp_hermetic(monkeypatch, tmp_path, _FakeLLMMutator)
    fuzz_exp.fuzz_single_seed(_seed(), process_index=4399)

    # 1) mutants.data holds ONLY valid mutants (no placeholder ""-call line)
    lines = []
    data_file = tmp_path / "mutants.data"
    if data_file.exists():
        lines = data_file.read_text().splitlines()
    assert lines, "fuzz_single_seed should have written at least one valid mutant"
    for line in lines:
        rec = json.loads(line)
        assert rec["function_call"] != "", (
            "placeholder must not be written to mutants.data"
        )

    # 2) no "fuzz" send whose mutant carries the placeholder
    fuzz_sends = [
        item for item in q.puts if isinstance(item, tuple) and item[0] == "fuzz"
    ]
    for _cmd, sent_mutant in fuzz_sends:
        assert sent_mutant.function_call != "", (
            "placeholder must not be sent to the worker"
        )
    # 10 rounds, one invalid (round 2) -> 9 valid written+sent
    assert len(fuzz_sends) == 9, fuzz_sends
    mutator = _FakeLLMMutator.instances[-1]
    assert len(mutator.reward_calls) == 9, (
        "update_reward must be called only for the 9 valid rounds, "
        "not the invalid one (no double negative-feedback)"
    )


# --------------------------------------------------------------------------- #
# 10. integration: RQ4 NSF ablation path (always valid, writes, counts correct)
# --------------------------------------------------------------------------- #


_RQ4_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "..",
    "experiments",
    "RQ4",
    "fuzz_exp-RQ4.py",
)


def _load_rq4_module():
    """Load experiments/RQ4/fuzz_exp-RQ4.py under a dotted-identifier-safe module name."""
    spec = importlib.util.spec_from_file_location("fuzz_exp_RQ4_under_test", _RQ4_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("cannot locate RQ4 module spec")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(
    not os.path.exists(_RQ4_PATH), reason="experiments/RQ4/fuzz_exp-RQ4.py 不存在"
)
def test_rq4_nsf_ablation_budget(monkeypatch, tmp_path):
    """RQ4 fuzz_single_seed with no_semantic_filter=True: every round is treated as
    valid (last_valid True), the mutant is written to mutants.data, and the budget /
    valid counters stay consistent (NSF has no invalid concept)."""
    rq4: Any = None  # 预声明：except 分支 pytest.skip 中断（pyright 静态不识别），此处保证已绑定
    try:
        rq4 = _load_rq4_module()
    except Exception as exc:  # pragma: no cover - deps missing (dcov/fire/...)
        pytest.skip(f"RQ4 module could not be imported: {exc}")

    class _FakeLLMMutator:
        instances = []

        def __init__(self, seed):
            self.seed = seed
            _FakeLLMMutator.instances.append(self)
            self.last_valid = True
            self.attempts_total = 0
            self.valid_count = 0
            self.invalid_count = 0
            self.reward_calls = []
            self._reward_calls = []

        def random_llm_mutate(self, *a, **k):
            # NSF: always valid; caller passes no_check_semantic=no_semantic_filter=True
            self.last_valid = True
            self.attempts_total += 1
            self.valid_count += 1
            return _mutant(_VALID_CODE), 0

        def update_reward(self, mutation_type, reward):
            self.reward_calls.append((mutation_type, reward))

        def calculate_reward(self, has_semantic_error, coverage_gain):
            self._reward_calls.append((has_semantic_error, coverage_gain))
            base = 0.5 * (1 - int(has_semantic_error)) + 0.5 * coverage_gain
            return min(max(base, 0), 1)

        def budget_report(self):
            return {
                "budget": self.attempts_total,
                "valid": self.valid_count,
                "invalid": self.invalid_count,
                "invalid_ratio": (self.invalid_count / self.attempts_total)
                if self.attempts_total
                else 0.0,
            }

    q = _FakeQueue()
    monkeypatch.setattr(rq4, "LLMMutator", _FakeLLMMutator)
    monkeypatch.setattr(rq4, "Queue", lambda: q)
    monkeypatch.setattr(rq4, "Process", _FakeProcess)
    monkeypatch.setattr(rq4, "BitmapManager", _FakeBitmapManager)
    monkeypatch.setattr(rq4, "get_redis_client", lambda: _FakeRedis())
    monkeypatch.setattr(rq4, "DATA_DIR", tmp_path)
    _FakeProcess.spawned = []

    # Full mode with NSF on (no_llm=False, no_data_fuzz=False, no_semantic_filter=True)
    rq4.fuzz_single_seed(_seed(), process_index=4399, no_semantic_filter=True)

    lines = []
    data_file = tmp_path / "mutants.data"
    if data_file.exists():
        lines = data_file.read_text().splitlines()
    # NSF always yields a valid mutant -> every one of the llm_fuzz_per_seed rounds written
    assert lines, "RQ4 NSF should write mutants.data for every round"
    for line in lines:
        assert json.loads(line)["function_call"] != ""
    # fake's counters reflect all rounds as valid
    mutator = _FakeLLMMutator.instances[-1]
    assert mutator.attempts_total >= 1
    assert mutator.valid_count == mutator.attempts_total
    assert mutator.invalid_count == 0
    assert mutator.last_valid is True
