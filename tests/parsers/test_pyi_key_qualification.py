"""stub 缓存键限定名化：防碰撞 + 读端解析链。

覆盖：
- 写端 `make_pyi_key` / `_pyi_module_from_path`：限定键形态、`__init__.pyi` 父模块语义、
  空 root/lib 拒绝（彻底关闭「未限定函数名键」路径）；
- 防碰撞：两个模块的同名函数各占一个键、互不覆盖，且不再产生裸名键；
- 读端 `build_name_index`：唯一裸名兜底、歧义裸名不入索引、运行时同名异身份剔除；
- 读端 `_visit`：限定键命中取对 stub、唯一裸名兜底命中、歧义裸名不兜底；
- 签名闭合链 `resolve_builtin_signature`：stub → inspect → docstring → 留空占位（不丢函数）。
"""

from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from respfuzzer.lib.library_visitor import LibraryVisitor
from respfuzzer.lib.parsers.pyi_parser import (
    _parse_pyi_file,
    _pyi_module_from_path,
    build_name_index,
    make_pyi_key,
)
from respfuzzer.lib.signature import (
    parse_docstring_signature,
    resolve_builtin_signature,
)
from respfuzzer.models import Argument


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _mock_file(content: str) -> Mock:
    """返回支持 context manager 的伪读文件对象。"""
    m = Mock()
    m.read.return_value = content
    m.__enter__ = Mock(return_value=m)
    m.__exit__ = Mock(return_value=None)
    return m


def _parse_pyi(cache, root_path, library_name, file_path, content):
    """用伪文件按指定 root/lib/file 跑 `_parse_pyi_file`（写端限定键）。"""
    with patch("builtins.open", Mock(return_value=_mock_file(content))):
        _parse_pyi_file(
            file_path, cache, root_path=root_path, library_name=library_name
        )


def _pyi_entry(name="f", arg_types=("int",)):
    args = [
        Argument(
            arg_name=f"a{i}", type=t, pos_type="POSITIONAL_OR_KEYWORD", default_value=""
        )
        for i, t in enumerate(arg_types)
    ]
    return {
        "name": name,
        "source": f"def {name}(...): ...",
        "args": args,
        "ret_type_str": "int",
        "file_path": "",
    }


# ---------------------------------------------------------------------------
# 写端：限定键
# ---------------------------------------------------------------------------
def test_make_pyi_key_basic():
    """顶层 __init__.pyi -> 库名.函数名；子模块文件 -> 库名.模块点分路径.函数名。"""
    assert make_pyi_key("/root/lib", "lib", "/root/lib/__init__.pyi", "f") == "lib.f"
    assert (
        make_pyi_key("/root/lib", "lib", "/root/lib/sub/a.pyi", "load")
        == "lib.sub.a.load"
    )
    assert make_pyi_key("/root/lib", "lib", "/root/lib/a/b/c.pyi", "g") == "lib.a.b.c.g"


def test_make_pyi_key_init_pyi_uses_parent_module():
    """sub/__init__.pyi -> lib.sub.f（父模块键，不是 lib.sub.__init__.f）。"""
    assert (
        make_pyi_key("/root/lib", "lib", "/root/lib/sub/__init__.pyi", "f")
        == "lib.sub.f"
    )
    cache = {}
    _parse_pyi(
        cache, "/root/lib", "lib", "/root/lib/sub/__init__.pyi", "def f(a=1): ..."
    )
    assert "lib.sub.f" in cache
    assert "lib.sub.__init__.f" not in cache


def test_empty_root_library_raises():
    """空 root_path / library_name 抛 ValueError：任何路径都不得产出未限定键。"""
    with pytest.raises(ValueError):
        make_pyi_key("", "lib", "/path/test.pyi", "f")
    with pytest.raises(ValueError):
        make_pyi_key("/root", "", "/root/a.pyi", "f")
    # 空守卫本身不抛：不产 ../ 链
    assert _pyi_module_from_path("", "/path/test.pyi") == ""
    assert _pyi_module_from_path("/root", "") == ""


