#!/bin/bash

# Usage: ./run_nl.sh [MODE]
#   MODE: Full (default), NL, NP, NSF, NCF
#
# This script is meant to be run from experiments/RQ4/
# so we anchor relative to that location.

mode="${1:-Full}"

cur_file_dir="$(cd "$(dirname "$0")" && pwd)"
run_data_dir="$cur_file_dir/run_data"
project_root_dir="$(cd "$cur_file_dir/../.." && pwd)"
scripts_dir="$project_root_dir/scripts"
config_file="$scripts_dir/libraries.conf"
DATA_DIR="${RESPFUZZER_DATA_DIR:-${project_root_dir}/RQ2_data_new_111}"

exp_script_path="$cur_file_dir/fuzz_exp-RQ4.py"

source "$config_file"

# .log 与 *_checkpoint.json 由 experiment_recorder 按统一命名写入 experiments/RQ4/（MC3-1）


# 【MC3-3】配对运行：SEED / RUN_INDEX 由 batch_run.sh（或手工）传入，
# 同一 RUN_INDEX 在各消融配置与 RQ3 三个 fuzzer 上派生出同一个随机种子。
pair_args=""
if [ -n "${SEED:-}" ]; then
    pair_args="$pair_args --seed $SEED"
fi
if [ -n "${RUN_INDEX:-}" ]; then
    pair_args="$pair_args --run-index $RUN_INDEX"
fi

for library in "${libraries[@]}"; do
    input_file="$DATA_DIR/${library}_seeds_sampled.json"
    if [ ! -f "$input_file" ]; then
        echo "WARNING: $input_file not found, skipping."
        continue
    fi

    cmd="uv run $exp_script_path $input_file --mode $mode $pair_args"
    echo "cmd: $cmd"
    eval "$cmd"
done
