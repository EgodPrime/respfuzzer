"""C-10 防碰撞测试：stub 缓存键升级为限定名（模块+函数）。

覆盖定案+补强（review-testplan.md）的 9 用例：P1 纯函数拼接、PA 空 root/lib 退化裸名、
P2 两文件同名键共存、P3 读端取对 stub、P3b 写读键等值、P6 端到端键等值、P4 __init__.pyi 父模块键、
P5 顶层 __init__.pyi 键、PC pandas 无关的两子模块同名端到端。
依赖 t20（pyi_parser 写端限定键）/ t21（library_visitor 读端限定键）已落地。
不跑 pytest（队长统一回归）；本文件仅新增。
"""

import types
from types import ModuleType
from unittest.mock import Mock, patch

from respfuzzer.lib.library_visitor import LibraryVisitor
from respfuzzer.lib.parsers.pyi_parser import (
    _parse_pyi_file,
    _pyi_module_from_path,
    make_pyi_key,
    make_pyi_key_from_module,
)


def _mock_file(content: str) -> Mock:
    """返回 __enter__/__exit__ 支持 context manager 的伪读文件对象。"""
    m = Mock()
    m.read.return_value = content
    m.__enter__ = Mock(return_value=m)
    m.__exit__ = Mock(return_value=None)
    return m


def _parse_pyi(cache, root_path, library_name, file_path, content):
    """用伪文件按指定 root/lib/file 跑 _parse_pyi_file（写端限定键）。"""
    with patch("builtins.open", Mock(return_value=_mock_file(content))):
        _parse_pyi_file(file_path, cache, root_path=root_path, library_name=library_name)


# ===== P1 / PA：make_pyi_key 纯函数 + 空 root/lib 退化 =====
def test_make_pyi_key_basic():
    """P1 纯函数拼接：顶层 __init__.pyi -> 库名.函数名；子模块文件 -> 库名.模块点分路径.函数名。"""
    assert make_pyi_key("/root/lib", "lib", "/root/lib/__init__.pyi", "f") == "lib.f"
    assert make_pyi_key("/root/lib", "lib", "/root/lib/sub/a.pyi", "load") == "lib.sub.a.load"
    assert make_pyi_key("/root/lib", "lib", "/root/lib/a/b/c.pyi", "g") == "lib.a.b.c.g"


def test_empty_root_degenerates_bare_key():
    """PA 空 root 或空 library_name 时 make_pyi_key 退化为裸函数名；_pyi_module_from_path 空守卫不抛。"""
    assert make_pyi_key("", "lib", "/path/test.pyi", "f") == "f"
    assert make_pyi_key("/root", "", "/root/a.pyi", "f") == "f"
    # 空守卫：不产 ../ 链、不抛异常（缺口 A/F）
    assert _pyi_module_from_path("", "/path/test.pyi") == ""
    assert _pyi_module_from_path("/root", "") == ""


# ===== P2：写端两文件同名函数键不冲突 =====
def test_write_two_files_same_name_no_collision():
    """P2 sub/a.pyi 与 sub/b.pyi 各定义 def load -> 产 lib.sub.a.load 与 lib.sub.b.load 两键共存互不覆盖。"""
    cache = {}
    _parse_pyi(cache, "/root/lib", "lib", "/root/lib/sub/a.pyi", "def load(x: int) -> int: ...")
    _parse_pyi(cache, "/root/lib", "lib", "/root/lib/sub/b.pyi", "def load(x: str) -> str: ...")
    assert cache["lib.sub.a.load"]["args"][0].type == "int"
    assert cache["lib.sub.b.load"]["args"][0].type == "str"
    assert "lib.sub.a.load" in cache and "lib.sub.b.load" in cache
    assert "load" not in cache  # 无裸名键


# ===== P3 / P3b：读端取对 stub + 写读键等值 =====
def test_read_gets_correct_stub(monkeypatch):
    """P3 读端 make_pyi_key_from_module(mod.__name__, name) 命中正确 stub；通过 _visit builtin 分支。"""
    cache = {}
    _parse_pyi(cache, "/root/mylib", "mylib", "/root/mylib/sub/a.pyi", "def load(x: int) -> int: ...")
    _parse_pyi(cache, "/root/mylib", "mylib", "/root/mylib/sub/b.pyi", "def load(x: str) -> str: ...")
    obj = len
    mod = ModuleType("mylib.sub.a")
    mod.__all__ = ["load"]
    setattr(mod, "load", obj)
    monkeypatch.setattr("dill.source._namespace", lambda _o: ["mylib", "sub", "a", "load"])
    visitor = LibraryVisitor("mylib")
    visitor.pyi_cache = cache
    funcs = list(visitor._visit(mod, "mylib", set()))
    assert len(funcs) == 1 and funcs[0].is_builtin == 1
    assert funcs[0].args[0].type == "int"


