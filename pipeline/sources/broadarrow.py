"""Broad Arrow Auctions (Hagerty).

Verified 2026-10-31 in a real browser:

* robots.txt disallows /vehicles/results (the search grid) and asks for a
  10 second crawl delay, so this adapter never touches the search pages.
* The sitemap named in robots.txt (/sitemaps/bagauction/sitemap.xml.gz) lists
  every lot page, ~370 of them Ferraris: /vehicles/<event code>_<lot>/<slug>.
* A lot page has `.vdp-header` with the title (h1), `.lot-and-branch` spans
  ("Lot 182 |", the auction name, sometimes the sale day) and a `.price-row`:
    "Sold Price:" + "$15,185,000"     sold, price with premium
    "$555,000"                        sold (older pages print the price alone)
    "Not Sold"                        unsold, no bid published
    "Estimate:" + "$6.5M - $8.5M"     upcoming (or, after the sale, no result published)
    "Sold After Auction", "Sold Before Auction", "Sold"
                                      no price published: skipped
  Prices come in $, EUR (3.015.625), GBP and CHF (75'000).
* The JSON-LD price on these pages is ten times the real figure, so it is never used.
* /past-auctions and /events list each auction's name with its dates
  ("5 - 14 October 2026"), which date the results.

Private sales live on a separate site (broadarrowprivatesales.com) and are never read.
Lots whose result is final, or that publish no price, are remembered so each
page is read once. `lot_pages` caps the pages read per run (10 s apart).
"""
from __future__ import annotations

import gzip
import json
import re
import unicodedata
from datetime import date, datetime
from typing import Iterator, Optional

from bs4 import BeautifulSoup

from .base import Source
from .. import db
from ..fetch import Blocked
from ..normalize import parse_price
from ..records import RawRecord, AUCTION

SITE = "https://www.broadarrowauctions.com"
SITEMAP = SITE + "/sitemaps/bagauction/sitemap.xml.gz"
_LOT = re.compile(r"/vehicles/([a-z]+\d+[a-z]*_[a-z]*\d+)/([^/?#]+)", re.I)
_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                        "september", "october", "november", "december"], 1)}
_DAY = re.compile(r"(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{4}))?")
NO_PRICE = ("sold after auction", "sold before auction", "withdrawn")


def key(name: str) -> str:
    """Lot pages and the auction list spell names differently: 'The Zürich Auction 2026' and
    'The Zurich Auction 2026', 'The Zoute Concours Auction 2025' and 'Zoute Concours Auction 2025',
    'Global Icons: Spring Online 2026' and 'Global Icons: Spring Online Auction 2026'."""
    t = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\b(the|auction)\b", " ", t)
    return re.sub(r"[^a-z0-9]+", "", t)


def find_event(events: dict, name: Optional[str]) -> Optional[tuple[str, str]]:
    k = key(name or "")
    if not k:
        return None
    if k in events:
        return events[k]
    year = re.findall(r"\d{4}", k)
    close = [v for e, v in events.items() if (k in e or e in k) and re.findall(r"\d{4}", e) == year]
    return close[0] if len(close) == 1 else None


