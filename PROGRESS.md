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

- `gui.py` — PyQt6 GUI (slice 4).

---

## Slice 3 — full filters + PP + keyword search ✅ (complete)

### Problem solved

Slice 2 only filtered by star rating and mode. Users need to narrow results by
key count, BPM, length, PP range, and free-text keywords (mania pattern types
like "jumpstream", "chordjack", "tech").

### Delivered

- **Full metadata filters** in `src/client.py` and `src/search.py`:
  - `--keys` — key count filter for mania (e.g. 4 or 7), filters by `cs` field
  - `--bpm` — BPM range filter (e.g. `180-220`, `180+`)
  - `--length` — length range in seconds (e.g. `60-180`, `120+`)
  - All filters compose with each other and flow through the slice-2
    sweep/shard engine. They're applied both server-side (via osu! search
    query operators) and client-side (per-beatmap verification).

- **`src/pp.py`** — PP calculation via `rosu-pp-py`:
  - Computes **max (SS, nomod) PP** — the ceiling assuming 100% accuracy, no
    mods. This is the same metric PP-farm sites use.
  - PP is **per-difficulty**, not per-set. The filter checks individual beatmaps
    matching the user's key count and star range.
  - **Staged filtering** for performance: cheap metadata filters (stars, BPM,
    length, keys) run first; `.osu` fetch + PP calc only runs on survivors.
  - `.osu` files fetched from `https://osu.ppy.sh/osu/{beatmap_id}` (no auth)
    and cached in `.osu_cache/`.
  - PP values cached in SQLite `pp_cache` table keyed by beatmap ID — each
    difficulty is only ever calculated once across runs.
  - Mania: native maps need no conversion; converts use
    `beatmap.convert(rosu.GameMode.Mania)`. Max combo is irrelevant for mania
    PP (accuracy/notecount-driven).

- **Keyword / pattern search** (`-q` / `--query`):
  - Wires the osu! API `q` parameter as a composable filter through the
    sweep/shard engine.
  - Matches tags, difficulty names, title, artist, creator — same as the
    osu! website search box.
  - Example: `-q "jumpstream" --keys 4 --stars 4-5` finds 4K jumpstream maps.

- **`src/registry.py`** — added `pp_cache` table with `get_cached_pp()` and
  `cache_pp()` methods. Schema auto-migrates on first run.

- **`src/client.py`** — `BeatmapsetHit` now carries `beatmaps: list[BeatmapInfo]`
  with per-difficulty metadata (id, difficulty_rating, bpm, total_length, cs,
  mode_int). `search_beatmapsets()` accepts all new filter params and applies
  them both in the query string and client-side.

- **`main.py`** — all new CLI args wired: `--keys`, `--bpm`, `--length`, `--pp`,
  `-q`/`--query`. Range args support `X-Y`, `X+`, and exact `X` formats.

### How it was verified

1. **Import + unit tests** — all modules import cleanly; registry PP cache
   round-trips correctly; CLI arg parser handles all range formats.
2. **PP computation** — fetched a real mania `.osu` file (beatmap 1355822) from
   `osu.ppy.sh`, cached it, computed max PP (75.36 PP). Also tested osu!std
   (beatmap 75 → 37.85 PP). Both succeeded.
3. **No live API credentials** in this environment, so full end-to-end search +
   download was not tested here. The user should verify with:
   ```bash
   python main.py --mode mania --keys 4 -q "jumpstream" --stars 4-5 --count 5
   python main.py --mode mania --keys 7 --stars 4.5-5.5 --bpm 180+ --pp 200-400 --count 10 --download
   ```

### Keyword search caveat

osu! has no structured "pattern type" field. The `-q` keyword search only finds
maps where a human wrote that word into the tags / difficulty name / metadata.
For mania it works reasonably well (the community tags skillsets fairly often),
but it's not exhaustive and will occasionally return false positives (e.g.
"tech" matching a genre or artist). It finds maps *labelled* "jumpstream", not
every map that *is* jumpstream.

Future: true pattern detection would mean analysing note data in the `.osu` files
(which are already being fetched for PP). The search layer is modular enough that
a future `classifier.py` could feed candidate IDs into the same pipeline.

### Stubbed (intentionally, for later slices)

- `gui.py` — PyQt6 GUI (slice 4).

---

## PP-filter performance fix ✅ (complete)

### Problem solved

With `--pp` and `--count 50`, the filter computed PP for the entire candidate
pool (~500 maps) sequentially, each requiring a `.osu` fetch (~1-2s). Total
wall time: 10-20 minutes. An identical rerun was fast (SQLite cache), but the
first run was painful.

### Delivered

Three optimisations in `src/pp.py`:

1. **Early termination:** `filter_by_pp` now accepts `target_count` and stops
   processing batches once that many sets have passed. `main.py` passes
   `args.count` as the target. A count=50 query stops after ~50-80 sets
   instead of checking all 500.

2. **Parallel `.osu` fetch + PP calc:** uses `concurrent.futures.ThreadPoolExecutor`
   with 8 workers, processing candidates in batches of 8 sets. Cached diffs
   resolve instantly (no thread pool needed); only uncached diffs hit the
   network. Effective concurrency is capped at 8 connections — polite to
   `osu.ppy.sh`. Per-fetch delay reduced from 0.3s to 0.15s.

3. **Per-set short-circuit:** once any difficulty in a set passes the PP range,
   remaining diffs for that set are skipped. Cache-resolved sets bypass the
   thread pool entirely.

Also: each map's computed PP is now printed next to "PASS" / "skip" for
visibility (e.g. `— 342pp PASS`).

### Expected behaviour

- **First run** (`--pp 300-500 --count 50`): checks far fewer than 500 maps
  (early termination) and fetches `.osu` files ~8× faster (parallelism).
  Wall time drops from 10-20 min to 1-3 min depending on cache hit rate.
- **Identical rerun:** near-instant — all PP values served from SQLite cache,
  no network I/O.

### Files changed

- `src/pp.py` — rewrote `filter_by_pp` with all three optimisations; added
  `_pp_in_range`, `_load_pp_cache`, `_fetch_and_compute` helpers.
- `main.py` — passes `target_count=args.count` to `filter_by_pp`.
- `CLAUDE.md` — documented PP filter performance design.
