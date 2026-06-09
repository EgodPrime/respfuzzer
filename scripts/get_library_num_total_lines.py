#!/usr/bin/env python3
"""
Generate a Markdown table of library statistics for RQ2.

For each library in libraries.conf:
  - Library  : name extracted from filename (e.g. numpy_functions.json → numpy)
  - Version  : via `uv pip list`
  - #Func    : number of function records in RQ2_data_common/{lib}_functions.json
  - #Line    : total source lines across all those functions

Requires: uv, coverage (for line counting via coverage.py)
"""

import coverage
import importlib
import json
import os
import tempfile
import re
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "libraries.conf"
DATA_DIR = SCRIPT_DIR / ".." / "RQ2_data_common"

# Maps library name (in functions.json) to pip package name (for version lookup).
PIP_NAME_MAP = {
    "sklearn": "scikit-learn",
    "paddle": "paddlepaddle",
    "yaml": "pyyaml",
}

PAPER_NAME_MAP = {
    'nltk': 'NLTK',
    'dask': 'Dask',
    'yaml': 'PyYAML',
    'prophet': 'Prophet',
    'numpy': 'NumPy',
    'pandas': 'Pandas',
    'sklearn': 'Scikit-learn',
    'scipy': 'SciPy',
    'requests': 'Requests',
    'spacy': 'spaCy',
    'torch': 'PyTorch',
    'paddle': 'PaddlePaddle',
}


def load_libraries() -> list[str]:
    """Parse libraries.conf and return the list of library names."""
    text = CONFIG_FILE.read_text()
    # Extract strings inside double-quotes from lines that look like bash array entries.
    libs = re.findall(r'"(\w+)"', text)
    return libs


def get_version(lib_name: str) -> str:
    """Query `uv pip list` for the installed version of lib_name."""
    pip_name = PIP_NAME_MAP.get(lib_name, lib_name)
    try:
        result = subprocess.run(
            ["uv", "pip", "list"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return "N/A"

    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == pip_name:
            return parts[1]
    return "N/A"


def get_library_num_statements(lib_name: str) -> int | None:
    """
    Use coverage.py to statically determine the theoretical executable line
    count (num_statements) for the given library.
    Returns None if the library cannot be imported.
    """
    import sys
    import io

    with tempfile.TemporaryDirectory() as tmpdir:
        cov_file = os.path.join(tmpdir, f"{lib_name}.coverage")

        try:
            fake_stderr = io.StringIO()
            old_stderr = sys.stderr
            sys.stderr = fake_stderr
            try:
                importlib.import_module(lib_name)
            finally:
                sys.stderr = old_stderr

            cov = coverage.Coverage(source_pkgs=[lib_name], data_file=cov_file)
            cov.start()
            cov.stop()
            cov.save()
        except Exception as e:
            print(f"[{lib_name}] Failed to analyse: {e}", file=sys.stderr)
            return None

        try:
            cov = coverage.Coverage(data_file=cov_file)
            cov.load()
            with tempfile.NamedTemporaryFile(suffix=".json", delete=True, mode="w") as tf:
                tmp_path = tf.name
            cov.json_report(outfile=tmp_path)
            with open(tmp_path) as f:
                data = json.load(f)
            return data.get("totals", {}).get("num_statements", 0)
        except Exception as e:
            print(f"[{lib_name}] Failed to generate report: {e}", file=sys.stderr)
            return None


def get_stats(lib_name: str) -> tuple[int, int]:
    """Return (#func, #line) by reading RQ2_data_common/{lib}_functions.json
    and using coverage.py to count executable statements for #Line.
    """
    json_path = DATA_DIR / f"{lib_name}_functions.json"
    if not json_path.exists():
        return 0, 0

    try:
        data = json.loads(json_path.read_text())
    except Exception:
        return 0, 0

    num_funcs = len(data)

    # Use coverage.py to get num_statements for the library.
    num_statements = get_library_num_statements(lib_name)
    if num_statements is None:
        num_statements = 0

    return num_funcs, num_statements


def print_markdown_table(libraries: list[str], rows: list[dict]) -> None:
    print(f"| {'Library':<15} | {'Version':<20} | {'#Func':>8} | {'#Line':>10} |")
    print(f"| {'---':<15} | {'---':<20} | {'---':>8} | {'---':>10} |")
    for r in rows:
        print(f"| {r['lib_name']:<15} | {r['version']:<20} | {r['num_funcs']:>8} | {r['num_lines']:>10} |")


def print_latex_table(libraries: list[str], rows: list[dict]) -> None:
    col = "l" + "r" * 3
    lines = [
        r"\begin{table}[htbp]",
        r"  \centering",
        r"  \begin{tabular}{@{} " + col + r" @{} }",
        r"    \hline",
        ("    \\textbf{Library} & \\textbf{Version} & "
         "\\textbf{\\#Func} & \\textbf{\\#Line} \\"),
        r"    \hline",
    ]
    for r in rows:
        lines.append(
            rf"    {r['lib_name']} & {r['version']} & "
            rf"{r['num_funcs']:,} & {r['num_lines']:,} \\"
        )
    lines += [
        r"    \hline",
        r"  \end{tabular}",
        r"\end{table}",
    ]
    print("\n" + "\n".join(lines))


def main() -> None:
    libraries = load_libraries()
    rows = []
    for lib_name in libraries:
        version = get_version(lib_name)
        num_funcs, num_lines = get_stats(lib_name)
        rows.append(
            {
                "lib_name": PAPER_NAME_MAP.get(lib_name, lib_name),
                "version": version,
                "num_funcs": num_funcs,
                "num_lines": num_lines,
            }
        )
    rows.append(
        {
            "lib_name": "Total",
            "version": "-",
            "num_funcs": sum(r["num_funcs"] for r in rows),
            "num_lines": sum(r["num_lines"] for r in rows),
        }
    )
    print_markdown_table(libraries, rows)
    print_latex_table(libraries, rows)


if __name__ == "__main__":
    main()