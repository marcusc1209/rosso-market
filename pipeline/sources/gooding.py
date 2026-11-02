"""Gooding Christie's.

Verified 2026-10-31 in a real browser. robots.txt allows everything.
The lots page (www.goodingco.com/lots) is a Gatsby site whose catalogue comes
from an Apollo GraphQL server:

  POST https://cdn.goodingco.com/graphql   (header Authorization: "Bearer", no token)
  query GetVehiclesAndFilters(filtersInput: {make: ["Ferrari"]}, pageNumber, sortBy, hitsPerPage)
  -> getVehicles { vehicles [...], page, nbPages, nbHits }      (944 Ferrari lots, 19 pages of 50)

Vehicle fields used: title, modelYear, model, itemType, auctionType ("Live Auction",
"Online Only", "Private Sale"), auctionName, lotNumber, lowEstimate, highEstimate,
uniqueEstimate ("In excess of CHF 7,500,000"), salePrice (a string, or null),
privateSalesPrice (true when the result is withheld; salePrice is then "1"),
currency, auctionEndDate and upcomingAuctionEndDate (epoch ms, 9999999999999
meaning none), hasReservePrice, slug (-> /lot/<slug>/).

Rules, the same as every other house: private sales and withheld results are
skipped, a past lot without a sale price is unsold, an upcoming lot carries only
its estimate. Sale prices include the buyer's premium.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Iterator, Optional

from .base import Source
from .. import db
from ..normalize import parse_price
from ..records import RawRecord, AUCTION
from ..images import gooding as gooding_image

API = "https://cdn.goodingco.com/graphql"
SITE = "https://www.goodingco.com"
NO_DATE = 9_000_000_000_000           # 9999999999999 and 9999999999998 mean "no date"

QUERY = """query GetVehiclesAndFilters($filtersInput: FiltersInput!, $pageNumber: Int, $sortBy: SortBy!,
  $updateFilters: Boolean, $searchQuery: String, $hitsPerPage: Int) {
  getVehicles(filtersInput: $filtersInput, pageNumber: $pageNumber, sortBy: $sortBy,
    updateFilters: $updateFilters, searchQuery: $searchQuery, hitsPerPage: $hitsPerPage) {
    vehicles {
      objectID make hasReservePrice modelYear lowEstimate highEstimate uniqueEstimate salePrice
      auctionType auctionName lotNumber model auctionEndDate upcomingAuctionEndDate auctionStartDate
      slug privateSalesPrice itemType title currency cloudinaryImages { public_id }
    }
    hitsPerPage page nbPages nbHits
  }
}"""


def _ms(v) -> Optional[datetime]:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if n <= 0 or n >= NO_DATE:
        return None
    return datetime.fromtimestamp(n / 1000, tz=timezone.utc)


def map_vehicle(v: dict, source: str, now: datetime) -> Optional[RawRecord]:
    if (v.get("itemType") or "Cars") != "Cars":
        return None
    if v.get("auctionType") == "Private Sale" or v.get("privateSalesPrice") is True:
        return None                                   # private sale, or result withheld
    title = (v.get("title") or "").strip()
    if not title:
        return None
    cur = (v.get("currency") or "USD").upper()
    try:
        sale = float(v.get("salePrice")) if v.get("salePrice") not in (None, "") else None
    except ValueError:
        sale = None
    if sale is not None and sale <= 1:
        sale = None                                   # "1" is a placeholder for a withheld price
    ended, upcoming = _ms(v.get("auctionEndDate")), _ms(v.get("upcomingAuctionEndDate"))
    est_lo, est_hi = v.get("lowEstimate"), v.get("highEstimate")
    if est_lo is None and v.get("uniqueEstimate"):
        est_lo, _ = parse_price(v["uniqueEstimate"], cur)      # "In excess of CHF 7,500,000"

    if sale:
        status, when = "sold", ended or upcoming
    elif upcoming and upcoming > now:
        status, when = "upcoming", upcoming
    elif ended and ended <= now:
        status, when = "unsold", ended
    else:
        return None

    reserve = v.get("hasReservePrice")
    return RawRecord(
        source=source, kind=AUCTION, source_id=str(v.get("objectID") or v.get("slug")),
        url=f"{SITE}/lot/{v.get('slug')}/", title=title, price=sale, currency=cur,
        year=v.get("modelYear"), make="Ferrari", model_text=v.get("model"),
        status=status, ends_at=when.isoformat() if when else None,
        estimate_low=float(est_lo) if est_lo else None, estimate_high=float(est_hi) if est_hi else None,
        no_reserve=(not reserve) if reserve is not None else None,
        lot_number=str(v["lotNumber"]) if v.get("lotNumber") is not None else None,
        location=v.get("auctionName"), auction_house="Gooding Christie's", seller_type="auction",
        image_url=gooding_image(((v.get("cloudinaryImages") or [{}])[0] or {}).get("public_id")),
        extra={"auction": v.get("auctionName"), "auction_type": v.get("auctionType"), "price_basis": "with premium",
               "photos": [u for u in (gooding_image((c or {}).get("public_id"), 1280) for c in (v.get("cloudinaryImages") or [])[:40]) if u]},
    )


def sale_pages(xml: str) -> list[str]:
    """Slugs of /auction/<slug> pages in the sitemap (not the /auction/realized/ copies)."""
    return sorted({m for m in re.findall(r"/auction/([a-z0-9-]+)/?\s*<", xml or "") if m != "realized"})


class Gooding(Source):
    kind = AUCTION

    def fetch(self) -> Iterator[RawRecord]:
        now = datetime.now(timezone.utc)
        size, page, pages = int(self.cfg.get("page_size", 50)), 0, 1
        max_pages = int(self.cfg.get("max_pages", 40))
        while page < min(pages, max_pages):
            body = {"operationName": "GetVehiclesAndFilters", "query": QUERY,
                    "variables": {"filtersInput": {"make": ["Ferrari"]}, "pageNumber": page,
                                  "sortBy": "DEFAULT", "hitsPerPage": size}}
            data = self.ctx.fetcher.post_json(API, body, headers={"Authorization": "Bearer", "Origin": SITE,
                                                                  "Referer": SITE + "/lots/"})
            if data.get("errors"):
                raise RuntimeError(f"Gooding GraphQL error: {str(data['errors'])[:300]}")
            got = (data.get("data") or {}).get("getVehicles") or {}
            pages = int(got.get("nbPages") or 0)
            for v in got.get("vehicles") or []:
                rec = map_vehicle(v, self.name, now)
                if rec:
                    yield rec
            page += 1
        self._save_sale_pages()

    def _save_sale_pages(self):
        """Gooding's sale pages, from the sitemap robots.txt names. Their names vary
        (amelia-island-auction-2025, amelia-island-auctions-2026), so the site matches each
        lot's sale to this list rather than guessing the address."""
        con = self.ctx.con
        if con is None:
            return
        try:
            pages = sale_pages(self.ctx.fetcher.get(SITE + "/sitemap.xml").text)
        except Exception as e:                           # sale links are a nice to have
            self.log.warning("could not read the sitemap for sale pages: %s", e)
            return
        if pages:
            db.kv_set(con, "sales:gooding", json.dumps(pages))
            self.log.info("%d sale pages in the sitemap", len(pages))
