#!/usr/bin/env python3
"""
Generate test cases for multiple libraries (5 APIs per library).
Uses the existing solve() logic from agentic_function_resolver.py.
Only selects functions whose C/C++ source code can be found.
Usage: uv run python scripts/generate_test_cases.py
"""

import concurrent.futures
import json
import re
import subprocess
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from respfuzzer.lib.library_visitor import LibraryVisitor
from respfuzzer.lib.agentic_function_resolver import solve
from loguru import logger

logger.level("INFO")

# Cache for source directories
SOURCE_DIRS = {}

# Libraries with C/C++ implementation (5 of the original 12)
LIBRARIES = {
    "numpy": "https://github.com/numpy/numpy.git",
    "pandas": "https://github.com/pandas-dev/pandas.git",
    "scipy": "https://github.com/scipy/scipy.git",
    "torch": "https://github.com/pytorch/pytorch.git",
    "paddle": "https://github.com/PaddlePaddle/Paddle.git",
}

API_PER_LIBRARY = 5


def get_source_dir(library_name: str) -> Path | None:
    """Get or clone library source code to local cache."""
    if library_name in SOURCE_DIRS:
        return SOURCE_DIRS[library_name]

    source_dir = Path.home() / ".cache" / f"{library_name}_source"
    SOURCE_DIRS[library_name] = source_dir

    if source_dir.exists():
        logger.info(f"Using cached {library_name} source at {source_dir}")
        return source_dir

    if library_name not in LIBRARIES:
        logger.error(f"No repository URL for {library_name}")
        return None

    logger.info(f"Cloning {library_name} source to {source_dir}...")
    source_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", LIBRARIES[library_name], str(source_dir)],
            check=True,
            capture_output=True,
        )
        logger.info(f"Successfully cloned {library_name} source")
        return source_dir
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to clone {library_name}: {e}")
        return None


# Cache for C/C++ file index per library: {library_name: [Path, ...]}
C_FILE_INDEX = {}


def build_c_file_index(library_name: str, source_dir: Path) -> list[Path]:
    """Build once per library: collect all C/C++ files."""
    if library_name in C_FILE_INDEX:
        return C_FILE_INDEX[library_name]

    print(f"    Building C/C++ file index for {library_name}...")
    c_extensions = ['.c', '.h', '.cpp', '.hpp']
    files = []
    for ext in c_extensions:
        for cfile in source_dir.rglob(f"*{ext}"):
            if any(skip in str(cfile) for skip in ['test', 'bench', 'doc', 'examples', '.git']):
                continue
            files.append(cfile)
    C_FILE_INDEX[library_name] = files
    print(f"    Indexed {len(files)} C/C++ files")
    return files


def find_c_source_for_function(func_name: str, source_dir: Path, library_name: str) -> Path | None:
    """Search for C/C++ source code implementing the given function."""
    if source_dir is None or not source_dir.exists():
        return None

    base_name = func_name.split(".")[-1]

    search_patterns = [
        rf'(?i){base_name}',
        rf'(?i)PyArray_{base_name}',
        rf'(?i)array_{base_name}',
    ]

    files = build_c_file_index(library_name, source_dir)

    for cfile in files:
        try:
            content = cfile.read_text(errors='ignore')
            for pattern in search_patterns:
                if re.search(pattern, content):
                    return cfile.relative_to(source_dir)
        except Exception:
            continue

    return None


def generate_for_function(function, output_dir: Path, source_file: Path = None) -> dict:
    """Generate test case for a single function using existing solve() logic."""
    func_name_short = function.func_name.split(".")[-1]
    filename = f"{function.library_name}_{func_name_short}.py"
    filepath = output_dir / filename

    logger.info(f"Try solving {function.func_name} ...")
    code = None
    try:
        code = solve(function)
    except Exception as e:
        logger.info(f"Failed to solve {function.func_name}: {e}")

    result = {
        "library_name": function.library_name,
        "func_name": function.func_name,
        "success": code is not None,
        "source_file": str(source_file) if source_file else None,
        "output_file": str(filepath),
    }

    if code:
        with open(filepath, "w") as f:
            f.write(code)
        logger.info(f"Seed found for {function.func_name}:\n{code}")
        result["code"] = code
    else:
        logger.info(f"Failed to solve {function.func_name}")
        result["code"] = None

    return result


def generate_for_function_from_manifest(item: dict, output_dir: Path, source_file: Path, source_dir: Path) -> dict:
    """Generate test case for a function from manifest data."""
    from respfuzzer.models import Function, Argument

    func_name = item["func_name"]
    library_name = item["library_name"]
    func_name_short = func_name.split(".")[-1]
    filename = f"{library_name}_{func_name_short}.py"
    filepath = output_dir / filename

    logger.info(f"Try solving {func_name} ...")

    func = Function(
        func_name=func_name,
        library_name=library_name,
        source=str(source_file) if source_file else "unknown",
        args=item.get("args", []),
    )

    code = None
    try:
        code = solve(func)
    except Exception as e:
        logger.info(f"Failed to solve {func_name}: {e}")

    result = {
        "library_name": library_name,
        "func_name": func_name,
        "success": code is not None,
        "source_file": str(source_file) if source_file else None,
        "output_file": str(filepath),
    }

    if code:
        with open(filepath, "w") as f:
            f.write(code)
        logger.info(f"Seed found for {func_name}:\n{code}")
        result["code"] = code
    else:
        logger.info(f"Failed to solve {func_name}")
        result["code"] = None

    return result


