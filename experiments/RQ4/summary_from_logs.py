import re
import json

def find_all_crash(log_path: str) -> list[dict[str, int]]:
    """
    从日志中找出所有疑似崩溃的记录，包含`Seed ID`和`random state`
    """
    crash_list = []
    re_str = r'Seed (\d+) attempt \d+ did not complete successfully, last random state: (\d+).'
    with open(log_path, 'r', encoding='utf-8') as f:
        log_content = f.read()
        crash_entries = re.findall(re_str, log_content)
        for entry in crash_entries:
            seed_id = int(entry[0])
            random_state = int(entry[1])
            crash_list.append({
                'seed_id': seed_id,
                'random_state': random_state
            })
    return crash_list

def find_all_logs(dir_path: str) -> list[str]:
    """
    找出目录下所有的log文件
    """
    import os
    log_files = []
    for root, _, files in os.walk(dir_path):
        for file in files:
            if file.endswith('.log'):
                log_files.append(os.path.join(root, file))
    return log_files

if __name__ == '__main__':
    log_files = find_all_logs('.')
    crash_data: dict[str, list[dict[str, int]]] = {}
    for log_file in log_files:
        # yyyymmddhhmm-RQ4-<library_name>.log
        library_name = log_file.split('-')[-1].replace('.log', '')
        crashes = find_all_crash(log_file)
        if library_name not in crash_data:
            crash_data[library_name] = []
        crash_data[library_name].extend(crashes)
    
    with open('crash_summary.json', 'w', encoding='utf-8') as f:
        json.dump(crash_data, f, indent=2, ensure_ascii=False)

