#!/bin/bash
#
# Run RQ2 DyFuzz budget campaign (ONE budget mode) across all measured
# libraries x 5 repetitions. DyFuzz has NO LLM — `-lmutant N` counts
# mutants (= test executions) per seed.
#
# Usage:
#   bash experiments/RQ2/run_rq2_dyfuzz_campaign.sh -lmutant 10
#   bash experiments/RQ2/run_rq2_dyfuzz_campaign.sh -ltime 60
# Datasets: experiments/RQ2/<lib>_capped100_seeds.json
# cwd: run_data/ (AGENTS.md isolation); checkpoints/.log -> experiments/RQ2/
# Progress: appended to run_data/campaign_progress.tsv

set -euo pipefail

LIBS=(nltk numpy pandas scipy sklearn spacy)
REPS=(${REPS_OVERRIDE:-1 2 3 4 5})   # 续跑：REPS_OVERRIDE="4 5" 只跑指定 rep
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/run_data"     # isolated cwd for `uv run` (AGENTS.md)
OUT_DIR="$SCRIPT_DIR"               # .log + *_checkpoint.json + seed datasets
SEEDS_DIR="$OUT_DIR"
DYFUZZ="$SCRIPT_DIR/DyFuzz/run_respfuzzer.py"
PROGRESS="$DATA_DIR/campaign_progress.tsv"

FLAG="${1:-}"
VALUE="${2:-}"
if [[ -z "$FLAG" || -z "$VALUE" ]]; then
    echo "usage: $0 <-lmutant N | -ltime S>"
    exit 1
fi

mkdir -p "$DATA_DIR"
: >> "$PROGRESS"
echo ">>> dyfuzz campaign start budget='$FLAG $VALUE' at $(date '+%F %T')"

for RUN_INDEX in "${REPS[@]}"; do
    printf '%s\n' "fuzzer=dyfuzz budget=$FLAG $VALUE run_index=$RUN_INDEX status=started" >> "$PROGRESS"
    for lib in "${LIBS[@]}"; do
    # 续跑跳过：该 (budget, rep, lib) 已 done 则不重跑
        if grep -q "fuzzer=dyfuzz budget=$FLAG $VALUE run_index=$RUN_INDEX library=$lib status=done" "$PROGRESS" 2>/dev/null; then
            echo ">>> [$lib] rep $RUN_INDEX already done, skip"
            continue
        fi
        ds="$SEEDS_DIR/${lib}_capped100_seeds.json"
        if [[ ! -s "$ds" ]]; then
            echo ">>> SKIP $lib: dataset missing ($ds)"
            printf '%s\n' "fuzzer=dyfuzz budget=$FLAG $VALUE run_index=$RUN_INDEX library=$lib status=skipped reason=missing_dataset" >> "$PROGRESS"
            continue
        fi
        echo ">>> [$lib] rep $RUN_INDEX"
        start=$(date +%s)
        set +e
        (cd "$DATA_DIR" && uv run -q python "$DYFUZZ" "$ds" \
            --run-index "$RUN_INDEX" "$FLAG" "$VALUE" > /dev/null 2>&1)
        rc=$?
        set -e
        end=$(date +%s)
        dur=$((end - start))
        cov=$(uv run -q python -c "
import json, glob
fs = sorted(glob.glob('$OUT_DIR/rq3_dyfuzz_${lib}_*_checkpoint.json'))
c = json.load(open(fs[-1]))['results'] if fs else {}
print(c.get('final_coverage', '?'))
" 2>/dev/null || echo "?")
        if [[ $rc -eq 0 ]]; then
            echo ">>> [$lib] rep $RUN_INDEX OK coverage=$cov dur=${dur}s"
            printf '%s\n' "fuzzer=dyfuzz budget=$FLAG $VALUE run_index=$RUN_INDEX library=$lib status=done coverage=$cov duration_s=$dur" >> "$PROGRESS"
        else
            echo ">>> [$lib] rep $RUN_INDEX FAILED rc=$rc dur=${dur}s"
            printf '%s\n' "fuzzer=dyfuzz budget=$FLAG $VALUE run_index=$RUN_INDEX library=$lib status=failed rc=$rc duration_s=$dur" >> "$PROGRESS"
        fi
    done
done
echo ">>> dyfuzz campaign end budget='$FLAG $VALUE' at $(date '+%F %T')"
