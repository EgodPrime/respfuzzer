"""C-10 G 组：resolve 前先去重（同一物理 builtin 只 resolve 一次）。

§10.1 实证发现：library_visitor.visit() 原本逐 (module, name) 绑定调用
resolve_builtin_signature，最终才按 real_path 去重 → 同一物理函数被多个公开
别名/模块重导出时重复 resolve（12 库绑定 native 20940 → 去重 distinct 12256，
42% 为别名重绑定）。修复：`_visit` 内以物理 real_path 为键缓存 resolve 结果，
别名绑定复用签名并重打 func_path 标签。本测试闸断言『先去重后 resolve』。
"""

import types
from collections.abc import Callable

from respfuzzer.lib import library_visitor as lv_mod
from respfuzzer.lib.library_visitor import LibraryVisitor


class _FakeFn:
    """最小 Function 替身：_visit 只用 func_path + model_copy(重打标签)。"""

    def __init__(self, path: str):
        self.func_path = path

    def model_copy(self, *, update):
        return _FakeFn(update["func_path"])


def _build(monkeypatch, blt1: Callable, blt2: Callable, calls: list[str], paths: tuple):
    """内存库：两个公开绑定名 ali/alias2（可同对象可异对象），monkeypatch resolve/realpath。

    一律经 monkeypatch 挂载/自动恢复，避免模块全局污染串扰其它测试文件。
    """
    mod = types.ModuleType("xlib")
    mod.__dict__["ali"] = blt1
    mod.__dict__["alias2"] = blt2

    def fake_resolve(obj, rp, *, pyi_cache, name_index, public_name):
        calls.append(public_name)
        return _FakeFn(public_name)

    def fake_find_real_path(root, obj, cache: dict | None = None):
        # paths = (path_for_ali, path_for_alias2)，各为完整 real_path 元组
        if obj is mod.__dict__.get("ali"):
            return paths[0]
        return paths[1]

    monkeypatch.setattr(lv_mod, "resolve_builtin_signature", fake_resolve)
    monkeypatch.setattr(lv_mod, "find_real_path", fake_find_real_path)

    v = LibraryVisitor("xlib")
    v._top_module = mod
    v.pyi_cache = {}
    v.name_index = {}
    v._builtin_resolve_cache = {}
    return v, mod


def test_G1_same_physical_builtin_resolved_once(monkeypatch):
    """同一物理 builtin 经两个公开别名 → 只 resolve 一次，两绑定输出各自 func_path。"""
    calls: list[str] = []
    v, mod = _build(monkeypatch, len, len, calls, (("xlib", "ali"), ("xlib", "ali")))
    funs = list(v._visit(mod, "xlib", set(), "xlib"))

    assert calls == ["xlib.ali"]  # 仅第一个绑定真正 resolve
    assert {f.func_path for f in funs} == {"xlib.ali", "xlib.alias2"}


def test_G2_prepopulated_cache_short_circuits_every_resolve(monkeypatch):
    """缓存已有该 real_path → 两个绑定都零 resolve，仅从缓存重打 func_path。"""
    calls: list[str] = []
    v, mod = _build(monkeypatch, len, len, calls, (("xlib", "ali"), ("xlib", "ali")))
    v._builtin_resolve_cache = {("xlib", "ali"): _FakeFn("xlib.ali")}
    funs = list(v._visit(mod, "xlib", set(), "xlib"))

    assert calls == []
    assert {f.func_path for f in funs} == {"xlib.ali", "xlib.alias2"}


def test_G3_distinct_physical_not_short_circuited(monkeypatch):
    """不同物理函数（不同 real_path）→ 各自 resolve，不被第一绑定短路。"""
    calls: list[str] = []
    v, mod = _build(monkeypatch, len, abs, calls, (("xlib", "ali"), ("xlib", "abs2")))
    funs = list(v._visit(mod, "xlib", set(), "xlib"))

    assert calls == ["xlib.ali", "xlib.alias2"]  # 两个不同物理键，各 resolve 一次
    assert {f.func_path for f in funs} == {"xlib.ali", "xlib.alias2"}


def test_G4_signature_resolved_field():
    """signature_resolved 字段语义：默认 True（旧数据兼容）；False = 失败留空条目，
    且不判满类型注解（空 args 的 all() 空真必须短路）。"""
    from respfuzzer.models import Function

    f = Function(func_path="a.b", source="", args=[])
    assert f.signature_resolved is True
    # 旧 data JSON（无该字段）经 model_validate → 默认 True
    f3 = Function.model_validate({"func_name": "a.b", "source": "", "args": []})
    assert f3.signature_resolved is True

    f2 = Function(
        func_path="a.b", source="", args=[], ret_type="", signature_resolved=False
    )
    assert f2.signature_resolved is False
    assert f2.ret_type == ""
    assert f2.has_full_type_hints is False  # 失败占位不算满注解

    assert f.has_full_type_hints is True  # 已解析零参函数仍是满注解（无参数可注解）
