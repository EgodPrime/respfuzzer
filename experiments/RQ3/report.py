"""
Parse RQ3 experiment logs and generate a LaTeX comparison table.

Table columns: Library | DyFuzz | Fuzz4All | RespFuzzer
Each cell: final line coverage (bits) for that library-fuzzer pair.

Usage:
    uv run experiments/RQ3/report_new.py
"""

import glob
import re
import os
import argparse
from collections import defaultdict

"""
| Library         | Version              |    #Func |      #Line |
| ---             | ---                  |      --- |        --- |
| nltk            | 3.9.2                |      479 |      52257 |
| dask            | 2025.12.0            |      200 |      83910 |
| yaml            | 6.0.3                |       26 |       3614 |
| prophet         | 1.2.1                |       34 |       2672 |
| numpy           | 2.3.4                |      720 |      65714 |
| pandas          | 2.3.3                |      654 |     256586 |
| sklearn         | 1.8.0                |      373 |     120346 |
| scipy           | 1.16.3               |     1001 |     216789 |
| requests        | 2.33.1               |       62 |       2192 |
| spacy           | 3.8.11               |      330 |      39770 |
| torch           | 2.9.1+cpu            |     2005 |     352793 |
| paddle          | 3.2.2                |     6248 |     179161 |
| Total           | -                    |    12132 |    1375804 |
"""

COV_TOTAL_MAP = {
    'nltk': 52257,
    'dask': 83910,
    'pyyaml': 3614,
    'prophet': 2672,
    'numpy': 65714,
    'pandas': 256586,
    'sklearn': 120346,
    'scipy': 216789,
    'requests': 2192,
    'spacy': 39770,
    'torch': 352793,
    'paddle': 179161,
}

LOG_DIR = os.path.join(os.path.dirname(__file__))
COVERAGE_PATTERN = re.compile(r"Current coverage after fuzzing .*?: (\d+) bits")

# Filename pattern: RQ3-{fuzzer}-{library}-{timestamp}.log
FILENAME_PATTERN = re.compile(r"RQ3-([^-]+)-(.+?)-\d{8,12}\.log$")


def extract_final_coverage(log_path: str) -> int | None:
    """Read a log file and return the last coverage value (bits)."""
    last_coverage = None
    with open(log_path, "r") as f:
        for line in f:
            m = COVERAGE_PATTERN.search(line)
            if m:
                last_coverage = int(m.group(1))
    return last_coverage


def parse_log_files(log_dir: str) -> dict[str, dict[str, int]]:
    """
    Scan all RQ3-*.log files, extract fuzzer + library + final coverage.

    Returns nested dict: {fuzzer: {library: coverage}}
    """
    results: dict[str, dict[str, int]] = defaultdict(dict)

    log_files = sorted(glob.glob(os.path.join(log_dir, "RQ3-*.log")))

    for log_path in log_files:
        filename = os.path.basename(log_path)
        m = FILENAME_PATTERN.match(filename)
        if not m:
            print(f"  SKIP (no match): {filename}")
            continue

        fuzzer_raw = m.group(1)  # dyfuzz, fuzz4all, respfuzzer
        library = m.group(2)     # Nltk, Yaml, etc.

        # Normalize fuzzer display name
        fuzzer_map = {
            "dyfuzz": "DyFuzz",
            "fuzz4all": "Fuzz4All",
            "respfuzzer": "RespFuzzer",
        }
        fuzzer_name = fuzzer_map.get(fuzzer_raw, fuzzer_raw)

        # Normalize library name: treat PyYAML and Yaml as the same
        if library in ("PyYAML", "Yaml", "PyYaml", "pyyaml"):
            library = "PyYAML"

        coverage = extract_final_coverage(log_path)
        if coverage is None:
            print(f"  WARN: no coverage found in {filename}")
            continue

        # If duplicate (e.g. both PyYAML and Yaml for same fuzzer), keep the one
        # from the newer log file (files are sorted, so later overwrites).
        results[fuzzer_name][library] = coverage
        print(f"  {fuzzer_name:12s} | {library:12s} -> {coverage:>6d} bits")

    return dict(results)


def cov_percent(coverage: int, lib: str) -> str:
    """Convert coverage to percentage of total lines, formatted as XX.XX%."""
    key = lib.lower()
    total = COV_TOTAL_MAP.get(key)
    if total is None:
        return str(coverage)
    return f"{coverage / total * 100:.2f}\%"


