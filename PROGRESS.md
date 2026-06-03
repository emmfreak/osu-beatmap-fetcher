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
