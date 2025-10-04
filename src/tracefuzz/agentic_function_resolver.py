import concurrent.futures
from datetime import datetime
import subprocess
import tempfile
from typing import Optional

import fire
import openai
from loguru import logger

from tracefuzz.db.function_table import get_functions
from tracefuzz.db.seed_table import create_seed
from tracefuzz.db.solve_history_table import create_solve_history
from tracefuzz.models import Function, ExecutionResultType, Seed
from tracefuzz.utils.config import get_config

cfg = get_config("reflective_seeder")

client = openai.Client(api_key=cfg["api_key"], base_url=cfg["base_url"])


class Attempter:
    def generate(self, function: Function, history: list) -> str:
        """构造一个包含function中信息的prompt来驱使大模型生成可能正确的function调用，利用history中的信息增强prompt中的引导"""
        prompt = f"<function>\n{function.model_dump_json()}\n</function>\n<history>\n{history}\n</history>请根据`function`和`history`中的信息来为{function.func_name}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。注意：1. 你生成的代码应该用<code></code>包裹。2. 不要生成``` 3. 不要生成`code`以外的任何内容 4. 函数调用应该是完成包路径调用，例如import a; a.b.c()，而不能是from a.b import c; c()"
        try:
            response = client.chat.completions.create(
                model=cfg["model_name"],
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
            if not code.startswith("<code>"):
                raise Exception("Prefix missing")
            if not code.endswith("</code>"):
                raise Exception("Suffix missing")

            history.append({"role": "attempter", "content": code})

            return code.split("<code>")[1].split("</code>")[0]
        except Exception as e:
            raise Exception(f"生成函数调用时发生错误：{str(e)}")


class QueitExecutor:
    def execute(self, code: str) -> dict:
        ret_code = 0
        stdout = ""
        stderr = ""
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".py", delete=True) as f:
            f.write(code)
            f.flush()
            command = ["python", f.name]
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
                )

                # 读取输出（捕获所有）
                stdout, stderr = proc.communicate(input="\n" * 24, timeout=10)  # 10秒超时
                ret_code = proc.returncode
                if ret_code != 0:
                    result_type = ExecutionResultType.ABNORMAL
                else:
                    result_type = ExecutionResultType.OK
            except subprocess.TimeoutExpired as e:
                result_type = ExecutionResultType.TIMEOUT
                proc.kill()
                stderr = str(e)
                ret_code = 1
            except Exception as e:
                result_type = ExecutionResultType.CALLFAIL
                stderr = str(e)
                ret_code = 1
            finally:
                result = {
                    "result_type": result_type,
                    "ret_code": ret_code,
                    "stdout": stdout,
                    "stderr": stderr,
                }
            return result


class Reasoner:
    def explain(self, code: str, result: dict) -> str:
        prompt = f"""<code>\n{code}\n</code>\n<result>\n{result["stderr"]}\n</result>\n`code`中的代码在执行后得到报错`result`，请对这一执行结果进行解释，以指导代码编写人员进行修正指导。输出结果应为一段话，用<explain></explain>包裹。"""
        try:
            # 调用模型进行推理
            response = client.chat.completions.create(
                model=cfg["model_name"],
                messages=[
                    {"role": "system", "content": "你是一个代码调试助手，擅长解释代码错误并提供修正建议。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=500,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            # 提取模型返回的文本
            explanation = response.choices[0].message.content.strip()
            # 确保输出包含 <explain> 标签
            if not explanation.startswith("<explain>"):
                raise Exception("Prefix missing")
            if not explanation.endswith("</explain>"):
                raise Exception("Suffix missing")
            return explanation.split("<explain>")[1].split("</explain>")[0]
        except Exception as e:
            raise Exception(f"解释执行结果时发生错误：{str(e)}")


def solve(function: Function) -> Optional[str]:
    attempter = Attempter()
    executor = QueitExecutor()
    reasoner = Reasoner()

    budget = 10
    history = []
    solved = False
    while True:
        code = attempter.generate(function, history)
        if f"{function.func_name}(" not in code:
            history.append({"role": "reasoner", "content": f"生成的代码中不包含对{function.func_name}的调用，请重新生成"})
            budget -= 1
            if budget == 0:
                break
            continue
        # logger.debug(f"code:\n{code}")
        result = executor.execute(code)
        # logger.debug(f"result:\n{result}")
        if result["result_type"] == ExecutionResultType.OK:
            solved = True
            break
        else:
            reason = reasoner.explain(code, result)
            logger.debug(f"reason:\n{reason}")
            history.append({"role": "attempter", "content": code})
            history.append({"role": "executor", "content": result["stderr"]})
            history.append({"role": "reasoner", "content": reason})
            budget -= 1
            if budget == 0:
                break
            continue

    create_solve_history(function, history)
    if solved:
        return code
    else:
        return None
    
    


def _main(library_name: str):
    functions = get_functions(library_name)
    for function in functions:
        logger.info(f"Try solving {function.func_name} ...")
        code = None
        try:
            code = solve(function)
        except Exception:
            pass
        if code:
            seed = Seed(
                func_id=function.id,
                library_name=function.library_name,
                func_name=function.func_name,
                args=function.args,
                function_call=code
            )
            create_seed(seed)
            logger.info(f"Seed find for {function.func_name}:\n{code}")
        else:
            logger.info(f"Failed to solve {function.func_name}")


def _mainC(library_name: str):
    functions = get_functions(library_name)

    def process_function(function: Function):
        logger.info(f"Try solving {function.func_name} ...")
        code = None
        try:
            code = solve(function)
        except Exception:
            pass
        if code:
            seed = Seed(
                func_id=function.id,
                library_name=function.library_name,
                func_name=function.func_name,
                args=function.args,
                function_call=code
            )
            create_seed(seed)
            logger.info(f"Seed found for {function.func_name}:\n{code}")
        else:
            logger.info(f"Failed to solve {function.func_name}")

    # 使用线程池，最多10个线程并发执行
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(process_function, function) for function in functions]
        concurrent.futures.wait(futures)


def main():
    fire.Fire(_mainC)


if __name__ == "__main__":
    main()
