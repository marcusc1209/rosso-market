"""Bring a Trailer.

Live auctions: the Ferrari make page (and the /auctions/ page) embed the
current listings as a JavaScript object in the HTML.

Results: BaT's own WordPress REST endpoint
  POST /wp-json/bringatrailer/1.0/data/listings-filter
returns completed auctions page by page. Rather than hard code the filter id
for Ferrari, we read the `base_filter` the make page itself uses, so the
adapter keeps working if BaT renumbers its categories.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Iterator

from .base import Source
from ..extract import js_vars, walk, norm_status
from ..normalize import parse_price
from ..records import RawRecord, AUCTION

API = "https://bringatrailer.com/wp-json/bringatrailer/1.0/data/listings-filter"


def _is_item(d) -> bool:
    return isinstance(d, dict) and isinstance(d.get("url"), str) and "/listing/" in d["url"] and "title" in d


def parse_item(d: dict, source: str = "bringatrailer") -> RawRecord:
    sold_text = re.sub(r"<[^>]+>", " ", str(d.get("sold_text") or ""))
    status = None
    price = None
    cur = (d.get("currency") or "USD").upper()
    if sold_text:
        low = sold_text.lower()
        status = "sold" if "sold for" in low else "unsold" if ("bid to" in low or "reserve" in low) else norm_status(low)
        price, cur = parse_price(sold_text.split(" on ")[0], d.get("currency") or "USD")
    if d.get("active") is True or (status is None and d.get("current_bid") and not sold_text):
        status = "live"
    if price is None:
        price, _ = parse_price(d.get("current_bid"))
    ends = d.get("timestamp_end")
    ends_at = None
    if isinstance(ends, (int, float)) or (isinstance(ends, str) and ends.isdigit()):
        ends_at = datetime.fromtimestamp(int(ends), tz=timezone.utc).isoformat()
    elif sold_text and re.search(r"on (\d{1,2}/\d{1,2}/\d{2,4})", sold_text):
        md = re.search(r"on (\d{1,2})/(\d{1,2})/(\d{2,4})", sold_text)
        y = int(md.group(3)); y = y + 2000 if y < 100 else y
        ends_at = datetime(y, int(md.group(1)), int(md.group(2)), tzinfo=timezone.utc).isoformat()
    title = html.unescape(re.sub(r"<[^>]+>", "", str(d.get("title", "")))).strip()
    return RawRecord(
        source=source, kind=AUCTION, source_id=str(d.get("id") or d["url"]), url=d["url"], title=title,
        price=price, currency=cur, price_text=sold_text or str(d.get("current_bid_formatted") or ""),
        year=int(d["year"]) if str(d.get("year", "")).isdigit() else None,
        location=d.get("country") or d.get("location"), country=d.get("country_code"),
        status=status, ends_at=ends_at, no_reserve=bool(d.get("noreserve")) if "noreserve" in d else None,
        bid_count=d.get("bid_count") if isinstance(d.get("bid_count"), int) else None,
        auction_house="Bring a Trailer", seller_type="auction",
        image_url=d.get("thumbnail_url") or d.get("image"),
        extra={"excerpt": d.get("excerpt"), "price_basis": "hammer"},
    )


class BringATrailer(Source):
    kind = AUCTION

    def fetch(self) -> Iterator[RawRecord]:
        f = self.ctx.fetcher
        seen = set()
        base_filter = None
        for url in self.cfg.get("live_urls", ["https://bringatrailer.com/ferrari/", "https://bringatrailer.com/auctions/"]):
            html = f.get(url).text
            for name, blob in js_vars(html).items():
                if isinstance(blob, dict) and "base_filter" in blob and base_filter is None and "ferrari" in url:
                    base_filter = blob["base_filter"]
                for item in walk(blob, _is_item):
                    rec = parse_item(item, self.name)
                    if rec.source_id in seen or "ferrari" not in (rec.title.lower() + url):
                        continue
                    seen.add(rec.source_id)
                    yield rec

        # completed results, newest first, stop when we reach results older than the lookback window
        pages = int(self.cfg.get("result_pages", 5))
        payload = {"page": 1, "per_page": 36, "get_items": 1, "get_stats": 0, "sort": "td",
                   "base_filter": base_filter or {"keyword_s": "Ferrari", "items_type": "make"}}
        for page in range(1, pages + 1):
            payload["page"] = page
            data = f.post_json(API, payload)
            items = list(walk(data, _is_item))
            if not items:
                break
            for item in items:
                rec = parse_item(item, self.name)
                if rec.source_id in seen:
                    continue
                seen.add(rec.source_id)
                yield rec
            if page >= int(data.get("pages_total", pages) or pages):
                break
