"""Where a film goes: its shape, the part of the screen the app leaves alone, and how long it may run.

Every safe zone here is an estimate. Apps move their buttons, the numbers change without notice,
and the only published figures are for ads. Each entry says which source it rests on, and a figure
nobody publishes says so. `rules.json` can override a zone for one project.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import AltocueError
from .project import Project

BT1848 = "https://www.itu.int/rec/R-REC-BT.1848"
PLAIN_MARGIN = {"left": 0.05, "right": 0.95, "top": 0.05, "bottom": 0.95}


@dataclass(frozen=True)
class Platform:
    name: str
    aspect: str
    zone: dict[str, float]
    zone_source: str
    longest: float | None
    shortest: float | None
    length_source: str
    loudness: str = "social"


PLATFORMS = {
    "reels": Platform(
        "reels",
        "9:16",
        {"left": 0.06, "right": 0.94, "top": 0.14, "bottom": 0.65},
        "Estimate. Meta's Reels ad guide asks for 14% top, 35% bottom and 6% each side clear: "
        "https://www.facebook.com/business/ads-guide/update/video/instagram-reels. That is ad guidance. Meta publishes none for organic Reels.",
        180.0,
        None,
        "Instagram's creators blog: reels up to 3 minutes in the Reels editor. https://creators.instagram.com/blog/the-latest-with-instagram",
    ),
    "tiktok": Platform(
        "tiktok",
        "9:16",
        {"left": 120 / 1080, "right": 900 / 1080, "top": 240 / 1920, "bottom": 1260 / 1920},
        "Estimate, no primary source read. TikTok publishes its zones as template files on "
        "https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads. These are Socialync's reading of that file, scaled to 1080x1920: "
        "https://www.socialync.io/tools/safe-zones/tiktok. They are for in-feed ads.",
        600.0,
        None,
        "TikTok's Content Posting API docs: all creators can post 3 minutes, some 5 or 10, and 10 is the most the API takes. "
        "https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide",
    ),
    "shorts": Platform(
        "shorts",
        "9:16",
        {"left": 48 / 1080, "right": 888 / 1080, "top": 288 / 1920, "bottom": 1248 / 1920},
        "Estimate. Google's universal safe zone for vertical YouTube video ads: 48 px left, 192 px right, 288 px top, 672 px bottom on "
        "1080x1920. https://support.google.com/google-ads/answer/13547298. That is ad guidance. YouTube publishes none for organic Shorts.",
        180.0,
        None,
        "YouTube Help: Shorts up to three minutes. https://support.google.com/youtube/answer/15424877",
    ),
    "youtube": Platform(
        "youtube",
        "16:9",
        PLAIN_MARGIN,
        f"Estimate. ITU-R BT.1848 sets a 5% graphics-safe margin for 16:9 pictures: {BT1848}. YouTube publishes no zone for organic video.",
        43200.0,
        None,
        "YouTube Help: 256 GB or 12 hours, whichever is less. An unverified account stops at 15 minutes. https://support.google.com/youtube/answer/71673",
    ),
    "linkedin": Platform(
        "linkedin",
        "4:5",
        PLAIN_MARGIN,
        f"Estimate, no primary source. LinkedIn publishes no safe zone, so this borrows the 5% graphics-safe margin of ITU-R BT.1848: {BT1848}.",
        900.0,
        3.0,
        "LinkedIn Help, video file requirements: at most 15 minutes, at least 3 seconds from a desktop. https://www.linkedin.com/help/linkedin/answer/a548372",
    ),
    "square": Platform(
        "square",
        "1:1",
        PLAIN_MARGIN,
        f"Estimate, no primary source. A square file is not tied to one app, so this borrows the 5% graphics-safe margin of ITU-R BT.1848: {BT1848}.",
        None,
        None,
        "No limit checked. A square file is not tied to one app.",
    ),
}


def find(name: str) -> Platform:
    if name not in PLATFORMS:
        raise AltocueError(f"Unknown platform {name}.", fix=f"Use {', '.join(PLATFORMS)}.")
    return PLATFORMS[name]


def parse(text: str) -> list[Platform]:
    """`reels,tiktok` as platforms, in the order given and without repeats."""
    return [find(name) for name in dict.fromkeys(part.strip() for part in text.split(",") if part.strip())]


def zone_of(project: Project) -> dict[str, float] | None:
    """The safe zone of the platform this project is cut for, if it names one under `[output]`."""
    name = project.setting("output", "platform")
    return find(str(name)).zone if name else None
