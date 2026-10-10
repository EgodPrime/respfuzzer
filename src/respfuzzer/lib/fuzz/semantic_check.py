"""算法2 中 `IsValidSemantic`（见 ``docs/latex_v7/sections/our-approach.tex``）的实现。

论文对该函数的描述是：生成的变体 ``v`` 必须既能通过 Python 内建函数 ``compile``，
又包含对目标函数的有效调用。

本模块据此把语义有效性拆成两个层次：

1. **语法检查（``ast.parse``）**：只捕获解析阶段的 ``SyntaxError``。
2. **编译检查（``compile``）**：在语法检查之上额外执行符号表分析与字节码生成，
   因此能捕获 ``ast.parse`` 放过、但 ``compile`` 拒绝的错误，例如模块级
   ``return`` / ``break`` / ``yield``、模块级 ``nonlocal``、重复形参、赋值给
   ``__debug__`` 等。
3. **目标调用存在性检查**：确认变体代码中存在对目标函数（``Mutant.func_name``，
   限定路径，例如 ``numpy.linalg.norm``）的调用。

第 3 步的判定顺序（尽量静态、必要时才 import）：

1. 收集代码中的 ``import`` / ``from ... import ...`` 别名，把调用表达式的点分
   路径归一化后与目标路径做字符串比较（可覆盖 ``import numpy as np``、
   ``from numpy.linalg import norm as n`` 等写法）；
2. 字符串不相等时，把两侧路径用 ``importlib`` 解析为运行时对象，做 ``is``
   同一性比较（可覆盖子模块 re-export，例如 ``from scipy.linalg import norm``
   实际拿到的是 numpy 的函数对象）；只要两侧都能解析且不是同一个对象，就判定
   为「未调用目标函数」；
3. 只有当解析失败（无法静态确定对象身份）时，才降级为「归一化路径的名字后缀
   比较」并记录 debug 日志。

已知局限：把目标函数先赋给局部变量（``f = math.ceil; f(1.5)``）或通过
``getattr`` / 容器取出后再调用的写法无法被本模块识别，会被判为未调用目标函数。
"""

import ast
import importlib
from dataclasses import dataclass
from functools import lru_cache

from loguru import logger

from respfuzzer.models import Mutant

REASON_OK = "ok"
REASON_SYNTAX_ERROR = "syntax_error"
REASON_COMPILE_ERROR = "compile_error"
REASON_NO_TARGET_CALL = "no_target_call"


@dataclass
class CheckResult:
    """语义有效性检查的结果。

    Attributes:
        ok: 变体是否通过检查（即算法2 中 ``IsValidSemantic(v) == true``）
        reason: 未通过的原因，取值为 ``REASON_*`` 常量之一
        error_line: 语法/编译错误的行号（如果可得）
        detail: 人类可读的补充信息（错误消息等）
        target_called: 是否检测到对目标函数的调用；语法/编译阶段即失败时为 None
    """

    ok: bool
    reason: str
    error_line: int | None = None
    detail: str = ""
    target_called: bool | None = None

    def __bool__(self) -> bool:
        return self.ok


