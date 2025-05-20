import importlib
import inspect
import os
import re
import sys
from types import BuiltinFunctionType, FunctionType, ModuleType
from typing import Callable, Iterator, Optional, Set, Dict
from pathlib import Path
import json

from loguru import logger

from mplfuzz.models import API, Argument, PosType


def from_function_type(obj: FunctionType) -> API:
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

    # get the arguments of the function
    args: list[Argument] = []
    code = obj.__code__
    num_normal_arg = code.co_argcount
    num_kwonly_arg = code.co_kwonlyargcount
    var_names = code.co_varnames
    idx = 0
    for _ in range(num_normal_arg):
        args.append(Argument(name=var_names[idx], pos_type=PosType.PositionalOnly))
        idx += 1
    for _ in range(num_kwonly_arg):
        args.append(Argument(name=var_names[idx], pos_type=PosType.KeywordOnly))
        idx += 1
    return API(name=name, source=source, args=args)


def from_builtin_function_type(pyi_dict: Dict[str,Dict], obj:BuiltinFunctionType) -> API:
    name = f"{obj.__module__}.{obj.__name__}"
    args: list[Argument] = []
    for arg in pyi_dict['args']:
        args.append(Argument(name=arg['name'], pos_type=arg['pos_type']))
    return API(name=name, source=pyi_dict['source'], args=args)


class LibraryVisitor:
    def __init__(self, library_name: str):
        self.library_name = library_name
        self.pyi_cache: Dict[str,str] = {}

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
        json.dump(self.pyi_cache, open(f"{self.library_name}_pyi_functions.json", "w"), indent=2)
        # sys.exit(0)
        
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
    
    def _parse_pyi_file(self, file_path: str) -> None:
        """
        解析 .pyi 文件，提取其中的函数
        """
        def split_args(arg_list_str:str):
            r_args = []
            stack = 0
            s = 0
            for i,ch in enumerate(arg_list_str+','):
                match ch:
                    case '[':
                        stack += 1
                    case ']':
                        stack -= 1
                    case ',':
                        if stack == 0:
                            r_args.append(arg_list_str[s:i])
                            s = i + 1
            while '' in r_args:
                r_args.remove('')
            return r_args


        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 使用正则表达式匹配函数定义
        function_def_pattern = re.compile(r'def\s+([^\(]+)\s*\(\s*([^\)]+)\s*\)(\s*->\s*([^:]+))?\s*:\s*\.\.\.')  #TODO: 参数单独换一种匹配模式，依次匹配出每一个参数
        matches = function_def_pattern.finditer(content)

        for match in matches:
            
            func_name = match.group(1)

            raw_args = match.group(2)

            # 一些字符串清洗处理
            args = re.sub(r'#.*', '', raw_args).strip()
            args = args.replace('\n','')
            clean_args = args.replace(' ','')
            # print("="*30)
            # print(args)
            args = split_args(clean_args)
            # print(args)
    
            posonly_args = []
            kwonly_args = []
            # 检查positional-only, keyword-only
            if '/' in args:
                posonly_flag_idx = args.index('/')
                posonly_args = args[:posonly_flag_idx]
                args = args[posonly_flag_idx+1:]
            if '*' in args:
                kwonly_flag_idx = args.index('*')
                kwonly_args = args[kwonly_flag_idx+1:]
                args = args[:kwonly_flag_idx]

            final_args = []
            for i,arg in enumerate(posonly_args + args):
                if ':' in arg:
                    arg_name, arg_type = arg.split(':')
                    arg_name = arg_name.strip()
                    arg_type = arg_type.strip()
                else:
                    arg_name = arg
                    arg_type = 'unknown'
                final_args.append({
                    'name': arg_name,
                    'type': arg_type,
                    'pos_type': 1
                })
            
            for i,arg in enumerate(kwonly_args):
                if ':' in arg:
                    arg_name, arg_type = arg.split(':')
                    arg_name = arg_name.strip()
                    arg_type = arg_type.strip() 
                else:
                    arg_name = arg
                    arg_type = 'unknown'
                final_args.append({
                    'name': arg_name,
                    'type': arg_type,
                    'pos_type': 2
                })

            ret_type = match.group(4)

            func_str = f"def {func_name}({clean_args})"
            if match.group(4):
                func_str += f" -> {ret_type}"
            func_str += ":..."
            self.pyi_cache[func_name] = {
                'name': func_name,
                'source': func_str,
                'args': final_args,
                'return_type': ret_type,
                'file_path': file_path,
            }
    
    def _find_all_pyi_files(self, dir_path:str, visited_files: Set[str]):
        """
        递归遍历指定目录下的所有 .pyi 文件，并将文件路径存入 visited_files 集合中
        """
        for root, dirs, files in os.walk(dir_path):
            for file in files:
                if file.endswith('.pyi'):
                    file_path = os.path.join(root, file)
                    if file_path in visited_files:
                        continue
                    visited_files.add(file_path)
                    self._parse_pyi_file(file_path)
            for dir_name in dirs:
                self._find_all_pyi_files(os.path.join(root, dir_name), visited_files)

    def find_all_pyi_functions(self):
        # 先根据库名找到库的根目录，然后递归遍历找出所有的pyi文件，再找出pyi文件中所有的函数，存入self.pyi_cache中
        root_path = self._get_library_root_path()
        if not root_path:
            return

        visited_files: Set[str] = set()
        self._find_all_pyi_files(root_path, visited_files)


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
                    yield from_function_type(obj)

            elif isinstance(obj, BuiltinFunctionType):
                if obj.__module__ is None:
                    logger.warning(f"{mod.__name__}.{name} is FunctionType but has no __module__")
                    continue
                if any(list(filter(lambda x: x.startswith("_"), obj.__module__.split(".")))):
                    continue
                if obj.__module__.startswith(root_mod_name) and name in self.pyi_cache:
                    yield from_builtin_function_type(self.pyi_cache[name], obj)
            
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
