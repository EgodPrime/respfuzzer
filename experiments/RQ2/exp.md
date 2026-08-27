# RQ2 Experiment

## Setup

> Note: we use {RESPFUZZER} to denote the root directory of the RespFuzzer repo.

### Step 1
Set the environment variable for RespFuzzer:
```bash
export RESPFUZZER_DATA_DIR=RQ2_data
```

### Step 2
Extract Functions from the libraries:

```bash
cd {RESPFUZZER}
bash scripts/run_extract_functions.sh
```

functions will be extracted and stored in json files under the directory `{RESPFUZZER}/RQ2_data/`.

### Step 3
Copy the json files for different experimental settings:

```bash
cd {RESPFUZZER}/
cp -r RQ2_data/ rq2_111_data/   # Full Configuration(SCE+RCM)
cp -r RQ2_data/ rq2_110_data/   # SCE-only
cp -r RQ2_data/ rq2_101_data/   # RCM-only
cp -r RQ2_data/ rq2_100_data/   # Baseline (no SCE, no RCM)


### Step 4
LLM may generate some files during the experiment, create a directory to store them:

```bash
mkdir -p {RESPFUZZER}/run_data
```

## Full Configuration(SCE+RCM)

```bash
cd {RESPFUZZER}
# change data path
export RESPFUZZER_DATA_DIR=rq2_111_data
# modify config.toml to enable both reasoner(RCM) and docs(SCE)
vim config.toml
cd run_data
# generate seeds
bash ../scripts/run_generate_seeds.sh
```

## SCE-only

```bash
cd {RESPFUZZER}
# change data path
export RESPFUZZER_DATA_DIR=rq2_110_data
# modify config.toml to enable docs(SCE) but disable reasoner(RCM)
vim config.toml
cd run_data
# generate seeds
bash ../scripts/run_generate_seeds.sh
```


## RCM-only

```bash
cd {RESPFUZZER}
# change data path
export RESPFUZZER_DATA_DIR=rq2_101_data
# modify config.toml to enable reasoner(RCM) but disable docs(SCE)
vim config.toml
cd run_data
# generate seeds
bash ../scripts/run_generate_seeds.sh
```

## Baseline (no SCE, no RCM)

```bash
cd {RESPFUZZER}
# change data path
export RESPFUZZER_DATA_DIR=rq2_100_data
# modify config.toml to disable both reasoner and docs
vim config.toml
cd run_data
# generate seeds
bash ../scripts/run_generate_seeds.sh
```

## View Results

```bash
RESPFUZZER_DATA_DIR=<data_dir_name> uv run db_tools view
# e.g., RESPFUZZER_DATA_DIR=rq2_111_data uv run db_tools view
```

## How to plot a similar figure in our paper
```bash
# This will generate a figure named `RQ2.pdf` in the current directory.
uv run plot.py # FIXME
```