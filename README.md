# TraceFuzz: Intelligent Fuzzing Framework

TraceFuzz is an advanced fuzzing framework that combines traditional fuzzing techniques with agentic intelligence to automatically discover and exploit vulnerabilities in Python libraries. The framework uses a combination of function parsing, parameter mutation, and intelligent reasoning to systematically test code for edge cases and security vulnerabilities.

## Key Features



## Architecture

 - ReflectiveSeeder
   - library_visitor.py
   - agentic_function_resolver.py
 - FuzzTrigger
   - fuzz/instrument.py
 - FuzzEngine
   - c
   - mutator.py
   - fuzz/fuzz_function.py
   - fuzz/fuzz_library.py

## Installation

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
```

## Usage Examples

### Fuzz a Specific Library
```bash
fuzz_library numpy
```

### Run Agentic Function Resolution
```bash
agentic_function_resolver numpy
```

### Instrument a Function for Fuzzing
```python
from src.tracefuzz.fuzz.instrument import instrument_function

def my_function(x, y):
    return x / y

# Instrument the function
instrumented_func = instrument_function(my_function)

# Execute with fuzzed parameters
result = instrumented_func(10, 0)  # Will trigger crash detection
```

### Run Library Visitor Script
```bash
bash scripts/run_library_visitor.sh
```

### Run Agentic Function Solver
```bashbash
bash scripts/run_agentic_function_solver.sh
```

### Run Fuzzing Script
```bash
bash scripts/run_fuzz_library.sh
```

### Analyze Results
```python
db_tools view
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

## Testing

Run the test suite:
```bash
pytest tests/
```

Generate a coverage report:
```bash
pytest --cov=src --cov-report=html
```

## Libraries Under Test

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


