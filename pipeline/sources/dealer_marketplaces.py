"""Dealer marketplaces: AutoTrader (US) and duPont Registry.

Both verified 2026-11-01 in a real browser. Each one's robots.txt allows
general crawlers on the search pages read here, and the adapters read only those
pages (no APIs, no listing detail pages). A bot challenge is never worked around:
it raises Blocked and the source reports itself down.

AutoTrader.com
  /cars-for-sale/ferrari?searchRadius=0&listingTypes=USED,CERTIFIED&numRecords=100
     &firstRecord=N&sortBy=derivedpriceASC              (~2,300 used Ferraris nationwide)
  The page is a Next.js app. Its data rides in self.__next_f.push([1,"..."]) chunks;
  joined, they hold "inventory": {id: listing} and "owners": {id: dealer} objects and
  "totalResultCount". A listing has vin, year, title, mileage.value ("11,741"),
  color.exteriorColor/interiorColor, specifications.transmission.value,
  pricingDetail.salePrice (before dealer fees), pricingHistory [{dateUpdated
  "10.02.2026", price "$$303,126"}], daysOnSite, ownerId/ownerName, images.sources[].src.
  Each page also carries ~25 sponsored listings from elsewhere in the results: kept
  once by id. Photos resize on images.autotrader.com/scaler/<w>/<h>/<path>.

duPont Registry  (dealer marketplace for exotic cars; not duPont Registry Live auctions)
  /used-ferrari-for-sale?page=N (~20 a page, ~1,800 cars). Each page has a schema.org
  ItemList in <script data-srp-structured-data>: Car items with vehicleIdentificationNumber,
  mileageFromOdometer, model, vehicleModelDate, image, offers.price and
  offers.seller (AutoDealer with address), additionalProperty Certified.
  robots.txt disallows only /api/ and /admin.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

from bs4 import BeautifulSoup

from .base import Source
from .. import db
from ..fetch import Blocked
from ..records import RawRecord, LISTING

_PUSH = "self.__next_f.push([1,"
_NUM = re.compile(r"[^\d.]")


def _int(text) -> Optional[int]:
    s = _NUM.sub("", str(text or ""))
    try:
        return int(float(s)) if s else None
    except ValueError:
        return None


def _private(name: str) -> bool:
    return bool(re.search(r"\bprivate\b", name or "", re.I))


class _Marketplace(Source):
    """Plain request first. If the site refuses it, a real browser for the rest of the run."""
    kind = LISTING
    use_browser = False

    def _stored_extra(self, sid: str) -> dict:
        """What an earlier run stored for this listing (its listing date, once read)."""
        con = self.ctx.con
        if con is None:
            return {}
        row = con.execute("SELECT raw FROM sightings WHERE uid=?", (f"{self.name}:{sid}",)).fetchone()
        try:
            return (json.loads(row[0]).get("extra") or {}) if row and row[0] else {}
        except (ValueError, TypeError):
            return {}

    def _with_listing_dates(self, recs, read_date) -> Iterator[RawRecord]:
        """The search pages carry no listing date, each car's own page does. Every car's page is
        read once; the date is kept with the listing so later runs never read it again.
        `date_pages` caps how many pages one run reads (the rest wait for the next run)."""
        budget, read, known, stop = int(self.cfg.get("date_pages", 400)), 0, 0, False
        # every page read is remembered here too: cars the pipeline rejects (not a Ferrari, no
        # model match) are never stored as listings, and would otherwise be read again each run
        con, key = self.ctx.con, f"listed:{self.name}"
        seen = json.loads(db.kv_get(con, key) or "{}") if con is not None else {}
        for rec in recs:
            old = self._stored_extra(rec.source_id)
            if not (old.get("listed_on") or old.get("listed_unknown")) and rec.source_id in seen:
                old = {"listed_on": seen[rec.source_id]} if seen[rec.source_id] else {"listed_unknown": True}
            if old.get("listed_on") or old.get("listed_unknown"):
                rec.extra.update({k: old[k] for k in ("listed_on", "listed_unknown") if k in old})
                known += 1
            elif not stop and read < budget and rec.url:
                read += 1
                try:
                    day = read_date(self._html(rec.url))
                except Blocked as e:
                    self.log.info("car pages refused (%s): listing dates wait for the next run", e)
                    stop = True
                    day = None
                except Exception as e:                    # one bad page never stops the source
                    self.log.debug("no listing date from %s: %s", rec.url, e)
                    day = None
                if day:
                    rec.extra["listed_on"] = day
                    seen[rec.source_id] = day
                elif not stop:
                    rec.extra["listed_unknown"] = True    # read, and the page has no date
                    seen[rec.source_id] = ""
            yield rec
        if con is not None:
            db.kv_set(con, key, json.dumps(seen))
        self.log.info("listing dates: %d pages read, %d already known", read, known)

    def _html(self, url: str) -> str:
        if not self.use_browser:
            try:
                return self.ctx.fetcher.get(url).text
            except Blocked as e:
                self.log.info("plain request refused (%s), switching to the browser", e)
                self.use_browser = True
        time.sleep(float(self.cfg.get("browser_pause_seconds", 3)))
        return self.ctx.browser.html(url)


# ------------------------------------------------------------------ AutoTrader.com

AT_SITE = "https://www.autotrader.com"
AT_SEARCH = (AT_SITE + "/cars-for-sale/ferrari?searchRadius=0&listingTypes=USED,CERTIFIED"
             "&numRecords={n}&firstRecord={first}&sortBy=derivedpriceASC")
# The listings feed the site's own pages use (checked 2026-11-07 in a real browser: 2,356 Ferraris nationwide, the
# same listing objects as the search page). robots.txt does not disallow /rest/lsc/. The search pages are refused to
# automated readers by Akamai, which is why the HTML route stopped on 2026-11-01.
AT_API = (AT_SITE + "/rest/lsc/listing?makeCode=FER&searchRadius=0&listingTypes=USED,CERTIFIED"
          "&numRecords={n}&firstRecord={first}&sortBy=derivedpriceASC")


def flight_data(html: str) -> str:
    """Join the Next.js flight chunks into one text."""
    out, dec, html, i = [], json.JSONDecoder(strict=False), html or "", 0
    while True:
        i = html.find(_PUSH, i)
        if i < 0:
            return "".join(out)
        i += len(_PUSH)
        try:
            chunk, i = dec.raw_decode(html, i)
        except ValueError:
            continue
        if isinstance(chunk, str):
            out.append(chunk)


def _object_after(text: str, key: str):
    i = text.find(f'"{key}":{{')
    if i < 0:
        return None
    try:
        return json.JSONDecoder(strict=False).raw_decode(text, i + len(key) + 3)[0]
    except ValueError:
        return None


def parse_autotrader(html: str) -> tuple[dict, dict, Optional[int]]:
    """(inventory by id, owners by id, total results)."""
    f = flight_data(html)
    total = re.search(r'"totalResultCount":(\d+)', f)
    return _object_after(f, "inventory") or {}, _object_after(f, "owners") or {}, int(total.group(1)) if total else None


def _snippet(text: str) -> str:
    """What came back instead of data: its title, or its first words."""
    t = re.search(r"<title>([^<]{0,100})", text or "", re.I)
    return t.group(1).strip() if t else re.sub(r"\s+", " ", (text or "")[:160])


def _at_photo(src: str, w: int, h: int) -> str:
    return re.sub(r"^https://images\.autotrader\.com/(?!scaler/)", f"https://images.autotrader.com/scaler/{w}/{h}/", src)


def map_autotrader(a: dict, owners: dict, source: str, today) -> Optional[RawRecord]:
    if "ferrari" not in str((a.get("make") or {}).get("name") or a.get("title") or "").lower():
        return None
    if str(a.get("listingType") or "").upper() == "NEW":
        return None
    p = a.get("pricingDetail") or {}
    price = p.get("salePrice") or p.get("preFeeDerivedPrice") or p.get("displayPrice") or None
    owner = owners.get(str(a.get("ownerId"))) or {}
    addr = ((owner.get("location") or {}).get("address")) or {}
    name = a.get("ownerName") or owner.get("name") or ""
    spec = a.get("specifications") or {}
    color = a.get("color") or {}
    history = []
    for h in a.get("pricingHistory") or []:
        d, v = str(h.get("dateUpdated") or ""), _int(h.get("price"))
        m = re.match(r"(\d\d)\.(\d\d)\.(\d{4})$", d)
        iso = f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else today.isoformat()
        if v:
            history.append([iso, v])
    srcs = [s.get("src") for s in ((a.get("images") or {}).get("sources") or []) if s.get("src")]
    days = a.get("daysOnSite")
    title = re.sub(r"^(Used|Certified)\s+", "", str(a.get("title") or "")).strip()
    return RawRecord(
        source=source, kind=LISTING, source_id=str(a.get("id")), url=f"{AT_SITE}/cars-for-sale/vehicle/{a.get('id')}",
        title=title, price=float(price) if price else None, currency="USD",
        year=a.get("year"), make="Ferrari", model_text=((a.get("model") or {}).get("name")),
        mileage=_int((a.get("mileage") or {}).get("value")), mileage_unit="mi",
        vin=(a.get("vin") or "").strip() or None,
        exterior_color=color.get("exteriorColor") or (spec.get("color") or {}).get("value"),
        interior_color=color.get("interiorColor") or (spec.get("interiorColor") or {}).get("value"),
        transmission=(spec.get("transmission") or {}).get("value"),
        location=", ".join(x for x in [addr.get("city"), addr.get("state")] if x) or None, country="US",
        seller_type="private" if _private(name) else "dealer", dealer_name=name or None,
        certified=False,       # AutoTrader "certified" is any brand's CPO; Ferrari Approved itself sets that flag
        marketplace="autotrader.com",
        image_url=_at_photo(srcs[0], 640, 480) if srcs else None,
        extra={"listed_on": (today - timedelta(days=int(days))).isoformat() if isinstance(days, int) else None,
               "price_history": history, "photos": [_at_photo(s, 1280, 960) for s in srcs[:40]],
               "stock": a.get("stockId")},
    )


class AutoTrader(_Marketplace):
    def _feed(self, url: str) -> Optional[dict]:
        """The listings feed: a plain request first, then the same request from inside the site's page in a real
        browser. If both are refused the run stops there: a refusal is not worked around."""
        if not self.use_browser:
            try:
                # ask for JSON, as the site's own pages do: without it the feed may answer with a page
                r = self.ctx.fetcher.get(url, headers={"Accept": "application/json, text/plain, */*"})
                try:
                    data = r.json()
                except ValueError:
                    data = None
                    self.log.info("plain request answered %s %s, not the feed: %r", r.status_code,
                                  r.headers.get("content-type"), _snippet(r.text))
                if isinstance(data, dict) and "listings" in data:
                    return data
            except Exception as e:
                self.log.info("plain request refused (%s)", e)
            self.log.info("switching to the browser")
            self.use_browser = True
        time.sleep(float(self.cfg.get("browser_pause_seconds", 3)))
        try:
            data = self.ctx.browser.json_in_page(AT_SITE + "/cars-for-sale/all-cars/ferrari", url)
        except Exception as e:
            self.log.warning("listings feed refused in the browser too (%s)", e)
            return None
        return data if isinstance(data, dict) and "listings" in data else None

    def fetch(self) -> Iterator[RawRecord]:
        if self.cfg.get("feed", True):
            yield from self._fetch_feed()
        else:
            yield from self._fetch_pages()

    def _fetch_feed(self) -> Iterator[RawRecord]:
        size = int(self.cfg.get("page_size", 100))
        first, total, seen = 0, None, set()
        for _ in range(int(self.cfg.get("max_pages", 40))):
            data = self._feed(AT_API.format(n=size, first=first))
            if not data:
                break
            rows = data.get("listings") or []
            owners = {str(o.get("id")): o for o in data.get("owners") or [] if isinstance(o, dict)}
            total = data.get("totalResultCount", total)
            for a in rows:
                if str(a.get("id")) in seen:
                    continue
                seen.add(str(a.get("id")))
                rec = map_autotrader(a, owners, self.name, self.ctx.run_date)
                if rec:
                    yield rec
            first += size
            if not rows or total is None or first >= int(total):
                break
        self.log.info("%d listings read of %s", len(seen), total)

    def _fetch_pages(self) -> Iterator[RawRecord]:
        size = int(self.cfg.get("page_size", 100))
        first, total, seen = 0, None, set()
        cap = int(self.cfg.get("max_pages", 40))
        for _ in range(cap):
            url = AT_SEARCH.format(n=size, first=first)
            html = self._html(url)
            inv, owners, n = parse_autotrader(html)
            if not inv and not self.use_browser:
                # Seen 2026-11-01 from the Mac: a plain request got status 200 and a page without
                # the listings data. A real browser gets the full page, so switch for the rest of the run.
                title = re.search(r"<title>([^<]{0,80})", html or "")
                self.log.info("plain request returned no listings (%d bytes, title %r), switching to the browser",
                              len(html or ""), title.group(1) if title else None)
                self.use_browser = True
                html = self._html(url)
                inv, owners, n = parse_autotrader(html)
            total = n if n is not None else total
            if not inv:
                title = re.search(r"<title>([^<]{0,80})", html or "")
                self.log.warning("no listings at offset %d (%d bytes, title %r)", first, len(html or ""),
                                 title.group(1) if title else None)
                break
            for a in inv.values():
                if str(a.get("id")) in seen:
                    continue                       # sponsored listings repeat on every page
                seen.add(str(a.get("id")))
                rec = map_autotrader(a, owners, self.name, self.ctx.run_date)
                if rec:
                    yield rec
            first += size
            if total is None or first >= total:
                break
        self.log.info("%d listings read of %s", len(seen), total)


# ------------------------------------------------------------------ duPont Registry

DR_SITE = "https://www.dupontregistry.com"


def parse_dupont(html: str) -> list[dict]:
    soup = BeautifulSoup(html or "", "html.parser")
    tag = soup.select_one("script[data-srp-structured-data]")
    if tag is None:
        return []
    try:
        data = json.loads(tag.string or tag.get_text())
    except ValueError:
        return []
    return [x.get("item") or {} for x in data.get("itemListElement") or []]


def map_dupont_registry(it: dict, source: str) -> Optional[RawRecord]:
    url = it.get("url") or it.get("@id") or ""
    if "ferrari" not in (str(it.get("name")) + url).lower():
        return None
    offer = it.get("offers") or {}
    seller = offer.get("seller") or {}
    addr = seller.get("address") or {}
    miles = (it.get("mileageFromOdometer") or {}).get("value")
    unit = "km" if (it.get("mileageFromOdometer") or {}).get("unitCode") == "KMT" else "mi"
    props = {p.get("name"): p.get("value") for p in it.get("additionalProperty") or [] if isinstance(p, dict)}
    img = it.get("image")
    img = img[0] if isinstance(img, list) and img else img
    price = offer.get("price")
    cond = str(offer.get("itemCondition") or "")
    if "NewCondition" in cond:
        return None
    return RawRecord(
        source=source, kind=LISTING, source_id=url.rstrip("/").split("/")[-1] or url, url=url,
        title=str(it.get("name") or "").strip(), price=float(price) if price else None,
        currency=(offer.get("priceCurrency") or "USD").upper(),
        year=_int(it.get("vehicleModelDate")), make="Ferrari", model_text=it.get("model"),
        mileage=_int(miles), mileage_unit=unit, vin=(it.get("vehicleIdentificationNumber") or "").strip() or None,
        exterior_color=None if str(it.get("color") or "").lower() in ("", "other") else it.get("color"),
        interior_color=it.get("vehicleInteriorColor"),
        location=", ".join(x for x in [addr.get("addressLocality"), addr.get("addressRegion")] if x) or None,
        country=addr.get("addressCountry") or "US",
        seller_type="private" if _private(seller.get("name")) else "dealer", dealer_name=seller.get("name"),
        certified=False, marketplace="duPont Registry",
        image_url=img if isinstance(img, str) else None,
        extra={"photos": [img] if isinstance(img, str) else [], "dealer_certified": bool(props.get("Certified"))},
    )


_DR_LISTED = re.compile(r'Date Listed\\?"\s*,\s*\\?"value\\?"\s*:\s*\\?"?(\d{10,13})')


def dupont_listed_on(html: str) -> Optional[str]:
    """A duPont Registry car page names its listing date as an additionalProperty
    {"name": "Date Listed", "value": "<epoch milliseconds>"} (sometimes inside escaped JSON)."""
    m = _DR_LISTED.search(html or "")
    if not m:
        return None
    ts = int(m.group(1))
    return datetime.fromtimestamp(ts / 1000 if ts > 1e11 else ts, tz=timezone.utc).date().isoformat()


class DupontRegistry(_Marketplace):
    def fetch(self) -> Iterator[RawRecord]:
        yield from self._with_listing_dates(self._search(), dupont_listed_on)

    def _search(self) -> Iterator[RawRecord]:
        seen = set()
        for page in range(1, int(self.cfg.get("max_pages", 150)) + 1):
            url = f"{DR_SITE}/used-ferrari-for-sale" + (f"?page={page}" if page > 1 else "")
            items = parse_dupont(self._html(url))
            new = 0
            for it in items:
                rec = map_dupont_registry(it, self.name)
                if rec and rec.source_id not in seen:
                    seen.add(rec.source_id)
                    new += 1
                    yield rec
            if not new:
                break                              # past the last page: it repeats or comes back empty
        self.log.info("%d listings read", len(seen))
