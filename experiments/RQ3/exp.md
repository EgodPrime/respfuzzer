# RQ3 experiment

> Note: we use `{RESPFUZZER}` to denote the root directory of the RespFuzzer repo.
>
> 本实验原编号为 **RQ4**（语义驱动变异消融），随全文编号调整改名为 **RQ3**：目录
> `experiments/RQ3/`、入口 `fuzz_exp-RQ3.py`、产物前缀 `rq3_`。
>
> RQ2 measures fuzzing efficiency（RespFuzzer vs miniFuzz4All vs DyFuzz），见
> [`experiments/RQ2/exp.md`](../RQ2/exp.md)。

## Dataset Setup

与 RQ2 使用**同一份种子**（否则按 `run_index` 配对无意义）：`run.sh` 在未显式设置
`RESPFUZZER_DATA_DIR` 时优先取 `experiments/RQ2/<library>_capped100_seeds.json`——
RQ2 的三个 fuzzer 与本实验的 Full 臂用的都是它（100 seeds/库，`Random(4399)` 采样，
2026-11-01 生成后未改动）。要换种子目录时显式指定（文件名须为 `<library>_seeds_sampled.json`）：

```bash
export RESPFUZZER_DATA_DIR=/path/to/seeds   # 缺省 {RESPFUZZER}/RQ2_data_new_111（已不在仓库）
```

被测库由 `scripts/libraries.conf` 决定（nltk / numpy / pandas / sklearn / scipy / spacy）。

## Safe tips

Before running the experiments, please make sure you have created a separate directory to store all the fuzzing garbage files to avoid messing up your project structure. For example:

```bash
cd {RESPFUZZER}/experiments/RQ3/
mkdir -p run_data
```

## Run RespFuzzer with different mutation strategies

All experiments use the unified `fuzz_exp-RQ3.py` entry point. Each ablation is controlled by a `--mode` flag:

| Mode  | Effect                            |
|-------|-----------------------------------|
| `NL`  | No LLM-based mutation             |
| `NP`  | No traditional parameter mutation |
| `NSF` | No semantic filtering             |
| `NCF` | No coverage feedback              |

### Full Configuration (RespFuzzer default mutation)

```bash
cd {RESPFUZZER}/experiments/RQ3/run_data
bash ../run.sh
```

> **Full 臂默认不重跑**，直接复用 RQ2 已跑完的 RespFuzzer 数据（同配置、同预算、同种子集），
> 见下一节「Full 臂复用 RQ2 的 RespFuzzer」；本命令只用于等价性抽查或 RQ2 数据不可用时重跑。
### Without LLM-based Mutation

```bash
cd {RESPFUZZER}/experiments/RQ3/run_data
bash ../run.sh NL
```

### Without Traditional Parameter Mutation

```bash
cd {RESPFUZZER}/experiments/RQ3/run_data
bash ../run.sh NP
```

### Without Semantic Filtering

```bash
cd {RESPFUZZER}/experiments/RQ3/run_data
bash ../run.sh NSF
```

### Without Coverage Feedback

```bash
cd {RESPFUZZER}/experiments/RQ3/run_data
bash ../run.sh NCF
```

## Full 臂复用 RQ2 的 RespFuzzer（不重跑）

RQ3 的 Full 配置（LLM 变异 + 参数变异 + 语义过滤 + 覆盖反馈全开）与 RQ2 的 RespFuzzer 组
**是同一个配置**，RQ2 已跑完 6 库 × 5 rep、`request_budget=10` 的数据
（`experiments/RQ2/rq2_respfuzzer_<lib>_capped100_*.log` + 同 stem `_checkpoint.json`，
全部 `status=completed`、`metadata.run_index=1..5`）。因此 **Full 臂复用、不重跑**：

