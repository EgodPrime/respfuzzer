#!/bin/bash

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
source $config_file

script_path="$current_dir/convert_to_dyfuzz_format.py"

for library in "${libraries[@]}"
do
    input_file="${RESPFUZZER_DATA_DIR}/${library}_seeds_sampled.json"
    if [ -f "$input_file" ]; then
        cmd="uv run $script_path -i $input_file"
        echo "$cmd"
        $cmd
    else
        echo "WARNING: $input_file not found, skipping."
    fi
    echo
done
