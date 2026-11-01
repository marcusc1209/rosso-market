"""One photo per car, the way classic.com shows a thumbnail on every sale and listing.

Every source names its main photo differently. Verified 2026-11-01:

  Bring a Trailer   thumbnail_url
  Cars & Bids       main_photo {base_url: media.carsandbids.com, path}
  SOMO              vehicleData.files.titleImage {fullUrl, resizedImages{small, medium, large}}
  duPont Live       image
  RM Sotheby's      crop (cdn.rmsothebys.com webp)
  Gooding           cloudinaryImages[0].public_id on media.goodingco.com
  Bonhams           image.url (images1.bonhams.com/image?src=...)
  Broad Arrow       og:image on the lot page
  PCARMARKET        the <img> inside the search card (assets.basta.app)
  Ferrari Approved  cardImages.<provider>[0]
  Mecum             not visible from here: found by searching the hit's image like keys

Only the URL is stored. The site loads the picture from the source's own CDN and
links the card back to the source page, as classic.com does.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import quote

_IMG_EXT = re.compile(r"\.(jpe?g|png|webp|avif|gif)(\?|$)", re.I)
_IMG_KEY = re.compile(r"image|photo|thumb|picture|crop|hero|media", re.I)
_NOT_PHOTO = re.compile(r"logo|icon|flag|avatar|sprite|placeholder|\.svg", re.I)
_PREFERRED = ("medium", "large", "url", "fullUrl", "src", "href", "original", "small")


def _ok(url: Any) -> Optional[str]:
    if not isinstance(url, str):
        return None
    u = url.strip()
    if u.startswith("//"):
        u = "https:" + u
    if not u.startswith("http") or _NOT_PHOTO.search(u):
        return None
    return u


def first_image(v: Any, depth: int = 0) -> Optional[str]:
    """The first usable photo URL in a string, a list, or a dict such as
    {"url": ...}, {"base_url": ..., "path": ...} or {"resizedImages": {"medium": ...}}."""
    if depth > 4 or v is None:
        return None
    if isinstance(v, str):
        return _ok(v)
    if isinstance(v, list):
        for x in v:
            got = first_image(x, depth + 1)
            if got:
                return got
        return None
    if isinstance(v, dict):
        if v.get("base_url") and v.get("path"):
            base, path = str(v["base_url"]).strip("/"), str(v["path"]).lstrip("/")
            if "carsandbids" in base:
                return f"https://{base}/cdn-cgi/image/width=640,quality=70/{path}"
            return _ok(base if base.startswith("http") else f"https://{base}/{path}")
        if isinstance(v.get("resizedImages"), dict):
            got = first_image(v["resizedImages"], depth + 1)
            if got:
                return got
        for k in _PREFERRED:
            got = first_image(v.get(k), depth + 1)
            if got:
                return got
        for x in v.values():
            got = first_image(x, depth + 1)
            if got:
                return got
    return None


def find_image(obj: Any, depth: int = 0) -> Optional[str]:
    """Search a record for an image like key (image, photo, thumbnail ...) holding a photo URL.
    Used where the field name is not known in advance (Mecum, generic sites)."""
    if depth > 4 or not isinstance(obj, (dict, list)):
        return None
    items = obj.items() if isinstance(obj, dict) else enumerate(obj)
    for k, v in items:
        if isinstance(k, str) and _IMG_KEY.search(k):
            got = first_image(v)
            if got and (_IMG_EXT.search(got) or "image" in got or "photo" in got or "cdn" in got):
                return got
    for k, v in (obj.items() if isinstance(obj, dict) else enumerate(obj)):
        if isinstance(v, (dict, list)):
            got = find_image(v, depth + 1)
            if got:
                return got
    return None


def gooding(public_id: Optional[str], width: int = 640) -> Optional[str]:
    if not public_id:
        return None
    return f"https://media.goodingco.com/image/upload/c_fill,g_auto,q_80,w_{width}/v1/{quote(public_id)}"


def sized(url: Optional[str], **params) -> Optional[str]:
    """Ask image services that resize on the fly for a card sized copy."""
    if not url:
        return None
    sep = "&" if "?" in url else "?"
    return url + sep + "&".join(f"{k}={v}" for k, v in params.items())
