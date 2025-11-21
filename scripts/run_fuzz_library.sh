#!/bin/bash

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
# Source the library list
source $config_file
# Fuzz each library
for library in "${libraries[@]}"
do
    echo "fuzz fuzz_library $library"
    fuzz fuzz_library "$library" > "/home/lisy/respfuzzer/experiments/RQ4/20251106-RQ4-$library.log" 2>&1
done
