import signal
from unittest.mock import MagicMock, mock_open, patch

import pytest

from mplfuzz.fuzz.fuzz_function import (
    convert_to_param_list,
    execute_once,
    fuzz_function,
    handle_timeout,
    reconvert_param_list,
)


def test_execute_once_success():
    mock_api = MagicMock(return_value="success")
    result = execute_once(mock_api, 1, 2, 3)
    assert result == "success"


def test_execute_once_timeout():
    mock_api = MagicMock(side_effect=TimeoutError("Execution timeout"))
    with pytest.raises(TimeoutError):
        execute_once(mock_api, 1, 2, 3)


def test_execute_once_exception():
    mock_api = MagicMock(side_effect=Exception("Something went wrong"))
    with pytest.raises(Exception):
        execute_once(mock_api, 1, 2, 3)


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


@patch("mplfuzz.fuzz.fuzz_function.logger")
def test_fuzz_api_no_args(
    mock_logger,
):
    mock_api = MagicMock()
    mock_api.__module__ = "test_module"
    mock_api.__name__ = "test_function"
    fuzz_function(mock_api)
    mock_logger.info.assert_any_call("test_module.test_function has no arguments, execute only once.")


@patch("mplfuzz.fuzz.fuzz_function.logger")
def test_fuzz_api_with_args(
    mock_logger,
):
    mock_api = MagicMock()
    mock_api.__module__ = "test_module"
    mock_api.__name__ = "test_function"
    args = (1, 2, 3)
    kwargs = {"a": 4, "b": 5}
    fuzz_function(mock_api, *args, **kwargs)
    mock_logger.info.assert_any_call("Start fuzz test_module.test_function")
