"""
## 该脚本的功能
读取`crash_summary.json`文件，依次对每个记录的测试用例进行重放（replay），并利用LLM合成POC代码，最终将重放结果保存到`replay_results.json`文件中。

## 输入形式
crash_summary.json:
{
    "library_name_1": [
        {
            "seed_id": int,
            "random_state": int
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

- replay_mutation_and_execution(function_call: str, random_state: int) -> tuple[str, str]
- pure_execution(function_call: str) -> str
- synthesize_poc(function_call: str, params_snapshot: str, stderr: str) -> str
- replay_one_record(seed_id: int, random_state: int) -> dict[str, int|str]
- replay_from_summary(data: dict[str, list[dict[str, int]]]) -> dict[str, list[dict[str, int|str]]]

## 工作流程
1. 读取`crash_summary.json`文件，获取所有需要重放的测试用例列表。
2. 对每个测试用例，调用`replay_one_record`函数进行重放，获取重放结果和LLM合成的POC代码。
    2.1. 调用`replay_mutation_and_execution`函数进行重放，获取重放结果和参数快照。
    2.2. 调用`synthesize_poc`函数合成POC代码。
        2.2.1. 构造LLM的输入提示（prompt），包括函数调用代码、参数快照和重放结果(stderr描述)。
        2.2.2. 调用LLM接口，获取模型合成的POC代码。
        2.2.3. 调用`pure_execution`函数对合成的POC代码进行纯执行，验证其stderr描述是否与重放结果一致。
        2.2.4. 如果一致，将POC代码保存到重放结果中。
3. 将所有重放结果保存到`replay_results.json`文件中。
"""

from loguru import logger
import subprocess

def replay_from_summary(data: dict[str, list[dict[str, int]]]) -> dict[str, list]:
    """
    Replay all mutations recorded in the crash summary data.
    """
    res = {}
    for library_name, crash_list in data.items():
        for crash in crash_list:
            seed_id = crash['seed_id']
            random_state = crash['random_state']
            result = ''
            logger.info(
                f"Replaying seed {seed_id} from library {library_name} with random state {random_state}"
            )
            p = subprocess.Popen(
                ["replay_mutation", "single_shot", str(seed_id), str(random_state)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                _, stderr = p.communicate(timeout=5)
                logger.info(f"Replay stderr: {stderr.decode()}")
                result = stderr.decode()
            except subprocess.TimeoutExpired:
                p.kill()
                logger.warning(f"Replay timed out after 5 seconds")
                result = "Timeout"
            if library_name not in res:
                res[library_name] = []
            res[library_name].append({
                'seed_id': seed_id,
                'random_state': random_state,
                'result': result
            })
    return res

if __name__ == '__main__':
    import json
    with open('crash_summary.json', 'r', encoding='utf-8') as f:
        crash_data = json.load(f)
    res = replay_from_summary(crash_data)

    with open('replay_results.json', 'w', encoding='utf-8') as f:
        json.dump(res, f, indent=2, ensure_ascii=False)