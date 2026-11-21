# RQ3 实验复现手册

## 实验说明

对 RespFuzzer 的**语义驱动变异**做消融：关掉其中一个能力后看行覆盖率 LC 掉多少。三个消融臂都与 **Full 臂**（完整 RespFuzzer）在同 `(库, run_index)` 上逐函数配对；Full 臂**不重跑**，直接复用 RQ2 已跑完的 RespFuzzer 结果。**NSF（无语义过滤）已从消融设计中移除**：判据已统一为「包含有效调用」、异常也计入模糊测试信号后，语义过滤不再构成独立可消融维度（该旁路开关已从 `llm_mutator` / 消融入口删除，历史 NSF 产物仅存档）。

| 维度 | 取值 |
|---|---|
| 配置（4） | `Full`（全开基线，复用 RQ2）/ `NL` 无 LLM 变异 / `NP` 无传统参数变异 / `NCF` 无覆盖反馈（`NSF` 已移除，见上） |
| 库（6） | nltk / numpy / pandas / scipy / sklearn / spacy，每库 `experiments/RQ2/<lib>_capped100_seeds.json` 的 **100 个函数**（合计 600） |
| 重复（5） | `--run-index 1…5`；同一 `run_index` 在各臂间经 `prepare_run_seed` 派生**同一随机种子**（CRN，实测 `run_index=1 → seed 104045904`） |
| 每臂规模 | 3 消融臂 × 6 库 × 5 重复 = **90 run**（Full 另计 RQ2 的 30 run） |
| 配对凭据 | 同一 `(mode, lib, run_index)` 键上 `metadata.seed` 逐一相等、`seed_count=100`、数据集同路径 → **逐函数严格配对** |
| 产物三件套 | 每 run 一个 `.log` + 同 stem `_checkpoint.json`（身份/`metadata` 配对键/`results` 结果与逐 seed 事件） |

**消融开关**（统一入口 `experiments/RQ3/fuzz_exp-RQ3.py --mode <MODE>`）：

| Mode | 效果 |
|---|---|
| `Full` | LLM 变异 + 参数变异 + 覆盖反馈全开（= RQ2 RespFuzzer 配置，故复用；判据 = 包含有效调用，异常计入信号） |
| `NL` | No LLM-based mutation |
| `NP` | No traditional parameter mutation |
| `NCF` | No coverage feedback |

`--mode` 传入 `NSF` 会直接报错退出（设计已移除，防误跑）；装载期遇到历史 `mode-nsf` 产物同样静默跳过（同 `-ltime` 存档批规则，不进 Notes）。
**模型范围（3 模型 × 2 档，正文只取 Qwen count 一行）**：`Qwen3.6-35B-A3B`（正文主线）/ `Llama-3.1-8B-Instruct` / `gpt-oss-120b`，每模型 count 批 90 run；另有 `-ltime 60` 时间预算批每模型 60 run（只跑 NP/NCF——NL 分支无 LLM 循环、时间预算惰性，跑完也等价 count 批，故跳过；Full 不跑，复用 RQ2 的 `-ltime 60` 30 run）。当前 `experiments/RQ3/` 共 **631 个 checkpoint（630 completed + 1 failed）+ 631 个 log、孤儿 log 0**（其中 `mode-nsf` 180 件为**历史 NSF 批，只存档、不装载**）。**分析范围固定为 count 批**：ltime 批与 nsf 批装载期直接跳过（不进 Notes，`--budget` 开关已移除）；**缺省一次输出三个模型的结果**（`analyze.py` 每模型一段、`report_tables.py` 每模型一套，`--model <模型名>` 只看一个）。
**主指标与统计口径**（与 RQ2 完全同口径，可直接对拍）：

