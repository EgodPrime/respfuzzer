"""RQ1 F9：共享 Executor 测试——covered 判定（ED-15 语义 + C-6 静态门控）。

- ut-F9-2 covered = ED-15 异常分级（构造级不覆盖 / 领域级计覆盖）+
          C-6 is_valid_semantic 静态门控（非"能运行"即算）。
- it-F9  execute 返回 schema（covered/result_type/error）签收。
判定目标用 math.sqrt（C 实现 → 退化异常类启发式）与构造型异常边界。
"""

from __future__ import annotations

from respfuzzer.lib.rq1.executor import RQ1Executor, static_gate
from respfuzzer.models import Function

executor = RQ1Executor(timeout=5.0)


def _func(func_path: str = "math.sqrt") -> Function:
    return Function(func_path=func_path, library_name="math", source="", args=[])


# --- C-6 静态门控（ut-F9-2 前半）---


def test_static_gate_accepts_target_call():
    code = "from math import sqrt\nx = sqrt(4.0)"
    assert static_gate(_func(), code) is True


def test_static_gate_rejects_no_target_call():
    assert static_gate(_func(), "x = 1 + 1") is False


def test_static_gate_rejects_syntax_error():
    assert static_gate(_func(), "def (:") is False


def test_static_gate_rejects_empty():
    assert static_gate(_func(), "") is False
    assert static_gate(_func(), "   \n") is False


# --- ED-15 执行判定（ut-F9-2 后半）---


def test_executor_no_exception_covered():
    """无异常 → covered=True。"""
    res = executor.execute(_func(), "from math import sqrt\nx = sqrt(4.0)")
    assert res["covered"] is True
    assert res["result_type"] == "OK"


def test_executor_construction_failure_not_covered():
    """ut-F9-2：构造级异常（缺参 TypeError，未进函数体）→ 不覆盖。"""
    res = executor.execute(_func(), "from math import sqrt\nx = sqrt()")
    assert res["covered"] is False
    assert res["result_type"] == "CALLFAIL"


def test_executor_domain_exception_covered():
    """ut-F9-2：领域级异常（math domain error，进函数体后被函数拒绝）→ 覆盖。"""
    res = executor.execute(_func(), "from math import sqrt\nx = sqrt(-1.0)")
    assert res["covered"] is True


def test_executor_static_gate_short_circuit():
    """covered() 对不过静态门控的代码直接 False（不启动子进程）。"""
    assert executor.covered(_func(), "x = 1 + 1") is False


def test_executor_timeout_not_covered():
    """超时 → covered=False + TIMEOUT。"""
    code = "import time\ntime.sleep(30)"
    fast = RQ1Executor(timeout=0.5)
    res = fast.execute(_func(func_path="time.sleep"), code)
    assert res["covered"] is False
    assert res["result_type"] == "TIMEOUT"


def test_executor_resolve_failure_not_covered():
    """目标函数无法解析 → covered=False（resolve 错误）。"""
    res = executor.execute(_func(func_path="nonexistent_mod.nofunc"), "x=1")
    assert res["covered"] is False
    assert str(res.get("error", "")).startswith("resolve")


def test_executor_result_schema():
    """it-F9：execute 返回 schema 键齐全。"""
    res = executor.execute(_func(), "from math import sqrt\nx = sqrt(4.0)")
    for key in ("covered", "result_type", "error"):
        assert key in res, f"缺少结果键: {key}"
