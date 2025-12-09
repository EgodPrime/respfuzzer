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

We have read the Fuzz4All paper and its github repository carefully. Then we implement the Fuzz4All mutator isolatedly in `experiments/RQ3/miniFuzz4All`, which contains the nessary code to perform the Fuzz4All mutation strategy on RespFuzzer's seeds.

## dataset Setup

Once the RQ2 experiment is done, we can use the `rq2_111` database to sample seeds for RQ3.

If you haven't run RQ2 yet, you can read the RQ2 experiment instructions in `experiments/RQ2/exp.md` to get the database file.

```bash
# set the db to `rq2_111`
cd {RESPFUZZER}
vim config.toml
cd {RESPFUZZER}/experiments/RQ3/
export_dyfuzz  # you will get a json named respfuzzer_seeds.json
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
fuzz fuzz_dataset ../respfuzzer_seeds.json
```

## Run Fuzz4All Mutation

```bash
cd {RESPFUZZER}/experiments/RQ3/
# All fuzzing work should be performed in a separate directory for saving your project structure :>
mkdir -p run_data
cd run_data
# run Fuzz4All mutation
python ../miniFuzz4All/fuzz_dataset.py normal ../respfuzzer_seeds.json
```

## Run DyFuzz

```bash
cd {RESPFUZZER}/experiments/RQ3/DyFuzz
## DyFuzz can handle its fuzzing garbage by itself
python run_respfuzzer.py
```

## How to draw a similar table in the paper
```bash
uv run reoort.py
```