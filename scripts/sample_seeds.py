#!/usr/bin/env python3
"""
Sample seeds from a JSON file with configurable ratio and count bounds.

Usage:
    python scripts/sample_seeds.py -i <input.json> [options]

Options:
    -i, --input      Path to input xx_seeds.json file (required)
    -o, --output     Output directory (default: same directory as input)
    -r, --ratio      Sampling ratio in [0, 1.0] (default: 1.0)
    -m, --min        Minimum number of samples (default: 0)
    -M, --max        Maximum number of samples (default: no limit)

Final sample count = min(M, max(m, floor(total * r)))
"""

import argparse
import json
import math
import os
import random
import sys


def parse_args():
    parser = argparse.ArgumentParser(description="Sample seeds from a JSON file.")
    parser.add_argument("-i", "--input", required=True, help="Path to input xx_seeds.json")
    parser.add_argument("-o", "--output", help="Output directory (default: same as input)")
    parser.add_argument("-r", "--ratio", type=float, default=1.0,
                        help="Sampling ratio in [0, 1.0] (default: 1.0)")
    parser.add_argument("-m", "--min", type=int, default=0,
                        help="Minimum number of samples (default: 0)")
    parser.add_argument("-M", "--max", type=int, default=None,
                        help="Maximum number of samples (default: no limit)")
    parser.add_argument("-s", "--seed", type=int, default=4399,
                        help="Random seed for reproducibility (default: 4399)")
    return parser.parse_args()


def main():
    args = parse_args()

    # Validate ratio
    if not (0.0 <= args.ratio <= 1.0):
        print(f"ERROR: ratio must be in [0, 1.0], got {args.ratio}", file=sys.stderr)
        sys.exit(1)

    # Load input data
    input_path = os.path.expanduser(args.input)
    if not os.path.isfile(input_path):
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    total_count = len(data)
    print(f"Total records: {total_count}")

    # Compute sampled count
    raw_count = total_count * args.ratio
    sampled_count_raw = math.floor(raw_count)
    print(f"Raw sampled count (floor of total * ratio): {sampled_count_raw}")

    # Apply min constraint
    sampled_count = max(args.min, sampled_count_raw)
    print(f"After applying min({args.min}): {sampled_count}")

    # Apply max constraint (if specified)
    if args.max is not None:
        if args.max < 0:
            print(f"ERROR: max must be non-negative, got {args.max}", file=sys.stderr)
            sys.exit(1)
        sampled_count = min(args.max, sampled_count)
        print(f"After applying max({args.max}): {sampled_count}")

    print(f"Final sample count: {sampled_count}")

    random.seed(args.seed)
    print(f"Random seed: {args.seed}")

    # Perform sampling
    if sampled_count >= total_count:
        sampled = data
        print("Sample size >= total count, keeping all records.")
    else:
        sampled = random.sample(data, sampled_count)

    # Determine output path
    if args.output:
        output_dir = os.path.expanduser(args.output)
    else:
        output_dir = os.path.dirname(os.path.abspath(input_path))

    os.makedirs(output_dir, exist_ok=True)

    input_basename = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"{input_basename}_sampled.json")

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sampled, f, indent=2, ensure_ascii=False)

    print(f"Output written to: {output_path}")
    print(f"Records written: {len(sampled)}")


if __name__ == "__main__":
    main()
