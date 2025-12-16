# src/respfuzzer/utils/paths.py
from os import getenv
from pathlib import Path

UTILS_DIR = Path(__file__).resolve().parent
SOURCE_DIR = UTILS_DIR.parent
PROJECT_DIR = SOURCE_DIR.parent.parent
CONFIG_PATH = PROJECT_DIR.joinpath("config.toml")
data_dir_name = getenv("RESPFUZZER_DATA_DIR")
if data_dir_name is None:
    data_dir_name = "data"
DATA_DIR = PROJECT_DIR.joinpath(data_dir_name)
RUNDATA_DIR = PROJECT_DIR.joinpath("run_data")
FUZZ_DIR = SOURCE_DIR / "fuzz"
FUZZ_BLACKLIST_PATH = FUZZ_DIR / "blacklist.txt"

DATA_DIR.mkdir(parents=True, exist_ok=True)
RUNDATA_DIR.mkdir(parents=True, exist_ok=True)
