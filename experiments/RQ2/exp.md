# RQ2 experiment

> Note: we use `{RESPFUZZER}` to denote the root directory of the RespFuzzer repo.
>
> RQ2 measures **fuzzing efficiency**: RespFuzzer vs miniFuzz4All vs DyFuzz on the same initial seeds under matched budgets. The experiment was previously called RQ3 — the runners still pass `--rq RQ3`, so logs/checkpoints are named `rq3_*`.

## Layout

Everything lives in `{RESPFUZZER}/experiments/RQ2/`:

| Path | Purpose |
|---|---|
| `run_respfuzzer.sh` / `run_fuzz4all.sh` / `run_dyfuzz.sh` | per-fuzzer runners (loop over all libraries) |
| `batch_run.sh <n>` | n reps × the three fuzzers, paired by `RUN_INDEX` |
| `miniFuzz4All/` | Fuzz4All mutator implementation |
| `DyFuzz/` + `DyFuzz-patch/` | DyFuzz clone and our patches |
| `report.py` | markdown table + statistics |
| `run_data/` | isolated working directory for every run |

## Setup

```bash
cd {RESPFUZZER}/experiments/RQ2
# DyFuzz: clone once, then keep the patches in sync
git clone https://github.com/xiaxinmeng/DyFuzz.git
cp ./DyFuzz-patch/* ./DyFuzz/
```

### Seeds

All three fuzzers share the same sampled seeds (`<lib>_seeds_sampled.json`), derived from the seed-generation runs' `<lib>_seeds.json`:

```bash
cd {RESPFUZZER}
RESPFUZZER_DATA_DIR=/path/to/seeds bash scripts/sample_seeds.sh              # -> <lib>_seeds_sampled.json
RESPFUZZER_DATA_DIR=/path/to/seeds bash scripts/convert_to_dyfuzz_format.sh  # -> <lib>_seeds_dyfuzz.json
```

The same `RESPFUZZER_DATA_DIR` is read by all three runners (default `RQ2_data_new_111`).

#### Seed-set coverage ceiling

To know the theoretical line-coverage ceiling of the shared seed set (an upper bound of what any of the three fuzzers can reach on it), count the executable statements of the seed APIs' files with coverage.py:

```bash
cd {RESPFUZZER}
RQ2=1 bash scripts/count_seeds_states.sh     # all libraries
# or a single seed set:
uv run scripts/count_seeds_states.py -f experiments/RQ2/nltk_capped100_seeds.json
```

Expected output, one line per seed set:

```
Total Statements: 15697, Number of Files: 59
```

- `Total Statements` — coverage.py `num_statements` (deduped `.py` files containing the seed APIs), same definition as coverage reports, comparable with the coverage totals behind the percentages in `report.py`.
- `Number of Files` — deduped count of those files.
- `scripts/count_seeds_loc.sh` (same `SAMPLED=1`/`RQ2=1` switches) reports raw non-blank/non-comment LOC instead — a looser figure, keep the statements figure for percentage ceilings.

## Run

```bash
cd {RESPFUZZER}/experiments/RQ2
mkdir -p run_data && cd run_data        # AGENTS.md: always run inside run_data/

# one rep = all three fuzzers, paired seeds (budget env vars are mandatory):
RF_REQUEST_BUDGET=500 RF_TIME_BUDGET=60 \
F4A_REQUEST_BUDGET=500 F4A_TIME_BUDGET=60 \
DYFUZZ_MUTANT_BUDGET=500 DYFUZZ_TIME_BUDGET=60 \
RESPFUZZER_DATA_DIR=/path/to/seeds bash ../batch_run.sh 1

# or a single fuzzer:
RF_REQUEST_BUDGET=500 RF_TIME_BUDGET=60 RESPFUZZER_DATA_DIR=/path/to/seeds bash ../run_respfuzzer.sh
```

### Budgets

Per-seed (RespFuzzer, DyFuzz) or per-function (miniFuzz4All); both flags take effect at the same time, whichever is exhausted first stops the loop.

| Fuzzer | count flag | time flag | env vars |
|---|---|---|---|
| RespFuzzer | `-lrequest N` (LLM requests / seed) | `-ltime S` (s / seed) | `RF_REQUEST_BUDGET`, `RF_TIME_BUDGET` |
| miniFuzz4All | `-lrequest N` (LLM request batches / function) | `-ltime S` (s / function) | `F4A_REQUEST_BUDGET`, `F4A_TIME_BUDGET` |
| DyFuzz | `-lmutant N` (mutants / seed, = test executions) | `-ltime S` (s / seed) | `DYFUZZ_MUTANT_BUDGET`, `DYFUZZ_TIME_BUDGET` |

At least one flag per fuzzer is required (no config fallback), except miniFuzz4All, which falls back to `llm_fuzz_per_seed` in `config.toml` when neither is given. Flags can also be passed directly, e.g. `uv run fuzz fuzz_dataset $SEEDS_FILE --rq RQ3 --group respfuzzer -lrequest 500 -ltime 60`.

## Report

```bash
uv run experiments/RQ2/report.py
```

Parses `rq3_*.log` (+ same-stem `*_checkpoint.json` for paired statistics) from this directory.

## Related

- miniFuzz4All budget pre-runs (capped100 seeds, two budget modes): [`rq2_minifuzz4all_track.md`](../../rq2_minifuzz4all_track.md)
