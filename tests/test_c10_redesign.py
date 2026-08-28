"""C-10 根源性重推：两段法 / LibraryModule / find_real_path / 模块级别名（§8.2/§8.1/§8.3 A-C）。

○ ⛔ 标注 = 当前 C-10 v2 失败的回归闸（模块级别名 `import a.b._c as c`）。
○ A 组：LibraryModule 模型；B 组：pub_mod 发现/函数收集；C 组：find_real_path。
"""

import importlib
import sys
from types import ModuleType

import pytest

from respfuzzer.lib.library_visitor import LibraryVisitor
from respfuzzer.lib.realpath import REALPATH_OVERRIDES, find_real_path
from respfuzzer.models import LibraryModule

_LIB = "aliaslib"


def _install(tmp_path, monkeypatch, files: dict[str, str], libname: str = _LIB):
    """在 tmp_path 布一个可导入的包，并把 tmp_path 挂进 sys.path（自动清理）。"""
    # 清除可能残留的同名模块缓存（测试间 sys.modules 串扰会返回上一个用例的包）
    for m in list(sys.modules):
        if m == libname or m.startswith(libname + "."):
            del sys.modules[m]
    pkg = tmp_path / libname
    for rel, content in files.items():
        p = pkg / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    monkeypatch.syspath_prepend(str(tmp_path))
    return pkg


# ---------------------------------------------------------------------------
# A 组：LibraryModule 模型（§8.2）
# ---------------------------------------------------------------------------


def test_A1_public_module_identity_equals_mod_path():
    m = LibraryModule(real_path="a.b", mod_path="a.b")
    assert m.is_public is True
    assert m.real_path == m.mod_path == "a.b"


def test_A2_public_alias_of_private_module_is_public():
    """⛔ 公开别名私有模块：real_path 私有、mod_path 公开、is_public 判 mod_path。"""
    m = LibraryModule(real_path="a.b._c", mod_path="a.b.c")
    assert m.real_path == "a.b._c"
    assert m.mod_path == "a.b.c"
    assert m.is_public is True  # 判记录路径 mod_path，不判物理名


def test_A3_pure_private_module_not_public():
    m = LibraryModule(real_path="a.b._c", mod_path="a.b._c")
    assert m.is_public is False


def test_A4_real_path_is_physical_module_name():
    mod = ModuleType("a.b._c")
    m = LibraryModule(real_path=mod.__name__, mod_path="a.b.c")
    assert m.real_path == "a.b._c"


# ---------------------------------------------------------------------------
# B 组：pub_mod 发现 / 函数收集（阶段A→B）
# ---------------------------------------------------------------------------


def test_B1_public_alias_module_function_collected(tmp_path, monkeypatch):
    """⛔ 模块级别名 `import a.b._c as c` → foo 以公开 func_path 进池。当前 v2 漏收集。"""
    _install(
        tmp_path,
        monkeypatch,
        {
            "__init__.py": "",
            "b/__init__.py": "import aliaslib.b._c as c\n",
            "b/_c.py": "def foo(x):\n    return x + 1\n",
        },
    )
    # 验证公开调用路径确实可用
    b = importlib.import_module("aliaslib.b")
    assert b.c.foo(1) == 2  # aliaslib.b.c.foo 是用户可调用的公开路径

    funcs = list(LibraryVisitor(_LIB).visit())
    foos = [f for f in funcs if f.func_path == "aliaslib.b.c.foo"]
    assert len(foos) == 1, f"expected 1 foo, got {[f.func_path for f in funcs]}"
    f = foos[0]
    assert f.real_path == "aliaslib.b._c.foo"  # 身份 = 物理名
    assert f.function_type == "user_defined"
    # 经别名 + 私有名双可达，只收集一次（B3）
    assert len(funcs) == 1


