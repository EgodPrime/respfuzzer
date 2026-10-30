import contextlib
import json
import os
import random
import shutil
import signal
import argparse
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import ContextVar
from pathlib import Path
from typing import List, Optional

import openai
from loguru import logger

from respfuzzer.models import ExecutionResultType, Function, Seed
from respfuzzer.lib.retriever import Retriever
from respfuzzer.lib.fuzz.semantic_check import is_valid_call
from respfuzzer.repos import get_functions
from respfuzzer.utils.config import get_config
from respfuzzer.utils.logging import setup_logging
from respfuzzer.utils.paths import DATA_DIR, PROJECT_DIR

OUTPUT_DIR = PROJECT_DIR / "experiments" / "function_solutions"

#: 种子执行沙箱根（``run_data/``，可用 ``RESPFUZZER_RUN_DATA`` 覆盖）。
#: QueitExecutor 的子进程 cwd 指向其中的一次性目录，seed 的相对路径产物
#: 不再污染项目根（numpy.so / paddle/ 遮蔽真实包的教训，随 RQ1 引擎移植）。
def _run_data_root() -> Path:
    root = Path(os.environ.get("RESPFUZZER_RUN_DATA", "run_data"))
    root.mkdir(parents=True, exist_ok=True)
    return root

#: 超时强制清理：SIGKILL 整个进程组（start_new_session 下含孙进程），再 reap 防僵尸。
#:
#: ``proc.kill()`` 只杀直接子进程——seed 内 Popen 派生的孙进程持有管道会逃逸并
#: 拖垮整 run（paddle ours 卡死根因之一）；按进程组整组强杀才是闭环超时控制。
#: 随 RQ1 引擎 ``experiments/RQ1/engine/executor.py`` 移植。
def _kill_process_group(proc: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        pgid = os.getpgid(proc.pid)
        if pgid and pgid > 1:
            os.killpg(pgid, signal.SIGKILL)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)

cfg = get_config("reflective_seeder")
llm_cfg = get_config("llm")

client = openai.Client(api_key=llm_cfg["api_key"], base_url=llm_cfg["base_url"])


#: ---- LLM 资源计数【MC4-3】：全局累计 + 每函数归因桶（ContextVar 按线程隔离）----
_usage_lock = threading.Lock()
_usage_total: dict[str, int] = {
    "calls": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
}
_usage_bucket_var: ContextVar[dict[str, int] | None] = ContextVar(
    "agentic_usage_bucket", default=None
)


def _record_usage(response) -> None:
    """记录一次 LLM response 的资源消耗：进程内累计 + 当前线程的每函数桶。

    usage 缺失（部分端点/异常路径）时 token 记 0，不抛。
    """
    usage = getattr(response, "usage", None)
    delta = {
        "calls": 1,
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
    }
    with _usage_lock:
        for key, value in delta.items():
            _usage_total[key] += value
    bucket = _usage_bucket_var.get()
    if bucket is not None:
        for key, value in delta.items():
            bucket[key] = bucket.get(key, 0) + value
