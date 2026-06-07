"""Download full .osz files via a mirror.

The official osu! API download endpoint (/beatmapsets/{id}/download) is
lazer-client-only and returns 403 for normal OAuth apps, so we fetch .osz
files from a public mirror instead. Mirrors are tried in order.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

DEFAULT_DOWNLOAD_DIR = Path(__file__).resolve().parent.parent / "downloads"

MIRRORS = [
    ("catboy", "https://catboy.best/d/{id}"),
    ("nerinyan", "https://api.nerinyan.moe/d/{id}"),
]

DOWNLOAD_DELAY = 0.5
DOWNLOAD_WORKERS = 4

_HEADERS = {"User-Agent": "osu-beatmap-fetcher/0.1 (slice1)"}


class DownloadError(Exception):
    pass


@dataclass
class DownloadResult:
    beatmapset_id: int
    path: Path | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.path is not None


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
                out_path.unlink()
            continue

    raise DownloadError(
        f"All mirrors failed for beatmapset {beatmapset_id}: {last_err}"
    )


def _download_one(beatmapset_id: int, dest_dir: Path) -> DownloadResult:
    try:
        path = download_beatmapset(beatmapset_id, dest_dir=dest_dir)
        time.sleep(DOWNLOAD_DELAY)
        return DownloadResult(beatmapset_id=beatmapset_id, path=path)
    except DownloadError as e:
        return DownloadResult(beatmapset_id=beatmapset_id, error=str(e))


def download_beatmapsets_parallel(
    beatmapset_ids: list[int],
    dest_dir: Path = DEFAULT_DOWNLOAD_DIR,
    on_result: Callable[[DownloadResult], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> list[DownloadResult]:
    """Download multiple .osz files concurrently. Returns results in completion order."""
    results: list[DownloadResult] = []
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        futures = {
            pool.submit(_download_one, bsid, dest_dir): bsid
            for bsid in beatmapset_ids
        }
        for fut in as_completed(futures):
            if cancelled and cancelled():
                pool.shutdown(wait=False, cancel_futures=True)
                break
            result = fut.result()
            results.append(result)
            if on_result:
                on_result(result)
    return results


def polite_delay():
    """Sleep between downloads to be gentle on mirrors."""
    time.sleep(DOWNLOAD_DELAY)