- 行覆盖率 `LC(f) = cov(f)/D_lib × 100`（%）。分子 `cov(f)` 取 checkpoint 的 `event == "seed_fuzzed"` 逐函数轨迹（`analyze.py` 只认该事件，轨迹不全整 run 跳过），等价于 `init × (1 + gain/100)` 还原出的**运行后全局累积覆盖**；分母 `D_lib` 取 `experiments/RQ2/coverage_denominators.json` 的 `package_statements`（目标库**安装包整包**的 coverage.py 语句数，与 dcov 位图同宽），故 **LC ≤ 100%**。
- 配对差 `ΔLC = LC_A − LC_B`（A=消融臂、B=Full，pp）：函数取配对运行的交集、按字典序对齐，run 取共同 `run_index`；**先对 5 次重复取均值再做函数级配对**（cluster，`--bootstrap hierarchical` 追加重复级二阶段重采样，点估计与 p 不变、仅 CI 更保守）。
- 统计量：函数级配对 bootstrap 95% CI（B=10000）、sign-flip 双侧检验、Cohen's h 与 CI 同源、配对 `cohens_dz`；`micro` 函数等权 / `macro` 库等权，RNG 种子 42，RNG 流 `[42, pair_i, lib_i, k]` 与 `analyze.py` 同构。

**Qwen / count 批正文结果**（`rq3_main_*.csv` 的 `overall-micro` 行，Δ 为相对 Full）：

| 配置 | LC %（95% CI） | ΔLC pp（95% CI） | p | sig |
|---|---|---|---|---|
| Full | 16.959 [16.391, 17.523] | — | — | — |
| NL | 16.538 [15.982, 17.096] | −0.421 [−0.441, −0.401] | <0.001 | * |
| NP | 16.713 [16.183, 17.245] | −0.246 [−0.252, −0.240] | <0.001 | * |
| NCF | 16.837 [16.302, 17.374] | −0.122 [−0.130, −0.115] | <0.001 | * |

逐库 18 格中 **17 格 `sig=*`**（唯一 `n.s.` 是 NCF/numpy，Δ=+0.006）。总体（overall-micro）排序：**NL −0.421 > NP −0.246 > NCF −0.122**（都是损失）。逐库看存在反例、不能写"全面优于/劣于"：NL 六库全负（−0.078 ~ −0.905）；NP 仅 pandas 为正（+0.106），nltk 降最多（−0.985）；NCF 仅 pandas 为正（+0.336）、numpy 打平（+0.006，n.s.）。`|h|` 全在 0.000–0.025（LC 把绝对尺度压扁，全属 `small`），跨配置比较看 `cohens_dz`；600 函数配对下 0.1 pp 也显著，**强弱一律按 `|ΔLC|` 与效应量读，别只看星号**。

**三模型总体对照**（同为 `rq3_main_*.csv` 的 `overall-micro` 行；三套结果各自独立出表、不合并，ΔLC 单位 pp，全部行 p<0.001 即 `sig=*`）：

| 模型 | Full LC % | NL ΔLC | NP ΔLC | NCF ΔLC |
|---|---|---|---|---|
| Qwen3.6-35B-A3B（正文） | 16.959 | −0.421 [−0.441, −0.401] | −0.246 [−0.252, −0.240] | −0.122 [−0.130, −0.115] |
| Llama-3.1-8B-Instruct | 17.228 | −0.639 [−0.654, −0.623] | +0.077 [+0.069, +0.085] | +0.048 [+0.041, +0.056] |
| gpt-oss-120b | 17.176 | −0.613 [−0.645, −0.581] | +0.104 [+0.097, +0.110] | −0.074 [−0.080, −0.067] |

跨模型一致的一条：**NL 三模型全部显著为负且是损失最大的臂**（−0.42 ~ −0.64 pp）。NP 与 NCF 的符号随模型翻转（Qwen 两臂为负；Llama 的 NP/NCF 与 gpt-oss 的 NP 为正），量级 ≤0.25 pp（NCF ≤0.12 pp），属**模型敏感的中性区**，正文按 Qwen 主线陈述、此处如实披露。

**实测开销**（`rq3_resources_*.csv` 的 ALL-6LIBS 行，单 run 均值；LLM 配置同 RQ2 的 `-lrequest 10`）：

| 配置 | LLM 调用 | token | 耗时 | 备注 |
|---|---|---|---|---|
| Full | 1003 | 43.1 万 | 192 s | 复用 RQ2 RespFuzzer |
| NL | 0 | 0 | 42 s | 分支不调 LLM |
| NP | 1002 | 43.0 万 | 199 s | |
| NCF | 1003 | 43.0 万 | 186 s | |

