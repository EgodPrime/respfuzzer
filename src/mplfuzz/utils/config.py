import tomllib
from typing import Optional
from mplfuzz.utils.paths import CONFIG_PATH
from mplfuzz.utils.result import Err, Ok, Result


def get_config(section: Optional[str] = None) -> Result[dict, str]:
    try:
        with open(CONFIG_PATH, "rb") as f:
            config = tomllib.load(f)
        if section:
            config = config.get(section)
        return Ok(config)
    except Exception as e:
        return Err(f"Failed to load config: {e}")
