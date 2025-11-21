"""
## 该脚本的功能 

利用LLM对漏洞进行过滤，排除掉所有的假阳性。
LLM的判断依据包含：
  - POC代码
  - stderr描述
  - 目标函数的源码（最好能够追踪相关代码，目前做不到）

## 该脚本的大致工作流程

1. 读取 replay_results.json 文件，获取所有的漏洞复现结果列表。该列表的每一项都包含`seed_id`、`random_state`、`result`（stderr描述）和`poc`（POC代码）等字段。
2. 通过`get_seed`获取每个漏洞对应的`seed`对象。
3. 通过`seed.func_name`获取函数的名称，然后通过 `get_function` 获取目标`function`对象，再通过`function.source`获取目标函数的源码`func_source`。
4. 构造 LLM 的输入提示（prompt），包括 POC 代码、stderr描述和目标函数源码`func_source`，以及任务描述（判断该stderr描述是否意味着一个崩溃或者潜在的安全漏洞）。
5. 调用 LLM 接口，获取模型对每个漏洞的判断结果（是否为真实漏洞）、风险等级和判断的推理过程。
6. 根据模型的判断结果，过滤掉假阳性漏洞，只保留真实漏洞。（这里如何尽可能减少LLM误判带来的损失呢？）
7. 过滤后的漏洞结果应该包含`seed_id`、`random_state`、`result`（stderr描述）、`func_source`（目标函数源码）、`poc`（POC代码）、`risk_level`（风险等级）和`reasoning`（模型的推理过程）等字段。
8. 将过滤后的漏洞结果保存到 filtered_replay_results.json 文件中。

## 实现架构

- judge_is_vulnerable(func_source: str, poc: str, stderr: str) -> tuple[bool, int, str]
- batch_judge(func_sources: list[str], pocs: list[str], stderrs: list[str], max_workers: int) -> list[tuple[bool, int, str]]
- load_replay_results(path: str) -> list[dict[str, int|str]]
- main() -> None
"""

from loguru import logger
import openai
import json
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from respfuzzer.repos.function_table import get_function
import re
from respfuzzer.repos.seed_table import get_seed

client = openai.OpenAI(base_url="http://192.168.2.29:8023", api_key="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
model_name = "qwen3-30b-a3b"

def chat_completion(prompt: str) -> str:
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": "You are a helpful assistant that helps people find bugs in code."},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=2048,
    )
    return response.choices[0].message.content

