#!/bin/bash

# This script is meant to be run from experiments/RQ3/run_data/
# so we anchor relative to that location.

cur_file_dir="$(cd "$(dirname "$0")" && pwd)"
run_data_dir="$cur_file_dir/run_data"
project_root_dir="$(cd "$cur_file_dir/../.." && pwd)"
scripts_dir="$project_root_dir/scripts"
config_file="$scripts_dir/libraries.conf"
DATA_DIR="${RESPFUZZER_DATA_DIR:-${project_root_dir}/RQ2_data_new_111}"

exp_script_path="$cur_file_dir/DyFuzz/run_respfuzzer.py"

source "$config_file"

cd "$cur_file_dir/DyFuzz" || exit 1

# .log 与 *_checkpoint.json 由 experiment_recorder 按统一命名写入 experiments/RQ3/

# 配对运行：SEED / RUN_INDEX 由 batch_run.sh（或手工）传入，
# 同一 RUN_INDEX 在三个 fuzzer 与 RQ3 各消融配置上派生出同一个随机种子。
pair_args=""
if [ -n "${SEED:-}" ]; then
    pair_args="$pair_args --seed $SEED"
fi
if [ -n "${RUN_INDEX:-}" ]; then
    pair_args="$pair_args --run-index $RUN_INDEX"
fi

# DyFuzz 预算参数（按单种子计，至少给一个）：
#   DYFUZZ_MUTANT_BUDGET -> -lmutant N  单种子变异次数上限
#   DYFUZZ_TIME_BUDGET   -> -ltime S    单种子总测试时间(秒)
budget_args=""
if [ -n "${DYFUZZ_MUTANT_BUDGET:-}" ]; then
    budget_args="$budget_args -lmutant $DYFUZZ_MUTANT_BUDGET"
fi
if [ -n "${DYFUZZ_TIME_BUDGET:-}" ]; then
    budget_args="$budget_args -ltime $DYFUZZ_TIME_BUDGET"
fi
if [ -z "$budget_args" ]; then
    echo "ERROR: set DYFUZZ_MUTANT_BUDGET and/or DYFUZZ_TIME_BUDGET (per-seed mutant count / total test seconds)"
    exit 1
fi

for library in "${libraries[@]}"; do
    input_file="$DATA_DIR/${library}_seeds_sampled.json"
    if [ ! -f "$input_file" ]; then
        echo "WARNING: $input_file not found, skipping."
        continue
    fi

    cmd="uv run $exp_script_path $input_file $pair_args $budget_args"
    echo "$cmd"
    eval "$cmd"
done