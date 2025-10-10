from unittest import mock
import subprocess

from tracefuzz.agentic_function_resolver import QueitExecutor
from tracefuzz.models import ExecutionResultType


def test_execute_ok(monkeypatch):
    mock_proc = mock.Mock()
    mock_proc.communicate.return_value = ("out", "")
    mock_proc.returncode = 0

    monkeypatch.setattr("tracefuzz.agentic_function_resolver.subprocess.Popen", lambda *a, **k: mock_proc)

    res = QueitExecutor().execute("print('x')")
    assert res["result_type"] == ExecutionResultType.OK
    assert res["ret_code"] == 0
    assert res["stdout"] == "out"


def test_execute_abnormal(monkeypatch):
    mock_proc = mock.Mock()
    mock_proc.communicate.return_value = ("", "err")
    mock_proc.returncode = 1

    monkeypatch.setattr("tracefuzz.agentic_function_resolver.subprocess.Popen", lambda *a, **k: mock_proc)

    res = QueitExecutor().execute("raise Exception('e')")
    assert res["result_type"] == ExecutionResultType.ABNORMAL
    assert res["ret_code"] == 1
    assert "err" in res["stderr"] or res["stderr"] == "err"


def test_execute_timeout(monkeypatch):
    mock_proc = mock.Mock()
    mock_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="python", timeout=10)

    monkeypatch.setattr("tracefuzz.agentic_function_resolver.subprocess.Popen", lambda *a, **k: mock_proc)

    res = QueitExecutor().execute("import time; time.sleep(100)")
    assert res["result_type"] == ExecutionResultType.TIMEOUT
    # implementation sets ret_code 124 on timeout cleanup
    assert res["ret_code"] == 124
    assert isinstance(res["stderr"], str)


def test_execute_callfail(monkeypatch):
    def raise_on_popen(*a, **k):
        raise Exception("spawn failed")

    monkeypatch.setattr("tracefuzz.agentic_function_resolver.subprocess.Popen", raise_on_popen)

    res = QueitExecutor().execute("print('x')")
    assert res["result_type"] == ExecutionResultType.CALLFAIL
    assert "spawn failed" in res["stderr"]