def test_write_two_files_same_name_no_collision():
    """两个模块的同名函数各占一键、互不覆盖，且不产生裸名键（同名冲突场景）。"""
    cache = {}
    _parse_pyi(
        cache, "/root/lib", "lib", "/root/lib/sub/a.pyi", "def load(x: int) -> int: ..."
    )
    _parse_pyi(
        cache, "/root/lib", "lib", "/root/lib/sub/b.pyi", "def load(x: str) -> str: ..."
    )
    assert cache["lib.sub.a.load"]["args"][0].type == "int"
    assert cache["lib.sub.b.load"]["args"][0].type == "str"
    assert "load" not in cache  # 无裸名键


# ---------------------------------------------------------------------------
# 读端：唯一裸名索引
# ---------------------------------------------------------------------------
def test_build_name_index_unique_and_ambiguous():
    """唯一裸名入索引；同名歧义（多限定键同名）不入索引。"""
    cache = {
        "lib.sub.a.load": {"name": "load"},
        "lib.sub.b.load": {"name": "load"},
        "lib.sub.unique": {"name": "unique"},
    }
    assert build_name_index(cache) == {"unique": "lib.sub.unique"}


def test_build_name_index_excludes_runtime_ambiguous_bare_names():
    """stub 侧唯一、但运行时同名异身份的裸名被剔除（宁缺勿错配）。"""
    cache = {
        "lib.sub.a.interpolate": {"name": "interpolate"},
        "lib.sub.b.helper": {"name": "helper"},
    }
    index = build_name_index(cache, ambiguous_bare_names={"interpolate"})
    assert index == {"helper": "lib.sub.b.helper"}


# ---------------------------------------------------------------------------
# 读端：_visit builtin 分支
# ---------------------------------------------------------------------------
def _visit_builtin(monkeypatch, *, module_name, runtime_path, cache, name_index=None):
    """在伪模块里放一个 builtin，按给定物理身份跑 `_visit`，返回产出的 Function 列表。"""
    obj = len  # 真实 builtin_function_or_method
    mod = ModuleType(module_name)
    mod.__dict__["load"] = obj
    monkeypatch.setattr("dill.source._namespace", lambda _o: list(runtime_path))
    visitor = LibraryVisitor(module_name.split(".")[0])
    visitor.pyi_cache = cache
    visitor.name_index = (
        name_index if name_index is not None else build_name_index(cache)
    )
    return list(visitor._visit(mod, module_name.split(".")[0], set()))


def test_read_hits_qualified_key(monkeypatch):
    """限定键命中：运行时物理身份与写端键一致，取到正确 stub。"""
    cache = {}
    _parse_pyi(
        cache,
        "/root/mylib",
        "mylib",
        "/root/mylib/sub/a.pyi",
        "def load(x: int) -> int: ...",
    )
    _parse_pyi(
        cache,
        "/root/mylib",
        "mylib",
        "/root/mylib/sub/b.pyi",
        "def load(x: str) -> str: ...",
    )
    funcs = _visit_builtin(
        monkeypatch,
        module_name="mylib.sub.a",
        runtime_path=("mylib", "sub", "a", "load"),
        cache=cache,
    )
    assert len(funcs) == 1
    assert funcs[0].is_builtin == 1
    assert funcs[0].args[0].type == "int"  # 取到 a.pyi 的 stub，不是 b.pyi 的


def test_read_uses_unique_bare_name_fallback(monkeypatch):
    """限定键 miss 时，整库唯一裸名兜底命中（救 re-export / 镜像布局库）。"""
    cache = {"mylib.deffile.load": _pyi_entry(name="load")}
    funcs = _visit_builtin(
        monkeypatch,
        module_name="mylib.sub",
        runtime_path=("mylib", "sub", "load"),
        cache=cache,
    )
    assert len(funcs) == 1
    assert funcs[0].args[0].type == "int"


def test_read_ambiguous_bare_name_not_used(monkeypatch):
    """限定键 miss 且裸名歧义：不用任一歧义 stub，落到运行时解析（inspect 兜底）。"""
    cache = {
        "mylib.sub.a.load": _pyi_entry(name="load"),
        "mylib.sub.b.load": _pyi_entry(name="load"),
    }
    funcs = _visit_builtin(
        monkeypatch,
        module_name="mylib.sub",
        runtime_path=("mylib", "sub", "load"),
        cache=cache,
    )
    assert len(funcs) == 1  # 仍被收集（不丢函数），但签名来自 inspect(len)
    assert (
        funcs[0].args[0].arg_name == "obj"
    )  # inspect(len) -> (obj, /)，非任一歧义 stub


