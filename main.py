"""CLI entry point for osu-beatmap-fetcher (slice 1).

Example:
    python main.py --stars 4-5 --count 5 --download
"""

import argparse
import sys

from src.client import OsuClient
from src.download import download_beatmapset, polite_delay, DownloadError
from src.registry import Registry


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
        "--download", action="store_true",
        help="Actually download .osz files (otherwise just search/list)",
    )
    args = parser.parse_args(argv)

    star_min, star_max = args.stars
    print(
        f"Searching: mode={args.mode} status={args.status} "
        f"stars={star_min}-{star_max} count={args.count}"
    )

    client = OsuClient()
    registry = Registry()

    # Over-fetch a bit so that after skipping dupes we still have enough new
    # ones to download.
    hits = client.search_beatmapsets(
        star_min=star_min,
        star_max=star_max,
        count=args.count * 3,
        mode=args.mode,
        category=args.status,
    )
    print(f"Found {len(hits)} candidate beatmapsets.")

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
            downloaded += 1  # count toward --count for a dry run
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
