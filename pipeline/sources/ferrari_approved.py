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
import time
from typing import Iterator

from .base import Source
from .. import wayback
from ..fetch import Blocked
from ..records import RawRecord, LISTING
from ..images import first_image, all_images, sized

BASE = "https://preowned.ferrari.com/en-US/r/north-america/used-ferrari/{country}/rfc"
AD_URL = "https://preowned.ferrari.com/en-US/a/north-america/used-ferrari/{country}/{dealer}/{model}/{id}"
_AD_PARTS = re.compile(r"/used-ferrari/([a-z]+)/([a-z0-9-]+)/")
_ND = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def parse_page(html: str) -> tuple[list[dict], int]:
    m = _ND.search(html)
    if not m:
        raise ValueError("no __NEXT_DATA__ on page (layout changed?)")
    try:
        sr = json.loads(m.group(1))["props"]["pageProps"]["initialState"]["search"]["searchResults"] or {}
    except (KeyError, TypeError, ValueError):
        # a page in another shape, with no search results (seen 2026-11-10 mid run, after 541 cars): treated
        # like an empty page, so the reader waits and retries it rather than failing the whole source
        return [], 0
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
        image_url=sized(first_image(a.get("cardImages")), height=400),
        extra={"stock": a.get("stock"), "price_on_request": on_request,
               "photos": [sized(u, height=900) for u in all_images(a.get("cardImages"))]},
    )


class FerrariApproved(Source):
    kind = LISTING

    use_browser = False

    def fetch(self) -> Iterator[RawRecord]:
        """Ferrari Approved publishes no real listing date (its feed re-stamps every car daily),
        so each car is dated from the Wayback Machine's archived ads where it can be."""
        archive = wayback.Lookups(self.ctx.con, self.ctx.fetcher, int(self.cfg.get("wayback_lookups", 60)))
        dated = 0
        for rec in self._ads():
            m = _AD_PARTS.search(rec.url)
            day = archive.ferrari_approved(m.group(1), m.group(2), rec.vin, self.ctx.run_date) if m else None
            if day:
                rec.extra.update(listed_on=day, listed_how="web archive")
                dated += 1
            yield rec
        self.log.info("%d cars dated from the Wayback Machine (%d dealer archives asked, %d answered)",
                      dated, archive.asked, archive.answered)

    def _ads(self) -> Iterator[RawRecord]:
        self.use_browser = bool(self.cfg.get("browser"))
        seen = set()
        for country in self.cfg.get("countries", ["usa", "canada"]):
            base = BASE.format(country=country)
            page, pages, empty_streak = 0, 1, 0
            cap = int(self.cfg.get("max_pages", 200))
            while page < min(pages, cap):
                url = f"{base}?pl={page}"
                ads, n = self._load(url)
                pages = max(pages, n)
                if not ads:
                    # A page inside the known range came back empty: the site slowed us down
                    # (seen 2026-11-01 after ~20 quick browser loads). Wait and try again before
                    # giving up, and never let one bad page end the whole country.
                    for pause in self.cfg.get("retry_pauses", [20, 45, 90]):
                        self.log.info("%s: no cars on page %d of %d, waiting %ds", country, page, pages, pause)
                        time.sleep(pause)
                        ads, n = self._load(url)
                        if ads:
                            break
                if not ads:
                    empty_streak += 1
                    if empty_streak >= 3 or page + 1 >= pages:
                        self.log.warning("%s: stopping at page %d of %d after repeated empty pages", country, page, pages)
                        break
                    page += 1
                    continue
                empty_streak = 0
                for a in ads:
                    rec = map_ad(a, self.name, country)
                    if rec.source_id not in seen:
                        seen.add(rec.source_id)
                        yield rec
                page += 1
            self.log.info("%s: %d pages of %d read, %d cars so far", country, page, pages, len(seen))

    def _load(self, url: str) -> tuple[list[dict], int]:
        if not self.use_browser:
            try:
                return parse_page(self.ctx.fetcher.get(url).text)
            except Blocked as e:
                # The site refuses plain requests from some networks (GitHub Actions got 403 on
                # 2026-10-31, home 405 on 2026-11-01). A real browser gets through, so switch for the rest of the run.
                self.log.info("plain request refused (%s), switching to the browser", e)
                self.use_browser = True
        time.sleep(float(self.cfg.get("browser_pause_seconds", 3)))
        return parse_page(self.ctx.browser.html(url))
