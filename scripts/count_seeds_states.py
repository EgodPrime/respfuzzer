#!/usr/bin/env python3
"""
Count the theoretical line-coverage ceiling for the APIs listed in a seeds
JSON, using coverage.py's statement analysis (``num_statements``).

Unlike ``count_seeds_loc.py`` (raw non-blank / non-comment LOC), the number
produced here uses the same definition as coverage.py reports, so it is
directly comparable with the coverage totals used by the RQ reports
(e.g. ``experiments/RQ2/report.py`` COV_TOTAL_MAP).

Files are located with the same logic as ``count_seeds_loc.py`` (deduped,
restricted to ``.py`` files inside the library package), then parsed
statically via ``Coverage.analysis2`` — no code is executed.

Usage:
    python scripts/count_seeds_states.py -f <seeds.json>

Options:
    -f, --function_file   Path to the seeds JSON file (required)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coverage  # noqa: E402

from count_seeds_loc import get_associated_files, get_function_paths  # noqa: E402


def count_statements(file_paths) -> int:
    """Static coverage.py statement count for each file (no execution)."""
    # config_file=False: ignore any .coveragerc in the cwd.
    cov = coverage.Coverage(config_file=False)
    total = 0
    for file_path in file_paths:
        try:
            _src, statements, _excluded, _missing, _excluded_lines = cov.analysis2(
                file_path
            )
        except Exception:
            # Unparsable / non-Python file: skip, same tolerance as LOC count.
            continue
        total += len(statements)
    return total


def main():
    parser = argparse.ArgumentParser(
        description="Count coverage.py num_statements for seed APIs."
    )
    parser.add_argument(
        "-f",
        "--function_file",
        required=True,
        help="Path to the JSON file containing the seed list.",
    )
    args = parser.parse_args()

    lib_name, func_paths = get_function_paths(args.function_file)
    if not lib_name:
        sys.exit(1)

    files = get_associated_files(lib_name, func_paths)
    total_statements = count_statements(files)
    print(f"Total Statements: {total_statements}, Number of Files: {len(files)}")


if __name__ == "__main__":
    main()
