# RQ1 实验复现手册

## 实验说明

对 6 个 Python 库执行 5 种 seeding 模式 × 5 次重复的种子生成实验，共 5 × 5 × 6 = **150 次 run**，产出每个函数的种子代码（seeds）与逐函数开销账本（meta）。

| 维度 | 取值 |
|---|---|
| 库（6） | nltk(492) numpy(776) pandas(652) scipy(1004) sklearn(373) spacy(330)（共 4127 个函数） |
| 模式（5） | `respfuzzer`（完整）/ `respfuzzer-wodoc`（无文档）/ `respfuzzer-wofb`（无反馈）/ `respfuzzer-woboth`（两者皆无）/ `respfuzzer-rag`（检索增强） |
| 重复（5） | `--rs 1` … `--rs 5`；重复编号 N 即随机种子 N，产物文件名带 `_rs<N>` 后缀，互不覆盖 |

模型服务：LLM = Llama-3.1-8B-Instruct（`config.toml` 的 `[llm].base_url`），embedding = Qwen3-Embedding-8B（`[embedding].base_url`，4096 维）。每函数 LLM 预算 `budget=10` 轮，seeding 线程并发 `concurrency=4`。

`{RESPFUZZER}` 为仓库根目录。以下命令均在该目录执行；凡涉及 `solve` 的单步操作都须先进入 `run_data/` 隔离目录。运行期间实时进度见 `{RESPFUZZER}/rq1_track.md`。

## 数据准备

**1. 安装环境**
- 命令：`cd {RESPFUZZER} && uv sync`
- 输入：`pyproject.toml`、`uv.lock`
- 预期输出：`.venv/`（Python 3.13 + 全部依赖）

**2. 确认 `config.toml`**
- 命令：`cat config.toml`（不存在时 `cp config.toml.default config.toml` 再改）
- 输入：`config.toml.default`
- 预期输出：`[llm].base_url`、`[embedding].base_url` 指向实验服务；`[reflective_seeder]` 下 `concurrency = 4`、`budget = 10`。注意环境不要设置 `all_proxy` 等代理（局域网服务不可走代理）

**3. 代码基线校验**
- 命令：`uv run pytest tests/fuzz/test_mutation_budget.py -q -p no:cacheprovider --no-cov`
- 输入：当前工作树（已含 seeding 必需的 `Seed`/`Mutant`/`Retriever` 修复）
- 预期输出：`8 passed`

**4. 下载 6 库源码语料**
- 命令：`uv run python scripts/download_lib_sources.py`
- 输入：venv 已装版本（全部取 PyPI 已装版本 sdist，无需代理）
- 预期输出：`data/<lib>_source/` × 6，每库为包目录内容平铺（含 `__init__.py`）；日志每库一行 `[ ok  ] <lib>: ...`，无 `[FAIL ]`

**5. 下载 nltk 数据集（必做）**
- 命令：`all_proxy=http://<proxy-host>:<port> uv run python scripts/download_nltk_data.py`（直连可省略代理前缀）
- 输入：nltk 数据源（GitHub raw）
- 预期输出：`~/nltk_data/` 内约 50 个数据集（punkt/punkt_tab、各类标注器、vader、wordnet、brown/movie_reviews/conll2000 等常用语料、grammars）；日志 `ok=50 failed=0` 且冒烟 `7/7 OK`。不做此步则 seeding 期间 nltk 现场下载数据集会因网络超时拖慢执行、浪费 LLM budget
**6. 创建隔离运行目录**
- 命令：`mkdir -p {RESPFUZZER}/run_data`
- 输入：—
- 预期输出：空目录 `run_data/`（`solve` 的沙箱与命令 cwd 均指向此处）

## 复现过程

两条战役脚本可并行执行：向量库战役走 embedding 服务，seeding 战役走 LLM 服务，互不争抢。两者均为串行 + 断点续跑（重跑脚本自动跳过已完成项）。

### 步骤 1：预建 6 库向量索引

- 命令：`bash scripts/run_vector_campaign.sh`
- 输入：`data/<lib>_source/` × 6（数据准备 4）
- 预期输出：
  - `data/<lib>_vector_storage/` × 6，每库含非空 `default__vector_store.json`、`docstore.json`、`index_store.json`（耗时参考：dask ≈ 1.5h/200MB，numpy ≈ 7h/1.15GB，全量 45–55h）
  - `experiments/function_solutions/vector_campaign_progress.tsv` 每完成一库追加一行：`timestamp library rc secs store_bytes`，rc 应为 0
  - `experiments/function_solutions/vector_campaign_master.log` 全量日志；每库末尾出现 `[check] <lib>: reload ok`

