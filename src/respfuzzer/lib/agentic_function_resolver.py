import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

import openai
from loguru import logger

from respfuzzer.models import ExecutionResultType, Function, Seed
from respfuzzer.repos import get_functions
from respfuzzer.utils.config import get_config
from respfuzzer.utils.experiment_recorder import record_event, record_result, start_run
from respfuzzer.utils.paths import DATA_DIR, PROJECT_DIR

#: RQ2 的 .log / *_checkpoint.json 落盘目录（experiments/RQ2/）
RQ2_DIR = PROJECT_DIR / "experiments" / "RQ2"

#: 种子执行沙箱根（``run_data/``，可用 ``RESPFUZZER_RUN_DATA`` 覆盖）。
#: QueitExecutor 的子进程 cwd 指向其中的一次性目录，seed 的相对路径产物
#: 不再污染项目根（numpy.so / paddle/ 遮蔽真实包的教训，随 RQ1 引擎移植）。
def _run_data_root() -> Path:
    root = Path(os.environ.get("RESPFUZZER_RUN_DATA", "run_data"))
    root.mkdir(parents=True, exist_ok=True)
    return root

cfg = get_config("reflective_seeder")
llm_cfg = get_config("llm")

client = openai.Client(api_key=llm_cfg["api_key"], base_url=llm_cfg["base_url"])


