"""RQ1 F6：5 模式 prompt 构造（ours/normal/prompted/extended/rag）。

生成/判定/落盘完全共享；唯一差异在 prompt 内容（ED-14a 方案 B：
docstring 始终在 function dump 中，不 exclude）：
  - normal    基座 prompt（静态、无自省）；
  - prompted  normal + <guidance> 指导段（ED-13：签名/类型/调用点指导）；
  - extended  上 + <source_slice> 源码切片注入（F7）；
  - rag       上 + <retrieval> 检索片段（F8，由 LLM 自行检索后注入）；
  - ours      RSG 自省循环（复用 agentic Attempter，不走本模块构造）。
"""

from __future__ import annotations

from respfuzzer.models import Function

MODES = ("ours", "normal", "prompted", "extended", "rag")
DEFAULT_MODE = "ours"

_BASE_PROMPT = """任务:
请根据`function`中的信息为{func_path}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。

注意:
1. 你生成的代码应该用<code></code>包裹。
2. 不要生成```。
3. 不要生成`code`以外的任何内容。
4. 不要生成与`function`无关的代码(例如打印、注释等)。
5. 生成的`code`以"from {pkg} import {name}"开头。
6. 生成的`code`中函数调用形式为"{name}(...)"

<function>
{function_dump}
</function>
"""

_GUIDANCE = """调用指导（供参考，不要求全部满足）：
- 依据参数名与类型注解构造实参：标量用字面量，数组/对象按库内常用构造方式初始化；
- 若函数有默认参数，可省略以最小化出错面；
- 返回值接收变量建议命名为 `res`；
- 除该库必需的辅助构造外，不要调用无关函数。"""


def build_prompt(
    mode: str,
    function: Function,
    *,
    guidance: str = _GUIDANCE,
    source_slice: str = "",
    retrieval: str = "",
) -> str:
    """按 mode 组装 prompt；未知 mode 抛 ValueError（ut-F6-1）。"""
    if mode not in MODES:
        raise ValueError(f"未知 mode: {mode}（合法: {MODES})")
    if mode == "ours":
        raise ValueError("ours 模式不走 build_prompt（复用 agentic Attempter）")
    pkg, name = function.func_path.rsplit(".", 1)
    prompt = _BASE_PROMPT.format(
        func_path=function.func_path,
        pkg=pkg,
        name=name,
        function_dump=function.model_dump_json(),
    )
    if mode in ("prompted", "extended", "rag"):
        prompt += f"<guidance>\n{guidance}\n</guidance>\n"
    if mode == "extended":
        prompt += f"<source_slice>\n{source_slice}\n</source_slice>\n"
    if mode == "rag":
        prompt += f"<retrieval>\n{retrieval}\n</retrieval>\n"
    return prompt
