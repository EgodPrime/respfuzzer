#!/usr/bin/env python3
"""
Generate test cases with C/C++ source context in the prompt.
Reads from /home/yb/respfuzzer/output/resp/manifest.json and uses the pre-stored
source_file paths to inject C/C++ source into the LLM prompt.

Usage: uv run python scripts/generate_test_cases_with_source.py
"""

import concurrent.futures
import json
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from respfuzzer.lib.library_visitor import LibraryVisitor
from respfuzzer.models import Function, ExecutionResultType
from respfuzzer.utils.config import get_config
from loguru import logger

logger.level("INFO")

llm_cfg = get_config("llm")
cfg = get_config("reflective_seeder")

import openai

client = openai.Client(api_key=llm_cfg["api_key"], base_url=llm_cfg["base_url"])

# Libraries with their GitHub repos
LIBRARIES = {
    "numpy": "https://github.com/numpy/numpy.git",
    "pandas": "https://github.com/pandas-dev/pandas.git",
    "scipy": "https://github.com/scipy/scipy.git",
    "torch": "https://github.com/pytorch/pytorch.git",
    "paddle": "https://github.com/PaddlePaddle/Paddle.git",
}

# Cache for source directories
SOURCE_DIRS = {}


def get_source_dir(library_name: str) -> Path | None:
    """Get or clone library source code to local cache."""
    if library_name in SOURCE_DIRS:
        return SOURCE_DIRS[library_name]

    source_dir = Path.home() / ".cache" / f"{library_name}_source"
    SOURCE_DIRS[library_name] = source_dir

    if source_dir.exists():
        logger.info(f"Using cached {library_name} source at {source_dir}")
        return source_dir

    if library_name not in LIBRARIES:
        logger.error(f"No repository URL for {library_name}")
        return None

    logger.info(f"Cloning {library_name} source to {source_dir}...")
    source_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", LIBRARIES[library_name], str(source_dir)],
            check=True,
            capture_output=True,
        )
        logger.info(f"Successfully cloned {library_name} source")
        return source_dir
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to clone {library_name}: {e}")
        return None


def read_c_source(source_file: str, source_dir: Path) -> str | None:
    """Read C/C++ source content from the pre-stored source file path."""
    if not source_file or not source_dir:
        return None
    full_path = source_dir / source_file
    if full_path.exists():
        try:
            content = full_path.read_text(errors='ignore')
            return f"File: {source_file}\n\n{content[:10000]}"
        except Exception:
            pass
    return None


class SourceAttempter:
    """Attempter that includes C/C++ source code in the prompt."""

    def generate(self, function: Function, history: list, c_source: str | None = None) -> str:
        package_path, func_name = function.func_name.rsplit(".", 1)

        if c_source:
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
<C source code>
{c_source}
</C source code>
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
<C source code>
{c_source}
</C source code>"""
        else:
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
</history>"""

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
                    max_tokens=500,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
                code = response.choices[0].message.content.strip()
                if "<code>" in code and "</code>" in code:
                    return code.split("<code>")[1].split("</code>")[0]
                if "```" in code:
                    parts = code.split("```")
                    if len(parts) >= 3:
                        return parts[1].strip()
                    elif len(parts) >= 2:
                        return parts[1].strip()
                raise ValueError("模型返回不包含 <code> 或 ``` 包裹的代码段")
            except Exception as e:
                last_exc = e
                logger.debug(f"SourceAttempter.generate attempt {attempt+1} failed: {e}")
                time.sleep(1 + attempt)
                continue
        tb = traceback.format_exception_only(type(last_exc), last_exc)
        raise Exception(f"生成函数调用时发生错误，最后一次错误: {''.join(tb)}")


