# RQ2 实验复现手册

## 实验说明

在**同一份共享种子集**上对比三个 fuzzer 的行覆盖率 LC 与覆盖增量：**RespFuzzer**（本方法）、**miniFuzz4All**（LLM 直接生成测试）、**DyFuzz**（变异式，无 LLM）。本实验早期挂在 RQ3 名下，DyFuzz runner 至今仍传 `--rq RQ3`，故其产物前缀是 `rq3_dyfuzz_*`。

| 维度 | 取值 |
|---|---|
| 库（6） | nltk / numpy / pandas / scipy / sklearn / spacy，每库取 `<lib>_capped100_seeds.json` 的 **100 个函数**（合计 600） |
| 预算档（2） | `-lrequest 10`（计数预算）与 `-ltime 60`（墙钟预算）；两 flag 同时给时**先到者停** |
| 重复（5） | `--run-index 1…5`（旧脚本为 `RUN_INDEX`）；同一 `run_index` 在三组间派生**同一随机种子**（CRN） |
| 每组规模 | 2 × 6 × 5 = **30 run**；三组合计 **90 run**（本手册口径） |
| 配对凭据 | 同一 `(预算档, 库, run_index)` 键上三组的 `metadata.seed` 逐一相等，实际 fuzz 的 100 个函数完全相同 → **逐函数严格配对** |
| 产物三件套 | 每 run 一个 `.log` + 同 stem `_checkpoint.json`（身份/环境/`decode_config`/配对键/结果/逐 seed 事件） |

**模型范围（重要）**：本 RQ2 只采用 **`Qwen3.6-35B-A3B`** 这一批结果。RespFuzzer 另有 `Llama-3.1-8B-Instruct`（11-09~10）与 `gpt-oss-120b`（11-11~13）两批产物，但 **miniFuzz4All 只有 Qwen 批**（11-01~03，全盘核查无其他存放点），凑不齐三方同模型对比；DyFuzz 不调 LLM（checkpoint 里的 `model_name` 只是运行配置残留），装载层按 `no-llm` 放行。装载时非 Qwen 产物按 (组, 模型) 汇总进 Notes，不参与任何统计。

**预算语义不跨组等价**（正文必须点明，别把三组当"同预算"）：

| 组 | count flag | time flag | 单位含义 | 环境变量 |
|---|---|---|---|---|
| RespFuzzer | `-lrequest N` | `-ltime S` | 每 seed 的 LLM 请求数 / 秒数（种子与变异共用配额） | `RF_REQUEST_BUDGET`, `RF_TIME_BUDGET` |
| miniFuzz4All | `-lrequest N` | `-ltime S` | 每**函数**的 LLM 请求批次 / 秒数 | `F4A_REQUEST_BUDGET`, `F4A_TIME_BUDGET` |
| DyFuzz | `-lmutant N` | `-ltime S` | 每 seed 的变异体数（≈ 测试执行次数）/ 秒数 | `DYFUZZ_MUTANT_BUDGET`, `DYFUZZ_TIME_BUDGET` |

每 fuzzer 至少给一个 flag（无 config 兜底）；唯一例外是 miniFuzz4All，两个 flag 都不给时回落到 `config.toml` 的 `llm_fuzz_per_seed`。
- **`-lrequest 10` 档的跨组语义**：RespFuzzer / miniFuzz4All 的 10 = 每 seed 的 **LLM 请求数**；DyFuzz 不调 LLM，对应档位是 `-lmutant 10` = **限制每个种子的变异数量为 10**（≈10 次测试执行）。三者的 "10" 都是"每 seed 工作量配额"，但计数对象不同——描述统计与配对检验三组全收（DyFuzz 不剔除），正文解读必须写"配额语义不同"，不能说成"同等 10 次 LLM 调用"。

