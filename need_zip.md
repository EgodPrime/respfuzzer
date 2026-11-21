# 实验结果打包清单（need_zip）

RespFuzzer 实验 artifact 打包清单。**一律以本仓库为准**：RQ 编号、口径、文件名全部跟随仓库
（RQ1 种子生成 / RQ2 效率对比 / RQ3 变异消融 / RQ4 漏洞复现），不与稿件编号对齐。

**唯一种子口径：`capped100` = 6 库 × 100 = 600 函数**
（`experiments/RQ2/<lib>_capped100_seeds.json`，由 `generate_rq2_seeds.py` 以固定 RNG
`random.Random(4399)` 抽样，可再生）。RQ1/RQ2/RQ3 全部吃这一份。

**zip 与仓库零重叠**：仓库分发代码与文档（git 跟踪），zip 只装 **git 忽略的数据与结果** + zip 自产 `README.md`；
判据：路径经 `git check-ignore` 命中才入 zip。track 跟踪文档、环境锁亦不入 zip；复算回仓库执行（复现手册 rq1/rq2/rq3.md 随仓库）。
---

## 一、打包清单（P0）

### rq1/ — 种子生成（`experiments/RQ1` + `data/`）

| 内容 | 路径 | 数量 / 大小 | 说明 |
|---|---|---|---|
| 种子 + 开销账本 | `data/respfuzzer*/<lib>_seeds[_meta]_<model>_rs{1..5}.json`，6 库 × 5 模式 × 3 模型 × 5 rs × 2 文件 | 900 文件 / **220 MB** | 5 模式 = respfuzzer / -rag / -wodoc / -wofb / -woboth；**事实来源** |
| 函数清单 | `data/<lib>_functions.json`（nltk/numpy/pandas/scipy/sklearn/spacy） | 6 份 / 12 MB | 仓库 rq1.md 步骤 2 的战役输入 |
| 分析产物 | `experiments/RQ1/results_3model/` | 34 份 / 1.0 MB | 6 份 bootstrap txt（cluster + hierarchical × 3 模型）+ csv/tex/pdf/png |
| 分层标签 | `runs/RQ1/labels_{nltk,numpy,pandas,scipy,sklearn,spacy}.json` | 6 份 / 0.7 MB | report_tables 三维度标签（`labels_all` 与其余 6 库标签**不进包**，无代码读取） |
| 进度台账 | `experiments/function_solutions/campaign_progress{,_Qwen3.6-35B-A3B,_gpt-oss-120b}.tsv` | 3 份 / ~40 KB | **仓库侧 report_tables.py 的输入**（三模型各读其一；重试行取晚者；脚本不入包） |

### rq2/ — 效率对比（`experiments/RQ2`）

| 内容 | 路径 | 数量 / 大小 | 说明 |
|---|---|---|---|
| run 三件套 | `rq2_*_{checkpoint.json,log}`（resp req10 90 + resp time60 30 + fuzz4all 60）+ legacy `rq3_dyfuzz_*` 60 | **240 对 / 524 MB（含 6 孤儿 log）** | 分析全覆盖（time60 是 RQ2 的第二批，**在分析内**） |
| 孤儿 log | 6 个无 checkpoint 的 `.log`（`rq2_fuzz4all_spacy` ×2、`rq2_respfuzzer_{pandas,scipy,sklearn}`、`rq3_dyfuzz_numpy`） | 6 份 / 6.8 MB | 中途被杀、不进分析——**照实打包并在 README 注明**，勿只放 ckpt |
| 种子集 | `experiments/RQ2/<lib>_capped100_seeds.json` | 6 份 / 0.5 MB | 三 fuzzer 共用的 600 函数 |
| 覆盖率分母 | `coverage_denominators.json` | 1 份 | 整包 `package_statements` 口径（生成脚本属代码，不入包） |
| 分析产物 | `experiments/RQ2/results_qwen/` | 23 份 / 1.7 MB | 6 csv + 6 tex + 主图/bylib/cvertime（pdf+png）+ `rq2_analyze_*.md`（§三-5 已补） |
| 进度台账 | `experiments/RQ2/run_data/campaign_progress*.tsv` | 4 份 / ~40 KB | 断点续跑凭据（分析不读，台账） |

