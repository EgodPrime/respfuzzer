#!/bin/bash

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
# Source the library list
source $config_file

# Generate function calls for each library
for library in "${libraries[@]}"
do
    echo "uv run reflective_seeder generate_seeds $library"
    uv run reflective_seeder generate_seeds "$library"
done
