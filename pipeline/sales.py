"""Where a car was offered: the sale it was in, a link to that sale, and the country.

Every auction lot on the site shows its house's sale by name, linked to the sale's own
page, and the country the sale took place in (for online auctions: where the car is).

  RM Sotheby's     name from the lot, page /auctions/<code>/ from the lot link, country code
  Bonhams          name, venue and country from Bonhams' sales index (kept in kv by the adapter)
  Broad Arrow      name from the lot, page and place from its auction list (kv)
  Gooding          name from the lot, page matched in its sitemap (kv), country from the venue
  Mecum            name from the lot, page /auctions/<slug>/, always the United States
  BaT, Cars & Bids, PCARMARKET, Sotheby's Motorsport, duPont Registry Live
                   online auctions: the lot's own page, country from where the car is

Adapters keep the lookups in the kv table so that results stored before this existed
are described too. prime(con) loads them once per export.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Optional

COUNTRIES = {
    "US": "United States", "USA": "United States", "CA": "Canada", "MX": "Mexico", "GB": "United Kingdom",
    "UK": "United Kingdom", "IE": "Ireland", "FR": "France", "DE": "Germany", "IT": "Italy", "CH": "Switzerland",
    "MC": "Monaco", "BE": "Belgium", "NL": "Netherlands", "LU": "Luxembourg", "AT": "Austria", "ES": "Spain",
    "PT": "Portugal", "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland", "PL": "Poland",
    "CZ": "Czech Republic", "HU": "Hungary", "GR": "Greece", "LI": "Liechtenstein", "AE": "United Arab Emirates",
    "SA": "Saudi Arabia", "QA": "Qatar", "KW": "Kuwait", "BH": "Bahrain", "JP": "Japan", "HK": "Hong Kong",
    "SG": "Singapore", "CN": "China", "TW": "Taiwan", "KR": "South Korea", "AU": "Australia", "NZ": "New Zealand",
    "ZA": "South Africa", "BR": "Brazil", "AR": "Argentina",
}
_NAMES = {v.lower(): v for v in COUNTRIES.values()} | {"england": "United Kingdom", "scotland": "United Kingdom",
                                                       "uae": "United Arab Emirates", "usa": "United States",
                                                       "u.s.a.": "United States", "great britain": "United Kingdom"}
_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC "
              "ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
_PROVINCES = set("AB BC MB NB NL NS NT NU ON PE QC SK YT".split())
_US_STATE_NAMES = ("alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|"
                   "illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|"
                   "mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|"
                   "north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|"
                   "south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming")

ONLINE = {
    "bringatrailer": "Bring a Trailer online auction", "carsandbids": "Cars & Bids online auction",
    "pcarmarket": "PCARMARKET online auction", "somo": "Sotheby's Motorsport online auction",
    "dupont_live": "duPont Registry Live online auction",
}

# Gooding venues: the house names its sales after the place
_GOODING_PLACE = [("london", "United Kingdom"), ("geneva", "Switzerland"), ("paris", "France"),
                  ("geared online", None)]          # online: no venue; everything else is in the US

_CACHE: dict = {"bonhams": {}, "broadarrow": {}, "gooding": []}


def key(name: str) -> str:
    t = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\b(the|auctions?|sale)\b", " ", t)
    return re.sub(r"[^a-z0-9]+", "", t)


def slug(name: str) -> str:
    t = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def prime(con) -> None:
    """Load the lookups adapters keep in kv."""
    for k in ("bonhams", "broadarrow", "gooding"):
        row = con.execute("SELECT value FROM kv WHERE key=?", ("sales:" + k,)).fetchone() if con is not None else None
        try:
            _CACHE[k] = json.loads(row[0]) if row and row[0] else ({} if k != "gooding" else [])
        except ValueError:
            pass


def country_name(code: Optional[str] = None, text: Optional[str] = None) -> Optional[str]:
    """A country name from an ISO code, or from a location such as 'Abu Dhabi, United Arab Emirates',
    'Boynton Beach, FL', 'Clinton, WA 98236' or 'Knokke-Heist, Belgium'."""
    if code and code.strip().upper() in COUNTRIES:
        return COUNTRIES[code.strip().upper()]
    t = (text or "").strip()
    if not t:
        return None
    low = t.lower()
    for name, full in _NAMES.items():
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            return full
    tail = re.split(r",\s*", t)[-1].strip()
    abbr = re.match(r"([A-Z]{2})\b(?:\s+\d{5}(-\d{4})?)?$", tail)
    if abbr and abbr.group(1) in _STATES:
        return "United States"
    if abbr and abbr.group(1) in _PROVINCES:
        return "Canada"
    if re.search(r"\b(" + _US_STATE_NAMES + r")\b", low):
        return "United States"
    return None


def broadarrow_event(name: Optional[str]) -> dict:
    """Lot pages and the auction list spell sale names differently ('The Zürich Auction 2026' and
    'The Zurich Auction 2026', 'Global Icons: Spring Online 2026' and '... Online Auction 2026')."""
    k = key(name or "")
    if not k:
        return {}
    events = {key(v.get("name") or ""): v for v in (_CACHE["broadarrow"] or {}).values()}
    if k in events:
        return events[k]
    year = re.findall(r"\d{4}", k)
    close = [v for e, v in events.items() if (k in e or e in k) and re.findall(r"\d{4}", e) == year]
    return close[0] if len(close) == 1 else {}


def gooding_page(name: str, year: Optional[str], pages: list[str]) -> Optional[str]:
    """Gooding's sale pages are named inconsistently (amelia-island-auction-2025,
    amelia-island-auctions-2026, retromobile-paris-2026): match on the words and the year."""
    k = key(name)
    if not k:
        return None
    if year and year not in k:
        k += year
    hits = [p for p in pages if key(p.replace("-", " ")) == k]
    if not hits and year:
        hits = [p for p in pages if key(p.replace("-", " ")).replace(year, "") == k.replace(year, "") and year in p]
    return f"https://www.goodingco.com/auction/{hits[0]}" if len(hits) == 1 else None


def describe(r) -> dict:
    """r: a lots row (source, url, country, location, auction_house, ends_at, raw)."""
    src = r["source"]
    try:
        raw = json.loads(r["raw"] or "{}")
    except (ValueError, TypeError, IndexError, KeyError):
        raw = {}
    ex = raw.get("extra") or {}
    url, loc, code = r["url"] or "", r["location"], r["country"]
    year = (r["ends_at"] or "")[:4] or None
    out = {"sale": None, "sale_url": None, "country": None, "online": False}

    if src == "rmsothebys":
        m = re.match(r"^(https?://[^/]+)/auctions/([a-z0-9]+)/", url, re.I)
        private = "private" in (r["auction_house"] or "").lower()
        out["sale"] = "Private Sales" if private else ex.get("auction")
        out["sale_url"] = None if private or not m else f"https://rmsothebys.com/auctions/{m.group(2).lower()}/"
        out["country"] = country_name(code, loc)
    elif src == "bonhams":
        aid = str(ex.get("auction") or "")
        info = _CACHE["bonhams"].get(aid) or {}
        out["sale"] = info.get("title") or (f"Bonhams sale {aid}" if aid else None)
        sl = info.get("slug") or ex.get("auction_slug")
        out["sale_url"] = (f"https://cars.bonhams.com/auction/{aid}/{sl}/" if sl
                           else f"https://www.bonhams.com/auction/{aid}/" if aid else None)
        out["country"] = country_name(code or info.get("country"), info.get("venue") or loc)
        out["venue"] = info.get("venue")
    elif src == "broadarrow":
        name = ex.get("auction") or loc
        info = broadarrow_event(name)
        out["sale"] = name
        out["sale_url"] = info.get("url")
        out["online"] = "online" in (name or "").lower()
        out["country"] = country_name(code, info.get("place"))
    elif src == "gooding":
        name = ex.get("auction") or loc or ""
        shown = name if (not year or year in name or "online" in name.lower()) else f"{name} {year}"
        out["sale"] = shown or None
        out["sale_url"] = gooding_page(name, year, _CACHE["gooding"])
        low = name.lower()
        out["online"] = "online" in low
        place = next((c for w, c in _GOODING_PLACE if w in low), "United States")
        out["country"] = None if out["online"] else place
    elif src == "mecum":
        name = ex.get("auction") or loc
        out["sale"] = f"Mecum {name}" if name else None
        out["sale_url"] = f"https://www.mecum.com/auctions/{ex.get('auction_slug') or slug(name)}/" if name else None
        out["country"] = "United States"
    elif src in ONLINE:
        out["sale"] = ONLINE[src]
        out["sale_url"] = lot_url(r)
        out["online"] = True
        # duPont Registry Live sells only cars located in the United States
        out["country"] = country_name(code, loc) or ("United States" if src == "dupont_live" else None)
    else:
        out["sale"] = ex.get("auction")
        out["country"] = country_name(code, loc)
    return out


def lot_url(r) -> str:
    """Mecum stores lot links without its domain."""
    u = r["url"] or ""
    if u.startswith("/") and r["source"] == "mecum":
        return "https://www.mecum.com" + u
    return u