`{RESPFUZZER}` 为仓库根目录。跑实验的命令（步骤 1–2 战役脚本、步骤 4 `analyze.py`）执行 `uv run` 前须先进入隔离目录 `run_data/`（AGENTS.md 约定；战役脚本自身已 `cd` 到 `experiments/RQ3/run_data/`）；纯分析、无副作用的 `report_tables.py` 例外，直接在 `experiments/RQ3/` 下执行（见步骤 5）。逐 run 进度见 `experiments/RQ3/run_data/campaign_progress*.tsv`——count 批是 `campaign_progress_<model>.tsv`（**Qwen 批用的是早期未带模型后缀的 `campaign_progress.tsv`**），时间预算批同理加 `ltime_` 前缀；人工跟踪表见仓库根 [`rq3_Qwen3.6-35B-A3B_track.md`](../../rq3_Qwen3.6-35B-A3B_track.md) / [`rq3_Llama-3.1-8B-Instruct_track.md`](../../rq3_Llama-3.1-8B-Instruct_track.md) / [`rq3_gpt-oss-120b_track.md`](../../rq3_gpt-oss-120b_track.md) 及各自的 `*_ltime_track.md`。

## 数据准备

**1. 安装环境**
- 命令：`cd {RESPFUZZER} && uv sync`
- 输入：`pyproject.toml`、`uv.lock`
- 预期输出：`.venv/`（含 coverage、matplotlib 等分析依赖）

**2. 确认 `config.toml`**
- 命令：`cat config.toml`（不存在时 `cp config.toml.default config.toml` 再改）
- 输入：`config.toml.default`
- 预期输出：`[llm].model_name = "Qwen3.6-35B-A3B"`（换模型即改这一处，进度文件名与分析默认值都跟着变）、`[fuzz].llm_fuzz_per_seed = 10`（= RQ2 的 `-lrequest 10`）、`[fuzz] max_workers = 10`
- 注意：环境里不要设 `all_proxy` 等代理（局域网端点走代理会超时）

**3. 共享种子集（600 函数）**
- 命令：**无需重新生成**——三臂与 Full 臂都读 `experiments/RQ2/<lib>_capped100_seeds.json`（2026-11-01 由 `Random(4399)` 抽样，此后未改动）；确缺时 `uv run python experiments/RQ2/generate_rq2_seeds.py`
- 输入：RQ1 `respfuzzer` 模式 5 次重复的 `<lib>_seeds_*.json`
- 预期输出：`<lib>_capped100_seeds.json` × 6（100/库），口径见 [`../RQ2/rq2.md`](../RQ2/rq2.md) 数据准备 3

**4. LC 分母表（`coverage_denominators.json`）**
- 命令：`uv run python experiments/RQ2/make_coverage_denominators.py`
- 输入：当前环境已安装的 6 个库（按导入模块名扫安装包目录，不依赖种子集）
- 预期输出：`experiments/RQ2/coverage_denominators.json`；`package_statements` 应为 nltk 60929 / numpy 109584 / pandas 256586 / scipy 216867 / sklearn 120821 / spacy 39770
- **何时必须重跑**：换了任何被测库版本（分母必须与数据版本一致）。`report_tables.py` 缺它会直接报错退出，不静默猜口径；RQ3 复用这份文件，自己不生成

**5. 隔离运行目录**
- 命令：`mkdir -p {RESPFUZZER}/experiments/RQ3/run_data`
- 输入：—
- 预期输出：空目录；`experiments/RQ3/run_data/` 只作 cwd，产物落 `experiments/RQ3/`

**6. Full 臂数据只读核对（复用 RQ2，不重跑）**
- 命令：
  ```bash
  ls {RESPFUZZER}/experiments/RQ2/rq2_respfuzzer_*_req10_Qwen3.6-35B-A3B_*_checkpoint.json | wc -l   # 30（不带模型过滤是 90，三模型各 30）
  cd {RESPFUZZER}/experiments/RQ3/run_data && uv run python ../analyze.py --mode full
  ```
