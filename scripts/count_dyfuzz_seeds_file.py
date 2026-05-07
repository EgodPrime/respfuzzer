import ast
import inspect
import importlib
import json
import os

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

def count_seeds(seeds_file):
    with open(seeds_file, 'r') as f:
        data = json.load(f)
    
    grand_total = 0
    print(f"{'Library':<12} | {'APIs':<6} | {'Files':<6} | {'SLOC (File-based)':<15}")
    print("-" * 50)

    for lib, api_names in data.items():
        # 统一路径拼接逻辑
        full_api_names = []
        for api in api_names.keys():
            if api.startswith(lib + "."):
                full_api_names.append(api)
            else:
                full_api_names.append(f"{lib}.{api}")
        
        loc, file_count = get_libraries_sloc_by_files(lib, full_api_names)
        print(f"{lib:<12} | {len(api_names):<6} | {file_count:<6} | {loc:<15}")
        grand_total += loc
    
    print("-" * 50)
    print(f"Grand Total SLOC: {grand_total}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Count total Python lines for sampled APIs.")
    parser.add_argument("-s", "--seeds_file", help="Path to the JSON file containing sampled APIs.")
    args = parser.parse_args()
    count_seeds(args.seeds_file)

if __name__ == "__main__":
    main()