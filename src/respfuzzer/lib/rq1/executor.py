"""RQ1 F9：共享 Executor——seed 覆盖判定（ED-15 语义 + C-6 静态门控）。

覆盖 ≠「能跑通」。判定链条（ED-15，M7 走「强化 oracle」路线）：
  1. C-6 静态门控：复用 ``is_valid_semantic``（compile + 目标调用存在性）；
     不过静态门控 → covered=False。
  2. 子进程隔离执行 seed（超时可控，防止 seed 拖垮/污染主进程）；
  3. 执行异常时用 ``semantic_oracle.entered_function_body`` 判定：
     - 领域级异常（进了目标函数体）→ covered=True；
     - 构造级异常（调用/绑定层失败）→ covered=False；
     - 无异常 → covered=True；超时 → covered=False。

所有 RQ1 模式（ours/normal/prompted/extended/rag）共用本判定（单点实现）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from respfuzzer.lib.fuzz.llm_mutator import is_valid_semantic
from respfuzzer.models import Function, Mutant

# 子进程判定脚本：读入 {func_path, code}，解析目标函数对象、执行、
# 用 ED-15 预言机判定 covered，以单行 JSON 输出结果（哨兵行便于提取）。
_SUBPROCESS_SNIPPET = """\
import importlib, json, sys

def _resolve(func_path):
    pkg, name = func_path.rsplit(".", 1)
    mod = importlib.import_module(pkg)
    return getattr(mod, name)

def main():
    payload = json.loads(sys.stdin.read())
    func_path, code = payload["func_path"], payload["code"]
    try:
        target = _resolve(func_path)
    except Exception as exc:
        print(json.dumps({"covered": False, "error": "resolve:" + repr(exc)}))
        return
    from respfuzzer.lib.fuzz.semantic_oracle import entered_function_body
    try:
        exec(compile(code, "<seed>", "exec"), {})
    except BaseException as exc:
        print(json.dumps({"covered": bool(entered_function_body(target, exc)),
                          "error": "exec"}))
        return
    print(json.dumps({"covered": True, "error": None}))

main()
"""


def static_gate(function: Function, code: str) -> bool:
    """C-6 静态门控：compile 有效 + 含对目标函数的调用（否则 covered=False）。"""
    if not code or not code.strip():
        return False
    mutant = Mutant(
        func_id=function.id,
        seed_id=0,  # 静态门控不涉及 seed
        library_name=function.library_name or "",
        func_path=function.func_path,
        args=function.args,
        function_call=code,
    )
    return is_valid_semantic(mutant) is not None


class RQ1Executor:
    """共享 Executor：对 seed 代码做 ED-15 覆盖判定（5 模式共用）。"""

    def __init__(self, timeout: float = 10.0, python: str | None = None) -> None:
        self.timeout = timeout
        # 默认用当前解释器（uv 虚拟环境），保证子进程能 import respfuzzer。
        self._python = python or sys.executable

    def execute(self, function: Function, code: str) -> dict[str, Any]:
        """子进程隔离执行，返回 {"covered": bool, "result_type": str, "error": str|None}。"""
        payload = json.dumps({"func_path": function.func_path, "code": code})
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(_SUBPROCESS_SNIPPET)
            script_path = Path(fh.name)
        try:
            proc = subprocess.run(
                [self._python, str(script_path)],
                input=payload,
                text=True,
                capture_output=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return {"covered": False, "result_type": "TIMEOUT", "error": "timeout"}
        finally:
            script_path.unlink(missing_ok=True)

        if proc.returncode != 0:  # 子进程崩（含 seed 内 os._exit 等极端情况）
            return {
                "covered": False,
                "result_type": "ABNORMAL",
                "error": f"subprocess rc={proc.returncode}: {(proc.stderr or '')[-400:]}",
            }
        result = self._parse_result(proc.stdout)
        result["result_type"] = "OK" if result.get("covered") else "CALLFAIL"
        return result

    @staticmethod
    def _parse_result(stdout: str) -> dict[str, Any]:
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                    if isinstance(data, dict) and "covered" in data:
                        return {
                            "covered": bool(data["covered"]),
                            "error": data.get("error"),
                        }
                except json.JSONDecodeError:
                    continue
        return {"covered": False, "error": "no-result"}

    def covered(self, function: Function, code: str) -> bool:
        """完整判定：静态门控失败或执行判定失败均返回 False。"""
        if not static_gate(function, code):
            return False
        return bool(self.execute(function, code).get("covered"))
