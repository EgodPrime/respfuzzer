import tomllib
from typing import Optional

from mplfuzz.utils.paths import CONFIG_PATH
from mplfuzz.utils.result import Err, Ok, Result, resultify

@resultify
def get_config(section: Optional[str] = None) -> Result[dict, Exception]:
    with open(CONFIG_PATH, "rb") as f:
        config = tomllib.load(f)
    if section:
        config = config.get(section)
    return config