class SourceJudger:
    """Judger that checks if generated code contains valid function call."""

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
                    messages=[
                        {
                            "role": "system",
                            "content": "你是一个代码审核助手，判断生成的代码是否包含对目标函数的有效调用。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=200,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )

                text = response.choices[0].message.content.strip()
                try:
                    start = text.find("{")
                    end = text.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        j = json.loads(text[start : end + 1])
                        return {
                            "valid": bool(j.get("valid")),
                            "reason": str(j.get("reason", "")),
                        }
                except Exception:
                    pass

                lowered = text.lower()
                if "true" in lowered or "yes" in lowered or "valid" in lowered:
                    return {"valid": True, "reason": text}
                else:
                    return {"valid": False, "reason": text}
            except Exception as e:
                last_exc = e
                logger.debug(f"Judger attempt {attempt+1} failed: {e}")
                time.sleep(1 + attempt)
                continue
        tb = traceback.format_exception_only(type(last_exc), last_exc)
        raise Exception(f"判断代码调用有效性时发生错误，最后一次错误: {''.join(tb)}")


class SourceExecutor:
    """Executor that runs generated code and checks for valid function call."""

    def execute(self, code: str, full_name: str) -> dict:
        import tempfile
        import subprocess
        result_type = ExecutionResultType.CALLFAIL
        ret_code = 1
        stdout = ""
        stderr = ""
        proc = None

        def gen_code(c, fn):
            indented = "\n    ".join(c.split("\n"))
            return f"""
from respfuzzer.lib.fuzz.instrument import instrument_function_via_path_check_ctx

with instrument_function_via_path_check_ctx("{fn}") as f:
    {indented}
    if not f.called:
        raise Exception(f"未包含对{full_name}的有效调用")
"""

        with tempfile.NamedTemporaryFile(mode="w+", suffix=".py", delete=True) as f:
            f.write(gen_code(code, full_name))
            f.flush()
            command = ["python", f.name]
            try:
                proc = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.PIPE,
                    text=True,
                    shell=False,
                    start_new_session=True,
                )
                try:
                    stdout, stderr = proc.communicate(input="\n" * 24, timeout=10)
                    ret_code = proc.returncode
                    if ret_code != 0:
                        result_type = ExecutionResultType.ABNORMAL
                    else:
                        result_type = ExecutionResultType.OK
                except subprocess.TimeoutExpired as e:
                    result_type = ExecutionResultType.TIMEOUT
                    try:
                        if proc is not None:
                            proc.kill()
                            out, err = proc.communicate(timeout=1)
                            stdout = (stdout or "") + (out or "")
                            stderr = (stderr or "") + (err or "")
                    except Exception:
                        pass
                    stderr = (stderr or "") + f"\nTimeoutExpired: {str(e)}"
                    ret_code = 124
            except Exception as e:
                result_type = ExecutionResultType.CALLFAIL
                stderr = (stderr or "") + f"\nException when starting subprocess: {str(e)}\n"
            finally:
                return {
                    "result_type": result_type,
                    "ret_code": ret_code,
                    "stdout": stdout,
                    "stderr": stderr,
                }


class SourceReasoner:
    """Reasoner that explains execution errors."""

    def explain(self, code: str, result: dict) -> str:
        prompt = f"""<code>\n{code}\n</code>\n<result>\n{result["stderr"]}\n</result>\n`code`中的代码在执行后得到报错`result`，请对这一执行结果进行解释，以指导代码编写人员进行修正指导。输出结果应为一段话，用<explain></explain>包裹。如果缺少文件，则提示Attempter通过open创建相应的临时文件。如果是参数错误，则提示Attempter调整参数的创建和初始化过程。请确保解释内容具体且有针对性。"""
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
                    max_tokens=500,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
                explanation = response.choices[0].message.content.strip()
                if "<explain>" in explanation and "</explain>" in explanation:
                    return explanation.split("<explain>")[1].split("</explain>")[0]
                if explanation:
                    return explanation
                raise ValueError("模型返回空的解释内容")
            except Exception as e:
                last_exc = e
                logger.debug(f"Reasoner attempt {attempt+1} failed: {e}")
                time.sleep(1 + attempt)
                continue
        tb = traceback.format_exception_only(type(last_exc), last_exc)
        raise Exception(f"解释执行结果时发生错误，最后一次错误: {''.join(tb)}")


def solve_with_source(function: Function, c_source: str | None = None) -> str | None:
    """Solve function with C/C++ source context in the prompt."""
    attempter = SourceAttempter()
    judger = SourceJudger()
    executor = SourceExecutor()
    reasoner = SourceReasoner()

    budget = 10
    history: list = []
    solved = False
    code = None

    while True:
        try:
            code = attempter.generate(function, history, c_source)
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
            judgment = judger.judge(code, function)
            if judgment["valid"]:
                logger.debug(f"Judger accepted the code:\n{code}")
            else:
                logger.debug(f"Judger rejected the code: {judgment['reason']}")
                history.append(
                    {"role": "judger", "content": f"Judger rejected the code: {judgment['reason']}"}
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

    if solved:
        return code
    else:
        return None


def process_function(item: dict, source_dirs: dict, output_dir: Path) -> dict:
    """Process a single function from the manifest."""
    func_name = item["func_name"]
    library_name = item["library_name"]
    source_file = item.get("source_file")
    func_name_short = func_name.split(".")[-1]
    filename = f"{library_name}_{func_name_short}.py"
    filepath = output_dir / filename

    logger.info(f"Try solving {func_name} ...")

    # Get C source content
    c_source = None
    source_dir = source_dirs.get(library_name)
    if source_file and source_dir:
        c_source = read_c_source(source_file, source_dir)

    # Create a minimal Function object for solve_with_source
    from respfuzzer.models import Function, Argument

    args_list = item.get("args", [])
    # If args is empty list or not present, create empty Argument list
    if not args_list:
        args_list = []

    func = Function(
        func_name=func_name,
        library_name=library_name,
        source=item.get("source_file", "unknown"),
        args=args_list,
    )

    code = None
    try:
        code = solve_with_source(func, c_source)
    except Exception as e:
        logger.info(f"Failed to solve {func_name}: {e}")

    result = {
        "library_name": library_name,
        "func_name": func_name,
        "success": code is not None,
        "source_file": source_file,
        "output_file": str(filepath),
    }

    if code:
        with open(filepath, "w") as f:
            f.write(code)
        logger.info(f"Seed found for {func_name}:\n{code}")
        result["code"] = code
    else:
        logger.info(f"Failed to solve {func_name}")
        result["code"] = None

    return result


def main():
    manifest_path = Path(__file__).parent / "manifest.json"

    if not manifest_path.exists():
        print(f"Error: {manifest_path} not found")
        sys.exit(1)

    with open(manifest_path, "r") as f:
        manifest = json.load(f)

    output_dir = Path(__file__).parent / "output" / "respfuzz_with_source"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Processing {len(manifest)} functions from manifest...")

    # Prepare source directories for all libraries
    print("Preparing source code for all libraries...")
    source_dirs = {}
    for library_name in LIBRARIES.keys():
        source_dirs[library_name] = get_source_dir(library_name)

    print(f"\nGenerating test cases with C/C++ source in prompt...")

    results = []
    for i, item in enumerate(manifest):
        func_name = item["func_name"]
        library_name = item["library_name"]
        source_file = item.get("source_file")

        print(f"  [{i+1}/{len(manifest)}] {func_name}", end=" ... ")

        if source_file and library_name in source_dirs and source_dirs[library_name]:
            print(f"[has C source] ", end="")
        else:
            print(f"[no C source] ", end="")

        result = process_function(item, source_dirs, output_dir)

        status = "OK" if result["success"] else "FAILED"
        print(status)
        results.append(result)

    # Summary
    success_count = sum(1 for r in results if r["success"])
    has_source_count = sum(1 for r in results if r.get("source_file"))
    print(f"\n{'='*60}")
    print(f"Summary: {success_count}/{len(results)} test cases generated successfully")
    print(f"         {has_source_count} functions had C/C++ source code")
    print(f"Output directory: {output_dir}")

    # Write manifest
    manifest_out_path = output_dir / "manifest.json"
    with open(manifest_out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Manifest: {manifest_out_path}")


if __name__ == "__main__":
    main()