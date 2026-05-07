#!/usr/bin/env python3
"""
Convert a seeds JSON file to DyFuzz format.

DyFuzz format:
    {lib_name: {func_name: {"pn": [N, N]}}}

where N = total number of arguments in the function.

Usage:
    python scripts/convert_to_dyfuzz_format.py -i <input_seeds.json> [-o <output_dir>]
"""

import argparse
import json
import os
import sys


def parse_args():
    parser = argparse.ArgumentParser(description="Convert seeds JSON to DyFuzz format.")
    parser.add_argument("-i", "--input", required=True, help="Path to input xx_seeds.json")
    parser.add_argument("-o", "--output", help="Output directory (default: same as input)")
    return parser.parse_args()


def seeds_to_dyfuzz(seeds):
    """
    Convert a list of seed dicts to DyFuzz format grouped by library.

    DyFuzz format: {lib_name: {func_name: {"pn": [N, N]}}}
    where N = total number of args (positional + keyword).
    """
    dyfuzz = {}
    for entry in seeds:
        lib = entry["library_name"]
        # func_name in DyFuzz format is stripped of the library prefix
        full_func = entry["func_name"]
        if full_func.startswith(f"{lib}."):
            func = full_func[len(lib) + 1:]
        else:
            func = full_func
        total_args = len(entry["args"])
        pn = [total_args, total_args]

        dyfuzz.setdefault(lib, {})[func] = {"pn": pn}

    return dyfuzz


def main():
    args = parse_args()

    input_path = os.path.expanduser(args.input)
    if not os.path.isfile(input_path):
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        seeds = json.load(f)

    if not isinstance(seeds, list):
        print(f"ERROR: expected a JSON list, got {type(seeds).__name__}", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(seeds)} seed entries from {input_path}")

    dyfuzz = seeds_to_dyfuzz(seeds)
    print(f"DyFuzz entries: {len(dyfuzz)} libraries, "
          f"{sum(len(v) for v in dyfuzz.values())} functions")

    # Determine output path
    if args.output:
        output_dir = os.path.expanduser(args.output)
    else:
        output_dir = os.path.dirname(os.path.abspath(input_path))

    os.makedirs(output_dir, exist_ok=True)

    input_basename = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"{input_basename}_dyfuzz.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dyfuzz, f, indent=2, ensure_ascii=False)

    print(f"Output written to: {output_path}")


if __name__ == "__main__":
    main()
