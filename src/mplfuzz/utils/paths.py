from pathlib import Path

UTILS_DIR = Path(__file__).resolve().parent
SOURCE_DIR = UTILS_DIR.parent
PROJECT_DIR = SOURCE_DIR.parent.parent
CONFIG_PATH = PROJECT_DIR.joinpath("config.toml")
RUNDATA_DIR = PROJECT_DIR.joinpath("run_data")

RUNDATA_DIR.mkdir(parents=True, exist_ok=True)
