#!/usr/bin/env python3
"""Standalone mirror-availability diagnostic for osu! beatmap downloads.

This is a MANUAL debugging tool — nothing in the app imports it. When maps fail
to download through the app (which uses catboy + nerinyan), run this to see
whether the map is genuinely missing everywhere, or whether a mirror the app
doesn't currently use (osu.direct / sayobot) would have served it, or whether a
mirror is just rate-limiting / erroring transiently.

Usage:
    python diagnose_mirrors.py <beatmapset_id> [<beatmapset_id> ...]
    python diagnose_mirrors.py --from-log      # read ids from failed_downloads.log

For each id it probes EVERY mirror (it does not stop at the first success) and
reports HTTP status, Content-Type, byte size, and whether the body is a valid
.osz (ZIP). Bodies are streamed to a temp file, validated, then deleted — no
maps are kept on disk.
"""

import argparse
import re
import sys
import tempfile
import time
from pathlib import Path

import requests

# Import the app's own validator so "valid zip" means exactly what it means in
# the downloader — don't reimplement it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.download import is_valid_osz, FAILED_LOG_PATH

# (name, url template, is it a mirror the app currently uses?)
# Kept in sync with src.download.MIRRORS — the app now cascades all five, using
# each mirror's no-video convention. Add a candidate with in_app=False to test
# whether it would extend coverage before wiring it into the app.
MIRRORS = [
    ("osu.direct",  "https://osu.direct/api/d/{id}?noVideo=1",              True),
    ("nerinyan",    "https://api.nerinyan.moe/d/{id}?nv=1",                 True),
    ("sayobot",     "https://dl.sayobot.cn/beatmaps/download/novideo/{id}", True),
    ("beatconnect", "https://beatconnect.io/b/{id}",                        True),
    ("catboy",      "https://catboy.best/d/{id}",                           True),
]
APP_MIRRORS = {name for name, _, in_app in MIRRORS if in_app}
NEW_MIRRORS = {name for name, _, in_app in MIRRORS if not in_app}

USER_AGENT = "osu-beatmap-fetcher-diagnostic/1.0 (mirror availability check)"
REQUEST_TIMEOUT = 30            # seconds per request
POLITE_DELAY = 0.7             # seconds between requests — don't hammer mirrors
MAX_BYTES = 200 * 1024 * 1024  # runaway guard; real .osz files are far smaller

# Result kinds
OK, MISSING, TRANSIENT, JUNK = "OK", "MISSING", "TRANSIENT", "JUNK"


def _human_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


def probe(name: str, url_template: str, beatmapset_id: int) -> dict:
    """Probe one mirror for one id. Never raises — errors become the result."""
    url = url_template.format(id=beatmapset_id)
    try:
        resp = requests.get(
            url, headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT, stream=True,
        )
    except requests.Timeout:
        return dict(kind=TRANSIENT, status="timeout", ctype="-", size=0,
                    valid=False, note="request timed out")
    except requests.RequestException as e:
        return dict(kind=TRANSIENT, status="conn-error", ctype="-", size=0,
                    valid=False, note=type(e).__name__)

    try:
        status = resp.status_code
        ctype = resp.headers.get("Content-Type", "-")

        if status in (404, 410):
            return dict(kind=MISSING, status=status, ctype=ctype, size=0,
                        valid=False, note="not found on this mirror")
        if status == 429 or status in (500, 502, 503, 504):
            return dict(kind=TRANSIENT, status=status, ctype=ctype, size=0,
                        valid=False, note=f"server/rate-limit ({status})")
        if status != 200:
            return dict(kind=JUNK, status=status, ctype=ctype, size=0,
                        valid=False, note=f"unexpected status {status}")

        # 200: pull the body to a temp file so we can measure it and validate
        # the ZIP end-of-central-directory (which requires the whole file).
        size = 0
        truncated = False
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".osz", delete=False
            ) as tmp:
                tmp_path = Path(tmp.name)
                for chunk in resp.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > MAX_BYTES:
                        truncated = True
                        break
                    tmp.write(chunk)
            valid = (not truncated) and is_valid_osz(tmp_path)
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        low = ctype.lower()
        if valid:
            return dict(kind=OK, status=status, ctype=ctype, size=size,
                        valid=True, note="valid osz")
        if truncated:
            return dict(kind=JUNK, status=status, ctype=ctype, size=size,
                        valid=False, note=f"exceeds {MAX_BYTES // (1024*1024)}MB cap")
        if "json" in low or "html" in low or "text" in low:
            return dict(kind=JUNK, status=status, ctype=ctype, size=size,
                        valid=False, note=f"non-archive body ({ctype})")
        return dict(kind=JUNK, status=status, ctype=ctype, size=size,
                    valid=False, note="200 but not a valid zip")
    finally:
        resp.close()


