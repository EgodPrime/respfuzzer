#!/bin/bash

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
# Source the library list
source $config_file
script_path="$current_dir/count_functions_loc.py"

# Generate function calls for each library
for library in "${libraries[@]}"
do
    echo "uv run $script_path -f $current_dir/functions_$library.json"
    uv run $script_path -f ${RESPFUZZER_DATA_DIR}/${library}_functions.json
done
