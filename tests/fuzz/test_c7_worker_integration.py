"""C-7 worker IPC integration tests (G1 gap closure).

C-7 makes the LLM mutator use *runtime exceptions during execution* as semantic
negative feedback.  The live parent wiring in ``fuzz_exp.fuzz_single_seed`` puts
``(has_semantic_error=runtime_exception, coverage_gain)`` into
``LLMMutator.calculate_reward``.  Two things were previously only code-inspected:

1. The worker's IPC *exception signal*: the real ``continue_safe_execute`` child
   must reply ``("done", runtime_exception)`` where ``runtime_exception`` is
   True iff the mutated call raised at exec time (test 1) and False otherwise
   (test 2).
2. The *timeout guard*: when ``fuzz_single_seed``'s ``recv.get`` times out, the
   worker is killed+respawned and the loop ``continue``s -- so
   ``LLMMutator.update_reward`` must NEVER be called with a value derived from an
   incomplete execution (test 3).

These tests run against the real, installed environment (dcov 0.1.1 + Redis at
127.0.0.1:6379 + ``uv pip install -e .[dev]``).  No network / no LLM is needed.

TARGET CHOICE — statistics vs math
-----------------------------------
We target ``statistics.median`` (pure-Python stdlib), not ``math.ceil``.  Both
are importable, but a C-family builtin such as ``math`` can trip dcov's
``LoaderWrapper.add_library`` origin check on some platforms, and a pure-
Python stdlib module with a real ``.py`` origin is the most portably-safe
choice (still stdlib, still no numpy / no LLM / no network).
``statistics.median(None)`` -> ``sorted(None)`` -> TypeError for the raising
case; ``statistics.median([1, 2, 3])`` is clean.
"""

from multiprocessing import Process, Queue

from respfuzzer.lib.fuzz.fuzz_exp import continue_safe_execute, fuzz_single_seed
from respfuzzer.models import Mutant, Seed
from respfuzzer.utils.process_helper import kill_process_tree_linux
from respfuzzer.utils.redis_util import get_redis_client

# Unique shared-memory process indices so these workers never collide with a
# real fuzz_dataset/fuzz_one_library run (which starts at 4399).  Process 4398
# is the reserved parent bitmap; we avoid it entirely.
WORKER_PROC_RAISING = 4399
WORKER_PROC_CLEAN = 4499
WORKER_PROC_CONSTRUCTION = 4899

WORKER_REPLY_TIMEOUT = 20.0
CLEANUP_TIMEOUT = 10.0

# A mutant whose code would HANG if executed (time.sleep(3600)) -- the exact
# condition that makes the real worker miss its reply.  In the (hermetic)
# timeout test the code never actually runs.
HANGING_FUNCTION_CALL = "import time\ntime.sleep(3600)\n"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _mutant(function_call: str) -> Mutant:
    """A minimal valid Mutant targeting the stdlib ``statistics.median``."""
    return Mutant(
        func_id=1,
        seed_id=1,
        library_name="statistics",
        func_name="statistics.median",
        args=[],
        function_call=function_call,
    )


def _seed(function_call: str) -> Seed:
    return Seed(
        func_id=1,
        library_name="statistics",
        func_name="statistics.median",
        args=[],
        function_call=function_call,
    )


def _run_real_worker(send, recv, process_index, command, payload):
    """Spawn the REAL continue_safe_execute worker, send one fuzz command,
    return the reply, and hard-clean the child (exit + kill) in all paths."""
    process = Process(target=continue_safe_execute, args=(send, recv, process_index))
    process.start()
    child_pid = process.pid
    try:
        send.put((command, payload))
        reply = recv.get(timeout=WORKER_REPLY_TIMEOUT)
        return reply, process, child_pid
    finally:
        # Command the worker to exit, then force-clean it no matter what.
        try:
            send.put(("exit", None))
        except Exception:
            pass
        try:
            process.join(timeout=CLEANUP_TIMEOUT)
        except Exception:
            pass
        if process.is_alive():
            kill_process_tree_linux(process)
            process.join(timeout=CLEANUP_TIMEOUT)
        # Remove the redis "random_state:<child_pid>" keys this worker created
        # (fuzz_function in the child writes them).  Redis is available per env.
        try:
            get_redis_client().hdel("random_state", str(child_pid))
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 1. worker IPC exception signal: exec-time crash -> ("done", True)
# --------------------------------------------------------------------------- #


def test_worker_domain_exception_signal():
    """A mutant whose call *enters* the target function body and raises there
    (domain-level / expected behavior on invalid input) must now be signalled
    back by the REAL worker as ``("done", False)`` -- E-15: the TypeError from
    ``statistics.median(None)`` is raised *inside* ``median`` (its frame is on
    the traceback), so it is NOT treated as a semantic/construction error."""
    send, recv = Queue(), Queue()
    mutant = _mutant("import statistics\nstatistics.median(None)\n")

    reply, process, child_pid = _run_real_worker(
        send, recv, WORKER_PROC_RAISING, "fuzz", mutant
    )

    assert process.exitcode in (0, None), (
        f"worker crashed (exitcode={process.exitcode}); child_pid={child_pid}"
    )
    assert reply == ("done", False), (
        f"worker domain-exception signal wrong for median(None): got {reply!r}, ",
        "expected ('done', False) -- in-body TypeError is not a semantic error",
    )


