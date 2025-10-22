# RQ2 experiment

> Note: we use {TRACEFUZZ} to denote the root directory of the TraceFuzz repo.

## DyFuzz Setup

```bash
cd {TRACEFUZZ}/experiments/RQ2
git clone https://github.com/xiaxinmeng/DyFuzz.git
# Apply DyFuzz patches to enable compatibility with TraceFuzz's seeds
cp ./DyFuzz-patch/* ./DyFuzz/
```

## Fuzz4All Mutation Setup

We have read the Fuzz4All paper and its github repository carefully. Then we implement the Fuzz4All mutator isolatedly in `experiments/RQ2/miniFuzz4All`, which contains the nessary code to perform the Fuzz4All mutation strategy on TraceFuzz's seeds.

## dataset Setup

Once the RQ1 experiment is done, we can copy the database file to RQ2 for further fuzzing.

If you haven't run RQ1 yet, you can read the RQ1 experiment instructions in `experiments/RQ1/exp.md` to get the database file.

```bash
cp {TRACEFUZZ}/run_data/tracefuzz-RQ1-111.db {TRACEFUZZ}/run_data/tracefuzz-RQ2.db
# sample seeds from the database
cd {TRACEFUZZ}/experiments/RQ2/
export_dyfuzz  # you will get a json named tracefuzz_seeds.json
```

## Run TraceFuzz

```bash
cd {TRACEFUZZ}
# modify config.toml to use tracefuzz-RQ2.db
vim config.toml
# run TraceFuzz
cd ./experiments/RQ2/
# All fuzzing work should be performed in a separate directory for saving your project structure :>
mkdir -p run_data
cd run_data
# run TraceFuzz
fuzz fuzz_dataset ../tracefuzz_seeds.json
```

## Run Fuzz4All Mutation

```bash
cd {TRACEFUZZ}/experiments/RQ2/
# All fuzzing work should be performed in a separate directory for saving your project structure :>
mkdir -p run_data
cd run_data
# run Fuzz4All mutation
python ../miniFuzz4All/fuzz_dataset.py normal ../tracefuzz_seeds.json
```

## Run DyFuzz

```bash
cd {TRACEFUZZ}/experiments/RQ2/DyFuzz
## DyFuzz can handle its fuzzing garbage by itself
python run_tracefuzz.py
```