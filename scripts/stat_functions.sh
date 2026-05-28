#!/usr/bin/env bash
# stat_functions.sh - 统计指定 Data 目录下每个库的 API 数量并展示版本
# 用法: ./scripts/stat_functions.sh <DATA_DIR>
# 示例: ./scripts/stat_functions.sh RQ2_data_common

set -euo pipefail

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"

DATA_DIR="${1:?用法: $0 <DATA_DIR>}"

if [ ! -d "$DATA_DIR" ]; then
    echo "错误: 目录 '$DATA_DIR' 不存在"
    exit 1
fi

# Source the library list
source "$config_file"

# pip 包名映射（库名 -> pip install 名）
declare -A PIP_NAMES=(
    ["sklearn"]="scikit-learn"
    ["yaml"]="pyyaml"
    ["paddle"]="paddlepaddle"
)

# 表头
printf "%-12s %8s  %-20s\n" "库" "函数数" "版本"
printf "%-12s %8s  %-20s\n" "---" "-------" "------"

total=0

for library in "${libraries[@]}"; do
    json_file="$DATA_DIR/${library}_functions.json"
    if [ ! -f "$json_file" ]; then
        continue
    fi

    count=$(python3 -c "import json; print(len(json.load(open('$json_file'))))")

    # 查版本：优先用映射名，否则用库名本身
    pip_name="${PIP_NAMES[$library]:-$library}"
    version=$(uv pip list 2>/dev/null | grep -i "^${pip_name} " | head -1 | awk '{print $2}' || echo "")
    if [ -z "$version" ]; then
        version="N/A"
    fi

    printf "%-12s %8d  %-20s\n" "$library" "$count" "$version"
    total=$((total + count))
done

printf "%-12s %8s  %-20s\n" "合计" "$total" ""