```bash
cd {RESPFUZZER}/experiments/RQ3/run_data
# 只验证复用的 Full 臂（RQ2 目录只读）
uv run python ../analyze.py --log-dir ../../RQ2
uv run python ../report.py  --log-dir ../../RQ2
# 消融臂（NL/NP/NSF/NCF）跑完后：两个目录一起传，按 run_index 跨目录配对
uv run python ../analyze.py --log-dir . ../../RQ2
uv run python ../report.py  --log-dir . ../../RQ2
```

**等价性论证（复用 vs 在 RQ3 里重跑 Full）**，逐条对应：

| 维度 | RQ2 RespFuzzer | RQ3 Full | 依据 |
|---|---|---|---|
| 种子集 | `experiments/RQ2/<lib>_capped100_seeds.json`（100/库） | 同左（`run.sh` 优先取它） | `metadata.dataset_path` |
| 消融开关 | LLM / 参数变异 / 语义过滤 / 覆盖反馈全开 | `--mode Full` = 四开关全开 | `fuzz_exp-RQ3.py::_parse_mode` |
| 预算 | `-lrequest 10`（每种子 10 次 LLM 请求） | `[fuzz] llm_fuzz_per_seed = 10` | `metadata.request_budget` / `config.toml` |
| 种子派生 | `run_index=1 → seed 104045904` | `run_index=1 → seed 104045904` | 两边 checkpoint `metadata.seed` 实测同值 |
| 并发 / LLM | `max_workers=10`；Qwen3.6-35B、t=0.7、top_p=1.0、max_tokens=32768 | 同（同一份 `config.toml`） | `results.concurrency`、`decode_config` |
| 逐函数轨迹 | `seed_fuzzed` 100/100 = `seed_count` | `analyze.py` 只取 `event == "seed_fuzzed"` | 两边 `results.events[]` |
| 入口 | `respfuzzer.cmd.fuzz:fuzz_dataset`（src） | `experiments/RQ3/fuzz_exp-RQ3.py`（加消融开关的分叉） | `metadata.entry` |

> 两个入口同源：RQ3 脚本是在 src 实验脚本上加四个消融开关的分叉，四开关全开时走的
> 就是默认路径；预算差异只在于 RQ3 入口不接 `-lrequest/-ltime`，而是固定读 config 的
> `llm_fuzz_per_seed`（=10，与 RQ2 实际跑的 `-lrequest 10` 同值）。论文口径可写
> "Full 配置复用 RQ2 的 RespFuzzer 运行（同一配置、同一预算、同一种子集、同一随机种子派生）"。

**预算口径规则（必须遵守）**：RQ2 目录里还并存着 RQ2 自己的 `-ltime 60` campaign 产物
（同库同 `run_index`、时间戳更晚，单种子约 7.5k 次 LLM 调用 ≈ `request=10` 的 7 倍）。
RQ3 只认**固定次数口径**，两侧都已内建隔离：

- `analyze.py`：同 `(mode, lib, run_index)` 有多份 checkpoint 时按
  `request 计数(0) → 无预算字段(1) → 仅时间预算(2)` 择一，弃用的逐条写进 Notes
  （`duplicate ('full', 'nltk') run 1: 保留 ...（request=10）；弃用 ...（time 预算）`）；
- `report.py`：同 `(library, mode)` 已有 count/无预算日志时丢弃纯 `-ltime` 的日志，
  打印 `[budget] <lib>/<mode>: dropped N time-budget log(s)`。

## Paired runs

`batch_run.sh <n>` 跑 n 轮，每轮内依次跑 Full / NL / NP / NCF / NSF，并导出同一个
`RUN_INDEX`：

```bash
cd {RESPFUZZER}/experiments/RQ3
mkdir -p run_data && cd run_data        # AGENTS.md: 始终在 run_data 下跑
bash ../batch_run.sh 5
```

同一 `run_index` 在 5 个配置上经 `prepare_run_seed(seed, run_index, section="fuzz")`
派生出**同一个随机种子**，写入 checkpoint 的 `metadata.seed` / `metadata.run_index`，
报表与 `analyze.py` 据此按 `(mode, library, run_index)` 配对。

