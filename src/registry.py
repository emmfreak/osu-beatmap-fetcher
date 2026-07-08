"""SQLite registry of downloaded beatmapsets.

Tracks which beatmapset ids we've already pulled so repeated runs never
re-download the same maps.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .paths import app_base_dir

DEFAULT_DB_PATH = app_base_dir() / "registry.db"


class Registry:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS downloads (
                beatmapset_id INTEGER PRIMARY KEY,
                artist        TEXT,
                title         TEXT,
                stars         REAL,
                downloaded_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pp_cache (
                beatmap_id  INTEGER PRIMARY KEY,
                max_pp      REAL NOT NULL,
                computed_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def is_downloaded(self, beatmapset_id: int) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM downloads WHERE beatmapset_id = ?", (beatmapset_id,)
        )
        return cur.fetchone() is not None

    def record(
        self,
        beatmapset_id: int,
        stars: float,
        artist: str = "",
        title: str = "",
    ):
        self.conn.execute(
            """
            INSERT OR REPLACE INTO downloads
                (beatmapset_id, artist, title, stars, downloaded_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                beatmapset_id,
                artist,
                title,
                stars,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.conn.commit()

    def remove(self, beatmapset_id: int) -> bool:
        """Forget a beatmapset so it can be re-downloaded. Returns True if a
        row was actually deleted."""
        cur = self.conn.execute(
            "DELETE FROM downloads WHERE beatmapset_id = ?", (beatmapset_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    def count(self) -> int:
        cur = self.conn.execute("SELECT COUNT(*) AS n FROM downloads")
        return cur.fetchone()["n"]

    def get_cached_pp(self, beatmap_id: int) -> float | None:
        cur = self.conn.execute(
            "SELECT max_pp FROM pp_cache WHERE beatmap_id = ?", (beatmap_id,)
        )
        row = cur.fetchone()
        return row["max_pp"] if row else None

    def cache_pp(self, beatmap_id: int, max_pp: float):
        self.conn.execute(
            """
            INSERT OR REPLACE INTO pp_cache (beatmap_id, max_pp, computed_at)
            VALUES (?, ?, ?)
            """,
            (beatmap_id, max_pp, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()
