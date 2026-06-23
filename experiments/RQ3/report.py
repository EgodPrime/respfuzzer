"""
Parse RQ3 experiment logs and generate a Markdown comparison table.

Rows: 3 fuzzers (DyFuzz / Fuzz4All / RespFuzzer)
Cols: 12 libraries (coverage %) + Average + #Time

Multiple log files per (fuzzer, library) are automatically averaged.
"""

import glob
import re
import os
import datetime
from collections import defaultdict

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

DISPLAY_ORDER = ["NLTK", "Dask", "PyYAML", "Prophet", "Numpy", "Pandas",
                 "Scikit-learn", "Scipy", "Requests", "spaCy", "PyTorch", "Paddle"]

DISPLAY_TO_COV = {
    "NLTK": "nltk", "Dask": "dask", "PyYAML": "pyyaml", "Prophet": "prophet",
    "Numpy": "numpy", "Pandas": "pandas", "Scikit-learn": "sklearn",
    "Scipy": "scipy", "Requests": "requests", "spaCy": "spacy",
    "PyTorch": "torch", "Paddle": "paddle",
}

LIB_TO_COV_KEY = {
    "nltk": "NLTK", "dask": "Dask", "pyyaml": "PyYAML", "prophet": "Prophet",
    "numpy": "Numpy", "pandas": "Pandas", "sklearn": "Scikit-learn",
    "scipy": "Scipy", "requests": "Requests", "spacy": "spaCy",
    "torch": "PyTorch", "pytorch": "PyTorch", "paddle": "Paddle",
    "NLTK": "NLTK", "Dask": "Dask", "Yaml": "PyYAML", "PyYAML": "PyYAML",
    "Prophet": "Prophet", "Numpy": "Numpy", "Pandas": "Pandas",
    "Sklearn": "Scikit-learn", "Scikit-learn": "Scikit-learn",
    "Scipy": "Scipy", "Requests": "Requests", "spaCy": "spaCy",
    "SpaCy": "spaCy", "Torch": "PyTorch", "PyTorch": "PyTorch",
    "Paddle": "Paddle",
    "Nltk": "NLTK", "Spacy": "spaCy",
    "yaml": "PyYAML",
}

# Display name → COV_TOTAL_MAP key (for percentage calculation)
DISPLAY_TO_COV_KEY = {
    "NLTK": "nltk", "Dask": "dask", "PyYAML": "pyyaml", "Prophet": "prophet",
    "Numpy": "numpy", "Pandas": "pandas", "Scikit-learn": "sklearn",
    "Scipy": "scipy", "Requests": "requests", "spaCy": "spacy",
    "PyTorch": "torch", "Paddle": "paddle",
}

LOG_DIR = os.path.join(os.path.dirname(__file__))
COVERAGE_PATTERN = re.compile(r"Current coverage after fuzzing .*?: (\d+) bits")

# Filename pattern: RQ3-{fuzzer}-{library}-{timestamp}.log
FILENAME_PATTERN = re.compile(r"RQ3-([^-]+)-(.+?)-\d{8,12}\.log$")
TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}")


def extract_final_coverage_and_time(log_path: str):
    """Read a log file and return (last_coverage, elapsed_seconds)."""
    last_coverage = None
    first_ts = None
    last_ts = None
    fmt = "%Y-%m-%d %H:%M:%S.%f"

    with open(log_path, "r") as f:
        for line in f:
            m = TIME_PATTERN.match(line)
            if m:
                try:
                    ts = datetime.datetime.strptime(line[:23], fmt)
                except ValueError:
                    pass
                else:
                    if first_ts is None:
                        first_ts = ts
                    last_ts = ts

            cov_m = COVERAGE_PATTERN.search(line)
            if cov_m:
                last_coverage = int(cov_m.group(1))

    elapsed = None
    if first_ts and last_ts:
        elapsed = round((last_ts - first_ts).total_seconds())

    return last_coverage, elapsed


