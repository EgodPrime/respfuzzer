#!/usr/bin/env bash
# 12 库 × 5 模式 × 5 重复 seeding 战役（RAG 组垫底）。
#
# 维度顺序: mode → rep → library
#   mode 外层优先，且 RAG 模式放最后 —— 把对 LLM 服务的独占期尽量留给
#   并行的向量库战役（RAG 每 run 都要查询向量库，晚跑可以蹭到更多已建好的库）。
#
# 用法: bash scripts/run_seeding_campaign.sh
#
# 设计要点：
#  * 完全串行：一次只跑一个 `uv run solve`；
#  * 断点续跑：run 结束落一行 campaign_progress.tsv；重跑时先查产物
#    data/<mode>/<lib>_seeds[_rsN].json（或 meta）存在即跳过；
#  * 重复编号 ↔ 随机种子：rep N ↔ --rs N；
#  * 单个 run 失败（rc!=0）只记录不中断战役；
#  * 输出全量进 campaign_master.log。
set -u

ROOT="/home/lisy/Projects/respfuzzer"
LOG_DIR="$ROOT/experiments/function_solutions"
# MASTER_LOG / PROGRESS 带模型段（MODEL_TAG 推导后赋值）
cd "$ROOT/run_data" || exit 1
mkdir -p "$LOG_DIR"

# 非 RAG 模式先跑（快、无向量库依赖），RAG 最后
MODES=(respfuzzer respfuzzer-wodoc respfuzzer-wofb respfuzzer-woboth respfuzzer-rag)
LIBS=(nltk numpy pandas scipy sklearn spacy)
RS=(1 2 3 4 5)
# 产物文件名含模型名段（与 solve 写出的 <lib>_seeds_<model>_rs<N>.json 对齐；
# 从 config 单一取源，与 solve 内的 model_tag 规则一致）
MODEL_TAG=$(uv run python -c "from respfuzzer.utils.config import get_config; print(str(get_config('llm')['model_name']).replace('/','-').replace(' ','-'))")
MASTER_LOG="$LOG_DIR/campaign_master_${MODEL_TAG}.log"
PROGRESS="$LOG_DIR/campaign_progress_${MODEL_TAG}.tsv"
[ -f "$PROGRESS" ] || printf 'timestamp\tlibrary\trandom_seed\tmode\tseeds\tllm_calls\tsecs\trc\n' > "$PROGRESS"

exists_artifact() { # $1=lib $2=mode $3=rs
  local lib="$1" mode="$2" rs="$3"
  [ -f "../data/$mode/${lib}_seeds_${MODEL_TAG}_rs${rs}.json" ] || [ -f "../data/$mode/${lib}_seeds_meta_${MODEL_TAG}_rs${rs}.json" ]
}

run_one() { # $1=lib $2=mode $3=rs
  local lib="$1" mode="$2" rs="$3"
  if exists_artifact "$lib" "$mode" "$rs"; then
    printf '[skip] %s %s rs=%s (artifacts exist)\n' "$lib" "$mode" "$rs" | tee -a "$MASTER_LOG"
    return 0
  fi
  local t0 t1 n calls
  local seeds_json="../data/$mode/${lib}_seeds_${MODEL_TAG}_rs${rs}.json"
  local meta_json="../data/$mode/${lib}_seeds_meta_${MODEL_TAG}_rs${rs}.json"
  t0=$(date +%s)
  printf '[start] %s lib=%s mode=%s rs=%s\n' "$(date '+%F %T')" "$lib" "$mode" "$rs" | tee -a "$MASTER_LOG"
  printf '===== START %s %s rs=%s %s =====\n' "$lib" "$mode" "$rs" "$(date '+%T')" >> "$MASTER_LOG"
  uv run solve "$lib" --mode "$mode" --rs "$rs" >> "$MASTER_LOG" 2>&1
  local rc=$?
  t1=$(date +%s)
  n=0; calls=0
  [ -f "$seeds_json" ] && n=$(python3 -c "import json;print(len(json.load(open('$seeds_json'))))" 2>/dev/null || echo 0)
  [ -f "$meta_json" ] && calls=$(python3 -c "import json;print(sum(v.get('llm_calls',0) for v in json.load(open('$meta_json'))['per_function'].values()))" 2>/dev/null || echo 0)
  printf '===== DONE %s %s rc=%s seeds=%s %s =====\n' "$lib" "$mode" "$rc" "$n" "$(date '+%T')" >> "$MASTER_LOG"
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$(date '+%F %T')" "$lib" "$rs" "$mode" "$n" "$calls" "$((t1-t0))" "$rc" >> "$PROGRESS"
  printf '[done ] %s lib=%s mode=%s rs=%s rc=%s seeds=%s calls=%s %ss\n' \
    "$(date '+%F %T')" "$lib" "$mode" "$rs" "$rc" "$n" "$calls" "$((t1-t0))" | tee -a "$MASTER_LOG"
}

# mode → rep → library（RAG 组在最后）
for mode in "${MODES[@]}"; do
  for rs in "${RS[@]}"; do
    for lib in "${LIBS[@]}"; do
      run_one "$lib" "$mode" "$rs"
    done
  done
done

printf '[campaign] ALL DONE %s\n' "$(date '+%F %T')" | tee -a "$MASTER_LOG"
