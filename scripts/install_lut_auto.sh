# scripts/install_lut_auto.sh

current_dir=$(dirname "$0")
config_file="$current_dir/libraries.conf"
# Source the library list
source $config_file

for library in "${libraries[@]}"
do
    uv pip install "$library"
done