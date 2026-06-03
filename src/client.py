"""osu! API v2 client: OAuth client-credentials auth + beatmapset search.

Wraps `ossapi` (https://github.com/circleguy/ossapi). Auth uses the
client-credentials grant, which is fine for public search endpoints but
canNOT download .osz files (the official download endpoint is lazer-only
and returns 403 for normal OAuth apps — that's why download.py uses a
mirror). See CLAUDE.md.
"""

from dataclasses import dataclass

from ossapi import Ossapi, BeatmapsetSearchMode, BeatmapsetSearchCategory

from .config import load_config


@dataclass
class BeatmapsetHit:
    """A minimal search result row."""
    id: int
    artist: str
    title: str
    stars: float  # max difficulty_rating among the set's beatmaps in range


# Map a short mode string to the ossapi enum.
_MODE_MAP = {
    "mania": BeatmapsetSearchMode.MANIA,
    "osu": BeatmapsetSearchMode.OSU,
    "taiko": BeatmapsetSearchMode.TAIKO,
    "catch": BeatmapsetSearchMode.CATCH,
    "any": BeatmapsetSearchMode.ANY,
}


class OsuClient:
    def __init__(self):
        creds = load_config()
        # client-credentials grant (no Grant arg needed; ossapi infers it
        # when no redirect_uri is supplied).
        self.api = Ossapi(int(creds["client_id"]), creds["client_secret"])

    def search_beatmapsets(
        self,
        star_min: float,
        star_max: float,
        count: int,
        mode: str = "mania",
        category: str = "ranked",
    ) -> list[BeatmapsetHit]:
        """Return up to `count` beatmapset hits matching the star range.

        Star filtering is done via osu!web's text-query operators
        (`star>=x star<=y`), then verified client-side.
        """
        search_mode = _MODE_MAP.get(mode.lower(), BeatmapsetSearchMode.MANIA)
        search_category = getattr(
            BeatmapsetSearchCategory, category.upper(), BeatmapsetSearchCategory.RANKED
        )
        query = f"star>={star_min} star<={star_max}"

        hits: list[BeatmapsetHit] = []
        seen: set[int] = set()
        cursor = None

        # Paginate until we have enough hits or the mirror runs out of pages.
        for _ in range(20):  # hard cap on pages, just in case
            result = self.api.search_beatmapsets(
                query,
                mode=search_mode,
                category=search_category,
                cursor=cursor,
            )

            for bset in result.beatmapsets:
                if bset.id in seen:
                    continue

                # Pick the in-range difficulty rating for display/recording.
                ratings = [
                    bm.difficulty_rating
                    for bm in (bset.beatmaps or [])
                    if star_min <= bm.difficulty_rating <= star_max
                ]
                if not ratings:
                    # No difficulty within the requested band; skip the set.
                    continue

                seen.add(bset.id)
                hits.append(
                    BeatmapsetHit(
                        id=bset.id,
                        artist=bset.artist,
                        title=bset.title,
                        stars=max(ratings),
                    )
                )
                if len(hits) >= count:
                    return hits

            cursor = result.cursor
            if cursor is None:
                break

        return hits
