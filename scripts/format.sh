# !/bin/bash
# scripts/format.sh
cur_dir=$(dirname "$0")
cur_dir=$(realpath "$cur_dir")
project_dir=$(dirname "$cur_dir")
# src
src_dir="$project_dir/src"
# tests
tests_dir="$project_dir/tests"
ruff check --fix --select=I "$src_dir" "$tests_dir" "$cur_dir"
black "$src_dir" "$tests_dir" "$cur_dir" --line-length=88