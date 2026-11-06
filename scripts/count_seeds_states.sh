#!/bin/bash

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
source $config_file

script_path="$current_dir/count_seeds_states.py"

# Default: count xx_seeds.json. Use SAMPLED=1 to count xx_seeds_sampled.json instead.
SAMPLED="${SAMPLED:-0}"
# RQ2=1 counts the RQ2 sampled sets: experiments/RQ2/<lib>_capped100_seeds.json
RQ2="${RQ2:-0}"
rq2_dir="$current_dir/../experiments/RQ2"

for library in "${libraries[@]}"
do
    if [ "$RQ2" = "1" ]; then
        input_file="${rq2_dir}/${library}_capped100_seeds.json"
    elif [ "$SAMPLED" = "1" ]; then
        input_file="${RESPFUZZER_DATA_DIR}/${library}_seeds_sampled.json"
    else
        input_file="${RESPFUZZER_DATA_DIR}/${library}_seeds.json"
    fi

    if [ -f "$input_file" ]; then
        echo "uv run $script_path -f $input_file"
        uv run "$script_path" -f "$input_file"
    else
        echo "WARNING: $input_file not found, skipping."
    fi
    echo
done