- 输入：`experiments/RQ2/rq2_respfuzzer_<lib>_capped100_<model>_rep<N>_*.log` + 同 stem `_checkpoint.json`（`status=completed`、`metadata.run_index=1..5`）
- 预期输出：`ls` = 30；analyze 首行 `加载运行数: Full=30, NL=30, NP=30, NCF=30`（装载数**不**随 `--mode` 变，`--mode full` 只筛表格）+ `[model] 按 --model=Qwen3.6-35B-A3B 过滤，跳过 480 个非目标模型 checkpoint`，表 1 只剩 Full 的 6 行（表 2 无 pair，显示「暂无可配对数据」）
- **等价性论证**（复用 vs 在 RQ3 重跑 Full）：同种子集、同三开关全开、同 `-lrequest 10` 预算、同 `run_index→seed` 派生、同 `config.toml`（并发/温度/max_tokens）、逐函数轨迹同为 `seed_fuzzed` 100/100；两个入口同源——`fuzz_exp-RQ3.py` 是 `respfuzzer.cmd.fuzz:fuzz_dataset` 加三个消融开关的分叉，三开关全开即默认路径。论文口径可写 "Full 配置复用 RQ2 的 RespFuzzer 运行（同一配置、同一预算、同一种子集、同一随机种子派生）"
- **预算隔离（必须遵守）**：RQ2 目录里并存 RQ2 自己的 `-ltime 60` campaign 产物（同库同 `run_index`、时间戳更晚，单种子约 5.5k 次调用 ≈ count 批的 5.5 倍）。分析只认固定次数批：`analyze.py` / `report_tables.py` 在装载期把 rank=2 的「仅时间预算」件**直接跳过**（不与 count 件竞争、不进 Notes），RQ3 目录自己的 ltime 批同理；`_budget_rank` 的 `0=request / 1=无字段 / 2=仅时间` 分类仍保留，供查档与单测用

## 复现过程

三个消融臂共用一个战役脚本，每次只跑**一个 mode**（6 库 × 5 重复 = 30 run），预算固定读 config 的 `llm_fuzz_per_seed`（入口不接 `-lrequest/-ltime`）；脚本自带断点续跑（进度里 `status=done` 的 `(mode, rep, lib)` 不重跑）。传入 `NSF` 会报错退出（设计已移除）。

### 步骤 1：消融战役 count 批（3 臂 × 30 = 90 run/模型）

- 命令：
  ```bash
  bash experiments/RQ3/run_rq3_ablation_campaign.sh NL
  bash experiments/RQ3/run_rq3_ablation_campaign.sh NP
  bash experiments/RQ3/run_rq3_ablation_campaign.sh NCF
  # 续跑指定重复：REPS_OVERRIDE="4 5" bash experiments/RQ3/run_rq3_ablation_campaign.sh NL
  ```
- 输入：`experiments/RQ2/<lib>_capped100_seeds.json`；`config.toml` 的 `[llm]` / `[fuzz]`
- 预期输出：
  - `experiments/RQ3/rq3_respfuzzer_<lib>_capped100_<ts>_mode-<mode|lower>.log` + 同 stem `_checkpoint.json` × 30/臂（Full 组文件名不带 `_mode-` 段，但 checkpoint 里恒有 `mode` 字段）
  - 脚本内部逐 (rep, lib) 执行 `cd experiments/RQ3/run_data && uv run python ../fuzz_exp-RQ3.py <seeds.json> --mode <MODE> --run-index <N> --out-dir <experiments/RQ3>`
  - `experiments/RQ3/run_data/campaign_progress_<model>.tsv`（**按模型隔离**；Qwen 批为早期未带后缀的 `campaign_progress.tsv`），每行 `fuzzer=respfuzzer-rq3 mode=<M> run_index=<N> library=<lib> status=done coverage=… duration_s=…`
- 参考耗时：单 run ≈ 1–7 min（NL 约 42 s/run，其余臂 ≈ 150–200 s/run）

### 步骤 2（存档批，**不进入分析**）：`-ltime 60` 时间预算批（2 臂 × 30 = 60 run/模型）

- 命令：`bash experiments/RQ3/run_rq3_ltime_campaign.sh NP|NCF`（`TIME_BUDGET` 默认 60；传 `NSF` 会报错退出）
- 输入：同步骤 1；`-ltime 60` 经 `normalize_budget_args` 归一（每种子 60 s，先耗尽者停）
- 预期输出：同名产物再各 +30（时间戳更晚），进度落 `run_data/campaign_progress_ltime_<model>.tsv`（`fuzzer=respfuzzer-rq3-ltime`）
- 注意：**NL 与 Full 不在此战役**——NL 沿用 count 批，Full 复用 RQ2 RespFuzzer `-ltime 60` 的 30 个 run。本批与 count 批**单种子请求数不可比**，只作存档；`analyze.py` / `report_tables.py` 已不接受 `--budget`，装载期直接跳过 `time_budget` 件，本批**不产生任何统计结果**

