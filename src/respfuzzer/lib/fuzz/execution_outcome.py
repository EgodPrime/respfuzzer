"""变体执行结果（含运行时异常）在 worker 与父进程之间的传递类型。

评审意见 MC2-6：论文称语义执行错误应提供负面反馈，但实现中运行时异常被静默捕获
（`exec(seed.function_call)` 外的 `except Exception: pass`），未参与算法2 的语义奖励
计算。本模块提供：

- :class:`ExecutionOutcome`：worker 执行一个变体后的结果，可被
  ``multiprocessing.Queue`` / ``Pipe`` 序列化后回传父进程；
- :func:`classify_exception`：把执行期捕获的异常转成 ``ExecutionOutcome``，并记录
  异常类型、消息、所在行号，以及异常**抛出的位置**是变体代码还是库内部代码
  （供后续「函数调用成功覆盖率」等分析使用，不影响奖励计算）。

奖励计算统一由 :meth:`respfuzzer.lib.fuzz.llm_mutator.LLMMutator.reward_for_outcome`
完成：只要出现运行时异常，就按语义无效处理（奖励降到 0，且不再计算覆盖率增益）。
"""

from dataclasses import dataclass

from respfuzzer.models import ExecutionResultType

#: 执行 ``exec(code)`` 时变体自身代码所在的"文件名"
VARIANT_FILENAMES = frozenset({"<string>", "<variant>", "<stdin>"})

ORIGIN_VARIANT = "variant"
ORIGIN_LIBRARY = "library"


@dataclass
class ExecutionOutcome:
    """一次变体执行的结果。

    Attributes:
        result_type: 执行结果类型，复用 :class:`ExecutionResultType` 的词汇
            （正常结束为 ``OK``，执行期抛异常为 ``ABNORMAL``）
        error_type: 异常类名（如 ``TypeError``），无异常时为空串
        error_message: 异常消息，无异常时为空串
        error_line: 异常抛出所在行号（如果可得）
        origin: 异常抛出位置，``variant``（变体代码）或 ``library``（被调用的库代码）；
            无异常时为空串
    """

    result_type: ExecutionResultType = ExecutionResultType.OK
    error_type: str = ""
    error_message: str = ""
    error_line: int | None = None
    origin: str = ""

    @property
    def has_runtime_error(self) -> bool:
        """执行期是否抛出了运行时异常。"""
        return self.result_type != ExecutionResultType.OK

    def describe(self) -> str:
        """返回用于日志/结果记录的可读描述。"""
        if not self.has_runtime_error:
            return "ok"
        line = (
            f"line {self.error_line}" if self.error_line is not None else "unknown line"
        )
        return f"{self.error_type} at {line} ({self.origin}): {self.error_message}"


def classify_exception(exc: BaseException) -> ExecutionOutcome:
    """把执行期捕获的异常转成 :class:`ExecutionOutcome`。

    异常位置取 traceback 的**最后一帧**（真正抛出异常的位置）：``exec(code)`` 执行的
    变体代码帧名为 ``<string>``，因此最后一帧落在 ``<string>`` 即说明异常由变体代码
    自己抛出，否则视为库内部抛出。
    """
    last_tb = exc.__traceback__
    while last_tb is not None and last_tb.tb_next is not None:
        last_tb = last_tb.tb_next

    filename = ""
    line: int | None = None
    if last_tb is not None:
        filename = last_tb.tb_frame.f_code.co_filename
        line = last_tb.tb_lineno

    origin = ORIGIN_VARIANT if filename in VARIANT_FILENAMES else ORIGIN_LIBRARY
    return ExecutionOutcome(
        result_type=ExecutionResultType.ABNORMAL,
        error_type=type(exc).__name__,
        error_message=str(exc),
        error_line=line,
        origin=origin,
    )
