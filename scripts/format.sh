# !/bin/bash
# scripts/format.sh
cur_dir=$(dirname "$0")
cur_dir=$(realpath "$cur_dir")
project_dir=$(dirname "$cur_dir")
# src
src_dir="$project_dir/src"
# tests
tests_dir="$project_dir/tests"
# delete unused imports and format code
echo "uv run ruff check --fix --select=F401,F403,F405,F811 $src_dir $tests_dir $cur_dir"
uv run ruff check --fix --select=F401,F403,F405,F811 "$src_dir" "$tests_dir" "$cur_dir"
echo "uv run black $src_dir $tests_dir $cur_dir --line-length=88"
uv run black "$src_dir" "$tests_dir" "$cur_dir" --line-length=88