def parse_event_dates(text: str) -> Optional[tuple[str, str]]:
    """'9 October 2026', '5 - 14 October 2026', '30 September - 2 October 2026' -> (start, end)."""
    parts = [(int(d), _MONTHS.get(m.lower()), int(y) if y else None) for d, m, y in _DAY.findall(text or "")]
    parts = [p for p in parts if p[1]]
    if not parts:
        m = re.match(r"\s*(\d{1,2})\s*[-–]\s*(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text or "")
        if not m or not _MONTHS.get(m.group(3).lower()):
            return None
        mo, y = _MONTHS[m.group(3).lower()], int(m.group(4))
        return date(y, mo, int(m.group(1))).isoformat(), date(y, mo, int(m.group(2))).isoformat()
    end_d, end_m, end_y = parts[-1]
    end_y = end_y or next((p[2] for p in reversed(parts) if p[2]), None)
    if end_y is None:
        return None
    lead = re.match(r"\s*(\d{1,2})\s*[-–]", text or "")
    start = date(end_y, end_m, int(lead.group(1))) if lead and len(parts) == 1 else \
        date(parts[0][2] or end_y, parts[0][1], parts[0][0])
    return start.isoformat(), date(end_y, end_m, end_d).isoformat()


def parse_events(html: str) -> dict[str, tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    for a in soup.select("a.content-col"):
        top, mid = a.select_one("h2.top"), a.select_one("h2.mid")
        if top and mid:
            dates = parse_event_dates(top.get_text(" ", strip=True))
            if dates:
                out[key(mid.get_text(" ", strip=True))] = dates
    return out


def lot_urls(body: bytes) -> list[str]:
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body.decode("utf-8", "replace"))
    return [u for u in urls if _LOT.search(u) and "ferrari" in u.lower()]


def parse_lot(html: str, url: str, events: dict, today: date, source: str = "broadarrow") -> tuple[Optional[RawRecord], str]:
    """(record or None, reason). reason is 'final', 'upcoming' or why nothing was kept."""
    soup = BeautifulSoup(html, "html.parser")
    head = soup.select_one(".vdp-header")
    m = _LOT.search(url)
    if head is None or m is None:
        return None, "no lot header"
    title = (head.select_one("h1") or head).get_text(" ", strip=True)
    spans = [s.get_text(" ", strip=True) for s in head.select(".lot-and-branch span")]
    spans = [s for s in spans if s]
    lot_no = next((re.sub(r"[^\d]", "", s) for s in spans if s.lower().startswith("lot")), None)
    event = next((s for s in spans if not s.lower().startswith("lot") and not re.search(r"\d{1,2} [A-Za-z]+ \d{4}$", s)), None)
    sale_day = next((s for s in spans if re.search(r"\d{1,2} [A-Za-z]+ \d{4}$", s)), None)
    dates = find_event(events, event)
    day = parse_event_dates(sale_day)[1] if sale_day and parse_event_dates(sale_day) else (dates[1] if dates else None)

    row = head.select_one(".price-row")
    bits = [s.get_text(" ", strip=True) for s in (row.find_all("span", recursive=False) if row else [])]
    bits = [b for b in bits if b]
    label = next((b for b in bits if b.endswith(":")), "").lower()
    value = next((b for b in bits if re.search(r"\d", b)), None)
    words = " ".join(bits).lower()

    status = price = est_lo = est_hi = None
    currency = "USD"
    if "estimate" in label and value:
        if day and day < today.isoformat():
            return None, "past lot with only an estimate: no result published"
        nums = re.split(r"\s+[-–]\s+", value)
        est_lo, currency = parse_price(nums[0])
        est_hi = parse_price(nums[1])[0] if len(nums) > 1 else None
        status = "upcoming"
    elif any(w in words for w in NO_PRICE) or (words.strip() == "sold"):
        return None, "sold, price not published"
    elif "not sold" in words:
        status = "unsold"
        if value and "bid" in label:
            price, currency = parse_price(value)
    elif value and ("sold" in label or not label):
        price, currency = parse_price(value)
        status = "sold"
    elif value and "bid" in label:
        price, currency = parse_price(value)
        status = "unsold"
    else:
        return None, f"unrecognised price row: {words[:60]}"

    vin = mileage = None
    for script in soup.find_all("script", type="application/ld+json"):
        txt = script.string or ""
        v = re.search(r'"vehicleIdentificationNumber"\s*:\s*"([^"]*)"', txt)
        if v and v.group(1).strip():
            vin = v.group(1).strip()
        mi = re.search(r'"mileageFromOdometer"\s*:\s*\{[^}]*"value"\s*:\s*"?([\d.,]+)', txt)
        if mi:
            mileage = float(mi.group(1).replace(",", "")) or None
    flag = head.select_one(".branch-country-flag") or soup.select_one(".vehicle-country-flag")
    country = None
    if flag:
        c = next((k for k in flag.get("class", []) if k.startswith("flag-icon-") and len(k) == 12), None)
        country = c[-2:].upper() if c else None

    rec = RawRecord(
        source=source, kind=AUCTION, source_id=m.group(1).lower(), url=SITE + m.group(0), title=title,
        price=price, currency=currency or "USD", price_text=None, make="Ferrari", status=status,
        ends_at=f"{day}T20:00:00+00:00" if day else None, estimate_low=est_lo, estimate_high=est_hi,
        lot_number=lot_no, vin=vin, mileage=mileage, country=country, location=event,
        auction_house="Broad Arrow", seller_type="auction",
        extra={"auction": event, "price_basis": "with premium", "keep_undated": True},
    )
    return rec, ("upcoming" if status == "upcoming" else "final")


class BroadArrow(Source):
    kind = AUCTION

    def _events(self) -> dict:
        events = {}
        for path in ("/past-auctions", "/events"):
            try:
                events.update(parse_events(self.ctx.fetcher.get(SITE + path).text))
            except Exception as e:                       # dates are a nice to have, never a reason to stop
                self.log.warning("could not read %s: %s", path, e)
        return events

    def _get(self, url: str) -> str:
        try:
            return self.ctx.fetcher.get(url).text
        except Blocked:
            if self.cfg.get("browser_fallback", True):
                return self.ctx.browser.html(url)
            raise

    def fetch(self) -> Iterator[RawRecord]:
        con, today = self.ctx.con, self.ctx.run_date
        events = self._events()
        urls = lot_urls(self.ctx.fetcher.get(SITEMAP).content)
        # newest auctions first: the event code ends in the year (am26, jc25, gi26e)
        urls.sort(key=lambda u: int((re.search(r"/vehicles/[a-z]+(\d+)", u) or [0, 0])[1]), reverse=True)
        budget = int(self.cfg.get("lot_pages", 80))
        self.log.info("%d Ferrari lot pages in the sitemap, %d auctions dated", len(urls), len(events))
        for url in urls:
            code = _LOT.search(url).group(1).lower()
            if con is not None:
                row = con.execute("SELECT status FROM lots WHERE uid=?", (f"{self.name}:{code}",)).fetchone()
                if row and row[0] in ("sold", "unsold"):
                    continue                              # final result already stored
                if db.kv_get(con, "ba_skip:" + code):
                    continue                              # no published price: read once, never again
            if budget <= 0:
                break
            budget -= 1
            rec, why = parse_lot(self._get(url), url, events, today, self.name)
            if rec is None:
                if con is not None and why.startswith(("sold, price", "past lot")):
                    db.kv_set(con, "ba_skip:" + code, why)
                self.log.debug("%s: %s", url, why)
                continue
            yield rec
