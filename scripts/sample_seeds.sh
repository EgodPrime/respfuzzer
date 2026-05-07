#!/bin/bash

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
source $config_file

script_path="$current_dir/sample_seeds.py"

# Default sampling parameters
RATIO=${RATIO:-0.3}
MIN=${MIN:-5}
MAX=${MAX:-50}
SEED=${SEED:-4399}

for library in "${libraries[@]}"
do
    input_file="${RESPFUZZER_DATA_DIR}/${library}_seeds.json"
    if [ -f "$input_file" ]; then
        cmd="uv run $script_path -i $input_file -r $RATIO -m $MIN -s $SEED"
        if [ -n "$MAX" ]; then
            cmd="$cmd -M $MAX"
        fi
        echo "$cmd"
        $cmd
    else
        echo "WARNING: $input_file not found, skipping."
    fi
    echo
done
