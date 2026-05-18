#!/usr/bin/env python3
"""
Sample seeds from a JSON file with a configurable target count.

Usage:
    python scripts/sample_seeds.py -i <input.json> [options]

Options:
    -i, --input    Path to input xx_seeds.json file (required)
    -o, --output   Output directory (default: same directory as input)
    -n, --num      Target sample count. If >= total, take all. (default: 50)
    -s, --seed     Random seed for reproducibility (default: 4399)

Final sample count = min(n, len(data))
"""

import argparse
import json
import os
import random
import sys


def parse_args():
    parser = argparse.ArgumentParser(description="Sample seeds from a JSON file.")
    parser.add_argument("-i", "--input", required=True, help="Path to input xx_seeds.json")
    parser.add_argument("-o", "--output", help="Output directory (default: same as input)")
    parser.add_argument("-n", "--num", type=int, default=50,
                        help="Target sample count. If >= total, take all. (default: 50)")
    parser.add_argument("-s", "--seed", type=int, default=4399,
                        help="Random seed for reproducibility (default: 4399)")
    return parser.parse_args()


def main():
    args = parse_args()

    # Validate num
    if args.num < 0:
        print(f"ERROR: num must be non-negative, got {args.num}", file=sys.stderr)
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
    sampled_count = min(args.num, total_count)
    print(f"Target (-n): {args.num}, Final sample count: {sampled_count}")

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
