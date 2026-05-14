#!/bin/bash

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
source "$config_file"

script_path="$current_dir/get_library_num_total_lines.py"

for library in "${libraries[@]}"; do
    cmd="uv run $script_path $library"
    echo "+ $cmd"
    $cmd
    echo
done