### 步骤 2：运行 seeding 主战役（150 run）

- 命令：`bash scripts/run_seeding_campaign.sh`
- 输入：`data/<lib>_functions.json` × 6（函数清单）；RAG 模式还需步骤 1 的向量库
- 预期输出：
  - 脚本内部按 `mode → rep → library` 顺序调用 `cd run_data && uv run solve <lib> --mode <mode> --rs <N>`，RAG 模式垫底
  - `data/respfuzzer[-wodoc|-wofb|-woboth|-rag]/<lib>_seeds_<model>_rs<N>.json` × 150（种子数组；参考量级：`respfuzzer` 模式 numpy ≈ 683 条）
  - 同目录同 stem 的 `<lib>_seeds_meta_<model>_rs<N>.json` × 150（`metadata` + 逐函数 `llm_calls`/token 统计）
  - `experiments/function_solutions/<mode>_<model>_<lib>_<ts>_rs<N>.log` × 150（单 run 全量日志）
  - `experiments/function_solutions/campaign_progress.tsv` 每 run 一行：`timestamp library random_seed mode seeds llm_calls secs rc`
  - `experiments/function_solutions/campaign_master.log` 全量日志

### 步骤 3：产物核对

- 命令：
  ```bash
  cd {RESPFUZZER}
  wc -l < experiments/function_solutions/campaign_progress.tsv            # run 计数
  awk -F'\t' 'NR>1 && $8!=0' experiments/function_solutions/campaign_progress.tsv | wc -l   # 失败 run 计数
  ```
- 输入：步骤 2 的 `campaign_progress.tsv`
- 预期输出：第一行 = 151（表头 + 150 run）；失败 run 计数 = 0

### 步骤 4：结果汇总

- 命令：
- 命令：
  ```bash
  # 4a. 聚合总览：每模式每库 seeds 数
  cd {RESPFUZZER}/run_data && uv run python -c "
  import json,glob,os
  for m in ['respfuzzer','respfuzzer-wodoc','respfuzzer-wofb','respfuzzer-woboth','respfuzzer-rag']:
      rows=[(os.path.basename(f).replace('_seeds','').rsplit('_rs',1)[0],len(json.load(open(f))))
            for f in sorted(glob.glob(f'../data/{m}/*_seeds_*_rs*.json'))]
      print(m, rows)
  "
  # 4b. 统计分析：函数配对 bootstrap 置信区间 + Cohen's h 效应量
  #     （实现见 experiments/RQ1/analyze.py，对应评审"在函数和库层面应用
  #      分层或配对 Bootstrap 分析"：函数级配对重采样 + 库为层的层内重采样；
  #      5 重复聚合为函数解出率 sfcr，B=10000，RNG 种子 42 可复现；
  #      --bootstrap hierarchical 追加重复级二阶段重采样（更保守））
  #     一张表覆盖全部 5 模式（每个 mode 固定与 respfuzzer 对比）；每个 mode
  #     6 行库连续排列，后接 -overall-micro（函数等权）与 -overall-macro
  #     （库等权）两行；mode=respfuzzer 为基线，差异与效应量列记为 `-`
  uv run python ../experiments/RQ1/analyze.py --model Llama-3.1-8B-Instruct
  ```
- 输入：`data/<mode>/<lib>_seeds[_meta]_rs*.json`
- 预期输出：4a 为 5 行（每行 `模式 [(库, seeds 数), ...]`，共 150 组）；4b 一张 markdown 表（列 `mode|lib|#func|avg sfcr|sfcr diff|sfcr diff CI|Cohen's h|Cohen's h CI`）——每 mode 6 行库 + 2 行 overall，给出 sfcr、相对 respfuzzer 的差值与 Cohen's h（附 small/medium/large 幅度标注，阈值 |h|<0.5 / 0.5–0.8 / ≥0.8）、95% 函数配对 bootstrap CI（CI 不含 0 才下强弱结论）

### 步骤 5（下游，可选）：抽样供 RQ2/RQ3

- 命令：`uv run python scripts/sample_seeds.py -i data/<mode>/<lib>_seeds_<model>_rs<N>.json -n 500`
- 输入：单个 run 的 seeds json
- 预期输出：同目录 `<lib>_seeds_sampled.json`（≤ 500 条，随机种子默认 4399，可用 `-s` 指定）；下游用法见 `experiments/RQ3/exp.md`