**主指标**：行覆盖率 `LC(f) = cov(f)/D_lib × 100`（%）。分子 `cov(f)` 是 fuzz 完函数 f 后 dcov 行位图命中数（checkpoint 逐函数 coverage = 库包目录内已执行行数）；分母 `D_lib` 取目标库**安装包整包**的 coverage.py 语句数（`coverage_denominators.json` 的 `package_statements`），与分子同宽，故 **LC ≤ 100%**。运行级另报覆盖增量 `Δpp = (final − init)/D_lib × 100`；`Δbits` 见 `rq2_runs_*.csv`；库版本与整包分母见 `rq2_denominators_*.csv`。统计口径与 RQ1 一致（函数级配对 bootstrap 95% CI、sign-flip 检验、Cohen's h 与 CI 同源、micro 函数等权 / macro 库等权，B=10000，RNG 种子 42 可复现）。

**实测开销**：成本**不作 coverage-成本曲线**——原始数据只有 run 级 `total_tokens/llm_calls` 总量，无逐调用 token/usage 落盘；final 状态的逐 run 汇报以 `rq2_runs_*.csv` 为准（每 run 一行：`final_lc_pct`、`delta_pp`、`llm_calls`、`*_tokens`、`test_execs`、`duration`），下表为 `rq2_resources_*.csv` 的 ALL-6LIBS 行（6 库×5 run 的均值/run；末列 Δpp = 覆盖增量 `(final−init)/D×100`，6 库整包语句数为分母；RespFuzzer `-ltime 60s` 行为 2026-11-19 重跑批，见 `rq2_respfuzzer_ltime_re_track.md`）：

| 预算档 | 组 | LLM 调用 | token | 测试执行 | 耗时 | Δpp |
|---|---|---|---|---|---|---|
| `-lrequest 10` | RespFuzzer | 1003 | 43.1 万 | — | 192 s | +0.71 |
| `-lrequest 10` | miniFuzz4All | 1000 | 21.5 万 | — | 805 s | +0.43 |
| `-lrequest 10` | DyFuzz | 0 | 0 | 933 | 1022 s | +0.04 |
| `-ltime 60s` | RespFuzzer | 5499 | 225 万 | — | 644 s | +1.46 |
| `-ltime 60s` | miniFuzz4All | 13237 | 267 万 | — | 6074 s | +1.86 |
| `-ltime 60s` | DyFuzz | 0 | 0 | 7202 | 5604 s | +0.05 |

`{RESPFUZZER}` 为仓库根目录。跑实验的命令（步骤 1–3 战役脚本、步骤 5 分析）执行 `uv run` 前须先进入隔离目录 `run_data/`（AGENTS.md 约定）；纯分析、无副作用的 `report_tables.py` 例外，直接在 `experiments/RQ2/` 下执行（见步骤 6）。战役脚本自身已 `cd` 到 `experiments/RQ2/run_data/`。逐 run 进度见 `experiments/RQ2/run_data/campaign_progress*.tsv`，人工跟踪表见仓库根 [`rq2_respfuzzer_track.md`](../../rq2_respfuzzer_track.md) / [`rq2_minifuzz4all_track.md`](../../rq2_minifuzz4all_track.md) / [`rq2_dyfuzz_track.md`](../../rq2_dyfuzz_track.md)。

## 数据准备

**1. 安装环境**
- 命令：`cd {RESPFUZZER} && uv sync`
- 输入：`pyproject.toml`、`uv.lock`
- 预期输出：`.venv/`（含 coverage、matplotlib 等分析依赖）

**2. 确认 `config.toml`**
- 命令：`cat config.toml`（不存在时 `cp config.toml.default config.toml` 再改）
- 输入：`config.toml.default`
- 预期输出：`[llm].model_name = "Qwen3.6-35B-A3B"` 且 `[llm].base_url` 指向该服务；RespFuzzer 与 miniFuzz4All **共用同一 `[llm]` 配置**，换模型即改这一处
- 注意：环境里不要设 `all_proxy` 等代理（局域网端点走代理会超时）

**3. 共享种子集（600 函数）**
- 命令：`uv run python experiments/RQ2/generate_rq2_seeds.py`
- 输入：RQ1 `respfuzzer` 模式 5 次重复的 `<lib>_seeds_*.json`
- 预期输出：`experiments/RQ2/<lib>_all_functions_seeds.json`（按 `func_path` 去重的全量参考集：nltk 376 / numpy 731 / pandas 600 / scipy 955 / sklearn 355 / spacy 270，共 3287）与 `<lib>_capped100_seeds.json`（固定 RNG `random.Random(4399)` 抽 `min(100, N)`，**100 × 6 = 600**）。三组战役读的都是同一批 capped100 文件，元素含 `func_path`；抽样口径详见 README「Seed Dataset」

