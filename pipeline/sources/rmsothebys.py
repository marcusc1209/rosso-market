"""RM Sotheby's.

Verified 2026-10-31 in a real browser:
  POST https://rmsothebys.com/api/search/SearchLots?page=<n>&pageSize=<n>
  JSON body {"searchTerm": "ferrari", "make": "Ferrari", "offerStatus": "Results" | "On Offer", "sortBy": "DatePosted"}
  -> {"items": [...], "pager": {"totalItems", "totalPages", ...}}

Item fields used: publicName (the title), value ("£668,750 GBP", "Sold", "Sale Agreed",
"$1,200,000 USD"), valueType ("Sold", "Asking", ""), preSaleEstimate, link, header
(auction name), location, countryCode, lot, sold, isStillForSale.

Results carry no sale date, but every lot link names its auction
(/auctions/lf26/lots/...) and each auction page carries a schema.org Event with
startDate and endDate. The adapter reads that once per auction, keeps it in the
kv table, dates each result by its auction's last day, and on every run dates up
to `date_backfill` older results that were saved without one. Private sales
(/ps00/inventory/...) have no auction and stay undated.
Prices on RM results include the buyer's premium.
"""
from __future__ import annotations

import json
import re
from typing import Iterator, Optional

import httpx

from .base import Source
from .. import db
from ..fetch import Blocked
from ..normalize import parse_price
from ..records import RawRecord, AUCTION

API = "https://rmsothebys.com/api/search/SearchLots"
SITE = "https://rmsothebys.com"


def is_range(text: str) -> bool:
    return len(re.findall(r"\d[\d,\.]*", text or "")) >= 2


def map_item(x: dict, source: str, offer_status: str) -> RawRecord | None:
    title = (x.get("publicName") or x.get("alt") or "").strip()
    if not title:
        return None
    value, vtype = str(x.get("value") or ""), str(x.get("valueType") or "").lower()
    price, cur = parse_price(value)
    if price is None or vtype not in ("sold", "asking", "bid to", "high bid", "") or is_range(value):
        price = None            # a range ("€275,000 - €300,000") is the estimate, never a result
    private = "PRIVATE" in str(x.get("header") or "").upper()
    if offer_status == "Results":
        if price is None:
            return None            # result withheld: only the estimate is published
        if "sold" in vtype or (x.get("sold") and price):
            status = "sold"
        elif price and not x.get("sold"):
            status = "unsold"
        else:
            return None            # "Sold" with the price withheld, or "Sale Agreed": nothing to learn from
    else:
        status = "upcoming"
    est_lo = est_hi = None
    # upcoming lots put the estimate in `value` ("$1,800,000 - $2,000,000 USD", "€520,000 - €620,000 EUR")
    est = str(x.get("preSaleEstimate") or "") or (value if status == "upcoming" else "")
    nums = re.findall(r"\d[\d,\.]*", est)
    if len(nums) >= 2:
        _, cur_est = parse_price(est)
        est_lo, _ = parse_price(nums[0]); est_hi, _ = parse_price(nums[1])
        cur = cur_est
    if status == "upcoming":
        price = None
    link = x.get("link") or ""
    return RawRecord(
        source=source, kind=AUCTION, source_id=str(x.get("id") or link), url=link if link.startswith("http") else SITE + link,
        title=title, price=price, currency=cur, price_text=None if is_range(value) else value, location=x.get("location"),
        country=x.get("countryCode"), status=status, estimate_low=est_lo, estimate_high=est_hi,
        lot_number=(x.get("lot") or None), no_reserve=("without reserve" in vtype) or None, auction_house="RM Sotheby's" + (" Private Sales" if private else ""),
        seller_type="auction", extra={"auction": x.get("header"), "price_basis": "with premium", "private_sale": private},
    )


_CODE = re.compile(r"^(https?://[^/]+)/auctions/([a-z0-9]+)/", re.I)
_EVENT = re.compile(r'"startDate"\s*:\s*"(\d{4}-\d{2}-\d{2})[^"]*"\s*,\s*"endDate"\s*:\s*"(\d{4}-\d{2}-\d{2})')


def auction_page(url: str) -> Optional[str]:
    m = _CODE.match(url or "")
    # sealed sales link to sealed.rmsothebys.com, but every auction page lives on the main site
    return f"{SITE}/auctions/{m.group(2).lower()}/" if m else None


def event_dates(html: str) -> Optional[tuple[str, str]]:
    m = _EVENT.search(html or "")
    return (m.group(1), m.group(2)) if m else None


class RMSothebys(Source):
    kind = AUCTION

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._events: dict[str, Optional[str]] = {}
        self._lookups = 0

    def auction_end(self, url: str) -> Optional[str]:
        """Last day of the auction a lot belongs to, read once per auction and cached in the database."""
        page = auction_page(url)
        if not page:
            return None
        if page in self._events:
            return self._events[page]
        con, key = self.ctx.con, "rm_event:" + page
        cached = db.kv_get(con, key) if con is not None else None
        if cached is not None:
            end = json.loads(cached).get("end")
        elif self._lookups >= int(self.cfg.get("date_backfill", 300)):
            return None                  # budget for this run spent, the rest wait for the next one
        else:
            self._lookups += 1
            try:
                dates = event_dates(self.ctx.fetcher.get(page).text)
            except (httpx.HTTPStatusError, Blocked) as e:
                self.log.info("no auction page for %s (%s)", page, e)
                dates = None
            end = dates[1] if dates else None
            if con is not None:
                db.kv_set(con, key, json.dumps({"start": dates[0] if dates else None, "end": end}))
        self._events[page] = end
        return end

    def date_stored_results(self) -> int:
        """Results imported before auction dates were read: date them in place."""
        con = self.ctx.con
        if con is None:
            return 0
        n = 0
        rows = con.execute("""SELECT uid, url FROM lots WHERE source=? AND ends_at IS NULL
                              AND status IN ('sold','unsold')""", (self.name,)).fetchall()
        for uid, url in rows:
            end = self.auction_end(url)
            if end:
                con.execute("UPDATE lots SET ends_at=? WHERE uid=?", (f"{end}T18:00:00+00:00", uid))
                n += 1
        con.commit()
        if n:
            self.log.info("dated %d stored results from their auction pages", n)
        return n

    def fetch(self) -> Iterator[RawRecord]:
        size = int(self.cfg.get("page_size", 60))
        for status, pages in (("On Offer", int(self.cfg.get("on_offer_pages", 3))), ("Results", int(self.cfg.get("result_pages", 5)))):
            for page in range(1, pages + 1):
                body = {"searchTerm": "ferrari", "make": "Ferrari", "offerStatus": status, "sortBy": "DatePosted"}
                data = self.ctx.fetcher.post_json(API + f"?page={page}&pageSize={size}", body)
                items = data.get("items") or []
                for x in items:
                    rec = map_item(x, self.name, status)
                    if rec and rec.ends_at is None:
                        end = self.auction_end(rec.url)
                        if end:
                            rec.ends_at = f"{end}T18:00:00+00:00"
                    if rec:
                        yield rec
                if page >= int((data.get("pager") or {}).get("totalPages") or 0):
                    break
        self.date_stored_results()
