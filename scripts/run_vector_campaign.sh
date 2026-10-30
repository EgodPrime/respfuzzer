#!/usr/bin/env bash
# RAG 预建向量库战役：12 库 × data/<lib>_vector_storage，串行构建。
# 与 seeding 战役并行运行（seeding 走 LLM 服务，本战役走 embedding 服务，互不争抢）。
#
# 用法: bash scripts/run_vector_campaign.sh
#
# 设计要点：
#  * 断点续跑：build_vector_index.py 自带 store_file 非空即 skip；
#  * spacy 排最后：其索引由向量索引 v7（任务 bd1c75d88）并行建设中，
#    构建前等待 v7 进程退出、再按 store_file 判定，避免 rmtree 破坏正在写的目录；
#  * 单库失败只记录 rc 不中断战役；
#  * 进度落 experiments/function_solutions/vector_campaign_progress.tsv。
set -u

ROOT="/home/lisy/Projects/respfuzzer"
LOG_DIR="$ROOT/experiments/function_solutions"
MASTER_LOG="$LOG_DIR/vector_campaign_master.log"
PROGRESS="$LOG_DIR/vector_campaign_progress.tsv"
cd "$ROOT" || exit 1
mkdir -p "$LOG_DIR"
[ -f "$PROGRESS" ] || printf 'timestamp\tlibrary\trc\tsecs\tstore_bytes\n' > "$PROGRESS"

# spacy 放最后：与并行的 v7 任务错开
LIBS=(nltk numpy pandas scipy sklearn spacy)   # 6 库收缩后的范围；已建的自动 skip

store_size() { # $1=lib
  local f="data/$1_vector_storage/default__vector_store.json"
  [ -f "$f" ] && stat -c%s "$f" || echo 0
}

run_one() { # $1=lib
  local lib="$1" t0 t1 rc bytes
  # spacy 特判：等 v7 进程退出，避免删掉正在写的目录
  if [ "$lib" = "spacy" ]; then
    local waited=0
    while pgrep -f "build_spacy_index.py" > /dev/null && [ "$waited" -lt 14400 ]; do
      sleep 60; waited=$((waited + 60))
    done
  fi
  t0=$(date +%s)
  printf '[start] %s lib=%s\n' "$(date '+%F %T')" "$lib" | tee -a "$MASTER_LOG"
  uv run python scripts/build_vector_index.py "$lib" >> "$MASTER_LOG" 2>&1
  rc=$?
  t1=$(date +%s)
  bytes=$(store_size "$lib")
  printf '%s\t%s\t%s\t%s\t%s\n' "$(date '+%F %T')" "$lib" "$rc" "$((t1-t0))" "$bytes" >> "$PROGRESS"
  printf '[done ] %s lib=%s rc=%s store=%sB %ss\n' "$(date '+%F %T')" "$lib" "$rc" "$bytes" "$((t1-t0))" | tee -a "$MASTER_LOG"
}

for lib in "${LIBS[@]}"; do
  run_one "$lib"
done

printf '[vcampaign] ALL DONE %s\n' "$(date '+%F %T')" | tee -a "$MASTER_LOG"