def gen_latex_table(data: dict[str, dict[str, int]], transpose: bool = False) -> str:
    """
    Generate a LaTeX table:
        columns: Library | DyFuzz | Fuzz4All | RespFuzzer
        rows: one per library, sorted alphabetically.

    If transpose=True, swap rows/columns:
        columns: 12 library row coverage counts
        rows: DyFuzz | Fuzz4All | RespFuzzer
    """
    fuzzers = ["DyFuzz", "Fuzz4All", "RespFuzzer"]

    # Collect all libraries across all fuzzers
    all_libs = set()
    for fuzzer_data in data.values():
        all_libs.update(fuzzer_data.keys())
    libs = sorted(all_libs)

    lines = []
    if not transpose:
        lines.append(r"\begin{tabular}{lccc}")
        lines.append(r"\toprule")
        header = r"\textbf{Library} & \textbf{DyFuzz} & \textbf{Fuzz4All} & \textbf{RespFuzzer} \\"
        lines.append(header)
        lines.append(r"\midrule")

        for lib in libs:
            cells = [lib]
            for fuzzer in fuzzers:
                cov = data.get(fuzzer, {}).get(lib, None)
                if cov is not None:
                    cells.append(cov_percent(cov, lib))
                else:
                    cells.append("--")
            lines.append(" & ".join(cells) + r" \\")

        lines.append(r"\bottomrule")
        lines.append(r"\end{tabular}")
    else:
        lines.append(r"\begin{tabular}{lcccccccccccc}")
        lines.append(r"\toprule")
        header = r"\textbf{} & " + " & ".join([r"\textbf{" + lib + "}" for lib in libs]) + r" \\"
        lines.append(header)
        lines.append(r"\midrule")

        for fuzzer in fuzzers:
            cells = [fuzzer]
            for lib in libs:
                cov = data.get(fuzzer, {}).get(lib, None)
                if cov is not None:
                    cells.append(cov_percent(cov, lib))
                else:
                    cells.append("--")
            lines.append(" & ".join(cells) + r" \\")

        lines.append(r"\bottomrule")
        lines.append(r"\end{tabular}")

    return "\n".join(lines)


def gen_markdown_table(data: dict[str, dict[str, int]], transpose: bool = False) -> str:
    """
    Generate a Markdown pipe table, mirroring the structure of gen_latex_table().

    If transpose=False:
        columns: Library | DyFuzz | Fuzz4All | RespFuzzer
        rows: one per library, sorted alphabetically.
    If transpose=True:
        columns: 12 library names
        rows: DyFuzz | Fuzz4All | RespFuzzer
    """
    fuzzers = ["DyFuzz", "Fuzz4All", "RespFuzzer"]

    all_libs = set()
    for fuzzer_data in data.values():
        all_libs.update(fuzzer_data.keys())
    libs = sorted(all_libs)

    lines = []
    if not transpose:
        header = "| " + " | ".join(["Library", "DyFuzz", "Fuzz4All", "RespFuzzer"]) + " |"
        lines.append(header)
        lines.append("| " + " | ".join(["---"] * 4) + " |")

        for lib in libs:
            cells = [lib]
            for fuzzer in fuzzers:
                cov = data.get(fuzzer, {}).get(lib, None)
                if cov is not None:
                    cells.append(cov_percent(cov, lib))
                else:
                    cells.append("--")
            lines.append("| " + " | ".join(cells) + " |")
    else:
        header = "| " + " | ".join([""] + libs) + " |"
        lines.append(header)
        lines.append("| " + " | ".join(["---"] * (len(libs) + 1)) + " |")

        for fuzzer in fuzzers:
            cells = [fuzzer]
            for lib in libs:
                cov = data.get(fuzzer, {}).get(lib, None)
                if cov is not None:
                    cells.append(cov_percent(cov, lib))
                else:
                    cells.append("--")
            lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Parse RQ3 logs and generate LaTeX table.")
    parser.add_argument("-T", action="store_true", help="Transpose the table: rows become 3对照组, columns become 12个库")
    args = parser.parse_args()

    print(f"Scanning logs in: {LOG_DIR}\n")
    data = parse_log_files(LOG_DIR)

    print(f"\n--- Summary ---")
    for fuzzer, lib_data in data.items():
        print(f"  {fuzzer}: {len(lib_data)} libraries")

    latex = gen_latex_table(data, transpose=args.T)
    print(f"\n--- Markdown Table ---")
    print(gen_markdown_table(data, transpose=args.T))
    print(f"\n--- LaTeX Table ---")
    print(latex)


if __name__ == "__main__":
    main()
