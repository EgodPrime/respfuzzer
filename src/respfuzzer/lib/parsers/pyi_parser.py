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
    """根据 .pyi 文件路径推导模块点分路径（相对库包根目录）。

    # --- C-10: 文件路径 -> 模块点分路径 ---
    空 root_path/file_path 守卫返回空串，防 os.path.relpath 抛异常或 ../ 链；
    relpath 转 os.sep 为点、去 .pyi 后缀；__init__.pyi 去掉 __init__ 段取父模块。
    """
    if not root_path or not file_path:
        return ""  # 空守卫：防 relpath 出错/../ 链
    rel = os.path.relpath(file_path, root_path).replace(os.sep, ".")
    rel = rel[:-4] if rel.endswith(".pyi") else rel  # 去 .pyi
    if rel == "__init__":  # 库顶层 __init__.pyi -> 空模块（无前导点，单独处理）
        return ""
    if rel.endswith(".__init__"):  # sub/__init__.pyi -> 取父模块
        rel = rel[: -len(".__init__")]
    return rel.rstrip(".")


def make_pyi_key_from_module(module_name: str, func_name: str) -> str:
    """读端放入口：以当前遍历模块 __name__ 为权威前缀构造完全限定键（C-10）。

    与写端 make_pyi_key 产出的 <库名>.<模块点分路径>.<函数名> 在镜像布局下逐字符一致。
    """
    return f"{module_name}.{func_name}"


def make_pyi_key(root_path: str, library_name: str, file_path: str, func_name: str) -> str:
    """写端出口：构造完全限定缓存键 <库名>.<模块点分路径>.<函数名>（C-10）。"""
    if not root_path or not library_name:
        return func_name  # 空 root/库名退化为裸函数名（无限定信息）
    mod = _pyi_module_from_path(root_path, file_path)
    if not mod:
        return f"{library_name}.{func_name}"  # 顶层 __init__.pyi -> <库名>.<函数名>
    return f"{library_name}.{mod}.{func_name}"

def _parse_pyi_file(
    file_path: str,
    pyi_cache: Dict[str, Dict],
    *,
    root_path: str = "",
    library_name: str = "",
) -> None:
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

                    # --- C-9: 提取默认参数值 ---
                    # 位置参数(posonlyargs + args)的默认值在 node.args.defaults 里，
                    # 与最右 len(defaults) 个位置参数按序对齐；此处先构造逐位置默认值表。
                    positional_params = [*node.args.posonlyargs, *node.args.args]
                    positional_defaults = node.args.defaults  # list[ast.expr]
                    pos_defaults: list[str] = [""] * len(positional_params)
                    n_def = len(positional_defaults)
                    if n_def > 0:
                        # defaults 对齐到 positional_params[-n_def:]（最右的 n_def 个位置参数）
                        offset = len(positional_params) - n_def
                        for j, expr in enumerate(positional_defaults):
                            pos_defaults[offset + j] = ast.unparse(expr)
                    pos_idx = 0  # 遍历位置参数（先 posonlyargs 后 args）的下标

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
                                default_value=pos_defaults[pos_idx],
                            )
                        )
                        pos_idx += 1
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
                                default_value=pos_defaults[pos_idx],
                            )
                        )
                        pos_idx += 1
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
                                default_value="",  # *args 永无默认值
                            )
                        )
                    for arg, kw_default in zip(
                        node.args.kwonlyargs, node.args.kw_defaults
                    ):
                        # kwonlyargs 的默认值在 node.args.kw_defaults（与 kwonlyargs 按序一一对应，
                        # None 表示无默认）；ast.arg 本身没有 .default 属性。
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
                                    ast.unparse(kw_default) if kw_default is not None else ""
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
                                default_value="",  # **kwargs 永无默认值
                            )
                        )

                    ret_type_str = (
                        ast.unparse(node.returns) if node.returns else "unknown"
                    )

                    key = make_pyi_key(root_path, library_name, file_path, func_name)  # --- C-10: 限定键 ---
                    pyi_cache[key] = {
                        "name": func_name,  # 保留裸名，供 from_builtin_function_type/public_name
                        "source": source,
                        "args": arg_list,
                        "ret_type_str": ret_type_str,
                        "file_path": file_path,
                    }


def parse_pyi_files(
    file_path: str,
    root_path: str = "",
    library_name: str = "",
) -> dict:
    tmp_cache = {}
    _parse_pyi_file(file_path, tmp_cache, root_path=root_path, library_name=library_name)
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