def judge_is_vulnerable(func_source: str, poc: str, stderr: str) -> bool:
    prompt = f"""## Task
你是一个资深的安全研究员，专门负责分析漏洞复现结果。
你需要根据`source`, `poc`和`stderr`判断该复现结果是否意味着一个崩溃或者潜在的安全漏洞。

## 判断标准
- 如果错误描述中包含明显的崩溃信息（如 segmentation fault, buffer overflow, null pointer dereference 等），则判断为真实漏洞。
- 如果不包含明显的崩溃信息，则结合目标函数源码和POC代码进行分析，判断是否可能存在实现上的漏洞。
- 如果有明确的边界检查或者错误处理机制或者报错信息表明是用户使用错误（例如"the ... should ..."），则判断为否。
- 如果无法确定，则倾向于判断为否。

## 预期输出形式
<is_bug>True/False</is_bug>
<risk_level>0/1/2/3</risk_level>  # 0表示无风险（明显的用户使用错误），1表示缺少边界检查或者Docstring未做出明确边界约定等低风险漏洞，2表示即使用户小心使用也可能触发的漏洞，3表示明确存在崩溃、溢出、远程代码执行等高风险漏洞。
<reasoning>你的推理过程</reasoning>

## Examples

### Example 1: 无文档提示，也无边界检查， 判断为低风险漏洞
<source>
def copy_buffer(input):
    ''' Copies input buffer'''
    buffer = [0] * 10
    for i in range(len(input)):
        buffer[i] = input[i]
    return buffer
</source>
<poc>
copy_buffer([1,2,3,4,5,6,7,8,9,10,11,12])
</poc>
<stderr>
IndexError: list assignment index out of range
</stderr>
<is_bug>True</is_bug>
<risk_level>1</risk_level>
<reasoning>
虽然错误信息是 IndexError，但结合源码可以看出函数并没有进行边界检查，并且Docstring也没有明确说明输入长度限制。因此这是一个低风险漏洞，攻击者可以利用这个漏洞导致程序异常终止。
</reasoning>

### Example 2: 有文档提示，且有边界检查， 判断为非漏洞
<source>
def copy_buffer(input):
    ''' Copies input buffer of max length 10'''
    if len(input) > 10:
        raise ValueError("Input buffer too large")
    buffer = [0] * 10
    for i in range(len(input)):
        buffer[i] = input[i]
    return buffer
</source>
<poc>
copy_buffer([1,2,3,4,5,6,7,8,9,10,11,12])
</poc>
<stderr>
ValueError: Input buffer too large
</stderr>
<is_bug>False</is_bug>
<risk_level>0</risk_level>
<reasoning>
函数的Docstring明确说明了输入长度的限制，并且源码中也有相应的边界检查机制。因此这个错误是由于用户输入不符合要求引起的，不构成漏洞。
</reasoning>

### Example 3: 报错时有明确的用户使用错误提示， 判断为非漏洞
<source>
def divide(a, b):
    ''' Divides a by b'''
    if b == 0:
        raise ValueError("Denominator cannot be zero")
    return a / b
</source>
<poc>
divide(10, 0)
</poc>
<stderr>
ValueError: Denominator cannot be zero
</stderr>
<is_bug>False</is_bug>
<risk_level>0</risk_level>
<reasoning>
错误信息明确指出了用户的使用错误（分母不能为零），因此这不构成漏洞。
</reasoning>

### Example 4: 明显的崩溃， 判断为高风险漏洞
<source>
from _core import getx
def access_memory(index):
    ''' Accesses memory at given index'''
    buffer = [0] * 10
    return getx(buffer, index)
</source>
<poc>
access_memory(1000)
</poc>
<stderr>
Segmentation fault (core dumped)
</stderr>
<is_bug>True</is_bug>
<risk_level>3</risk_level>
<reasoning>
错误信息显示发生了段错误（Segmentation fault），这表明程序试图访问非法内存地址。结合源码可以看出函数没有进行边界检查，因此这是一个高风险漏洞。
</reasoning>

### Example 5: 文档中表明为安全使用，但实际实现中缺少边界检查， 用户使用时非常容易触发， 判断为中风险漏洞
<source>
def get_item(lst, index):
    ''' Gets item at index from list safely'''
    return lst[index]
</source>
<poc>
get_item([1,2,3], 10)
</poc>
<stderr>
IndexError: list index out of range
</stderr>
<is_bug>True</is_bug>
<risk_level>2</risk_level>
<reasoning>
虽然Docstring中表明该函数是安全使用的，但实际上函数并没有进行边界检查，用户在使用时非常容易触发 IndexError 异常。因此这是一个中风险漏洞。
</reasoning>

## Start
<source>
{func_source}
</source>
<poc>
{poc}
</poc>
<stderr>
{stderr}
</stderr>
"""
    for _ in range(10):
        response = chat_completion(prompt)
        re_str = re.compile(r"<is_bug>(True|False)</is_bug>.*<risk_level>(\d+)</risk_level>.*<reasoning>(.*?)</reasoning>", re.DOTALL)
        m = re_str.search(response)
        if m:
            is_bug = m.group(1) == "True"
            risk_level = int(m.group(2))
            reasoning = m.group(3).strip()
            logger.debug(f"{poc}\nIs Bug: {is_bug}\nRisk Level: {risk_level}\nReasoning: {reasoning}")
            return is_bug, risk_level, reasoning
        else:
            logger.warning(f"Failed to parse LLM response, retrying...\nResponse was:\n{response}")
    logger.error(f"Failed to parse LLM response after multiple attempts. Returning default values.")

    return False, 0, ""

def batch_judge(func_sources: list[str], pocs: list[str], stderrs: list[str], max_workers: int=4) -> list[tuple[bool, int, str]]:
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for func_source, poc, stderr in zip(func_sources, pocs, stderrs):
            futures.append(executor.submit(judge_is_vulnerable, func_source, poc, stderr))
        for future in futures:
            results.append(future.result())
    return results

def load_replay_results(path: str) -> dict[str,list[dict[str, int|str]]]:
    with open(path, "r") as f:
        results = json.load(f)
    return results

def main() -> None:
    replay_results = load_replay_results("replay_results.json")
    final_results = {}
    for library_name, results in replay_results.items():
        func_sources = []
        pocs = []
        stderrs = []
        for result in results:
            seed_id = result["seed_id"]
            seed = get_seed(seed_id)
            if seed is None:
                logger.error(f"Seed {seed_id} not found.")
                func_sources.append("")
                pocs.append(result["poc"])
                stderrs.append(result["result"])
                continue
            func_name = seed.func_name
            function = get_function(func_name)
            if function is None:
                logger.error(f"Function {func_name} not found.")
                func_sources.append("")
            else:
                func_sources.append(function.source)
            pocs.append(result["poc"])
            stderrs.append(result["result"])
        judgments = batch_judge(func_sources, pocs, stderrs, max_workers=4)
        filtered_results = []
        for result, (is_bug, risk_level, reasoning) in zip(results, judgments):
            if is_bug:
                filtered_result = deepcopy(result)
                filtered_result["risk_level"] = risk_level
                filtered_result["reasoning"] = reasoning
                filtered_results.append(filtered_result)
        logger.info(f"Library {library_name}: {len(filtered_results)}/{len(results)} vulnerabilities retained after filtering.")
        final_results[library_name] = filtered_results
    with open("filtered_replay_results.json", "w") as f:
        json.dump(final_results, f, indent=4, ensure_ascii=False)


if __name__ == "__main__":
    main()