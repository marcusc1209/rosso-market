"""Bonhams (cars.bonhams.com and www.bonhams.com).

Verified 2026-10-31 in a real browser. robots.txt allows search and lot pages.
Bonhams search runs on a Typesense proxy:

  POST https://api01.bonhams.com/search-proxy/multi_search?use_cache=true
  header X-TYPESENSE-API-KEY: <public search only key>
  {"searches": [{"collection": "lots", "q": "ferrari", "query_by": "title",
                 "filter_by": "department.name: [`Cars`, `Collector Cars`]", "page": 1, "per_page": 250}]}

The key is the one every visitor's browser gets: it sits in the site's own
/_next/static/chunks/pages/_app-<hash>.js as path:"/search-proxy",apiKey:"...".
The adapter reads it from there on each run (config `api_key` is only a fallback).

~1,800 Ferrari car lots. Document fields used: title, status (SOLD, BI = bought in,
WD = withdrawn, CS, NEW = upcoming), price.hammerPremium (with premium),
price.estimateLow/High, currency.iso_code, auctionEndDate / hammerTime,
auctionId, lotId, slug, country.code, auctionType, flags.isResultPublished.
Automobilia, watches and the like sit in other departments and are filtered out.

Rules, as for every house: a SOLD lot needs a published price, BI is unsold
(Bonhams publishes no high bid), WD and CS (a conditional sale) are skipped,
NEW is upcoming with its estimate only. Lot URL:
https://www.bonhams.com/auction/<auctionId>/lot/<lotId>/<slug>/
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Iterator, Optional

from .base import Source
from .. import db
from ..records import RawRecord, AUCTION
from ..images import first_image, sized

SEARCH = "https://api01.bonhams.com/search-proxy/multi_search?use_cache=true"
SITE = "https://www.bonhams.com"
DEPARTMENTS = "department.name: [`Cars`, `Collector Cars`]"
_APP = re.compile(r'(/_next/static/chunks/pages/_app-[0-9a-f]+\.js)')
_KEY = re.compile(r'path:"/search-proxy",apiKey:"([A-Za-z0-9]+)"')


def find_key(app_js: str) -> Optional[str]:
    m = _KEY.search(app_js or "")
    return m.group(1) if m else None


def _when(d: dict) -> Optional[str]:
    for k in ("auctionEndDate", "hammerTime"):
        v = d.get(k) or {}
        ts = v.get("timestamp") if isinstance(v, dict) else None
        if ts:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    return None


def map_lot(d: dict, source: str) -> Optional[RawRecord]:
    status = d.get("status")
    if (d.get("auctionType") or "PUBLIC") != "PUBLIC":
        return None                                   # private sale
    price = d.get("price") or {}
    cur = ((d.get("currency") or {}).get("iso_code") or "USD").upper()
    lo, hi = price.get("estimateLow") or None, price.get("estimateHigh") or None
    flags = d.get("flags") or {}
    if status == "SOLD":
        sale = price.get("hammerPremium") or price.get("hammerPrice") or None
        if not sale or flags.get("isResultPublished") is False:
            return None                               # sold, price not published
        st, value = "sold", float(sale)
    elif status == "BI":
        st, value = "unsold", None                    # bought in, no high bid published
    elif status == "NEW":
        st, value = "upcoming", None
    else:
        return None                                   # WD withdrawn, CS conditional sale
    title = re.sub(r"\s+", " ", d.get("title") or "").strip()
    if not title:
        return None
    aid, lot = d.get("auctionId"), d.get("lotId")
    url = f"{SITE}/auction/{aid}/lot/{lot}/" + (f"{d['slug']}/" if d.get("slug") else "")
    return RawRecord(
        source=source, kind=AUCTION, source_id=str(d.get("id") or f"{aid}-{lot}"), url=url, title=title,
        price=value, currency=cur, status=st, ends_at=_when(d),
        estimate_low=float(lo) if lo else None, estimate_high=float(hi) if hi else None,
        no_reserve=bool(flags.get("isWithoutReserve")) if "isWithoutReserve" in flags else None,
        lot_number=str(lot) if lot not in (None, "0") else None,
        country=(d.get("country") or {}).get("code"), location=(d.get("country") or {}).get("name"),
        auction_house="Bonhams", seller_type="auction", image_url=sized(first_image(d.get("image")), width=640),
        extra={"auction": aid, "auction_slug": d.get("auctionSlug"),
               "department": (d.get("department") or {}).get("name"), "price_basis": "with premium"},
    )


def parse_sales(res: dict) -> dict:
    """Documents of Bonhams' "auctions" collection -> {auction id: title, slug, venue, country code}."""
    out = {}
    for hit in res.get("hits") or []:
        a = hit.get("document") or {}
        if a.get("id"):
            out[str(a["id"])] = {"title": (a.get("auctionTitle") or a.get("auctionHeading") or "").strip() or None,
                                 "slug": a.get("slug"), "venue": (a.get("venue") or {}).get("name"),
                                 "country": (a.get("country") or {}).get("code")}
    return out


