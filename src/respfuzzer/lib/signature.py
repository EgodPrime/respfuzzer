"""builtin 签名闭合解析链（C-10 根源性重推，§8 阶段3 / §8.3 E 组）。

职责：为 builtin（C 扩展函数）解析「参数 + 返回类型」，三阶优先级：
  ① .pyi stub（按物理身份键查，见 from_builtin_function_type）
  ② inspect.signature / __text_signature__（多数 C 扩展抛 ValueError，降为此级）
  ③ 解析 __doc__ 签名（torch / numpy 把调用签名写在 docstring 首行，如
     "linalg.norm(A, ord=None, dim=None, keepdim=False, *, out=None) -> Tensor"）
  ④ 三类全无 → fail-closed（fail_closed=True 时显式抛错中止，而非静默跳过）。

设计取舍（§10）：fail-closed 是 Q2 定稿原则，但会让缺 stub 的库（torch/pandas 大量
builtin）提取中止，需先补 supplemental-stub；故默认 fail_closed=False（跳过并返回 None），
由调用方在需要强制闭合的场合开启。supplemental-stub 注入让"先补 stub 再提取"可落地。
"""

from inspect import Parameter, _ParameterKind
from inspect import signature as _inspect_signature
from typing import Any

from respfuzzer.lib.parsers.function_parser import _default_to_str
from respfuzzer.models import Argument, Function


class SignatureUnresolvedError(RuntimeError):
    """fail-closed：builtin 三类签名源（stub/inspect/docstring）全缺失时抛出。"""


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


def _has_top_level(s: str, sep: str) -> bool:
    return len(_split_top_level(s, sep)) >= 2


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
    type_ = nt[1].strip() if len(nt) > 1 else "unknown"

    pos_type = (
        _ParameterKind.KEYWORD_ONLY.name
        if kw_only
        else _ParameterKind.POSITIONAL_OR_KEYWORD.name
    )
    return ("ARG", name, type_, default, pos_type)


def _signature_line_and_args(doc: str, max_lines: int = 5):
    """在 docstring 前若干行里找『形如签名』的首行（括号内容含顶层 , 或 =）。"""
    for line in (doc or "").splitlines()[:max_lines]:
        line = line.strip()
        if not line:
            continue
        content = _extract_paren_args(line)
        if content is None:
            continue
        # 散文内容（如 math.sin "measured in radians"）无顶层 , 或 = → 不是签名
        if _has_top_level(content, ",") or (content and _has_top_level(content, "=")):
            return line, content
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
        parsed = _parse_doc_param(tok, kw_only)
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
    fail_closed: bool = False,
):
    """builtin 签名闭合三阶解析链（§8.3 E1-E5）。

    返回 Function 或 None（可解析则建签名函数）；若全缺且 fail_closed=True 则抛
    `SignatureUnresolvedError`（Q2 硬失败原则），否则跳过返回 None。
    """
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

    # tier④ fail-closed
    where = f"{public_name} (real={'.'.join(real_path)})"
    if fail_closed:
        raise SignatureUnresolvedError(f"unresolvable builtin signature: {where}")
    return None
