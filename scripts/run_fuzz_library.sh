#!/bin/bash

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
# 202511251918
timestamp=$(date +"%Y%m%d%H%M")
# Source the library list
source $config_file
# Fuzz each library
log_path="$current_dir/../experiments/RQ5/fuzz_$timestamp.log"
touch "$log_path"
for library in "${libraries[@]}"
do
    echo "fuzz fuzz_library $library"
    uv run fuzz fuzz_library "$library" >> "$log_path" 2>&1
done
