import json
import os
import signal
import subprocess
import tempfile
import time
import sys
import traceback
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

import openai
from loguru import logger

from respfuzzer.models import ExecutionResultType, Function, Seed
from respfuzzer.repos import get_functions
from respfuzzer.utils.config import get_config
from respfuzzer.utils.paths import DATA_DIR
from respfuzzer.lib.exec_lock import EXEC_LOCK

cfg = get_config("reflective_seeder")
llm_cfg = get_config("llm")

client = openai.Client(
    api_key=llm_cfg["api_key"],
    base_url=llm_cfg["base_url"],
    timeout=180,  # 硬超时护栏：防服务长沉默无限挂（paddle ours 教训；正常响应毫秒级）
)


class Attempter:
    def generate(self, function: Function, history: list) -> str:
        """构造一个包含function中信息的prompt来驱使大模型生成可能正确的function调用，利用history中的信息增强prompt中的引导

        Raises:
            Exception: If code generation fails or response format is invalid.
        """
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
7. 运行环境是纯 CPU 机器，**严禁使用 .cuda() / device='cuda' / GPU 相关操作**（会 AssertionError 崩溃）；
   只能用 CPU 张量/操作（如 torch 用默认 CPU 设备）

例子：
<function>
{{
    func_path: "a.b.c",
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

        if cfg.get("use_docs", True) == False:  # 显式 False 走简版签名 prompt（None/缺省 → 完整文档版）
            from inspect import _ParameterKind

            func_path = function.func_path
            func_args = function.args
            func_sig = f"def {func_path}("
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
5. 生成的`code`以"from {package_path} import {func_path}"开头
6. 生成的`code`中函数调用形式为"{func_path}(...)"
7. 运行环境是纯 CPU 机器，**严禁使用 .cuda() / device='cuda' / GPU 相关操作**（会 AssertionError 崩溃）；
   只能用 CPU 张量/操作（如 torch 用默认 CPU 设备）

例子：
<function>
{{
    func_path: "a.b.c",
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

        # Add a small retry loop for transient API errors
        last_exc = None
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=llm_cfg["model_name"],
                    messages=[
                        {
                            "role": "system",
                            "content": "你是一个代码生成助手，你的名字是attempter，擅长根据用户提供的信息信息生成函数调用。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=8000,
                    temperature=0.7,  # RQ1 F4：显式采样温度（pass@k 候选多样性源）
                    extra_body={
                        "chat_template_kwargs": {"enable_thinking": False},
                        "reasoning": {"enabled": False},
                    },
                )
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
                logger.debug(f"Attempter.generate attempt {attempt + 1} failed: {e}")
                # transient wait before retry
                time.sleep(1 + attempt)
                continue
        # after retries, raise a clear exception with traceback
        tb = traceback.format_exception_only(type(last_exc), last_exc)
        raise RuntimeError(f"生成函数调用时发生错误，最后一次错误: {''.join(tb)}")

def _run_data_root() -> Path:
    """子进程沙箱根目录（run_data/）：seed 相对路径产物统一落这里，不污染项目根。"""
    root = Path(os.environ.get("RESPFUZZER_RUN_DATA", "run_data")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root



def _kill_process_group(proc) -> None:
    """超时强制清理：SIGKILL 整个进程组（start_new_session 下含孙进程），再 reap 防僵尸。

    ``proc.kill()`` 只杀直接子进程——生成的代码里 Popen 派生的孙进程会逃逸并
    拖垮整 run（paddle ours 卡死根因）；按进程组整组强杀才是闭环超时控制。"""
    try:
        pgid = os.getpgid(proc.pid)
        if pgid and pgid > 1:
            os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


class QueitExecutor:
    def gen_code(self, code: str, full_name: str) -> str:
        """
        生成用于执行的完整代码，包含对目标函数调用的检查。

        例如，假设full_name是"a.b.c"，生成的代码结构如下：


        >>> with instrument_function_via_path_check_ctx("a.b.c") as f:
            code
            if not f.called:
                sys.exit(3)  # 未调用目标→rc=3（solve 反馈纠错）；已调用但抛领域异常→rc!=3（判覆盖）
        """
        # ident code with 4 spaces
        indented_code = "\n    ".join(code.split("\n"))
        res = f"""
import os, sys
# 剔脚本自身目录（tempfile /tmp）出 sys.path——防 /tmp 同名 .so 污染 import（numpy.so 教训）
sys.path = [p for p in sys.path if os.path.abspath(p) != os.path.dirname(os.path.abspath(__file__))]
from respfuzzer.lib.fuzz.instrument import instrument_function_via_path_check_ctx

with instrument_function_via_path_check_ctx("{full_name}") as f:
    {indented_code}
    if not f.called:
        sys.exit(3)  # 未调用目标：特殊退出码 3（solve 据此区分「没调用对」→反馈纠错；
                      #  已调用但抛领域异常的 rc!=3 → 与 ED-15 covered 口径对称，不再误丢）
"""
        return res

    def execute(self, code: str, full_name: str) -> dict:
        ret_code = 1
        stdout = ""
        stderr = ""
        proc = None
        result_type = (
            ExecutionResultType.CALLFAIL
        )  # 预初始化：任何路径都确定赋值，杜绝 possibly-unbound
        sandbox = Path(tempfile.mkdtemp(prefix="fz-", dir=_run_data_root()))  # 沙箱 cwd（run_data/）
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".py", delete=True) as f:
            f.write(self.gen_code(code, full_name))
            f.flush()
            command = [
                sys.executable,
                f.name,
            ]  # 用 venv 解释器（系统 python3 缺本项目依赖 → 首函数必败）
            with EXEC_LOCK:  # 子进程代码执行全局串行（LLM 请求并发不受影响）
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
                        cwd=str(sandbox),  # 沙箱 cwd：seed 相对路径产物不落项目根
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
                                _kill_process_group(proc)  # 整组强杀（防孙进程逃逸）
                                out, err = proc.communicate(timeout=1)
                                stdout = (stdout or "") + (out or "")
                                stderr = (stderr or "") + (err or "")
                        except Exception:
                            pass  # ignore further errors while cleaning up
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
                    shutil.rmtree(sandbox, ignore_errors=True)  # 沙箱产物即删
            result = {
                "result_type": result_type,
                "ret_code": ret_code,
                "stdout": stdout,
                "stderr": stderr,
            }
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
                    messages=[
                        {
                            "role": "system",
                            "content": "你是一个代码调试助手，擅长解释代码错误并提供修正建议。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=8000,
                    temperature=0.7,  # RQ1 F4
                    extra_body={
                        "chat_template_kwargs": {"enable_thinking": False},
                        "reasoning": {"enabled": False},
                    },
                )
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
                logger.debug(f"Reasoner.explain attempt {attempt + 1} failed: {e}")
                time.sleep(1 + attempt)
                continue
        tb = traceback.format_exception_only(type(last_exc), last_exc)
        raise RuntimeError(f"解释执行结果时发生错误，最后一次错误: {''.join(tb)}")


class Judger:
    """Use the LLM to judge whether a generated code string contains a valid call to the target function.

    The `judge` method returns a dict: {"valid": bool, "reason": str}.
    """

    def judge(self, code: str, function: Function) -> dict:
        package_path, func_name = function.func_path.rsplit(".", 1)
        prompt = (
            f"<function>\n{function.model_dump_json()}\n</function>\n"
            f"<code>\n{code}\n</code>\n"
            "请判断上面的 `code` 是否包含对 `function` 的有效调用（例如完整包路径的函数调用或显式的通过别名能够唯一映射到目标函数的调用）。"
            ' 输出应为 JSON 对象，形如 {"valid": true, "reason": "..."}。'
            f'合理的import形式为"from {package_path} import {func_name}"'
            f'合理的调用形式为"{func_name}(...) "'
        )

        last_exc = None
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=llm_cfg["model_name"],
                    messages=[
                        {
                            "role": "system",
                            "content": "你是一个代码审核助手，判断生成的代码是否包含对目标函数的有效调用。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=8000,
                    temperature=0.7,  # RQ1 F4
                    extra_body={
                        "chat_template_kwargs": {"enable_thinking": False},
                        "reasoning": {"enabled": False},
                    },
                )

                text = response.choices[0].message.content
                if text is None:
                    raise ValueError("模型返回空内容 (content is None)")
                text = text.strip()
                # try to extract json blob
                try:
                    # find first '{' and last '}' to extract JSON
                    start = text.find("{")
                    end = text.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        j = json.loads(text[start : end + 1])
                        return {
                            "valid": bool(j.get("valid")),
                            "reason": str(j.get("reason", "")),
                        }
                except Exception:
                    # fall through and attempt simple parsing
                    pass

                # fallback heuristics
                lowered = text.lower()
                if "true" in lowered or "yes" in lowered or "valid" in lowered:
                    return {"valid": True, "reason": text}
                else:
                    return {"valid": False, "reason": text}
            except Exception as e:
                last_exc = e
                logger.debug(f"Judger.judge attempt {attempt + 1} failed: {e}")
                time.sleep(1 + attempt)
                continue

        tb = traceback.format_exception_only(type(last_exc), last_exc)
        raise RuntimeError(f"判断代码调用有效性时发生错误，最后一次错误: {''.join(tb)}")


def solve(function: Function, budget: int = 10, covered_checker=None) -> Optional[str]:
    attempter = Attempter()
    judger = Judger()
    executor = QueitExecutor()
    reasoner = Reasoner()

    # 预算直接使用入参 budget（RQ1 F1：*对齐* 对照 pass@k 的 k，ED-4 等预算）
    history: List[dict] = []
    solved = False
    code = None
    while True:
        # generate may raise; handle and record the error into history so we can save it later
        try:
            code = attempter.generate(function, history)
        except Exception as e:
            err_msg = f"Attempter error: {str(e)}"
            logger.debug(err_msg)
            history.append({"role": "attempter_error", "content": err_msg})
            budget -= 1
            if budget <= 0:
                break
            continue

        history.append({"role": "attempter", "content": code})

        # judge may raise; handle and record the error into history so we can save it later
        try:
            judgment = judger.judge(code, function)
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

        result = executor.execute(code, function.func_path)

        # 成功判据与最终 covered 口径（RQ1Executor/ED-15）对称：
        # - OK：调用目标且无异常 → 直接 solved；
        # - ABNORMAL 且 ret_code!=3：已调用目标但抛异常。交给 covered_checker
        #   （RQ1Executor.covered / ED-15 oracle）精确判定：领域级=True→solved；
        #   构造级=False→继续 RCM 反馈环纠错（不提前停，保留纠错到无异常的机会）；
        #   covered_checker=None（legacy 直调）→ 保持 ABNORMAL 即 solved 的兼容语义；
        # - ret_code==3（未调用目标）/TIMEOUT/CALLFAIL：进入 RCM 反馈环纠错。
        _rt = result["result_type"]
        _rc = result.get("ret_code")
        if _rt == ExecutionResultType.OK or (
            _rt == ExecutionResultType.ABNORMAL
            and _rc != 3
            and (covered_checker is None or covered_checker(function, code))
        ):
            solved = True
            break
        else:
            if cfg.get("use_reasoner", True) == False:  # 显式 False 禁用 reasoner（缺省默认开）
                break

            try:
                reason = reasoner.explain(code, result)
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

    if solved:
        return code
    else:
        return None


def solve_function(function: Function) -> Optional[Seed]:
    logger.info(f"Try solving {function.func_path} ...")
    code = None
    try:
        code = solve(function)
        seed = Seed(
            func_id=function.id,
            library_name=function.library_name
            or "",  # Function.library_name 可空 → Seed 需 str
            func_path=function.func_path,
            args=function.args,
            function_call=code
            or "",  # None（solve 失败）→ 空串：Seed.function_call 为 str 类型
        )
        logger.success(f"Seed found for {function.func_path}:\n{code}")
        return seed
    except Exception as e:
        logger.info(f"Failed to solve {function.func_path}:\n{str(e)}")


def solve_library_functions(library_name: str) -> None:
    """Load all functions of the given library from the database and attempt to generate seeds for them."""
    functions = get_functions(library_name)
    concurrency = int(cfg.get("concurrency", 4))

    seeds: List[Seed] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(solve_function, function) for function in functions]
        for future in as_completed(futures):
            seed = future.result()
            if seed is not None:
                seeds.append(seed)

    # Save all seeds to a JSON file
    if len(seeds) == 0:
        logger.info(f"No seeds generated for library {library_name}")
        return
    seeds_data = [seed.model_dump() for seed in seeds]
    output_file = DATA_DIR / f"{library_name}_seeds.json"
    with open(output_file, "w") as f:
        json.dump(seeds_data, f, indent=2)
