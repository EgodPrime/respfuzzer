import importlib
import types
from types import ModuleType

from respfuzzer.lib.library_visitor import LibraryVisitor
from respfuzzer.models import Function


def test_visit_ast():
    """Test basic functionality with ast library"""
    visitor = LibraryVisitor("ast")
    for function in visitor.visit():
        assert isinstance(function, Function)


def test_visit_pandas():
    """Test basic functionality with pandas library (skipped if pandas not installed)"""
    import pytest

    pytest.importorskip(
        "pandas"
    )  # 环境(本工作区 py3.14)未装 pandas 时跳过；安装后执行真实遍历
    visitor = LibraryVisitor("pandas")
    functions = list(visitor.visit())
    assert len(functions) > 0


def test_visit_nonexistent_library():
    """Test error handling when library is not found"""
    visitor = LibraryVisitor("nonexistent_library")
    functions = list(visitor.visit())
    assert len(functions) == 0


def test_find_all_pyi_spec_none(monkeypatch):
    """When find_spec returns None, find_all_pyi_functions should simply return None and not crash."""
    visitor = LibraryVisitor("ast")
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert visitor.find_all_pyi_functions() is None


def test__visit_skip_seen():
    visitor = LibraryVisitor("dummy")
    mod = ModuleType("dummy")
    # mark as seen
    seen = {id(mod)}
    res = list(visitor._visit(mod, "dummy", seen))
    assert res == []


def test__visit_getattr_attribute_error():
    visitor = LibraryVisitor("dummy")
    mod = ModuleType("dummy")
    # Provide __all__ with a name that doesn't exist to cause getattr to raise
    mod.__all__ = ["no_such_attr"]
    res = list(visitor._visit(mod, "dummy", set()))
    assert res == []


def test__visit_submodule_name_exception():
    visitor = LibraryVisitor("rootmod")
    root = ModuleType("rootmod")

    class BadModule(ModuleType):
        def __getattribute__(self, name):
            if name == "__name__":
                raise Exception("no name")
            return super().__getattribute__(name)

    sub = BadModule("rootmod.sub")
    # expose submodule through root
    root.__all__ = ["sub"]
    root.sub = sub

    # Accessing sub.__name__ raises in this BadModule; the implementation tries to
    # clone and access clone.__name__, but that may still raise. Treat this as a
    # handled failure mode: ensure the exception is raised by _visit and can be
    # observed by callers.
    import pytest

    with pytest.raises(Exception):
        list(visitor._visit(root, "rootmod", set()))


def test__visit_function_module_none():
    visitor = LibraryVisitor("myns")
    mod = ModuleType("myns")

    def func():
        return 1

    # set function module to None to trigger the warning path
    func.__module__ = None
    mod.__all__ = ["func"]
    mod.func = func

    res = list(visitor._visit(mod, "myns", set()))
    # the function has __module__ None so it should be skipped -> no results
    assert res == []


def test_find_all_pyi_spec_origin_none(monkeypatch):
    visitor = LibraryVisitor("pkg")
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: types.SimpleNamespace(origin=None)
    )
    assert visitor.find_all_pyi_functions() is None


def test_find_all_pyi_root_path_empty(monkeypatch):
    visitor = LibraryVisitor("pkg")
    # origin is empty string -> dirname empty -> root_path falsy -> function returns None
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda name: types.SimpleNamespace(origin="")
    )
    assert visitor.find_all_pyi_functions() is None


def test__visit_submodule_not_in_root():
    visitor = LibraryVisitor("rootmod")
    root = ModuleType("rootmod")
    sub = ModuleType("otherpkg.sub")
    root.__all__ = ["sub"]
    root.sub = sub

    res = list(visitor._visit(root, "rootmod", set()))
    # submodule exists but its __name__ doesn't start with root_mod_name, so it should be ignored
    assert res == []


def test_builtin_hits_qualified_key(monkeypatch):
    """缺口B完整链路：_visit 用限定键（当前遍历模块 __name__ + 函数名）命中 pyi_cache，
    产出 is_builtin==1 且 func_name 含模块前缀；换另一模块同名函数则 miss，证明键区分模块。"""

    def fake_namespace(obj):
        return ["mylib", "sub", "a", "load"]

    monkeypatch.setattr("dill.source._namespace", fake_namespace)

    pyi_dict = {
        "name": "load",
        "source": "def load(x: int) -> int: ...",
        "args": [],
        "ret_type_str": "int",
        "file_path": "/root/mylib/sub/a.pyi",
    }

    # 命中：pyi_cache 预填模块 mylib.sub.a 的 load（限定键 mylib.sub.a.load）
    visitor = LibraryVisitor("mylib")
    visitor.pyi_cache = {"mylib.sub.a.load": pyi_dict}
    mod = ModuleType("mylib.sub.a")
    mod.load = len  # 真实 builtin

    res = list(visitor._visit(mod, "mylib", set()))
    assert len(res) == 1
    assert res[0].is_builtin == 1
    assert res[0].func_path == "mylib.sub.a.load"
    assert "mylib.sub" in res[0].func_path

    # miss：仅另一模块 mylib.sub.b 有同一函数名 load 的 stub → 本模块查不到，键区分模块
    visitor2 = LibraryVisitor("mylib")
    visitor2.pyi_cache = {"mylib.sub.b.load": pyi_dict}
    res2 = list(visitor2._visit(mod, "mylib", set()))
    assert res2 == []
