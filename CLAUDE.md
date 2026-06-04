# osu-beatmap-fetcher

A desktop app for **bulk-downloading osu! beatmaps by filter** — built mainly
for osu!mania (4K/7K) but multi-mode. Core use case: *"give me N maps matching
tight criteria, download them,"* run repeatedly, **never re-grabbing maps I
already have.**

## Tech stack (settled — don't re-research unless something's actually broken)

- **API search:** [`ossapi`](https://github.com/circleguy/ossapi) (osu! API v2).
  Auth = OAuth **client-credentials** grant, with `client_id` / `client_secret`
  loaded from a local `config.json`.
- **Full `.osz` downloads:** mirror APIs — **catboy.best** (primary) /
  **nerinyan.moe** (fallback).
- **PP (slice 3):** `rosu-pp-py`.
- **GUI (slice 4):** PyQt6.

## ⚠️ The lazer-download caveat

The official osu! API endpoint `/beatmapsets/{id}/download` is
**lazer-client-only** — a normal OAuth app gets **403**. That's why `.osz`
downloads **must** go through a mirror (catboy/nerinyan), not `ossapi`. Auth via
client-credentials is fine for *search*, just not *download*.

## Architecture

```
src/
  config.py       # loads client_id/client_secret from config.json
  client.py       # osu! API v2 auth + beatmapset search (via ossapi)
  download.py     # .osz fetching via mirrors (catboy -> nerinyan)
  registry.py     # SQLite store of downloaded maps + dupe check + PP cache
  search.py       # sort-sweep + star-shard search engine (slice 2)
  pp.py           # PP calc via rosu-pp-py — max (SS, nomod) per difficulty
main.py           # CLI entry point
gui.py            # STUB — PyQt6 GUI (slice 4)
verify_sweep.py   # before/after comparison script for search engine
config.json       # REAL credentials — gitignored, never commit
config.json.template
registry.db       # SQLite registry — gitignored
.osu_cache/       # cached .osu files for PP calc — gitignored
downloads/        # .osz output — gitignored
```

## Search engine design (slice 2)

The search engine in `search.py` solves the "ran dry" problem where the osu!
API caps pagination depth per sort order. Two techniques combined:

- **Sort-sweep:** Each search runs across 4 sort orders (`plays_desc`,
  `ranked_desc`, `difficulty_desc`, `favourites_desc`) and unions the results.
  Each sort surfaces a different slice of the matching pool.
- **Star-range sharding:** Narrow ranges (e.g. 4.3–4.6★) are split into
  ~0.2★ sub-ranges so each sub-range gets a full cursor window. Width adapts
  based on overall range span.

`client.py` accepts a `sort` parameter and `exclude_ids` for pre-filtering.
`main.py` calls `sweep_search()` which orchestrates shard→sweep→union→dedup.

## CLI usage

```bash
python main.py --stars 4-5 --count 5 --download
python main.py --mode mania --keys 7 --stars 4.5-5.5 --bpm 180+ --pp 600-900 --count 50 --download
python main.py --mode mania --keys 4 -q "jumpstream" --stars 4-5 --count 30 --download

# --stars     star-rating range, e.g. 4-5
# --count     number of NEW maps to fetch (dupes don't count)
# --mode      mania (default) / osu / taiko / catch
# --keys      key count for mania (4 or 7)
# --bpm       BPM range: '180-220', '180+', or exact '180'
# --length    length range in seconds: '60-180', '120+'
# --pp        max (SS, nomod) PP range: '300-500', '400+'
# -q/--query  free-text keyword (e.g. 'jumpstream', 'chordjack')
# --status    ranked (default) / loved / qualified / ...
# --download  actually download .osz; omit for dry-run listing
```

Setup: copy `config.json.template` → `config.json`, fill in osu! OAuth creds,
then `pip install ossapi requests rosu-pp-py`.

### Keyword search caveat

The `-q` flag uses the osu! API's free-text search (`q` parameter), matching
tags, difficulty names, title, artist, and creator. For mania skillset keywords
like "jumpstream", "chordjack", "tech" etc., this works reasonably well because
the community tags maps fairly often — but it only finds maps where a human
wrote that word into the metadata. It's not exhaustive and may have false
positives (e.g. "tech" matching a genre or artist name).

### PP filter details

PP values are **max (SS, nomod)** — the ceiling assuming 100% accuracy with no
mods. This matches what PP-farm sites show. PP is per-difficulty, not per-set;
the filter checks individual difficulties matching your key count and star range.
`.osu` files are fetched from `osu.ppy.sh` and cached locally (`.osu_cache/`).
Computed PP is cached in SQLite so each difficulty is calculated once.

## The 4-slice plan

1. **Slice 1 (DONE):** Prove the end-to-end pipeline — scaffold, config, minimal
   search, mirror download, SQLite registry with dedup, CLI. ✅
2. **Slice 2 (DONE):** Sort-sweep + star-shard search engine. Solves the
   "ran dry" problem by sweeping 4 sort orders and sharding narrow star
   ranges. Built in `search.py`, wired into `main.py`. ✅
3. **Slice 3 (DONE):** Full filters (keys, BPM, length), PP calc + filter via
   `rosu-pp-py`, keyword/pattern search via API `q` param. ✅
4. **Slice 4:** GUI — PyQt6 desktop app. `gui.py`.

See `PROGRESS.md` for current state.
