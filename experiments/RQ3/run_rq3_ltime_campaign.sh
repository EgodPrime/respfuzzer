#!/bin/bash
#
# RQ3 ltime 战役：语义驱动变异消融的 -ltime 60 时间预算批（配置与 RQ2 的
# -ltime 60 战役相同口径：每种子 60s，先耗尽者停）。
#
# 范围（见 rq3_<model>_ltime_track.md §1，如 rq3_Llama-3.1-8B-Instruct_ltime_track.md）：
#   * NP / NSF / NCF 三臂 x 6 库 x 5 rep = 90 run；
#   * NL 臂跳过——其分支无 LLM 循环（只单次执行 seed），时间预算惰性、
#     跑了也等价 count 批，分析层 --budget time 下沿用 count 批 NL 数据；
#   * Full 臂不跑——复用 RQ2 RespFuzzer -ltime 60 的 30 个 run
#     （同配置/同 capped100 种子/同 run_index 派生，见 exp.md 复用规则）。
#
# 预算旗标：-ltime 60（每 seed 60s 变异时间）经 normalize_budget_args 归一。
#
# Usage:
#   bash experiments/RQ3/run_rq3_ltime_campaign.sh NP
#   REPS_OVERRIDE="4 5" bash ...        # resume: only listed reps
# Datasets: ../RQ2/<lib>_capped100_seeds.json (shared with RQ2)
# cwd: run_data/ (AGENTS.md isolation); artifacts -> experiments/RQ3/
# Progress: run_data/campaign_progress_ltime_<model>.tsv
#            (fuzzer=respfuzzer-rq3-ltime mode=<M>; per-model isolation)

set -euo pipefail

LIBS=(nltk numpy pandas scipy sklearn spacy)
REPS=(${REPS_OVERRIDE:-1 2 3 4 5})
TIME_BUDGET="${TIME_BUDGET:-60}"   # 每种子秒数，与 RQ2 -ltime 60 对齐
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/run_data"     # isolated cwd (AGENTS.md)
OUT_DIR="$SCRIPT_DIR"               # .log + *_checkpoint.json
SEEDS_DIR="$SCRIPT_DIR/../RQ2"      # shared capped100 datasets
ENTRY="$SCRIPT_DIR/fuzz_exp-RQ3.py"
MODEL_TAG="$(cd "$SCRIPT_DIR/../.." && uv run -q python -c "from respfuzzer.utils.config import get_config; print(str(get_config('llm')['model_name']).replace('/','-').replace(' ','-'))")"
PROGRESS="$DATA_DIR/campaign_progress_ltime_${MODEL_TAG}.tsv"

MODE="${1:-}"
case "$MODE" in
    NP|NSF|NCF) ;;
    *) echo "usage: $0 <NP|NSF|NCF>   (NL/Full 不在此战役：见脚本头注)"; exit 1 ;;
esac

mkdir -p "$DATA_DIR"
: >> "$PROGRESS"
echo ">>> rq3 ltime campaign model=$MODEL_TAG mode=$MODE -ltime $TIME_BUDGET at $(date '+%F %T')"

for RUN_INDEX in "${REPS[@]}"; do
    printf '%s\n' "fuzzer=respfuzzer-rq3-ltime mode=$MODE run_index=$RUN_INDEX status=started" >> "$PROGRESS"
    for lib in "${LIBS[@]}"; do
        # resume skip: already-done (mode, rep, lib) is not re-run
        if grep -q "fuzzer=respfuzzer-rq3-ltime mode=$MODE run_index=$RUN_INDEX library=$lib status=done" "$PROGRESS" 2>/dev/null; then
            echo ">>> [$lib] $MODE rep $RUN_INDEX already done, skip"
            continue
        fi
        ds="$SEEDS_DIR/${lib}_capped100_seeds.json"
        if [[ ! -s "$ds" ]]; then
            echo ">>> SKIP $lib: dataset missing ($ds)"
            printf '%s\n' "fuzzer=respfuzzer-rq3-ltime mode=$MODE run_index=$RUN_INDEX library=$lib status=skipped reason=missing_dataset" >> "$PROGRESS"
            continue
        fi
        echo ">>> [$lib] $MODE rep $RUN_INDEX"
        start=$(date +%s)
        set +e
        (cd "$DATA_DIR" && uv run -q python "$ENTRY" "$ds" \
            --mode "$MODE" -ltime "$TIME_BUDGET" \
            --run-index "$RUN_INDEX" --out-dir "$OUT_DIR" \
            > /dev/null 2>&1)
        rc=$?
        set -e
        end=$(date +%s)
        dur=$((end - start))
        cov=$(uv run -q python -c "
import json, glob
fs = sorted(glob.glob('$OUT_DIR/rq3_respfuzzer_${lib}_*_mode-${MODE,,}_checkpoint.json'))
c = json.load(open(fs[-1]))['results'] if fs else {}
print(c.get('final_coverage', '?'))
" 2>/dev/null || echo "?")
        if [[ $rc -eq 0 ]]; then
            echo ">>> [$lib] $MODE rep $RUN_INDEX OK coverage=$cov dur=${dur}s"
            printf '%s\n' "fuzzer=respfuzzer-rq3-ltime mode=$MODE run_index=$RUN_INDEX library=$lib status=done coverage=$cov duration_s=$dur" >> "$PROGRESS"
        else
            echo ">>> [$lib] $MODE rep $RUN_INDEX FAILED rc=$rc dur=${dur}s"
            printf '%s\n' "fuzzer=respfuzzer-rq3-ltime mode=$MODE run_index=$RUN_INDEX library=$lib status=failed rc=$rc duration_s=$dur" >> "$PROGRESS"
        fi
    done
done
echo ">>> rq3 ltime campaign mode=$MODE end at $(date '+%F %T')"
