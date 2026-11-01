"""Mecum.

Verified 2026-10-31 in a real browser. Mecum's lot search runs on Algolia:
  index:   wp_posts_lot
  filter:  facetFilters [["taxonomies.make.name:Ferrari"]]   (~1,850 Ferrari lots)
  fields:  post_title, permalink, sold (0/1), highest_bid_or_price, bid_goes_on,
           auction_end_date_meta (unix seconds), lot_number_meta,
           taxonomies.auction_tax[0].name ("Kissimmee 2027|<start>|<end>"),
           taxonomies.lot_status[].name (includes "No reserve")

The application id and the search-only API key are public (every visitor's browser
uses them), but they are issued at runtime, so the adapter opens mecum.com once in
headless Chromium, reads them from the page's own Algolia request, then queries
Algolia directly. Set MECUM_ALGOLIA_APP and MECUM_ALGOLIA_KEY to skip the browser.
Prices are hammer plus buyer's premium.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import urlparse, parse_qs

from .base import Source
from ..fetch import Blocked
from ..records import RawRecord, AUCTION
from ..images import first_image, find_image, gooding as gooding_image, sized


def map_hit(h: dict, source: str, now: float) -> RawRecord | None:
    title = (h.get("post_title") or "").strip()
    if not title:
        return None
    tax = h.get("taxonomies") or {}
    end = h.get("auction_end_date_meta")
    end = float(end) if isinstance(end, (int, float)) or (isinstance(end, str) and end.isdigit()) else None
    price = h.get("highest_bid_or_price") or None
    bgo_number = None
    if h.get("sold"):
        if not price:
            return None            # sold, price not published
        status = "sold"
    elif end and end > now:
        status = "upcoming"
    elif h.get("bid_goes_on") or price:
        # "The Bid Goes On": not sold on the block. The number Mecum shows for these lots is
        # not a high bid (live check 2026-10-31: a 2015 458 Spider showed $600,000, over twice
        # its value), so it is kept for reference and never used as a price.
        status = "unsold"
    else:
        return None
    if status == "unsold":
        bgo_number, price = price, None
    auction = ((tax.get("auction_tax") or [{}])[0].get("name") or "").split("|")[0] or None
    labels = [s.get("name", "") for s in tax.get("lot_status") or [] if isinstance(s, dict)]
    year = (tax.get("lot_year") or [{}])[0].get("name")
    return RawRecord(
        source=source, kind=AUCTION, source_id=str(h.get("objectID") or h.get("post_id")),
        url=h.get("permalink") or "", title=title, price=float(price) if price else None, currency="USD",
        year=int(year) if str(year or "").isdigit() else None, make="Ferrari",
        status=status, ends_at=datetime.fromtimestamp(end, tz=timezone.utc).isoformat() if end else None,
        no_reserve="No reserve" in labels, lot_number=h.get("lot_number_meta"),
        location=auction, auction_house="Mecum", seller_type="auction", image_url=find_image(h),
        extra={"auction": auction, "price_basis": "with premium",
               **({"bid_goes_on_figure": bgo_number} if status == "unsold" else {})},
    )


class Mecum(Source):
    kind = AUCTION

    def _keys(self) -> tuple[str, str]:
        app, key = os.environ.get("MECUM_ALGOLIA_APP"), os.environ.get("MECUM_ALGOLIA_KEY")
        if app and key:
            return app, key
        urls = self.ctx.browser.capture_requests("https://www.mecum.com/lots/", "algolia.net")
        for u in urls:
            q = parse_qs(urlparse(u).query)
            app, key = (q.get("x-algolia-application-id") or [None])[0], (q.get("x-algolia-api-key") or [None])[0]
            if app and key:
                return app, key
        raise Blocked("could not read Mecum's Algolia key from the page")

    def fetch(self) -> Iterator[RawRecord]:
        app, key = self._keys()
        url = f"https://{app}-dsn.algolia.net/1/indexes/wp_posts_lot/query"
        headers = {"X-Algolia-Application-Id": app, "X-Algolia-API-Key": key}
        now = time.time()
        since = now - 86400 * int(self.cfg.get("lookback_days", 400))
        page, pages = 0, 1
        while page < pages:
            body = {"query": "", "hitsPerPage": 200, "page": page,
                    "facetFilters": [["taxonomies.make.name:Ferrari"]],
                    "numericFilters": [f"auction_end_date_meta>={int(since)}"]}
            data = self.ctx.fetcher.request("POST", url, json=body, headers=headers).json()
            pages = int(data.get("nbPages") or 0)
            for h in data.get("hits") or []:
                rec = map_hit(h, self.name, now)
                if rec:
                    yield rec
            page += 1
