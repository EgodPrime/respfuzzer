"""RQ1 F6b：静态模式 pass@k 引擎（normal/prompted/extended/rag）+ 候选提取。

与 ours 的差异仅在「是否自省」：
  - ours   ：RSG 自省循环（agentic solve，驱动层接入，见 RQ1Execuctor 注释）；
  - 本模块：对照模式的 pass@k 循环——生成 k 个候选、任意 covered 即短路。
等预算：对照 k 次 LLM 调用 ≈ ours 自省预算 k（回应 M4）。

covered 判定统一走共享 Executor（ED-15 语义 + C-6 静态门控，F9）；
extended 的源码切片与 rag 的检索片段由驱动层准备后注入（F7/F8）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from respfuzzer.lib.rq1.prompts import build_prompt
from respfuzzer.models import Function


@dataclass
class CandidateRecord:
    """单个候选：candidate_index 1..k、code、covered（ED-15 判定）。"""

    candidate_index: int
    code: str | None
    covered: bool


@dataclass
class FunctionRun:
    """单函数一次 run 的结果（供 F9 落盘）。"""

    func_path: str
    library_name: str
    covered: bool  # pass@k：任一候选覆盖
    candidates: list[CandidateRecord] = field(default_factory=list)


def extract_code(text: str | None) -> str | None:
    """从 LLM 响应提取代码：<code></code> 优先，``` 兜底；无则 None。

    与 RSG Attempter 的解析口径一致（统一 prompt 协议）。
    """
    if not text or not text.strip():
        return None
    if "<code>" in text and "</code>" in text:
        return text.split("<code>", 1)[1].split("</code>", 1)[0].strip()
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            code = parts[1].strip()
            lines = code.splitlines()
            # 去掉可能的语言标签首行（如 ```python），单 token 标识符即语言名
            if len(lines) > 1 and lines[0].strip().isidentifier():
                code = "\n".join(lines[1:]).strip()
            return code or None
    return None


def run_static_loop(
    mode: str,
    function: Function,
    k: int,
    llm,
    executor,
    *,
    source_slice: str = "",
    retrieval: str = "",
) -> FunctionRun:
    """对照模式 pass@k 循环：

    - 预算：至多 k 次 LLM 调用（每个候选 1 次，等预算）；
    - 任一候选 covered=True 即短路（后序候选不再生成 = ut-F6-6）；
    - 每候选记录 (candidate_index, code, covered) 供落盘。
    """
    if mode == "ours":
        raise ValueError("ours 走自省循环，不经 run_static_loop")
    candidates: list[CandidateRecord] = []
    covered = False
    for idx in range(1, k + 1):
        prompt = build_prompt(
            mode, function, source_slice=source_slice, retrieval=retrieval
        )
        text = llm.chat(
            [
                {
                    "role": "system",
                    "content": "你是一个代码生成助手，擅长根据函数信息生成函数调用。",
                },
                {"role": "user", "content": prompt},
            ]
        )
        code = extract_code(text)
        is_covered = False
        if code is not None:
            is_covered = bool(executor.covered(function, code))
        candidates.append(CandidateRecord(idx, code, bool(is_covered)))
        if is_covered:
            covered = True
            break
    return FunctionRun(
        func_path=function.func_path,
        library_name=function.library_name or "",
        covered=covered,
        candidates=candidates,
    )
