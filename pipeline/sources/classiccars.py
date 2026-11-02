"""ClassicCars.com: dealer and private listings of classic and modern Ferraris.

Verified 2026-11-01 in a real browser. robots.txt bars only /dealerhybrid,
/auction-central/ and /resource-guide/, and no bot check runs on the pages read here
(Cloudflare serves analytics only).

Search  /listings/find/all-years/ferrari?p=N   15 cards a page, ~180 Ferraris.
  Card: div.search-result-item, a link /listings/view/<id>/<slug>, .h-sri-car-title
  "1987 Ferrari 328 GTS", .mrg-b-sri-price "$132,000" or "Auction Vehicle" (a car consigned
  to an auction such as Mecum, which we read from the auction house itself: skipped).
Listing  /listings/view/<id>/<slug>
  li.p-* rows "Label:Value": Location, Model, Exterior Color, Interior Color, Odometer,
  Transmission, VIN (a chassis number on older cars). Odometer 0 means not given.
  "Listed By:" seller name, PageAdParams CompanyType Dealer or PrivateSeller and
  CountryCode, and the gallery as PhotoSwipe items src ...-std.jpg (1280x960).

A listing page is read once. Later runs reuse what was stored and only take the price and
card photo from the search page, so a normal day costs about a dozen requests.
"""
from __future__ import annotations

import html as htmlmod
import json
import re
from datetime import date
from typing import Iterator, Optional

from bs4 import BeautifulSoup

from .dealer_marketplaces import _Marketplace, _int
from .pcarmarket import stored_record
from ..records import RawRecord, LISTING
from .. import idclock, wayback

SITE = "https://classiccars.com"
SEARCH = SITE + "/listings/find/all-years/ferrari"
_ID = re.compile(r"/listings/view/(\d+)/")
_PHOTO = re.compile(r'src:\s*"(https://photos\.classiccars\.com/[^"]+?-std\.jpg)"')
_ADS = re.compile(r'id="PageAdParams"\s+value="([^"]*)"')


def parse_search(html: str) -> list[dict]:
    soup = BeautifulSoup(html or "", "html.parser")
    out = []
    for it in soup.select("div.search-result-item"):
        a = it.select_one('a[href*="/listings/view/"]')
        m = _ID.search(a.get("href", "")) if a else None
        if not m:
            continue
        title = it.select_one(".h-sri-car-title")
        price = it.select_one(".mrg-b-sri-price")
        img = it.select_one("img[data-src], img[src]")
        out.append({
            "id": m.group(1), "href": a["href"],
            "title": " ".join((title.get_text() if title else a.get("title", "")).split()),
            "price_text": " ".join(price.get_text().split()) if price else "",
            "image": (img.get("data-src") or img.get("src")) if img else None,
        })
    return out


