# RQ1 Experiment

## Setup

> Note: we use {TRACEFUZZ} to denote the root directory of the TraceFuzz repo.

### Step 1
Modify the `{TRACEFUZZ}/config.toml` as follows:

```toml
[db_config]
db_name = "tracefuzz-RQ1-111" # without .db suffix
```

### Step 2
Extract Functions from the libraries:

```bash
cd {TRACEFUZZ}
bash scripts/run_extract_functions.sh
```

functions will be extracted and stored in the database `{TRACEFUZZ}/run_data/tracefuzz-RQ1-111.db`.

### Step 3
Copy the database file for experiments:

```bash
cp {TRACEFUZZ}/run_data/tracefuzz-RQ1-111.db {TRACEFUZZ}/run_data/tracefuzz-RQ1-101.db
cp {TRACEFUZZ}/run_data/tracefuzz-RQ1-111.db {TRACEFUZZ}/run_data/tracefuzz-RQ1-110.db
cp {TRACEFUZZ}/run_data/tracefuzz-RQ1-111.db {TRACEFUZZ}/run_data/tracefuzz-RQ1-100.db
```

### Step 4
LLM may generate some files during the experiment, create a directory to store them:

```bash
mkdir -p {TRACEFUZZ}/run_data
```

## Attempter + Reasoner vs full docs

```bash
cd {TRACEFUZZ}
# modify config.toml to use tracefuzz-RQ1-111.db and enable both reasoner and docs
vim config.toml
cd run_data
# generate seeds
bash ../scripts/run_generate_seeds.sh
# view results
db_tools view
```

|    Library Name    |     UDF Count      |     UDF Solved     |      BF Count      |     BF Solved      |      TF Count      |     TF Solved      |
|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|
|        nltk        |        764         |    601 (78.66%)    |         0          |      0 (N/A)       |        764         |    601 (78.66%)    |
|        dask        |        237         |    216 (91.14%)    |         0          |      0 (N/A)       |        237         |    216 (91.14%)    |
|        yaml        |         26         |    25 (96.15%)     |         0          |      0 (N/A)       |         26         |    25 (96.15%)     |
|      prophet       |         39         |    39 (100.00%)    |         0          |      0 (N/A)       |         39         |    39 (100.00%)    |
|       numpy        |        550         |    542 (98.55%)    |        121         |    114 (94.21%)    |        671         |    656 (97.76%)    |
|       pandas       |        346         |    271 (78.32%)    |         2          |    2 (100.00%)     |        348         |    273 (78.45%)    |
|      sklearn       |        383         |    361 (94.26%)    |         0          |      0 (N/A)       |        383         |    361 (94.26%)    |
|       scipy        |        1412        |   1381 (97.80%)    |         1          |    1 (100.00%)     |        1413        |   1382 (97.81%)    |
|      requests      |        112         |    110 (98.21%)    |         0          |      0 (N/A)       |        112         |    110 (98.21%)    |
|       spacy        |        403         |    310 (76.92%)    |         0          |      0 (N/A)       |        403         |    310 (76.92%)    |
|       torch        |         62         |    60 (96.77%)     |        643         |    573 (89.11%)    |        705         |    633 (89.79%)    |
|       paddle       |        423         |    399 (94.33%)    |         0          |      0 (N/A)       |        423         |    399 (94.33%)    |


## Attempter + Reasoner vs no docs


```bash
cd {TRACEFUZZ}
# modify config.toml to use tracefuzz-RQ1-110.db and enable reasoner but disable docs
vim config.toml
cd run_data
# generate seeds
bash ../scripts/run_generate_seeds.sh
# view results
db_tools view
```

|    Library Name    |     UDF Count      |     UDF Solved     |      BF Count      |     BF Solved      |      TF Count      |     TF Solved      |
|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|
|        nltk        |        764         |    569 (74.48%)    |         0          |      0 (N/A)       |        764         |    569 (74.48%)    |
|        dask        |        237         |    215 (90.72%)    |         0          |      0 (N/A)       |        237         |    215 (90.72%)    |
|        yaml        |         26         |    25 (96.15%)     |         0          |      0 (N/A)       |         26         |    25 (96.15%)     |
|      prophet       |         39         |    39 (100.00%)    |         0          |      0 (N/A)       |         39         |    39 (100.00%)    |
|       numpy        |        550         |    543 (98.73%)    |        121         |    114 (94.21%)    |        671         |    657 (97.91%)    |
|       pandas       |        346         |    265 (76.59%)    |         2          |    2 (100.00%)     |        348         |    267 (76.72%)    |
|      sklearn       |        383         |    346 (90.34%)    |         0          |      0 (N/A)       |        383         |    346 (90.34%)    |
|       scipy        |        1412        |   1350 (95.61%)    |         1          |    1 (100.00%)     |        1413        |   1351 (95.61%)    |
|      requests      |        112         |    109 (97.32%)    |         0          |      0 (N/A)       |        112         |    109 (97.32%)    |
|       spacy        |        403         |    264 (65.51%)    |         0          |      0 (N/A)       |        403         |    264 (65.51%)    |
|       torch        |         62         |    59 (95.16%)     |        643         |    546 (84.91%)    |        705         |    605 (85.82%)    |
|       paddle       |        423         |    395 (93.38%)    |         0          |      0 (N/A)       |        423         |    395 (93.38%)    |



