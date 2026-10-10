# RQ1 experiment

> Note: we use {RESPFUZZER} to denote the root directory of the RespFuzzer repo.

## RQ1 seed 质量实验引擎（5 模式，MC6-2）

RQ1 的对照实验引擎位于 `experiments/RQ1/engine/`（实验专属代码，不进 `src/respfuzzer`）：

| 文件 | 作用 |
| --- | --- |
| `prompts.py` | 5 模式 prompt（`ours` / `normal` / `prompted` / `extended` / `rag`） |
| `modes.py` | 对照组 pass@k 引擎（任一候选覆盖即短路） |
| `driver.py` | 多 run、随机种子、并发派发、逐候选落盘与合并 |
| `executor.py` | 共享覆盖判定（静态门控 + 调用成功口径【MC7-1】） |
| `extended.py` / `rag_index.py` / `embedding_mock.py` | Extended 源码切片 / **RAG 检索索引【MC6-2】** / 本地 mock embedding 服务 |
| `counting.py` `manifest.py` `seeding.py` | 资源计数、decode/manifest 快照、run 随机种子 |
| `aggregate.py` `stats.py` `table.py` | 配对聚合、bootstrap 与 Cliff's delta、报表 |
| `exec_lock.py` | 代码执行与模块加载的全局串行锁 |

入口（仓库根目录执行，无需 `PYTHONPATH`）：

```bash
python experiments/RQ1/rq1_seed.py --mode normal --library numpy --runs 5 --k 10
python experiments/RQ1/rq1_seed.py --mode ours   --library math --runs 1 --limit 3
python experiments/RQ1/rq1_seed.py --mode rag    --library requests --no-fake-embedding  # 正式 RAG（需 [embedding]）

  # 【MC6-5】先跑 ours 记录 LLM 调用次数，再以相同次数驱动其余对照组
  python experiments/RQ1/rq1_seed.py --library yaml --runs 5 --k 10 --mc6-5
```

说明：

- **等预算【MC6-5】**：`--mc6-5` 先跑 `ours`（RespFuzzer），按 run×函数记录它实际
  消耗的 LLM 调用次数（`budget.json`），再把该次数逐函数当作 pass@k 的 k 去驱动
  normal / prompted / extended / rag；单组补跑用 `--mode <m> --budget-from ours`。
  `--k` 只是 ours 的自省轮次封顶与「预算缺函数」时的兜底值；对照组 pass@k 短路
  可少用，实际用量 ≤ 基准；
- `--mode rag` 每库建一个源码索引（语料 = 采样前整库函数池），检索片段注入 prompt 的
  `<retrieval>`；embedding 服务读 config `[embedding]`，未配置时用确定性 fake embedding，
  正式跑加 `--no-fake-embedding` 做显式校验；`python -m experiments.RQ1.engine.embedding_mock`
  可起本地 mock 服务；默认剔除目标函数自身，`--rag-include-self` 可切回；
- 产物：`runs/RQ1/<mode>/<library>/r<run>_s<seed>/{manifest,decode_config,results}` 三件套，
  外加 `experiments/RQ1/rq1_<mode>_<library>_<ts>.log` + 同 stem 的 `*_checkpoint.json`【MC3-1】；
- 测试在 `tests/rq1/`（由 `pyproject.toml` 的 `pythonpath = [".", "src"]` 提供搜索路径）。

## Purpose

This experiment generates example programs for libraries/APIs using a large
language model (LLM). The generated examples are saved as Python files and
later used for instrumentation and coverage analysis in downstream RQ2/RQ3
experiments.

## Requirements
- Python 3.13（在仓库根执行 `python experiments/RQ1/rq1_seed.py`，依赖已装入 `.venv`）
- `config.toml`：`[llm]` 必填；`--mode rag` 另需 `[embedding]`（未配置时用确定性 fake embedding）
- 目标函数池 `data/<library>_functions.json`（12 库，含 MC6-1 分层标签）
- 覆盖判定会在子进程中 import 目标库并执行 seed（`engine/executor.py`）

## Post-generation: 覆盖判定（FC）与历史脚本清理

FC 判定与产物都在引擎内完成，不再需要外部脚本：

- **判定**：`engine/executor.py` 的共享 Executor——静态门控（`has_valid_call`：编译 +
  目标调用存在性）∩ 子进程执行不抛异常【MC7-1】，5 个模式同一口径；
- **产物**：逐候选 `covered` 落 `runs/RQ1/<mode>/<library>/r*_s*/results.jsonl`，
  函数层 FC 用 `engine/aggregate.function_covered` 聚合（pass@k）；
- **资源与解码**：每次 run 的 `manifest.json` 带 usage（调用次数/token），
  `decode_config.json` 带 temperature/top_p/max_tokens/chat template/修订版本。

**已删除的历史脚本**（MC6-2 清理，需要时用 `git log --diff-filter=D` 找回）：

| 删除的文件 | 原用途 | 为什么删 |
| --- | --- | --- |
| `coverage/instrument.py` | 插桩 + FCR 包装（含 f4a/replay/check 三套 fork 残留） | FC 判定已由 `engine/executor.py` 承担；265/370 行本就无人引用 |
| `coverage/check_instrument_calls_fixed.py`、`coverage/seed_instrument_runner.py` | 插桩后统计「哪些 API 被调用」的独立报告 | 与新 FC 口径重复，产物未接入 MC3-1 契约，默认路径还写死他人机器 |
| `coverage/cov.py`、`coverage/driver.py`、`coverage/Logger.py` | dcov 行覆盖率辅助（LC） | RQ1 报 FC 不报 LC；仓库内从无产物、无调用 |
| `LLM_testcases/generate_tests_from_apis.py`、`..._by_api.py` | 只给 library/API 名的一次性生成 | 与 5 模式引擎重叠，且没有函数信息（无法算 FC） |

`LLM_testcases/api_list.json`（12 库、5,596 个 API 的清单）作为**数据**保留：
当前无代码消费方，供后续按 API 子集重跑或与已发布种子数据集对账。
