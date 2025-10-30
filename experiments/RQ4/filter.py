"""
## 该脚本的功能 

利用LLM对漏洞进行过滤，排除掉所有的假阳性。
LLM的判断依据包含：
  - POC代码
  - stderr描述
  - 目标函数的源码（最好能够追踪相关代码，目前做不到）

## 该脚本的大致工作流程

1. 读取 replay_results.json 文件，获取所有的漏洞复现结果列表。该列表的每一项都包含`seed_id`、`random_state`、`params_snapshot`（变异后函数调用参数的内存快照）、`result`（stderr描述）三个字段。
2. 通过`get_seed`获取每个漏洞对应的`seed`对象，通过`seed.function_call`获取变异前的函数调用代码`function_call`。
3. 构造 LLM 的输入提示（prompt），包括 `function_call`、`params_snapshot`和任务描述（复原出变异后的函数调用代码）。
4. 调用 LLM 接口，获取模型对每个漏洞的变异后函数调用代码的复原结果`poc`。
5. 通过`seed.func_name`获取函数的名称，然后通过 `get_function` 获取目标`function`对象，再通过`function.source`获取目标函数的源码`func_source`。
6. 构造 LLM 的输入提示（prompt），包括 POC 代码、stderr描述和目标函数源码`func_source`，以及任务描述（判断该stderr描述是否意味着一个崩溃或者潜在的安全漏洞）。
7. 调用 LLM 接口，获取模型对每个漏洞的判断结果（是否为真实漏洞）和判断的推理过程。
8. 根据模型的判断结果，过滤掉假阳性漏洞，只保留真实漏洞。（这里如何尽可能减少LLM误判带来的损失呢？）
9. 过滤后的漏洞结果应该包含`seed_id`、`random_state`、`result`（stderr描述）、`func_source`（目标函数源码）、`poc`（POC代码）等字段。
10. 将过滤后的漏洞结果保存到 filtered_replay_results.json 文件中。

## 实现架构

- judge_is_vulnerable(func_source: str, poc: str, stderr: str) -> bool
- batch_judge(func_sources: list[str], pocs: list[str], stderrs: list[str], max_workers: int) -> list[bool]
- load_replay_results(path: str) -> list[dict[str, int|str]]
- reproduce_poc(seed: Seed, params_snapshot: str) -> str # 其实应该在replay的过程中就顺便完成这个工作
- main() -> None
"""

from loguru import logger
import openai
import json
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from tracefuzz.repos.function_table import get_function
from tracefuzz.repos.seed_table import get_seed

client = openai.OpenAI(base_url="http://192.168.2.29:8023", api_key="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
model_name = "qwen3-30b-a3b"

def judge_is_vulnerable(description: str) -> bool:
    prompt = f"""
## 任务
请判断以下Python程序的错误描述(stderr)是否意味着一个崩溃或者潜在的安全漏洞。如果是，请回答“是”，否则回答“否”。
## 注意
- 需要区分Python解释器报错和程序本身的崩溃。仅当错误描述表明程序崩溃或存在安全漏洞时，才回答“是”。
- 超时可以视为潜在的安全漏洞，应回答“是”。
- 如果错误描述仅包含Python异常（如“IndexError”、“TypeError”等），通常不表示程序崩溃，应回答“否”。
- 如果错误描述看起来像是C语言级别或者linux内核级别的错误，应回答“是”。
- 如果错误描述不明确，无法判断是否为crash或安全漏洞，应回答“否”。
"""
    prompt += f"\n错误描述：{description}\n你的回答："

    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    answer = response.choices[0].message.content.strip()

    logger.debug(f"对于描述：{description}，模型回答：{answer}")
    return answer == "是"

def batch_judge(descriptions: list[str]) -> list[bool]:
    results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(judge_is_vulnerable, desc) for desc in descriptions]
        for future in futures:
            results.append(future.result())
    return results

if __name__ == '__main__':
    replay_results: dict[str,list[dict[str,int|str]]] = json.load(open('replay_results.json', 'r', encoding='utf-8'))
    filtered_results: dict[str,list[dict]] = {}
    for library_name, results in replay_results.items():
        descriptions = [res['result'] for res in results]
        judgments = batch_judge(descriptions)
        filtered_results[library_name] = []
        for res, is_vuln in zip(results, judgments):
            if is_vuln:
                new_res = deepcopy(res)
                new_res["validated"] = False
                filtered_results[library_name].append(new_res)

    with open('filtered_replay_results.json', 'w', encoding='utf-8') as f:
        json.dump(filtered_results, f, indent=2, ensure_ascii=False)