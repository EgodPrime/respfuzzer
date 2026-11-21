#!/bin/bash
#
# Run RQ3 ablation campaign for ONE mode (NL / NP / NCF) across all
# measured libraries x 5 repetitions.
#
# Full arm is NOT run here — it reuses RQ2 RespFuzzer results (same config,
# same capped100 seeds, same run_index seed derivation; see rq3.md).
# Budget: fuzz_exp-RQ3.py reads config [fuzz] llm_fuzz_per_seed (=10),
# aligned with RQ2's -lrequest 10. No budget flags on this entry.
#
# Usage:
#   bash experiments/RQ3/run_rq3_ablation_campaign.sh NL
#   REPS_OVERRIDE="4 5" bash ...        # resume: only listed reps
# Datasets: ../RQ2/<lib>_capped100_seeds.json (shared with RQ2)
# cwd: run_data/ (AGENTS.md isolation); artifacts -> experiments/RQ3/
# Progress: run_data/campaign_progress.tsv (fuzzer=respfuzzer-rq3 mode=<M>)

set -euo pipefail

LIBS=(nltk numpy pandas scipy sklearn spacy)
REPS=(${REPS_OVERRIDE:-1 2 3 4 5})
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/run_data"     # isolated cwd (AGENTS.md)
OUT_DIR="$SCRIPT_DIR"               # .log + *_checkpoint.json
SEEDS_DIR="$SCRIPT_DIR/../RQ2"      # shared capped100 datasets
ENTRY="$SCRIPT_DIR/fuzz_exp-RQ3.py"
# 进度文件按模型隔离：不同 LLM 批的 done 记录互不匹配，避免新批被旧批跳过
MODEL_TAG="$(cd "$SCRIPT_DIR/../.." && uv run -q python -c "from respfuzzer.utils.config import get_config; print(str(get_config('llm')['model_name']).replace('/','-').replace(' ','-'))")"
PROGRESS="$DATA_DIR/campaign_progress_${MODEL_TAG}.tsv"

MODE="${1:-}"
case "$MODE" in
    NL|NP|NCF) ;;
    NSF) echo "NSF 已从消融设计移除（语义有效性弱化为调用成功 + 异常计入信号后无独立维度），不再跑批"; exit 1 ;;
    *) echo "usage: $0 <NL|NP|NCF>   (Full reuses RQ2; NSF removed from design)"; exit 1 ;;
esac

mkdir -p "$DATA_DIR"
: >> "$PROGRESS"
echo ">>> rq3 ablation campaign model=$MODEL_TAG mode=$MODE at $(date '+%F %T')"

for RUN_INDEX in "${REPS[@]}"; do
    printf '%s\n' "fuzzer=respfuzzer-rq3 mode=$MODE run_index=$RUN_INDEX status=started" >> "$PROGRESS"
    for lib in "${LIBS[@]}"; do
        # resume skip: already-done (mode, rep, lib) is not re-run
        if grep -q "fuzzer=respfuzzer-rq3 mode=$MODE run_index=$RUN_INDEX library=$lib status=done" "$PROGRESS" 2>/dev/null; then
            echo ">>> [$lib] $MODE rep $RUN_INDEX already done, skip"
            continue
        fi
        ds="$SEEDS_DIR/${lib}_capped100_seeds.json"
        if [[ ! -s "$ds" ]]; then
            echo ">>> SKIP $lib: dataset missing ($ds)"
            printf '%s\n' "fuzzer=respfuzzer-rq3 mode=$MODE run_index=$RUN_INDEX library=$lib status=skipped reason=missing_dataset" >> "$PROGRESS"
            continue
        fi
        echo ">>> [$lib] $MODE rep $RUN_INDEX"
        start=$(date +%s)
        set +e
        (cd "$DATA_DIR" && uv run -q python "$ENTRY" "$ds" \
            --mode "$MODE" --run-index "$RUN_INDEX" --out-dir "$OUT_DIR" \
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
            printf '%s\n' "fuzzer=respfuzzer-rq3 mode=$MODE run_index=$RUN_INDEX library=$lib status=done coverage=$cov duration_s=$dur" >> "$PROGRESS"
        else
            echo ">>> [$lib] $MODE rep $RUN_INDEX FAILED rc=$rc dur=${dur}s"
            printf '%s\n' "fuzzer=respfuzzer-rq3 mode=$MODE run_index=$RUN_INDEX library=$lib status=failed rc=$rc duration_s=$dur" >> "$PROGRESS"
        fi
    done
done
echo ">>> rq3 ablation campaign mode=$MODE end at $(date '+%F %T')"
