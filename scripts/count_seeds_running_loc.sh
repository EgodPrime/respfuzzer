#!/bin/bash

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
source $config_file

script_path="$current_dir/count_seeds_running_loc.py"

# Determine data directory: use RESPFUZZER_DATA_DIR if set, otherwise fall back to RQ2_data_new_111
DATA_DIR="${RESPFUZZER_DATA_DIR:-${current_dir}/../RQ2_data_new_111}"

# Default: count xx_seeds.json. Use SAMPLED=1 to count xx_seeds_sampled.json instead.
SAMPLED="${SAMPLED:-0}"

for library in "${libraries[@]}"
do
    if [ "$SAMPLED" = "1" ]; then
        input_file="${DATA_DIR}/${library}_seeds_sampled.json"
    else
        input_file="${DATA_DIR}/${library}_seeds.json"
    fi

    if [ -f "$input_file" ]; then
        cmd="uv run $script_path -f $input_file -s $library"
        echo "$cmd"
        $cmd
    else
        echo "WARNING: $input_file not found, skipping."
    fi
    echo
done
