# RQ4 Experiment

## SecurityEval Experiment

This experiment evaluates the effectiveness of RespFuzzer's capabilities in detecting known vulnerabilities using the SecurityEval benchmark.

### Setup

```bash
# clone SecurityEval repository
git clone https://github.com/s2e-lab/SecurityEval.git
# install bandit
uv pip install bandit
# install codeql
wget -c https://github.com/github/codeql-action/releases/download/codeql-bundle-v2.23.5/codeql-bundle-linux64.tar.zst
tar -I zstd -xf codeql-bundle-linux64.tar.zst
export PATH=$PATH:$(pwd)/codeql
```

> Version Information:
> - Python version: 3.13.9
> - CodeQL version: 2.23.5
> - Bandit version: 1.9.1

### Generate Test Cases

```bash
uv run agentic_function_resolver_vul.py
```

The test cases will be generated in the `experiments/RQ4/SecurityEval/Testcases_RespFuzzer` directory.

### Analyze Test Cases with bandit

```bash
# first step: analyze test cases with bandit
bandit -r ./SecurityEval/Testcases_RespFuzzer -f json -o ./SecurityEval/Result/testcases_respfuzzer.json
# second step: count unique vulnerabilities
uv run report.py bandit SecurityEval/Result/testcases_respfuzzer.json
```

### Analyze Test Cases with CodeQL

```bash
# first step: create database
cd SecurityEval/Testcases_RespFuzzer
codeql database create --language=python ../Databases/Testcases_RespFuzzer_DB
cd ../..
bash job_respfuzzer.sh
# second step: count unique vulnerabilities
uv run report.py codeql ./SecurityEval/Result/testcases_respfuzzer
```

> Note: SecurityEval does not an official script to count unique vulnerabilities. After reading and analyzing the output CSV files and json files, we write our own script `report.py` to count unique vulnerabilities based on the output results of Bandit and CodeQL.
