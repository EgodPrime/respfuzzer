#!/usr/bin/env python3
"""
Generate RQ2 miniFuzz4All seed datasets.

Two artifacts per measured library, both derived from RQ1's Llama/respfuzzer
seeds (which carry real ``function_call`` strings -- the Fuzz4AllMutator
mutation starting point):

- ``<lib>_all_functions_seeds.json``  : every unique function (dedup by
  ``func_path`` across rs1..rs5). Reference full-coverage set.
- ``<lib>_capped100_seeds.json``      : random sample of CAP seeds per library
  (fixed RNG seed -> reproducible). Used by RQ2's two budget runs so the
  budgets are compared on an identical dataset.

Usage:
    uv run python experiments/RQ2/generate_rq2_seeds.py
Outputs are written next to this script in ``experiments/RQ2/`` (alongside
the per-run ``rq2_*.log`` / ``*_checkpoint.json``), not into ``run_data/``.
"""

import json
import random
from pathlib import Path

LIBS = ["nltk", "numpy", "pandas", "scipy", "sklearn", "spacy"]
MODEL = "Llama-3.1-8B-Instruct"
MODE = "respfuzzer"
CAP = 100
CAP_SEED = 4399  # fixed -> reproducible random sample per library
_HERE = Path(__file__).resolve().parent
REPO_ROOT = _HERE.parents[1]
OUT_DIR = _HERE  # seed datasets live in experiments/RQ2/ next to this script
DATA_SRC = REPO_ROOT / "data"  # RQ1 seeds


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    seeds_all: dict[str, list[dict]] = {}
    for lib in LIBS:
        seen: set[str] = set()
        seeds: list[dict] = []
        for rep in range(1, 6):
            src = DATA_SRC / MODE / f"{lib}_seeds_{MODEL}_rs{rep}.json"
            if not src.exists():
                raise FileNotFoundError(f"missing RQ1 seeds: {src}")
            with open(src, "r", encoding="utf-8") as f:
                data = json.load(f)
            for s in data:
                key = s["func_path"]
                if key not in seen:
                    seen.add(key)
                    seeds.append(s)
        seeds.sort(key=lambda s: s["func_path"])
        seeds_all[lib] = seeds
        with open(OUT_DIR / f"{lib}_all_functions_seeds.json", "w") as f:
            json.dump(seeds, f, ensure_ascii=False, indent=2)
        print(f"{lib}: {len(seeds):>4} unique functions -> all_functions")

    # capped random sample per library (fixed RNG -> reproducible)
    rng = random.Random(CAP_SEED)
    for lib in LIBS:
        pop = seeds_all[lib]
        sample = rng.sample(pop, min(CAP, len(pop)))
        out = OUT_DIR / f"{lib}_capped100_seeds.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(sample, f, ensure_ascii=False, indent=2)
        print(f"{lib}: {len(pop):>4} -> {len(sample):>3} random capped -> capped100")


if __name__ == "__main__":
    main()
