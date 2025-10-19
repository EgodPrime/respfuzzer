from unittest import mock

import tracefuzz.lib.agentic_function_resolver as afr
from tracefuzz.lib.agentic_function_resolver import (
    Attempter,
    Judger,
    QueitExecutor,
    Reasoner,
    solve,
)
from tracefuzz.models import Argument, ExecutionResultType, Function


class MockFunction(Function):
    def __init__(self):
        super().__init__(
            id=1,
            func_name="ast.literal_eval",
            source="mock",
            args=[Argument(arg_name="s", type="str", pos_type="POSITIONAL_OR_KEYWORD")],
        )


def test_solve_retries_and_returns_code(monkeypatch):
    f = MockFunction()

    # First generate returns bad code, then good code
    gen = mock.Mock(side_effect=["bad code", "good code"])
    # executor should return OK for the accepted code (it's only called after Judger accepts)
    exec_ = mock.Mock(
        side_effect=[
            {
                "result_type": ExecutionResultType.OK,
                "ret_code": 0,
                "stdout": "ok",
                "stderr": "",
            },
        ]
    )
    reason = mock.Mock(return_value="hint")
    # attach mocks
    monkeypatch.setattr(Attempter, "generate", gen)
    monkeypatch.setattr(QueitExecutor, "execute", exec_)
    monkeypatch.setattr(Reasoner, "explain", reason)

    # Judger: first rejects, second accepts
    judge = mock.Mock(
        side_effect=[
            {"valid": False, "reason": "no call"},
            {"valid": True, "reason": "ok"},
        ]
    )
    monkeypatch.setattr(Judger, "judge", judge)

    code = solve(f)

    assert code == "good code"
    # ensure Attempter.generate called twice (1st rejected, 2nd accepted)
    assert gen.call_count == 2
    # Judger judged twice
    assert judge.call_count == 2
    # Executor executed only the accepted code once
    assert exec_.call_count == 1


def test_solve_returns_none_when_budget_exhausted(monkeypatch):
    f = MockFunction()
    gen = mock.Mock(return_value="bad code")
    exec_ = mock.Mock(
        return_value={
            "result_type": ExecutionResultType.ABNORMAL,
            "ret_code": 1,
            "stdout": "",
            "stderr": "err",
        }
    )
    reason = mock.Mock(return_value="no help")

    monkeypatch.setattr(Attempter, "generate", gen)
    monkeypatch.setattr(QueitExecutor, "execute", exec_)
    monkeypatch.setattr(Reasoner, "explain", reason)
    # make Judger always reject so budget will be consumed
    monkeypatch.setattr(
        Judger, "judge", mock.Mock(return_value={"valid": False, "reason": "no call"})
    )

    code = solve(f)
    assert code is None
    # Attempter should have been called multiple times until budget exhausted (budget default 10)
    assert gen.call_count >= 1
    # Judger should have been called same number of times as Attempter.generate
    assert Judger.judge.call_count == gen.call_count


def test_solve_handles_attempter_errors_then_succeeds(monkeypatch):
    f = MockFunction()
    # first call raises, second returns good code
    gen = mock.Mock(side_effect=[Exception("gen fail"), "good_code"])
    exec_ = mock.Mock(
        return_value={
            "result_type": ExecutionResultType.OK,
            "ret_code": 0,
            "stdout": "ok",
            "stderr": "",
        }
    )
    judge = mock.Mock(return_value={"valid": True, "reason": "ok"})

    monkeypatch.setattr(Attempter, "generate", gen)
    monkeypatch.setattr(QueitExecutor, "execute", exec_)
    monkeypatch.setattr(Judger, "judge", judge)

    # capture history passed to create_solve_history
    captured = {}

    def fake_create(function, history):
        captured["history"] = history

    monkeypatch.setattr(afr, "create_solve_history", fake_create)

    code = solve(f)
    assert code == "good_code"
    assert gen.call_count == 2
    # history should contain at least one attempter_error entry
    assert any(
        h.get("role", "").startswith("attempter")
        or h.get("role", "") == "attempter_error"
        for h in captured["history"]
    )


def test_solve_handles_judger_exception_then_succeeds(monkeypatch):
    f = MockFunction()
    gen = mock.Mock(side_effect=["code1", "code2"])
    # judge first raises, then accepts second code
    judge = mock.Mock(
        side_effect=[Exception("judge fail"), {"valid": True, "reason": "ok"}]
    )
    exec_ = mock.Mock(
        return_value={
            "result_type": ExecutionResultType.OK,
            "ret_code": 0,
            "stdout": "ok",
            "stderr": "",
        }
    )

    monkeypatch.setattr(Attempter, "generate", gen)
    monkeypatch.setattr(Judger, "judge", judge)
    monkeypatch.setattr(QueitExecutor, "execute", exec_)

    captured = {}

    def fake_create(function, history):
        captured["history"] = history

    monkeypatch.setattr(afr, "create_solve_history", fake_create)

    code = solve(f)
    assert code == "code2"
    # history should include a judger_error record
    assert any(h.get("role") == "judger_error" for h in captured["history"])


def test_solve_retries_after_abnormal_execution(monkeypatch):
    """Simulate executor returning ABNORMAL first, then OK after Reasoner guidance."""
    f = MockFunction()

    gen = mock.Mock(side_effect=["code1", "code2"])
    # Judger accepts both generated codes
    judge = mock.Mock(return_value={"valid": True, "reason": "ok"})
    # executor: first ABNORMAL, second OK
    exec_ = mock.Mock(
        side_effect=[
            {
                "result_type": ExecutionResultType.ABNORMAL,
                "ret_code": 1,
                "stdout": "",
                "stderr": "ValueError",
            },
            {
                "result_type": ExecutionResultType.OK,
                "ret_code": 0,
                "stdout": "ok",
                "stderr": "",
            },
        ]
    )
    # Reasoner provides a hint after the first failure
    reason = mock.Mock(return_value="use proper literal string")

    monkeypatch.setattr(Attempter, "generate", gen)
    monkeypatch.setattr(Judger, "judge", judge)
    monkeypatch.setattr(QueitExecutor, "execute", exec_)
    monkeypatch.setattr(Reasoner, "explain", reason)

    captured = {}

    def fake_create(function, history):
        captured["history"] = history

    monkeypatch.setattr(afr, "create_solve_history", fake_create)

    result = solve(f)
    assert result == "code2"
    # executor should have been called twice (first failed, second succeeded)
    assert exec_.call_count == 2
    # Reasoner should have been invoked for the first ABNORMAL execution
    assert reason.call_count >= 1
    # History should include executor and reasoner entries from the failed attempt
    assert any(h.get("role") == "executor" for h in captured["history"])
    assert any(h.get("role") == "reasoner" for h in captured["history"])