def test_write_read_key_equivalence():
    """P3b 纯函数级：写端 make_pyi_key(文件路径派生) == 读端 make_pyi_key_from_module(模块名派生)。"""
    write_key = make_pyi_key("/root/lib", "lib", "/root/lib/sub/a.pyi", "load")
    read_key = make_pyi_key_from_module("lib.sub.a", "load")
    assert write_key == read_key == "lib.sub.a.load"


# ===== P4 / P5：__init__.pyi 键语义 =====
def test_init_pyi_key():
    """P4 sub/__init__.pyi 定义 f -> 键 lib.sub.f（父模块键，非 lib.sub.__init__.f）。"""
    assert make_pyi_key("/root/lib", "lib", "/root/lib/sub/__init__.pyi", "f") == "lib.sub.f"
    cache = {}
    _parse_pyi(cache, "/root/lib", "lib", "/root/lib/sub/__init__.pyi", "def f(a: int = 1) -> int: ...")
    assert "lib.sub.f" in cache
    assert "lib.sub.__init__.f" not in cache


def test_top_level_pyi_key():
    """P5 顶层 __init__.pyi 定义 f -> 键 lib.f（无中间段）。"""
    assert make_pyi_key("/root/lib", "lib", "/root/lib/__init__.pyi", "f") == "lib.f"


# ===== PC / P6：端到端（真实 pyi 文件，两键共存不覆盖 + 端到端键等值） =====
def _write_pyi_files(tmp_path):
    """在 tmp_path 下构造伪 typed 库 mylib：__init__/sub/a/sub/b 各一个 pyi。返回 (root, contents)。"""
    root = tmp_path / "mylib"
    (root / "sub").mkdir(parents=True, exist_ok=True)
    (root / "__init__.pyi").write_text("def load(y: float) -> float: ...")
    (root / "sub" / "a.pyi").write_text("def load(x: int) -> int: ...")
    (root / "sub" / "b.pyi").write_text("def load(x: str) -> str: ...")
    contents = {
        str(root / "__init__.pyi"): "def load(y: float) -> float: ...",
        str(root / "sub" / "a.pyi"): "def load(x: int) -> int: ...",
        str(root / "sub" / "b.pyi"): "def load(x: str) -> str: ...",
    }
    return str(root), contents


def test_pandas_free_two_module_collision(tmp_path, monkeypatch):
    """PC 端到端：tmp_path 伪 typed 库两子模块同名 load(x:int) vs load(x:str)，两键共存不覆盖、读端各取对。"""
    root, contents = _write_pyi_files(tmp_path)
    cache = {}
    for fp, content in contents.items():
        with patch("builtins.open", Mock(return_value=_mock_file(content))):
            _parse_pyi_file(fp, cache, root_path=root, library_name="mylib")
    assert "mylib.sub.a.load" in cache and "mylib.sub.b.load" in cache
    assert "mylib.load" in cache
    assert "load" not in cache
    assert cache["mylib.sub.a.load"]["args"][0].type == "int"
    assert cache["mylib.sub.b.load"]["args"][0].type == "str"
    obj = len
    mod = ModuleType("mylib.sub.a")
    mod.__all__ = ["load"]
    setattr(mod, "load", obj)
    monkeypatch.setattr("dill.source._namespace", lambda _o: ["mylib", "sub", "a", "load"])
    visitor = LibraryVisitor("mylib")
    visitor.pyi_cache = cache
    funcs = list(visitor._visit(mod, "mylib", set()))
    assert len(funcs) == 1 and funcs[0].is_builtin == 1
    assert funcs[0].args[0].type == "int"


def test_e2e_write_key_equals_read_key(tmp_path):
    """P6 端到端：写端实际产键与读端 make_pyi_key_from_module 查键一致，取回正确 stub。"""
    root, contents = _write_pyi_files(tmp_path)
    cache = {}
    for fp, content in contents.items():
        with patch("builtins.open", Mock(return_value=_mock_file(content))):
            _parse_pyi_file(fp, cache, root_path=root, library_name="mylib")
    write_key = make_pyi_key(root, "mylib", str(tmp_path / "mylib/sub/a.pyi"), "load")
    read_key = make_pyi_key_from_module("mylib.sub.a", "load")
    assert write_key == read_key == "mylib.sub.a.load"
    assert read_key in cache

