import importlib
import inspect
import json
import os
import re
from types import BuiltinFunctionType, FunctionType, ModuleType
from typing import Dict, Iterator, List, Optional, Set

import fire
from loguru import logger

from mplfuzz.models import API, Argument, PosType
from mplfuzz.utils.result import Ok, Err, Result, resultify
from mplfuzz.db.api_parse_record_table import create_api

logger.level("INFO")


@resultify
def _compactify_arg_list_str(raw_arg_list_str: str) -> Result[str, Exception]:
    """
    删除所有的行注释，并将字符串中的所有空格和换行符删除以转换为方便后续处理的紧凑格式。
    Args:
        raw_args_str (str): 包含原始参数列表的字符串。
    Returns:
        str: 清理后的紧凑参数列表字符串。
    """
    compact_arg_list_str = re.sub(r"\ #.*\n", "", raw_arg_list_str).strip()
    compact_arg_list_str = compact_arg_list_str.replace("\n", "")
    compact_arg_list_str = compact_arg_list_str.replace(" ", "")
    return compact_arg_list_str


@resultify
def _split_compact_arg_list_str(compact_arg_list_str: str) -> Result[List[str], Exception]:
    """
    将紧凑的参数列表字符串拆分为单独的参数字符串列表。
    Args:
        compact_arg_list_str (str): 包含紧凑参数列表的字符串。
    Returns:
        List[str]: 包含拆分后的参数字符串的列表。
    """
    arg_str_list = []
    parentheses_stack = 0
    brackets_stack = 0
    braces_stack = 0
    dquote_stack = 0
    squote_stack = 0
    s = 0
    # logger.debug(compact_arg_list_str)
    for i, ch in enumerate(compact_arg_list_str):
        match ch:
            case "[":
                brackets_stack += 1
            case "]":
                brackets_stack -= 1
            case "(":
                parentheses_stack += 1
            case ")":
                parentheses_stack -= 1
            case "{":
                braces_stack += 1
            case "}":
                braces_stack -= 1
            case '"':
                dquote_stack = 1 - dquote_stack
            case "'":
                squote_stack = 1 - squote_stack
            case ",":
                if sum([parentheses_stack, brackets_stack, braces_stack, dquote_stack, squote_stack])==0:
                    arg_str_list.append(compact_arg_list_str[s:i])
                    s = i + 1
    if s < len(compact_arg_list_str):
        arg_str_list.append(compact_arg_list_str[s:])
    while "" in arg_str_list:
        arg_str_list.remove("")
    return arg_str_list


@resultify
def _parse_arg_str(arg_str: str) -> Result[Argument, Exception]:
    r"""
    解析参数字符串，返回参数对象。
    需要考虑这些情况：
    a
    a:int
    a:List[int]
    a:Dict[str,int]
    a:int=2
    a={2:3}
    a:Dict[str,int]={"2":3}
    a:List[int]=[1,2,3,4,5]
    a:Tuple=(1,2,3)
    a:Set={1,2,3}
    a:X=X(a=1,b=2)
    pat=re.compile(r"\s*(?:#\s*)?$").match
    Args:
        arg_str (str): 参数字符串。
    Returns:
        Argument: 参数对象。
    """
    pattern = re.compile(r"([a-zA-Z0-9_\*]+)(?:\:([^\=]+))?(?:\=(.+))?", re.DOTALL)
    arg_name, arg_type, arg_default = pattern.match(arg_str).groups()
    if not arg_type:
        arg_type = "unknown"
    return Argument(arg_name=arg_name, type=arg_type, pos_type=0)  # pos_type默认为0，后续根据实际情况修改


@resultify
def _parse_arg_str_list(arg_str_list: list[str]) -> Result[List[Argument], Exception]:
    """
    解析参数字符串列表，返回参数对象列表。
    Args:
        arg_str_list (List[str]): 包含拆分后的参数字符串的列表。
    Returns:
        List[Argument]: 包含解析后的参数对象的列表。
    """
    posonly_arg_strs = []
    kwonly_arg_strs = []
    # 检查positional-only, keyword-only
    if "/" in arg_str_list:
        posonly_flag_idx = arg_str_list.index("/")
        posonly_arg_strs = arg_str_list[:posonly_flag_idx]
        arg_str_list = arg_str_list[posonly_flag_idx + 1 :]
    if "*" in arg_str_list:
        kwonly_flag_idx = arg_str_list.index("*")
        kwonly_arg_strs = arg_str_list[kwonly_flag_idx + 1 :]
        arg_str_list = arg_str_list[:kwonly_flag_idx]

    arg_list = []

    for i, arg_str in enumerate(posonly_arg_strs + arg_str_list):
        # logger.debug(arg_str)
        arg = _parse_arg_str(arg_str).unwrap()
        arg.pos_type = PosType.PositionalOnly
        arg_list.append(arg)

    for i, arg_str in enumerate(kwonly_arg_strs):
        arg = _parse_arg_str(arg_str)
        if arg.is_err:
            logger.warning(str(arg.error))
            # logger.debug(kwonly_arg_strs)
            # logger.debug(arg_str)
            raise Exception("Invalid keyword-only argument")
            continue
        arg = arg.value
        arg.pos_type = PosType.KeywordOnly
        arg_list.append(arg)

    return arg_list