### 步骤 3：产物核对

- 命令：
  ```bash
  cd {RESPFUZZER}/experiments/RQ3
  printf 'ckpt=%s log=%s\n' "$(ls rq3_respfuzzer_*_checkpoint.json | wc -l)" "$(ls rq3_respfuzzer_*.log | wc -l)"
  for m in nl np nsf ncf; do printf 'mode-%-4s %s\n' "$m" "$(ls rq3_respfuzzer_*_mode-${m}_checkpoint.json | wc -l)"; done
  awk -F'[ =]' '/status=done/{d++} END{print "progress done:", d}' run_data/campaign_progress_*.tsv
  ```
- 输入：步骤 1–2 的产物
- 预期输出：当前三模型合计 `ckpt=631 log=631`（孤儿 log 0；630 completed + 1 failed）；`mode-nl 90`（只有 count 批）、`mode-np 180`、`mode-ncf 180`（count + `-ltime 60` 各 90），单模型单批每 mode 30；`mode-nsf 180` 为**历史 NSF 批，只存档、不装载**；`ckpt == log` 说明没有中途被杀的 run；progress 的 `status=done` 计数 630

### 步骤 4：配对分析（Markdown 直读）

- 命令：
  ```bash
  cd {RESPFUZZER}/experiments/RQ3 && mkdir -p run_data && cd run_data
  uv run python ../analyze.py                                    # 缺省三模型各一段（Qwen / Llama / gpt-oss）
  uv run python ../analyze.py --model Qwen3.6-35B-A3B            # 只看一个模型
  uv run python ../analyze.py --model none                       # 不过滤（三模型互顶，仅自查）
  uv run python ../analyze.py --bootstrap hierarchical           # 函数+重复两阶段重采样（更保守）
  ```
- 输入：缺省已同时装载**消融臂 `experiments/RQ3/` + 复用的 Full 臂 `experiments/RQ2/`**（只给本目录会得到 `Full=0`、表 2 空白；需要别处产物时 `--log-dir . ../../RQ2` 显式传）
- 预期输出：三个模型各一段（标题行 `## 模型 …`），每段先打印 `加载运行数: Full=30, NL=30, NP=30, NCF=30`（三模型 count 批齐全）与 `[model] 按 --model=… 过滤，跳过 … 个非目标模型 checkpoint`（Qwen 段 480、Llama/gpt-oss 段各 510），随后表 1 描述统计（每次运行一行）+ 表 2 三臂 vs Full 逐函数配对（每模型 3 × (6 库 + micro + macro) = **24 行**，列 `pair|mode|lib|n func|n run|gain_A|gain_B|Δ|Δ 95% CI|p|sig|Cohen's h|h CI|h 幅度|Cohen's d`）；每段 Notes 仅 1 条（三模型共用的那件 failed checkpoint——历史 nsf / ltime 产物静默跳过，不进 Notes）
- 口径提示：`analyze.py` 的 Δ 用**相对增益** `gain = (cov − init)/init × 100`（抵消各次运行 `initial_coverage` 抖动），与 `report_tables.py` 的 **LC 绝对口径**同为逐函数配对、同 RNG 流，方向大体一致而数值不同（init 不齐的边界情形会翻符号，见「局限性」）；正文引用哪一列就在正文里写死口径

### 步骤 5：汇报表与图

- 命令：
  ```bash
  cd {RESPFUZZER}/experiments/RQ3
  uv run python report_tables.py                       # 三模型 × (4 csv + main.tex + 主图)
  uv run python report_tables.py --tables main,runs
  uv run python report_tables.py --model Qwen3.6-35B-A3B   # 只出一个模型
  uv run python report_tables.py --model none --no-figure --outdir /tmp/rq3_check
  ```