class AbstractAttempter:
    def make_prompt(self, function: Function, history: list) -> str:
        raise NotImplementedError("Subclasses must implement make_prompt")

    def generate(self, function: Function, history: list) -> str:
        prompt = self.make_prompt(function, history)
        last_exc = None
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=llm_cfg["model_name"],
                    temperature=0.7,
                    messages=[
                        {
                            "role": "system",
                            "content": "你是一个代码生成助手，你的名字是attempter，擅长根据用户提供的信息信息生成函数调用。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=8000,
                    extra_body={
                        "chat_template_kwargs": {"enable_thinking": False},
                        "reasoning": {"enabled": False}
                    },
                )
                _record_usage(response)
                code = response.choices[0].message.content
                if code is None:
                    raise ValueError("模型返回空内容 (content is None)")
                code = code.strip()
                # tolerate some common variations: try to extract code between <code> tags
                if "<code>" in code and "</code>" in code:
                    return code.split("<code>")[1].split("</code>")[0]
                # fallback: if triple-backtick used, extract that
                if "```" in code:
                    parts = code.split("```")
                    # if language specified like ```py, the code is in parts[1]
                    if len(parts) >= 3:
                        return parts[1].strip()
                    elif len(parts) >= 2:
                        return parts[1].strip()
                # if no recognized wrapper, raise to trigger retry or higher-level handling
                raise ValueError("模型返回不包含 <code> 或 ``` 包裹的代码段")
            except Exception as e:
                last_exc = e
                logger.debug(f"Attempter.generate attempt {attempt+1} failed: {e}")
                # transient wait before retry
                time.sleep(1 + attempt)
                continue
        # after retries, raise a clear exception with traceback
        tb = traceback.format_exception_only(type(last_exc), last_exc)
        raise Exception(f"生成函数调用时发生错误，最后一次错误: {''.join(tb)}")
    
class AttempterRespFuzzer(AbstractAttempter):
    def make_prompt(self, function: Function, history: list) -> str:
        package_path, func_name = function.func_path.rsplit(".", 1)
        prompt = f"""任务:
请根据`function`和`history`中的信息来为{function.func_path}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。

注意：
1. 你生成的代码应该用<code></code>包裹。
2. 不要生成``` 
3. 不要生成`code`以外的任何内容 
4. 不要生成与`function`无关的代码(例如打印、注释、画图等)
5. 生成的`code`以"from {package_path} import {func_name}"开头
6. 生成的`code`中函数调用形式为"{func_name}(...)"

例子：
<function>
{{
    func_name: "a.b.c",
    ...  // 其他字段省略
}}
</function>
<history>
...
</history>
<code>
from a.b import c
x = 2
y = "str"
res = c(x, y)
</code>

现在任务开始：
<function>
{function.model_dump_json()}
</function>
<history>
{history}
</history>
"""
        return prompt
    
class AttempterWithoutDocs(AbstractAttempter):
    def make_prompt(self, function: Function, history: list) -> str:
        from inspect import _ParameterKind

        package_path, func_name = function.func_path.rsplit(".", 1)
        func_args = function.args
        func_sig = f"def {func_name}("
        if func_args:
            for i, arg in enumerate(func_args):
                if i > 0:
                    func_sig += ", "
                if arg.pos_type == _ParameterKind.POSITIONAL_ONLY.name:
                    func_sig += arg.arg_name
                elif arg.pos_type == _ParameterKind.POSITIONAL_OR_KEYWORD.name:
                    func_sig += arg.arg_name
                elif arg.pos_type == _ParameterKind.VAR_POSITIONAL.name:
                    func_sig += "*" + arg.arg_name
                elif arg.pos_type == _ParameterKind.KEYWORD_ONLY.name:
                    func_sig += arg.arg_name
                elif arg.pos_type == _ParameterKind.VAR_KEYWORD.name:
                    func_sig += "**" + arg.arg_name
        func_sig += "): ..."
        prompt = f"""任务:
请根据`function`和`history`中的信息来为{function.func_path}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。

注意：
1. 你生成的代码应该用<code></code>包裹。
2. 不要生成```
3. 不要生成`code`以外的任何内容
4. 不要生成与`function`无关的代码(例如打印、注释、画图等)
5. 生成的`code`以"from {package_path} import {func_name}"开头
6. 生成的`code`中函数调用形式为"{func_name}(...)"

例子：
<function>
{{
    func_name: "a.b.c",
    ...  // 其他字段省略
}}
</function>
<history>
...
</history>
<code>
from a.b import c
x = 2
y = "str"
res = c(x, y)
</code>

现在任务开始：
<function>
{func_sig}
</function>
<history>
{history}
</history>
"""
        return prompt

class AttempterWithoutFeedback(AbstractAttempter):
    def make_prompt(self, function: Function, history: list) -> str:
        package_path, func_name = function.func_path.rsplit(".", 1)
        prompt = f"""任务:
请根据`function`中的信息来为{function.func_path}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。

注意：
1. 你生成的代码应该用<code></code>包裹。
2. 不要生成```
3. 不要生成`code`以外的任何内容
4. 不要生成与`function`无关的代码(例如打印、注释、画图等)
5. 生成的`code`以"from {package_path} import {func_name}"开头
6. 生成的`code`中函数调用形式为"{func_name}(...)"

例子：
<function>
{{
    func_name: "a.b.c",
    ...  // 其他字段省略
}}
<code>
from a.b import c
x = 2
y = "str"
res = c(x, y)
</code>

现在任务开始：
<function>
{function.model_dump_json()}
</function>
"""
        return prompt

class AttempterWithoutDocsAndFeedback(AbstractAttempter):
    def make_prompt(self, function: Function, history: list) -> str:
        from inspect import _ParameterKind

        package_path, func_name = function.func_path.rsplit(".", 1)
        func_args = function.args
        func_sig = f"def {func_name}("
        if func_args:
            for i, arg in enumerate(func_args):
                if i > 0:
                    func_sig += ", "
                if arg.pos_type == _ParameterKind.POSITIONAL_ONLY.name:
                    func_sig += arg.arg_name
                elif arg.pos_type == _ParameterKind.POSITIONAL_OR_KEYWORD.name:
                    func_sig += arg.arg_name
                elif arg.pos_type == _ParameterKind.VAR_POSITIONAL.name:
                    func_sig += "*" + arg.arg_name
                elif arg.pos_type == _ParameterKind.KEYWORD_ONLY.name:
                    func_sig += arg.arg_name
                elif arg.pos_type == _ParameterKind.VAR_KEYWORD.name:
                    func_sig += "**" + arg.arg_name
        func_sig += "): ..."
        prompt = f"""任务:
请根据`function`中的信息来为{function.func_path}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。

注意：
1. 你生成的代码应该用<code></code>包裹。
2. 不要生成```
3. 不要生成`code`以外的任何内容
4. 不要生成与`function`无关的代码(例如打印、注释、画图等)
5. 生成的`code`以"from {package_path} import {func_name}"开头
6. 生成的`code`中函数调用形式为"{func_name}(...)"

例子：
<function>
{{
    func_name: "a.b.c",
    ...  // 其他字段省略
}}
</function>
<code>
from a.b import c
x = 2
y = "str"
res = c(x, y)
</code>

现在任务开始：
<function>
{func_sig}
</function>
"""
        return prompt

class AttempterRAG(AbstractAttempter):
    def __init__(self, retriever: Retriever):
        self.retriever = retriever

    def make_prompt(self, function: Function, history: list) -> str:
        package_path, func_name = function.func_path.rsplit(".", 1)
        related_ctx: str = self.retriever.retrieve(function.func_path)
        prompt = f"""任务:
请根据`function`和`related_ctx`中的信息以及相关上下文来为{function.func_path}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。

注意：
1. 你生成的代码应该用<code></code>包裹。
2. 不要生成```
3. 不要生成`code`以外的任何内容
4. 不要生成与`function`无关的代码(例如打印、注释、画图等)
5. 生成的`code`以"from {package_path} import {func_name}"开头
6. 生成的`code`中函数调用形式为"{func_name}(...)"

例子：
<function>
{{
    func_name: "a.b.c",
    ...  // 其他字段省略
}}
</function>
<related_ctx>
...
</related_ctx>
<code>
from a.b import c
x = 2
y = "str"
res = c(x, y)
</code>

现在任务开始：
<function>
{function.model_dump_json()}
</function>
<related_ctx>
{related_ctx}
</related_ctx>
"""
        return prompt

class QueitExecutor:
    def gen_code(self, code: str, full_name: str) -> str:
        """
        生成用于执行的完整代码，包含对目标函数调用的检查。

        例如，假设full_name是"a.b.c"，生成的代码结构如下：


        >>> with instrument_function_via_path_check_ctx("a.b.c") as f:
            code
            if not f.called:
                raise Exception(f"未包含对{full_name}的有效调用")
        """
        # ident code with 4 spaces
        indented_code = "\n    ".join(code.split("\n"))
        res = f"""
from respfuzzer.lib.fuzz.instrument import instrument_function_via_path_check_ctx

with instrument_function_via_path_check_ctx("{full_name}") as f:
    {indented_code}
    if not f.called:
        raise Exception(f"未包含对{full_name}的有效调用")
"""
        return res

    def execute(self, code: str, full_name: str) -> dict:
        ret_code = 1
        stdout = ""
        stderr = ""
        proc = None
        # 沙箱 cwd：seed 的相对路径产物落 run_data/ 一次性目录，执行完即删
        # 沙箱：每次执行前 mkdtemp 新建一次性隔离目录（cwd + 脚本都在其中），
        # 执行完 finally 里 rmtree 删除 —— 单次执行的一切产物（含临时脚本）
        # 都不落沙箱之外，防止 /tmp 或项目根的同名 .py/.so/包目录遮蔽真实包
        # （事故根因：脚本曾默认写 /tmp，sys.path[0]=/tmp，某 seed 写入的
        #  /tmp/numpy.cpython-*.so 全零垃圾使所有依赖 numpy 的执行全部 ImportError）
        sandbox = Path(tempfile.mkdtemp(prefix="fz-", dir=_run_data_root()))
        # 脚本同样写进沙箱：sys.path[0]=脚本目录，落 /tmp 会被其中同名模块遮蔽
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".py", dir=sandbox, delete=True) as f:
            f.write(self.gen_code(code, full_name))
            f.flush()
            # sys.executable：与父进程同一解释器（.venv），系统 python3 缺本项目依赖
            command = [sys.executable, f.name]
            try:
                # 启动子进程
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.PIPE,
                    text=True,  # 读取为字符串
                    shell=False,
                    start_new_session=True,
                    cwd=str(sandbox),  # 沙箱 cwd：产物不落项目根
                )

                # 读取输出（捕获所有）
                try:
                    stdout, stderr = proc.communicate(
                        input="\n" * 24, timeout=10
                    )  # 10秒超时
                    ret_code = proc.returncode
                    if ret_code != 0:
                        result_type = ExecutionResultType.ABNORMAL
                    else:
                        result_type = ExecutionResultType.OK
                except subprocess.TimeoutExpired as e:
                    # try to kill and collect any remaining output
                    result_type = ExecutionResultType.TIMEOUT
                    try:
                        if proc is not None:
                            # 整组强杀（含孙进程），防逃逸管道拖垮 run
                            _kill_process_group(proc)
                            out, err = proc.communicate(timeout=1)
                            stdout = (stdout or "") + (out or "")
                            stderr = (stderr or "") + (err or "")
                    except Exception:
                        # ignore further errors while cleaning up
                        pass
                    stderr = (stderr or "") + f"\nTimeoutExpired: {str(e)}"
                    ret_code = 124
            except Exception as e:
                result_type = ExecutionResultType.CALLFAIL
                stderr = (
                    (stderr or "")
                    + f"\nException when starting subprocess: {str(e)}\n"
                    + traceback.format_exc()
                )
                ret_code = 1
            finally:
                result = {
                    "result_type": result_type,
                    "ret_code": ret_code,
                    "stdout": stdout,
                    "stderr": stderr,
                }
                shutil.rmtree(sandbox, ignore_errors=True)  # 沙箱即删
            return result


