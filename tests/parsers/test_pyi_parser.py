from unittest.mock import Mock, patch

from tracefuzz.parsers.pyi_parser import _find_all_pyi_files, _parse_pyi_file


def test_parse_pyi_file():
    """Test parsing .pyi file"""
    # Mock file content
    content = """
def test_func(a: int, b: str) -> bool:...
def another_func(c: List[int]) -> None:...
"""

    # Create a proper mock file object that supports context manager protocol
    mock_file = Mock()
    mock_file.read.return_value = content
    mock_file.__enter__ = Mock(return_value=mock_file)
    mock_file.__exit__ = Mock(return_value=None)

    mock_open = Mock()
    mock_open.return_value = mock_file

    # Test the function
    pyi_cache = {}
    with patch("builtins.open", mock_open):
        _parse_pyi_file("/path/to/test.pyi", pyi_cache)

    assert "test_func" in pyi_cache
    assert "another_func" in pyi_cache

    # Check test_func
    test_func = pyi_cache["test_func"]
    assert test_func["name"] == "test_func"
    assert test_func["source"] == "def test_func(a: int, b: str) -> bool:..."
    assert len(test_func["args"]) == 2
    assert test_func["args"][0].arg_name == "a"
    assert test_func["args"][0].type == "int"
    assert test_func["args"][1].arg_name == "b"
    assert test_func["args"][1].type == "str"
    assert test_func["ret_type_str"] == "bool"

    # Check another_func
    another_func = pyi_cache["another_func"]
    assert another_func["name"] == "another_func"
    assert another_func["source"] == "def another_func(c: List[int]) -> None:..."
    assert len(another_func["args"]) == 1
    assert another_func["args"][0].arg_name == "c"
    assert another_func["args"][0].type == "List[int]"
    assert another_func["ret_type_str"] == "None"

    def test_find_all_pyi_files():
        """Test finding all .pyi files"""
        # Mock walk results
        mock_walk = Mock()
        mock_walk.return_value = [
            ("/path/to", [], ["test.pyi", "other.pyi"]),
            ("/path/to/subdir", [], ["sub.pyi"]),
        ]

        # Mock file content
        content = """
    def test_func(a: int) -> bool:...
    """

        # Create a proper mock file object that supports context manager protocol
        mock_file = Mock()
        mock_file.read.return_value = content
        mock_file.__enter__ = Mock(return_value=mock_file)
        mock_file.__exit__ = Mock(return_value=None)

        mock_open = Mock()
        mock_open.return_value = mock_file

        # Test the function
        visited_files = set()
        pyi_cache = {}
        with patch("builtins.open", mock_open):
            _find_all_pyi_files("/path/to", visited_files, pyi_cache)

        # Verify that all three files were processed
        assert len(pyi_cache) == 3
        assert "test_func" in pyi_cache
        assert "test_func" in pyi_cache["test_func"]["source"]
        assert "test.pyi" in visited_files
        assert "other.pyi" in visited_files
        assert "sub.pyi" in visited_files
