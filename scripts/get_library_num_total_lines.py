#!/usr/bin/env python3
"""
Get the theoretical executable line count (num_statements) for one or more libraries.

Usage:
    python get_library_num_total_lines.py numpy dask yaml

Output example:
    numpy: 123456 lines
    dask: 67890 lines
    yaml: 4321 lines

The theoretical executable lines are determined via static analysis of each
library's source files using coverage.py. This is the same approach used by
fuzz_exp_new.py's _get_covered_line_count(), but here we expose the
`num_statements` (total denominator) instead of `covered_lines` (numerator).
"""

import argparse
import importlib
import json
import os
import tempfile
import sys

import coverage


def get_library_num_statements(lib_name: str) -> int | None:
    """
    Use coverage.py to statically determine the theoretical executable line
    count (num_statements) for the given library.

    Returns None if the library cannot be imported.
    """
    # Create a temporary directory to hold the .coverage data file so we don't
    # pollute the working directory.
    with tempfile.TemporaryDirectory() as tmpdir:
        cov_file = os.path.join(tmpdir, f"{lib_name}.coverage")

        try:
            # Import the library to make coverage.py discover all its files.
            # swallow stderr to suppress import-time noise from the library.
            import io
            fake_stderr = io.StringIO()
            old_stderr = sys.stderr
            sys.stderr = fake_stderr
            try:
                importlib.import_module(lib_name)
            finally:
                sys.stderr = old_stderr

            # coverage.py needs at least one DataHandler to compute the report,
            # so we create a minimal Coverage instance with source_pkgs.
            cov = coverage.Coverage(source_pkgs=[lib_name], data_file=cov_file)
            cov.start()
            # By importing the library above, its modules are already loaded.
            # We stop immediately and save, then generate the report.
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Get theoretical executable line count for one or more Python libraries.",
    )
    parser.add_argument(
        "libraries",
        nargs="+",
        help="Library name(s) to analyse (e.g. numpy, dask, yaml).",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Only print numbers, no library names.",
    )
    args = parser.parse_args()

    results: dict[str, int] = {}
    for lib_name in args.libraries:
        n = get_library_num_statements(lib_name)
        if n is not None:
            results[lib_name] = n

    if not results:
        sys.exit(1)

    for lib_name, nlines in results.items():
        if args.quiet:
            print(nlines)
        else:
            print(f"{lib_name}: {nlines} lines")

    # Exit 0 if at least one library succeeded.
    sys.exit(0)


if __name__ == "__main__":
    main()