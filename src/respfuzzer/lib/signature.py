"""builtin 签名闭合解析链（C-10 根源性重推，§8 阶段3 / §8.3 E 组）。

职责：为 builtin（C 扩展函数）解析「参数 + 返回类型」，三阶优先级：
  ① .pyi stub（按物理身份键查，见 from_builtin_function_type）
  ② inspect.signature / __text_signature__（多数 C 扩展抛 ValueError，降为此级）
  ③ 解析 __doc__ 签名（torch / numpy 把调用签名写在 docstring 首行，如
     "linalg.norm(A, ord=None, dim=None, keepdim=False, *, out=None) -> Tensor"）
  ④ 三类全无 → 不抛错、不 drop：失败字段留空（args=[]/ret_type=""），返回
     signature_resolved=False 的占位条目，由下游显式识别/过滤。

设计取舍（§10）：不再 fail-closed（删除抛错机制）、不再静默 drop——unsolved 一律收录
并打标（signature_resolved），使『分桶到失败』落地为数据形态而非统计口径；同时
signature_resolved=False 的占位条目不参与满类型注解判定（has_full_type_hints 短路）。
"""

import re

from inspect import Parameter, _ParameterKind
from inspect import signature as _inspect_signature
from typing import Any

from respfuzzer.lib.parsers.function_parser import _default_to_str
from respfuzzer.models import Argument, Function



def _split_top_level(s: str, sep: str = ",") -> list[str]:
    """按顶层分隔符切分，忽略括号/方括号/花括号嵌套与引号内的分隔符。"""
    parts: list[str] = []
    cur = ""
    depth = 0
    quote: str | None = None
    for ch in s:
        if quote is not None:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            cur += ch
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        cur += ch
    parts.append(cur)
    return parts


def _extract_paren_args(line: str) -> str | None:
    """取 line 内第一个平衡括号组的内容；不成对/无括号返回 None。"""
    start = line.find("(")
    if start < 0:
        return None
    depth = 0
    quote: str | None = None
    for i in range(start, len(line)):
        ch = line[i]
        if quote is not None:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return line[start + 1 : i]
    return None


def _first_top_sep(seg: str, chars: str = ":=") -> int:
    """首个顶层冒号/等号下标（忽略括号与引号嵌套），无则 -1。"""
    depth = 0
    quote: str | None = None
    for i, ch in enumerate(seg):
        if quote is not None:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and ch in chars:
            return i
    return -1


def _is_identifier(s: str) -> bool:
    return re.fullmatch(r"\w[\w.]*", s.strip()) is not None
def _is_param_token(seg: str) -> bool:
    """判定逗号分段是否为形参 token。先剥掉所有 [] 可选括号组（numpy [start,] / stop[, step,] / [start,] stop[, step,]），
    再要求剩余核心为标识符或『名:=默认』。散文（多词无分隔）→ False。"""
    s = seg.strip()
    if not s:
        return True
    if s in ("*", "/") or s.startswith("*"):
        return True  # 裸*（kw-only）/ 仅位置标记 / *args/**kwargs
    core = re.sub(r"\[[^\]]*\]", "", s).replace(" ", "").rstrip(",")
    if not core:
        return True  # 纯可选括号组（如 [start,]）
    i = _first_top_sep(core, ":=")
    if i >= 0:
        return _is_identifier(core[:i])  # 冒号/等号前是单个标识符 → 形参
    return _is_identifier(core)  # 无分隔 → 核心须为标识符


def _parse_doc_param(tok: str, kw_only: bool):
    """把单个参数 token 解析为统一 5 元组 (kind, name, type, default, pos_type)。"""
    tok = tok.strip()
    if tok == "*":
        return None  # 裸 *：仅由调用方置 kw_only，不算参数
    if tok.startswith("**"):
        return (
            "VAR_KEYWORD",
            tok[2:].strip(),
            "unknown",
            "",
            _ParameterKind.VAR_KEYWORD.name,
        )
    if tok.startswith("*") and len(tok) > 1:
        return (
            "VAR_POSITIONAL",
            tok[1:].strip(),
            "unknown",
            "",
            _ParameterKind.VAR_POSITIONAL.name,
        )

    eq = _split_top_level(tok, sep="=")
    name_type = eq[0].strip()
    default = eq[1].strip() if len(eq) > 1 else ""

    nt = _split_top_level(name_type, sep=":")
    name = nt[0].strip().strip(".,;")

    _m = re.match(r"\[?\s*([\w.]+)", name)  # numpy 可选括号记法 [start,] / stop[, step,] → 取首个标识符

    if _m:
        name = _m.group(1)
    type_ = nt[1].strip() if len(nt) > 1 else "unknown"

    pos_type = (
        _ParameterKind.KEYWORD_ONLY.name
        if kw_only
        else _ParameterKind.POSITIONAL_OR_KEYWORD.name
    )
    return ("ARG", name, type_, default, pos_type)