@resultify
def from_function_type(obj: FunctionType) -> Result[API, Exception]:
    """
    convert a `FunctionType` to an `API` object
    """
    # get the "full package path" name of the function
    name = f"{obj.__module__}.{obj.__name__}"
    # get the source code of the function
    try:
        source = inspect.getsource(obj)
    except OSError:
        # if no source code is available, use the signature instead
        # a pure Python function always has a signature
        source = str(inspect.signature(obj))

    source = re.sub(r"\ #.*\n", "", source)  # 删除注释
    # logger.debug(f"Function name:{name}\nSource code:\n{source}")
    # match_str = r"def\s+[a-zA-Z][a-zA-Z0-9_]*\s*\((.*?)\)\s*(?:->\s*([^:]+))?:"
    match_str = r"""
    def\s+
    ([a-zA-Z][a-zA-Z0-9_]*)\s*
    \((.*?)\)\s*
    (?:->\s*([^:]+))?:\s*
    (?:
    (?:r?)?
    ('''|\"\"\"|\"\"\"|'''|'|\"|\"|')
    (.*?)
    \4
    )?
"""
    re_flags = re.DOTALL|re.VERBOSE
    match = re.search(match_str, source, re_flags)
    try:
        _name, raw_args_str, ret_type_str, _ , docstring = match.groups()
        ret_type_str = "unknown" if ret_type_str is None else ret_type_str
        logger.debug(f"Parsing function {_name}({name})")
    except Exception as e:
        print(source)
        print(match_str)
        raise e

    logger.debug(raw_args_str)
    args = _compactify_arg_list_str(raw_args_str).and_then(_split_compact_arg_list_str).and_then(_parse_arg_str_list)

    if args.is_ok:
        return API(api_name=name, source=source, args=args.value, ret_type=ret_type_str)
    else:
        return args


@resultify
def from_builtin_function_type(pyi_dict: Dict[str, Dict], obj: BuiltinFunctionType) -> Result[API, Exception]:
    name = f"{obj.__module__}.{obj.__name__}"
    return API(api_name=name, source=pyi_dict["source"], args=pyi_dict["args"], ret_type=pyi_dict["ret_type_str"])


