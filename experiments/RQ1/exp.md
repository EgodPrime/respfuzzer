# RQ1 Experiment

## How to disable Reasoner

Do the following change in `src/tracefuzz/lib/agentic_function_resolver.py`:

```python
# agentic_function_resolver.py:solve
# ...

            if result["result_type"] == ExecutionResultType.OK:
                solved = True
                break
            else:
                # =============================================================================================
                # Uncomment below to stop at first failure, not using reasoner to analyze failures
                # break 
                # =============================================================================================
                # try to get an explanation; if reasoner fails, record the failure and continue
                try:
                    reason = reasoner.explain(code, result)
                except Exception as e:
                    reason = f"Reasoner error: {str(e)}"
                    logger.debug(reason)

# ...
```

## How to disable the full docs

Do the following change in `src/tracefuzz/lib/agentic_function_resolver.py`:

```python
# agentic_function_resolver.py:Attempter:generate
# ...

        prompt = f"""任务:
请根据`function`和`history`中的信息来为{function.func_name}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。

注意：
1. 你生成的代码应该用<code></code>包裹。
2. 不要生成``` 
3. 不要生成`code`以外的任何内容 
4. 不要生成与`function`无关的代码(例如打印、注释、画图等)

例子：
<function>
{{
    func_name: "a.b.c",
    ...  // 其他字段省略
}}
</function>
<history>
...
</history>
<code>
import a
x = 2
y = "str"
res = a.b.c(x, y)
</code>

现在任务开始：
<function>
{function.model_dump_json()}
</function>
<history>
{history}
</history>
"""

# =============================================================================================
# Uncomment below to not take use of function information such as docstring and source code
#         from inspect import _ParameterKind
#         func_name = function.func_name
#         func_args = function.args
#         func_sig = f"def {func_name}("
#         if func_args:
#             for i, arg in enumerate(func_args):
#                 if i > 0:
#                     func_sig += ", "
#                 if arg.pos_type == _ParameterKind.POSITIONAL_ONLY.name:
#                     func_sig += arg.arg_name
#                 elif arg.pos_type == _ParameterKind.POSITIONAL_OR_KEYWORD.name:
#                     func_sig += arg.arg_name
#                 elif arg.pos_type == _ParameterKind.VAR_POSITIONAL.name:
#                     func_sig += "*" + arg.arg_name
#                 elif arg.pos_type == _ParameterKind.KEYWORD_ONLY.name:
#                     func_sig += arg.arg_name
#                 elif arg.pos_type == _ParameterKind.VAR_KEYWORD.name:
#                     func_sig += "**" + arg.arg_name
#         func_sig += "): ..."
#         prompt = f"""任务:
# 请根据`function`和`history`中的信息来为{function.func_name}生成一段完整的调用代码，应该包含import过程、函数参数创建和初始化过程以及最终的函数调用过程。

# 注意：
# 1. 你生成的代码应该用<code></code>包裹。
# 2. 不要生成``` 
# 3. 不要生成`code`以外的任何内容 
# 4. 不要生成与`function`无关的代码(例如打印、注释、画图等)

# 例子：
# <function>
# {{
#     func_name: "a.b.c",
#     ...  // 其他字段省略
# }}
# </function>
# <history>
# ...
# </history>
# <code>
# import a
# x = 2
# y = "str"
# res = a.b.c(x, y)
# </code>

# 现在任务开始：
# <function>
# {func_sig}
# </function>
# <history>
# {history}
# </history>
# """
# =============================================================================================