**4. DyFuzz 就位（clone + 打补丁）**
- 命令：
  ```bash
  cd {RESPFUZZER}/experiments/RQ2
  git clone https://github.com/xiaxinmeng/DyFuzz.git
  cp ./DyFuzz-patch/* ./DyFuzz/
  ```
- 输入：上游仓库 + `DyFuzz-patch/`（我们的改动：接入 `run_respfuzzer.py`、coverage 记录、预算 flag）
- 预期输出：`DyFuzz/run_respfuzzer.py` 可执行；`DyFuzz/` 被 `.gitignore` 的 `**/DyFuzz/**` 排除（含嵌套 git 仓库，不随本仓库提交），重跑前须重新 clone 并同步补丁
- 注意：战役路径直接把 capped100 种子喂给打过补丁的 runner，**不需要**旧路径的 `<lib>_seeds_dyfuzz.json` 转换（那是 `convert_to_dyfuzz_format.sh` + `run_dyfuzz.sh` 的历史产物）

**5. 隔离运行目录**
- 命令：`mkdir -p {RESPFUZZER}/run_data {RESPFUZZER}/experiments/RQ2/run_data`
- 输入：—
- 预期输出：空目录；`experiments/RQ2/run_data/` 只作 cwd，产物落 `experiments/RQ2/`

**6. 生成 LC 分母表（`coverage_denominators.json`）**
- 命令：`uv run python experiments/RQ2/make_coverage_denominators.py`
- 输入：当前环境已安装的 6 个库（`--libs` 可指定；按导入模块名扫安装包目录，**不再依赖种子集**）
- 预期输出：`experiments/RQ2/coverage_denominators.json`（被 `.gitignore` 的 `experiments/**/*.json` 排除，按需再生）；`package_statements` 应为 nltk 60929 / numpy 109584 / pandas 256586 / scipy 216867 / sklearn 120821 / spacy 39770，并逐库打印版本与文件数
- **口径**：dcov 位图只统计目标库安装包目录内被执行的行，故 LC 分母取同一目录整包的 coverage.py 语句数 `package_statements`（与分子同宽，LC ≤ 100%）
- **何时必须重跑**：`uv pip install` 换了任何被测库版本（分母必须与数据版本一致）。复现步骤 5 之前必须先有本文件——`analyze.py` 缺它会直接报错退出，不静默猜口径

## 复现过程

RespFuzzer 与 miniFuzz4All 共用同一 LLM 端点（彼此必须串行），DyFuzz 纯本地可并行。三个战役脚本每次只跑**一个预算档**（6 库 × 5 重复），两档各跑一次即 30 run/组。

### 步骤 1：RespFuzzer 战役（30 run/模型）

- 命令：
  ```bash
  bash experiments/RQ2/run_rq2_respfuzzer_campaign.sh -lrequest 10
  bash experiments/RQ2/run_rq2_respfuzzer_campaign.sh -ltime 60
  ```
- 输入：`experiments/RQ2/<lib>_capped100_seeds.json`；`config.toml` 的 `[llm]`
- 预期输出：
  - `experiments/RQ2/rq2_respfuzzer_<lib>_capped100_<budget>_<model>_rep<N>_<ts>.log` + 同 stem `_checkpoint.json` × 30（2026-11-19 起命名增强：预算档 `req10`/`time60`、模型、重复号直接写进文件名，旧产物已同规则重命名）
  - 内部逐 (rep, lib) 执行 `cd experiments/RQ2/run_data && uv run -q fuzz fuzz_dataset <lib>_capped100_seeds.json --rq RQ2 --group respfuzzer --out-dir experiments/RQ2 --run-index <N> <flag> <value>`
  - `experiments/RQ2/run_data/campaign_progress_<model>.tsv`（**按模型隔离**，`status=done` 的 (budget, rep, lib) 键不重复跑；续跑用 `REPS_OVERRIDE="4 5"` 只补指定重复），每行含 `coverage=` 与 `duration_s=`
  - 参考耗时（Qwen 实测）：`-lrequest 10` ≈ 3.2 min/run，`-ltime 60` ≈ 10.6 min/run

