"""Ferrari Approved (preowned.ferrari.com), the official network's used inventory.

Verified 2026-10-31 in a real browser:
  * search page: /en-US/r/north-america/used-ferrari/<country>/rfc   (usa: ~1,200 cars, canada: ~100)
  * the cars are in the Next.js page data at
    props.pageProps.initialState.search.searchResults.ads
  * pagination: ?pl=<0 based page index>, 11 cars per page plus one promotional slot,
    page count in searchResults.pagination.pages
  * the network lists every car at a Ferrari dealer, not only certified ones;
    `ferrariApproved` says which are certified
  * `requestPrice` true or a price under $1,000 means "price on request"
"""
from __future__ import annotations

import json
import re
from typing import Iterator

from .base import Source
from ..fetch import Blocked
from ..records import RawRecord, LISTING

BASE = "https://preowned.ferrari.com/en-US/r/north-america/used-ferrari/{country}/rfc"
AD_URL = "https://preowned.ferrari.com/en-US/a/north-america/used-ferrari/{country}/{dealer}/{model}/{id}"
_ND = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def parse_page(html: str) -> tuple[list[dict], int]:
    m = _ND.search(html)
    if not m:
        raise ValueError("no __NEXT_DATA__ on page (layout changed?)")
    sr = json.loads(m.group(1))["props"]["pageProps"]["initialState"]["search"]["searchResults"]
    ads = [a for a in sr.get("ads") or [] if isinstance(a, dict) and a.get("id")]
    return ads, int((sr.get("pagination") or {}).get("pages") or 1)


def map_ad(a: dict, source: str, country: str) -> RawRecord:
    dealer = a.get("dealer") or {}
    model = a.get("model") or {}
    name = a.get("carName") or model.get("name") or ""
    title = f"{a.get('year') or ''} Ferrari {name}".replace("Ferrari Ferrari", "Ferrari").strip()
    price = a.get("price")
    on_request = bool(a.get("requestPrice")) or not isinstance(price, (int, float)) or price < 1000
    vin = (a.get("vin") or "").strip() or None
    return RawRecord(
        source=source, kind=LISTING, source_id=str(vin or a.get("stock") or a["id"]),
        url=AD_URL.format(country=country, dealer=_slug(dealer.get("name")), model=_slug(name), id=a["id"]),
        title=title, price=None if on_request else float(price), currency=(a.get("priceUnit") or "USD").upper(),
        year=a.get("year"), make="Ferrari", model_text=name,
        mileage=a.get("odometer"), mileage_unit="km" if (a.get("odometerUnit") or "mi").lower().startswith("k") else "mi",
        vin=vin, exterior_color=a.get("exteriorColor"), interior_color=a.get("trimColor"),
        transmission=a.get("gearBox"),
        location=", ".join(x for x in [dealer.get("city"), dealer.get("state") or dealer.get("province")] if x) or None,
        country="CA" if country == "canada" else "US",
        seller_type="dealer", dealer_name=dealer.get("name"), certified=bool(a.get("ferrariApproved")),
        marketplace="Ferrari Approved",
        extra={"stock": a.get("stock"), "price_on_request": on_request},
    )


class FerrariApproved(Source):
    kind = LISTING

    use_browser = False

    def fetch(self) -> Iterator[RawRecord]:
        self.use_browser = bool(self.cfg.get("browser"))
        seen = set()
        for country in self.cfg.get("countries", ["usa", "canada"]):
            base = BASE.format(country=country)
            page, pages = 0, 1
            cap = int(self.cfg.get("max_pages", 200))
            while page < min(pages, cap):
                url = f"{base}?pl={page}"
                if not self.use_browser:
                    try:
                        html = self.ctx.fetcher.get(url).text
                    except Blocked as e:
                        # The site refuses plain requests from some networks (GitHub Actions got 403 on
                        # 2026-10-31). A real browser usually gets through, so switch for the rest of the run.
                        self.log.info("plain request refused (%s), switching to the browser", e)
                        self.use_browser = True
                if self.use_browser:
                    html = self.ctx.browser.html(url)
                ads, pages = parse_page(html)
                if not ads:
                    break
                for a in ads:
                    rec = map_ad(a, self.name, country)
                    if rec.source_id not in seen:
                        seen.add(rec.source_id)
                        yield rec
                page += 1
