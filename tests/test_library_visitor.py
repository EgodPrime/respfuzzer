import importlib
import types
from types import ModuleType

from tracefuzz.lib.library_visitor import LibraryVisitor
from tracefuzz.models import Function


def test_visit_ast():
    """Test basic functionality with ast library"""
    visitor = LibraryVisitor("ast")
    for function in visitor.visit():
        assert isinstance(function, Function)


def test_visit_pandas():
    """Test basic functionality with pandas library"""
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
    setattr(root, "sub", sub)

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
    setattr(mod, "func", func)

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
    setattr(root, "sub", sub)

    res = list(visitor._visit(root, "rootmod", set()))
    # submodule exists but its __name__ doesn't start with root_mod_name, so it should be ignored
    assert res == []
