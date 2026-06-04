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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import rosu_pp_py as rosu

from .client import BeatmapsetHit, BeatmapInfo
from .registry import Registry

OSU_CACHE_DIR = Path(__file__).resolve().parent.parent / ".osu_cache"
_HEADERS = {"User-Agent": "osu-beatmap-fetcher/0.3 (slice3)"}
FETCH_DELAY = 0.15

_MODE_MAP = {
    0: rosu.GameMode.Osu,
    1: rosu.GameMode.Taiko,
    2: rosu.GameMode.Catch,
    3: rosu.GameMode.Mania,
}

WORKERS = 8
BATCH_SIZE = 8


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


def _pp_in_range(pp: float, pp_min: float | None, pp_max: float | None) -> bool:
    if pp_min is not None and pp < pp_min:
        return False
    if pp_max is not None and pp > pp_max:
        return False
    return True


def _load_pp_cache(registry: Registry) -> dict[int, float]:
    """Bulk-load all cached PP values into a dict for thread-safe reads."""
    cur = registry.conn.execute("SELECT beatmap_id, max_pp FROM pp_cache")
    return {row["beatmap_id"]: row["max_pp"] for row in cur.fetchall()}


def _fetch_and_compute(beatmap_id: int, mode_int: int) -> tuple[int, float]:
    """Fetch .osu file and compute PP. Designed for thread-pool use."""
    pp = compute_max_pp(beatmap_id, mode_int)
    return beatmap_id, pp


def filter_by_pp(
    candidates: list[BeatmapsetHit],
    pp_min: float | None,
    pp_max: float | None,
    registry: Registry,
    target_count: int | None = None,
) -> list[BeatmapsetHit]:
    """Filter beatmapset candidates by PP range.

    For each candidate, computes PP for its matching beatmaps (difficulties).
    A set passes if at least one difficulty has PP in [pp_min, pp_max].

    Optimisations over the naive approach:
    - Early termination: stops once target_count sets have passed.
    - Parallel fetch: .osu downloads + PP calc run in a thread pool.
    - Per-set short-circuit: skips remaining diffs once one passes.
    """
    if pp_min is None and pp_max is None:
        return candidates

    pp_cache = _load_pp_cache(registry)
    passed: list[BeatmapsetHit] = []
    total = len(candidates)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for batch_start in range(0, total, BATCH_SIZE):
            if target_count is not None and len(passed) >= target_count:
                break

            batch = candidates[batch_start:batch_start + BATCH_SIZE]

            # Phase 1: resolve sets from cache; collect uncached beatmaps
            resolved: dict[int, list[BeatmapInfo] | None] = {}
            futures: dict = {}

            for bi, hit in enumerate(batch):
                passed_from_cache = False
                uncached = []

                for bm in hit.beatmaps:
                    if bm.id in pp_cache:
                        if _pp_in_range(pp_cache[bm.id], pp_min, pp_max):
                            resolved[bi] = [bm]
                            passed_from_cache = True
                            break
                    else:
                        uncached.append(bm)

                if passed_from_cache:
                    continue

                all_cached = len(uncached) == 0
                if all_cached:
                    resolved[bi] = None
                else:
                    for bm in uncached:
                        fut = pool.submit(_fetch_and_compute, bm.id, bm.mode_int)
                        futures[fut] = (bi, bm)

            # Phase 2: collect parallel results
            for fut in as_completed(futures):
                bi, bm = futures[fut]
                try:
                    bm_id, pp = fut.result()
                    pp_cache[bm_id] = pp
                    registry.cache_pp(bm_id, pp)
                except Exception:
                    pass

            # Phase 3: evaluate each set in the batch
            for bi, hit in enumerate(batch):
                idx = batch_start + bi + 1

                if bi in resolved:
                    matching = resolved[bi]
                    if matching is not None:
                        bm = matching[0]
                        pp_val = pp_cache[bm.id]
                        print(f"  PP check {idx}/{total}: {hit.artist} - {hit.title}"
                              f" — {pp_val:.0f}pp PASS")
                        passed.append(BeatmapsetHit(
                            id=hit.id, artist=hit.artist, title=hit.title,
                            stars=bm.difficulty_rating, beatmaps=matching,
                        ))
                    else:
                        best = max((pp_cache.get(bm.id, 0) for bm in hit.beatmaps),
                                   default=0)
                        print(f"  PP check {idx}/{total}: {hit.artist} - {hit.title}"
                              f" — {best:.0f}pp skip")
                else:
                    # Was unresolved — check fetched results
                    first_match = None
                    for bm in hit.beatmaps:
                        pp = pp_cache.get(bm.id)
                        if pp is not None and _pp_in_range(pp, pp_min, pp_max):
                            first_match = bm
                            break

                    if first_match is not None:
                        pp_val = pp_cache[first_match.id]
                        print(f"  PP check {idx}/{total}: {hit.artist} - {hit.title}"
                              f" — {pp_val:.0f}pp PASS")
                        passed.append(BeatmapsetHit(
                            id=hit.id, artist=hit.artist, title=hit.title,
                            stars=first_match.difficulty_rating,
                            beatmaps=[first_match],
                        ))
                    else:
                        best = max((pp_cache.get(bm.id, 0) for bm in hit.beatmaps),
                                   default=0)
                        print(f"  PP check {idx}/{total}: {hit.artist} - {hit.title}"
                              f" — {best:.0f}pp skip")

    if target_count is not None:
        passed = passed[:target_count]

    checked = min(batch_start + BATCH_SIZE, total) if total > 0 else 0
    if target_count is not None and checked < total:
        print(f"  (early termination: {len(passed)} passed after checking"
              f" {checked}/{total} sets)")

    return passed
