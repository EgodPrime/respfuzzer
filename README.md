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
# dcov 已作为 dependencies 随上面的 `uv pip install -e .[dev]` 自动安装；手动方式：
uv pip install git+https://github.com/EgodPrime/dcov.git@ba85c8508cb29bc8b065b813c5d0b11f8d903b78
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
- `model_name`: Served model id on the API server (default `Qwen3.6-35B-A3B`; must match the checkpoint one serves, see below).

The paper reports three final models; the exact (quantized) checkpoints used are:

| Paper name | Exact checkpoint (ModelScope) | Notes |
|---|---|---|
| `Llama3.1-8B-Instruct` | `RedHatAI/Meta-Llama-3.1-8B-Instruct-FP8` | FP8 (W8A8) quantization of `meta-llama/Meta-Llama-3.1-8B-Instruct`; llama3.1 license |
| `Qwen3.6-35B-A3B` | `Qwen/Qwen3.6-35B-A3B-FP8` | Official Qwen3.6-35B-A3B (35B MoE, 3B active) quantized to FP8 (block-128); Apache-2.0 |
| `gpt-oss-120b` | `openai-mirror/gpt-oss-120b` | == `openai/gpt-oss-120b`; 117B MoE (5.1B active), MXFP4; uses the harmony format; Apache-2.0 |

All three checkpoints are served via **SGLang** (version `0.5.6.post3.dev9187+g21c88f862`). Reproduce each by serving its checkpoint and setting `model_name` in both `[llm]` and `[llm_mutator]` to the served model id.

```bash
#Llama-3.1-8B-Instruct-FP8
sglang serve \
  --model-path models/Meta-Llama-3.1-8B-Instruct-FP8 \
  --served-model-name Llama-3.1-8B-Instruct --host 0.0.0.0 --port 8021 \
  --mem-fraction-static 0.8 \
  --attention-backend flashinfer \
  --chunked-prefill-size 2048 \
  --max-running-requests 32 --cuda-graph-max-bs-decode 32 \
  --kv-cache-dtype fp8_e4m3 \
  --disable-radix-cache --mamba-ssm-dtype bfloat16 --mamba-full-memory-ratio 0.07 --mamba-radix-cache-strategy extra_buffer_lazy
```

> TODO: Qwen3.6-35B-A3B-FP8和gpt-oss-120b的部署脚本，跑的时候调整好了再复制过来



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

