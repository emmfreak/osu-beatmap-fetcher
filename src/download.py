"""Download full .osz files via a mirror.

The official osu! API download endpoint (/beatmapsets/{id}/download) is
lazer-client-only and returns 403 for normal OAuth apps, so we fetch .osz
files from a public mirror instead. Mirrors are tried in order.
"""

import time
import zipfile
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

# A real .osz is a ZIP holding at least a .osu file; anything much smaller than
# this is a truncated stream or an error/placeholder stub, never a real map.
MIN_OSZ_BYTES = 1024

_HEADERS = {"User-Agent": "osu-beatmap-fetcher/0.1 (slice1)"}


class DownloadError(Exception):
    pass


def is_valid_osz(path: Path) -> bool:
    """True only if `path` is a real .osz — i.e. a readable, non-empty ZIP.

    Mirrors sometimes return a tiny error/placeholder body or a truncated
    stream that isn't 0 bytes, so a size check alone lets corrupt files through.
    Since .osz *is* a ZIP, opening it (which reads the end-of-central-directory
    record) catches both truncation and non-archive junk cheaply.
    """
    path = Path(path)
    try:
        if path.stat().st_size < MIN_OSZ_BYTES:
            return False
        with zipfile.ZipFile(path) as zf:
            return bool(zf.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


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

            # Reject empty/truncated/stub responses that aren't a real archive
            # so we fall through to the next mirror instead of saving junk that
            # osu! can't import.
            if not is_valid_osz(out_path):
                raise DownloadError(
                    f"{name} returned an invalid or corrupt .osz "
                    f"({out_path.stat().st_size} bytes)"
                )

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


def find_broken_osz(dest_dir: Path) -> list[Path]:
    """Return every ``*.osz`` in `dest_dir` that fails validation.

    Bundle archives (``*.zip``) are ignored — only individual maps are checked.
    """
    dest_dir = Path(dest_dir)
    if not dest_dir.exists():
        return []
    return sorted(f for f in dest_dir.glob("*.osz") if not is_valid_osz(f))


def cleanup_broken_osz(dest_dir: Path) -> list[int]:
    """Delete corrupt ``*.osz`` files in `dest_dir`.

    Returns the beatmapset ids (parsed from the filenames) that were removed,
    so the caller can also drop them from the registry and re-fetch them.
    """
    removed: list[int] = []
    for f in find_broken_osz(dest_dir):
        try:
            f.unlink()
        except OSError:
            continue
        try:
            removed.append(int(f.stem))
        except ValueError:
            pass  # non-numeric name — file removed, just no registry id
    return removed


def bundle_osz(
    paths: list[Path],
    out_path: Path,
    on_progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Path | None:
    """Package many .osz files into one .zip for easy sharing.

    ``.osz`` archives are already zip-compressed (audio/images), so re-deflating
    them costs CPU for near-zero size gain — we store them uncompressed and the
    bundle ends up roughly the sum of its parts. The win is a single file to
    send, not a smaller one. ZIP64 (default) handles bundles well over 4 GB.

    Returns the bundle path, or None if cancelled (partial file is removed).
    """
    out_path = Path(out_path)
    total = len(paths)
    try:
        with zipfile.ZipFile(
            out_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as zf:
            for i, p in enumerate(paths, 1):
                if cancelled and cancelled():
                    zf.close()
                    if out_path.exists():
                        out_path.unlink()
                    return None
                zf.write(p, arcname=Path(p).name)
                if on_progress:
                    on_progress(i, total)
    except Exception:
        if out_path.exists():
            out_path.unlink()
        raise
    return out_path


def bundle_osz_split(
    paths: list[Path],
    out_dir: Path,
    base_name: str,
    max_bytes: int = 0,
    max_files: int = 0,
    on_progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> list[Path] | None:
    """Pack .osz files into several independent .zip parts.

    Each part is a self-contained, separately-extractable archive named
    ``{base_name}-partNN.zip`` — ideal for sending large map packs piece by
    piece (upload limits, chat attachments, etc.).

    Two split modes (pass exactly one):
    - ``max_bytes`` > 0: each part is at most that many bytes. Packing is by
      whole maps (a single .osz never spans two parts, so a map larger than
      ``max_bytes`` gets a part to itself).
    - ``max_files`` > 0: each part holds at most that many maps.

    Returns the list of created part paths, or None if cancelled (any parts
    already written are removed so a partial set isn't mistaken for complete).
    """
    out_dir = Path(out_dir)

    # Greedy first-fit: start a new part when adding a file would exceed
    # whichever limit is active (byte budget or map count).
    groups: list[list[Path]] = []
    current: list[Path] = []
    current_size = 0
    for p in paths:
        p = Path(p)
        size = p.stat().st_size
        over_bytes = max_bytes and current and current_size + size > max_bytes
        over_files = max_files and len(current) >= max_files
        if over_bytes or over_files:
            groups.append(current)
            current, current_size = [], 0
        current.append(p)
        current_size += size
    if current:
        groups.append(current)

    parts: list[Path] = []

    def _cleanup():
        for pp in parts:
            if pp.exists():
                pp.unlink()

    total = len(paths)
    files_done = 0
    width = max(2, len(str(len(groups))))
    for idx, group in enumerate(groups, 1):
        out = out_dir / f"{base_name}-part{idx:0{width}d}.zip"
        try:
            with zipfile.ZipFile(
                out, "w", compression=zipfile.ZIP_STORED, allowZip64=True
            ) as zf:
                for p in group:
                    if cancelled and cancelled():
                        zf.close()
                        if out.exists():
                            out.unlink()
                        _cleanup()
                        return None
                    zf.write(p, arcname=p.name)
                    files_done += 1
                    if on_progress:
                        on_progress(files_done, total)
        except Exception:
            if out.exists():
                out.unlink()
            _cleanup()
            raise
        parts.append(out)

    return parts
