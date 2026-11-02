"""Listing dates for sites that publish none, from the Internet Archive's Wayback Machine.

The Wayback CDX API (web.archive.org/cdx/search/cdx) lists every capture the archive holds
for a URL or URL prefix, with its timestamp. Two kinds of evidence come from it:

  * Ferrari Approved ad addresses used to end in the car's VIN and the moment the ad was
    created, in epoch milliseconds:
        .../usa/boardwalk-ferrari/296-gtb/ZFF99SLA2R0300845-1724919183952   (Aug 29, 2024)
    so archived ads date each VIN's listing directly. Dealers' feeds re-create ads in bulk
    now and then (every Boardwalk car got a new ad on Oct 20, 2025), so a VIN can carry
    several of these; every one counts as evidence the car was listed then.
  * Any archived page: its first capture shows the listing existed by that day.

A car's listing date is the start of the most recent unbroken stretch of evidence, where
unbroken means no gap longer than GAP_DAYS, and today counts as evidence (the car is listed
now). A car archived in 2024 that sold and came back in 2026 is dated 2026, not 2024; a car
the archive has not seen for over GAP_DAYS gets no archive date at all.

Lookups go through the shared Fetcher (robots.txt, rate limits). Results are kept in the kv
table, so each dealer or page is asked about once. When the archive is down the lookup is
skipped and tried again on the next run.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

from . import db

log = logging.getLogger("wayback")

CDX = "https://web.archive.org/cdx/search/cdx"
GAP_DAYS = 180
_FA_AD = re.compile(r"/(Z(?:FF|SG)[A-HJ-NPR-Z0-9]{14})-(\d{13})(?:[/?#]|$)")


SPACING = 12.0                   # seconds between archive requests: the index server answers one
_last_call = [0.0]               # query and refuses (503) the next one a few seconds later


def _pace():
    gap = time.monotonic() - _last_call[0]
    if gap < SPACING:
        time.sleep(SPACING - gap)
    _last_call[0] = time.monotonic()


class Unavailable(Exception):
    """The archive did not answer (it is offline now and then): try again next run."""


def cdx(fetcher, url: str, match: str = "prefix", limit: int = 20000, collapse: str = "urlkey",
        **extra) -> list[tuple[str, str]]:
    """[(timestamp YYYYMMDDhhmmss, original url)]: by default one row per distinct URL, earliest capture."""
    q = urlencode({"url": url, "matchType": match, "output": "json", "fl": "timestamp,original",
                   "collapse": collapse, "limit": limit, **extra})
    try:
        rows = fetcher.get_json(f"{CDX}?{q}")
    except Exception as e:
        raise Unavailable(str(e)) from e
    if not isinstance(rows, list):
        raise Unavailable("unexpected answer")
    return [(r[0], r[1]) for r in rows[1:] if isinstance(r, list) and len(r) >= 2]


def _day(ts: str) -> Optional[str]:
    try:
        return datetime.strptime(ts[:8], "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def listing_start(evidence: list[str], today: date, gap_days: int = GAP_DAYS) -> Optional[str]:
    """Start of the latest unbroken stretch of dates that reaches today."""
    days = sorted({d for d in evidence if d and d <= today.isoformat()} | {today.isoformat()})
    start = days[-1]
    for d in reversed(days[:-1]):
        if (date.fromisoformat(start) - date.fromisoformat(d)).days > gap_days:
            break
        start = d
    return None if start == today.isoformat() else start


def fa_evidence(rows: list[tuple[str, str]]) -> dict[str, list[str]]:
    """VIN -> dates from archived Ferrari Approved ads: each ad's creation time (in its address)
    and the day it was captured."""
    out: dict[str, list[str]] = {}
    for ts, url in rows:
        m = _FA_AD.search(url)
        if not m:
            continue
        created = datetime.fromtimestamp(int(m.group(2)) / 1000, tz=timezone.utc).date().isoformat()
        out.setdefault(m.group(1), []).extend(d for d in (created, _day(ts)) if d)
    return out


class Lookups:
    """Per run budget and the kv cache shared by the adapters."""

    STRIKES = 3                  # failures in a row before the archive counts as down for this run
    PAUSE = 30.0                 # seconds to wait after a failure

    def __init__(self, con, fetcher, budget: int = 40):
        self.con, self.fetcher, self.budget, self.down = con, fetcher, budget, False
        self.failures = 0
        self.asked = self.answered = 0

    def _cached(self, key: str):
        v = db.kv_get(self.con, key) if self.con is not None else None
        return None if v is None else json.loads(v)

    def _ask(self, key: str, url: str, match: str):
        """Cached CDX rows for url, asking the archive only once ever per key."""
        hit = self._cached(key)
        if hit is not None:
            return hit
        if self.down or self.budget <= 0 or self.con is None:
            return None
        self.budget -= 1
        self.asked += 1
        _pace()
        try:
            rows = cdx(self.fetcher, url, match)
        except Unavailable as e:
            # the archive's index server sheds load with 503s now and then: skip this one,
            # pause, and go on; several failures in a row mean it is down for this run
            self.failures += 1
            if self.failures >= self.STRIKES:
                log.info("Wayback Machine unavailable (%s): archive dates wait for the next run", str(e)[:120])
                self.down = True
            else:
                time.sleep(self.PAUSE)
            return None
        self.failures = 0
        self.answered += 1
        db.kv_set(self.con, key, json.dumps(rows))
        return rows

    def ferrari_approved(self, country: str, dealer_slug: str, vin: Optional[str], today: date) -> Optional[str]:
        if not vin:
            return None
        prefix = f"preowned.ferrari.com/en-US/a/north-america/used-ferrari/{country}/{dealer_slug}/"
        rows = self._ask("wb:fa:" + prefix, prefix, "prefix")
        if not rows:
            return None
        return listing_start(fa_evidence(rows).get(vin.upper(), []), today)

    def captures(self, url: str, since: str = "2015") -> Optional[list[str]]:
        """Timestamps of good captures of one page, at most one a day (not cached here)."""
        if self.down or self.budget <= 0:
            return None
        self.budget -= 1
        self.asked += 1
        _pace()
        try:
            rows = cdx(self.fetcher, url, "exact", collapse="timestamp:8", filter="statuscode:200", **{"from": since})
        except Unavailable:
            self.failures += 1
            self.down = self.failures >= self.STRIKES
            return None
        self.failures = 0
        self.answered += 1
        return [ts for ts, _ in rows]

    def snapshot(self, ts: str, url: str) -> Optional[str]:
        """The archived page as it was captured (raw, without the archive's toolbar)."""
        if self.down or self.budget <= 0:
            return None
        self.budget -= 1
        self.asked += 1
        _pace()
        try:
            body = self.fetcher.get(f"https://web.archive.org/web/{ts}id_/{url}").text
        except Exception as e:
            self.failures += 1
            if self.failures >= self.STRIKES:
                log.info("Wayback Machine unavailable (%s): archive dates wait for the next run", str(e)[:120])
                self.down = True
            else:
                time.sleep(self.PAUSE)
            return None
        self.failures = 0
        self.answered += 1
        return body

    def first_capture(self, page_url: str) -> Optional[str]:
        """Day the archive first captured a page (its listing existed by then)."""
        u = re.sub(r"^https?://(www\.)?", "", page_url).split("?")[0]
        rows = self._ask("wb:page:" + u, u, "prefix")
        days = sorted(d for d in (_day(ts) for ts, _ in rows or []) if d)
        return days[0] if days else None