class Reasoner:
    def explain(self, code: str, result: dict) -> str:
        """解释代码执行结果，提供修正建议。

        Raises:
            Exception: If explanation generation fails or response format is invalid.
        """
        prompt = f"""<code>\n{code}\n</code>\n<result>\n{result["stderr"]}\n</result>\n`code`中的代码在执行后得到报错`result`，请对这一执行结果进行解释，以指导代码编写人员进行修正指导。输出结果应为一段话，用<explain></explain>包裹。如果缺少文件，则提示Attempter通过open创建相应的临时文件。如果是参数错误，则提示Attempter调整参数的创建和初始化过程。请确保解释内容具体且有针对性。"""
        # small retry loop for transient API issues
        last_exc = None
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=llm_cfg["model_name"],
                    temperature=0.7,  # RQ1/RSG 解码设置统一 0.7（与 CountingLLM 一致）
                    messages=[
                        {
                            "role": "system",
                            "content": "你是一个代码调试助手，擅长解释代码错误并提供修正建议。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=8000,
                    extra_body={
                        "chat_template_kwargs": {"enable_thinking": False},
                        "reasoning": {"enabled": False}
                    },
                )
                _record_usage(response)
                explanation = response.choices[0].message.content
                if explanation is None:
                    raise ValueError("模型返回空内容 (content is None)")
                explanation = explanation.strip()
                if "<explain>" in explanation and "</explain>" in explanation:
                    return explanation.split("<explain>")[1].split("</explain>")[0]
                # fallback: return whole text if no tags but non-empty
                if explanation:
                    return explanation
                raise ValueError("模型返回空的解释内容")
            except Exception as e:
                last_exc = e
                logger.debug(f"Reasoner.explain attempt {attempt+1} failed: {e}")
                time.sleep(1 + attempt)
                continue
        tb = traceback.format_exception_only(type(last_exc), last_exc)
        raise Exception(f"解释执行结果时发生错误，最后一次错误: {''.join(tb)}")


class Judger:
    """确定性静态门控（算法2 的 ``IsValidSemantic``），取代原基于 LLM 的判定。

    同一 ``(code, function)`` 恒定结论、零 LLM 调用；未通过时给出结构化
    ``reason`` 回填自省历史，指导 attempter 下一轮修正。
    """

    def judge(self, code: str, function: Function) -> dict:
        """返回 ``{"valid": bool, "reason": str}``（与原 LLM Judger 同契约）。

        判定为纯本地静态检查（ast/compile + 目标调用存在性），不会因
        LLM 服务故障而重试或抛错。
        """
        check = is_valid_call(code, function)
        if check.ok:
            return {"valid": True, "reason": ""}
        package_path, func_name = function.func_path.rsplit(".", 1)
        if check.target_called is False:
            reason = (
                f"未找到对目标函数 {function.func_path} 的有效调用；"
                f'合理的import形式为"from {package_path} import {func_name}"，'
                f'合理的调用形式为"{func_name}(...)"'
            )
        else:
            loc = f"第{check.error_line}行" if check.error_line else "未知行"
            detail = check.detail or check.reason
            reason = f"代码未通过静态门控（{check.reason}，{loc}）: {detail}"
        return {"valid": False, "reason": reason}
    
class Solver:
    def __init__(self, library_name: str, mode: str = "respfuzzer", rs: Optional[int] = None):
        self.library_name = library_name
        self.mode = mode
        # 重复实验的随机种子（--rs）：用于产物文件名防覆盖与可追溯，None 时不加后缀
        self.rs = rs
        match mode:
            case "respfuzzer":
                self.attempter = AttempterRespFuzzer()
                self.use_reasoner = True
            case "respfuzzer-wodoc":
                self.attempter = AttempterWithoutDocs()
                self.use_reasoner = True
            case "respfuzzer-wofb":
                self.attempter = AttempterWithoutFeedback()
                self.use_reasoner = False
            case "respfuzzer-woboth":
                self.attempter = AttempterWithoutDocsAndFeedback()
                self.use_reasoner = False
            case "respfuzzer-rag":
                from respfuzzer.lib.retriever import Retriever
                retriever = Retriever(library_name)
                self.attempter = AttempterRAG(retriever)
                self.use_reasoner = False
            case _:
                raise ValueError(f"Unknown mode: {mode!r}")

        self.budget = int(cfg.get("budget", 10))
        # 逐函数实验开销账本【MC4-3】：solve_library_functions 收尾写 seeds_meta.json
        self._func_stats: dict[str, dict] = {}
        self._stats_lock = threading.Lock()
        self.judger = Judger()
        self.executor = QueitExecutor()
        self.reasoner = Reasoner()
        

    def solve(self, function: Function, stats: dict | None = None) -> Optional[str]:
        # 预算取本地副本：并发求解时 self.budget 不被改动（每函数各自独立预算，
        # 此前 self.budget -= 1 在 ThreadPool 下被多函数共享递减，账目互相污染）
        budget = self.budget
        rounds = 0
        history: List[dict] = []
        solved = False
        code = None

        while True:
            rounds += 1
            try:
                code = self.attempter.generate(function, history)
            except Exception as e:
                err_msg = f"Attempter error: {str(e)}"
                logger.debug(err_msg)
                history.append({"role": "attempter_error", "content": err_msg})
                budget -= 1
                if budget <= 0:
                    break
                continue

            history.append({"role": "attempter", "content": code})

            try:
                judgment = self.judger.judge(code, function)
                if judgment["valid"]:
                    logger.debug(f"Judger accepted the code:\n{code}")
                else:
                    logger.debug(f"Judger rejected the code: {judgment['reason']}")
                    history.append(
                        {
                            "role": "judger",
                            "content": f"Judger rejected the code: {judgment['reason']}",
                        }
                    )
                    budget -= 1
                    if budget <= 0:
                        break
                    continue
            except Exception as e:
                err_msg = f"Judger error: {str(e)}"
                logger.debug(err_msg)
                history.append({"role": "judger", "content": err_msg})
                budget -= 1
                if budget <= 0:
                    break
                continue

            result = self.executor.execute(code, function.func_path)

            if result["result_type"] == ExecutionResultType.OK:
                solved = True
                break
            else:
                if self.use_reasoner is False:
                    break

                try:
                    reason = self.reasoner.explain(code, result)
                except Exception as e:
                    reason = f"Reasoner error: {str(e)}"
                    logger.debug(reason)

                logger.debug(f"reason:\n{reason}")
                history.append({"role": "executor", "content": result.get("stderr")})
                history.append({"role": "reasoner", "content": reason})
                budget -= 1
                if budget == 0:
                    break
                continue

        if stats is not None:
            # 预算消耗次数 = 自省轮次（含成功那一轮；与 MC6-5「成功 → 成功那一轮」
            # 的等预算口径一致；失败每轮扣 1，故 rounds ≤ 初始预算）
            stats["budget_used"] = rounds
        if solved:
            return code
        else:
            return None
        

    def solve_function(self, function: Function) -> Optional[Seed]:
        logger.info(f"Try solving {function.func_path} ...")
        # 每函数资源归因桶：ContextVar 按线程隔离，并发函数不串账
        bucket_token = _usage_bucket_var.set(
            {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        )
        stats: dict = {}
        code = None
        try:
            code = self.solve(function, stats=stats)
            # solve() 返回 None 表示预算内未解出：直接判失败返回，不能拿 None
            # 去构造 Seed（pydantic 会抛 function_call 类型错，掩盖真实失败原因）
            if code is None:
                logger.info(f"Failed to solve {function.func_path}: no seed within budget")
                return None
            seed = Seed(
                func_id=function.id,
                library_name=function.library_name,
                func_path=function.func_path,
                args=function.args,
                function_call=code,
            )
            logger.success(f"Seed found for {function.func_path}:\n{code}")
            return seed
        except Exception as e:
            logger.info(f"Failed to solve {function.func_path}:\n{str(e)}")
        finally:
            bucket = _usage_bucket_var.get() or {}
            _usage_bucket_var.reset(bucket_token)
            with self._stats_lock:
                # 失败函数同样入账：失败也消耗了预算与 LLM，等预算对照需要这笔账
                self._func_stats[function.func_path] = {
                    "budget_used": int(stats.get("budget_used", 0)),
                    "llm_calls": int(bucket.get("calls", 0)),
                    "token_input": int(bucket.get("prompt_tokens", 0)),
                    "token_output": int(bucket.get("completion_tokens", 0)),
                }

    def solve_library_functions(self) -> None:
        # 统一日志初始化【框架级 logging】：控制台 INFO + 实验 .log（文件 sink DEBUG
        # 全量细节）落 RQ2 目录；文件名 <self.mode>_<model>_<library>_<ts>[_rs<N>].log，
        # 含所用模型名以区分不同模型的实验；同秒重复启动为追加写；
        # --rs 给定随机种子时追加 _rs<N> 后缀，避免重复实验互相覆盖
        rs_suffix = "" if self.rs is None else f"_rs{self.rs}"
        # 模型名可能含路径不安全字符（如 "org/model"），替换为 "-"
        model_tag = str(llm_cfg.get("model_name", "unknown")).replace("/", "-").replace(" ", "-")
        log_file = (
            OUTPUT_DIR
            / f"{self.mode}_{model_tag}_{self.library_name}_{time.strftime('%Y%m%dT%H%M%S')}{rs_suffix}.log"
        )
        setup_logging(level=str(cfg.get("log_level", "INFO")), log_file=log_file)
        logger.info(
            f"Seeding library {self.library_name} (mode={self.mode}, "
            f"concurrency={cfg.get('concurrency', 4)}) -> {log_file}"
        )
        functions = get_functions(self.library_name)
        concurrency = int(cfg.get("concurrency", 4))

        # 开跑前清空历史残留沙箱：正常执行即建即删（execute 的 finally rmtree），
        # 被 SIGKILL 的进程会留下 fz-* 目录。注意本设计要求同一 run_data 下
        # 串行执行 solve——并发多进程会互删对方的活动沙箱。
        root = _run_data_root()
        stale = [d for d in root.glob("fz-*") if d.is_dir()]
        for d in stale:
            shutil.rmtree(d, ignore_errors=True)
        if stale:
            logger.info(f"Cleared {len(stale)} stale sandbox dirs under {root}")

        seeds: List[Seed] = []
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(self.solve_function, function) for function in functions
            ]
            for future in as_completed(futures):
                seed = future.result()
                if seed is not None:
                    seeds.append(seed)

        # 实验开销元数据 → seeds_meta_<model>.json（与 seeds_<model>.json 同路径同 stem 对齐，
        # 文件名含模型名以区分不同模型的实验）；无论最终是否有 seed 都落盘（失败函数的开销同样要入账）
        (DATA_DIR / f"{self.mode}").mkdir(parents=True, exist_ok=True)
        meta = {
            "metadata": {
                "model_name": str(llm_cfg.get("model_name", "")),
                "temperature": llm_cfg.get("temperature", 0.7),
                "concurrency": concurrency,
                "library": self.library_name,
                "mode": self.mode,
                "random_seed": self.rs,
            },
            # 逐函数开销：预算消耗次数、LLM 调用次数、输入/输出 token 总量
            "per_function": dict(self._func_stats),
        }
        meta_file = (
            DATA_DIR / f"{self.mode}" / f"{self.library_name}_seeds_meta_{model_tag}{rs_suffix}.json"
        )
        with open(meta_file, "w") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        logger.info(f"Per-function cost meta -> {meta_file}")

        # Save all seeds to a JSON file
        if len(seeds) == 0:
            logger.info(f"No seeds generated for library {self.library_name}")
            return
        seeds_data = [seed.model_dump() for seed in seeds]
        output_file = DATA_DIR / f"{self.mode}" / f"{self.library_name}_seeds_{model_tag}{rs_suffix}.json"
        with open(output_file, "w") as f:
            json.dump(seeds_data, f, indent=2)

def solve_library(
    library_name: str, mode: str = "respfuzzer", rs: Optional[int] = None
) -> None:
    solver = Solver(library_name, mode=mode, rs=rs)
    solver.solve_library_functions()


def main():
    parser = argparse.ArgumentParser(description="RespFuzzer library seeding")
    parser.add_argument("library", help="Library name to seed (e.g., numpy, json)")
    parser.add_argument("--mode", default="respfuzzer", choices=[
        "respfuzzer", "respfuzzer-wodoc", "respfuzzer-wofb",
        "respfuzzer-woboth", "respfuzzer-rag",
    ], help="Seeding mode (default: %(default)s)")
    parser.add_argument(
        "--rs", type=int, default=None, metavar="INT",
        help="随机种子：启动时立即 random.seed(INT)，并在 2 个 json 与 1 个 log "
             "文件名后追加 _rs<INT> 后缀（默认不设种子、不加后缀）",
    )
    args = parser.parse_args()
    # 随机种子必须在任何求解动作之前设置，保证同一 --rs 的重跑可复现
    if args.rs is not None:
        random.seed(args.rs)
    solve_library(args.library, mode=args.mode, rs=args.rs)