def test_worker_construction_error_signal():
    """A mutant whose call fails at *binding* without entering the function body
    (missing required argument) is a construction-level semantic error and must
    be signalled ``("done", True)``.  ``statistics.median()`` -> TypeError
    "missing 1 required positional argument", so median's frame is never created
    and the call is a binding failure, not an in-body domain exception."""
    send, recv = Queue(), Queue()
    mutant = _mutant("import statistics\nstatistics.median()\n")

    reply, process, child_pid = _run_real_worker(
        send, recv, WORKER_PROC_CONSTRUCTION, "fuzz", mutant
    )

    assert process.exitcode in (0, None), (
        f"worker crashed (exitcode={process.exitcode}); child_pid={child_pid}"
    )
    assert reply == ("done", True), (
        f"worker construction-error signal wrong for median(): got {reply!r}, ",
        "expected ('done', True) -- binding-level TypeError is a semantic error",
    )


# --------------------------------------------------------------------------- #
# 2. worker clean signal: no exception -> ("done", False)
# --------------------------------------------------------------------------- #


def test_worker_clean_signal():
    """A mutant whose function_call does NOT raise must come back as
    ``("done", False)``.  ``statistics.median([1, 2, 3])`` succeeds; the
    deterministic data fuzzing that follows runs inside ``execute_once`` which
    swallows inner non-Timeout exceptions, so the top-level exec stays clean."""
    send, recv = Queue(), Queue()
    mutant = _mutant("import statistics\nresult = statistics.median([1, 2, 3])\n")

    reply, process, child_pid = _run_real_worker(
        send, recv, WORKER_PROC_CLEAN, "fuzz", mutant
    )

    assert process.exitcode in (0, None), (
        f"worker crashed (exitcode={process.exitcode}); child_pid={child_pid}"
    )
    assert reply == ("done", False), (
        f"worker clean signal wrong for non-raising mutant: got {reply!r}, "
        "expected ('done', False)"
    )


# --------------------------------------------------------------------------- #
# 3. timeout guard: fuzz_single_seed's timeout branch must NOT call update_reward
# --------------------------------------------------------------------------- #
# Driving the full real fuzz_single_seed needs a real LLM mutation call
# (LLMMutator.random_llm_mutate -> mutate_chain.invoke, network) plus real
# coverage bitmaps + Redis, which is heavy and slow.  We use the lighter,
# hermetic option B the task sanctions: patch the fuzz_exp seam so that
#   * LLMMutator.random_llm_mutate returns a HANGING mutant (no real LLM),
#   * recv.get raises TimeoutError (simulating that the exec never replied) and
#   * Process/BitmapManager/redis are faked (no real worker / shared mem).
# The EXCEPT branch in fuzz_single_seed is genuinely hit (it is the literal
# timeout path) at every iteration, which kills+respawns the (fake) worker and
# `continue`s past update_reward.  We assert update_reward was never called AND
# that a worker was respawned on timeouts (so the test really exercised the
# guard instead of silently skipping the loop body).


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
    def __init__(self):
        self.puts = []

    def put(self, item):
        self.puts.append(item)

    def get(self, timeout=None):
        raise TimeoutError("simulated exec timeout: no reply from worker")


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


class _FakeLLMMutator:
    update_calls = []

    def __init__(self, seed):
        self.seed = seed
        self.mutation_types = [0]

    def random_llm_mutate(self, *a, **k):
        # The mutant's code would HANG if executed (time.sleep(3600)), which is
        # exactly the condition that makes the real worker miss its reply.
        return _mutant(HANGING_FUNCTION_CALL), 0

    def update_reward(self, mutation_type, reward):
        _FakeLLMMutator.update_calls.append((mutation_type, reward))


def test_fuzz_single_seed_timeout_never_calls_update_reward(tmp_path, monkeypatch):
    """When fuzz_single_seed's recv.get times out it must kill+respawn the
    worker and CONTINUE without ever calling LLMMutator.update_reward."""
    # Reset per-test state.
    _FakeLLMMutator.update_calls = []
    _FakeProcess.spawned = []

    monkeypatch.setattr("respfuzzer.lib.fuzz.fuzz_exp.LLMMutator", _FakeLLMMutator)
    monkeypatch.setattr("respfuzzer.lib.fuzz.fuzz_exp.Queue", _FakeQueue)
    monkeypatch.setattr("respfuzzer.lib.fuzz.fuzz_exp.Process", _FakeProcess)
    monkeypatch.setattr(
        "respfuzzer.lib.fuzz.fuzz_exp.BitmapManager", _FakeBitmapManager
    )
    monkeypatch.setattr(
        "respfuzzer.lib.fuzz.fuzz_exp.get_redis_client", lambda: _FakeRedis()
    )
    # Keep the per-mutant mutants.data writes out of the real data dir.
    monkeypatch.setattr("respfuzzer.lib.fuzz.fuzz_exp.DATA_DIR", tmp_path)

    seed = _seed(HANGING_FUNCTION_CALL)
    # No exception should escape the timeout handling inside fuzz_single_seed.
    fuzz_single_seed(seed, process_index=4399)

    # The authoritative G1 assertion.
    assert _FakeLLMMutator.update_calls == [], (
        "update_reward was called even though every recv.get timed out: "
        f"{_FakeLLMMutator.update_calls!r}"
    )
    # Prove the timeout branch (kill+respawn) really ran, rather than the loop
    # being skipped: one process per initial spawn + one respawn per iteration.
    # llm_fuzz_per_seed default is 10, so >= 2 spawns must have happened.
    assert len(_FakeProcess.spawned) >= 2, (
        "no worker respawn observed; the timeout/kill+respawn branch was "
        f"probably not exercised (spawned={len(_FakeProcess.spawned)})"
    )
