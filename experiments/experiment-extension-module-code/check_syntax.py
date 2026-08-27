#!/usr/bin/env python3
"""
Check Python syntax correctness and runtime execution for all .py files
in the output directories (resp, llm_easy, llm_hard).
"""

import ast
import subprocess
import sys
import tempfile
from pathlib import Path


TIMEOUT_SEC = 10


def check_file_syntax(filepath: Path) -> tuple[bool, str]:
    """Check if a Python file has valid syntax. Returns (is_valid, error_msg)."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        ast.parse(content)
        return True, ""
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} at line {e.lineno}"
    except Exception as e:
        return False, f"Error: {str(e)}"


def check_file_runtime(filepath: Path) -> tuple[bool, str]:
    """Run a Python file and check if it executes without errors.
    Returns (success, error_msg).
    """
    try:
        result = subprocess.run(
            ["python", str(filepath)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SEC,
        )
        if result.returncode == 0:
            return True, ""
        else:
            stderr = result.stderr.strip()
            if not stderr:
                stderr = f"Exit code: {result.returncode}"
            return False, stderr
    except subprocess.TimeoutExpired:
        return False, f"Timeout after {TIMEOUT_SEC}s"
    except Exception as e:
        return False, f"Runtime error: {str(e)}"


def check_directory(dir_path: Path) -> dict:
    """Check all .py files in a directory and return statistics."""
    if not dir_path.exists():
        return {"error": f"Directory {dir_path} does not exist"}

    py_files = list(dir_path.glob("*.py"))

    results = {
        "total": len(py_files),
        "syntax_valid": 0,
        "syntax_invalid": 0,
        "runtime_ok": 0,
        "runtime_error": 0,
        "files": [],
    }

    for py_file in sorted(py_files):
        # Syntax check
        syntax_ok, syntax_error = check_file_syntax(py_file)

        # Runtime check (only if syntax is valid)
        runtime_ok, runtime_error = False, ""
        if syntax_ok:
            runtime_ok, runtime_error = check_file_runtime(py_file)

        status = "OK" if (syntax_ok and runtime_ok) else "FAIL"
        results["files"].append({
            "filename": py_file.name,
            "syntax_ok": syntax_ok,
            "syntax_error": syntax_error if not syntax_ok else None,
            "runtime_ok": runtime_ok if syntax_ok else "SKIP",
            "runtime_error": runtime_error if not runtime_ok and syntax_ok else None,
            "status": status,
        })

        if syntax_ok:
            results["syntax_valid"] += 1
        else:
            results["syntax_invalid"] += 1

        if syntax_ok and runtime_ok:
            results["runtime_ok"] += 1
        elif syntax_ok:
            results["runtime_error"] += 1

    if results["total"] > 0:
        results["syntax_rate"] = results["syntax_valid"] / results["total"] * 100
        # Runtime rate based on syntax-valid files
        valid_files = results["syntax_valid"]
        if valid_files > 0:
            results["runtime_rate"] = results["runtime_ok"] / valid_files * 100
        else:
            results["runtime_rate"] = 0.0
    else:
        results["syntax_rate"] = 0.0
        results["runtime_rate"] = 0.0

    return results


def main():
    base_dir = Path(__file__).parent / "output"

    directories = ["respfuzz", "llm_easy", "llm_hard", "respfuzz_with_source"]

    print(f"{'='*80}")
    print(f"{'Directory':<15} {'Total':>8} {'Syntax':>12} {'Runtime':>12} {'Rate':>12}")
    print(f"{'='*80}")

    all_stats = {}

    for dir_name in directories:
        dir_path = base_dir / dir_name
        stats = check_directory(dir_path)

        if "error" in stats:
            print(f"{dir_name:<15} {stats['error']}")
            all_stats[dir_name] = stats
        else:
            syntax_str = f"{stats['syntax_valid']}/{stats['total']}"
            runtime_str = f"{stats['runtime_ok']}/{stats['syntax_valid']}"
            print(f"{dir_name:<15} {stats['total']:>8} {syntax_str:>12} {runtime_str:>12}")
            all_stats[dir_name] = stats

    print(f"{'='*80}")

    # Show details for failed files
    print("\n" + "=" * 80)
    print("FAILED FILES DETAILS:")
    print("=" * 80)

    has_failed = False
    for dir_name in directories:
        stats = all_stats[dir_name]
        if "files" in stats:
            failed_files = [f for f in stats["files"] if f["status"] == "FAIL"]
            if failed_files:
                has_failed = True
                print(f"\n[{dir_name}]")
                for f in failed_files:
                    print(f"  - {f['filename']}")
                    if f["syntax_error"]:
                        print(f"      Syntax: {f['syntax_error']}")
                    if f["runtime_error"]:
                        print(f"      Runtime: {f['runtime_error']}")

    if not has_failed:
        print("\nAll files passed syntax and runtime checks!")

    # Summary comparison
    print("\n" + "=" * 80)
    print("SUMMARY COMPARISON:")
    print("=" * 80)
    print(f"{'Dir':<15} {'Syntax (ok/total)':>20} {'Runtime (ok/valid)':>20}")
    print("-" * 55)
    for dir_name in directories:
        stats = all_stats[dir_name]
        if "total" in stats and stats["total"] > 0:
            print(f"{dir_name:<15} {stats['syntax_valid']:>10}/{stats['total']:<9} {stats['runtime_ok']:>10}/{stats['syntax_valid']:<9}")


if __name__ == "__main__":
    main()