### 步骤 2：miniFuzz4All 战役（30 run）

- 命令：
  ```bash
  bash experiments/RQ2/run_rq2_budget_campaign.sh -lrequest 10 capped100
  bash experiments/RQ2/run_rq2_budget_campaign.sh -ltime 60   capped100
  ```
  （`run_rq2_budget_campaign.sh` 是按 rep 串行 5 次的外层驱动，内部调 `RUN_INDEX=<rep> bash run_rq2_campaign.sh <flag> <value> capped100`；第 3 参数省略时默认 `all_functions`，**必须显式写 `capped100`**）
- 输入：同步骤 1 的种子集与 `[llm]` 配置；实现位于 `miniFuzz4All/fuzz_dataset.py`
- 预期输出：`experiments/RQ2/rq2_fuzz4all_<lib>_capped100_<ts>{.log,_checkpoint.json}` × 30；进度追加到 `run_data/campaign_progress.tsv`（该脚本**无模型维**，换模型跑前先自行改名或挪目录，否则续跑会误判已完成）
- 参考耗时：`-lrequest 10` 均值 13.0 min/run（≈6.5 h/模型）；`-ltime 60` 均值 101.2 min/run（≈50.6 h/模型，贴着 60 s × 100 seed 的预算硬耗时）

### 步骤 3：DyFuzz 战役（30 run）

- 命令：
  ```bash
  bash experiments/RQ2/run_rq2_dyfuzz_campaign.sh -lmutant 10
  bash experiments/RQ2/run_rq2_dyfuzz_campaign.sh -ltime 60
  ```
- 输入：同一种子集；DyFuzz 无 LLM；`DyFuzz/run_respfuzzer.py`（数据准备 4）
- 预期输出：`experiments/RQ2/rq3_dyfuzz_<lib>_capped100_<ts>{.log,_checkpoint.json}` × 30 —— **前缀仍是 `rq3_`**（runner 内硬编码 `start_run("RQ3", group="dyfuzz")`，历史 RQ3→RQ2 迁移遗留；分析侧用 `rq*_` 前缀兼容），进度同样落 `run_data/campaign_progress.tsv`
- 注意：DyFuzz 的 checkpoint `events` 为空，逐函数覆盖轨迹由同名 `.log` 里的 `Current coverage after fuzzing <func>: N bits` 解析

### 步骤 4：产物核对

- 命令：
  ```bash
  cd {RESPFUZZER}/experiments/RQ2
  for g in rq2_respfuzzer rq2_fuzz4all rq3_dyfuzz; do
    printf '%-16s ckpt=%s log=%s\n' "$g" \
      "$(ls ${g}_*_checkpoint.json | wc -l)" "$(ls ${g}_*.log | wc -l)"
  done
  ```
- 输入：步骤 1–3 的产物
- 预期输出：Qwen 口径下 checkpoint 至少 60 / 60 / 60（RespFuzzer 主目录 = 三模型 `-lrequest 10` 90 对 + Qwen `-ltime 60` 30 对 = 120；旧 RespFuzzer-ltime 180 件存档于 `experiments/RQ2/old_data/`，不进分析）；`log` 数可大于 checkpoint 数——**孤儿 `.log` 即中途被杀、未写出 checkpoint 的运行**，本次有 6 个（2 个 `rq2_fuzz4all_spacy`、3 个 `rq2_respfuzzer_{pandas,scipy,sklearn}`、1 个 `rq3_dyfuzz_numpy`），一律不进分析

### 步骤 5：配对分析（Markdown 直读）

- 命令：
  ```bash
  cd {RESPFUZZER}/run_data
  uv run python ../experiments/RQ2/analyze.py                 # 默认 --model Qwen3.6-35B-A3B
  uv run python ../experiments/RQ2/analyze.py --budget count  # 只看请求数档
  uv run python ../experiments/RQ2/analyze.py --bootstrap hierarchical
  ```
