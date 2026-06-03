"""Download full .osz files via a mirror.

The official osu! API download endpoint (/beatmapsets/{id}/download) is
lazer-client-only and returns 403 for normal OAuth apps, so we fetch .osz
files from a public mirror instead. Mirrors are tried in order.
"""

import time
from pathlib import Path

import requests

DEFAULT_DOWNLOAD_DIR = Path(__file__).resolve().parent.parent / "downloads"

# Mirror .osz endpoints, tried in order. {id} is the beatmapset id.
MIRRORS = [
    ("catboy", "https://catboy.best/d/{id}"),
    ("nerinyan", "https://api.nerinyan.moe/d/{id}"),
]

# Polite delay between downloads (seconds).
DOWNLOAD_DELAY = 2.0

_HEADERS = {"User-Agent": "osu-beatmap-fetcher/0.1 (slice1)"}


class DownloadError(Exception):
    pass


def download_beatmapset(
    beatmapset_id: int,
    dest_dir: Path = DEFAULT_DOWNLOAD_DIR,
    timeout: int = 60,
) -> Path:
    """Fetch the .osz for `beatmapset_id` and save it. Returns the file path.

    Tries each mirror in turn; raises DownloadError if all fail.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / f"{beatmapset_id}.osz"

    last_err = None
    for name, template in MIRRORS:
        url = template.format(id=beatmapset_id)
        try:
            resp = requests.get(
                url, headers=_HEADERS, timeout=timeout, stream=True
            )
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type or "text/html" in content_type:
                # Mirror returned an error page, not an archive.
                raise DownloadError(
                    f"{name} returned non-archive content ({content_type})"
                )

            with out_path.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            if out_path.stat().st_size == 0:
                raise DownloadError(f"{name} returned an empty file")

            return out_path
        except (requests.RequestException, DownloadError) as e:
            last_err = e
            if out_path.exists():
                out_path.unlink()  # clean up a partial/bad file
            continue

    raise DownloadError(
        f"All mirrors failed for beatmapset {beatmapset_id}: {last_err}"
    )


def polite_delay():
    """Sleep between downloads to be gentle on mirrors."""
    time.sleep(DOWNLOAD_DELAY)
