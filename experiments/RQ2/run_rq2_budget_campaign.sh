#!/bin/bash
#
# RQ2 miniFuzz4All budget campaign master: run ALL 5 repetitions of one
# budget mode across all measured libraries.
#
# Usage:
#   bash experiments/RQ2/run_rq2_budget_campaign.sh -lrequest 10 capped100
#   bash experiments/RQ2/run_rq2_budget_campaign.sh -ltime 60   capped100
#   (temporary compat shim `scripts/run_rq2_budget_campaign.sh` forwards here)
#
# Delegates to run_rq2_campaign.sh (one rep per invocation, via RUN_INDEX).

set -euo pipefail

FLAG="${1:-}"
VALUE="${2:-}"
SUFFIX="${3:-capped100}"

if [[ -z "$FLAG" || -z "$VALUE" ]]; then
    echo "usage: $0 <-lrequest N | -ltime N> [all_functions|capped100]"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

for rep in 1 2 3 4 5; do
    echo "=== master: budget='$FLAG $VALUE' rep=$rep at $(date '+%F %T') ==="
    RUN_INDEX="$rep" bash "$SCRIPT_DIR/run_rq2_campaign.sh" "$FLAG" "$VALUE" "$SUFFIX"
done
echo "=== master done: budget='$FLAG $VALUE' at $(date '+%F %T') ==="
