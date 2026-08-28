from unittest.mock import MagicMock, patch

import pytest
from respfuzzer.lib.fuzz.fuzz_function import (
    convert_to_param_list,
    execute_once,
    fuzz_function,
    reconvert_param_list,
)
from respfuzzer.utils.redis_util import get_redis_client
import math


@pytest.fixture(autouse=True)
def clean_exec_cnt():
    rc = get_redis_client()
    rc.hset("fuzz", "exec_cnt", 0)
    rc.delete("exec_record")
    yield
    rc.delete("fuzz")
    rc.delete("exec_record")


def test_execute_once_success():
    mock_function = MagicMock(return_value="success")
    result = execute_once(mock_function, 1, 2, 3)
    assert result == "success"


def test_convert_to_param_list():
    args = (1, 2, 3)
    kwargs = {"a": 4, "b": 5}
    result = convert_to_param_list(*args, **kwargs)
    assert result == [1, 2, 3, 4, 5]


def test_reconvert_param_list():
    param_list = [1, 2, 3, 4, 5]
    args = (1, 2, 3)
    kwargs = {"a": 4, "b": 5}
    result_args, result_kwargs = reconvert_param_list(param_list, *args, **kwargs)
    assert result_args == (1, 2, 3)
    assert result_kwargs == {"a": 4, "b": 5}


@patch("respfuzzer.lib.fuzz.fuzz_function.logger")
def test_fuzz_function_no_args(
    mock_logger,
):
    # No user args -> param_list empty -> early return AFTER the start log,
    # and WITHOUT the "done" log (impl returns before logging done).
    fuzz_function(math.ceil, data_fuzz_per_seed=3)
    # start log is now via logger.info and records the worker pid + initial RNG seed
    start_msgs = [c.args[0] for c in mock_logger.info.call_args_list]
    assert any(isinstance(s, str) and s.startswith("RespFuzzer start fuzz math.ceil (worker_pid=") for s in start_msgs)


@patch("respfuzzer.lib.fuzz.fuzz_function.logger")
def test_fuzz_function_with_args(
    mock_logger,
):
    args = (1, 2, 3)
    kwargs = {"a": 4, "b": 5}
    # data_fuzz_per_seed is the 2nd positional arg; user args follow.
    fuzz_function(math.ceil, 3, *args, **kwargs)
    start_msgs = [c.args[0] for c in mock_logger.info.call_args_list]
    assert any(isinstance(s, str) and s.startswith("RespFuzzer start fuzz math.ceil (worker_pid=") for s in start_msgs)
    mock_logger.debug.assert_any_call("RespFuzzer fuzz math.ceil done")
