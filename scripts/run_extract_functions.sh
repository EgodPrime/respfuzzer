#!/bin/bash

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
# Source the library list
source $config_file

# Extract function information for each library
for library in "${libraries[@]}"
do
    echo "reflective_seeder extract_functions $library"
    reflective_seeder extract_functions "$library"
done
