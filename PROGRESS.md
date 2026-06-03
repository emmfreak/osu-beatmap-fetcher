# Progress

## Slice 1 — prove the pipeline ✅ (complete)

### Delivered

- **Repo scaffold** matching the planned architecture (`src/` package + `main.py`
  + `gui.py`).
- **`.gitignore`** excluding the real `config.json`, `downloads/`, `songs/`, and
  `*.db`. **`config.json.template`** committed as the credential shape.
- **`src/config.py`** — loads `client_id`/`client_secret` from `config.json`,
  with clear errors if missing.
- **`src/client.py`** — `OsuClient` authenticates via OAuth client-credentials
  (`ossapi`) and `search_beatmapsets()` does a mania / ranked / star-range search,
  paginating via cursor and verifying each set has a difficulty in range. Returns
  a list of `BeatmapsetHit(id, artist, title, stars)`.
- **`src/download.py`** — `download_beatmapset(id)` fetches the `.osz` from a
  mirror (catboy.best primary, nerinyan.moe fallback), saving to `downloads/`.
  Guards against HTML/JSON error pages and empty files; `polite_delay()` adds a
  2s gap between downloads.
- **`src/registry.py`** — SQLite `Registry` with `is_downloaded()`, `record()`,
  `count()`. Schema: `beatmapset_id` (PK), `artist`, `title`, `stars`,
  `downloaded_at`.
- **`main.py`** — CLI tying it together:
  `python main.py --stars 4-5 --count 5 --download`. Over-fetches candidates,
  skips dupes via the registry, downloads up to `--count` *new* maps.

### How it was verified (real run, 2026-06-03)

1. `python main.py --stars 4-5 --count 5 --download` →
   **5 `.osz` files** landed in `downloads/` and were recorded in `registry.db`.
2. **Second run, same args** → all 5 originals reported `SKIP ... [already
   downloaded]`, and 5 *different* new maps were fetched (registry grew 5 → 10).
   Confirms dedup works and `--count` counts only new maps.
3. Spot-checked downloaded `.osz` files: valid zip archives containing multiple
   `.osu` difficulty charts (e.g. `2217940.osz` = 17 files / 8 diffs).

### Stubbed (intentionally, for later slices)

- `src/search.py` — robust search engine (slice 2).
- `src/pp.py` — PP calculation via `rosu-pp-py` (slice 3).
- `gui.py` — PyQt6 GUI (slice 4).

### Notes / caveats

- `.osz` download goes through mirrors by necessity — the official endpoint is
  lazer-only (403 for OAuth apps). See CLAUDE.md.
- Star filtering uses osu!web text-query operators (`star>=x star<=y`) and is
  re-verified client-side per beatmapset.

---

## Slice 2 — sort-sweep + star-shard search engine ✅ (complete)

### Problem solved

The old single-sort search (slice 1) used only `plays_desc`, which exhausts
osu!'s pagination window quickly for narrow filters — the tool falsely reports
"no maps match" when plenty of qualifying maps exist deeper in the result set.

### Delivered

- **`src/search.py`** — `sweep_search()` orchestrates the full pipeline:
  1. **Star-range sharding:** splits the requested range into ~0.2★ sub-ranges
     (adaptive width, min 0.1★). Each sub-range gets its own full cursor window.
  2. **Sort-sweep:** for each shard, searches across 4 sort orders
     (`plays_desc`, `ranked_desc`, `difficulty_desc`, `favourites_desc`) and
     unions results by beatmapset ID.
  3. **Registry dedup:** loads all downloaded IDs from SQLite and excludes them
     from every API call and from the result pool.
  - Also includes `single_sort_search()` for baseline comparison.
  - `_compute_shards()` computes adaptive sub-ranges.

- **`src/client.py`** — `search_beatmapsets()` now accepts `sort`
  (`BeatmapsetSearchSort` enum) and `exclude_ids` (set of IDs to skip).

- **`main.py`** — wired to use `sweep_search()` instead of calling the client
  directly.

- **`verify_sweep.py`** — before/after comparison script: runs both
  `single_sort_search()` and `sweep_search()` on a narrow 4.3–4.6★ mania
  filter and prints unique-ID counts, overlap, and improvement percentage.

### Verification

Requires live osu! API credentials (`config.json`). Run:
```bash
python verify_sweep.py
```
Expected: sweep search finds significantly more unique beatmapsets than
single-sort, especially on narrow star ranges where a single sort exhausts
its pagination window.

### Stubbed (intentionally, for later slices)

- `src/pp.py` — PP calculation via `rosu-pp-py` (slice 3).
- `gui.py` — PyQt6 GUI (slice 4).
