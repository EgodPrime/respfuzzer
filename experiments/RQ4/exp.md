# RQ4 experiment

> Note: we use {RESPFUZZER} to denote the root directory of the RespFuzzer repo.

## Dataset Setup

In our paper, we use the same seeds as RQ3 for RQ4. Thus, please first follow the instructions in `experiments/RQ3/exp.md` to get the `<library>_seeds_sampled.json` file.

## Safe tips
Before running the experiments, please make sure you have created a separate directory to store all the fuzzing garbage files to avoid messing up your project structure. For example:

```bash
cd {RESPFUZZER}/experiments/RQ4/
mkdir -p run_data
```

## Run RespFuzzer with different mutation strategies

All experiments use the unified `fuzz_exp-RQ4.py` entry point. Each ablation is controlled by a `--mode` flag:

| Mode  | Effect                            |
|-------|-----------------------------------|
| `NL`  | No LLM-based mutation             |
| `NP`  | No traditional parameter mutation |
| `NSF` | No semantic filtering             |
| `NCF` | No coverage feedback              |

### Full Configuration (RespFuzzer default mutation)

```bash
cd {RESPFUZZER}/experiments/RQ4/run_data
bash ../run.sh
```

### Without LLM-based Mutation

```bash
cd {RESPFUZZER}/experiments/RQ4/run_data
bash ../run.sh NL
```

### Without Traditional Parameter Mutation

```bash
cd {RESPFUZZER}/experiments/RQ4/run_data
bash ../run.sh NP
```

### Without Semantic Filtering

```bash
cd {RESPFUZZER}/experiments/RQ4/run_data
bash ../run.sh NSF
```

### Without Coverage Feedback

```bash
cd {RESPFUZZER}/experiments/RQ4/run_data
bash ../run.sh NCF
```

## How to get the similar table data as in our paper

```bash
# edit the script to set the correct log file paths
uv run report.py
```