class Bonhams(Source):
    kind = AUCTION

    def _key(self) -> str:
        why = "no _app script on the search page"
        try:
            html = self.ctx.fetcher.get(SITE + "/search/").text
            m = _APP.search(html)
            if m:
                key = find_key(self.ctx.fetcher.get(SITE + m.group(1)).text)
                if key:
                    return key
                why = "key pattern not found in " + m.group(1)
        except Exception as e:
            why = f"{type(e).__name__}: {e}"
        if self.cfg.get("api_key"):
            self.log.info("using the configured search key (%s)", why)
            return self.cfg["api_key"]
        raise RuntimeError(f"Bonhams search key not found ({why}) and none configured")

    def fetch(self) -> Iterator[RawRecord]:
        key, size = self._key(), int(self.cfg.get("page_size", 250))
        page, pages, ids = 1, 1, set()
        while page <= min(pages, int(self.cfg.get("max_pages", 20))):
            body = {"searches": [{"collection": "lots", "q": "ferrari", "query_by": "title",
                                  "filter_by": DEPARTMENTS, "sort_by": "hammerTime.timestamp:desc",
                                  "exclude_fields": "footnotes,catalogDesc,styledDescription,images,embedding",
                                  "page": page, "per_page": size}]}
            res = (self.ctx.fetcher.post_json(SEARCH, body, headers={"X-TYPESENSE-API-KEY": key,
                                                                      "Origin": SITE, "Referer": SITE + "/search/"})
                   .get("results") or [{}])[0]
            if res.get("error"):
                raise RuntimeError(f"Bonhams search error: {res['error']}")
            found = int(res.get("found") or 0)
            pages = -(-found // size)
            for hit in res.get("hits") or []:
                rec = map_lot(hit.get("document") or {}, self.name)
                if rec:
                    ids.add(str(rec.extra["auction"]))
                    yield rec
            page += 1
        self._save_sales(key, ids)

    def _save_sales(self, key: str, ids: set):
        """Each sale's title, venue and country, from the same search service's "auctions" collection.
        Kept in kv so that the site can name the sale of every stored result."""
        con = self.ctx.con
        if con is None:
            return
        try:
            known = json.loads(db.kv_get(con, "sales:bonhams") or "{}")
        except ValueError:
            known = {}
        todo = sorted(i for i in ids if i and i not in known)
        for n in range(0, len(todo), 100):
            chunk = todo[n:n + 100]
            body = {"searches": [{"collection": "auctions", "q": "*", "filter_by": "id: [" + ",".join(chunk) + "]",
                                  "exclude_fields": "description,images,embedding", "per_page": len(chunk)}]}
            try:
                res = (self.ctx.fetcher.post_json(SEARCH, body, headers={"X-TYPESENSE-API-KEY": key, "Origin": SITE,
                                                                          "Referer": SITE + "/search/"})
                       .get("results") or [{}])[0]
                known.update(parse_sales(res))
            except Exception as e:                       # sale names are a nice to have
                self.log.warning("could not read Bonhams sale names: %s", e)
                break
        db.kv_set(con, "sales:bonhams", json.dumps(known))
        self.log.info("%d Bonhams sales named (%d new)", len(known), len(todo))
