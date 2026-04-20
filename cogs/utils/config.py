"""Channel configuration management (JSON file-based)."""

import json
import os

from cogs.utils.constants import DATA_DIR

CONFIG_PATH = os.path.join(DATA_DIR, "channels.json")


def load_config() -> dict:
    """Load channel configuration from JSON file."""
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(config: dict):
    """Save channel configuration to JSON file."""
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def get_channel_id(guild_id: int, channel_type: str) -> int | None:
    """Get channel ID for a guild and channel type."""
    config = load_config()
    return config.get(str(guild_id), {}).get(channel_type)


def set_channel_id(guild_id: int, channel_type: str, channel_id: int):
    """Set channel ID for a guild and channel type."""
    config = load_config()
    guild_key = str(guild_id)
    if guild_key not in config:
        config[guild_key] = {}
    config[guild_key][channel_type] = channel_id
    save_config(config)