def _paren_span(lines: list[str], idx: int):
    """自 lines[idx] 首个 '(' 起跨行扫到匹配 ')'（numpy 长签名多行）。返回 (签名串, 括号内内容) 或 None。"""
    line = lines[idx]
    open_i = line.find("(")
    if open_i < 0:
        return None
    depth = 0
    quote: str | None = None
    buf = ""
    content_buf = ""
    started = False
    for li in range(idx, len(lines)):
        text = lines[li]
        for i, ch in enumerate(text):
            if li == idx and i < open_i:
                buf += ch
                continue
            if quote is not None:
                buf += ch
                if started:
                    content_buf += ch
                if ch == quote:
                    quote = None
                continue
            if ch in "\"'":
                quote = ch
                buf += ch
                if started:
                    content_buf += ch
                continue
            if ch == "(":
                depth += 1
                buf += ch
                if started:
                    content_buf += ch  # 嵌套 '('（仅内容）
                else:
                    started = True  # 外层 '('：开始收集，但不入内容
                continue
            if ch == ")":
                depth -= 1
                buf += ch
                if depth == 0:
                    # 闭括号后收集本行剩余（含 -> 返回注解），供 ret_type 检测
                    return buf + text[i + 1 :], content_buf
                if started:
                    content_buf += ch  # 嵌套 ')'
                continue
            buf += ch
            if started:
                content_buf += ch
    return None
def _signature_line_and_args(doc: str, max_lines: int = 5):
    """在 docstring 前若干行（支持跨行平衡括号）找形如签名的首行：前缀标识符、段落形参形态、零参单判。"""
    lines = [l.strip() for l in (doc or "").splitlines()[:max_lines]]
    for idx, line in enumerate(lines):
        if not line or "(" not in line:
            continue
        span = _paren_span(lines, idx)
        if span is None:
            continue
        sig, content = span
        # '(' 前必须是纯标识符（点分/下划线），排除句中括号的散文（“Return the sine of x (…)”）
        prefix = line[: line.find("(")].strip()
        if prefix and not re.fullmatch(r"[\w.]+", prefix):
            continue
        if content.strip() == "":
            # 零参签名：括号后须有 -> 返回注解，或整行就是 `name()`
            tail = sig[sig.rfind(")") + 1 :].strip()
            if "->" in tail or (tail == "" and prefix):
                return sig, content
            continue
        if all(_is_param_token(s) for s in _split_top_level(content, sep=",")):
            return sig, content
    return None


def parse_docstring_signature(doc: str):
    """从 builtin 的 __doc__ 解析调用签名 → dict(args, ret_type) 或 None。"""
    found = _signature_line_and_args(doc)
    if not found:
        return None
    line, content = found

    args: list[Argument] = []
    kw_only = False
    for tok in _split_top_level(content, sep=","):
        t = tok.strip()
        if not t:
            continue  # 零参（content 为空）不产出无名参数
        if t == "/":
            continue  # 仅位置参数标记 /：跳过（近似视为位置参数均可）
        # numpy 连续可选括号记法（[start,] stop[, step,]）→ 展开为多个位置参数
        if "[" in t:
            _names = re.findall(r"\[?([\w.]+)", t)
            if len(_names) >= 1 and len(t.split()) > 1:
                for _nm in _names:
                    args.append(
                        Argument(
                            arg_name=_nm,
                            type="unknown",
                            pos_type=(
                                _ParameterKind.KEYWORD_ONLY.name
                                if kw_only
                                else _ParameterKind.POSITIONAL_OR_KEYWORD.name
                            ),
                            default_value="",
                        )
                    )
                continue
        parsed = _parse_doc_param(t, kw_only)
        if parsed is None:
            kw_only = True
            continue
        kind, name, type_, default, pos_type = parsed
        args.append(
            Argument(
                arg_name=name,
                type=type_,
                pos_type=pos_type,
                default_value=(default if kind.startswith("ARG") else ""),
            )
        )
    ret_type = "unknown"
    if "->" in line:
        ret_type = line.split("->", 1)[1].strip().rstrip(".").strip() or "unknown"
    return {"args": args, "ret_type": ret_type}


