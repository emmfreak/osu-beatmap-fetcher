"""CLI entry point for osu-beatmap-fetcher.

Example:
    python main.py --stars 4-5 --count 5 --download
    python main.py --mode mania --keys 7 --stars 4.5-5.5 --bpm 180+ --pp 600-900 --count 50 --download
    python main.py --mode mania --keys 4 -q "jumpstream" --stars 4-5 --count 30 --download
"""

import argparse
import sys

from src.client import OsuClient
from src.download import download_beatmapset, polite_delay, DownloadError
from src.registry import Registry
from src.search import sweep_search
from src.pp import filter_by_pp


def parse_star_range(s: str) -> tuple[float, float]:
    """Parse '4-5' (or '4.2-5.8') into (min, max)."""
    try:
        lo, hi = s.split("-", 1)
        lo_f, hi_f = float(lo), float(hi)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--stars must look like '4-5', got {s!r}"
        )
    if lo_f > hi_f:
        lo_f, hi_f = hi_f, lo_f
    return lo_f, hi_f


def parse_range_open(s: str) -> tuple[float | None, float | None]:
    """Parse 'X-Y', 'X+', or 'X' into (min, max). '+' means no upper bound."""
    s = s.strip()
    if "+" in s and "-" not in s:
        return float(s.replace("+", "")), None
    if "-" in s:
        lo, hi = s.split("-", 1)
        lo_v = float(lo) if lo else None
        hi_v = float(hi) if hi else None
        return lo_v, hi_v
    v = float(s)
    return v, v


def main(argv=None):
    parser = argparse.ArgumentParser(description="osu! beatmap bulk fetcher")
    parser.add_argument(
        "--stars", type=parse_star_range, required=True,
        help="Star-rating range, e.g. 4-5",
    )
    parser.add_argument(
        "--count", type=int, default=5,
        help="Number of (new) beatmapsets to fetch (default 5)",
    )
    parser.add_argument(
        "--mode", default="mania",
        help="Game mode: mania/osu/taiko/catch (default mania)",
    )
    parser.add_argument(
        "--status", default="ranked",
        help="Ranked status / category (default ranked)",
    )
    parser.add_argument(
        "--keys", type=int, default=None,
        help="Key count filter for mania (e.g. 4 or 7)",
    )
    parser.add_argument(
        "--bpm", type=str, default=None,
        help="BPM range: '180-220', '180+', or exact '180'",
    )
    parser.add_argument(
        "--length", type=str, default=None,
        help="Length range in seconds: '60-180', '120+', or exact '90'",
    )
    parser.add_argument(
        "--pp", type=str, default=None,
        help="Max (SS) PP range: '300-500', '400+'. Computed locally via rosu-pp-py.",
    )
    parser.add_argument(
        "-q", "--query", type=str, default=None,
        help="Free-text keyword search (e.g. 'jumpstream', 'chordjack', 'tech'). "
             "Matches tags, difficulty names, title, artist, creator. "
             "Note: finds maps labelled with the keyword, not every map of that type.",
    )
    parser.add_argument(
        "--download", action="store_true",
        help="Actually download .osz files (otherwise just search/list)",
    )
    args = parser.parse_args(argv)

    star_min, star_max = args.stars
    bpm_min = bpm_max = None
    if args.bpm:
        bpm_min, bpm_max = parse_range_open(args.bpm)

    length_min = length_max = None
    if args.length:
        lmin, lmax = parse_range_open(args.length)
        length_min = int(lmin) if lmin is not None else None
        length_max = int(lmax) if lmax is not None else None

    pp_min = pp_max = None
    if args.pp:
        pp_min, pp_max = parse_range_open(args.pp)

    filters = f"mode={args.mode} status={args.status} stars={star_min}-{star_max}"
    if args.keys:
        filters += f" keys={args.keys}"
    if bpm_min is not None or bpm_max is not None:
        filters += f" bpm={args.bpm}"
    if length_min is not None or length_max is not None:
        filters += f" length={args.length}"
    if pp_min is not None or pp_max is not None:
        filters += f" pp={args.pp}"
    if args.query:
        filters += f" q=\"{args.query}\""
    print(f"Searching: {filters} count={args.count}")

    client = OsuClient()
    registry = Registry()

    pool_multiplier = 3
    if pp_min is not None or pp_max is not None:
        pool_multiplier = 10

    hits = sweep_search(
        client=client,
        registry=registry,
        star_min=star_min,
        star_max=star_max,
        mode=args.mode,
        category=args.status,
        max_total=args.count * pool_multiplier,
        keys=args.keys,
        bpm_min=bpm_min,
        bpm_max=bpm_max,
        length_min=length_min,
        length_max=length_max,
        keyword=args.query,
    )
    print(f"Found {len(hits)} candidate beatmapsets (after sweep + metadata filters).")

    if pp_min is not None or pp_max is not None:
        print(f"Applying PP filter ({pp_min or '?'}-{pp_max or '?'} max SS PP)...")
        hits = filter_by_pp(hits, pp_min, pp_max, registry,
                            target_count=args.count)
        print(f"{len(hits)} beatmapsets passed PP filter.")

    downloaded = 0
    skipped = 0
    for hit in hits:
        if downloaded >= args.count:
            break

        if registry.is_downloaded(hit.id):
            print(f"  SKIP  {hit.id}  {hit.artist} - {hit.title} "
                  f"({hit.stars:.2f}*) [already downloaded]")
            skipped += 1
            continue

        label = f"{hit.id}  {hit.artist} - {hit.title} ({hit.stars:.2f}*)"
        if not args.download:
            print(f"  FOUND {label}")
            downloaded += 1
            continue

        try:
            path = download_beatmapset(hit.id)
        except DownloadError as e:
            print(f"  FAIL  {label}: {e}")
            continue

        registry.record(hit.id, stars=hit.stars,
                         artist=hit.artist, title=hit.title)
        size_kb = path.stat().st_size / 1024
        print(f"  GOT   {label} -> {path.name} ({size_kb:.0f} KB)")
        downloaded += 1
        polite_delay()

    action = "downloaded" if args.download else "listed"
    print(f"\nDone. {action} {downloaded}, skipped {skipped} dupes. "
          f"Registry now holds {registry.count()} maps.")
    registry.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
