"""osu! API v2 client: OAuth client-credentials auth + beatmapset search.

Wraps `ossapi` (https://github.com/circleguy/ossapi). Auth uses the
client-credentials grant, which is fine for public search endpoints but
canNOT download .osz files (the official download endpoint is lazer-only
and returns 403 for normal OAuth apps — that's why download.py uses a
mirror). See CLAUDE.md.
"""

from dataclasses import dataclass, field

from ossapi import (
    Ossapi,
    BeatmapsetSearchMode,
    BeatmapsetSearchCategory,
    BeatmapsetSearchSort,
)

from .config import load_config


@dataclass
class BeatmapInfo:
    """A single difficulty within a beatmapset."""
    id: int
    difficulty_rating: float
    bpm: float
    total_length: int
    cs: float  # key count for mania
    mode_int: int


@dataclass
class BeatmapsetHit:
    """A search result row with per-difficulty metadata."""
    id: int
    artist: str
    title: str
    stars: float
    beatmaps: list[BeatmapInfo] = field(default_factory=list)
    cover_url: str = ""
    creator: str = ""


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
        sort: BeatmapsetSearchSort | None = None,
        exclude_ids: set[int] | None = None,
        keys: int | None = None,
        bpm_min: float | None = None,
        bpm_max: float | None = None,
        length_min: int | None = None,
        length_max: int | None = None,
        keyword: str | None = None,
    ) -> list[BeatmapsetHit]:
        """Return up to `count` beatmapset hits matching all filters.

        Filters are composed into the osu! search query string where possible
        (stars, BPM, length, keys, free-text keyword) and verified client-side.
        """
        search_mode = _MODE_MAP.get(mode.lower(), BeatmapsetSearchMode.MANIA)
        search_category = getattr(
            BeatmapsetSearchCategory, category.upper(), BeatmapsetSearchCategory.RANKED
        )

        parts = [f"star>={star_min}", f"star<={star_max}"]
        if keys is not None:
            parts.append(f"keys={keys}")
        if bpm_min is not None:
            parts.append(f"bpm>={bpm_min}")
        if bpm_max is not None:
            parts.append(f"bpm<={bpm_max}")
        if length_min is not None:
            parts.append(f"length>={length_min}")
        if length_max is not None:
            parts.append(f"length<={length_max}")
        if keyword:
            parts.append(keyword)
        query = " ".join(parts)

        exclude = exclude_ids or set()
        hits: list[BeatmapsetHit] = []
        seen: set[int] = set()
        cursor = None

        for _ in range(20):
            result = self.api.search_beatmapsets(
                query,
                mode=search_mode,
                category=search_category,
                cursor=cursor,
                sort=sort,
            )

            for bset in result.beatmapsets:
                if bset.id in seen or bset.id in exclude:
                    continue

                matching_bms = []
                for bm in (bset.beatmaps or []):
                    if not (star_min <= bm.difficulty_rating <= star_max):
                        continue
                    if keys is not None and mode.lower() == "mania":
                        if int(bm.cs) != keys:
                            continue
                    bm_bpm = bm.bpm if bm.bpm else (bset.bpm or 0)
                    if bpm_min is not None and bm_bpm < bpm_min:
                        continue
                    if bpm_max is not None and bm_bpm > bpm_max:
                        continue
                    if length_min is not None and bm.total_length < length_min:
                        continue
                    if length_max is not None and bm.total_length > length_max:
                        continue
                    matching_bms.append(BeatmapInfo(
                        id=bm.id,
                        difficulty_rating=bm.difficulty_rating,
                        bpm=bm_bpm,
                        total_length=bm.total_length,
                        cs=bm.cs,
                        mode_int=bm.mode_int if hasattr(bm, "mode_int") else bm.mode.value,
                    ))

                if not matching_bms:
                    continue

                cover_url = ""
                if hasattr(bset, "covers") and bset.covers:
                    cover_url = getattr(bset.covers, "list", "") or ""
                    if not cover_url:
                        cover_url = getattr(bset.covers, "card", "") or ""

                seen.add(bset.id)
                hits.append(
                    BeatmapsetHit(
                        id=bset.id,
                        artist=bset.artist,
                        title=bset.title,
                        stars=max(b.difficulty_rating for b in matching_bms),
                        beatmaps=matching_bms,
                        cover_url=cover_url,
                        creator=getattr(bset, "creator", ""),
                    )
                )
                if len(hits) >= count:
                    return hits

            cursor = result.cursor
            if cursor is None:
                break

        return hits
