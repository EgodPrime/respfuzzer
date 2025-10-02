#!/bin/bash

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
# Source the library list
source $config_file

# Extract API information for each library
for library in "${libraries[@]}"
do
    echo "library_visitor $library"
    library_visitor "$library"
done
