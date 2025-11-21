# RQ2 Experiment

## Setup

> Note: we use {RESPFUZZER} to denote the root directory of the RespFuzzer repo.

### Step 1
Modify the `{RESPFUZZER}/config.toml` as follows:

```toml
[db_config]
db_name = "rq2_111" # without .db suffix
```

### Step 2
Extract Functions from the libraries:

```bash
cd {RESPFUZZER}
bash scripts/run_extract_functions.sh
```

functions will be extracted and stored in the database.

### Step 3
LLM may generate some files during the experiment, create a directory to store them:

```bash
mkdir -p {RESPFUZZER}/run_data
```

## Full Configuration(SCE+RCM)

```bash
cd {RESPFUZZER}
# modify config.toml to use db `rq2_111` and enable both reasoner(RCM) and docs(SCE)
vim config.toml
cd run_data
# generate seeds
bash ../scripts/run_generate_seeds.sh
```

## SCE-only

```bash
cd {RESPFUZZER}
# modify config.toml to use db `rq2_110` and enable docs(SCE) but disable reasoner(RCM)
vim config.toml
cd run_data
# generate seeds
bash ../scripts/run_generate_seeds.sh
```


## RCM-only

```bash
cd {RESPFUZZER}
# modify config.toml to use db `rq2_110` and enable reasoner(RCM) but disable docs(SCE)
vim config.toml
cd run_data
# generate seeds
bash ../scripts/run_generate_seeds.sh
```

## Baseline (no SCE, no RCM)

```bash
cd {RESPFUZZER}
# modify config.toml to use db `rq2_100` and disable both reasoner and docs
vim config.toml
cd run_data
# generate seeds
bash ../scripts/run_generate_seeds.sh
```

## View Results

Connect to the database, e.g., 'rq2_111'.

Run the following SQL query to view the results:

```sql
SELECT t1.library_name,
    t1.function_cnt,
        CASE
            WHEN t2.seed_cnt IS NULL THEN 0::bigint
            ELSE t2.seed_cnt
        END AS seed_cnt,
        CASE
            WHEN t2.seed_cnt IS NULL THEN '0%'::text
            ELSE concat((t2.seed_cnt::numeric(10,4) / t1.function_cnt::numeric * 100::numeric)::numeric(10,2)::text, '%')
        END AS fcr
   FROM ( SELECT function.library_name,
            count(1) AS function_cnt
           FROM function
          GROUP BY function.library_name) t1
     LEFT JOIN ( SELECT seed.library_name,
            count(1) AS seed_cnt
           FROM seed
          GROUP BY seed.library_name) t2 ON t1.library_name = t2.library_name
```

## How to plot a similar figure in our paper
```bash
# This will generate a figure named `RQ2.pdf` in the current directory.
python plot.py
```