## Attempter only vs full docs

```bash
cd {TRACEFUZZ}
# modify config.toml to use tracefuzz-RQ1-101.db and disable reasoner but enable docs
vim config.toml
cd run_data
# generate seeds
bash ../scripts/run_generate_seeds.sh
# view results
db_tools view
```

|    Library Name    |     UDF Count      |     UDF Solved     |      BF Count      |     BF Solved      |      TF Count      |     TF Solved      |
|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|
|        nltk        |        764         |    507 (66.36%)    |         0          |      0 (N/A)       |        764         |    507 (66.36%)    |
|        dask        |        237         |    198 (83.54%)    |         0          |      0 (N/A)       |        237         |    198 (83.54%)    |
|        yaml        |         26         |    13 (50.00%)     |         0          |      0 (N/A)       |         26         |    13 (50.00%)     |
|      prophet       |         39         |    16 (41.03%)     |         0          |      0 (N/A)       |         39         |    16 (41.03%)     |
|       numpy        |        535         |    501 (93.64%)    |        120         |    83 (69.17%)     |        655         |    584 (89.16%)    |
|       pandas       |        346         |    238 (68.79%)    |         2          |     1 (50.00%)     |        348         |    239 (68.68%)    |
|      sklearn       |        383         |    293 (76.50%)    |         0          |      0 (N/A)       |        383         |    293 (76.50%)    |
|       scipy        |        1412        |   1262 (89.38%)    |         1          |    1 (100.00%)     |        1413        |   1263 (89.38%)    |
|      requests      |        112         |    104 (92.86%)    |         0          |      0 (N/A)       |        112         |    104 (92.86%)    |
|       spacy        |        403         |    244 (60.55%)    |         0          |      0 (N/A)       |        403         |    244 (60.55%)    |
|       torch        |         62         |    39 (62.90%)     |        643         |    144 (22.40%)    |        705         |    183 (25.96%)    |
|       paddle       |        423         |    166 (39.24%)    |         0          |      0 (N/A)       |        423         |    166 (39.24%)    |

## Attempter only vs no docs

```bash
cd {TRACEFUZZ}
# modify config.toml to use tracefuzz-RQ1-100.db and disable both reasoner and docs
vim config.toml
cd run_data
# generate seeds
bash ../scripts/run_generate_seeds.sh
# view results
db_tools view
```

|    Library Name    |     UDF Count      |     UDF Solved     |      BF Count      |     BF Solved      |      TF Count      |     TF Solved      |
|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|
|        nltk        |        764         |    388 (50.79%)    |         0          |      0 (N/A)       |        764         |    388 (50.79%)    |
|        dask        |        237         |    175 (73.84%)    |         0          |      0 (N/A)       |        237         |    175 (73.84%)    |
|        yaml        |         26         |     8 (30.77%)     |         0          |      0 (N/A)       |         26         |     8 (30.77%)     |
|      prophet       |         39         |     6 (15.38%)     |         0          |      0 (N/A)       |         39         |     6 (15.38%)     |
|       numpy        |        535         |    445 (83.18%)    |        120         |    44 (36.67%)     |        655         |    489 (74.66%)    |
|       pandas       |        346         |    177 (51.16%)    |         2          |     1 (50.00%)     |        348         |    178 (51.15%)    |
|      sklearn       |        383         |    124 (32.38%)    |         0          |      0 (N/A)       |        383         |    124 (32.38%)    |
|       scipy        |        1412        |    863 (61.12%)    |         1          |     0 (0.00%)      |        1413        |    863 (61.08%)    |
|      requests      |        112         |    100 (89.29%)    |         0          |      0 (N/A)       |        112         |    100 (89.29%)    |
|       spacy        |        403         |    211 (52.36%)    |         0          |      0 (N/A)       |        403         |    211 (52.36%)    |
|       torch        |         62         |    29 (46.77%)     |        643         |     44 (6.84%)     |        705         |    73 (10.35%)     |
|       paddle       |        423         |    50 (11.82%)     |         0          |      0 (N/A)       |        423         |    50 (11.82%)     |

## How to plot a similar figure in our paper
```bash
cd {TRACEFUZZ}/experiments/RQ1
cp {TRACEFUZZ}/run_data/tracefuzz-RQ1-111.db ./
cp {TRACEFUZZ}/run_data/tracefuzz-RQ1-110.db ./
cp {TRACEFUZZ}/run_data/tracefuzz-RQ1-101.db ./
cp {TRACEFUZZ}/run_data/tracefuzz-RQ1-100.db ./

# This will generate a figure named `RQ1.pdf` in the current directory.
python plot.py
```