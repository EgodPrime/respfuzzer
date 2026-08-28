"""E-15 语义预言机：区分「调用成功（进了函数体）」与「构造级调用失败」。

FC 度量与变异奖励过去把「调用抛异常」一律视为语义错误 / 不覆盖（P-3 / E-15
审稿意见）。本模块提供一个无需 ground-truth 的更强语义判据：**调用是否真正
进入了目标函数体**。

- 若异常回溯栈中包含目标函数自身的帧（``func.__code__``）⇒ 参数绑定成功、真的
  进了函数体，异常是函数在无效输入上主动抛出的「领域级 / 预期行为」⇒ 视为
  有效调用（invocation success）。
- 若回溯中不含目标函数帧 ⇒ 异常发生在调用 / 绑定层（缺参、参数过多、接收者属性
  不存在、名字未定义等）⇒ 调用本身构造错误 ⇒ 视为无效调用（call-construction
  failure）。

用「帧判断」而非「异常类名」，从而对函数体内主动抛任意异常（含 TypeError /
AttributeError）的边界情况也稳健。对无 ``__code__`` 的内置 / C 函数（不产生
Python 帧，如 numpy 的 C 扩展）退化为异常类启发式。
"""

from __future__ import annotations

# 绑定 / 构造级异常的典型异常类：绝大多数在目标函数体执行之前发生。
# 仅用于无 ``__code__`` 的内置 / C 函数回退（其不产生可检查的 Python 帧）。
_CONSTRUCTION_ERROR_TYPES = (TypeError, AttributeError, NameError)


def entered_function_body(func, exc: BaseException) -> bool:
    """Return True iff ``exc`` was raised by code executing inside ``func``'s own
    frame -- i.e. the call successfully *entered* the function body.

    这是 E-15 的『调用成功』语义预言机判据。区分两种情形：
      - 领域级异常（进函数体后在无效输入上被函数正确拒绝 = 预期行为 = 有效调用）；
      - 构造级错误（根本没进函数体 = 无效调用）。

    Args:
        func: 目标函数或绑定方法。
        exc: 调用 ``func`` 时捕获到的异常。

    Returns:
        True  异常源于目标函数自身帧（进了函数体/领域级）。
        False 异常在绑定/调用层（未进函数体/构造级）。
    """
    code = getattr(func, "__code__", None)
    if code is None:
        # 绑定方法无直接 __code__，但从其底层函数可取得官方代码对象。
        code = getattr(getattr(func, "__func__", None), "__code__", None)

    if code is not None:
        tb = exc.__traceback__
        while tb is not None:
            if tb.tb_frame.f_code is code:
                return True
            tb = tb.tb_next
        return False

    # 内置 / C 扩展函数：没有可检查的 Python 帧，退化为异常类启发式。
    # 绑定/构造级错误通常表现为这几类；其余（ValueError/KeyError/IndexError/
    # 自定义异常等）视为进入后的领域异常。
    return not isinstance(exc, _CONSTRUCTION_ERROR_TYPES)