def _build_import_aliases(tree: ast.AST) -> dict[str, str]:
    """收集代码中的 import 别名，返回 ``局部名字 -> 完整点分路径`` 的映射。

    例：
        ``import numpy as np``                 -> ``{"np": "numpy"}``
        ``import numpy.linalg``                -> ``{"numpy": "numpy"}``
        ``import numpy.linalg as la``          -> ``{"la": "numpy.linalg"}``
        ``from numpy.linalg import norm``      -> ``{"norm": "numpy.linalg.norm"}``
        ``from numpy.linalg import norm as n`` -> ``{"n": "numpy.linalg.norm"}``

    相对导入（``node.level > 0``）无法静态还原为绝对路径，直接忽略。
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    aliases[root] = root
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                aliases[local] = f"{module}.{alias.name}" if module else alias.name
    return aliases


def _dotted_name(node: ast.AST) -> str | None:
    """把 ``Name`` / ``Attribute`` 链还原成点分字符串，其他表达式返回 None。"""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _normalize(dotted: str, aliases: dict[str, str]) -> str:
    """把点分路径的第一个名字按 import 别名表替换。"""
    head, _, tail = dotted.partition(".")
    if head not in aliases:
        return dotted
    return f"{aliases[head]}.{tail}" if tail else aliases[head]


@lru_cache(maxsize=2048)
def _resolve_object(dotted: str) -> object | None:
    """把点分路径解析为运行时对象，解析失败返回 None。

    解析成功时会 import 对应模块（模块级代码会被执行一次），因此只在字符串比较
    失败后才调用；进程内结果被缓存，避免重复 import。
    """
    parts = dotted.split(".")
    for split in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:split]))
        except Exception as e:
            # 模块名来自 LLM 生成的代码，任何导入失败都只说明「无法静态确定对象身份」。
            logger.debug(f"Cannot resolve module {'.'.join(parts[:split])!r}: {e}")
            continue
        try:
            for attr in parts[split:]:
                obj = getattr(obj, attr)
        except AttributeError:
            continue
        return obj
    return None


def _name_suffix_match(candidate: str, target: str) -> bool:
    """名字后缀比较，仅用于对象身份无法静态确定时的降级判定。"""
    return candidate.endswith("." + target) or target.endswith("." + candidate)


def _calls_target(tree: ast.AST, target: str, aliases: dict[str, str]) -> bool:
    """判断变体代码中是否存在对 ``target``（限定路径）的调用。"""
    # 目标对象按需解析：绝大多数变体能在字符串比较这一步命中，无需 import 目标库。
    target_obj: object | None = None
    target_resolved = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted_name(node.func)
        if dotted is None:
            continue
        candidate = _normalize(dotted, aliases)
        if candidate == target:
            return True
        candidate_obj = _resolve_object(candidate)
        if candidate_obj is not None and not target_resolved:
            target_obj = _resolve_object(target)
            target_resolved = True
        if candidate_obj is not None and target_obj is not None:
            if candidate_obj is target_obj:
                logger.debug(
                    f"Target call matched by object identity: {candidate} is {target}"
                )
                return True
            # 双方都能解析为不同对象，确定没有调用目标函数，不再降级判定。
            continue
        if _name_suffix_match(candidate, target):
            logger.debug(
                f"Imprecise target call match: candidate={candidate}, target={target}"
            )
            return True
    return False


def has_valid_call(mutant: Mutant) -> CheckResult:
    """检查 LLM 变体是否同时通过编译检查与目标调用存在性检查（算法2 的 ``IsValidSemantic``）。

    Args:
        mutant: 待检查的变体，使用其 ``function_call``（代码）与 ``func_name``
            （目标函数的限定路径）

    Returns:
        CheckResult: 检查结果；``ok`` 为 True 表示变体同时通过编译检查与目标调用
        存在性检查
    """
    code = mutant.function_call
    target = mutant.func_name

    try:
        tree = ast.parse(code, filename="<variant>")
    except SyntaxError as e:
        return CheckResult(False, REASON_SYNTAX_ERROR, e.lineno, e.msg or "", None)
    except ValueError as e:
        return CheckResult(False, REASON_SYNTAX_ERROR, None, str(e), None)

    try:
        compile(tree, "<variant>", "exec")
    except (SyntaxError, ValueError) as e:
        return CheckResult(
            False,
            REASON_COMPILE_ERROR,
            getattr(e, "lineno", None),
            getattr(e, "msg", None) or str(e),
            None,
        )

    aliases = _build_import_aliases(tree)
    if not _calls_target(tree, target, aliases):
        return CheckResult(
            False,
            REASON_NO_TARGET_CALL,
            None,
            f"no call to the target function {target}",
            False,
        )

    return CheckResult(True, REASON_OK, None, "", True)
