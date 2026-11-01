"""Sotheby's Motorsport (SOMO) and duPont Registry Live.

Both verified 2026-10-31 in a real browser. They share a URL scheme
(/auction/<slug>) and a similar listings endpoint, with different response shapes.

SOMO       GET https://sothebysmotorsport.com/api/auctions/listings?limit=50&page=N&type=<t>&sort=<s>
           type/sort pairs: live/ending_soon, sold/closed_date_desc
           (upcoming/ending_soon answers 400 "unauthorized" since 2026-11-01: it needs a sign in)
           -> data.auctions[], data.pagination.pageCount
           lot: vehicleData{year, make, model, slug, location{city,state}}, bidDetails.value,
                buyerFeeGross, status (started, closed), endTime, soldDate, lotNumber
           Prices here are the hammer bid; the buyer's fee is kept in extra.

duPont     GET https://live.dupontregistry.com/api/auctions/listings?limit=50&page=N&type=<t>&sort=<s>
Live       -> data.cards[], data.pageCount
           card: title, slug, href, highestBid, endTime (ms), vehicle{year, make, model},
                 outcome{vehicleSold, vehicleUnsold}, status, lotNumber, location
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

import httpx

from .base import Source
from urllib.parse import urlencode

from ..fetch import Blocked
from ..records import RawRecord, AUCTION
from ..images import first_image

QUERIES = [("live", "ending_soon"), ("upcoming", "ending_soon"), ("sold", "closed_date_desc")]


def _iso(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc).isoformat()
    return str(v)


def map_somo(a: dict, source: str, qtype: str) -> RawRecord | None:
    v = a.get("vehicleData") or {}
    if "ferrari" not in str(v.get("make") or "").lower():
        return None
    loc = v.get("location") or {}
    bid = (a.get("bidDetails") or {}).get("value")
    if qtype == "live":
        status = "live"
    elif qtype == "upcoming":
        status = "upcoming"
    else:
        # verified 2026-10-31: the "sold" feed only holds sold cars (bid at or above reserve);
        # older results simply have no buyerFeeGross recorded
        status = "sold"
    return RawRecord(
        source=source, kind=AUCTION, source_id=str(a.get("auctionId") or v.get("slug")),
        url=f"https://sothebysmotorsport.com/auction/{v.get('slug')}",
        title=f"{v.get('year') or ''} Ferrari {v.get('model') or ''}".strip(),
        price=float(bid) if bid else None, currency=((a.get("reservePrice") or {}).get("currency") or "USD"),
        year=v.get("year"), make="Ferrari", model_text=v.get("model"),
        location=", ".join(str(x).strip(" ,") for x in [loc.get("city"), loc.get("state")] if x) or None,
        status=status, ends_at=_iso(a.get("soldDate") if status == "sold" else a.get("endTime")),
        no_reserve=not a.get("hasReservePrice", True), lot_number=str(a.get("lotNumber") or "") or None,
        auction_house="Sotheby's Motorsport", seller_type="auction",
        image_url=first_image(((v.get("files") or {}).get("titleImage"))),
        extra={"buyer_fee": a.get("buyerFeeGross"), "price_basis": "hammer"},
    )


def map_dupont(c: dict, source: str, qtype: str) -> RawRecord | None:
    v = c.get("vehicle") or {}
    if "ferrari" not in (str(v.get("make") or "") + " " + str(c.get("title") or "")).lower():
        return None
    out = c.get("outcome") or {}
    if qtype == "live":
        status = "live"
    elif qtype == "upcoming":
        status = "upcoming"
    else:
        status = "sold" if out.get("vehicleSold") or out.get("acceptedOffer") else "unsold"
    href = c.get("href") or f"/auction/{c.get('slug')}"
    bid = c.get("highestBid")
    return RawRecord(
        source=source, kind=AUCTION, source_id=str(c.get("auctionId") or c.get("slug")),
        url="https://live.dupontregistry.com" + href, title=str(c.get("title") or "").strip(),
        price=float(bid) if bid else None, currency="USD", year=v.get("year"), make=v.get("make"),
        model_text=str(v.get("model") or "").split("|")[0].strip() or None, location=c.get("location") or None,
        status=status, ends_at=_iso(out.get("soldAt") if status == "sold" and out.get("soldAt") else c.get("endTime")),
        no_reserve=None,   # hasReserve came back false on every card checked 2026-10-31, so it is not trusted
        lot_number=str(c.get("lotNumber") or "") or None, image_url=first_image(c.get("image")),
        auction_house="duPont Registry Live", seller_type="auction", extra={"subtitle": c.get("subtitle"), "price_basis": "hammer"},
    )


class _ListingsApi(Source):
    kind = AUCTION
    skip_types: tuple = ()
    host = ""
    container = ""
    mapper = None

    def _get(self, params: dict) -> dict:
        url = f"{self.host}/api/auctions/listings?" + urlencode(params)
        if not self.use_browser:
            try:
                return self.ctx.fetcher.get_json(url)
            except Blocked as e:
                # SOMO refused GitHub Actions with 403 on 2026-10-31; the same call from inside
                # the site's own page in a real browser is the fallback.
                self.log.info("plain request refused (%s), switching to the browser", e)
                self.use_browser = True
        return self.ctx.browser.json_in_page(self.host + "/", url)

    def fetch(self) -> Iterator[RawRecord]:
        self.use_browser = bool(self.cfg.get("browser"))
        for qtype, sort in QUERIES:
            pages = int(self.cfg.get("sold_pages", 4)) if qtype == "sold" else 5
            if qtype in self.skip_types:
                continue
            for page in range(1, pages + 1):
                try:
                    data = self._get({"limit": 50, "page": page, "type": qtype, "sort": sort})
                except httpx.HTTPStatusError as e:
                    # one list going private should not take the whole source down
                    if e.response.status_code not in (400, 401, 404) or qtype == "sold":
                        raise
                    self.log.warning("%s list refused (%s), skipping it", qtype, e.response.status_code)
                    break
                d = data.get("data") or {}
                rows = d.get(self.container) or []
                for row in rows:
                    rec = type(self).mapper(row, self.name, qtype)
                    if rec:
                        yield rec
                total = (d.get("pagination") or {}).get("pageCount") or d.get("pageCount") or 1
                if not rows or page >= int(total):
                    break


class SothebysMotorsport(_ListingsApi):
    host, container, mapper = "https://sothebysmotorsport.com", "auctions", map_somo
    skip_types = ("upcoming",)        # members only since 2026-11-01


class DupontLive(_ListingsApi):
    host, container, mapper = "https://live.dupontregistry.com", "cards", map_dupont
