#!/bin/bash

# This script is meant to be run from experiments/RQ3/run_data/
# so we anchor relative to that location.

cur_file_dir="$(cd "$(dirname "$0")" && pwd)"
run_data_dir="$cur_file_dir/run_data"
project_root_dir="$(cd "$cur_file_dir/../.." && pwd)"
scripts_dir="$project_root_dir/scripts"
config_file="$scripts_dir/libraries.conf"
DATA_DIR="${RESPFUZZER_DATA_DIR:-${project_root_dir}/RQ2_data_new_111}"

f4a_script_path="$cur_file_dir/miniFuzz4All/fuzz_dataset.py"

source "$config_file"

timestamp=$(date +"%Y%m%d%H%M")


for library in "${libraries[@]}"; do
    input_file="$DATA_DIR/${library}_seeds_sampled.json"
    if [ ! -f "$input_file" ]; then
        echo "WARNING: $input_file not found, skipping."
        continue
    fi

    log_file="$cur_file_dir/RQ3-fuzz4all-${library^}-${timestamp}.log"
    cmd="uv run $f4a_script_path normal $input_file > $log_file 2>&1"
    echo "cmd: $cmd"
    eval "$cmd"
done