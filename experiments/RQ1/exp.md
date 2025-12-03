# RQ1 experiment

> Note: we use {RESPFUZZER} to denote the root directory of the RespFuzzer repo.

## Purpose

This experiment generates example programs for libraries/APIs using a large
language model (LLM). The generated examples are saved as Python files and
later used for instrumentation and coverage analysis in downstream RQ2/RQ3
experiments.

## Requirements

- Python 3.13
- `httpx` (async) — install with `pip install httpx`
- A running OpenAI-compatible chat completions endpoint or API key.


## Generate tests by library

This mode asks the LLM to produce example programs for whole libraries (one
sample per request by default).

Usage example:

```bash
cd {RESPFUZZER}
export OPENAI_API_KEY="sk-..."
python3 experiments/RQ1/LLM_testcases/generate_tests_from_apis.py \
    --input experiments/RQ1/LLM_testcases/api_list.json \
    --outdir experiments/RQ1/generated_by_lib \
    --parallel 50 \
    --count-per-lib 20 \
```

Notes:
- `--parallel` controls concurrent requests. Adjust to your API rate limits.
- `--count-per-lib` controls how many examples to generate per library (or see
  the input JSON `count` field for per-library overrides).

## Generate tests by specific API

This mode prompts the LLM for examples of a specific API symbol (useful when
you want per-API tests).

Usage example:

```bash
python3 experiments/RQ1/LLM_testcases/generate_tests_from_apis_by_api.py \
    --input experiments/RQ1/LLM_testcases/api_list.json \
    --outdir experiments/RQ1/generated_by_api \
    --parallel 50 \
    --count-per-api 3 \
    --model yb \
    --api-base <http://127.0.0.1:8021/v1/chat/completions> ## llm_url
```

Output layout
- Files are written under `--outdir/<library>/` with names like
  `<library>__<api>_001.py` (or `<library>_001.py` for the library-level mode).


## Post-generation: instrumentation check (coverage)

After generation, use the coverage tools to compile, instrument and run the
generated seeds. The script `experiments/RQ1/coverage/check_instrument_calls_fixed.py`
performs instrumentation-checks and writes a JSON report of called APIs.

Example:

```bash
# default behavior uses repository-relative defaults; override with flags
python3 experiments/RQ1/coverage/check_instrument_calls_fixed.py \
    --base experiments/RQ1/generated_by_api/<library> \
    --apis-json experiments/RQ1/LLM_testcases/api_list.json \
    --out experiments/RQ1/instrument_check_report.json \
    --timeout 10 --max-seeds 200
```

Notes:
- The script will instrument listed APIs and run each seed (in-process by
  default). If a seed times out in-process, it will be retried in a subprocess.
- Use `--skip-interactive` to filter seeds that appear to wait for input or
  signals (helpful to skip long-running interactive examples).


## Script: `check_instrument_calls_fixed.py` (detailed)

Purpose
- Instruments API targets and runs generated seeds to collect which APIs are
  actually called. Produces a JSON report summarizing per-seed results and
  aggregate API call counts.

Key flags and behavior
- `--base / -b`: base directory containing Python seed files. Defaults to a
  repository-relative path (`experiments/RQ2/generated_tests_bigrun`) when not
  provided.
- `--apis-json / -a`: path to the APIs JSON used for instrumentation.
- `--out / -o`: output JSON report path. Defaults to `experiments/RQ2/instrument_check_report.json` in the repo.
- `--timeout`: per-seed execution timeout (seconds). Default `10`.
- `--workers / -j`: number of parallel subprocess workers (for subprocess retries).
- `--skip-interactive`: skip seeds that appear interactive (heuristic keyword
  scan).
- `--start-index` / `--end-index` / `--max-seeds`: slicing and limit options
  for partial runs or resuming.

Example: run instrument check on a library folder (repo-relative defaults used)

```bash
PYTHONPATH=$(pwd)/src python3 experiments/RQ1/coverage/check_instrument_calls_fixed.py \
    --base experiments/RQ1/generated_by_api/numpy \
    --apis-json experiments/RQ1/LLM_testcases/api_list.json \
    --out experiments/RQ1/instrument_check_report.json \
    --timeout 8 --max-seeds 100
```

Output
- The script writes a JSON report (see `--out`) with structure roughly:

```json
{
  "base": "<base dir>",
  "total_seeds_compilable": 123,
  "per_seed_results": {"/path/to/seed.py": {"seed": "...", "covered": [...], "errors": {...}}},
  "api_called_counts": {"numpy.array": 5, ...}
}
```

Artifacts
- For each seed the script creates a `.log` file next to the seed (stdout+stderr).
- The aggregated JSON in `--out` is the primary artifact for downstream analysis.

Notes & troubleshooting
- Ensure the project `src` directory is on `PYTHONPATH` so internal imports
  (e.g., `respfuzzer.*`) resolve. Example: `PYTHONPATH=$(pwd)/src python3 ...`.
- Ensure `config.toml` exists at the repository root (copy `config.toml.default` if needed).
- Use `--skip-interactive` if you observe many seeds hanging waiting for input.
- If a seed triggers native crashes or cannot be killed from inside Python,
  the orchestrator will retry it in a separate subprocess which is forcibly
  terminated when necessary.


## Script: `cov.py` (detailed usage)

Purpose
- Lightweight helper that drives the project's coverage bitmap (`dcov`) by
  running a driver over generated seed files. Use this script when you want
  to quickly exercise a set of generated examples and update the coverage
  bitmap for later analysis.

Prerequisites
- `dcov` must be available and importable in your environment.
- The driver script `experiments/RQ1/driver.py` should exist and be runnable.
- Ensure the repository `src` folder is on `PYTHONPATH` so imports resolve.

What it does
- Opens and clears the coverage bitmap via `dcov` APIs, then iterates over
  a list of libraries and runs the `driver.py` for each Python seed found in
  the corresponding `generated_tests_by_api/<lib>` directory. After each
  invocation it logs current coverage and progress to `cov_log.txt`.


Usage example (run all libraries listed in the script)

```bash
# ensure repository package is importable
PYTHONPATH=$(pwd)/src python3 experiments/RQ1/coverage/cov.py
```

How it invokes the driver
- For each seed the script runs:

```text
python <repo>/experiments/RQ1/driver.py <library> <path-to-seed.py>
```

Runtime behavior and tuning
- The helper uses `subprocess.run(..., timeout=10)` for each seed. If your
  seeds require more time, increase or remove the timeout in the script.
- The list of libraries is embedded in the script; you can edit it or adapt
  the script to accept a dynamic list from a file or CLI argument.

Logs and artifacts
- `cov_log.txt` (under `experiments/RQ1` by default) contains per-seed
  progress messages and coverage counts written by the script's `Logger`.

Troubleshooting
- `ModuleNotFoundError: No module named 'respfuzzer'` — ensure you run with
  `PYTHONPATH=$(pwd)/src` or install the package into your environment.
- `FileNotFoundError` for `driver.py` — verify the driver exists at
  `experiments/RQ1/driver.py` or update the script's `repo_root` computation.
- If `dcov` functions fail, verify the `dcov` package is installed and that
  any coverage bitmap files referenced by `dcov` are writable.

When to use
- Use `cov.py` for quick, local coverage updates across many generated
  examples. For precise per-seed API-invocation analysis prefer
  `check_instrument_calls_fixed.py` which performs instrumentation and
  detailed per-seed reporting.