def test_B1b_discover_public_modules_alias_real_path(tmp_path, monkeypatch):
    """discover_public_modules：模块 real_path 走 find_real_path（物理身份），mod_path 用公开别名。"""
    _install(
        tmp_path,
        monkeypatch,
        {
            "__init__.py": "",
            "b/__init__.py": "import aliaslib.b._c as c\n",
            "b/_c.py": "def foo(x):\n    return x + 1\n",
        },
    )
    importlib.import_module("aliaslib.b")  # 触发 b 导入并绑定为 aliaslib 属性
    v = LibraryVisitor(_LIB)
    mods = v.discover_public_modules()
    alias = [m for m in mods if m.mod_path == "aliaslib.b.c"]
    assert len(alias) == 1
    assert alias[0].real_path == "aliaslib.b._c"  # 物理身份（经 find_real_path）
    assert alias[0].is_public is True


def test_B2_private_non_aliased_module_not_collected(tmp_path, monkeypatch):
    _install(
        tmp_path,
        monkeypatch,
        {
            "__init__.py": "",
            "secret.py": "def hidden(): return 1\n",
        },
    )
    funcs = [f.func_path for f in LibraryVisitor(_LIB).visit()]
    assert "aliaslib.secret.hidden" not in funcs  # 私有模块不公开 → 不收集


def test_B4_cross_library_star_import_filtered(tmp_path, monkeypatch):
    _install(
        tmp_path,
        monkeypatch,
        {
            "__init__.py": "from os import *\nfrom . import own\n",
            "own.py": "def myfun(): return 1\n",
        },
    )
    funcs = list(LibraryVisitor(_LIB).visit())
    # 收集的每个函数物理归属都是 aliaslib（B4 跨库 star 灌入被挡）
    for f in funcs:
        assert f.real_path.split(".")[0] == "aliaslib"
    assert all("getcwd" not in f.func_path for f in funcs)
    assert any(f.func_path == "aliaslib.own.myfun" for f in funcs)


def test_B6_multi_level_public_recursion(tmp_path, monkeypatch):
    _install(
        tmp_path,
        monkeypatch,
        {
            "__init__.py": "from . import a\n",
            "a/__init__.py": "from . import b\n",
            "a/b/__init__.py": "def deep(): return 1\n",
        },
    )
    funcs = [f.func_path for f in LibraryVisitor(_LIB).visit()]
    assert "aliaslib.a.b.deep" in funcs


# ---------------------------------------------------------------------------
# C 组：find_real_path（§8.1）
# ---------------------------------------------------------------------------


def test_C1_math_sin():
    import math

    assert find_real_path(math, math.sin) == ("math", "sin")


def test_C2_torch_linalg_norm():
    torch = pytest.importorskip("torch")
    assert find_real_path(torch, torch.linalg.norm) == (
        "torch",
        "_C",
        "_linalg",
        "linalg_norm",
    )


def test_C5_memoization():
    import math

    cache: dict = {}
    r1 = find_real_path(math, math.sin, cache=cache)
    r2 = find_real_path(math, math.sin, cache=cache)
    assert r1 == r2 == ("math", "sin")


def test_C7_none_on_unresolvable():
    """dill 无法给出该库前缀身份 → None（异常信号）。"""
    root = ModuleType("mylib")
    obj = object()  # 非函数，无 __module__，dill 解析不到库前缀
    assert find_real_path(root, obj) is None


def test_C4_override_hook():
    root = ModuleType("paddle")
    obj = object()

    def override(_o, _fp):
        return ("paddle", "base", "to_tensor")

    REALPATH_OVERRIDES["paddle"] = override
    try:
        assert find_real_path(root, obj) == ("paddle", "base", "to_tensor")
    finally:
        REALPATH_OVERRIDES.pop("paddle", None)


def test_C6_dill_failure_falls_through(monkeypatch):
    import math

    import respfuzzer.lib.realpath as rp

    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(rp.dill.source, "_namespace", boom)
    assert find_real_path(math, math.sin) is None  # dill 失败且无 override → None


def test_F4_old_data_func_name_compat():
    """F4 旧数据兼容：老 data JSON 用 `func_name` 键，新模型读取需映射到 func_path。"""
    from respfuzzer.models import Function, Seed

    f = Function.model_validate({"func_name": "a.b.c", "source": "", "args": []})
    assert f.func_path == "a.b.c"
    s = Seed.model_validate(
        {
            "func_id": 1,
            "library_name": "a",
            "func_name": "a.b.c",
            "args": [],
            "function_call": "",
        }
    )
    assert s.func_path == "a.b.c"
