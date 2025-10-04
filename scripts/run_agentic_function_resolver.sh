#!/bin/bash

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
# Source the library list
source $config_file

# Generate function calls for each library
for library in "${libraries[@]}"
do
    echo "agentic_function_resolver $library"
    agentic_function_resolver "$library"
done
