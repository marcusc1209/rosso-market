"""AutosToday: a national used car marketplace built from dealers' own inventory feeds.

Verified 2026-11-01 in a real browser. robots.txt allows everything except its sitemap
pages, and the search pages answer plain requests with their data. Cloudflare's passive
detection script is loaded, but no challenge is served. Photos come from the dealers' own
websites and inventory systems, not from another marketplace.

Search  /cars-for-sale/used-cars/ferrari?page=N   ~31 cars a page, ~2,400 used Ferraris
  Next.js page data props.pageProps.listingsData = {count, results: [{id, sponsored,
  fields: {title, vin, price, trim, mileage, location "Joliet, IL", photo, year, dealer,
  dealer_code, phone, new_used, make, model "F8", exterior_color, interior_color, dom}}]}.
  dom is days on market. Sponsored cars repeat across pages: kept once by VIN.
Listing  /cars-for-sale/used-cars/listing/<VIN> redirects to the car's page.

Titles are often loose ("2018 Ferrari 488 Base" for a 488 Spider), so the model name the
VIN encodes (characters 4 and 5 on cars from 2010 on) is passed as the model hint.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Iterator, Optional

from .dealer_marketplaces import _Marketplace, _int
from ..records import RawRecord, LISTING

SITE = "https://www.autostoday.com"
SEARCH = SITE + "/cars-for-sale/used-cars/ferrari"
_NEXT = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

# Ferrari VIN characters 4 and 5, cars from model year 2010 on (checked against Ferrari Approved)
VIN_MODEL = {
    "65": "California", "77": "California T", "67": "458 Italia", "68": "458 Spider", "75": "458 Speciale",
    "78": "458 Speciale A", "74": "F12berlinetta", "81": "F12tdf", "79": "488 GTB", "80": "488 Spider",
    "90": "488 Pista", "91": "488 Pista Spider", "92": "F8 Tributo", "93": "F8 Spider", "83": "812 Superfast",
    "97": "812 GTS", "03": "812 Competizione", "04": "812 Competizione A", "89": "Portofino", "02": "Portofino M",
    "98": "Roma", "09": "Roma Spider", "95": "SF90 Stradale", "96": "SF90 Spider", "99": "296 GTB",
    "01": "296 GTS", "06": "Purosangue", "10": "12Cilindri", "11": "12Cilindri Spider",
}
_YEAR_CODE = "ABCDEFGHJKLMNPRSTVWXY"           # VIN character 10: A = 2010 ...


def vin_model(vin: Optional[str]) -> Optional[str]:
    v = (vin or "").upper()
    if len(v) != 17 or not v.startswith(("ZFF", "ZSG")) or v[9] not in _YEAR_CODE:
        return None
    return VIN_MODEL.get(v[3:5])


def parse_page(html: str) -> tuple[list[dict], int]:
    m = _NEXT.search(html or "")
    if not m:
        return [], 0
    data = ((json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}).get("listingsData") or {}
    return data.get("results") or [], int(data.get("count") or 0)


def _clean(v) -> Optional[str]:
    v = str(v or "").strip()
    return None if not v or v.lower() in ("other", "unknown", "n/a") else v


def _name(s: Optional[str]) -> Optional[str]:
    s = (s or "").strip()
    return s.title() if s and s == s.lower() else (s or None)


def map_listing(x: dict, source: str, today) -> Optional[RawRecord]:
    f = x.get("fields") or {}
    if str(f.get("make") or "").lower() != "ferrari" or str(f.get("new_used") or "used").lower() != "used":
        return None
    vin = str(f.get("vin") or "").strip().upper() or None
    price = f.get("price")
    dom = _int(f.get("dom"))
    loc = _clean(f.get("location"))
    photo = f.get("photo") if isinstance(f.get("photo"), str) else None
    return RawRecord(
        source=source, kind=LISTING, source_id=vin or str(x.get("id")),
        url=f"{SITE}/cars-for-sale/used-cars/listing/{vin or x.get('id')}",
        title=" ".join(str(f.get("title") or "").split()), price=float(price) if price else None, currency="USD",
        year=_int(f.get("year")), make="Ferrari", model_text=vin_model(vin), mileage=_int(f.get("mileage")),
        mileage_unit="mi", vin=vin, exterior_color=_clean(f.get("exterior_color")),
        interior_color=_clean(f.get("interior_color")), location=loc, country="US",
        seller_type="dealer", dealer_name=_name(f.get("dealer")), certified=False, marketplace="AutosToday",
        image_url=photo,
        extra={"photos": [photo] if photo else [],
               **({"listed_on": (today - timedelta(days=dom)).isoformat()} if dom is not None else {})},
    )


class AutosToday(_Marketplace):
    def fetch(self) -> Iterator[RawRecord]:
        seen, count, page, stale = set(), 0, 1, 0
        limit = int(self.cfg.get("max_pages", 120))
        while page <= limit:
            rows, n = parse_page(self._html(SEARCH + (f"?page={page}" if page > 1 else "")))
            count = max(count, n)
            new = 0
            for x in rows:
                rec = map_listing(x, self.name, self.ctx.run_date)
                if rec and rec.source_id not in seen:
                    seen.add(rec.source_id)
                    new += 1
                    yield rec
            # past the last page the site repeats or comes back empty; sponsored cars alone are not progress
            stale = stale + 1 if new == 0 else 0
            if not rows or stale >= 2:
                break
            page += 1
        self.log.info("%d cars read from %d pages (site count %d)", len(seen), page, count)
