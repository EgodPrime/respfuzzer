# RQ3 experiment

> Note: we use {RESPFUZZER} to denote the root directory of the RespFuzzer repo.

## DyFuzz Setup

```bash
cd {RESPFUZZER}/experiments/RQ3
git clone https://github.com/xiaxinmeng/DyFuzz.git
# Apply DyFuzz patches to enable compatibility with RespFuzzer's seeds
cp ./DyFuzz-patch/* ./DyFuzz/
```

## Fuzz4All Mutation Setup

We use the official Fuzz4All mutation strategy as a baseline. Rather than invoking the official system directly, we re-implement it as an isolated, adapted copy: `experiments/RQ3/miniFuzz4All` extracts the core mutator from the official Fuzz4All source (commit `0bf42fe`; see the per-method source/line annotations in `f4a_mutator.py`) and adapts it to run against RespFuzzer's seeds for the 12 Python libraries in this study. It is therefore an independent reproduction based on the official source, not the official system itself. See `miniFuzz4All/README.md` for the exact upstream commit, the methods extracted, and the adaptation points.

## dataset Setup

Once the RQ2 experiment is done, we can use the `rq2_111_data` to sample seeds for RQ3.

If you haven't run RQ2 yet, you can read the RQ2 experiment instructions in `experiments/RQ2/exp.md` to get the database file.

```bash
cd {RESPFUZZER}
# Sample seeds for RQ3 from RQ2 data, 
# the results will be stored in `$RESPFUZZER_DATA_DIR/<library>_seeds_sampled.json` 
RESPFUZZER_DATA_DIR=${RESPFUZZER}/rq2_111_data bash scripts/sample_seeds.sh
# Convert the sampled seeds to DyFuzz format, 
# the results will be stored in `$RESPFUZZER_DATA_DIR/<library>_seeds_dyfuzz.json`
RESPFUZZER_DATA_DIR=${RESPFUZZER}/rq2_111_data bash scripts/convert_to_dyfuzz_format.sh
```

## Run RespFuzzer

```bash
cd {RESPFUZZER}
# run RespFuzzer
cd ./experiments/RQ3/
# All fuzzing work should be performed in a separate directory for saving your project structure :>
mkdir -p run_data
cd run_data
# run RespFuzzer
RESPFUZZER_DATA_DIR=${RESPFUZZER}/rq2_111_data bash ../run_respfuzzer.sh
```

## Run Fuzz4All Mutation

```bash
cd {RESPFUZZER}/experiments/RQ3/
# All fuzzing work should be performed in a separate directory for saving your project structure :>
mkdir -p run_data
cd run_data
# run Fuzz4All mutation
RESPFUZZER_DATA_DIR=${RESPFUZZER}/rq2_111_data bash ../run_fuzz4all.sh
```

## Run DyFuzz

```bash
cd {RESPFUZZER}/experiments/RQ3/
## DyFuzz can handle its fuzzing garbage by itself
mkdir -p run_data
cd run_data
# run DyFuzz
RESPFUZZER_DATA_DIR=${RESPFUZZER}/rq2_111_data bash ../run_dyfuzz.sh
```

## How to get the similar table data as in our paper
```bash
# edit the script to set the correct log file paths
uv run report.py
```