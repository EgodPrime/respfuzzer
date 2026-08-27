#!/bin/bash

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <count>"
    exit 1
fi

count=$1


script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
run_script="${script_dir}/run.sh"

for (( i=1; i<=count; i++ )); do
    bash "$run_script" Full
    bash "$run_script" NL
    bash "$run_script" NP
    bash "$run_script" NCF
    bash "$run_script" NSF
done
