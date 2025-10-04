import subprocess
from unittest import mock

import pytest

from tracefuzz.agentic_function_resolver import ExecutionResultType, QueitExecutor


# 测试正常执行成功的情况
@mock.patch("tracefuzz.agentic_function_resolver.subprocess.Popen")
def test_execute_success(mock_popen):
    # 模拟 Popen 返回值
    mock_proc = mock.Mock()
    mock_proc.communicate.return_value = ("stdout", "stderr")
    mock_proc.returncode = 0
    mock_popen.return_value = mock_proc

    code = "print('hello')"
    result = QueitExecutor().execute(code)

    assert result["result_type"] == ExecutionResultType.OK
    assert result["ret_code"] == 0
    assert result["stdout"] == "stdout"
    assert result["stderr"] == "stderr"


# 测试执行失败（非零退出码）
@mock.patch("tracefuzz.agentic_function_resolver.subprocess.Popen")
def test_execute_failure(mock_popen):
    mock_proc = mock.Mock()
    mock_proc.communicate.return_value = ("", "Error message")
    mock_proc.returncode = 1
    mock_popen.return_value = mock_proc

    code = "raise Exception('error')"
    result = QueitExecutor().execute(code)

    assert result["result_type"] == ExecutionResultType.ABNORMAL
    assert result["ret_code"] == 1
    assert result["stdout"] == ""
    assert result["stderr"] == "Error message"


# 测试执行超时
@mock.patch("tracefuzz.agentic_function_resolver.subprocess.Popen")
def test_execute_timeout(mock_popen):
    mock_proc = mock.Mock()
    mock_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="python", timeout=10)
    mock_popen.return_value = mock_proc

    code = "import time; time.sleep(100)"
    result = QueitExecutor().execute(code)

    assert result["result_type"] == ExecutionResultType.TIMEOUT
    assert result["ret_code"] == 1  # 默认超时返回码
    assert "after 10 seconds" in result["stderr"]


# 测试执行时抛出异常
@mock.patch("tracefuzz.agentic_function_resolver.subprocess.Popen")
def test_execute_exception(mock_popen):
    mock_popen.side_effect = Exception("Execution error")

    code = "import sys; sys.exit(1)"
    result = QueitExecutor().execute(code)

    assert result["result_type"] == ExecutionResultType.CALLFAIL
    assert "Execution error" in result["stderr"]