def parse_log_files(log_dir: str) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, int]]]:
    """
    Scan all RQ3-*.log files, extract fuzzer + library + final coverage + elapsed time.

    Multiple log files per (fuzzer, library) are automatically averaged.

    Returns (coverage_data, time_data):
      coverage_data: {fuzzer: {library: avg_coverage}}  (float for averaging)
      time_data:      {fuzzer: {library: avg_elapsed_seconds}}  (int)
    """
    # Collect raw values per (fuzzer, library) for averaging
    cov_accum: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    time_accum: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

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

        # Normalize library name to canonical display form
        library = LIB_TO_COV_KEY.get(library, library)

        coverage, elapsed = extract_final_coverage_and_time(log_path)
        if coverage is None:
            print(f"  WARN: no coverage found in {filename}")
            continue

        cov_accum[fuzzer_name][library].append(coverage)
        if elapsed is not None:
            time_accum[fuzzer_name][library].append(elapsed)
        print(f"  {fuzzer_name:12s} | {library:12s} -> cov={coverage:>6d}  time={elapsed}s")

    # Average across multiple logs per (fuzzer, library)
    coverage_results: dict[str, dict[str, float]] = {}
    time_results: dict[str, dict[str, int]] = {}

    for fuzzer in cov_accum:
        coverage_results[fuzzer] = {}
        time_results[fuzzer] = {}
        for lib, covs in cov_accum[fuzzer].items():
            avg_cov = sum(covs) / len(covs)
            coverage_results[fuzzer][lib] = avg_cov
            if fuzzer in time_accum and lib in time_accum[fuzzer]:
                times = time_accum[fuzzer][lib]
                time_results[fuzzer][lib] = round(sum(times) / len(times))
            else:
                time_results[fuzzer][lib] = 0

    # Print summary of averaged values
    for fuzzer in coverage_results:
        for lib, cov in coverage_results[fuzzer].items():
            t = time_results[fuzzer].get(lib, 0)
            print(f"  [AVG] {fuzzer:12s} | {lib:12s} -> cov={cov:>8.1f}  time={t}s")

    return coverage_results, time_results


def cov_percent(coverage: float, lib: str) -> str:
    """Convert coverage to percentage of total lines, formatted as XX.XX%."""
    key = DISPLAY_TO_COV_KEY.get(lib, lib).lower()
    total = COV_TOTAL_MAP.get(key)
    if total is None:
        return f"{coverage:.1f}"
    return f"{coverage / total * 100:.2f}%"


def gen_markdown_table(coverage_data: dict[str, dict[str, float]],
                        time_data: dict[str, dict[str, int]]) -> str:
    """
    Generate a Markdown pipe table:
        rows: DyFuzz | Fuzz4All | RespFuzzer
        cols: 12 library names + Average + #Time
    """
    fuzzers = ["DyFuzz", "Fuzz4All", "RespFuzzer"]

    libs = DISPLAY_ORDER

    lines = []
    header = "| " + " | ".join([""] + libs + ["Average", "#Time"]) + " |"
    lines.append(header)
    lines.append("| " + " | ".join(["---"] * (len(libs) + 2)) + " |")

    for fuzzer in fuzzers:
        cells = [fuzzer]
        row_vals = []
        for lib in libs:
            cov = coverage_data.get(fuzzer, {}).get(lib, None)
            if cov is not None:
                pct = cov_percent(cov, lib)
                cells.append(pct)
                try:
                    row_vals.append(float(pct.rstrip("%")))
                except ValueError:
                    pass
            else:
                cells.append("--")
        # Average column
        if row_vals:
            avg = sum(row_vals) / len(row_vals)
            cells.append(f"{avg:.2f}%")
        else:
            cells.append("--")
        # #Time column
        td = time_data.get(fuzzer, {})
        total_time = sum(td.get(lib, 0) for lib in libs)
        cells.append(str(total_time))
        lines.append("| " + " | ".join(cells) + " |")

    return "\n".join(lines)


def main():
    print(f"Scanning logs in: {LOG_DIR}\n")
    data, time_data = parse_log_files(LOG_DIR)

    print(f"\n--- Summary ---")
    for fuzzer, lib_data in data.items():
        print(f"  {fuzzer}: {len(lib_data)} libraries")

    print(f"\n--- Markdown Table ---")
    print(gen_markdown_table(data, time_data))


if __name__ == "__main__":
    main()
