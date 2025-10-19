# TraceFuzz

> This repository contains the implementation and all necessary scripts to reproduce the results of our paper: [TraceFuzz: xxx](https://egodprime.github.io/papers/tracefuzz.pdf)


## Architecture

 - ReflectiveSeeder
   - src/tracefuzz/lib/library_visitor.py
   - src/tracefuzz/lib/agentic_function_resolver.py
 - FuzzTrigger
   - src/tracefuzz/lib/fuzz/instrument.py
 - FuzzEngine
   - src/tracefuzz/lib/mutator.py
   - src/tracefuzz/lib/fuzz/fuzz_function.py
   - src/tracefuzz/lib/fuzz/fuzz_library.py
   - src/lib.rs
   - src/chain_rng.rs
   - src/mutator.rs

## Installation

0. Prerequisites:
  - `uv` tool for virtual environment management (https://docs.astral.sh/uv/getting-started/installation/)
  - Python 3.13 (Based on `uv`)
  - `rust` and `cargo` for building native extensions (https://rust-lang.org/tools/install/)
  - `clang 14.0+` compiler for building native extensions
  - (Optional) `dcov` for coverage analysis (To reproduce experiments)
    - https://github.com/EgodPrime/dcov

1. Clone the repository:
```bash
git clone https://github.com/EgodPrime/tracefuzz.git
cd tracefuzz
```

2. Create and activate a virtual environment:
```bash
uv venv --python 3.13
source .venv/bin/activate
```

3. Install the project and its dependencies:
```bash
uv pip install -e .
```

4. (Optional) Developer mode:
```bash
uv pip install -e .[dev]
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

## Configuration

The framework uses `config.toml` for configuration. Key settings include:

### Model Configuration
- `base_url`: Base URL for the model API.
- `api_key`: API key for authentication.
- `model_name`: Name of the model to use.

### Database Configuration
- `db_name`: Name of the SQLite database file.

### Redis Configuration
- `host`: Redis server host.
- `port`: Redis server port.
- `db`: Redis database index.

### Fuzzing Parameters
- `execution_timeout`: Timeout for function execution.
- `mutants_per_seed`: Number of mutations per seed.


## Usage Examples

### Extract Functions from a Library
```bash
reflective_seeder extract_functions numpy
```

There will be a new SQLite database file (`<db_name>.db` where `<db_name>` is set in `config.toml`) created in the `run_data` folder containing the extracted functions.

## Generate Function Calls
```bash
reflective_seeder generate_seeds numpy
```

## View the Database
```bash
# Approach 1: Using the `sqlite_web`
# This will automatically open a web page in your browser if you are using VSCode
# You can also access it according to the host and port shown in the terminal
sqlite_web run_data/<db_name>.db

# Approach 2: Using the `db_tools` CLI
db_tools view
```

### Fuzz a Specific Library
```bash
fuzz fuzz_library numpy
```

## Libraries Under Test

> The following libraries are used for testing and evaluation of the TraceFuzz framework:

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