class Attempter:
    def generate(self, function: Function, history: list) -> str:
        """构造一个包含function中信息的prompt来驱使大模型生成可能正确的function调用，利用history中的信息增强prompt中的引导

        Raises:
            Exception: If code generation fails or response format is invalid.
        """
        package_path, func_name = function.func_name.rsplit(".", 1)
        prompt = f"""任务:
请根据`function`和`history`中的信息来为{function.func_name}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。

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

        if cfg.get("use_docs", True) is False:
            from inspect import _ParameterKind

            func_name = function.func_name
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
请根据`function`和`history`中的信息来为{function.func_name}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。

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

        # Add a small retry loop for transient API errors
        last_exc = None
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=llm_cfg["model_name"],
                    temperature=0.7,  # RQ1/RSG 解码设置统一 0.7（与 CountingLLM 一致）
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
        # （避免在项目根写出 numpy.so / paddle/ 等遮蔽真实包）
        sandbox = Path(tempfile.mkdtemp(prefix="fz-", dir=_run_data_root()))
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".py", delete=True) as f:
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
                            proc.kill()
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
    """Use the LLM to judge whether a generated code string contains a valid call to the target function.

    The `judge` method returns a dict: {"valid": bool, "reason": str}.
    """

    def judge(self, code: str, function: Function) -> dict:
        package_path, func_name = function.func_name.rsplit(".", 1)
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
                    temperature=0.7,  # RQ1/RSG 解码设置统一 0.7（与 CountingLLM 一致）
                    messages=[
                        {
                            "role": "system",
                            "content": "你是一个代码审核助手，判断生成的代码是否包含对目标函数的有效调用。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=8000,
                    extra_body={
                        "chat_template_kwargs": {"enable_thinking": False},
                        "reasoning": {"enabled": False}
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
                logger.debug(f"Judger.judge attempt {attempt+1} failed: {e}")
                time.sleep(1 + attempt)
                continue

        tb = traceback.format_exception_only(type(last_exc), last_exc)
        raise Exception(f"判断代码调用有效性时发生错误，最后一次错误: {''.join(tb)}")


def solve(
    function: Function, budget: int = 10, stats: dict | None = None
) -> Optional[str]:
    """反射式种子生成主循环（SCE + RCM 自省）。

    Args:
        function: 目标函数。
        budget: 自省预算——每轮失败扣 1，扣到 0 停（默认 10 保持历史行为）。
        stats: 可选出参，回填本次自省的计量：``rounds``（实际自省轮次）、
            ``budget``（给定预算）、``budget_left``、``solved``、``exhausted``
            （是否耗尽预算）。RQ1 用它把「ours 成功用了几轮 / 是否耗尽」记账，
            从而驱动对照组的尝试轮次【MC6-5】——**单位是轮次，不是 LLM 调用次数**。

    Returns:
        成功的代码字符串；失败返回 ``None``。
    """
    attempter = Attempter()
    judger = Judger()
    executor = QueitExecutor()
    reasoner = Reasoner()
    initial_budget = budget
    rounds = 0

    history: List[dict] = []
    solved = False
    code = None
    while True:
        rounds += 1  # 一轮自省 = attempter → judger → executor(+reasoner) 一个来回
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

        result = executor.execute(code, function.func_name)

        if result["result_type"] == ExecutionResultType.OK:
            solved = True
            break
        else:
            if cfg.get("use_reasoner", True) is False:
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

    if stats is not None:
        stats.update(
            {
                "rounds": rounds,
                "budget": initial_budget,
                "budget_left": budget,
                "solved": solved,
                "exhausted": budget <= 0,  # 耗尽预算 → 对照组按约定拿满预算轮次【MC6-5】
            }
        )

    if solved:
        return code
    else:
        return None


def solve_function(function: Function) -> Optional[Seed]:
    logger.info(f"Try solving {function.func_name} ...")
    code = None
    try:
        code = solve(function)
        seed = Seed(
            func_id=function.id,
            library_name=function.library_name,
            func_name=function.func_name,
            args=function.args,
            function_call=code,
        )
        logger.success(f"Seed found for {function.func_name}:\n{code}")
        record_event(
            "seed_generated",
            func_name=function.func_name,
            library_name=function.library_name,
            seed_id=seed.id,
        )
        return seed
    except Exception as e:
        logger.info(f"Failed to solve {function.func_name}:\n{str(e)}")
        record_event(
            "seed_failed",
            func_name=function.func_name,
            library_name=function.library_name,
            error=repr(e),
        )


def _rq2_group() -> str:
    """按 reflective_seeder 配置推导 RQ2 实验组（见 ``experiments/RQ2/exp.md``）。

    配置项与实验组的对应：docs = SCE、reasoner = RCM；两者都开是 Full 配置（文件名里
    记作 ``respfuzzer``），只开 docs 是 ``sce-only``，只开 reasoner 是 ``rcm-only``，
    都关是 ``baseline``。
    """
    use_docs = bool(cfg.get("use_docs", False))
    use_reasoner = bool(cfg.get("use_reasoner", False))
    if use_docs and use_reasoner:
        return "respfuzzer"
    if use_docs:
        return "sce-only"
    if use_reasoner:
        return "rcm-only"
    return "baseline"


def solve_library_functions(library_name: str) -> None:
    """Load all functions of the given library from the database and attempt to generate seeds for them."""
    with start_run(
        "RQ2",
        group=_rq2_group(),
        library=library_name,
        out_dir=RQ2_DIR,
        metadata={
            "entry": "respfuzzer.cmd.reflective_seeder:generate_seeds",
            "library_name": str(library_name),
            "use_docs": bool(cfg.get("use_docs", False)),
            "use_reasoner": bool(cfg.get("use_reasoner", False)),
            "concurrency": int(cfg.get("concurrency", 4)),
        },
    ):
        functions = get_functions(library_name)
        concurrency = int(cfg.get("concurrency", 4))
        record_result("function_count", len(functions))

        seeds: List[Seed] = []
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(solve_function, function) for function in functions
            ]
            for future in as_completed(futures):
                seed = future.result()
                if seed is not None:
                    seeds.append(seed)

        # Save all seeds to a JSON file
        record_result("seed_count", len(seeds))
        if len(seeds) == 0:
            logger.info(f"No seeds generated for library {library_name}")
            return
        seeds_data = [seed.model_dump() for seed in seeds]
        output_file = DATA_DIR / f"{library_name}_seeds.json"
        with open(output_file, "w") as f:
            json.dump(seeds_data, f, indent=2)
        record_result("output_file", str(output_file))
