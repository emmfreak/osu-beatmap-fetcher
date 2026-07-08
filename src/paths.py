"""Base-directory resolution that survives PyInstaller freezing.

Running from source, user data (config.json, registry.db, downloads/,
.osu_cache/, failed_downloads.log) lives at the repo root — one level up
from src/. In a frozen .exe, ``__file__`` points inside PyInstaller's
temporary extraction dir (_MEIPASS), which is throwaway and wiped on exit —
anchoring data there would silently lose the registry and downloads. So when
frozen, everything anchors to the folder the .exe itself sits in instead.
"""

import sys
from pathlib import Path


def app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
