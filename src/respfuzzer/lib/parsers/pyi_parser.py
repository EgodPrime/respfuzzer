import ast
import os
from inspect import _ParameterKind
from typing import Dict, Set

from respfuzzer.models import Argument


def is_top_level_function(node):
    """检查节点是否是模块级别的函数（不在类内部）"""
    parent = node
    while hasattr(parent, "parent"):
        parent = parent.parent
        if isinstance(parent, ast.ClassDef):
            return False  # 在类内部
    return True  # 在模块级别


def _pyi_module_from_path(root_path: str, file_path: str) -> str:
    """由 .pyi 文件路径推导模块点分路径（相对库包根目录）。

    空 root_path/file_path 守卫返回空串（防 os.path.relpath 抛异常或产出 ../ 链）；
    relpath 的 os.sep 转点、去 .pyi 后缀；`__init__.pyi` 去掉 __init__ 段取父模块。
    """
    if not root_path or not file_path:
        return ""
    rel = (
        os.path.relpath(file_path, root_path).replace(os.sep, ".").removesuffix(".pyi")
    )
    if rel == "__init__":  # 库顶层 __init__.pyi -> 无模块段
        return ""
    rel = rel.removesuffix(".__init__")  # sub/__init__.pyi -> 父模块
    return rel.rstrip(".")


def make_pyi_key(
    root_path: str, library_name: str, file_path: str, func_name: str
) -> str:
    """写端出口：构造完全限定缓存键 `<库名>.<模块点分路径>.<函数名>`。

    root_path / library_name 必传：空参会抛 ValueError，彻底关闭「未限定函数名键」路径
    （否则不同模块的同名函数会退化成同一个裸名键互相覆盖）。file_path 为空时退化为
    `<库名>.<函数名>`（库里顶层键，仍带库名前缀，不是裸名键）。
    """
    if not root_path or not library_name:
        raise ValueError(
            "make_pyi_key 需要非空 root_path 与 library_name：空参会产出未限定函数名键，"
            "导致不同模块同名函数的 stub 互相覆盖"
        )
    mod = _pyi_module_from_path(root_path, file_path)
    if not mod:
        return f"{library_name}.{func_name}"
    return f"{library_name}.{mod}.{func_name}"


def build_name_index(
    pyi_cache: dict[str, dict],
    *,
    ambiguous_bare_names: set[str] | None = None,
) -> dict[str, str]:
    """由 pyi_cache 派生「裸函数名 -> 限定键」索引：仅当该裸名在整库 stub 中唯一时登记。

    读端限定键 miss 时用唯一裸名兜底，用于救回 stub 布局与运行时模块路径不一致的库
    （re-export / 镜像布局）；同名歧义（多个限定键共用同一裸名）不入索引，仍强制走限定键，
    以保留防碰撞语义。

    `ambiguous_bare_names` 额外剔除「stub 侧唯一、但运行时存在 ≥2 个不同物理身份同名函数」
    的裸名：该情形下裸名兜底会让 stub 缺失的那一方错拿另一物理身份的 stub，故宁缺勿错配——
    直接不兜底，落到运行时签名解析。
    """
    counts: dict[str, int] = {}
    for key, entry in pyi_cache.items():
        nm = entry.get("name") or key.rsplit(".", 1)[-1]
        counts[nm] = counts.get(nm, 0) + 1
    ambiguous = set(ambiguous_bare_names or ())
    index: dict[str, str] = {}
    for key, entry in pyi_cache.items():
        nm = entry.get("name") or key.rsplit(".", 1)[-1]
        if nm in ambiguous:
            continue
        if counts[nm] == 1:
            index[nm] = key
    return index


