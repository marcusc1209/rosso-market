"""PCARMARKET.

Verified 2026-10-31 in a real browser. robots.txt allows general crawlers
(it bars named AI training bots and carries the Cloudflare content signal
search=yes, ai-train=no, which a price tracker respects). There is no JSON API:
pages are rendered on the server.

* Search: /search?q=ferrari&stage=results&page=N (24 cards a page, newest first,
  ~1,400 results, most of them parts and memorabilia) and /search?q=ferrari&stage=live.
  A card is <a href="/results/<slug>"> (or /auction/<slug> while live) around an
  <article> whose text reads: title | location | "13 Bids" | "Sold price" or
  "Bid to" or "Current bid" | "$450,000" (or "—").
* A lot page adds what the card lacks: "Ended Oct 21, 2026, 7:12 PM" (or "Ending ..."
  while live), Year, Make, Model, VIN, "Mileage Shown 22,143 mi", Seller Type.

Only cards whose title carries a model year and reads like a car are read further,
since the rest are parts and memorabilia. Lot pages are read once: a finished result
already stored is yielded again from the stored record (so the run's count stays
steady) without fetching its page, and `lot_pages` caps the pages read per run.

History: until every results page has been read once (~60 pages), each run reads
up to `history_pages` search pages. After that only the newest `result_pages`.

"Sold price" is sold (PCARMARKET prices include the buyer's fee), "Bid to $X" did
not meet reserve (unsold with a high bid), "Bid to —" is unsold with no bid.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterator, Optional

from bs4 import BeautifulSoup

import dataclasses
import json

from .base import Source
from .. import db
from ..normalize import parse_price
from ..records import RawRecord, AUCTION
from ..taxonomy import looks_like_car, other_make_first
from ..images import first_image

SITE = "https://www.pcarmarket.com"
_YEAR = re.compile(r"\b(19[4-9]\d|20[0-3]\d)\b")
_ENDED = re.compile(r"\bEnd(?:ed|ing)\s*([A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})(?:,\s*(\d{1,2}:\d{2}\s*[AP]M))?")
_FIELD = r"{label}\s*(.+?)(?=(Year|Make|Model|Trim|VIN|Mileage Shown|Transmission|Title|Exterior Color|Interior|Auction Details|Auction Type|Seller Type|Dealer Fee|Location|Zip Code|Country|Ended|Ending|Documents)\b|$)"


def parse_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.select('a[href^="/results/"], a[href^="/auction/"]'):
        art = a.find("article")
        if art is None:
            continue
        bits = [t.strip() for t in art.find_all(string=True) if t.strip()]
        label = next((b for b in bits if b in ("Sold price", "Bid to", "Current bid")), None)
        if label is None:
            continue
        after = bits[bits.index(label) + 1] if bits.index(label) + 1 < len(bits) else ""
        title = next((b for b in bits if b not in ("Sold", "Sold price", "Bid to", "Current bid", "Ends in", "—")
                      and not re.match(r"^[\d,]+ (Bids?|Offers?)$|^\$|^\d+D ", b)), "")
        bids = next((int(b.split()[0].replace(",", "")) for b in bits if re.match(r"^[\d,]+ Bids?$", b)), None)
        idx = bits.index(title) if title in bits else -1
        location = bits[idx + 1] if 0 <= idx < len(bits) - 1 and not re.match(r"^[\d,]+ Bids?$|^\$", bits[idx + 1]) else None
        img = art.find("img") or a.find("img")
        out.append({"href": a.get("href"), "title": title, "label": label, "value": after, "bids": bids, "location": location,
                    "image": first_image(img.get("src") if img else None)})
    # the same card can appear twice on a page (image link and text link)
    seen, uniq = set(), []
    for c in out:
        if c["href"] not in seen:
            seen.add(c["href"])
            uniq.append(c)
    return uniq


def _field(text: str, label: str) -> Optional[str]:
    m = re.search(_FIELD.format(label=re.escape(label)), text)
    return m.group(1).strip() if m else None


def parse_detail(html: str) -> dict:
    text = re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" "))
    text = re.sub(r"\s+([,:])", r"\1", text)          # "Oct 15 , 2026" -> "Oct 15, 2026"
    out = {}
    m = _ENDED.search(text.replace(" ,", ","))
    if m:
        for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y"):
            try:
                stamp = f"{m.group(1)} {m.group(2)}" if m.group(2) and "%I" in fmt else m.group(1)
                out["ends_at"] = datetime.strptime(stamp.replace(".", ""), fmt).replace(tzinfo=timezone.utc).isoformat()
                break
            except ValueError:
                continue
    specs = text[text.find("Car Details"):] if "Car Details" in text else text   # skip buttons like "Make Offer"
    for key, label in (("year", "Year"), ("make", "Make"), ("model", "Model"), ("vin", "VIN"),
                       ("mileage", "Mileage Shown"), ("seller_type", "Seller Type"), ("color", "Exterior Color")):
        v = _field(specs, label)
        if v:
            out[key] = v
    return out


def to_record(card: dict, detail: dict, source: str) -> Optional[RawRecord]:
    value, _ = parse_price(card["value"]) if card.get("value") not in (None, "", "—") else (None, "USD")
    if card["label"] == "Sold price":
        if not value:
            return None
        status = "sold"
    elif card["label"] == "Bid to":
        status = "unsold"
    else:
        status = "live"
    make = (detail.get("make") or "").lower()
    if make and "ferrari" not in make:
        return None
    year = detail.get("year")
    miles = None
    if detail.get("mileage"):
        miles, _ = parse_price(detail["mileage"].split(" ")[0])
    slug = card["href"].rstrip("/").split("/")[-1]
    return RawRecord(
        source=source, kind=AUCTION, source_id=slug, url=SITE + card["href"], title=card["title"],
        price=value, currency="USD", status=status, ends_at=detail.get("ends_at"),
        year=int(year) if year and year.isdigit() else None, make="Ferrari" if make else None,
        model_text=detail.get("model"), vin=detail.get("vin"), mileage=miles, exterior_color=detail.get("color"),
        bid_count=card.get("bids"), location=card.get("location"), auction_house="PCARMARKET",
        seller_type="auction", image_url=card.get("image"),
        extra={"seller_type": detail.get("seller_type"), "price_basis": "with buyer fee",
                                      "keep_undated": True},
    )


def is_car_card(title: str) -> bool:
    """A model year, no parts or memorabilia words, and no other maker named first."""
    return bool(_YEAR.search(title)) and looks_like_car(title) and not other_make_first(title)


def stored_record(raw_json: str) -> Optional[RawRecord]:
    try:
        d = json.loads(raw_json)
        names = {f.name for f in dataclasses.fields(RawRecord)}
        return RawRecord(**{k: v for k, v in d.items() if k in names})
    except Exception:
        return None


class PCarMarket(Source):
    kind = AUCTION

    def fetch(self) -> Iterator[RawRecord]:
        con = self.ctx.con
        budget = int(self.cfg.get("lot_pages", 60))
        history_done = con is None or bool(db.kv_get(con, "pcm_history_done"))
        pages = int(self.cfg.get("result_pages", 3)) if history_done else int(self.cfg.get("history_pages", 60))
        cards = parse_cards(self.ctx.fetcher.get(f"{SITE}/search?q=ferrari&stage=live").text)
        reached_end = False
        for page in range(1, pages + 1):
            got = parse_cards(self.ctx.fetcher.get(f"{SITE}/search?q=ferrari&stage=results&page={page}").text)
            if not got or (page > 1 and {c["href"] for c in got} <= {c["href"] for c in cards}):
                reached_end = True                         # past the last page (it repeats or comes back empty)
                break
            cards += got
        self.log.info("%d Ferrari cards from %d results pages, %d look like cars", len(cards), page,
                      sum(is_car_card(c["title"]) for c in cards))
        for card in cards:
            if not is_car_card(card["title"]):
                continue                                   # parts, signs, watches, models
            slug = card["href"].rstrip("/").split("/")[-1]
            if con is not None and card["label"] != "Current bid":
                row = con.execute("SELECT status, ends_at, raw FROM lots WHERE uid=?", (f"{self.name}:{slug}",)).fetchone()
                if row and row[0] in ("sold", "unsold") and row[1]:
                    rec = stored_record(row[2])            # finished result already stored with its date
                    if rec:
                        rec.image_url = rec.image_url or card.get("image")
                        yield rec
                    continue
            detail = {}
            if budget > 0:
                budget -= 1
                try:
                    detail = parse_detail(self.ctx.fetcher.get(SITE + card["href"]).text)
                except Exception as e:
                    self.log.warning("%s: %s", card["href"], e)
            elif card["label"] != "Current bid":
                continue                                   # no date without the lot page: try next run
            rec = to_record(card, detail, self.name)
            if rec:
                yield rec
        if con is not None and not history_done and reached_end and budget > 0:
            db.kv_set(con, "pcm_history_done", "1")
            self.log.info("every results page read once: daily runs now read the newest %s",
                          self.cfg.get("result_pages", 3))
