#!/bin/bash

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 <count>"
    exit 1
fi

count=$1

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for (( i=1; i<=count; i++ )); do
    echo "=== Iteration $i ==="
    bash "$script_dir/run_respfuzzer.sh"
    bash "$script_dir/run_fuzz4all.sh"
    bash "$script_dir/run_dyfuzz.sh"
done
