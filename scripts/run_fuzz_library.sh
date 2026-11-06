#!/bin/bash

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
# 202511251918
timestamp=$(date +"%Y%m%d%H%M")
# Source the library list
source $config_file
# 预算参数（按单种子计，至少给一个）：
#   RF_REQUEST_BUDGET -> -lrequest N  单种子 LLM 请求次数上限
#   RF_TIME_BUDGET    -> -ltime S     单种子总变异时间(秒)
budget_args=""
if [ -n "${RF_REQUEST_BUDGET:-}" ]; then
    budget_args="$budget_args -lrequest $RF_REQUEST_BUDGET"
fi
if [ -n "${RF_TIME_BUDGET:-}" ]; then
    budget_args="$budget_args -ltime $RF_TIME_BUDGET"
fi
if [ -z "$budget_args" ]; then
    echo "ERROR: set RF_REQUEST_BUDGET and/or RF_TIME_BUDGET (per-seed LLM request count / total mutation seconds)"
    exit 1
fi
# Fuzz each library
log_path="$current_dir/../experiments/RQ5/fuzz_$timestamp.log"
touch "$log_path"
for library in "${libraries[@]}"
do
    echo "fuzz fuzz_library $library"
    uv run fuzz fuzz_library "$library" $budget_args >> "$log_path" 2>&1
done