### rq3/ — 变异消融（`experiments/RQ3`）

| 内容 | 路径 | 数量 / 大小 | 说明 |
|---|---|---|---|
| run 三件套（**count 批**） | `rq3_respfuzzer_*_mode-{nl,np,ncf}_{checkpoint.json,log}`（NL 90 + NP 90 + NCF 90，3 模型 × 30） | **270 对 / 186 MB** | 分析唯一输入；Full 臂复用 rq2 的 RespFuzzer |
| 分析输出存档 | `experiments/RQ3/run_data/analysis_*.txt` | 9 份 / 0.19 MB | count analyze ×3（三模型，现行加载器重生成，§三-6 已补）+ count report ×2 与 ltime ×4（已删 report.py 的历史存档，rq3.md 引用） |
| 分析产物 | `experiments/RQ3/results_qwen/` | 21 份 / 0.6 MB | 三模型各 main（csv+tex+图）+ runs/strata/resources |
| 进度台账 | `experiments/RQ3/run_data/campaign_progress{,_Llama-3.1-8B-Instruct,_gpt-oss-120b}.tsv` | 3 份 / ~40 KB | count 批台账（**无后缀者 = Qwen 批**，早期未加模型后缀，见仓库 rq3.md；分析不读）；ltime 3 份在下方排除 |

### README.md — zip 自产

| 内容 | 路径 | 说明 |
|---|---|---|
| 说明文件 | `README.md` | **zip 自产、不属仓库**：RQ 编号与仓库材料指路（rq1/rq2/rq3.md、vul_*.py、prompts.md、vul_links.md 均在仓库）、capped100=600 链路、孤儿 log 清单、运行步骤 |


---

## 二、不打包（多余 / 废弃 / 可再生）

