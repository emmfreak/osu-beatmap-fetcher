"""PP calculation via rosu-pp-py.

Computes max (SS, nomod) PP per difficulty. This is the ceiling — PP earned
depends on the player's accuracy and mods. Values match what PP-farm sites
show and should be labelled "max (SS) PP" in any UI.

PP is per-difficulty, not per-set. The filter operates on individual beatmaps
within a set: a set "passes" if any of its matching difficulties fall within
the PP range.

.osu files are fetched from https://osu.ppy.sh/osu/{beatmap_id} (no auth
required) and cached locally. Computed PP values are cached in SQLite so
each difficulty is only ever calculated once.
"""

import time
from pathlib import Path

import requests
import rosu_pp_py as rosu

from .client import BeatmapsetHit, BeatmapInfo
from .registry import Registry

OSU_CACHE_DIR = Path(__file__).resolve().parent.parent / ".osu_cache"
_HEADERS = {"User-Agent": "osu-beatmap-fetcher/0.3 (slice3)"}
FETCH_DELAY = 0.3

_MODE_MAP = {
    0: rosu.GameMode.Osu,
    1: rosu.GameMode.Taiko,
    2: rosu.GameMode.Catch,
    3: rosu.GameMode.Mania,
}


def fetch_osu_file(beatmap_id: int) -> Path:
    """Fetch and cache a .osu file. Returns the local path."""
    OSU_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = OSU_CACHE_DIR / f"{beatmap_id}.osu"
    if path.exists() and path.stat().st_size > 0:
        return path

    url = f"https://osu.ppy.sh/osu/{beatmap_id}"
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    if len(resp.content) < 50:
        raise ValueError(f"Empty or invalid .osu for beatmap {beatmap_id}")
    path.write_bytes(resp.content)
    time.sleep(FETCH_DELAY)
    return path


def compute_max_pp(beatmap_id: int, mode_int: int) -> float:
    """Compute max (SS, nomod) PP for a single difficulty.

    For mania, accuracy=100% with no misses. Max combo is irrelevant for mania.
    """
    osu_path = fetch_osu_file(beatmap_id)
    beatmap = rosu.Beatmap(path=str(osu_path))

    game_mode = _MODE_MAP.get(mode_int, rosu.GameMode.Mania)
    if beatmap.mode != game_mode:
        beatmap.convert(game_mode)

    perf = rosu.Performance(accuracy=100.0, misses=0)
    result = perf.calculate(beatmap)
    return round(result.pp, 2)


def compute_pp_cached(beatmap_id: int, mode_int: int, registry: Registry) -> float:
    """Compute PP with SQLite caching."""
    cached = registry.get_cached_pp(beatmap_id)
    if cached is not None:
        return cached

    pp = compute_max_pp(beatmap_id, mode_int)
    registry.cache_pp(beatmap_id, pp)
    return pp


def filter_by_pp(
    candidates: list[BeatmapsetHit],
    pp_min: float | None,
    pp_max: float | None,
    registry: Registry,
) -> list[BeatmapsetHit]:
    """Filter beatmapset candidates by PP range.

    For each candidate, computes PP for its matching beatmaps (difficulties).
    A set passes if at least one difficulty has PP in [pp_min, pp_max].
    Returns a new list of passing sets (with beatmaps narrowed to matches).
    """
    if pp_min is None and pp_max is None:
        return candidates

    passed: list[BeatmapsetHit] = []
    total = len(candidates)

    for i, hit in enumerate(candidates):
        print(f"  PP calc {i + 1}/{total}: {hit.artist} - {hit.title} ...", end="", flush=True)
        matching_bms: list[BeatmapInfo] = []
        for bm in hit.beatmaps:
            try:
                pp = compute_pp_cached(bm.id, bm.mode_int, registry)
            except Exception as e:
                print(f" [err:{e}]", end="")
                continue
            if pp_min is not None and pp < pp_min:
                continue
            if pp_max is not None and pp > pp_max:
                continue
            matching_bms.append(bm)

        if matching_bms:
            print(f" PASS ({len(matching_bms)} diffs)")
            passed.append(BeatmapsetHit(
                id=hit.id,
                artist=hit.artist,
                title=hit.title,
                stars=max(b.difficulty_rating for b in matching_bms),
                beatmaps=matching_bms,
            ))
        else:
            print(" skip")

    return passed