- 输入：`experiments/RQ2/rq*_{checkpoint,log}`（仅 `status=completed` 且带 `run_index` 配对键）
- 预期输出：表 1 描述统计（每 budget × lib × 组一行）+ 表 2 两两逐函数配对（3 pair × 2 档 × (6 库 + micro + macro) = **48 行**，列 `pair|budget|lib|n func|n run|LC_A|LC_B|Δ|Δ 95% CI|p|sig|Cohen's h|h CI|h 幅度|Cohen's d`）
- 预期装载数：`miniFuzz4All=60, DyFuzz=60, RespFuzzer=60`，Notes 只剩一条 `按 --model 滤除：…` 汇总；**不应再出现 RespFuzzer 被跳过的条目**（历史缺陷：装载层曾按 `len(events) != seed_count` 判完成，而 RespFuzzer 的 events 混记 `mutant_executed` 等 7 类事件，导致 180 run 全被判为未完成）
- 逐函数轨迹口径：只认 `event == "seed_fuzzed"`（RespFuzzer / miniFuzz4All），DyFuzz 回退解析日志

### 步骤 6：汇报表与图

- 命令：
  ```bash
  cd {RESPFUZZER}/experiments/RQ2
  uv run python report_tables.py --latex --figure
  uv run python report_tables.py --tables runs,levels --budget time   # 单档快查
  ```
- `report_tables.py` 是纯分析脚本（只读产物、写 `results_qwen/`，路径按 `__file__` 解析、与 cwd 无关），**直接在 `experiments/RQ2/` 执行即可，不必进 `run_data/` 隔离目录**；`run_data/` 只服务跑实验的战役脚本（步骤 1–3 与步骤 5 的 `analyze.py` 仍按原约定在隔离目录下跑）
- 输入：同步骤 5；分层表另读 `runs/RQ1/labels_<lib>.json`
- 预期输出：`experiments/RQ2/results_qwen/`（文件名 `rq2_<表>_<model>.<ext>`）

| 产物 | 内容 | 行数/份数 |
|---|---|---|
| `rq2_runs_*.csv` | 逐 run 明细：`seed`（CRN 凭据）、init/final/Δbits、init/final LC%、Δpp、LLM 调用、token、测试执行、耗时、来源 checkpoint | 180 |
| `rq2_levels_*.csv` | 三组 LC% 点值 + 函数级 bootstrap 95% CI，6 库 + overall-micro/macro；并列 init/final bits 与 Δpp | 48 |
| `rq2_paired_*.csv` | 3 pair × 2 档 × (6 库 + micro + macro)：Δpp、CI、sign-flip p、sig、Cohen's h(+CI)、dz | 48 |
| `rq2_resources_*.csv` | 实际用量 + 单位增量成本（`llm_calls_per_pp` / `tokens_per_pp`，按 Δpp） | 42（6 库 + ALL × 3 组 × 2 档） |
| `rq2_strata_*.csv` | 按 RQ1 三维度标签分层的配对差 CI + h + p，含 `unknown` 层 | 54 |
| `rq2_denominators_*.csv` | 每库 LC 分母：整包语句数/文件数 + 库版本（口径见数据准备 6） | 6 |
| `rq2_main_<model>_{req10,time60}.pdf/png` | 主图：(a) 三组 LC% + 95% CI，(b) 相对 RespFuzzer 配对差 + 95% CI（含 0 参考线） | 2 档各 1 |
| `rq2_bylib_<model>_{req10,time60}.pdf/png` | 附录：每库一面板的相对 RespFuzzer 配对差（各面板横轴独立、均含 0） | 2 档各 1 |
| `rq2_cvertime_<model>_req10.pdf/png` | `-lrequest` 档 coverage-time：每库一面板，三组 5-run 均值线 + min–max 带；t0 = 初始种子覆盖算完，run 结束后保持 final 值 | 1 |
| `rq2_analyze_*.md` | 步骤 5 的 Markdown 全量输出存档（首行注明生成命令与 B/seed） | 1 |