# ...
```

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

## Attempter + Reasoner vs full docs

```bash
cd {TRACEFUZZ}
bash scripts/run_generate_seeds.sh
db_tools view
```

|    Library Name    |     UDF Count      |     UDF Solved     |      BF Count      |     BF Solved      |      TF Count      |     TF Solved      |
|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|
|        nltk        |        764         |    558 (73.04%)    |         0          |      0 (N/A)       |        764         |    558 (73.04%)    |
|        dask        |        237         |    215 (90.72%)    |         0          |      0 (N/A)       |        237         |    215 (90.72%)    |
|        yaml        |         26         |    26 (100.00%)    |         0          |      0 (N/A)       |         26         |    26 (100.00%)    |
|      prophet       |         39         |    30 (76.92%)     |         0          |      0 (N/A)       |         39         |    30 (76.92%)     |
|       numpy        |        550         |    537 (97.64%)    |        121         |    112 (92.56%)    |        671         |    649 (96.72%)    |
|       pandas       |        346         |    262 (75.72%)    |         2          |    2 (100.00%)     |        348         |    264 (75.86%)    |
|      sklearn       |        383         |    345 (90.08%)    |         0          |      0 (N/A)       |        383         |    345 (90.08%)    |
|       scipy        |        1412        |   1386 (98.16%)    |         1          |    1 (100.00%)     |        1413        |   1387 (98.16%)    |
|      requests      |        112         |    110 (98.21%)    |         0          |      0 (N/A)       |        112         |    110 (98.21%)    |
|       spacy        |        403         |    277 (68.73%)    |         0          |      0 (N/A)       |        403         |    277 (68.73%)    |
|       torch        |         62         |    57 (91.94%)     |        642         |    565 (88.01%)    |        704         |    622 (88.35%)    |
|       paddle       |        423         |    419 (99.05%)    |         0          |      0 (N/A)       |        423         |    419 (99.05%)    |

## Attempter only vs full docs

```bash
cd {TRACEFUZZ}
# modify config.toml to use tracefuzz-RQ1-110.db
vim config.toml
# disable reasoner in agentic_function_resolver.py
vim src/tracefuzz/lib/agentic_function_resolver.py
bash scripts/run_generate_seeds.sh
db_tools view
```

|    Library Name    |     UDF Count      |     UDF Solved     |      BF Count      |     BF Solved      |      TF Count      |     TF Solved      |
|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|
|        nltk        |        764         |    471 (61.65%)    |         0          |      0 (N/A)       |        764         |    471 (61.65%)    |
|        dask        |        237         |    189 (79.75%)    |         0          |      0 (N/A)       |        237         |    189 (79.75%)    |
|        yaml        |         26         |    26 (100.00%)    |         0          |      0 (N/A)       |         26         |    26 (100.00%)    |
|      prophet       |         39         |    13 (33.33%)     |         0          |      0 (N/A)       |         39         |    13 (33.33%)     |
|       numpy        |        535         |    505 (94.39%)    |        120         |    86 (71.67%)     |        655         |    591 (90.23%)    |
|       pandas       |        346         |    237 (68.50%)    |         2          |     1 (50.00%)     |        348         |    238 (68.39%)    |
|      sklearn       |        383         |    275 (71.80%)    |         0          |      0 (N/A)       |        383         |    275 (71.80%)    |
|       scipy        |        1412        |   1322 (93.63%)    |         1          |    1 (100.00%)     |        1413        |   1323 (93.63%)    |
|      requests      |        112         |    100 (89.29%)    |         0          |      0 (N/A)       |        112         |    100 (89.29%)    |
|       spacy        |        403         |    196 (48.64%)    |         0          |      0 (N/A)       |        403         |    196 (48.64%)    |
|       torch        |         62         |    51 (82.26%)     |        642         |    531 (82.71%)    |        704         |    582 (82.67%)    |
|       paddle       |        423         |    387 (91.49%)    |         0          |      0 (N/A)       |        423         |    387 (91.49%)    |

## Attempter + Reasoner vs no docs

```bash
cd {TRACEFUZZ}
# modify config.toml to use tracefuzz-RQ1-101.db
vim config.toml
# modify agentic_function_resolver.py to enable reasoner but not use docs
vim src/tracefuzz/lib/agentic_function_resolver.py
bash scripts/run_generate_seeds.sh
db_tools view
```

|    Library Name    |     UDF Count      |     UDF Solved     |      BF Count      |     BF Solved      |      TF Count      |     TF Solved      |
|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|
|        nltk        |        764         |    508 (66.49%)    |         0          |      0 (N/A)       |        764         |    508 (66.49%)    |
|        dask        |        237         |    210 (88.61%)    |         0          |      0 (N/A)       |        237         |    210 (88.61%)    |
|        yaml        |         26         |    26 (100.00%)    |         0          |      0 (N/A)       |         26         |    26 (100.00%)    |
|      prophet       |         39         |    31 (79.49%)     |         0          |      0 (N/A)       |         39         |    31 (79.49%)     |
|       numpy        |        535         |    520 (97.20%)    |        120         |    107 (89.17%)    |        655         |    627 (95.73%)    |
|       pandas       |        346         |    246 (71.10%)    |         2          |    2 (100.00%)     |        348         |    248 (71.26%)    |
|      sklearn       |        383         |    341 (89.03%)    |         0          |      0 (N/A)       |        383         |    341 (89.03%)    |
|       scipy        |        1412        |   1340 (94.90%)    |         1          |    1 (100.00%)     |        1413        |   1341 (94.90%)    |
|      requests      |        112         |    107 (95.54%)    |         0          |      0 (N/A)       |        112         |    107 (95.54%)    |
|       spacy        |        403         |    251 (62.28%)    |         0          |      0 (N/A)       |        403         |    251 (62.28%)    |
|       torch        |         62         |    54 (87.10%)     |        642         |    545 (84.89%)    |        704         |    599 (85.09%)    |
|       paddle       |        423         |    416 (98.35%)    |         0          |      0 (N/A)       |        423         |    416 (98.35%)    |

## Attempter only vs no docs

```bash
cd {TRACEFUZZ}
# modify config.toml to use tracefuzz-RQ1-100.db
vim config.toml
# disable reasoner and doc in agentic_function_resolver.py
vim src/tracefuzz/lib/agentic_function_resolver.py
bash scripts/run_generate_seeds.sh
db_tools view
```

|    Library Name    |     UDF Count      |     UDF Solved     |      BF Count      |     BF Solved      |      TF Count      |     TF Solved      |
|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|--------------------|
|        nltk        |        764         |    389 (50.92%)    |         0          |      0 (N/A)       |        764         |    389 (50.92%)    |
|        dask        |        237         |    176 (74.26%)    |         0          |      0 (N/A)       |        237         |    176 (74.26%)    |
|        yaml        |         26         |    24 (92.31%)     |         0          |      0 (N/A)       |         26         |    24 (92.31%)     |
|      prophet       |         39         |     7 (17.95%)     |         0          |      0 (N/A)       |         39         |     7 (17.95%)     |
|       numpy        |        550         |    481 (87.45%)    |        121         |    69 (57.02%)     |        671         |    550 (81.97%)    |
|       pandas       |        346         |    209 (60.40%)    |         2          |     1 (50.00%)     |        348         |    210 (60.34%)    |
|      sklearn       |        383         |    153 (39.95%)    |         0          |      0 (N/A)       |        383         |    153 (39.95%)    |
|       scipy        |        1412        |    998 (70.68%)    |         1          |    1 (100.00%)     |        1413        |    999 (70.70%)    |
|      requests      |        112         |    99 (88.39%)     |         0          |      0 (N/A)       |        112         |    99 (88.39%)     |
|       spacy        |        403         |    202 (50.12%)    |         0          |      0 (N/A)       |        403         |    202 (50.12%)    |
|       torch        |         62         |    42 (67.74%)     |        642         |    352 (54.83%)    |        704         |    394 (55.97%)    |
|       paddle       |        423         |    302 (71.39%)    |         0          |      0 (N/A)       |        423         |    302 (71.39%)    |

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