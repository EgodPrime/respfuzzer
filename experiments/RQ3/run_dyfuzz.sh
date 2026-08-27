#!/bin/bash

# This script is meant to be run from experiments/RQ3/run_data/
# so we anchor relative to that location.

cur_file_dir="$(cd "$(dirname "$0")" && pwd)"
run_data_dir="$cur_file_dir/run_data"
project_root_dir="$(cd "$cur_file_dir/../.." && pwd)"
scripts_dir="$project_root_dir/scripts"
config_file="$scripts_dir/libraries.conf"
DATA_DIR="${RESPFUZZER_DATA_DIR:-${project_root_dir}/RQ2_data_new_111}"

exp_script_path="$cur_file_dir/DyFuzz/run_respfuzzer.py"

source "$config_file"

cd "$cur_file_dir/DyFuzz" || exit 1

timestamp=$(date +"%Y%m%d%H%M")

for library in "${libraries[@]}"; do
    input_file="$DATA_DIR/${library}_seeds_sampled.json"
    if [ ! -f "$input_file" ]; then
        echo "WARNING: $input_file not found, skipping."
        continue
    fi

    log_file="$cur_file_dir/RQ3-DyFuzz-${library^}-${timestamp}.log"
    cmd="uv run $exp_script_path $input_file > $log_file 2>&1"
    echo "$cmd"
    eval "$cmd"
done