- 预期一致性：`rq2_paired_*.csv` 与步骤 5 表 2 **逐值可对拍**（pair 顺序与 RNG 流 `[42, pair_i, budget_i, lib_i, k]` 完全一致；实测 48/48 行在各自小数位舍入内一致（overall 行标签互译 `micro (6 libs)` ↔ `overall-micro` 后 0 差异：LC 2 位 / Δ 与 CI 3 位 / h 4 位））
- `--latex` 追加同名 `.tex`（裸 `tabular`，不含浮动体/caption）
- 读表注意：
  - **45/48 行 `sig=*`**：`n.s.` 3 行都在 `-ltime 60s`——`miniFuzz4All−RespFuzzer` micro/macro（Δ=−0.072 pp，|Δ|<0.1 pp，总体打平：sign-flip n.s.，bootstrap CI [−0.136, −0.004] 恰不含 0，属 borderline）与 `DyFuzz−RespFuzzer` nltk（Δ=−0.002 pp，CI 含 0）；600 函数配对下 0.1 pp 的差也能显著，强弱一律看 `|Δ|` 与效应量：LC 压缩了绝对尺度（本批 |h| = 0.000–0.055，全属 small/可忽略），跨组比较可参考不受压缩影响的 `cohens_dz`（本批约 0.08–11.2）
  - **逐库并非全胜**（`diff_pp`，正 = 前者优于 RespFuzzer）：`DyFuzz−RespFuzzer`——`-lrequest 10` RespFuzzer 领先 5 库、**nltk 由 DyFuzz 领先**（+0.219*）；`-ltime 60s` RespFuzzer 领先 5 库、**nltk 打平**（−0.002，n.s.）。`miniFuzz4All−RespFuzzer`——两档的 numpy / scipy / sklearn 都由 RespFuzzer 领先，但 **nltk、pandas 两档都由 miniFuzz4All 领先**（`-lrequest 10` +0.134 / +0.380；`-ltime 60s` +0.473 / +0.423），`-ltime 60s` **spacy 也由 miniFuzz4All 领先**（+0.761）→ 结论必须按 (库, 预算档) 逐格陈述，别写"全面优于"；总体 micro：`-lrequest 10` RespFuzzer 显著领先 miniFuzz4All / DyFuzz（−0.210 / −0.365 pp），`-ltime 60s` 与 miniFuzz4All 打平（−0.072，n.s.）并显著领先 DyFuzz（−0.751）
  - **LC 与旧 gain 口径的差异**：LC 排的是**最终**累积覆盖，配对两组的种子阶段覆盖并不总相等，要排除种子阶段差异就看表 1 的 Δpp（覆盖增量）。注：`-ltime 60s` 数据已于 2026-11-19 整档重跑（`rq2_respfuzzer_ltime_re_track.md`），早期"4/48 格符号翻转"清单基于旧批 + 旧 gain 口径，已作废不再引用
  - 六库每库都是 100 函数，故 `overall-micro` 与 `overall-macro` 的**点值相同、CI 不同**（micro 池化 600 函数重采样，macro 为 6 库重采样分布等权平均），两行都保留
  - **coverage-time 图**（`rq2_cvertime_*_req10`）：x = 相对时间（三组同轴可比，t0 = 初始种子覆盖算完），线为 5-run 均值、带为同一时刻 5 条 run 的 min–max，run 结束后保持其 final 值；各组总时长不同是预算执行节奏的结果（`-lrequest 10` 下 RespFuzzer 收尾最快、DyFuzz 最慢），DyFuzz 的配额按"跨组语义"读


### 步骤 7（历史路径，本批未用）：`run_*.sh` / `batch_run.sh` / `report.py`

- 命令：
  ```bash
  cd {RESPFUZZER}/experiments/RQ2 && mkdir -p run_data && cd run_data
  RF_REQUEST_BUDGET=500 RF_TIME_BUDGET=60 \
  F4A_REQUEST_BUDGET=500 F4A_TIME_BUDGET=60 \
  DYFUZZ_MUTANT_BUDGET=500 DYFUZZ_TIME_BUDGET=60 \
  RESPFUZZER_DATA_DIR=/path/to/seeds bash ../batch_run.sh 1
  uv run experiments/RQ2/report.py
  ```
