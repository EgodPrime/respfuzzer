import tomllib
from typing import Optional

from respfuzzer.utils.paths import CONFIG_PATH


def get_config(section: Optional[str] = None) -> dict:
    """Get configuration from the config.toml file.
    If section is provided, return only that section of the config.
    Args:
        section (str, optional): The section of the config to return. Defaults to None.
    Returns:
        dict: The configuration dictionary.
    Raises:
        FileNotFoundError: If the config.toml file is not found.
        tomllib.TOMLDecodeError: If the config.toml file is not a valid TOML file.
        KeyError: If the section is not found in the config.
    """
    with open(CONFIG_PATH, "rb") as f:
        config = tomllib.load(f)
    if section:
        config = config.get(section)
    return config