def _signature_from_inspect(obj: Any):
    """tier②：inspect.signature（C 扩展常抛 ValueError → None）。"""
    try:
        sig = _inspect_signature(obj)
    except (ValueError, TypeError):
        return None
    args: list[Argument] = []
    for k, v in sig.parameters.items():
        arg_type = (
            str(v.annotation)[8:-2]
            if v.annotation is not Parameter.empty
            else "unknown"
        )
        args.append(
            Argument(
                arg_name=k,
                type=arg_type,
                pos_type=v.kind.name,
                default_value=_default_to_str(v.default),
            )
        )
    ret_type = (
        str(sig.return_annotation)[8:-2]
        if sig.return_annotation is not Parameter.empty
        else "unknown"
    )
    return {"args": args, "ret_type": ret_type}


def _build_builtin_function(obj, real_path, public_name, parsed) -> Function:
    args = parsed["args"]
    ret_type = parsed["ret_type"]
    source = (
        f"def {public_name}("
        + ", ".join(_arg_sig(a) for a in args)
        + f") -> {ret_type}: ...\n    # signature resolved at runtime"
    )
    return Function(
        func_path=public_name,
        real_path=".".join(real_path),
        source=source,
        args=args,
        ret_type=ret_type,
        is_builtin=1,
        function_type="built_in",
        docstring=obj.__doc__ or "",
    )


def _arg_sig(a: Argument) -> str:
    return f"{a.arg_name}: {a.type}"


SUPPLEMENTAL_STUBS: list[dict] = []
# supplemental-stub 注入（§8.3 E5）：{library_name, key(物理身份键), entry} 前置查询


def resolve_builtin_signature(
    obj,
    real_path,
    *,
    pyi_cache,
    name_index,
    public_name,
):
    '''builtin 签名闭合三阶解析链（§8.3 E1-E5）。

    返回 Function，**永不返回 None、永不抛错**：三源(stub/inspect/docstring)全缺时
    返回失败字段留空（args=[]/ret_type=""）的 signature_resolved=False 占位条目，
    由下游显式识别/过滤（删 fail/drop 机制，§10）。
    '''
    from respfuzzer.lib.parsers.function_parser import from_builtin_function_type

    key = ".".join(real_path)
    lib = real_path[0] if real_path else ""

    # tier① stub：supplemental 前置 → pyi 物理身份键 → 整库唯一裸名兜底
    entry = None
    for sup in SUPPLEMENTAL_STUBS:
        if sup.get("library_name") == lib and sup.get("key") == key:
            entry = sup.get("entry")
            break
    if entry is None:
        entry = pyi_cache.get(key)
    if entry is None:
        mapped = name_index.get(real_path[-1]) if real_path else None
        if mapped:
            entry = pyi_cache.get(mapped)
    if entry is not None:
        return from_builtin_function_type(entry, obj, public_name=public_name)

    # tier② inspect.signature / __text_signature__
    parsed = _signature_from_inspect(obj)
    if parsed is not None:
        return _build_builtin_function(obj, real_path, public_name, parsed)

    # tier③ 解析 __doc__ 签名（torch/numpy 把签名写进 docstring）
    parsed = parse_docstring_signature(obj.__doc__)
    if parsed is not None:
        return _build_builtin_function(obj, real_path, public_name, parsed)

    # tier④ 三源全缺：不抛错、不 drop——失败字段留空，返回 signature_resolved=False 占位条目
    try:
        _doc = (obj.__doc__ or "") if hasattr(obj, "__doc__") else ""
    except Exception:
        _doc = ""
    return Function(
        func_path=public_name,
        real_path=".".join(real_path) if real_path else "",
        source="",
        args=[],
        ret_type="",
        is_builtin=1,
        function_type="built_in",
        docstring=_doc,
        signature_resolved=False,
    )