| 排除项 | 大小 | 理由 |
|---|---|---|
| `experiments/RQ3` **ltime 批 180 对** | ~540 MB | 时间预算批，分析静默跳过（`_budget_rank==2`），rq3.md 声明存档不分析 |
| `experiments/RQ3` **NSF 批 180 对 + 1 failed ckpt** | ~570 MB | 已从设计移除（entry 拒绝 `mode-nsf`），历史批仅供存档 |
| 仓库根 `rq*_track.md` ×13（rq1 ×3 + rq2 ×4 + rq3 count ×3 + rq3 ltime ×3） | ~0.2 MB | 人工调试/失败跟踪笔记，非结果性材料，不入包 |
| `experiments/RQ3/run_data/campaign_progress_ltime*.tsv` ×3 + `run_rq3_ltime_campaign.sh` | ~50 KB | 随 ltime 批一起废弃 |
| `experiments/RQ2/old_data/` | 543 MB | 被替换的旧 RespFuzzer-ltime 批，rq2.md 声明存档不进分析 |
| `data/*_vector_storage/` | 5.7 GB | RAG 向量库，`build_vector_index.py` 可再生 |
| `data/mutants.data` | 1.1 GB | 变异缓存（ndjson），可再生 |
| `runs/RQ1/`（6 库 labels 除外） | 1.2 GB | 早期 12 库旧批（ours/normal/prompted/extended/rag + budget.json），非现行数据源 |
| `experiments/function_solutions/*.log`（478 单 run 日志 + 3 campaign_master） | 1.15 GB | 战役全量日志；seeds_meta + progress tsv 已承载每 run 开销与状态，日志可复跑再生 |
| `experiments/RQ1/{respfuzzer,respfuzzer-*}/` | 226 MB | 同批 seeds 的**冗余副本**（rq1.md 声明"不作数据源"，读 `data/`） |
| `experiments/RQ2/run_data/`、`experiments/RQ3/run_data/` 沙箱残留（nltk_data、path_to_data 等） | ~310 MB | 运行期残留；只挑出 RQ2/RQ3 的 progress tsv 与 RQ3 的 `analysis_*.txt`（**`analyze_time.out` 不入包**：无约定、无 rq3.md 引用，疑 ltime txt 的早期重复） |
| `experiments/RQ2/*_all_functions_seeds.json` | 3.1 MB | 全量参考集，`generate_rq2_seeds.py` 从 `data/respfuzzer/` 确定性再生 |
| `experiments/RQ2/respfuzzer_seeds.json` | 16 KB | 旧 `seeds_sampled` 路径遗产（200 函数），现行口径为 capped100 |
| `experiments/RQ2/rq3_fuzz4all_nltk_20261030T120256`（1 对） | ~0 | 早期预跑：无配对键、被 `--model` 滤除（rq2.md 已注明） |
| `runs/RQ1/labels_all.json` + 非 6 库 labels、`experiments/function_solutions/vector_campaign_progress.tsv` | 4.2 MB | 前者无代码读取；后者属 RAG 向量战役（向量库已排除、`run_vector_campaign.sh` 可再生），report_tables 不读 |
| `data/<lib>_functions.json` 非 6 库（dask/paddle/prophet/requests/torch/yaml） | 15 MB | 12 库旧批清单，非正文范围 |
| `data/respfuzzer*/` 非 6 库 seeds | 4.3 MB | 同上（paddle/prophet/requests/torch/yaml） |
| SecurityEval 全套（`data/SecurityEval/`、`src/SecurityEval/`、`rq5_*` 残留） | — | MC1 要求删除真实漏洞案例实验，零内容呈现 |
| **全部代码**：`src/`、`tests/`、根 `fuzz_exp-RQ3.py`、`experiments/**/*.py`（analyze/report_tables/战役脚本/分母生成器）、`run_rq*_campaign.sh`、`scripts/`、`miniFuzz4All/`、`DyFuzz/`、`DyFuzz-patch/` | ~10 MB | 用户裁定 artifact 不含代码；复算回仓库（rq1/rq2/rq3.md 有步骤；环境锁亦不入包） |
| 环境锁 `pyproject.toml`、`uv.lock`、`Dockerfile`、`config.toml.default`、`experiments/requirements.txt` | ~0.1 MB | 用户裁定不入包；解码参数由各 checkpoint `decode_config`/`config` 自证，复现环境回仓库（`uv` 一键恢复） |
| 仓库 git 跟踪的文档与 PoC：`rq1.md`/`rq2.md`/`rq3.md`、`experiments/llm_deploy_reference.md`、`experiments/RQ4/vul_*.py` ×4、`prompts.md`、`vul_links.md` | ~0.1 MB | **随仓库分发、与 zip 零重叠**；复现手册/部署参考/复现脚本/prompt 留档/issue 快照均查仓库 |
| `data/*_source/` | 271 MB | `find_bug.py` 真实漏洞挖掘产物源码，真实漏洞材料见仓库 `experiments/RQ4/`（vul_*.py + vul_links.md，随仓库） |
| `docs/` 整个目录（`latex_v7/`、`plan/`、`实验期间GPU服务器监控.md`、评审文档等） | — | 稿件、内部计划与运维监控笔记，不属 artifact |

---

## 三、打包前必须补齐

| # | 事项 | 现状 | 动作 |
|---|---|---|---|
| 1 | 种子口径自洽 | `capped100`=600 由 `generate_rq2_seeds.py` 确定性生成，但其原料是 RQ1 全量种子（每库 unique 数 >100） | zip 内 README 写清链路：**RQ1 全量生成 → RQ2 抽样 600（RNG 4399）→ RQ3 沿用**；三处口径一致即自洽 |
| 2 | prompt 与 issue 链接留档 | 仓库根 `prompts.md`（AST 提取）与 `experiments/RQ4/vul_links.md`（2026-11-16 状态快照）已生成，均 **untracked 待提交** | 提交 git（随仓库、不入 zip）；核对 prompts §4 RQ4 pipeline 段落（脚本已移除仅留档）；复核 issue 链接可达 |
| 3 | `.gitignore` 白名单 | 第 49–52 行的 `!` 白名单目前全被注释 | 若走 git 分发，恢复注释行；否则打包时直接 `-i` 忽略规则 |
| 4 | 孤儿 log 6 件 | 中途被杀、无 checkpoint，不进分析 | 照实打包并在 zip README 注明（清单见 rq2 节） |
| 5 | ~~RQ2 analyze 存档缺失~~ **已补齐** | `experiments/RQ2/results_qwen/rq2_analyze_Qwen3.6-35B-A3B.md`（13 KB，首行注明命令与 B/seed）已生成并入包，与 `rq2_paired_*.csv` 同源 | ✓ 完成 |
| 6 | ~~RQ3 Qwen analysis txt 缺失~~ **已补齐** | `run_data/analysis_qwen_count_analyze.txt` 已生成入包；另将 llama/gptoss 的 count analyze txt 用现行加载器统一重生成（NSF 行消失、Notes 收敛 1 条），三模型各 1 份共 3 份 `*_count_analyze.txt` | ✓ 完成 |