def filter_functions_with_source(functions: list, source_dir: Path, library_name: str, min_needed: int = 5) -> list:
    """Filter functions to only those with findable C/C++ source code.

    Stops early once min_needed functions with source are found.
    """
    functions_with_source = []
    print(f"\n  Filtering {len(functions)} functions by source availability (stop at {min_needed})...")
    for i, func in enumerate(functions):
        print(f"    [{i+1}/{len(functions)}] Checking {func.func_name} ... ", end="", flush=True)
        source_file = find_c_source_for_function(func.func_name, source_dir, library_name)
        if source_file:
            try:
                rel_path = source_file.relative_to(source_dir)
            except ValueError:
                rel_path = source_file
            print(f"FOUND ({rel_path})")
            functions_with_source.append((func, rel_path))
            if len(functions_with_source) >= min_needed:
                print(f"  Found {min_needed} functions with source, stopping early")
                break
        else:
            print("NOT FOUND")
    return functions_with_source


def select_diverse_functions_with_source(functions_with_source: list, n: int) -> list:
    """Select n functions ensuring diversity across modules."""
    if not functions_with_source:
        return []

    modules: dict[str, list] = {}
    for func, source_file in functions_with_source:
        parts = func.func_name.split(".")
        if len(parts) >= 2:
            module = parts[1]
        else:
            module = "root"
        if module not in modules:
            modules[module] = []
        modules[module].append((func, source_file))

    print(f"\n  Module distribution:")
    for module, funcs in sorted(modules.items()):
        print(f"    {module}: {len(funcs)} functions")

    selected = []
    module_names = sorted(modules.keys())
    module_indices = {m: 0 for m in module_names}

    while len(selected) < n:
        for module in module_names:
            if len(selected) >= n:
                break
            if module_indices[module] < len(modules[module]):
                selected.append(modules[module][module_indices[module]])
                module_indices[module] += 1

    return selected


def process_library(library_name: str, n_apis: int, output_dir: Path) -> list:
    """Process a single library: extract functions, filter by source, generate test cases."""
    print(f"\n{'='*60}")
    print(f"Processing library: {library_name}")

    visitor = LibraryVisitor(library_name)
    try:
        all_functions = list(visitor.visit())
    except Exception as e:
        logger.error(f"Failed to extract functions from {library_name}: {e}")
        return []

    print(f"  Total functions found: {len(all_functions)}")

    source_dir = get_source_dir(library_name)
    if source_dir is None:
        print(f"  Error: Could not get source directory for {library_name}")
        return []

    functions_with_source = filter_functions_with_source(all_functions, source_dir, library_name, min_needed=n_apis)
    print(f"\n  {len(functions_with_source)}/{len(all_functions)} functions have C/C++ source")

    if len(functions_with_source) == 0:
        print(f"  Warning: No functions with C/C++ source found for {library_name}")
        return []

    selected = select_diverse_functions_with_source(functions_with_source, n_apis)

    print(f"\n  Selected {len(selected)} diverse functions with source:")
    for i, (func, source_file) in enumerate(selected):
        print(f"    [{i+1}/{n_apis}] {func.func_name} (source: {source_file.name})")

    print(f"\n  Generating test cases for {len(selected)} functions...")

    results = []
    print(f"  Submitting {len(selected)} tasks to thread pool...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(generate_for_function, func, output_dir, source_file): func
            for func, source_file in selected
        }
        print(f"  Waiting for results...")
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            func = futures[future]
            try:
                result = future.result(timeout=120)
            except concurrent.futures.TimeoutError:
                result = {
                    "library_name": func.library_name,
                    "func_name": func.func_name,
                    "success": False,
                    "source_file": None,
                    "output_file": None,
                    "code": None,
                }
                print(f"    [{i+1}/{len(selected)}] {func.func_name} ... TIMEOUT")
            results.append(result)
            status = "OK" if result["success"] else "FAILED"
            print(f"    [{i+1}/{len(selected)}] {func.func_name} ... {status}")

    return results


def main():
    base_dir = Path(__file__).parent
    manifest_input = base_dir / "manifest.json"

    if not manifest_input.exists():
        print(f"Error: {manifest_input} not found")
        sys.exit(1)

    with open(manifest_input, "r") as f:
        manifest = json.load(f)

    output_dir = base_dir / "output" / "respfuzz"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Regenerating test cases for {len(manifest)} APIs from manifest")

    # Prepare source directories for all libraries
    print("Preparing source code for all libraries...")
    source_dirs = {}
    for library_name in LIBRARIES.keys():
        source_dirs[library_name] = get_source_dir(library_name)

    results = []
    for i, item in enumerate(manifest):
        func_name = item["func_name"]
        library_name = item["library_name"]
        source_file = item.get("source_file")

        print(f"  [{i+1}/{len(manifest)}] {func_name}", end=" ... ")

        if source_file and library_name in source_dirs and source_dirs[library_name]:
            source_dir = source_dirs[library_name]
            # Convert string source_file back to Path for generate_for_function
            source_path = Path(source_file) if source_file else None
            print(f"[has C source] ", end="")
            result = generate_for_function_from_manifest(item, output_dir, source_path, source_dir)
        else:
            print(f"[no C source] ", end="")
            result = generate_for_function_from_manifest(item, output_dir, None, None)

        status = "OK" if result["success"] else "FAILED"
        print(status)
        results.append(result)

    # Summary
    success_count = sum(1 for r in results if r["success"])
    print(f"\n{'='*60}")
    print(f"OVERALL SUMMARY: {success_count}/{len(results)} test cases generated successfully")
    print(f"Output directory: {output_dir}")

    # Write manifest
    manifest_path = output_dir / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()