import json
import re


def find_all_crash(log_path: str) -> list[dict[str, int]]:
    """
    从日志中找出所有疑似崩溃的记录，包含`Mutant ID`和`random state`

    Example log lines:
    2025-12-11 09:10:07.698 | INFO     | respfuzzer.lib.fuzz.fuzz_dataset:fuzz_single_seed:272 - Mutant 755 execution timeout after 5.0 seconds, restarting worker process. Last random state: 2074081936842814406
    2025-12-11 09:10:07.698 | INFO     | respfuzzer.lib.fuzz.fuzz_dataset:fuzz_single_seed:272 - Mutant 755 execution timeout after 5.0 seconds, restarting worker process. Last random state: None
    """
    crash_list = []
    # 因为测试用例的日志包含非法字符，所以以二进制方式读取，对应的正则表达式也要用二进制模式
    re_str = rb"Mutant (\d+) execution timeout after [\d\.]+ seconds, restarting worker process\. Last random state: (None|\d+)"
    with open(log_path, "rb") as f:
        log_content = f.read()
        crash_entries = re.findall(re_str, log_content)
        for entry in crash_entries:
            mutant_id = int(entry[0])
            random_state = None if entry[1] == b"None" else int(entry[1])
            crash_list.append({"mutant_id": mutant_id, "random_state": random_state})
    return crash_list


def find_all_logs(dir_path: str) -> list[str]:
    """
    找出目录下所有的log文件
    """
    import os

    log_files = []
    for root, _, files in os.walk(dir_path):
        for file in files:
            if file.endswith(".log"):
                log_files.append(os.path.join(root, file))
    return log_files


def main(log_file_path: str) -> None:
    crash_data: dict[str, list[dict[str, int]]] = {}
    library_name = "xixi"
    crashes = find_all_crash(log_file_path)
    if library_name not in crash_data:
        crash_data[library_name] = []
    crash_data[library_name].extend(crashes)

    with open("crash_summary.json", "w", encoding="utf-8") as f:
        json.dump(crash_data, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    import fire

    fire.Fire(main)
