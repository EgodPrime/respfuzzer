#!/bin/bash

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
# 202511251918
timestamp=$(date +"%Y%m%d%H%M")
# Source the library list
source $config_file
# Fuzz each library
for library in "${libraries[@]}"
do
    echo "fuzz fuzz_library $library"
    fuzz fuzz_library "$library" > "$current_dir/../experiments/RQ4/RQ4-$library-$timestamp.log" 2>&1
done