def diagnose(beatmapset_id: int) -> dict:
    """Probe every mirror for one id, print a block, return per-mirror results."""
    print("=" * 64)
    print(f" Beatmapset #{beatmapset_id}")
    print("=" * 64)

    results: dict[str, dict] = {}
    for i, (name, template, _in_app) in enumerate(MIRRORS):
        if i:
            time.sleep(POLITE_DELAY)
        r = probe(name, template, beatmapset_id)
        results[name] = r
        valid_txt = "YES" if r["valid"] else "no "
        print(
            f"  {name:<11} status={str(r['status']):<11} "
            f"type={r['ctype'][:28]:<28} size={_human_size(r['size']):>9}  "
            f"valid={valid_txt}  -> {r['note']}"
        )

    ok = [n for n, r in results.items() if r["kind"] == OK]
    bad = [f"{n}({results[n]['kind'].lower()})"
           for n, r in results.items() if r["kind"] != OK]
    ok_txt = ", ".join(ok) if ok else "none"
    bad_txt = ", ".join(bad) if bad else "none"
    print(f"  verdict: valid on [{ok_txt}]; failed on [{bad_txt}]")
    print()
    return results


def ids_from_log(path: Path) -> list[int]:
    """Pull unique beatmapset ids (in first-seen order) out of the failure log."""
    path = Path(path)
    if not path.exists():
        print(f"No failure log found at {path}")
        return []
    ids: list[int] = []
    seen: set[int] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.search(r"id=(\d+)", line)
        if m:
            bid = int(m.group(1))
            if bid not in seen:
                seen.add(bid)
                ids.append(bid)
    print(f"Read {len(ids)} unique id(s) from {path}")
    return ids


def summarize(all_results: dict[int, dict]) -> None:
    n = len(all_results)
    available = redundant = fragile = dead = transient = 0

    for results in all_results.values():
        kinds = [r["kind"] for r in results.values()]
        ok_mirrors = {name for name, r in results.items() if r["kind"] == OK}
        if ok_mirrors:
            available += 1
            if len(ok_mirrors) >= 2:
                redundant += 1      # safe: survives a mirror going down
            else:
                fragile += 1        # only one mirror has it right now
        elif MISSING in kinds:
            # A mirror authoritatively 404'd and none served it — treat the map
            # as genuinely gone, even if another mirror is transiently down.
            dead += 1
        else:
            # Only transient errors / ambiguous junk, no authoritative "missing"
            # and no success — the map may reappear on a retry.
            transient += 1

    print("=" * 64)
    print(f" SUMMARY - {n} id(s) checked")
    print("=" * 64)
    print(f"  available on >=1 mirror ......................... {available}")
    print(f"    of those, on >=2 mirrors (outage-safe) ....... {redundant}")
    print(f"    on exactly 1 mirror (fragile) ................ {fragile}")
    print(f"  dead on every mirror (genuinely missing) ........ {dead}")
    print(f"  no success, transient/rate-limit signals")
    print(f"    (worth a retry later) ......................... {transient}")
    print("=" * 64)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe osu! beatmap mirrors for one or more beatmapset ids.",
    )
    parser.add_argument("ids", nargs="*", type=int,
                        help="beatmapset id(s) to diagnose")
    parser.add_argument("--from-log", action="store_true",
                        help="read ids from failed_downloads.log instead")
    args = parser.parse_args()

    if args.from_log:
        ids = ids_from_log(FAILED_LOG_PATH)
    else:
        ids = args.ids

    if not ids:
        parser.print_usage()
        print("\nGive one or more beatmapset ids, or --from-log.")
        return 1

    print(f"Probing {len(MIRRORS)} mirrors for {len(ids)} id(s). "
          f"App currently uses: {', '.join(sorted(APP_MIRRORS))}.\n")

    all_results: dict[int, dict] = {}
    for i, bid in enumerate(ids):
        if i:
            time.sleep(POLITE_DELAY)
        all_results[bid] = diagnose(bid)

    summarize(all_results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
