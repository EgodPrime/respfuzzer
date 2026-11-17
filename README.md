# RespFuzzer

> This repository contains the implementation and all necessary scripts to reproduce the results of our paper "Reflective Seed Generation and Semantic-Driven Mutation for Python Library Fuzzing"， currently submitted to IEEE TSE.


## Architecture

 - Reflective Seed Generation
   - src/respfuzzer/lib/library_visitor.py
   - src/respfuzzer/lib/agentic_function_resolver.py
 - Semantic-Driven Mutation
   - src/respfuzzer/lib/fuzz/instrument.py
   - src/respfuzzer/lib/mutator.py
   - src/respfuzzer/lib/llm_mutator.py
   - src/respfuzzer/lib/fuzz/fuzz_function.py
   - src/respfuzzer/lib/fuzz/fuzz_library.py
   - src/lib.rs
   - src/chain_rng.rs
   - src/mutator.rs

## Installation

0. Prerequisites:
  - `uv` tool for virtual environment management (https://docs.astral.sh/uv/getting-started/installation/)
  - Python 3.13 (Based on `uv`)

1. Clone the repository:
```bash
git clone https://github.com/EgodPrime/respfuzzer.git
cd respfuzzer
```

2. Create and activate a virtual environment:
```bash
uv venv --python 3.13
source .venv/bin/activate
```

3. Install the project and its dependencies:
```bash
uv pip install -e .
# (Optional) Developer mode:
# uv pip install -e .[dev]
```

4. install dcov for coverage analysis:
```bash
uv pip install git+https://github.com/EgodPrime/dcov.git@python_v2
```

5. Install the libraries under test:
```bash
# edit the script and set USE_UV=0 if you are not using uv
bash scripts/install_lut.sh
```   

6. Configure the framework:
```bash
cp config.toml.default config.toml
# Edit config.toml to set your model API key and other settings
vim config.toml
```

7. Download the NLTK datasets (**required** — many nltk functions need
   external datasets; without pre-downloading they try to fetch at call time,
   which stalls the fuzzing sandbox on network timeouts and wastes LLM budget):

   ```bash
   # Direct connection (may be slow in restricted networks):
   uv run python scripts/download_nltk_data.py
   # Or via proxy (recommended; NLTK data lives on GitHub raw):
   all_proxy=http://<proxy-host>:<port> uv run python scripts/download_nltk_data.py
   ```

   The script downloads ~50 datasets (tokenizers/taggers/chunkers/sentiment/
   common corpora/grammars) into `~/nltk_data` (NLTK's first-priority search
   path, visible to the fuzzing sandbox), removes stale `.lock`/`.tmp`
   leftovers from interrupted downloads, skips anything already present
   (idempotent), and finishes with a proxy-free smoke check that the key
   resources actually load. Note: NLTK >= 3.10 refuses proxied downloads by
   default (SSRF guard); the script opts in via `nltk.pathsec.ALLOW_PROXIED_FETCH`
   for trusted proxies.

## Configuration

The framework uses `config.toml` for configuration. Key settings include:

### Model Configuration
> used by Reflective Seed Generation
- `base_url`: Base URL for the model API.
- `api_key`: API key for authentication.
- `model_name`: Name of the model to use.


### Redis Configuration
> Redis is **not** used for data storage; it is only a runtime coordination store (per-process random state during fuzzing). All persistent data are JSON files.
- `host`: Redis server host.
- `port`: Redis server port.
- `db`: Redis database index.

### Fuzzing Parameters
- `execution_timeout`: Timeout for function execution.
- `llm_fuzz_per_seed`: Number of LLM-based mutations per seed.
- `data_fuzz_per_seed`: Number of data-based mutations per seed.
- `max_try_per_seed`: Maximum attempts per seed.


## Usage Examples

### Extract Functions from a Library
```bash
reflective_seeder extract_functions numpy
```

The extracted functions are written to `data/<library>_functions.json` (JSON, one entry per function). All other persistent artifacts are JSON as well — e.g. `data/<mode>/<library>_seeds_<model>_rs<N>.json` and the accompanying `*_seeds_meta_*.json` ledgers. The codebase no longer uses SQLite or PostgreSQL for storage.

### Generate Function Calls
```bash
reflective_seeder generate_seeds numpy
```

### Start Fuzzing
```bash
fuzz fuzz_library numpy
```

## Libraries Under Test

> The following libraries are used for testing and evaluation of the RespFuzzer framework:

| Library | Type | Composition | URL|
|---------|------|-------------|----|
| NLTK | Natural Language Processing | Pure Python | https://www.nltk.org/ |
| NumPy   | Scientific Computing | Python + C extension | https://numpy.org/ |
| Pandas  | Data Analysis | Python + C extension | https://pandas.pydata.org/ |
| Scikit-learn | Machine Learning | Python + C extension | https://scikit-learn.org/ |
| Scipy | Scientific Computing | Python + C extension | https://scipy.org/ |
| spaCy | Natural Language Processing | Python + C extension | https://spacy.io/ |



## Seed Dataset (600 functions)

The efficiency (RQ2, `experiments/RQ2/`) and ablation (RQ3, `experiments/RQ3/`) experiments fuzz a shared seed bench of **600 initial seeds: 100 functions per library across the 6 libraries above**. The dataset is built as follows:

1. **Source**: the reflective seeding outputs of RQ1 — `data/respfuzzer/<lib>_seeds_Llama-3.1-8B-Instruct_rs{1..5}.json` (5 repetitions, 6 libraries). Each seed carries the real `function_call` string used as the mutation starting point by the `Fuzz4AllMutator`.
2. **Deduplication**: seeds from all 5 repetitions are merged and deduplicated by `func_path` (one seed per unique function), then sorted. This produces the full-coverage reference sets `<lib>_all_functions_seeds.json` (nltk 376, numpy 731, pandas 600, scipy 955, sklearn 355, spacy 270; 3,287 in total).
3. **Sampling**: from each library's deduplicated set, exactly `min(100, N)` seeds are drawn with a fixed RNG (`random.Random(4399)`), yielding `<lib>_capped100_seeds.json` — **100 seeds × 6 libraries = 600 seeds in total**. With the fixed RNG the sample is fully reproducible; every sampled seed belongs to a distinct function.

Regenerate the dataset deterministically with:

```bash
uv run python experiments/RQ2/generate_rq2_seeds.py
```

Both the three fuzzers (RespFuzzer / miniFuzz4All / DyFuzz) and all budget configurations and ablation arms consume the **identical** `experiments/RQ2/<lib>_capped100_seeds.json` files, so comparisons are paired on the same functions. Seed-set statistics can be checked with:

```bash
RQ2=1 bash scripts/count_seeds_states.sh    # per-library seed/state counts
RQ2=1 bash scripts/count_seeds_loc.sh        # seed-set LOC (for coverage ceilings)
```

## Experiment Map: Inputs, Outputs, Naming and Recorded Data (RQ1–RQ4)

> RQ numbering below follows the repository layout: **RQ1** seed generation, **RQ2** fuzzing efficiency, **RQ3** semantic-driven mutation ablation, **RQ4** real-world vulnerability discovery (the manuscript renumbering is applied when the paper is updated). The libraries under test are always the six from `scripts/libraries.conf`. All fuzzing sandboxes and single-run commands execute inside a `run_data/` directory.

### Unified run-record contract (RQ2 and RQ3)

Every fuzzing run writes exactly one `.log` / `*_checkpoint.json` pair into the experiment directory (`experiments/RQ2/` or `experiments/RQ3/`):

```
<rq>_<group>_<library>_<dataset>_<YYYYMMDDTHHMMSS>[_mode-<m>].log
<rq>_<group>_<library>_<dataset>_<YYYYMMDDTHHMMSS>[_mode-<m>]_checkpoint.json
```

| Segment | Values |
|---|---|
| `<rq>` | `rq2` / `rq3` — some early RQ2 runs carry `rq3_` because the runner passed `--rq RQ3`; `report.py` normalises both prefixes |
| `<group>` | `respfuzzer` \| `fuzz4all` \| `dyfuzz` |
| `<library>` | `nltk` `numpy` `pandas` `scipy` `sklearn` `spacy` |
| `<dataset>` | seed-set stem, e.g. `capped100` |
| `_mode-<m>` | RQ3 ablation arm only: `nl`, `np`, `nsf`, `ncf` |

The `.log` holds the complete run output; the checkpoint records the structured result:

| Block | Recorded data |
|---|---|
| run identity | `schema_version`, `rq`, `group`, `library`, `mode`, `run_id`, `status`, `error`, `started_at`, `finished_at`, `duration_seconds`, `log`, `checkpoint` |
| `environment` | `argv`, `cwd`, `pid`, `python`, `platform`, `hostname`, `git_commit` |
| `config` | sanitised snapshot of `config.toml` (`llm`, `embedding`, `redis`, `fuzz`, `reflective_seeder`, `fuzz4all`, `dyfuzz`) |
| `decode_config` | exact LLM decoding settings — `model_name`, `temperature`, `top_p`, `max_tokens`, `chat_template_kwargs` (present in every RQ2/RQ3 checkpoint) |
| `metadata` | `entry`, `dataset_path`, `mode`, `seed`, `run_index` (the paired-comparison keys), `request_budget`, `time_budget` |
| `results` | `concurrency`, `request_budget`, `time_budget`, `seed_count`, `initial_coverage`, `final_coverage`, `llm_calls`, `input_tokens`, `output_tokens`, `total_tokens` |
| `event_counts`, `events`, `totals` | per-seed trajectory events: `func_name`, `library_name`, `coverage`, `ts`, `event` |

### RQ1 — Reflective seed generation (5 modes × 5 repetitions × 3 models)

- **Inputs**: `data/<library>_functions.json` (extracted function lists; stratification tags `implementation_type` / `docstring_quality` / `type_annotation_completeness` are backfilled into the same file by `scripts/backfill_function_tags.py`); `data/<library>_vector_storage/` prebuilt vector indexes (only for the `respfuzzer-rag` mode, built by `scripts/run_vector_campaign.sh`); `config.toml` (`[llm]`, `[embedding]`, `[reflective_seeder]` with `budget=10`, `concurrency=4`).
- **Driver**: `bash scripts/run_seeding_campaign.sh` — serial with resume; each run is `cd run_data && uv run solve <library> --mode <mode> --rs <N>` (modes: `respfuzzer`, `respfuzzer-wodoc`, `respfuzzer-wofb`, `respfuzzer-woboth`, `respfuzzer-rag`; `--rs` 1…5).
- **Outputs** (150 files per model = 6 libraries × 5 modes × 5 repetitions):
  - `data/<mode>/<library>_seeds_<model>_rs<N>.json` — the seed array; each record has `id`, `func_id`, `library_name`, `func_path`, `args`, `function_call` (the real call string that becomes the mutation starting point);
  - `data/<mode>/<library>_seeds_meta_<model>_rs<N>.json` — per-run header (`model_name`, `temperature`, `concurrency`, `library`, `mode`, `random_seed`) plus the per-function cost ledger `per_function`: `budget_used`, `llm_calls`, `token_input`, `token_output`;
  - `experiments/function_solutions/<mode>_<model>_<library>_<ts>_rs<N>.log` — full per-run log, plus two progress ledgers: `campaign_progress.tsv` (`timestamp library random_seed mode seeds llm_calls secs rc`, one row per run) and `vector_campaign_progress.tsv` (one row per library index build).
- **Analysis**: `uv run python experiments/RQ1/analyze.py --model <MODEL>` (optional `--bootstrap hierarchical`) prints a markdown table with per-mode, per-library and `-overall-micro`/`-overall-macro` rows: sfcr, paired 95% CI, Cohen's h. Reference outputs for all three models: `experiments/RQ1/results_3model/<model>_{cluster,hierarchical}.txt`.

### RQ2 — Fuzzing efficiency (RespFuzzer vs miniFuzz4All vs DyFuzz)

- **Inputs**: `experiments/RQ2/<library>_capped100_seeds.json` (the 600-seed bench above; all three fuzzers share it), selected via `RESPFUZZER_DATA_DIR`; budgets via env vars — `RF_REQUEST_BUDGET`/`RF_TIME_BUDGET`, `F4A_REQUEST_BUDGET`/`F4A_TIME_BUDGET`, `DYFUZZ_MUTANT_BUDGET`/`DYFUZZ_TIME_BUDGET` (request/time budget axes; whichever is exhausted first stops the run).
- **Driver**: from `experiments/RQ2/run_data/`, `bash ../batch_run.sh <rep>` runs all three fuzzers paired on the same seeds, or use `run_respfuzzer.sh` / `run_fuzz4all.sh` / `run_dyfuzz.sh` individually. Axes: fuzzer × {request, time} budget × 5 repetitions × 6 libraries; RespFuzzer additionally runs all three models.
- **Outputs**: the unified `rq2_*` record pairs in `experiments/RQ2/`.
- **Report**: `uv run experiments/RQ2/report.py` — coverage/time summary tables plus FC% ± sd with 95% CI, paired same-function differences and Cliff's δ.

### RQ3 — Semantic-driven mutation ablation

- **Inputs**: the same `capped100` seed sets as RQ2 (required for `run_index` pairing); the ablation arm is selected by mode: `NL` (no LLM mutation), `NP` (no parameter mutation), `NSF` (no semantic filtering), `NCF` (no coverage feedback); the `Full` arm reuses RQ2's `respfuzzer` runs (same configuration, budget and seeds).
- **Driver**: `bash experiments/RQ3/run.sh <MODE>` → `fuzz_exp-RQ3.py`, inside `experiments/RQ3/run_data/`.
- **Outputs**: `rq3_respfuzzer_<library>_capped100_<ts>_mode-<m>.log` + checkpoint in `experiments/RQ3/`, same contract as RQ2 (`results` carries `seed_count`, `initial_coverage`/`final_coverage`, `llm_calls`, token counts; `event_counts` shows one `seed_fuzzed` event per seed).
- **Analysis**: `experiments/RQ3/report.py` (run-level tables with paired CI) and `experiments/RQ3/analyze.py` (function-level paired/hierarchical bootstrap CI with Cohen's h).

### RQ4 — Real-world vulnerability discovery

Candidates are mined from RespFuzzer campaign logs (RQ1–RQ4 runs on the target libraries) and validated end to end; all scripts live in `experiments/RQ4/` and write their intermediates as JSON next to the scripts:

1. `summary_from_logs.py <log dir>` — scans fuzzing logs for crash/timeout records (`Mutant N execution timeout … Last random state`) → `crash_summary.json` (`{library: [{seed_id, random_state}]}`);
2. `simple_replay.py` — replays each crash and synthesises a PoC with the LLM → `replay_results.json` (`seed_id`, `random_state`, `result` (stderr), `poc`);
3. `filter.py` — an LLM judge keeps records that are genuine crashes or potential security issues → `filtered_replay_results.json`;
4. `final_validate.py` — final confirmation over the filtered set;
5. `vul_<n>_<library>.py` — a standalone reproduction script per reported issue (the artifact attached to the upstream issue);
6. `report_new.py` — aggregates the confirmed findings into the paper's real-world-vulnerability table (status snapshot date, maintainer assessment, root-cause distinctness, severity, patch/fix commit, reproduction conditions).

> The SecurityEval-based evaluation of the previous submission is **not** part of this revision and has been removed together with its reported claims (see the response letter, Major Comment 1).

### Artifact index (manuscript claim → repository location)

| Manuscript artifact item | Location |
|---|---|
| Function lists (with stratification tags) | `data/<library>_functions.json` |
| Raw seeds (per mode / model / repetition) | `data/<mode>/<library>_seeds_<model>_rs<N>.json` (+ `_meta_` ledgers) |
| Seed bench used by RQ2/RQ3 | `experiments/RQ2/<library>_capped100_seeds.json` (regenerate: `experiments/RQ2/generate_rq2_seeds.py`) |
| Prompts (seeding and mutation) | inline in `src/respfuzzer/lib/agentic_function_resolver.py` and `src/respfuzzer/lib/fuzz/llm_mutator.py` |
| Decoding parameters | `config.toml` `[llm]` / `[llm_mutator]`, frozen per run in each checkpoint's `decode_config` |
| Random seeds | `--rs <N>` for RQ1; `metadata.seed` + `metadata.run_index` in RQ2/RQ3 checkpoints (derived by `respfuzzer.utils.run_seed.derive_run_seed`, same seed across paired runs) |
| Environment profile | checkpoint `environment` + `config` snapshot; `pyproject.toml` / `uv.lock` |
| Raw run results and logs | per-run `.log` + `*_checkpoint.json` in `experiments/RQ<2|3>/`; RQ1 logs in `experiments/function_solutions/` |
| Table / figure scripts | `experiments/RQ1/analyze.py`, `experiments/RQ2/report.py`, `experiments/RQ3/{report,analyze}.py`, `experiments/RQ4/report_new.py` |
| Model checkpoint identity | `decode_config.model_name` in checkpoints + `environment.git_commit` |