---

## 四、checkpoint 自证（`*_checkpoint.json` 字段）

| 字段 | 内容 |
|---|---|
| `metadata` | rs、mode、target_lib、budget_kind/秒数/条数、model、base_url、**failure_threshold**、no_field、origin |
| `decode_config` | temperature / top_p / max_tokens（与稿件解码参数对拍） |
| `results` | 每 attempt 的 prompt/response/repairs/feedback（RQ3 还有 `mutations`） |
| `environment` | UTC 时间、hostname、python 版本、token 用量 |
| `config` | 完整运行配置快照 |

**privacy 检查**：`api_key` 已在 recorder 内 mask（`***`），无 `sk-` 明文；但 `base_url` 含内网 IP
`http://192.168.1.17:8021`——内部分发可留，对外需脱敏。

---

## 五、zip 结构与体积

按仓库相对路径组织（不重排），建议 `rsync` 按本清单投料到 **仓库内** `run_data/respfuzzer-artifact/`（check-ignore 判据依赖仓库内路径），再整体压缩：

```
respfuzzer-artifact/
├── README.md                    # RQ 编号说明、capped100 链路、孤儿 log、运行步骤
├── data/                        # 6 库 seeds+meta（220M）+ functions.json（12M）
├── runs/RQ1/labels_*.json       # 6 库
├── experiments/
│   ├── RQ1/   # results_3model/（仅数据/结果，手册在仓库）
│   ├── RQ2/   # run 240 对（524M 含孤儿 6）+ seeds + denominators + results_qwen + tsv（无 .py/.sh/基线/md）
│   ├── RQ3/   # count 批 270 对（186M）+ analysis txt + results_qwen + tsv（无 .py/.sh/md）
│   └── function_solutions/campaign_progress*.tsv
```

**体积（2026-11-21 实测）**：data ≈ 226 MB + RQ1 ≈ 1 MB + RQ2 ≈ 526 MB + RQ3 ≈ 189 MB + runs/labels ≈ 1 MB + tsv ≈ 0.04 MB + README < 1 MB
→ **未压缩 = 942 MB**（staging 实测；zip 条目 2043 与 `find` 一致），< 1.0 GB。
**`artifact.zip` 实测 88 MB**（Python zipfile DEFLATE-6，2043 条目，19s，`testzip()` 完整性 OK）——log/json 文本压缩比远高于原估 250–350 MB。

---

## 六、打包后自检

