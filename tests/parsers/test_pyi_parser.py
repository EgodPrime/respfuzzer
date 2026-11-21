from unittest.mock import Mock, patch

from respfuzzer.lib.parsers.pyi_parser import (
    _find_all_pyi_files,
    _parse_pyi_file,
    parse_pyi_files,
)


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
        _parse_pyi_file(
            "/path/to/test.pyi", pyi_cache, root_path="/path/to", library_name="lib"
        )

    # 缓存键为完全限定名 <库名>.<模块路径>.<函数名>，不再是裸函数名
    assert "lib.test.test_func" in pyi_cache
    assert "lib.test.another_func" in pyi_cache
    assert "test_func" not in pyi_cache

    # Check test_func
    test_func = pyi_cache["lib.test.test_func"]
    assert test_func["name"] == "test_func"
    assert test_func["source"] == "def test_func(a: int, b: str) -> bool:..."
    assert len(test_func["args"]) == 2
    assert test_func["args"][0].arg_name == "a"
    assert test_func["args"][0].type == "int"
    assert test_func["args"][1].arg_name == "b"
    assert test_func["args"][1].type == "str"
    assert test_func["ret_type_str"] == "bool"

    # Check another_func
    another_func = pyi_cache["lib.test.another_func"]
    assert another_func["name"] == "another_func"
    assert another_func["source"] == "def another_func(c: List[int]) -> None:..."
    assert len(another_func["args"]) == 1
    assert another_func["args"][0].arg_name == "c"
    assert another_func["args"][0].type == "List[int]"
    assert another_func["ret_type_str"] == "None"


def test_parse_pyi_file_complex():
    """Parse a .pyi file with many kinds of parameters and a class method to ensure it's skipped."""
    content = """
def pos_only(a: int, /, b: str, *args: int, c: float, d: int = 1, **kwargs) -> int:...
class C:
    def method(self, x: int) -> None:...
def plain(x):...
"""

    mock_file = Mock()
    mock_file.read.return_value = content
    mock_file.__enter__ = Mock(return_value=mock_file)
    mock_file.__exit__ = Mock(return_value=None)

    mock_open = Mock()
    mock_open.return_value = mock_file

    pyi_cache = {}
    with patch("builtins.open", mock_open):
        _parse_pyi_file(
            "/some/path/test.pyi", pyi_cache, root_path="/some/path", library_name="lib"
        )

    # pos_only should be parsed（限定键）
    assert "lib.test.pos_only" in pyi_cache
    info = pyi_cache["lib.test.pos_only"]
    assert info["ret_type_str"] == "int"
    # Check parameter kinds
    kinds = [a.pos_type for a in info["args"]]
    from inspect import _ParameterKind

    assert _ParameterKind.POSITIONAL_ONLY.name in kinds
    assert _ParameterKind.POSITIONAL_OR_KEYWORD.name in kinds
    assert _ParameterKind.VAR_POSITIONAL.name in kinds
    assert _ParameterKind.KEYWORD_ONLY.name in kinds
    assert _ParameterKind.VAR_KEYWORD.name in kinds

    # plain should also be present and have unknown return
    assert "lib.test.plain" in pyi_cache
    assert pyi_cache["lib.test.plain"]["ret_type_str"] == "unknown"


def test_find_all_pyi_files_walk_and_visited(monkeypatch):
    """Test _find_all_pyi_files traverses directories and respects visited_files."""

    # Provide os.walk-like behavior depending on the requested path to avoid recursion
    def fake_walk(path):
        if path == "/root":
            return [("/root", ["subdir"], ["a.pyi"])]
        elif path == "/root/subdir":
            return [("/root/subdir", [], ["b.pyi", "a.pyi"])]
        else:
            return []

    monkeypatch.setattr("os.walk", fake_walk)

    # Provide different contents per file path by inspecting the file_path argument
    def fake_open(path, *args, **kwargs):
        file_mock = Mock()
        if path.endswith("a.pyi"):
            file_mock.read.return_value = "def a1():...\ndef a2():..."
        else:
            file_mock.read.return_value = "def b1():..."
        file_mock.__enter__ = Mock(return_value=file_mock)
        file_mock.__exit__ = Mock(return_value=None)
        return file_mock

    monkeypatch.setattr("builtins.open", fake_open)

    visited_files = set()
    _find_all_pyi_files("/root", visited_files)

    # visited_files should contain full paths
    assert any(p.endswith("a.pyi") for p in visited_files)
    assert any(p.endswith("b.pyi") for p in visited_files)

    # 解析走 parse_pyi_files：root_path + library_name 必传，产出限定键
    pyi_cache: dict = {}
    for fp in sorted(visited_files):
        pyi_cache.update(parse_pyi_files(fp, "/root", "mylib"))

    # Expect functions a1,a2,b1 parsed under qualified keys
    assert "mylib.a.a1" in pyi_cache
    assert "mylib.a.a2" in pyi_cache
    assert "mylib.subdir.a.a1" in pyi_cache
    assert "mylib.subdir.b.b1" in pyi_cache
    # 不再有未限定（裸名）键
    assert "a1" not in pyi_cache and "b1" not in pyi_cache


def test_find_all_pyi_files_skips_already_visited(monkeypatch):
    """重复收集幂等：同一路径只入集合一次（set 去重），重复调用不新增文件。"""

    # Setup walk to return same files under two calls
    def fake_walk(path):
        if path == "/root":
            return [("/root", ["subdir"], ["a.pyi"])]
        elif path == "/root/subdir":
            return [("/root/subdir", [], ["a.pyi"])]
        else:
            return []

    monkeypatch.setattr("os.walk", fake_walk)

    def fake_open(path, *args, **kwargs):
        file_mock = Mock()
        file_mock.read.return_value = "def a1():..."
        file_mock.__enter__ = Mock(return_value=file_mock)
        file_mock.__exit__ = Mock(return_value=None)
        return file_mock

    monkeypatch.setattr("builtins.open", fake_open)

    visited_files = set()
    _find_all_pyi_files("/root", visited_files)
    before_count = len(visited_files)

    # 再次收集：同一路径不会重复入集合
    _find_all_pyi_files("/root", visited_files)
    assert len(visited_files) == before_count

    # 每个文件只解析一次，键仍为限定名
    pyi_cache: dict = {}
    for fp in sorted(visited_files):
        pyi_cache.update(parse_pyi_files(fp, "/root", "mylib"))
    assert set(pyi_cache) == {"mylib.a.a1", "mylib.subdir.a.a1"}
