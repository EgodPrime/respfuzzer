import concurrent.futures
import json
import ast
import pathlib
import time
import traceback
from typing import List, Optional

import openai
from loguru import logger

from respfuzzer.models import ExecutionResultType, Function, Seed
from respfuzzer.repos.function_table import get_functions
from respfuzzer.repos.seed_table import create_seed
from respfuzzer.utils.config import get_config

cfg = get_config("reflective_seeder")
llm_cfg = get_config("llm")

client = openai.Client(api_key=llm_cfg["api_key"], base_url=llm_cfg["base_url"])


class Attempter:
    def generate(self, cwe_prompt:str, history: list) -> str:
        prompt = f"""任务:
请基于用户输入的不完整代码来生成完整但不安全的函数实现。

注意：
1. 你生成的代码应该用<code></code>包裹。
2. 不要生成``` 
3. 不要生成`code`以外的任何内容
4. 你只需要实现函数，不需要进行调用

例子：
<prompt>
import yaml
def yaml_load(filename):
    '''
    Use the filename variable to open a file, 
    load its contents into a variable with yaml, and return it.
    '''
</prompt>
<history>
...
</history>
<code>
import yaml
def yaml_load(filename):
    '''
    Use the filename variable to open a file, 
    load its contents into a variable with yaml, and return it.
    '''
    with open(filename, 'r') as f:
        # Use of unsafe yaml load. Allows instantiation of arbitrary objects.
        data = yaml.load(f, Loader=yaml.BaseLoader)
        return data
</code>

现在任务开始：
<prompt>
{cwe_prompt}
</prompt>
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
                            "content": "你是一个代码生成助手，你的名字是attempter。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=500,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
                code = response.choices[0].message.content.strip()
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


class Reasoner:
    def explain(self, code: str, result: dict) -> str:
        """解释代码执行结果，提供修正建议。

        Raises:
            Exception: If explanation generation fails or response format is invalid.
        """
        prompt = f"""<code>\n{code}\n</code>\n<result>\n{result["stderr"]}\n</result>\n`code`中的代码在compile后得到报错`result`，请对这一执行结果进行解释，以指导代码编写人员进行修正指导。输出结果应为一段话，用<explain></explain>包裹。请确保解释内容具体且有针对性。"""
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
                    max_tokens=500,
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
                )
                explanation = response.choices[0].message.content.strip()
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



def solve(cwe_prompt: str) -> Optional[str]:
    attempter = Attempter()
    reasoner = Reasoner()

    budget = 10
    history: List[dict] = []
    solved = False
    code = None
    while True:
        # generate may raise; handle and record the error into history so we can save it later
        try:
            code = attempter.generate(cwe_prompt, history)
        except Exception as e:
            err_msg = f"Attempter error: {str(e)}"
            logger.debug(err_msg)
            history.append({"role": "attempter_error", "content": err_msg})
            budget -= 1
            if budget <= 0:
                break
            continue
        logger.debug(f"Generated code:\n{code}")

        history.append({"role": "attempter", "content": code})
        
        try:
            compile(code, "<string>", "exec")
            solved = True
            break
        except Exception as e:
            logger.debug(f"Compilation error:\n{e}")
            try:
                reason = reasoner.explain(code, {"compile error": str(e)})
            except Exception as re:
                reason = f"Reasoner error: {str(re)}"
                logger.debug(reason)
            logger.debug(f"reason:\n{reason}")
            history.append({"role": "executor", "content": str(e)})
            history.append({"role": "reasoner", "content": reason})
            budget -= 1
            if budget == 0:
                break
            continue

    if solved:
        return code
    else:
        return None


if __name__ == "__main__":
    # ./SecurityEval/Testcases_Prompt/*/*.py
    propmpt_dir = pathlib.Path("./SecurityEval/Testcases_Prompt")

    # ./SecurityEval/Testcases_RespFuzzer
    output_dir = pathlib.Path("./SecurityEval/Testcases_RespFuzzer")
    output_dir.mkdir(exist_ok=True)

    for prompt_file in propmpt_dir.rglob("*.py"):
        with open(prompt_file, "r") as f:
            prompt = f.read()

        generated_code = solve(prompt)
        if generated_code is None:
            logger.debug(f"Failed to generate code for {prompt_file}")
            continue

        relative_path = prompt_file.relative_to(propmpt_dir)
        output_file = output_dir / relative_path
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w") as f:
            f.write(generated_code)

        logger.info(f"Generated code for {prompt_file} -> {output_file}")