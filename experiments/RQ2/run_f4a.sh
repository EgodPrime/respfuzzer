current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
# Source the library list
source $config_file

script_path="$current_dir/../miniFuzz4All/fuzz_library.py"
# Fuzz each library
for library in "${libraries[@]}"
do
    python "$script_path" "$library"
done