def _parse_pyi_file(
    file_path: str,
    pyi_cache: Dict[str, Dict],
    *,
    root_path: str,
    library_name: str,
) -> None:
    """
    解析 .pyi 文件，提取其中的函数，并以完全限定键写入 pyi_cache。
    """
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
        tree = ast.parse(content)

        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                child.parent = node

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if is_top_level_function(node):
                    func_name = node.name
                    source = ast.get_source_segment(content, node)

                    arg_list = []
                    # 提取默认参数值。
                    # 位置参数（posonlyargs + args 合并后）的默认值集中在 node.args.defaults，
                    # 与最右 len(defaults) 个位置参数按序对齐；其余位置参数记空串（无默认）。
                    positional_params = [*node.args.posonlyargs, *node.args.args]
                    positional_defaults = node.args.defaults
                    pos_default_values = [""] * len(positional_params)
                    offset = len(positional_params) - len(positional_defaults)
                    for i, default_node in enumerate(positional_defaults):
                        pos_default_values[offset + i] = ast.unparse(default_node)
                    pos_index = 0

                    for arg in node.args.posonlyargs:
                        arg_name = arg.arg
                        arg_type = (
                            ast.unparse(arg.annotation) if arg.annotation else "unknown"
                        )
                        pos_type = _ParameterKind.POSITIONAL_ONLY.name
                        arg_list.append(
                            Argument(
                                arg_name=arg_name,
                                type=arg_type,
                                pos_type=pos_type,
                                default_value=pos_default_values[pos_index],
                            )
                        )
                        pos_index += 1
                    for arg in node.args.args:
                        arg_name = arg.arg
                        arg_type = (
                            ast.unparse(arg.annotation) if arg.annotation else "unknown"
                        )
                        pos_type = _ParameterKind.POSITIONAL_OR_KEYWORD.name
                        arg_list.append(
                            Argument(
                                arg_name=arg_name,
                                type=arg_type,
                                pos_type=pos_type,
                                default_value=pos_default_values[pos_index],
                            )
                        )
                        pos_index += 1
                    if node.args.vararg:
                        arg = node.args.vararg
                        arg_name = arg.arg
                        arg_type = (
                            ast.unparse(arg.annotation) if arg.annotation else "unknown"
                        )
                        pos_type = _ParameterKind.VAR_POSITIONAL.name
                        arg_list.append(
                            Argument(
                                arg_name=arg_name,
                                type=arg_type,
                                pos_type=pos_type,
                                default_value="",  # *args 无默认值
                            )
                        )
                    # 关键字参数（kwonlyargs）的默认值在 node.args.kw_defaults 中按序对应，
                    # 元素为 None 表示该参数无默认值。
                    for arg, kw_default in zip(
                        node.args.kwonlyargs, node.args.kw_defaults
                    ):
                        arg_name = arg.arg
                        arg_type = (
                            ast.unparse(arg.annotation) if arg.annotation else "unknown"
                        )
                        pos_type = _ParameterKind.KEYWORD_ONLY.name
                        arg_list.append(
                            Argument(
                                arg_name=arg_name,
                                type=arg_type,
                                pos_type=pos_type,
                                default_value=(
                                    ast.unparse(kw_default)
                                    if kw_default is not None
                                    else ""
                                ),
                            )
                        )
                    if node.args.kwarg:
                        arg = node.args.kwarg
                        arg_name = arg.arg
                        arg_type = (
                            ast.unparse(arg.annotation) if arg.annotation else "unknown"
                        )
                        pos_type = _ParameterKind.VAR_KEYWORD.name
                        arg_list.append(
                            Argument(
                                arg_name=arg_name,
                                type=arg_type,
                                pos_type=pos_type,
                                default_value="",  # **kwargs 无默认值
                            )
                        )

                    ret_type_str = (
                        ast.unparse(node.returns) if node.returns else "unknown"
                    )

                    key = make_pyi_key(
                        root_path, library_name, file_path, func_name
                    )  # 完全限定键
                    pyi_cache[key] = {
                        "name": func_name,  # 保留裸名作为元数据（source/args 消费方使用）
                        "source": source,
                        "args": arg_list,
                        "ret_type_str": ret_type_str,
                        "file_path": file_path,
                    }


def parse_pyi_files(file_path: str, root_path: str, library_name: str) -> dict:
    tmp_cache = {}
    _parse_pyi_file(
        file_path, tmp_cache, root_path=root_path, library_name=library_name
    )
    return tmp_cache


def _find_all_pyi_files(dir_path: str, unique_files: Set[str]) -> None:
    for root, dirs, files in os.walk(dir_path):
        for file in files:
            if file.endswith(".pyi"):
                file_path = os.path.join(root, file)
                unique_files.add(file_path)
        for dir_name in dirs:
            dir_path = os.path.join(root, dir_name)
            _find_all_pyi_files(dir_path, unique_files)
