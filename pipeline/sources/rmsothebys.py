"""RM Sotheby's.

Verified 2026-10-31 in a real browser:
  POST https://rmsothebys.com/api/search/SearchLots?page=<n>&pageSize=<n>
  JSON body {"searchTerm": "ferrari", "make": "Ferrari", "offerStatus": "Results" | "On Offer", "sortBy": "DatePosted"}
  -> {"items": [...], "pager": {"totalItems", "totalPages", ...}}

Item fields used: publicName (the title), value ("£668,750 GBP", "Sold", "Sale Agreed",
"$1,200,000 USD"), valueType ("Sold", "Asking", ""), preSaleEstimate, link, header
(auction name), location, countryCode, lot, sold, isStillForSale.

Results carry no sale date. The pipeline dates a new result by the day it first
appears (see run.py), and the first import is treated as history without a date.
Prices on RM results include the buyer's premium.
"""
from __future__ import annotations

import re
from typing import Iterator

from .base import Source
from ..normalize import parse_price
from ..records import RawRecord, AUCTION

API = "https://rmsothebys.com/api/search/SearchLots"
SITE = "https://rmsothebys.com"


def map_item(x: dict, source: str, offer_status: str) -> RawRecord | None:
    title = (x.get("publicName") or x.get("alt") or "").strip()
    if not title:
        return None
    value, vtype = str(x.get("value") or ""), str(x.get("valueType") or "").lower()
    price, cur = parse_price(value)
    if price is None or vtype not in ("sold", "asking", "bid to", "high bid", ""):
        price = None
    private = "PRIVATE" in str(x.get("header") or "").upper()
    if offer_status == "Results":
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
        title=title, price=price, currency=cur, price_text=value, location=x.get("location"),
        country=x.get("countryCode"), status=status, estimate_low=est_lo, estimate_high=est_hi,
        lot_number=(x.get("lot") or None), no_reserve=("without reserve" in vtype) or None, auction_house="RM Sotheby's" + (" Private Sales" if private else ""),
        seller_type="auction", extra={"auction": x.get("header"), "price_basis": "with premium", "private_sale": private},
    )


class RMSothebys(Source):
    kind = AUCTION

    def fetch(self) -> Iterator[RawRecord]:
        size = int(self.cfg.get("page_size", 60))
        for status, pages in (("On Offer", int(self.cfg.get("on_offer_pages", 3))), ("Results", int(self.cfg.get("result_pages", 5)))):
            for page in range(1, pages + 1):
                body = {"searchTerm": "ferrari", "make": "Ferrari", "offerStatus": status, "sortBy": "DatePosted"}
                data = self.ctx.fetcher.post_json(API + f"?page={page}&pageSize={size}", body)
                items = data.get("items") or []
                for x in items:
                    rec = map_item(x, self.name, status)
                    if rec:
                        yield rec
                if page >= int((data.get("pager") or {}).get("totalPages") or 0):
                    break
