import csv
import json
import os
import re
import sqlite3
from pathlib import Path

from tracefuzz.utils.paths import RUNDATA_DIR

DB_PATH = RUNDATA_DIR / "tracefuzz.db"
OUTPUT_CSV = RUNDATA_DIR / "filtered_vulns.csv"
OUTPUT_JSON = RUNDATA_DIR / "filtered_vulns.json"


# 多维度崩溃关键词分类
CRASH_KEYWORDS = {
    "kernel": [
        "kernel panic",
        "oops",
        "system halted",
        "kernel BUG",
        "systrap",
        "soft lockup",
        "hard lockup",
        "nmi watchdog",
        "stack corruption",
        "slab corruption",
        "memory corruption",
    ],
    "memory": [
        "segfault",
        "segmentation fault",
        "core dumped",
        "bus error",
        "illegal instruction",
        "stack overflow",
        "out of memory",
        "oom killer",
        "memory allocation failed",
        "memory error",
        "malloc failed",
        "virtual memory exhausted",
    ],
    "process": [
        "aborted",
        "killed",
        "terminated",
        "exit code",
        "failed with result",
        "service crashed",
        "process died",
        "signal 11",
        "signal 6",
        "child process exited",
    ],
    "hardware": [
        "hardware error",
        "mce",
        "machine check exception",
        "thermal event",
        "cpu temperature",
        "i/o error",
        "disk failure",
        "sector error",
        "memory scrubbing",
        "ecc error",
        "corrected error",
        "uncorrectable error",
    ],
    "hang": [
        "hang",
        "unresponsive",
        "not responding",
        "freeze",
        "timeout",
        "stuck",
        "blocked",
        "hung task",
        "deadlock detected",
        "lockup",
    ],
    "generic": [
        "crash",
        "crashed",
        "critical error",
        "fatal error",
        "unrecoverable error",
        "system error",
        "abnormal termination",
    ],
}

EXCLUDED_PATTERNS = [
    re.compile(r"ValueError: .*", re.IGNORECASE),
    re.compile(r"KeyError", re.IGNORECASE),
    re.compile(r"TypeError: .*", re.IGNORECASE),
    re.compile(r"AttributeError: .* has no attribute .*", re.IGNORECASE),
    re.compile(r"AssertionError", re.IGNORECASE),
    re.compile(r"SyntaxError", re.IGNORECASE),
    re.compile(r"Traceback \(most recent call last\):.*pandas", re.DOTALL),
    re.compile(r"_UFuncInputCastingError", re.IGNORECASE),
    re.compile(r"_UFuncNoLoopError", re.IGNORECASE),
    re.compile(r"aggfunc cannot be used without values", re.IGNORECASE),
    re.compile(r"arrays and names must have the same length", re.IGNORECASE),
    re.compile(r"cannot be bound to", re.IGNORECASE),
    re.compile(r"re\.error: .*", re.IGNORECASE),
    re.compile(r"got an unexpected keyword argument.*", re.IGNORECASE),
]


def contains_crash_keyword(stderr: str) -> bool:
    stderr_lower = stderr.lower()
    for keyword_list in CRASH_KEYWORDS.values():
        if any(kw in stderr_lower for kw in keyword_list):
            return True
    return False


def is_potential_vuln(stderr: str) -> bool:
    if not stderr:
        return False
    if not contains_crash_keyword(stderr):
        return False
    if any(p.search(stderr) for p in EXCLUDED_PATTERNS):
        return False
    return True


def filter_vulnerabilities(batch_size=1000):
    if not os.path.isfile(DB_PATH):
        print(f"错误：数据库文件不存在 -> {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    offset = 0
    filtered_results = []

    try:
        while True:
            query = f"""
                SELECT * FROM mutant_execution
                WHERE ret_code != 0
                LIMIT {batch_size} OFFSET {offset}
            """
            rows = cursor.execute(query).fetchall()
            if not rows:
                break

            for row in rows:
                stderr = row[7] or ""
                if is_potential_vuln(stderr):
                    filtered_results.append(
                        {
                            "id": row[0],
                            "mutant_id": row[1],
                            "library_name": row[2],
                            "func_name": row[3],
                            "result_type": row[4],
                            "ret_code": row[5],
                            "stdout": row[6],
                            "stderr": stderr,
                        }
                    )

            offset += batch_size
    except Exception as e:
        print(f"查询或处理数据时出错: {e}")
    finally:
        conn.close()

    if not filtered_results:
        print("没有筛选到任何有效的崩溃/超时型漏洞。")
        return

    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)

    try:
        with open(OUTPUT_CSV, "w", encoding="utf-8", newline="") as f_csv:
            writer = csv.DictWriter(f_csv, fieldnames=filtered_results[0].keys())
            writer.writeheader()
            writer.writerows(filtered_results)
        print(f"筛选结果已导出到 CSV 文件: {OUTPUT_CSV}")
    except Exception as e:
        print(f"导出 CSV 出错: {e}")

    try:
        with open(OUTPUT_JSON, "w", encoding="utf-8") as f_json:
            json.dump(filtered_results, f_json, indent=2, ensure_ascii=False)
        print(f"筛选结果已导出到 JSON 文件: {OUTPUT_JSON}")
    except Exception as e:
        print(f"导出 JSON 出错: {e}")


if __name__ == "__main__":
    filter_vulnerabilities()
