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


def _parse_pyi_file(file_path: str, pyi_cache: Dict[str, Dict]) -> None:
    """
    解析 .pyi 文件，提取其中的函数
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
                    # 【MC2-8】提取默认参数值。
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

                    pyi_cache[func_name] = {
                        "name": func_name,
                        "source": source,
                        "args": arg_list,
                        "ret_type_str": ret_type_str,
                        "file_path": file_path,
                    }


def parse_pyi_files(file_path: str) -> dict:
    tmp_cache = {}
    _parse_pyi_file(file_path, tmp_cache)
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
