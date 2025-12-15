"""
## 该脚本的功能
读取`crash_summary.json`文件，依次对每个记录的测试用例进行重放（replay），并利用LLM合成POC代码，最终将重放结果保存到`replay_results.json`文件中。
对于randome_state为None的记录，表示种子本身就导致崩溃，无需合成POC代码，其本身就是POC，直接记录重放结果即可。

## 输入形式
crash_summary.json:
{
    "library_name_1": [
        {
            "seed_id": int,
            "random_state": int|None
        },
        ...
    ],
    "library_name_2": [
        ...
    ],
    ...
}

## 输出形式
replay_results.json:
{
    "library_name_1": [
        {
            "seed_id": int,
            "random_state": int,
            "result": str  # 重放时的stderr描述
            "poc": str | None  # LLM合成的POC代码
        },
        ...
    ],
    "library_name_2": [
        ...
    ],
    ...
}

## 关键函数

- replay_mutation_and_execution(seed_id: int, random_state: int) -> tuple[str, str]
- pure_execution(function_call: str) -> str
- synthesize_poc(function_call: str, mutated_params_snapshot: str, stderr: str) -> str
- replay_one_record(seed_id: int, random_state: int) -> dict[str, int|str]
- replay_from_summary(data: dict[str, list[dict[str, int]]]) -> dict[str, list[dict[str, int|str]]]

## 工作流程
1. 读取`crash_summary.json`文件，获取所有需要重放的测试用例列表。
2. 对每个测试用例，调用`replay_one_record`函数进行重放，获取重放结果和LLM合成的POC代码。
    2.1. 调用`replay_mutation_and_execution`函数进行重放，获取重放结果和参数快照。如果`random_state`为`None`，则不进行2.2的POC合成，直接将重放结果保存到重放结果中。
    2.2. 调用`synthesize_poc`函数合成POC代码。
        2.2.1. 构造LLM的输入提示（prompt），包括函数调用代码、参数快照和重放结果(stderr描述)。
        2.2.2. 调用LLM接口，获取模型合成的POC代码。
        2.2.3. 调用`pure_execution`函数对合成的POC代码进行纯执行，验证其stderr描述是否与重放结果一致。
        2.2.4. 如果一致，将POC代码保存到重放结果中。
3. 将所有重放结果保存到`replay_results.json`文件中。

## 备注
提取参数快照来自于重放过程中loguru的输出，其代码语句为`logger.info(f"Replayed params: args={args}, kwargs={kwargs}")`,可以使用正则表达式进行提取。
"""

import threading
from loguru import logger
import subprocess
import re
import openai
from respfuzzer.repos.mutant_table import get_mutant
import concurrent.futures
from copy import deepcopy
import tempfile
import difflib

# 前置变量：并发数量（可根据需要调整或在运行前修改此变量）
MAX_CONCURRENCY = 4

def call_llm_api(prompt: str) -> str:
    client = openai.OpenAI(api_key="no", base_url="http://192.168.1.44:8021")
    response = client.chat.completions.create(
        model="qwen3-30b-a3b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        top_p=0.9,
        presence_penalty=1,
        max_tokens=1024,
    )
    return response.choices[0].message.content.strip()