- `report_tables.py` 是纯分析脚本（只读产物、写 `results_qwen/`，路径按 `__file__` 解析、与 cwd 无关），**直接在 `experiments/RQ3/` 执行即可，不必进 `run_data/` 隔离目录**；`run_data/` 只服务跑实验的战役脚本（步骤 4 的 `analyze.py` 仍按原约定在隔离目录下跑）
- 输入：同步骤 4；LC 分母读 `experiments/RQ2/coverage_denominators.json`；分层表另读 `runs/RQ1/labels_<lib>.json`
- 预期输出：`experiments/RQ3/results_qwen/` 下**每模型一套**（文件名 `rq3_<表>_<model>.<ext>`，缺省三模型全出；`--model <模型名>` 只出一个），每套装载行数 `Full=30, NL=30, NP=30, NCF=30`

| 产物 | 内容 | 行数/份数 |
|---|---|---|
| `rq3_main_*.csv` + `.tex` | **正文大表**：每 (mode × 库 + overall-micro/macro) 一行——`LC% + 95% CI`，消融行另给 `ΔLC vs Full + 95% CI + sign-flip p + sig + Cohen's h(+CI) + dz` | 每模型 32（4 臂 × 8 行），3 模型 3 份 |
| `rq3_runs_*.csv` | 逐 run 明细：model/mode/budget/库/`run_index`/`seed`（CRN 凭据）、init/final bits、init/final LC%、Δbits/Δpp、gain%、LLM 调用、token、耗时、来源 checkpoint | 每模型 120 |
| `rq3_strata_*.csv` | 按 RQ1 三维度标签（实现类型/文档质量/注解完整性）分层的 ΔLC 95% CI + h + p，含 `unknown` 层 | 每模型 27 |
| `rq3_resources_*.csv` | 各配置实际用量：LLM 调用 / token / 耗时的逐 run 均值，6 库 + `ALL-6LIBS` | 每模型 28（4 臂 × 7） |
| `rq3_main_<model>.pdf/png` | 主图：(a) 各配置 LC% + 95% CI，(b) 相对 Full 配对差 + 95% CI（含 0 参考线），overall-micro、上下排列共享 y 轴 | 3（每模型 1 组） |

- 预期一致性：`rq3_main_*.csv` 的 Full 行与 RQ2 `rq2_levels_*.csv` 的 RespFuzzer 行**逐值相同**（同一 30 个 run、同一分母、同 RNG 流）——实测 `nltk 30.056 [30.056, 30.057]`、`numpy 9.137` 两表一致；消融行的 Δ 与 `analyze.py` 表 2 的 gain 口径不逐值相等（见步骤 4 口径提示），但配对对象、n、run 索引完全一致
- `main` 是唯一 csv + tex 双出的表（裸 `tabular`，不含浮动体/caption），其余表只出 csv
- 读表注意：
  - **17/18 格 `sig=*`**：600 函数配对下 0.1 pp 也显著，强弱看 `|ΔLC|` 与 `cohens_dz`，别只看星号；`|h|` 全 small（LC 压缩绝对尺度，RQ2 同现象）
  - **逐库有反例**：NL 六库全负（−0.078 ~ −0.905）；NP 仅 pandas 为正（+0.106）、NCF 仅 pandas 为正（+0.336）且 NCF/numpy 是唯一 `n.s.`（+0.006）→ 结论按 (库, 臂) 逐格陈述，别写"全面优于/劣于"
  - 六库每库 100 函数，故 `overall-micro` 与 `overall-macro` **点值相同、CI 不同**（micro 池化 600 函数重采样，macro 为 6 库分布等权平均），两行都保留

## 产物位置说明