def parse_listing(html: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    rows = {}
    for li in soup.select('li[class*="p-"]'):
        spans = li.find_all("span", recursive=False)
        if len(spans) >= 2:
            label = spans[0].get_text(" ", strip=True).rstrip(":").strip().lower()
            rows[label] = " ".join(spans[1].get_text(" ", strip=True).split())
    ads = {}
    m = _ADS.search(html or "")
    if m:
        try:
            for x in json.loads(htmlmod.unescape(m.group(1))):
                vals = x.get("TargetValues") or []
                ads[x.get("TargetName")] = vals[0] if vals else None
        except ValueError:
            pass
    seller = soup.select_one('#seller-information [aria-label="Seller or Company Name"]')
    photos = list(dict.fromkeys(_PHOTO.findall(html or "")))
    return {"rows": rows, "ads": ads, "seller": seller.get_text(" ", strip=True) if seller else None, "photos": photos}


def _clean(v: Optional[str]) -> Optional[str]:
    v = (v or "").strip()
    return None if not v or v.lower() in ("n/a", "na", "unknown", "other", "-") else v


def map_listing(card: dict, detail: dict, source: str, today: date) -> Optional[RawRecord]:
    title = card.get("title") or ""
    if "ferrari" not in title.lower():
        return None
    price = _int(card.get("price_text")) if "$" in (card.get("price_text") or "") else None
    if not price:
        return None                      # "Auction Vehicle" or no price given
    year = _int(title[:4]) if title[:4].isdigit() else None
    rows, ads = detail.get("rows") or {}, detail.get("ads") or {}
    miles = _int(rows.get("odometer"))
    if miles == 0 and not (year and year >= today.year - 1):
        miles = None                     # an odometer of 0 on an older car means it was not given
    vin = _clean(rows.get("vin"))
    if vin and not re.fullmatch(r"[A-Z0-9]{3,17}", vin.upper()):
        vin = None
    model = re.sub(r"\s*Get Collector Car Insurance.*$", "", rows.get("model") or "").strip() or None
    country = (ads.get("CountryCode") or "US").upper()
    private = (ads.get("CompanyType") or "").lower().startswith("private")
    photos = detail.get("photos") or []
    thumb = card.get("image")
    return RawRecord(
        source=source, kind=LISTING, source_id=card["id"], url=SITE + card["href"],
        title=title, price=float(price), price_text=card.get("price_text"), currency="USD",
        year=year, make="Ferrari", model_text=model, mileage=miles, mileage_unit="mi",
        vin=vin.upper() if vin else None,
        exterior_color=_clean(rows.get("exterior color")), interior_color=_clean(rows.get("interior color")),
        transmission=_clean(rows.get("transmission")), location=_clean(rows.get("location")), country=country,
        seller_type="private" if private else "dealer", dealer_name=None if private else detail.get("seller"),
        certified=False, marketplace="ClassicCars.com",
        image_url=(photos[0] if photos else thumb), extra={"photos": photos[:40], "detail": bool(rows)},
    )


class ClassicCars(_Marketplace):
    def fetch(self) -> Iterator[RawRecord]:
        cards, seen = [], set()
        for page in range(1, int(self.cfg.get("max_pages", 25)) + 1):
            got = [c for c in parse_search(self._html(SEARCH + (f"?p={page}" if page > 1 else ""))) if c["id"] not in seen]
            if not got:
                break                                   # past the last page: it repeats or comes back empty
            seen.update(c["id"] for c in got)
            cards += got
        budget = int(self.cfg.get("detail_pages", 150))
        clock = self._clock()
        dated = 0
        for rec in self._cards(cards, budget):
            # ClassicCars shows no listing date, but numbers its listings in order: the listing
            # number, read against archived captures of the home page, says when it went up
            if not rec.extra.get("listed_on") and clock is not None and rec.source_id.isdigit():
                day = clock.date_of(int(rec.source_id))
                if day:
                    rec.extra.update(listed_on=day, listed_how="listing number")
                    dated += 1
            yield rec
        if clock is not None:
            self.log.info("%d listings dated from their listing numbers (%d archive requests, %d answered)",
                          dated, clock.lk.asked, clock.lk.answered)

    def _clock(self):
        if self.ctx.con is None:
            return None
        lookups = wayback.Lookups(self.ctx.con, self.ctx.fetcher, int(self.cfg.get("wayback_lookups", 40)))
        clock = idclock.Clock(self.ctx.con, lookups, self.name, "https://classiccars.com/",
                              r"/listings/view/(\d+)/", self.ctx.run_date)
        try:
            # today's pin: the newest listings, straight from the site
            clock.pin(self.ctx.run_date.isoformat(), clock.max_id(self._html(SITE + "/listings/find")))
        except Exception as e:                       # the clock is a nicety: never fail the source for it
            self.log.info("newest listings page not read (%s)", e)
        return clock

    def _cards(self, cards, budget) -> Iterator[RawRecord]:
        read = reused = 0
        for card in cards:
            old = self._stored(card["id"])
            if old is not None and (old.extra or {}).get("detail"):
                rec = map_listing(card, {}, self.name, self.ctx.run_date)
                if rec is None:
                    continue
                for f in ("model_text", "mileage", "vin", "exterior_color", "interior_color", "transmission",
                          "location", "country", "seller_type", "dealer_name"):
                    setattr(rec, f, getattr(old, f))
                rec.extra = dict(old.extra)
                rec.image_url = old.image_url or rec.image_url
                reused += 1
                yield rec
                continue
            if not _looks_priced(card) or "ferrari" not in card["title"].lower():
                continue
            if read >= budget:
                continue                                # the rest wait for tomorrow's run
            read += 1
            rec = map_listing(card, parse_listing(self._html(SITE + card["href"])), self.name, self.ctx.run_date)
            if rec:
                yield rec
        self.log.info("%d cards, %d listing pages read, %d reused from earlier runs", len(cards), read, reused)

    def _stored(self, sid: str) -> Optional[RawRecord]:
        con = self.ctx.con
        if con is None:
            return None
        row = con.execute("SELECT raw FROM sightings WHERE uid=?", (f"{self.name}:{sid}",)).fetchone()
        return stored_record(row[0]) if row and row[0] else None


def _looks_priced(card: dict) -> bool:
    return "$" in (card.get("price_text") or "")
