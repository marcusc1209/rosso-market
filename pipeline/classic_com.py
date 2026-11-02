"""Listing dates from CLASSIC.COM for cars no listing site dates.

Ferrari Approved and a few duPont Registry cars publish no listing date. CLASSIC.COM follows
dealer inventory across the web and shows, for each car it tracks, the day the listing was
posted ("Date this listing was posted"). Its search page takes a VIN and answers with that car's
card: status, price, dealer and that date, all in the page's HTML.

    /search/?q=ZFF80AMA9J0228738  ->  Ferrari of Naples ... Pending Sale ... Oct 3, 2026

Only a card for the same VIN that is for sale now (For Sale or Pending Sale) counts: an ended
listing ("Last Asking", "Sold") is an earlier time on the market, not this one. Verified
2026-11-02: robots.txt allows /search/ and /veh/ pages; about half the undated cars are there.

Each VIN is asked once (kv classic:dates); one CLASSIC.COM did not know is asked again after
RETRY_DAYS, since it picks up new dealer listings within days. A bot check stops the step for
the run: it is never worked around.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Optional

from bs4 import BeautifulSoup

from . import db
from .fetch import Blocked, Fetcher
from .records import LISTING

log = logging.getLogger("classic_com")

SEARCH = "https://www.classic.com/search/?q={vin}"
KEY = "classic:dates"
RETRY_DAYS = 7
_DATE = re.compile(r"\b([A-Z][a-z]{2}) (\d{1,2}), (\d{4})\b")
_ACTIVE = re.compile(r"\b(For Sale|Pending Sale)\b", re.I)


def _day(m) -> Optional[str]:
    try:
        return datetime.strptime(" ".join(m.groups()), "%b %d %Y").date().isoformat()
    except ValueError:
        return None


def parse(html: str, vin: str, today: date) -> Optional[str]:
    """Posted date of the car's current listing on a CLASSIC.COM search page, if it has one."""
    soup = BeautifulSoup(html or "", "html.parser")
    v = vin.lower()
    days = []
    for a in soup.select('a[href*="/veh/"]'):
        if v not in a.get("href", "").lower():
            continue
        card = a
        for _ in range(10):                     # up to the card: the first parent holding a date
            if card is None or _DATE.search(card.get_text(" ")):
                break
            card = card.parent
        if card is None:
            continue
        text = " ".join(card.get_text(" ").split())
        if not _ACTIVE.search(text):
            continue                            # an ended listing: an earlier time on the market
        d = next((_day(m) for m in _DATE.finditer(text)), None)
        if d and "2000-01-01" < d <= today.isoformat():
            days.append(d)
    return min(days) if days else None


def known(con) -> dict:
    raw = db.kv_get(con, KEY) if con is not None else None
    return json.loads(raw) if raw else {}


def apply(rec, dates: dict) -> None:
    """Give a listing without a date of its own the date CLASSIC.COM has for its VIN."""
    if rec.kind != LISTING or not rec.vin or (rec.extra or {}).get("listed_on"):
        return
    hit = dates.get(rec.vin.upper())
    if hit and hit[0]:
        rec.extra.update(listed_on=hit[0], listed_how="classic.com")


def undated_vins(con) -> list[str]:
    """VINs of cars for sale that no site has dated."""
    rows = con.execute("""SELECT vehicle_key, MAX(CASE WHEN json_valid(raw) THEN json_extract(raw, '$.extra.listed_on') END)
                          FROM sightings WHERE removed_on IS NULL AND vehicle_key LIKE 'vin:%'
                          GROUP BY vehicle_key""").fetchall()
    return sorted(k[4:] for k, d in rows if not d)


def run(con, cfg: dict, raw_dir, today: date) -> int:
    """Look up undated cars; returns how many got a date."""
    scfg = cfg.get("classic_com") or {}
    if not scfg.get("enabled"):
        return 0
    dates = known(con)
    todo = []
    for vin in undated_vins(con):
        hit = dates.get(vin)
        if hit is None or (not hit[0] and (today - date.fromisoformat(hit[1])).days >= RETRY_DAYS):
            todo.append(vin)
    fetcher = Fetcher({**cfg.get("settings", {}), **(scfg.get("fetch") or {})}, raw_dir, "classic_com")
    found = asked = 0
    try:
        for vin in todo[: int(scfg.get("lookups", 200))]:
            try:
                html = fetcher.get(SEARCH.format(vin=vin)).text
            except Blocked as e:
                log.info("CLASSIC.COM refused (%s): the rest wait for the next run", e)
                break
            except Exception as e:
                log.info("CLASSIC.COM lookup failed for %s (%s)", vin, e)
                continue
            asked += 1
            day = parse(html, vin, today)
            dates[vin] = [day or "", today.isoformat()]
            if day:
                found += 1
                con.execute("""UPDATE sightings SET raw = json_set(raw, '$.extra.listed_on', ?, '$.extra.listed_how', 'classic.com')
                               WHERE vehicle_key = ? AND json_valid(raw)
                               AND json_extract(raw, '$.extra.listed_on') IS NULL""", (day, "vin:" + vin))
            if asked % 25 == 0:
                db.kv_set(con, KEY, json.dumps(dates))      # keep progress if the run is cut short
    finally:
        db.kv_set(con, KEY, json.dumps(dates))
        con.commit()
        fetcher.close()
    log.info("CLASSIC.COM: %d undated cars, %d looked up, %d dated", len(todo), asked, found)
    return found