```bash
# 1) 成对完整性
RQ2 对：ls experiments/RQ2/*_checkpoint.json | grep -v 20261030T120256 | wc -l   # 期望 240（rq2_* 180 + rq3_dyfuzz_* 60）
RQ3 count 批 270 对：必须按 checkpoint 内 budget 字段判（ltime 文件名无标记），见下方 python
RQ1 齐全：6 库 × 5 模式 × 3 模型 × 5 rs × 2 文件（seeds+meta）= 900 文件
分析产出齐全 ✓：results_3model 34 + results_qwen(RQ2) 23（22+analyze md）+ results_qwen(RQ3) 21 + analysis txt 9（count×3 重生成 + report/ltime 历史 6）；report txt ×4 为已删 report.py 历史产物（README 注明，不再可再生）

# 2) 批次口径（RQ3 ltime 文件名无标记，按 checkpoint budget 字段判）
python3 - <<'EOF'
import json, glob, collections
c = collections.Counter()
for f in glob.glob('experiments/RQ3/*_checkpoint.json'):
    md = json.load(open(f)).get('metadata', {})
    if md.get('request_budget') is not None: r = 0        # count 批
    elif 'request_budget' in md or 'time_budget' in md: r = 2  # ltime 批
    else: r = 1                                            # legacy 无预算 → 视作 count
    c[(md.get('mode'), r)] += 1
print(dict(c))   # 入包 = mode∈{NL,NP,NCF} 且 rank∈{0,1} = 90×3 = 270；排除 = NSF 全批 180（rank0/1）
                  #       + NP/NCF ltime 180（rank2）+ failed Full 1（rank0）= 361（总数 631）
EOF
EOF
# 3) 废弃项未混入与体积
du -sh .   # 入包后应 = 942 MB（< 1.0 GB）✓ 2026-11-21 实测；出现 old_data / vector_storage / mutants.data 即错
find experiments/RQ3 -type f \( -name "*ltime*" -o -name "*nsf*" \) ! -name "analysis_*" ! -name "run_rq3_ltime_campaign.sh" ! -name "rq3.md"   # 打包时应为空

# 4) 机密扫描
grep -r "sk-[A-Za-z0-9]\{20,\}" . && echo FAIL || echo OK

# 5) 零重叠判据（在【仓库根】对 staging 执行——zip 内无 .git，且不能对 staging 路径直接
#    check-ignore：staging 在 run_data/ 下恒被忽略，判据必假阳性；须按 rel 路径映射回【源路径】判断）
python3 - ./run_data/respfuzzer-artifact <<'EOF'
import subprocess, sys, pathlib
root = pathlib.Path(sys.argv[1]); repo = pathlib.Path('.')
overlap, missing = [], []
for p in sorted(root.rglob('*')):
    if not p.is_file() or p.name == 'README.md':
        continue                      # README.md = zip 自产，仓库没有
    src = repo / p.relative_to(root)  # staging 相对路径 = 仓库相对路径
    if subprocess.run(['git', 'ls-files', '--error-unmatch', str(src)],
                      capture_output=True).returncode == 0:
        overlap.append(str(src))      # 被 git 跟踪 → 与仓库重叠，失败
    elif subprocess.run(['git', 'check-ignore', '-q', str(src)]).returncode != 0 \
            and src.exists():
        overlap.append(str(src))      # 未跟踪且未被忽略 → 失败
    elif not src.exists():
        missing.append(str(src))      # 源不在仓库（README 之外不该有）
print('overlap:', overlap[:10] or 'NONE ✓', '| missing:', missing[:10] or 'NONE ✓')
EOF

# 6) 可复算（对拍，回仓库执行——包内无代码）
cd respfuzzer 仓库 && uv run experiments/RQ2/analyze.py ... && diff 对拍 results_qwen/
uv run experiments/RQ3/analyze.py ... 同理（count 批）；步骤见 rq1/rq2/rq3.md
```

- [x] zip 内含 `README.md`（RQ 编号、capped100=600 链路、孤儿 log 清单、分析产物来源、运行步骤）✓ 2026-11-21 实测
- [x] 仓库侧齐备：`rq1/rq2/rq3.md`、need_zip.md、`prompts.md`、`vul_links.md` 均已 git 跟踪 ✓（prompts.md @ d7c7c60，vul_links.md @ 7b3f98e）
- [x] 对外分发链接已回填：Google Drive `https://drive.google.com/file/d/1b4Z17Wm732QQ2M8kQ2JBt3JDCT1GgZPX/view?usp=drive_link`，写入仓库 `README.md`（替换原 `_TODO (fill in after upload)_` 占位）
- [x] 包内不含任何代码：`.py/.sh` 计数 = 0 ✓（vul_*.py 随仓库）
- [x] 包内无 track 文档：`*track*.md` 计数 = 0 ✓（13 份留在仓库）
- [x] 包内无环境锁：`pyproject.toml`/`uv.lock`/`Dockerfile`/`config.toml.default` 计数 = 0 ✓
- [ ] `.gitignore` 白名单恢复（仅当走 git 分发时；白名单针对的是 log/results/tsv 等数据文件，rsync 打包则无需）