- 输入：`$RESPFUZZER_DATA_DIR/<lib>_seeds_sampled.json`（默认目录 `RQ2_data_new_111/`，由 `scripts/sample_seeds.sh` 生成，DyFuzz 另需 `scripts/convert_to_dyfuzz_format.sh` 的 `<lib>_seeds_dyfuzz.json`）
- 预期输出：`batch_run.sh <n>` 按 `RUN_INDEX=i` 依次调 `run_respfuzzer.sh` / `run_fuzz4all.sh` / `run_dyfuzz.sh`（同轮次三组同种子），产物前缀 `rq3_*`；`report.py` 只 glob `rq3_*.log`，输出 12 库 coverage 百分比的 Markdown 表
- 为何本批不走这条路：种子集换成 capped100、预算口径改成单档 flag、命名改成 `rq2_*`（DyFuzz 除外），三件都已由步骤 1–3 的战役脚本承担；`report.py` 在 RQ2 目录只能看到 DyFuzz 与早期预跑件，正文的 LC 配对口径一律以 `analyze.py` / `report_tables.py` 为准

## 产物位置说明

| 位置 | 内容 | 性质 |
|---|---|---|
| `experiments/RQ2/rq2_respfuzzer_*` / `rq2_fuzz4all_*` / `rq3_dyfuzz_*` | 三组 run 的 `.log` + `_checkpoint.json`（`rq3_dyfuzz` 为历史命名） | **事实来源**（`analyze.py` / `report_tables.py` 读这里） |
| `experiments/RQ2/<lib>_capped100_seeds.json`（+ `_all_functions_seeds.json`） | 三组共用的 600 函数种子集（+ 去重全量参考集 3287） | 输入 |
| `experiments/RQ2/run_data/campaign_progress*.tsv` | 逐 run 进度（RespFuzzer 按模型分文件，另两组单文件） | 运行日志 |
| `experiments/RQ2/{analyze,report_tables,report}.py` | 配对口径分析 / 论文表与图 / 旧口径 coverage 报表 | 分析入口 |
| `experiments/RQ2/run_rq2_*_campaign.sh` | 三组战役脚本（含 `run_rq2_campaign.sh` 单 rep 内核） | 驱动 |
| `experiments/RQ2/{batch_run,run_respfuzzer,run_fuzz4all,run_dyfuzz}.sh` | 历史路径（`seeds_sampled` + `--rq RQ3`） | 旧驱动，本批未用 |
| `experiments/RQ2/miniFuzz4All/`、`DyFuzz/` + `DyFuzz-patch/` | 对照 fuzzer 实现；`DyFuzz/` 不入 git，由补丁目录同步 | 第三方代码 |
| `experiments/RQ2/rq2.md` | 本手册 | 文档 |
| `runs/RQ1/labels_<lib>.json` | 三维度分层标签（`strata` 表用） | 上游标签 |
| `experiments/RQ2/results_qwen/` | 6 份 CSV/.tex + 4 组图（pdf/png）+ analyze 存档 | 分析产物，不入 git（被 `experiments/**/results_*/` 忽略），随 artifact 上传 |

**已知缺件与例外（报告 Notes 会如实列出，不静默）**

- miniFuzz4All 仅 `Qwen3.6-35B-A3B` 一批；`Llama-3.1-8B-Instruct` / `gpt-oss-120b` 未跑 → 那两模型的 `rq2_paired` 只有 `DyFuzz−RespFuzzer` 一对，涉及 miniFuzz4All 的行缺席并给出"无共同 run_index"说明
- miniFuzz4All 侧只有 1 个非 Qwen 件 `rq3_fuzz4all_nltk_20261030T120256`（10-30 早期预跑）：`gpt-oss-120b`、无 `run_index`、无 `library` 字段、`-ltime 10` 与 `request_budget=2` 混记、日志仅 2 KB → 既无配对键，也被 `--model` 滤除
- 6 个孤儿 `.log`（未写出 checkpoint）——见步骤 4
- 分层标签只部分命中 capped100 函数：nltk 67 / numpy 80 / pandas 78 / scipy 100 / sklearn 95 / spacy 76（未命中集中在 `*.demo` 一类嵌套/演示函数），`strata` 表统一记入 `unknown` 层，命中率由脚本按库打印