def replay_mutation_and_execution(seed_id: int, random_state: int) -> tuple[str, str]:
    """
    Replay the mutation and execute the function call, returning stderr and params snapshot.
    """
    p = subprocess.Popen(
        ["replay_mutation", "single_shot", str(seed_id), str(random_state)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _, stderr = p.communicate(timeout=5)
        stderr = stderr.decode()
        logger.info(f"Replay's stderr:\n{stderr}")
        # with open(f'/tmp/respfuzzer_replay_{random_state}_args.dump', 'rb') as f:
        #     args_dump = f.read()
        # with open(f'/tmp/respfuzzer_replay_{random_state}_kwargs.dump', 'rb') as f:
        #     kwargs_dump = f.read()
        # mutated_params_snapshot = f"args={args_dump}, kwargs={kwargs_dump}"
            
        # logger.info(f"Params snapshot: {mutated_params_snapshot}")
        mutated_params_snapshot = "skipped"
        
    except subprocess.TimeoutExpired:
        p.kill()
        logger.warning(f"Replay timed out after 5 seconds")
        stderr = "Timeout after 5 seconds"
        mutated_params_snapshot = ""
    except Exception as e:
        logger.error(f"Error during replay: {e}")
        stderr = f"{e}"
        mutated_params_snapshot = ""
    
    return stderr, mutated_params_snapshot

def pure_execution(function_call: str) -> str:
    """
    Purely execute the function call without any mutation, returning stderr.
    """
    with tempfile.NamedTemporaryFile(delete=False) as temp_file:
        temp_file.write(function_call.encode())
        temp_file_path = temp_file.name
    p = subprocess.Popen(
        ["python", temp_file_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _, stderr = p.communicate(timeout=5)
        stderr = stderr.decode()
        logger.info(f"Pure execution stderr: {stderr}")
    except subprocess.TimeoutExpired:
        p.kill()
        logger.warning(f"Pure execution timed out after 5 seconds")
        stderr = "Timeout after 5 seconds"
    except Exception as e:
        logger.error(f"Error during pure execution: {e}")
        stderr = f"{e}"
    
    return stderr

def llm_judge_similarity(stderr_orig: str, stderr_new: str) -> bool:
    """
    Use LLM to judge the similarity between two stderr strings.
    Returns True if similar, False otherwise.
    """
    prompt = f""" ## Task
You are a security researcher. Given two stderr outputs from program executions, determine if they indicate the same underlying issue.
You should consider the context and content of the error messages, not just exact string matches.

## Examples

### Example 1: very similar

<stderr_orig>
Segmentation fault (core dumped)
</stderr_orig>
<stderr_new>
Segmentation fault (core dumped)
</stderr_new>
<is_similar>True</is_similar>

### Example 2: similar stack traces and very similar error messages

<stderr_orig>
Traceback (most recent call last):
  File "example.py", line 10, in <module>
    main()
  File "example.py", line 6, in main
    result = 1 / 0
ZeroDivisionError: division by zero
</stderr_orig>
<stderr_new>
Traceback (most recent call last):
  File "test.py", line 5, in <module>
    compute()
  File "test.py", line 2, in compute
    value = 10 / 0
ZeroDivisionError: division by zero
</stderr_new>
<is_similar>True</is_similar>

## Start
<stderr_orig>{stderr_orig}</stderr_orig>
<stderr_new>{stderr_new}</stderr_new>
"""
    response = call_llm_api(prompt)
    return response

def synthesize_poc(function_call: str, mutated_params_snapshot: str, stderr: str) -> str:
    """
    Synthesize a POC code snippet using LLM based on the function call, params snapshot, and stderr.
    """
    prompt_base = f""" ## Task
You are a security researcher. Given the following `function_call` that caused an `stderr` when executed with certain mutated parameters `mutated_params_snapshot`, generate a minimal Proof of Concept (POC) code snippet that reproduces the same error.

## Constraints
- The POC should be as minimal as possible while still reproducing the error.
- Use the provided `function_call` and `mutated_params_snapshot` to construct the POC.
- Output only the POC code snippet without any additional explanations or comments or useless print statements.
- Ouput should be warpped in <poc></poc> tags.
- The `mutated_params_snapshot` provides args and kwargs, but both of them are serialized as bytes by pickle. You need to deserialize them before use.
- You can deserialize the args and kwargs using `pickle.loads`.

## Example
<function_call>
from some_library import vulnerable_function
vulnerable_function(user_input="malicious_payload")
</function_call>
<mutated_params_snapshot>
args=('malicious_payload',), kwargs={{}}
</mutated_params_snapshot>
<stderr>
Segmentation fault (core dumped)
</stderr>
<poc>
from some_library import vulnerable_function
vulnerable_function(user_input="malicious_payload")
</poc>
"""

    prompt_history = ""

    prompt_data= f"""
## Start
<function_call>{function_call}</function_call>
<mutated_params_snapshot>{mutated_params_snapshot}</mutated_params_snapshot>
<stderr>{stderr}</stderr>
"""
    
    for _ in range(10):
        prompt = prompt_base + prompt_history + prompt_data
        poc_code = call_llm_api(prompt)
        # Extract POC code from <poc> tags
        m = re.search(r"<poc>(.*?)</poc>", poc_code, re.DOTALL)
        if m:
            poc_code_snippet = m.group(1).strip()
            # Validate the generated POC
            pure_stderr = pure_execution(poc_code_snippet)
            is_similar = llm_judge_similarity(stderr, pure_stderr)
            if is_similar:
                return poc_code_snippet
            else:
                logger.info("Generated POC did not reproduce the same stderr, retrying...")
                prompt_history += f"""
## Failed Attempt
<poc>{poc_code_snippet}</poc>
<output>{pure_stderr}</output>
"""
    return ""

def replay_one_record(seed_id: int, random_state: int) -> dict[str, int|str]:
    """
    Replay one record given seed_id and random_state, returning the result and synthesized POC.
    """
    logger.info(f"Replaying mutant {seed_id} with random state {random_state}")
    seed = get_mutant(seed_id)
    function_call = seed.function_call
    
    stderr, mutated_params_snapshot = replay_mutation_and_execution(seed_id, random_state)
    return {
        'seed_id': seed_id,
        'random_state': random_state,
        'result': stderr,
        'poc': function_call
    }

    if random_state is None:
        logger.debug("SM")
        stderr = pure_execution(function_call)
        # If random_state is None, the seed itself causes the crash, no need to synthesize POC
        return {
            'seed_id': seed_id,
            'random_state': random_state,
            'result': stderr,
            'poc': function_call  # The function call itself is the POC
        }
    else:
        logger.debug("PM")
        stderr, mutated_params_snapshot = replay_mutation_and_execution(seed_id, random_state)
        poc_code = synthesize_poc(function_call, mutated_params_snapshot, stderr)
        return {
            'seed_id': seed_id,
            'random_state': random_state,
            'result': stderr,
            'poc': poc_code
        }

def replay_from_summary(data: dict[str, list[dict[str, int]]], max_workers: int = MAX_CONCURRENCY) -> dict[str, list]:
    """
    Replay all mutations recorded in the crash summary data using a thread pool.
    每个 (seed_id, random_state) 对应一个并发任务，任务完成后将结果归并到对应的 library_name 列表中。
    """
    res: dict[str, list] = {}
    future_map: dict[concurrent.futures.Future, str] = {}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        for library_name, crash_list in data.items():
            for crash in crash_list:
                seed_id = crash['seed_id']
                random_state = crash['random_state']
                future = executor.submit(replay_one_record, seed_id, random_state)
                c = deepcopy(crash)
                c['library_name'] = library_name
                future_map[future] = c

        # 收集结果并归并
        for future in concurrent.futures.as_completed(future_map):
            crash = future_map[future]
            library_name = crash['library_name']
            try:
                record_result = future.result()
            except Exception as e:
                logger.error(f"Error during replaying record {crash}: {e}")
                continue
            if library_name not in res:
                res[library_name] = []
            res[library_name].append(record_result)

    return res

if __name__ == '__main__':
    import json
    with open('crash_summary.json', 'r', encoding='utf-8') as f:
        crash_data = json.load(f)
    res = replay_from_summary(crash_data)

    with open('replay_results.json', 'w', encoding='utf-8') as f:
        json.dump(res, f, indent=2, ensure_ascii=False)