| 位置 | 内容 | 性质 |
|---|---|---|
| `experiments/RQ3/rq3_respfuzzer_*_mode-*.log` / `_checkpoint.json` | 三个消融臂 run 三件套（3 模型 × 2 预算批），另有历史 NSF 批 180 件（存档） | **事实来源**（`analyze.py` / `report_tables.py` 读这里，nsf/ltime 装载期跳过） |
| `experiments/RQ2/rq2_respfuzzer_*` | 复用的 Full 臂（count 与 `-ltime 60` 两批） | **事实来源（只读）**，经 `DEFAULT_LOG_DIRS` 一并装载 |
| `experiments/RQ2/<lib>_capped100_seeds.json` | 三臂与 Full 共用的 600 函数种子集 | 输入 |
| `experiments/RQ2/coverage_denominators.json` | LC 分母（整包 `package_statements`） | 输入 |
| `experiments/RQ3/run_data/campaign_progress*.tsv` | 逐 run 进度（count 按模型分文件；ltime 另一套） | 运行日志 |
| `experiments/RQ3/{fuzz_exp-RQ3.py,run_rq3_*_campaign.sh}` | 消融入口 + count/ltime 战役脚本 | 驱动 |
| `experiments/RQ3/{analyze,report_tables}.py` | 逐函数配对分析（Markdown）/ 论文表与图 | 分析入口 |
| `runs/RQ1/labels_<lib>.json` | 三维度分层标签（`strata` 表用） | 上游标签 |
| `experiments/RQ3/results_qwen/` | 12 份 CSV + 3 份 `main.tex` + 3 组图（pdf/png，三模型各一套） | 分析产物，不入 git（被 `experiments/**/results_*/` 忽略），随 artifact 上传 |
| `experiments/RQ3/rq3.md` | 本手册 | 文档 |

**已知缺件与局限性（报告 Notes 会如实列出，不静默）**

- **`initial_coverage` 跨臂不齐，ΔLC 含基线差**：同 seed、同数据集、同 `seed_count=100`，各臂实测 `initial_coverage` 仍不同——nltk 上 Full 多为 30.539%、NP 恒为 29.047%（差约 900 行 ≈ **1.5 pp**），六库整体 init 均值差 0.08–0.19 pp，**与消融效应（0.1–0.5 pp）同量级**；同臂内各 rep 也有抖动（Full 的 nltk：17698/18000/18607，因初始覆盖是并发跑种子实测的）。后果：`report_tables.py` 的 ΔLC 是**终态绝对差**，会被基线差拖动，边界格会与相对增益口径反号（实测 NL−Full 在 nltk：ΔLC=−0.298 pp，`analyze.py` 的 gain 口径 +0.34 pp；按覆盖增量看 NL 确实最弱）。正文以 ΔLC 为准并在此注明口径，增益口径的敏感性看 `analyze.py` 表 2
- **逐函数轨迹含并发邻居贡献**：`fuzz_dataset` 在 `max_workers=10` 下按提交顺序等待并记录**全局累积覆盖**，"函数 i 的覆盖"近似归因；4 配置同种子、同顺序、同并发结构，配对比较成立，但单函数绝对增益应谨慎解释
- **分层标签只部分命中 capped100 函数**：nltk 67 / numpy 80 / pandas 78 / scipy 100 / sklearn 95 / spacy 76，未命中统一进 `unknown` 层，命中率由脚本逐库打印
- **1 件 failed checkpoint**：`rq3_respfuzzer_experiments_20261106T205419_checkpoint.json`（status=failed），不进分析；孤儿 `.log` 0（该 failed 件属历史 NSF 批）
- **分析范围固定 count 批**：`-ltime 60` 批（NP/NCF 每模型 60 run、三模型共 180 run）、历史 NSF 批（每模型 60 run、三模型共 180 run）与 RQ2 目录里的 t60 产物只作存档——装载期直接跳过，不参与统计也不进 Notes；`--budget` 开关已移除，不存在「time 批分析结果」。跨预算口径的 count vs ltime 稳健性对照如需重做，须先恢复该开关（历史产物与当时的分析输出仍留存在 `run_data/analysis_*_ltime_*.txt` 与 ltime track 中）
- **NSF 消融已从设计移除**：判据已统一为「包含有效调用」+ 异常计入模糊测试信号后，语义过滤不构成独立可消融维度——`llm_mutator.random_llm_mutate` 的 `no_check_semantic` 旁路已删除，入口 `--mode NSF` 直接报错退出，装载期静默跳过历史 `mode-nsf` 180 件产物；如需恢复该对照，须回滚上述两处实现（NSF 组的历史结果见各 `rq3_*_track.md` 存档）
- **模型过滤是必需的**：三模型批同目录同名规则，靠 `checkpoint.decode_config.llm.model_name` 区分；`analyze.py` / `report_tables.py` 缺省 `--model each`——**一次输出三模型**（analyze 每模型一段、report_tables 每模型一套文件），`--model <模型名>` 只看一个，`--model none` 关闭过滤只用于自查（run_index 会互相顶替，统计失真）
