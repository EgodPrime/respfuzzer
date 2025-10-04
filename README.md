# TraceFuzz: Intelligent Fuzzing Framework

TraceFuzz is an advanced fuzzing framework that combines traditional fuzzing techniques with agentic intelligence to automatically discover and exploit vulnerabilities in Python libraries. The framework uses a combination of function parsing, parameter mutation, and intelligent reasoning to systematically test code for edge cases and security vulnerabilities.

## Key Features

### Agentic Function Resolution
- **Attempter**: Generates test cases based on function signatures and history
- **QueitExecutor**: Executes code safely with timeout protection
- **Reasoner**: Analyzes execution results and provides explanations for failures

### Library Analysis
- **LibraryVisitor**: Discovers functions from Python libraries by traversing module trees
- **FunctionParser**: Extracts function signatures and types from Python modules
- **PyiParser**: Parses .pyi interface files to understand type hints

### Fuzzing Engine
- **Mutator**: Generates test cases by mutating parameters of various types (lists, dicts, complex numbers, etc.)
- **Instrumenter**: Instruments functions for detailed execution tracking
- **FuzzFunction**: Executes functions with mutated inputs and captures results

### Persistent Storage
- **Database System**: Stores function definitions, seeds, and solve history
- **SQLite/Redis Integration**: Supports both file-based and in-memory storage
- **History Tracking**: Maintains complete execution history for analysis

### Advanced Capabilities
- **Crash Detection**: Identifies potential vulnerabilities through crash keywords
- **Vulnerability Filtering**: Filters results to focus on high-potential issues
- **Configuration Management**: Flexible configuration via config.toml

## Architecture

```
+-------------------+
|    Agentic System |
| (Attempter, Executor, Reasoner) |
+-------------------+
          |
          v
+-------------------+
|   Fuzzing Engine  |
| (Mutator, Instrumenter) |
+-------------------+
          |
          v
+-------------------+
|   Library Visitor |
| (Function Discovery) |
+-------------------+
          |
          v
+-------------------+
|    Parsers        |
| (.pyi, Function)  |
+-------------------+
          |
          v
+-------------------+
|   Database        |
| (Functions, Seeds, History) |
+-------------------+
```

## Installation

1. Clone the repository:
```bash
git clone http://192.168.1.21:9980/PrimedEgod/mplfuzz.git
cd mplfuzz
```

2. Create and activate a virtual environment using uv:
```bash
uv venv
source .venv/bin/activate
```

3. Install the project and its dependencies:
```bash
uv pip install -e .
```

## Usage Examples

### Fuzz a Specific Library
```bash
fuzz_library requests
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

### Analyze Results
```python
db_tools view
```

## Configuration

The framework uses `config.toml` for configuration. Key settings include:
- `database`: Database connection details (SQLite path or Redis URL)
- `fuzzing`: Fuzzing parameters (timeout, mutation depth, etc.)
- `agentic`: Agentic system settings (reasoning model, retry limits)

## Testing

Run the test suite:
```bash
pytest tests/
```
