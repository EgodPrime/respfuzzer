#!/bin/bash
#
# Run RQ2 miniFuzz4All budget campaign for ONE budget mode across all
# measured libraries x 5 repetitions.
#
# Usage:
#   bash experiments/RQ2/run_rq2_campaign.sh -lrequest 10
#   bash experiments/RQ2/run_rq2_campaign.sh -ltime 60
#   bash experiments/RQ2/run_rq2_campaign.sh -ltime 60 capped100
# Seeds (all measured-library functions) live in experiments/RQ2/.
# Per-run .log + *_checkpoint.json go to experiments/RQ2/; a rolling
# campaign_progress.tsv is appended to experiments/RQ2/run_data/.

set -euo pipefail

LIBS=(nltk numpy pandas scipy sklearn spacy)
REPS=(1 2 3 4 5)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$SCRIPT_DIR/run_data"      # isolated cwd for `uv run` (AGENTS.md)
OUT_DIR="$SCRIPT_DIR"                # .log + *_checkpoint.json + seed datasets
SEEDS_DIR="$OUT_DIR"  # sampled seed datasets live next to the run outputs
F4A="$SCRIPT_DIR/miniFuzz4All/fuzz_dataset.py"
PROGRESS="$DATA_DIR/campaign_progress.tsv"

FLAG="${1:-}"
VALUE="${2:-}"
DSUFFIX="${3:-all_functions}"
if [[ -z "$FLAG" || -z "$VALUE" ]]; then
    echo "usage: $0 <-lrequest N | -ltime N>"
    exit 1
fi
RUN_INDEX="${RUN_INDEX:-1}"

echo ">>> campaign start budget='$FLAG $VALUE' run_index=$RUN_INDEX at $(date '+%F %T')"

: >> "$PROGRESS"
printf '%s\n' "budget=$FLAG $VALUE run_index=$RUN_INDEX status=started" >> "$PROGRESS"

mkdir -p "$DATA_DIR"
cd "$DATA_DIR"
for lib in "${LIBS[@]}"; do
    ds="$SEEDS_DIR/${lib}_${DSUFFIX}_seeds.json"
    if [[ ! -s "$ds" ]]; then
        echo ">>> SKIP $lib: dataset missing/empty ($ds)"
        printf '%s\n' "budget=$FLAG $VALUE run_index=$RUN_INDEX library=$lib status=skipped reason=missing_dataset" >> "$PROGRESS"
        continue
    fi
    echo ">>> [$lib] rep $RUN_INDEX (index $RUN_INDEX)"
    start=$(date +%s)
    set +e
    uv run -q python "$F4A" normal "$ds" --rq RQ2 --group fuzz4all --out-dir "$OUT_DIR" \
        --run-index "$RUN_INDEX" "$FLAG" "$VALUE" > /dev/null 2>&1
    rc=$?
    end=$(date +%s)
    set -e
    cov=$(uv run -q python -c "import json,glob; fs=sorted(glob.glob('$OUT_DIR/rq2_fuzz4all_${lib}*_checkpoint.json')); c=json.load(open(fs[-1]))['results'] if fs else {}; print(c.get('final_coverage','?'))" 2>/dev/null || echo "?")
    dur=$((end - start))
    if [[ $rc -eq 0 ]]; then
        echo ">>> [$lib] rep $RUN_INDEX OK coverage=$cov dur=${dur}s"
        printf '%s\n' "budget=$FLAG $VALUE run_index=$RUN_INDEX library=$lib status=done coverage=$cov duration_s=$dur" >> "$PROGRESS"
    else
        echo ">>> [$lib] rep $RUN_INDEX FAILED rc=$rc dur=${dur}s"
        printf '%s\n' "budget=$FLAG $VALUE run_index=$RUN_INDEX library=$lib status=failed rc=$rc duration_s=$dur" >> "$PROGRESS"
    fi
done
echo ">>> campaign end budget='$FLAG $VALUE' at $(date '+%F %T')"
