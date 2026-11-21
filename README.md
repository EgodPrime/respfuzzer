# RespFuzzer

> This repository contains the implementation and all necessary scripts to reproduce the results of our paper "Reflective Seed Generation and Semantic-Driven Mutation for Python Library Fuzzing"， currently submitted to IEEE TSE.


## Architecture

 - Reflective Seed Generation
   - src/respfuzzer/lib/library_visitor.py
   - src/respfuzzer/lib/agentic_function_resolver.py
 - Semantic-Driven Mutation
   - src/respfuzzer/lib/fuzz/semantic_check.py
   - src/respfuzzer/lib/fuzz/instrument.py
   - src/respfuzzer/lib/fuzz/mutator.py
   - src/respfuzzer/lib/fuzz/llm_mutator.py
   - src/respfuzzer/lib/fuzz/fuzz_function.py
   - src/respfuzzer/lib/fuzz/fuzz_exp.py

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
extract numpy
```

The extracted functions are written to `data/<library>_functions.json` (JSON, one entry per function). All other persistent artifacts are JSON as well — e.g. `data/<mode>/<library>_seeds_<model>_rs<N>.json` and the accompanying `*_seeds_meta_*.json` ledgers. The codebase no longer uses SQLite or PostgreSQL for storage.

### Generate Function Calls
```bash
solve numpy
```

### Start Fuzzing
```bash
fuzz fuzz_library numpy -lrequest 10
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
RQ2=1 bash scripts/count_seeds_loc.sh        # seed-set LOC (statistics only; LC denominator is the whole package — see experiments/RQ2/rq2.md)
```

## Experiments (RQ1–RQ4)

Numbering follows the repository layout: RQ1 seed generation, RQ2 fuzzing
efficiency, RQ3 mutation ablation, RQ4 vulnerability discovery. The libraries
are the six in `scripts/libraries.conf`; single-run commands execute inside a
`run_data/` directory.

| RQ | Description | Driver | Outputs / analysis |
|---|---|---|---|
| RQ1 | Reflective seed generation (5 modes × 3 models) | `bash scripts/run_seeding_campaign.sh` | seeds + per-function cost ledgers in `data/<mode>/`; `experiments/RQ1/analyze.py` (paired diffs) + `experiments/RQ1/report_tables.py` (level CIs, strata, per-run, resources; `--latex` for raw `tabular` blocks) |
| RQ2 | Fuzzing efficiency (RespFuzzer vs miniFuzz4All vs DyFuzz) | `experiments/RQ2/run_rq2_respfuzzer_campaign.sh` / `run_rq2_budget_campaign.sh` / `run_rq2_dyfuzz_campaign.sh <-lrequest N \| -ltime S>` (one budget flag per call; the older `batch_run.sh` / `run_*.sh` trio is the legacy `seeds_sampled` path) | `rq2_*` (+ legacy `rq3_dyfuzz_*`) log/checkpoint pairs; `experiments/RQ2/analyze.py` (paired per-function line coverage, LC %) + `report_tables.py` (runs/levels/paired/strata/resources/denominators + figures, `--latex`); Qwen-only scope and step-by-step reproduction in `experiments/RQ2/rq2.md` |
| RQ3 | Semantic-driven mutation ablation (`nl`, `np`, `ncf`; `nsf` removed from design — rejected at entry, historical artifacts archived only) | `bash experiments/RQ3/run_rq3_ablation_campaign.sh <NL\|NP\|NCF>` (one arm per call; `run_rq3_ltime_campaign.sh <NP\|NCF>` also ran the `-ltime 60` batch — **archive only, not analyzed**, `--budget` removed; the Full arm is not run — it reuses the RQ2 RespFuzzer runs) | `rq3_*_mode-<m>` pairs; `experiments/RQ3/analyze.py` (per-function paired Δ vs Full, gain %) + `report_tables.py` (main table (csv+tex) / runs / strata / resources + main figure, LC %) — both default to **all 3 models, count batch only**; scope, equivalence argument and reproduction steps in `experiments/RQ3/rq3.md` |
| RQ4 | Real-world vulnerability discovery | standalone reproduction scripts (unpatched library + `uv run python vul_<n>_<lib>.py`) | four upstream reports: `experiments/RQ4/vul_{1,2}_nltk.py`, `vul_3_scipy.py`, `vul_4_cpython.py`; per-issue status snapshot in [`experiments/RQ4/vul_links.md`](experiments/RQ4/vul_links.md) |

Each RQ2/RQ3 run writes one `.log` + `*_checkpoint.json` pair named
`<rq>_<group>_<library>_<dataset>_<timestamp>[_mode-<m>]_checkpoint.json`, where
`<group>` is `respfuzzer` \| `fuzz4all` \| `dyfuzz`. The checkpoint records run
identity, environment and config snapshots, `decode_config` (LLM decoding
settings), the paired-comparison keys (`seed`, `run_index`), results (coverage,
LLM calls, token counts) and per-seed events. RQ1 instead writes seed sets
(`*_seeds_<model>_rs<N>.json`) and `_meta_` ledgers into `data/<mode>/`.

The generated result tables (`experiments/**/results_*/`, including the
per-run CSVs behind every reported number) are not committed to git; they are
packaged with the full logs as the **artifact archive** (`artifact.zip`,
see the checklist in `need_zip.md` for its exact contents) and uploaded to
Google Drive:
<https://drive.google.com/file/d/1b4Z17Wm732QQ2M8kQ2JBt3JDCT1GgZPX/view?usp=drive_link>.
The archive contains only git-ignored data/results plus its own `README.md`
(the repository itself carries all code and documentation); `data/results` above
means the git-ignored `data/` and results directories, not a `data/results` path.
