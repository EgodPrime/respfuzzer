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
  - `rust` and `cargo` for building native extensions (https://rust-lang.org/tools/install/)

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
# The template defaults to model "Qwen3.6-35B-A3B" (see the [llm]/[llm_mutator] sections).
vim config.toml
```

## Configuration

The framework uses `config.toml` for configuration. Key settings include:

### Model Configuration
> used by Reflective Seed Generation
- `base_url`: Base URL for the model API.
- `api_key`: API key for authentication.
- `model_name`: Name of the model to use (default `Qwen3.6-35B-A3B`).

The paper reports three final models: **Llama3.1-8B-Instruct**, **Qwen3.6-35B-A3B**, and **gpt-oss-120b**. Reproduce each by replacing `model_name` accordingly (the same applies to the `[llm_mutator]` section); record the checkpoint version as noted in E-20.

### Data Storage
Data is stored as JSON files under the directory selected by the `RESPFUZZER_DATA_DIR` environment variable (default `data/`). No SQL database is used.
- `RESPFUZZER_DATA_DIR`: root directory for the extracted functions and seeds (default `data/`).


### Redis Configuration
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

Extracted functions are written to `<RESPFUZZER_DATA_DIR>/<library>_functions.json` (and seeds to `<library>_seeds.json`); no SQL database is created.

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
| Dask | Parallel Computing | Pure Python | https://dask.org/ |
| PyYAML | YAML Parsing | Pure Python | https://pyyaml.org/ |
| Prophet | Time Series Forecasting | Python + C extension | https://facebook.github.io/prophet/ |
| NumPy   | Scientific Computing | Python + C extension | https://numpy.org/ |
| Pandas  | Data Analysis | Python + C extension | https://pandas.pydata.org/ |
| Scikit-learn | Machine Learning | Python + C extension | https://scikit-learn.org/ |
| Scipy | Scientific Computing | Python + C extension | https://scipy.org/ |
| Requests | HTTP Library | Pure Python | https://requests.readthedocs.io/ |
| spaCy | Natural Language Processing | Python + C extension | https://spacy.io/ |
| PyTorch | Deep Learning | Python + C extension | https://pytorch.org/ |
| PaddlePaddle | Deep Learning | Python + C extension | https://www.paddlepaddle.org.cn/ |
## Baselines

For RQ3 comparisons, this repository includes a re-implementation of the **Fuzz4All** mutation
baseline under `experiments/RQ3/miniFuzz4All/`. It is **not** the official Fuzz4All system,
and it is not a from-scratch reimplementation either: it is an isolated, adapted copy
extracted from the official Fuzz4All source (commit `0bf42fe`) and adapted to run against
RespFuzzer's seeds for the 12 libraries in this study. The exact upstream commit, the methods
extracted, and every intentional adaptation point are documented in
`experiments/RQ3/miniFuzz4All/README.md` (which also carries the fidelity contract used to
verify it against the official logic).

