#!/bin/bash

# Usage: ./run_nl.sh [MODE]
#   MODE: Full (default), NL, NP, NSF, NCF
#
# This script is meant to be run from experiments/RQ4/
# so we anchor relative to that location.

mode="${1:-Full}"

cur_file_dir="$(cd "$(dirname "$0")" && pwd)"
run_data_dir="$cur_file_dir/run_data"
project_root_dir="$(cd "$cur_file_dir/../.." && pwd)"
scripts_dir="$project_root_dir/scripts"
config_file="$scripts_dir/libraries.conf"
DATA_DIR="${RESPFUZZER_DATA_DIR:-${project_root_dir}/RQ2_data_new_111}"

exp_script_path="$cur_file_dir/fuzz_exp-RQ4.py"

source "$config_file"

timestamp=$(date +"%Y%m%d%H%M")


for library in "${libraries[@]}"; do
    input_file="$DATA_DIR/${library}_seeds_sampled.json"
    if [ ! -f "$input_file" ]; then
        echo "WARNING: $input_file not found, skipping."
        continue
    fi

    log_file="$cur_file_dir/RQ4-respfuzzer-${library^}-${timestamp}-mode-${mode}.log"
    if [ "$mode" = "Full" ]; then
        cmd="uv run $exp_script_path $input_file > $log_file 2>&1"
    else
        cmd="uv run $exp_script_path $input_file --mode $mode > $log_file 2>&1"
    fi
    echo "cmd: $cmd"
    eval "$cmd"
done
