"""Load osu! OAuth credentials from a local config.json.

The real config.json holds the client_id/client_secret and is gitignored.
See config.json.template for the expected shape.
"""

import json
from pathlib import Path

from .paths import app_base_dir

# config.json lives at the repo root (next to the .exe when frozen).
DEFAULT_CONFIG_PATH = app_base_dir() / "config.json"


class ConfigError(Exception):
    pass


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Return {'client_id': ..., 'client_secret': ...} from config.json."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"Config not found at {path}. Copy config.json.template to "
            f"config.json and fill in your osu! OAuth credentials."
        )

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    for key in ("client_id", "client_secret"):
        if not data.get(key):
            raise ConfigError(f"Missing '{key}' in {path}")

    return {
        "client_id": str(data["client_id"]),
        "client_secret": str(data["client_secret"]),
    }