class LibraryVisitor:
    def __init__(self, library_name: str):
        self.library_name = library_name
        self.pyi_cache: Dict[str, Dict] = {}

    def visit(self) -> Iterator[API]:
        """
        Visit all the public APIs in the library and yield them.

        Example:
        >>> visitor = LibraryVisitor('math')
        >>> for api in visitor.visit():
        ...     print(api)
        """
        try:
            library = importlib.import_module(self.library_name)
        except ModuleNotFoundError:
            logger.error(f"Library {self.library_name} not found")
            return
        self.find_all_pyi_functions()
        json.dump(
            self.pyi_cache,
            open(f"{self.library_name}_pyi_functions.json", "w"),
            indent=2,
            default=lambda x: x.model_dump(),
        )

        mod_has_been_seen = set()
        for api in self._visit(library, self.library_name, mod_has_been_seen):
            yield api

    def _get_library_root_path(self) -> Optional[str]:
        """
        获取指定库的 root 路径
        """
        spec = importlib.util.find_spec(self.library_name)
        if spec is None or spec.origin is None:
            return None
        return os.path.dirname(spec.origin)

    @resultify
    def _parse_pyi_file(self, file_path: str) -> Result[None, Exception]:
        """
        解析 .pyi 文件，提取其中的函数
        """
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # 使用正则表达式匹配函数定义
        function_def_pattern = re.compile(
            r"def\s+([a-zA-Z][a-zA-Z0-9_]*)\s*\((.*?)\)\s*(?:->\s*([^:]+))?:\s*...",
            re.DOTALL
        )
        matches = function_def_pattern.finditer(content)

        for match in matches:
            func_name = match.group(1)
            raw_args_str = match.group(2)
            logger.debug(f"Parsing builtin function {func_name}")
            ret_type_str = match.group(3) if match.group(3) else "unknown"

            if raw_args_str is None:
                compat_arg_list_str = ""
                args = []
            else:
                # 一些字符串清洗处理
                try:
                    compat_arg_list_str = _compactify_arg_list_str(raw_args_str).unwrap()
                    arg_str_list = _split_compact_arg_list_str(compat_arg_list_str).unwrap()
                    # logger.debug(arg_str_list)
                    args = _parse_arg_str_list(arg_str_list).unwrap()
                except Exception as e:
                    print(raw_args_str)
                    raise e

            func_str = f"def {func_name}({compat_arg_list_str})"
            if match.group(3):
                func_str += f" -> {ret_type_str}"
            func_str += ":..."

            self.pyi_cache[func_name] = {
                "name": func_name,
                "source": func_str,
                "args": args,
                "ret_type_str": ret_type_str,
                "file_path": file_path,
            }

    @resultify
    def _find_all_pyi_files(self, dir_path: str, visited_files: Set[str]) -> Result[None, Exception]:
        """
        递归遍历指定目录下的所有 .pyi 文件，并将文件路径存入 visited_files 集合中
        """
        for root, dirs, files in os.walk(dir_path):
            for file in files:
                if file.endswith(".pyi"):
                    file_path = os.path.join(root, file)
                    if file_path in visited_files:
                        continue
                    visited_files.add(file_path)
                    self._parse_pyi_file(file_path).unwrap()
                    # self._parse_pyi_file(file_path).map_err(lambda e: logger.warning(f"Error parsing {file_path}: {e}"))
            for dir_name in dirs:
                self._find_all_pyi_files(os.path.join(root, dir_name), visited_files).unwrap()
                # self._find_all_pyi_files(os.path.join(root, dir_name), visited_files).map_err(
                #     lambda e: logger.warning(f"Error finding pyi files in {dir_path}: {e}")
                # )

    def find_all_pyi_functions(self):
        # 先根据库名找到库的根目录，然后递归遍历找出所有的pyi文件，再找出pyi文件中所有的函数，存入self.pyi_cache中
        root_path = self._get_library_root_path()
        if not root_path:
            return

        visited_files: Set[str] = set()
        self._find_all_pyi_files(root_path, visited_files).unwrap()
        # self._find_all_pyi_files(root_path, visited_files).map_err(
        #     lambda e: logger.warning(f"Error finding pyi files in {self.library_name}: {e}")
        # )

    def _visit(self, mod: ModuleType, root_mod_name: str, mod_has_been_seen: set) -> Iterator[API]:
        # Skip if the module has already been seen.
        if id(mod) in mod_has_been_seen:
            return
        mod_has_been_seen.add(id(mod))

        # Visit all the attributes in the module.
        if hasattr(mod, "__all__"):
            names = mod.__all__
        else:
            names = dir(mod)
        for name in names:
            # Skip if the attribute is a private attribute.
            if name.startswith("_"):
                continue

            # Try to get the attribute.
            try:
                obj = getattr(mod, name)
            except AttributeError:
                logger.warning(f"getattr({mod.__name__}, {name}) failed")
                continue

            # We think API is one of [`FunctionType`, `BuiltinFunctionType`]
            if isinstance(obj, FunctionType):
                # Thanks for Python's namespace mechanism :(
                # We need to filter out the APIs that don't belong to the library.
                if obj.__module__ is None:
                    logger.warning(f"{mod.__name__}.{name} is FunctionType but has no __module__")
                    continue
                if any(list(filter(lambda x: x.startswith("_"), obj.__module__.split(".")))):
                    continue
                if obj.__module__.startswith(root_mod_name):
                    api = from_function_type(obj)
                    if api.is_err:
                        logger.warning(f"Failed to create API from {mod.__name__}.{name}: {api.error}")
                        continue
                    yield api.value

            elif isinstance(obj, BuiltinFunctionType):
                if obj.__module__ is None:
                    logger.warning(f"{mod.__name__}.{name} is FunctionType but has no __module__")
                    continue
                if any(list(filter(lambda x: x.startswith("_"), obj.__module__.split(".")))):
                    continue
                if obj.__module__.startswith(root_mod_name) and name in self.pyi_cache:
                    api = from_builtin_function_type(self.pyi_cache[name], obj)
                    if api.is_err:
                        logger.warning(f"Failed to create API from {mod.__name__}.{name}: {api.error}")
                        continue
                    yield api.value

            # Recursively visit the submodule.
            elif isinstance(obj, ModuleType):
                try:
                    # `ModuleType`.__name__ is a "full package path" str
                    obj_full_name = obj.__name__
                except Exception:
                    # Thanks for the lazy mode used by some libraries :(
                    clone_obj = mod.__new__(type(obj))
                    clone_obj.__dict__.update(obj.__dict__)
                    obj_full_name = clone_obj.__name__
                # Skip the submodule if it doesn't belong to the library.
                if obj_full_name.startswith(root_mod_name):
                    for api in self._visit(obj, root_mod_name, mod_has_been_seen):
                        yield api


def _main(library_name: str, verbose: bool = False):
    logger.info(f"Parsing APIs in library {library_name}")
    lv = LibraryVisitor(library_name)
    cnt = 0
    for api in lv.visit():
        create_api(api).map_err(lambda e: logger.error(f"Error saving  API {api.api_name}: {e}"))
        cnt += 1
        if verbose:
            logger.info(f"API {api.api_name} parsed and saved to db")
    logger.info(f"Finished parsing {cnt} APIs in library {library_name}")


def main():
    fire.Fire(_main)


if __name__ == "__main__":
    main()