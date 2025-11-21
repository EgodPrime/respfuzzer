from unittest.mock import MagicMock, patch

import pytest

from respfuzzer.lib.fuzz.fuzz_function import (
    convert_to_param_list,
    execute_once,
    fuzz_function,
    reconvert_param_list,
)
from respfuzzer.utils.redis_util import get_redis_client


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
    mock_function = MagicMock()
    mock_function.__module__ = "test_module"
    mock_function.__name__ = "test_function"
    fuzz_function(mock_function)
    mock_logger.info.assert_any_call(
        "test_module.test_function has no arguments, execute only once."
    )


@patch("respfuzzer.lib.fuzz.fuzz_function.logger")
def test_fuzz_function_with_args(
    mock_logger,
):
    mock_function = MagicMock()
    mock_function.__module__ = "test_module"
    mock_function.__name__ = "test_function"
    args = (1, 2, 3)
    kwargs = {"a": 4, "b": 5}
    fuzz_function(mock_function, *args, **kwargs)
    mock_logger.info.assert_any_call("Start fuzz test_module.test_function")
