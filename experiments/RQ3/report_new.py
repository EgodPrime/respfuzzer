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
from collections import defaultdict

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


def gen_latex_table(data: dict[str, dict[str, int]]) -> str:
    """
    Generate a LaTeX table:
        columns: Library | DyFuzz | Fuzz4All | RespFuzzer
        rows: one per library, sorted alphabetically.
    """
    fuzzers = ["DyFuzz", "Fuzz4All", "RespFuzzer"]

    # Collect all libraries across all fuzzers
    all_libs = set()
    for fuzzer_data in data.values():
        all_libs.update(fuzzer_data.keys())
    libs = sorted(all_libs)

    lines = []
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
                cells.append(str(cov))
            else:
                cells.append("--")
        lines.append(" & ".join(cells) + r" \\")

    lines.append(r"\bottomrule")
    lines.append(r"\end{tabular}")

    return "\n".join(lines)


def main():
    print(f"Scanning logs in: {LOG_DIR}\n")
    data = parse_log_files(LOG_DIR)

    print(f"\n--- Summary ---")
    for fuzzer, lib_data in data.items():
        print(f"  {fuzzer}: {len(lib_data)} libraries")

    latex = gen_latex_table(data)
    print(f"\n--- LaTeX Table ---")
    print(latex)


if __name__ == "__main__":
    main()