def test_builtin_never_dropped_when_no_stub(monkeypatch):
    """没有任何 stub 时也产出条目：不再像旧实现那样整条丢弃。"""
    funcs = _visit_builtin(
        monkeypatch,
        module_name="mylib.sub",
        runtime_path=("mylib", "sub", "load"),
        cache={},
        name_index={},
    )
    assert len(funcs) == 1
    assert funcs[0].is_builtin == 1
    assert funcs[0].args  # inspect 兜底解出了签名


# ---------------------------------------------------------------------------
# 签名闭合链
# ---------------------------------------------------------------------------
def test_resolve_tier1_stub_by_qualified_key():
    """tier①：物理身份限定键命中 stub。"""
    cache = {"lib.mod.f": _pyi_entry()}
    fn = resolve_builtin_signature(
        len,
        ("lib", "mod", "f"),
        pyi_cache=cache,
        name_index=build_name_index(cache),
        public_name="lib.mod.f",
    )
    assert fn.args[0].type == "int"
    assert fn.is_builtin == 1
    assert fn.signature_resolved is True
    assert fn.source == "def f(...): ..."  # stub 源码原样透传，公开名另行打标
    assert fn.func_name == "lib.mod.f"


def test_resolve_tier2_inspect_when_stub_miss():
    """tier②：无 stub 时用 inspect.signature。"""
    fn = resolve_builtin_signature(
        len,
        ("torch", "no_such"),
        pyi_cache={},
        name_index={},
        public_name="torch.no_such",
    )
    assert fn.args[0].arg_name == "obj"
    assert fn.real_path == "torch.no_such"
    assert fn.signature_resolved is True


def test_resolve_tier3_docstring_signature():
    """tier③：inspect 失败但 docstring 首行是签名时解析 docstring。"""
    obj = SimpleNamespace(
        __doc__="linalg.norm(A, ord=None, dim=None, keepdim=False, *, out=None) -> Tensor\n\nDoc prose."
    )
    fn = resolve_builtin_signature(
        obj,
        ("torch", "_C", "norm"),
        pyi_cache={},
        name_index={},
        public_name="torch.norm",
    )
    assert [a.arg_name for a in fn.args] == ["A", "ord", "dim", "keepdim", "out"]
    assert fn.args[1].default_value == "None"
    assert fn.ret_type == "Tensor"
    assert fn.signature_resolved is True


def test_resolve_tier4_unresolved_placeholder():
    """tier④：三源全缺 → 留空占位 + signature_resolved=False，不抛错不丢函数。"""
    obj = SimpleNamespace(__doc__="Some prose without any call signature line here.")
    fn = resolve_builtin_signature(
        obj, ("lib", "mystery"), pyi_cache={}, name_index={}, public_name="lib.mystery"
    )
    assert fn.args == []
    assert fn.ret_type == ""
    assert fn.source == ""
    assert fn.signature_resolved is False
    assert fn.func_name == "lib.mystery"


def test_parse_docstring_signature_variants():
    """docstring 签名解析：多行、零参、散文拒绝、numpy 可选括号记法。"""
    parsed = parse_docstring_signature("f(a: int, b='x') -> int\n\nMore text.")
    assert [a.arg_name for a in parsed["args"]] == ["a", "b"]
    assert parsed["args"][1].default_value == "'x'"
    assert parsed["ret_type"] == "int"

    # 零参 + 返回注解
    parsed = parse_docstring_signature("g() -> None")
    assert parsed["args"] == []
    assert parsed["ret_type"] == "None"

    # 说明文字里的括号不是签名（'(' 前缀非标识符 / 分段非形参）
    assert (
        parse_docstring_signature("Return the sine of x (in radians) for arrays.\n")
        is None
    )

    # numpy 可选括号记法展开
    parsed = parse_docstring_signature(
        "arange([start,] stop[, step,] dtype=None) -> array"
    )
    assert [a.arg_name for a in parsed["args"]] == ["start", "stop", "step", "dtype"]
