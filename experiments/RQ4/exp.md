# RQ4 experiment

> Note: we use {RESPFUZZER} to denote the root directory of the RespFuzzer repo.

## dataset Setup

In our paper, we use the same seeds as RQ3 for RQ4. Thus, please first follow the instructions in `experiments/RQ3/exp.md` to get the `respfuzzer_seeds.json` file.

## Safe tips
Before running the experiments, please make sure you have created a separate directory to store all the fuzzing garbage files to avoid messing up your project structure. For example:

```bash
cd {RESPFUZZER}/experiments/RQ4/
mkdir -p run_data
```

## Run RespFuzzer with different mutation strategies

### Backup the original fuzz_exp.py

```bash
cd {RESPFUZZER}/experiments/RQ4/
cp {RESPFUZZER}/src/respfuzzer/lib/fuzz/fuzz_exp.py fuzz_exp-backup.py
``` 

### Full Configuration (RespFuzzer default mutation)

```bash
cd {RESPFUZZER}/experiments/RQ4/run_data
fuzz fuzz_dataset ../../RQ3/respfuzzer_seeds.json > <xxxx>.log 2>&1
```

### Without LLM-based Mutation

```bash
cd {RESPFUZZER}/experiments/RQ4/
cp fuzz_exp-RQ4-NL.py {RESPFUZZER}/src/respfuzzer/lib/fuzz/fuzz_exp.py
cd run_data
fuzz fuzz_dataset ../../RQ3/respfuzzer_seeds.json > <xxxx>.log 2>&1
```

### Without Traditional Parameter Mutation
```bash
cd {RESPFUZZER}/experiments/RQ4/
cp fuzz_exp-RQ4-NP.py {RESPFUZZER}/src/respfuzzer/lib/fuzz/fuzz_exp.py
cd run_data
fuzz fuzz_dataset ../../RQ3/respfuzzer_seeds.json > <xxxx>.log 2>&1
```

### Without Semantic Feedback
```bash
cd {RESPFUZZER}/experiments/RQ4/
cp fuzz_exp-RQ4-NSF.py {RESPFUZZER}/src/respfuzzer/lib/fuzz/fuzz_exp.py
cp {RESPFUZZER}/src/respfuzzer/lib/fuzz/llm_mutator.py ./llm_mutator-backup.py
cp llm_mutator-RQ4-NSF.py {RESPFUZZER}/src/respfuzzer/lib/fuzz/llm_mutator.py
cd run_data
fuzz fuzz_dataset ../../RQ3/respfuzzer_seeds.json > <xxxx>.log 2>&1
# don't forget to restore the llm_mutator.py
cp llm_mutator-backup.py {RESPFUZZER}/src/respfuzzer/lib/fuzz/llm_mutator.py
```

### Without Coverage Feedback
```bash
cd {RESPFUZZER}/experiments/RQ4/
cp fuzz_exp-RQ4-NCF.py {RESPFUZZER}/src/respfuzzer/lib/fuzz/fuzz_exp.py
cd run_data
fuzz fuzz_dataset ../../RQ3/respfuzzer_seeds.json > <xxxx>.log 2>&1
```

### Restore the original fuzz_exp.py

```bash
cd {RESPFUZZER}/experiments/RQ4/
cp fuzz_exp-backup.py {RESPFUZZER}/src/respfuzzer/lib/fuzz/fuzz_exp.py
```

## How to get the similar table data as in our paper
```bash
# edit the script to set the correct log file paths
uv run report.py
```