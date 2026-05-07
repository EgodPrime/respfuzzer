import ast
import inspect
import importlib
import json
import os

def get_libraries_sloc_by_executable_lines(lib_name, func_paths):
    """
    逻辑：获取 API 所在的模块文件，去重后利用 co_lines() 统计
    这些文件中真正“可执行”的代码行总数。
    """
    try:
        main_lib = importlib.import_module(lib_name)
        lib_root = os.path.normpath(os.path.dirname(inspect.getfile(main_lib)))
    except Exception as e:
        print(f"无法加载库 {lib_name}: {e}")
        return 0, 0

    associated_files = set()

    for path in func_paths:
        try:
            parts = path.split('.')
            mod_name = ".".join(parts[:-1])
            f_name = parts[-1]
            
            module = importlib.import_module(mod_name)
            obj = getattr(module, f_name)
            
            f_path = os.path.normpath(inspect.getfile(obj))
            
            if f_path.startswith(lib_root) and f_path.endswith('.py'):
                associated_files.add(f_path)
        except:
            continue

    total_executable_loc = 0
    
    for file_path in associated_files:
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                source = f.read()
            
            # 1. 将源码编译为代码对象 (code object)
            # compile 的第二个参数是文件名，用于报错追踪，第三个参数 'exec' 代表整个模块
            code_obj = compile(source, file_path, 'exec')
            
            # 2. 递归统计该模块及其内部所有 Code Object 的可执行行号
            executable_lines = set()

            def collect_lines(co):
                # co_lines() 返回 (start_line, end_line, line_number) 的三元组
                # 只有 line_number 不为 None 的行才是可执行的
                for _, _, line_no in co.co_lines():
                    if line_no is not None:
                        executable_lines.add(line_no)
                
                # 递归处理嵌套的 code objects (如函数、类、推导式)
                for const in co.co_consts:
                    if inspect.iscode(const):
                        collect_lines(const)

            collect_lines(code_obj)
            
            # 3. 累加当前文件的唯一可执行行数
            total_executable_loc += len(executable_lines)
            
        except Exception as e:
            print(f"处理文件 {file_path} 失败: {e}")
            continue

    return total_executable_loc, len(associated_files)

def get_libraries_sloc_by_files(lib_name, func_paths):
    """
    逻辑：获取 API 所在的模块文件，去重后统计这些文件的总行数。
    """
    try:
        main_lib = importlib.import_module(lib_name)
        lib_root = os.path.normpath(os.path.dirname(inspect.getfile(main_lib)))
    except Exception as e:
        print(f"无法加载库 {lib_name}: {e}")
        return 0

    associated_files = set()

    for path in func_paths:
        try:
            # 动态获取对象以找到其所在文件
            parts = path.split('.')
            mod_name = ".".join(parts[:-1])
            f_name = parts[-1]
            
            module = importlib.import_module(mod_name)
            obj = getattr(module, f_name)
            
            # 获取该 API 所在的源文件绝对路径
            f_path = os.path.normpath(inspect.getfile(obj))
            
            # 只统计属于该库目录下的 Python 文件
            if f_path.startswith(lib_root) and f_path.endswith('.py'):
                associated_files.add(f_path)
        except Exception as e:
            # print(f"无法定位 API 文件 {path}: {e}")
            continue

    # 模仿 cloc 逻辑统计这些文件的 LOC (去空行和注释)
    total_loc = 0
    for file_path in associated_files:
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    # 只有非空且不是以 # 开头的才算作 code
                    if line and not line.startswith('#'):
                        total_loc += len(line.splitlines()) 
        except:
            continue

    return total_loc, len(associated_files)

def get_function_paths(function_file):
    try:
        with open(function_file, 'r') as f:
            data = json.load(f) # list[dict[str,str]]
            return data[0]['library_name'] ,[item['func_name'] for item in data]
    except Exception as e:
        print(f"无法读取函数文件 {function_file}: {e}")
        return None, []

    

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Count total Python lines for sampled APIs.")
    parser.add_argument("-f", "--function_file", help="Path to the JSON file containing function paths.")
    args = parser.parse_args()
    lib_name, func_paths = get_function_paths(args.function_file)
    total_loc, num_files = get_libraries_sloc_by_executable_lines(lib_name, func_paths)
    print(f"Total LOC: {total_loc}, Number of Files: {num_files}")

if __name__ == "__main__":
    main()