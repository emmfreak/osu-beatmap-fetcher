"""Search engine with sort-sweep and star-range sharding.

Solves the "ran dry" problem: osu!'s search API caps how deep you can
paginate per sort order. A single sort (e.g. plays_desc) exhausts its
reachable window quickly for narrow filters. This engine:

1. Shards narrow star ranges into sub-ranges so each gets a full cursor window.
2. Sweeps multiple sort orders per shard and unions the results.
3. Dedupes against the SQLite registry so already-downloaded maps are skipped.
"""

from ossapi import BeatmapsetSearchSort

from .client import OsuClient, BeatmapsetHit
from .registry import Registry

SWEEP_SORTS = [
    BeatmapsetSearchSort.PLAYS_DESCENDING,
    BeatmapsetSearchSort.RANKED_DESCENDING,
    BeatmapsetSearchSort.DIFFICULTY_DESCENDING,
    BeatmapsetSearchSort.FAVORITES_DESCENDING,
]

SHARD_WIDTH_DEFAULT = 0.2
SHARD_WIDTH_MIN = 0.1
MAX_PER_SORT = 200


def _compute_shards(
    star_min: float, star_max: float
) -> list[tuple[float, float]]:
    """Split [star_min, star_max] into sub-ranges for independent searches."""
    span = star_max - star_min
    if span <= SHARD_WIDTH_DEFAULT:
        return [(star_min, star_max)]

    width = max(SHARD_WIDTH_MIN, min(SHARD_WIDTH_DEFAULT, span / 5))
    shards = []
    lo = star_min
    while lo < star_max - 1e-9:
        hi = min(lo + width, star_max)
        shards.append((round(lo, 2), round(hi, 2)))
        lo = hi
    return shards


def sweep_search(
    client: OsuClient,
    registry: Registry,
    star_min: float,
    star_max: float,
    mode: str = "mania",
    category: str = "ranked",
    max_total: int = 500,
    keys: int | None = None,
    bpm_min: float | None = None,
    bpm_max: float | None = None,
    length_min: int | None = None,
    length_max: int | None = None,
    keyword: str | None = None,
    on_progress=None,
) -> list[BeatmapsetHit]:
    """Run a full shard + sort-sweep search, returning deduplicated hits.

    Returns up to `max_total` unique BeatmapsetHit objects that are NOT
    already in the registry.

    `on_progress(sweeps_done, sweeps_total, found, target)` is called after each
    sort-sweep API call so callers can show progress / estimate time remaining.
    """
    registry_ids = _load_registry_ids(registry)
    shards = _compute_shards(star_min, star_max)
    sweeps_total = len(shards) * len(SWEEP_SORTS)
    sweeps_done = 0

    pool: dict[int, BeatmapsetHit] = {}

    for shard_min, shard_max in shards:
        for sort in SWEEP_SORTS:
            if len(pool) >= max_total:
                break

            exclude = registry_ids | set(pool.keys())
            remaining = max_total - len(pool)
            fetch_count = min(MAX_PER_SORT, remaining)

            hits = client.search_beatmapsets(
                star_min=shard_min,
                star_max=shard_max,
                count=fetch_count,
                mode=mode,
                category=category,
                sort=sort,
                exclude_ids=exclude,
                keys=keys,
                bpm_min=bpm_min,
                bpm_max=bpm_max,
                length_min=length_min,
                length_max=length_max,
                keyword=keyword,
            )
            for h in hits:
                if h.id not in pool and h.id not in registry_ids:
                    pool[h.id] = h

            sweeps_done += 1
            if on_progress:
                on_progress(sweeps_done, sweeps_total, len(pool), max_total)

        if len(pool) >= max_total:
            break

    return list(pool.values())


def single_sort_search(
    client: OsuClient,
    registry: Registry,
    star_min: float,
    star_max: float,
    mode: str = "mania",
    category: str = "ranked",
    max_total: int = 500,
) -> list[BeatmapsetHit]:
    """Baseline: single plays_desc search (no sweep, no sharding).

    Used for before/after comparison.
    """
    registry_ids = _load_registry_ids(registry)

    hits = client.search_beatmapsets(
        star_min=star_min,
        star_max=star_max,
        count=max_total,
        mode=mode,
        category=category,
        sort=BeatmapsetSearchSort.PLAYS_DESCENDING,
        exclude_ids=registry_ids,
    )
    return hits


def _load_registry_ids(registry: Registry) -> set[int]:
    cur = registry.conn.execute("SELECT beatmapset_id FROM downloads")
    return {row[0] for row in cur.fetchall()}
