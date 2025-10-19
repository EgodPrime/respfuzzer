import inspect
from unittest.mock import Mock, patch

from tracefuzz.lib.parsers.function_parser import (
    from_builtin_function_type,
    from_function_type,
)
from tracefuzz.models import Argument


@patch("inspect.getsource")
@patch("inspect.signature")
def test_from_function_type(mock_signature, mock_getsource):
    """Test converting function type to Function object"""
    # Mock function object
    mock_func = Mock()
    mock_func.__name__ = "test_func"
    mock_func.__module__ = "test_module"

    # Mock source code
    source_code = '''
def test_func(a: int, b: str, c: List[int] = [1,2,3]) -> bool:
    """Test function"""
    return True
'''
    mock_getsource.return_value = source_code

    # Mock signature
    mock_signature.return_value = inspect.Signature(
        parameters=[
            inspect.Parameter(
                "a", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=int
            ),
            inspect.Parameter(
                "b", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str
            ),
            inspect.Parameter(
                "c", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=list
            ),
        ],
        return_annotation=bool,
    )

    mod = Mock()
    mod.__name__ = "test_module"

    # Test the function
    result = from_function_type(mod, mock_func)
    assert result.func_name == "test_module.test_func"
    assert len(result.args) == 3
    assert result.args[0].arg_name == "a"
    assert result.args[0].type == "int"
    assert result.args[1].arg_name == "b"
    assert result.args[1].type == "str"
    assert result.args[2].arg_name == "c"
    assert result.args[2].type == "list"
    assert result.ret_type == "bool"


@patch("inspect.getsource")
@patch("inspect.signature")
def test_from_function_type_no_source(mock_signature, mock_getsource):
    """Test converting function type to Function object when source is not available"""
    # Mock function object
    mock_func = Mock()
    mock_func.__name__ = "test_func"
    mock_func.__module__ = "test_module"

    # Mock getsource to raise OSError
    mock_getsource.side_effect = OSError

    # Mock signature
    mock_signature.return_value = inspect.Signature(
        parameters=[
            inspect.Parameter(
                "a", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=int
            ),
            inspect.Parameter(
                "b", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=str
            ),
        ],
        return_annotation=inspect.Signature.empty,
    )

    mod = Mock()
    mod.__name__ = "test_module"

    # Test the function
    result = from_function_type(mod, mock_func)
    assert result.func_name == "test_module.test_func"
    assert len(result.args) == 2
    assert result.args[0].arg_name == "a"
    assert result.args[0].type == "int"
    assert result.args[1].arg_name == "b"
    assert result.args[1].type == "str"
    assert result.ret_type == "unknown"


def test_from_builtin_function_type():
    """Test converting builtin function type to Function object"""
    # Test with valid pyi dict
    pyi_dict = {
        "source": "def test_func(a: int, b: str) -> bool:...",
        "args": [
            Argument(
                arg_name="a",
                type="int",
                pos_type=inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ),
            Argument(
                arg_name="b",
                type="str",
                pos_type=inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ),
        ],
        "ret_type_str": "bool",
    }

    mock_mod = Mock()
    mock_mod.__name__ = "test_module"
    mock_obj = Mock()
    mock_obj.__module__ = "test_module"
    mock_obj.__name__ = "test_func"

    result = from_builtin_function_type(pyi_dict, mock_mod, mock_obj)
    assert result.func_name == "test_module.test_func"
    assert len(result.args) == 2
    assert result.args[0].arg_name == "a"
    assert result.args[0].type == "int"
    assert result.args[1].arg_name == "b"
    assert result.args[1].type == "str"
    assert result.ret_type == "bool"


@patch("inspect.signature")
def test_from_function_type_signature_value_error(mock_signature):
    """If inspect.signature raises ValueError, from_function_type should return None"""
    mock_func = Mock()
    mock_func.__name__ = "broken"
    mock_func.__module__ = "test_module"

    # Make signature() raise ValueError
    mock_signature.side_effect = ValueError

    mod = Mock()
    mod.__name__ = "test_module"

    result = from_function_type(mod, mock_func)
    assert result is None
