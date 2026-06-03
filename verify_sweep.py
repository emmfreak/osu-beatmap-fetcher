"""Verify that sweep search finds more unique maps than a single sort.

Compares single plays_desc search vs full sweep+shard on a narrow filter.
"""

from src.client import OsuClient
from src.registry import Registry
from src.search import sweep_search, single_sort_search, _compute_shards

STAR_MIN = 4.3
STAR_MAX = 4.6
MODE = "mania"
CATEGORY = "ranked"
MAX_TOTAL = 500

def main():
    client = OsuClient()
    registry = Registry()

    print(f"Filter: mode={MODE} status={CATEGORY} stars={STAR_MIN}-{STAR_MAX}")
    print(f"Max results per method: {MAX_TOTAL}\n")

    shards = _compute_shards(STAR_MIN, STAR_MAX)
    print(f"Shards computed: {shards}\n")

    print("--- Single sort (plays_desc only, no sharding) ---")
    single = single_sort_search(
        client, registry, STAR_MIN, STAR_MAX,
        mode=MODE, category=CATEGORY, max_total=MAX_TOTAL,
    )
    single_ids = {h.id for h in single}
    print(f"Result: {len(single)} unique beatmapsets\n")

    print("--- Sweep search (4 sorts x shards, union + dedup) ---")
    swept = sweep_search(
        client, registry, STAR_MIN, STAR_MAX,
        mode=MODE, category=CATEGORY, max_total=MAX_TOTAL,
    )
    swept_ids = {h.id for h in swept}
    print(f"Result: {len(swept)} unique beatmapsets\n")

    only_in_sweep = swept_ids - single_ids
    overlap = swept_ids & single_ids
    print("--- Comparison ---")
    print(f"Single-sort found:   {len(single_ids)}")
    print(f"Sweep found:         {len(swept_ids)}")
    print(f"Overlap:             {len(overlap)}")
    print(f"Only in sweep:       {len(only_in_sweep)}")
    improvement = (
        ((len(swept_ids) - len(single_ids)) / len(single_ids) * 100)
        if single_ids else 0
    )
    print(f"Improvement:         {improvement:+.1f}%")


if __name__ == "__main__":
    main()
