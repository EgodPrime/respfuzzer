import os
import unittest
from unittest.mock import Mock, patch

import pytest

from mplfuzz.library_visitor import LibraryVisitor
from mplfuzz.models import API


def test_visit_numpy():
    """Test basic functionality with numpy library"""
    visitor = LibraryVisitor("numpy")
    for api in visitor.visit():
        assert isinstance(api, API)
        break


def test_visit_nonexistent_library():
    """Test error handling when library is not found"""
    visitor = LibraryVisitor("nonexistent_library")
    apis = list(visitor.visit())
    assert len(apis) == 0


def test_find_all_pyi_functions():
    """Test pyi file parsing functionality"""
    # Create a mock library with a pyi file
    with patch("mplfuzz.library_visitor.importlib.util.find_spec") as mock_find_spec:
        mock_spec = Mock()
        mock_spec.origin = "/mock/path/to/library/__init__.py"
        mock_find_spec.return_value = mock_spec

        # Create a mock pyi file
        with patch("builtins.open", unittest.mock.mock_open(read_data="def test_func(a: int) -> str: ...")):
            visitor = LibraryVisitor("mock_library")
            visitor.find_all_pyi_functions()

            # Verify the pyi cache was populated
            assert "test_func" in visitor.pyi_cache
            assert visitor.pyi_cache["test_func"]["name"] == "test_func"
            assert visitor.pyi_cache["test_func"]["ret_type_str"] == "str"


def test_visit_with_pyi_cache():
    """Test that pyi cache is used when available"""
    visitor = LibraryVisitor("mock_library")
    # Mock the pyi cache
    visitor.pyi_cache = {
        "test_func": {
            "name": "test_func",
            "source": "def test_func(a: int) -> str: ...",
            "args": [],
            "ret_type_str": "str",
            "file_path": "/mock/path/test.pyi",
        }
    }

    # Mock the from_builtin_function_type function
    with patch("mplfuzz.library_visitor.from_builtin_function_type") as mock_from_builtin:
        mock_from_builtin.return_value = Mock(value=Mock(api_name="test_func"))

        apis = list(visitor.visit())
        assert len(apis) == 1
        assert apis[0].api_name == "test_func"
        mock_from_builtin.assert_called_once()


def test_visit_with_function():
    """Test that regular functions are properly parsed"""
    visitor = LibraryVisitor("mock_library")

    # Mock a function
    mock_function = Mock()
    mock_function.__name__ = "test_func"
    mock_function.__module__ = "mock_library"
    mock_function.__qualname__ = "mock_library.test_func"

    # Mock inspect.getsource to return source code
    with patch("mplfuzz.library_visitor.inspect.getsource") as mock_getsource:
        mock_getsource.return_value = "def test_func(a: int, b: str = 'hello') -> bool: pass"

        # Mock from_function_type to return a valid API
        with patch("mplfuzz.library_visitor.from_function_type") as mock_from_function:
            mock_api = Mock()
            mock_api.api_name = "mock_library.test_func"
            mock_from_function.return_value = Mock(value=mock_api)

            # Create a mock module
            mock_module = Mock()
            mock_module.__name__ = "mock_library"
            mock_module.__all__ = ["test_func"]

            # Call _visit with the mock function
            apis = list(visitor._visit(mock_module, "mock_library", set()))
            assert len(apis) == 1
            assert apis[0].api_name == "mock_library.test_func"
            mock_from_function.assert_called_once()


def test_visit_with_private_attributes():
    """Test that private attributes are filtered out"""
    visitor = LibraryVisitor("mock_library")

    # Create a module with private attributes
    mock_module = Mock()
    mock_module.__name__ = "mock_library"
    mock_module.__all__ = ["_private_func", "public_func"]

    # Mock the _visit method to return APIs
    with patch("mplfuzz.library_visitor.LibraryVisitor._visit") as mock_visit:
        mock_visit.return_value = []

        # Call visit
        apis = list(visitor.visit())
        assert len(apis) == 0


def test_visit_with_submodules():
    """Test that submodules are properly visited"""
    visitor = LibraryVisitor("mock_library")

    # Create a mock submodule
    mock_submodule = Mock()
    mock_submodule.__name__ = "mock_library.submodule"
    mock_submodule.__all__ = ["sub_func"]

    # Mock the _visit method to return APIs
    with patch("mplfuzz.library_visitor.LibraryVisitor._visit") as mock_visit:
        mock_visit.return_value = [Mock(api_name="mock_library.submodule.sub_func")]

        # Create a mock module with a submodule
        mock_module = Mock()
        mock_module.__name__ = "mock_library"
        mock_module.__all__ = ["submodule"]

        # Call visit
        apis = list(visitor.visit())
        assert len(apis) == 1
        assert apis[0].api_name == "mock_library.submodule.sub_func"


def test_visit_with_error_handling():
    """Test error handling in visit method"""
    visitor = LibraryVisitor("mock_library")

    # Mock importlib.import_module to raise an exception
    with patch("mplfuzz.library_visitor.importlib.import_module") as mock_import:
        mock_import.side_effect = Exception("Import failed")

        # Call visit
        apis = list(visitor.visit())
        assert len(apis) == 0


def test_visit_with_module_not_found():
    """Test that module not found is handled correctly"""
    visitor = LibraryVisitor("mock_library")

    # Mock find_spec to return None
    with patch("mplfuzz.library_visitor.importlib.util.find_spec") as mock_find_spec:
        mock_find_spec.return_value = None

        # Call visit
        apis = list(visitor.visit())
        assert len(apis) == 0


def test_visit_with_private_module():
    """Test that private modules are filtered out"""
    visitor = LibraryVisitor("mock_library")

    # Create a module with a private submodule
    mock_module = Mock()
    mock_module.__name__ = "mock_library"
    mock_module.__all__ = ["_private_submodule", "public_submodule"]

    # Mock the _visit method to return APIs
    with patch("mplfuzz.library_visitor.LibraryVisitor._visit") as mock_visit:
        mock_visit.return_value = []

        # Call visit
        apis = list(visitor.visit())
        assert len(apis) == 0


def test_visit_with_module_without_all():
    """Test that modules without __all__ are handled correctly"""
    visitor = LibraryVisitor("mock_library")

    # Create a module without __all__
    mock_module = Mock()
    mock_module.__name__ = "mock_library"
    mock_module.__all__ = None

    # Mock the _visit method to return APIs
    with patch("mplfuzz.library_visitor.LibraryVisitor._visit") as mock_visit:
        mock_visit.return_value = [Mock(api_name="mock_library.test_func")]

        # Call visit
        apis = list(visitor.visit())
        assert len(apis) == 1
        assert apis[0].api_name == "mock_library.test_func"