## Report

```bash
cd {RESPFUZZER}/experiments/RQ3/run_data
# 运行级：覆盖率表（表 VI）+ n/mean±sd/95% CI/Cliff's δ + 按 run_index 配对 Δ + 库层分层 bootstrap
uv run python ../report.py                                          # 本目录（RQ3 自己的产物）
uv run python ../report.py --log-dir ../../RQ2                      # 只看 RQ2 的 Full 臂
uv run python ../report.py --log-dir . ../../RQ2                    # 两目录一起（消融臂+复用的 Full）

# 函数级：逐函数配对 bootstrap CI + 符号翻转检验 + Cohen's h/d（4 个消融 vs Full）
uv run python ../analyze.py
uv run python ../analyze.py --log-dir ../../RQ2                      # 只看 RQ2 的 Full 臂
uv run python ../analyze.py --log-dir . ../../RQ2                    # 两目录一起（消融臂+复用的 Full）
uv run python ../analyze.py --bootstrap hierarchical   # 函数+重复两阶段重采样（更保守）
```

默认扫描**本目录**（`experiments/RQ3/`）；`--log-dir` 可指向别处（如 `../../RQ2` 复用 Full 臂）。
`report.py` 认 `rq[23]_respfuzzer_*.log`（RQ2 的 `rq2_` 前缀 + `_capped100` 数据集段会被归一到
规范库名），`analyze.py` 认 `rq*_respfuzzer_*_checkpoint.json`；两边都只取同 stem 的
`*_checkpoint.json`，没有 checkpoint 的日志不参与配对（`report.py` 会列在表下）。

## Notes

- **产物命名**：`rq3_respfuzzer_{library}_{YYYYmmddTHHMMSS}[_mode-{mode}].log` / `_checkpoint.json`，
  Full 组不带 `_mode-` 段（`experiment_recorder.run_stem` 规则）。
  `experiments/RQ2/` 里遗留的 `rq3_dyfuzz_*` 是 RQ3→RQ2 编号迁移的历史产物，与本目录前缀相同但
  目录不同，各报表/分析脚本都以自身目录为 `LOG_DIR`，互不串。
- **checkpoint 字段**（`analyze.py` 的数据源）：`results.initial_coverage` / `results.final_coverage` /
  `results.seed_count` / `results.events[]`（取 `event == "seed_fuzzed"` 的 `func_name`+`coverage`
 作为逐函数轨迹）/ `results.llm_calls`+`total_tokens`（入口已接 `reset_llm_stats()` 与
  `record_llm_stats(concurrency=...)`）/ `metadata.run_index`+`seed`（配对键）。
- **逐函数轨迹的口径**：`_fuzz_dataset` 在 `max_workers`（config.toml，默认 10）并发下按提交顺序
  等待并记录全局累积覆盖，因此"函数 i 的覆盖"含并发邻居的贡献，归因是近似的；5 个配置同种子、
  同顺序、同并发结构，配对比较仍然成立，但单函数绝对增益应谨慎解释。
- **`initial_coverage` 有抖动**：同一份 seeds（mtime 未变）不同次运行的 `results.initial_coverage`
  仍有出入（实测 nltk 17698/18000/18607、spacy 8242/8318/8319、pandas 32172/32180），因为初始覆盖
  是并发执行种子实测的。增益分母取各次运行自己的 `initial_coverage`，因此小增益的库（如 nltk
  +0.05%）解读要保守；配对设计（同种子、同分母口径）正好抵消其中的系统性部分。
- **预算择一**：RQ2 目录里并存 RQ2 自己的 `-ltime 60` campaign 产物与本实验的固定次数口径，
  `analyze.py` 按 request→无字段→time 的优先级择一、`report.py` 丢弃纯 time 件，明细见上一节。
