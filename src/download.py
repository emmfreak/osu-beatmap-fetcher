"""Download full .osz files via a mirror.

The official osu! API download endpoint (/beatmapsets/{id}/download) is
lazer-client-only and returns 403 for normal OAuth apps, so we fetch .osz
files from a public mirror instead. Mirrors are tried in order.
"""

import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

from .paths import app_base_dir

# Repo root when run from source; the .exe's folder when frozen — same base
# dir the registry.db lives in.
_BASE_DIR = app_base_dir()
DEFAULT_DOWNLOAD_DIR = _BASE_DIR / "downloads"

# Durable record of downloads that failed on every mirror. Lives next to
# registry.db so it survives the GUI closing and doesn't depend on the cwd.
FAILED_LOG_PATH = _BASE_DIR / "failed_downloads.log"
_failed_log_lock = threading.Lock()

# Download mirrors, tried in order until one serves a valid .osz. A single
# mirror going dark (or missing a specific map) just fails over to the next.
# URL patterns verified against real beatmapset ids with diagnose_mirrors.py;
# no-video variants are preferred where the mirror supports one so file sizes
# stay consistent across mirrors:
#   - osu.direct  ?noVideo=1        (true no-video, sends Content-Length)
#   - nerinyan    ?nv=1             (no-video; chunked, occasional rate-limit stub)
#   - sayobot     /novideo/{id}     (no-video; byte-for-byte match to osu.direct)
#   - beatconnect /b/{id}           (valid osz; rate-limits aggressively -> low)
#   - catboy      /d/{id}           (standard endpoint; currently down 502, kept
#                                    as fail-over — a dead mirror just cascades)
MIRRORS = [
    ("osu.direct",  "https://osu.direct/api/d/{id}?noVideo=1"),
    ("nerinyan",    "https://api.nerinyan.moe/d/{id}?nv=1"),
    ("sayobot",     "https://dl.sayobot.cn/beatmaps/download/novideo/{id}"),
    ("beatconnect", "https://beatconnect.io/b/{id}"),
    ("catboy",      "https://catboy.best/d/{id}"),
]

DOWNLOAD_DELAY = 0.5
DOWNLOAD_WORKERS = 4

# Per-mirror retry: a transient failure (timeout, connection error, 429, 5xx)
# gets ONE retry against the same mirror after a short backoff before failing
# over. A clean 404 / non-archive body is not retried — that mirror simply
# doesn't have this map, so move on immediately.
MIRROR_RETRY_BACKOFF = 1.0

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


class _MirrorAttemptError(Exception):
    """One mirror attempt failed.

    ``transient`` marks retryable failures (timeout, connection error, HTTP 429
    or 5xx) that get one retry against the same mirror; definitive failures
    (404, non-archive body, corrupt/stub .osz) are not retried — that mirror
    just doesn't have a good copy, so we cascade to the next one immediately.
    """

    def __init__(self, message: str, transient: bool):
        super().__init__(message)
        self.transient = transient


def _fetch_from_mirror(name: str, url: str, out_path: Path, timeout: int) -> None:
    """Download and validate one .osz from one mirror URL into ``out_path``.

    Returns on success (``out_path`` holds a valid .osz); otherwise raises
    ``_MirrorAttemptError`` with its transient/definitive classification.
    """
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout, stream=True)
    except requests.Timeout:
        raise _MirrorAttemptError(f"{name}: request timed out", transient=True)
    except requests.RequestException as e:
        raise _MirrorAttemptError(
            f"{name}: connection error ({type(e).__name__})", transient=True
        )

    try:
        status = resp.status_code
        if status != 200:
            # 429 / 5xx are transient (rate-limit or server hiccup); a 404 or
            # other 4xx means this mirror hasn't got the map — don't retry it.
            transient = status == 429 or 500 <= status < 600
            raise _MirrorAttemptError(f"{name}: HTTP {status}", transient=transient)

        content_type = resp.headers.get("Content-Type", "")
        if "application/json" in content_type or "text/html" in content_type:
            raise _MirrorAttemptError(
                f"{name}: non-archive content ({content_type})", transient=False
            )

        try:
            with out_path.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        except requests.RequestException as e:
            raise _MirrorAttemptError(
                f"{name}: dropped mid-download ({type(e).__name__})", transient=True
            )
    finally:
        resp.close()

    # Reject empty/truncated/stub responses so we fail over instead of saving
    # junk osu! can't import (e.g. nerinyan's occasional rate-limit stub).
    if not is_valid_osz(out_path):
        size = out_path.stat().st_size if out_path.exists() else 0
        raise _MirrorAttemptError(
            f"{name}: invalid or corrupt .osz ({size} bytes)", transient=False
        )


def download_beatmapset(
    beatmapset_id: int,
    dest_dir: Path = DEFAULT_DOWNLOAD_DIR,
    timeout: int = 60,
) -> Path:
    """Fetch the .osz for `beatmapset_id` and save it. Returns the file path.

    Tries each mirror in order, with one retry per mirror on a transient
    failure; raises DownloadError only if every mirror fails.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path = dest_dir / f"{beatmapset_id}.osz"

    errors: list[str] = []
    for name, template in MIRRORS:
        url = template.format(id=beatmapset_id)
        for attempt in range(2):  # initial try + up to one retry on transient
            try:
                _fetch_from_mirror(name, url, out_path, timeout)
                return out_path
            except _MirrorAttemptError as e:
                if out_path.exists():
                    out_path.unlink()
                if e.transient and attempt == 0:
                    time.sleep(MIRROR_RETRY_BACKOFF)
                    continue  # retry this same mirror once before failing over
                errors.append(str(e))
                break  # give up on this mirror, cascade to the next

    raise DownloadError(
        f"All mirrors failed for beatmapset {beatmapset_id}: " + "; ".join(errors)
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


def log_failed_download(
    beatmapset_id: int,
    artist: str,
    title: str,
    error: str,
    log_path: Path = FAILED_LOG_PATH,
) -> None:
    """Append one line recording a beatmapset that failed on every mirror.

    Format: ``<iso-8601> | id=<id> | <artist> - <title> | <error>``. Append-only
    (existing entries are never touched) and thread-safe, so the parallel
    download workers can't interleave a line mid-write. The `error` string is the
    DownloadError message, which already names which mirror(s) failed and why.
    """
    def _clean(value) -> str:
        return str(value).replace("\r", " ").replace("\n", " ").strip()

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = (
        f"{ts} | id={beatmapset_id} | "
        f"{_clean(artist)} - {_clean(title)} | {_clean(error)}\n"
    )
    with _failed_log_lock:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)


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
