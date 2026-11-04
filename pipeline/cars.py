"""One page of data per car, the way classic.com has a page per vehicle.

A car is every record we hold for one physical vehicle:

* records that share a VIN (vin:...) or a chassis number (chassis:...) are the same car,
  wherever they came from: a dealer listing, Ferrari Approved, an auction result;
* a dealer listing without a VIN keeps the fingerprint the deduplicator gave it
  (model, year, mileage band, colour, seller), so the same car on two sites is one car;
* an auction lot without a VIN or chassis stands alone. Fingerprints are too loose to
  join auctions (two 1973 365 GT4 2+2s sold by RM in different years share one).

Each car gets data/cars/<id>.json: photos, specs, where it is now, every event we have
seen for it (listed, repriced, delisted, offered, sold, not sold) and recent sales of
the same model. The model pages and the site's cards link to it by id.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from . import clean, sales, taxonomy

STRONG = ("vin:", "chassis:")
AGGREGATORS = {"cars.com", "autotrader.com", "cargurus.com", "cargurus", "carfax", "edmunds", "dupont registry"}


def car_id(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def group_key(vehicle_key: Optional[str], uid: str, kind: str) -> str:
    vk = vehicle_key or ""
    if vk.startswith(STRONG):
        return vk
    if kind == "listing" and vk:
        return vk
    return ("lot:" if kind == "lot" else "s:") + uid


def _raw(text) -> dict:
    try:
        return json.loads(text or "{}")
    except (ValueError, TypeError):
        return {}             # very long records were cut when stored


def _photos(raw: dict, image: Optional[str]) -> list[str]:
    out = [u for u in ((raw.get("extra") or {}).get("photos") or []) if isinstance(u, str)]
    if image and not out:
        out = [image]
    return out


def _round(x):
    return None if x is None else round(x)


class Index:
    """Maps the site's rows to car ids: lots and sightings by uid, listings by vehicle key."""

    def __init__(self):
        self.by_uid: dict[str, str] = {}
        self.by_vkey: dict[str, str] = {}

    def lot(self, uid: str) -> Optional[str]:
        return self.by_uid.get(uid)

    def listing(self, vehicle_key: str) -> Optional[str]:
        return self.by_vkey.get(vehicle_key)


def build(con, day: date, stats: dict) -> tuple[dict[str, dict], Index]:
    today = day.isoformat()
    skip = clean.implausible_lots(con)
    cars: dict[str, dict] = defaultdict(lambda: {"listings": [], "lots": []})
    idx = Index()

    prices = defaultdict(list)
    for uid, d, p in con.execute("SELECT uid, day, price_usd FROM sighting_prices ORDER BY uid, day"):
        prices[uid].append((d, p))

    # every car we have ever seen keeps its page: a car that sold or left the market drops off
    # the lists but stays findable by its VIN in the site's search
    for r in con.execute("SELECT * FROM sightings WHERE model_id IS NOT NULL"):
        k = group_key(r["vehicle_key"], r["uid"], "listing")
        cars[k]["listings"].append(r)
        cid = car_id(k)
        idx.by_uid[r["uid"]] = cid
        if r["vehicle_key"]:
            idx.by_vkey[r["vehicle_key"]] = cid
    for r in con.execute("SELECT * FROM lots WHERE model_id IS NOT NULL"):
        if r["uid"] in skip:
            continue
        k = group_key(r["vehicle_key"], r["uid"], "lot")
        cars[k]["lots"].append(r)
        idx.by_uid[r["uid"]] = car_id(k)

    # recent sales of each model, for the "comparable sales" strip
    comps = defaultdict(list)
    for mid in {r[0] for r in con.execute("SELECT DISTINCT model_id FROM lots WHERE model_id IS NOT NULL")}:
        for r in con.execute("""SELECT uid, title, image_url, price_usd, ends_at, auction_house, year, mileage_mi,
                                       source, url, country, location, raw FROM lots
                                WHERE status='sold' AND price_usd > 0 AND ends_at IS NOT NULL AND model_id=?
                                AND substr(ends_at,1,10) <= ? ORDER BY ends_at DESC LIMIT 14""", (mid, today)):
            if r["uid"] in skip:
                continue
            sl = sales.describe(r)
            comps[mid].append({"id": idx.by_uid.get(r["uid"]), "title": r["title"], "image": r["image_url"],
                               "price": _round(r["price_usd"]), "date": r["ends_at"][:10], "house": r["auction_house"],
                               "mileage": r["mileage_mi"], "sale": sl["sale"], "country": sl["country"]})

    out = {}
    for key, g in cars.items():
        page = _page(key, g, prices, comps, stats, today)
        if page:
            out[page["id"]] = page
    return out, idx


def _page(key: str, g: dict, prices: dict, comps: dict, stats: dict, today: str) -> Optional[dict]:
    listings, lots = g["listings"], g["lots"]
    if not listings and not lots:
        return None
    events, photos, raws = [], [], []
    latest = None                        # (date, row, raw) of the newest record, for title and specs

    for r in listings:
        raw = _raw(r["raw"])
        ph = _photos(raw, r["image_url"])
        series = prices.get(r["uid"], [])
        extra = raw.get("extra") or {}
        # the marketplace's own price history (AutoTrader keeps one) comes before what we observed
        before = [(d, p) for d, p in (extra.get("price_history") or [])
                  if isinstance(p, (int, float)) and (not series or d < series[0][0])]
        changes, last = [], None
        for i, (d, p) in enumerate([*before, *series]):
            # where the earlier history meets what we observed, a gap under 1% is the two sites
            # rounding the same price differently, not a price change
            if before and i == len(before) and last and p and abs(p - last) / last < 0.01:
                last = p
                continue
            if p and p != last:
                changes.append([d, _round(p)])
                last = p
        active = r["removed_on"] is None
        listed = min(x for x in [r["first_seen"], extra.get("listed_on")] if x) if (r["first_seen"] or extra.get("listed_on")) else None
        # where the date came from, when it is not the day we first saw the car
        how = (extra.get("listed_how") or "the listing site") if extra.get("listed_on") and listed == extra.get("listed_on") \
            and listed != r["first_seen"] else None
        seller = r["dealer_name"] or r["marketplace"] or r["source"]
        events.append({
            "date": listed, "end": None if active else (r["removed_on"] or r["last_seen"]),
            "kind": "listed" if active else "delisted",
            "label": ("Listed by " if active else "Previously listed by ") + seller,
            "price": changes[-1][1] if changes else None, "price_label": "Asking" if active else "Last asking",
            "where": r["marketplace"] or r["source"], "seller": seller, "url": r["url"],
            "mileage": r["mileage_mi"], "location": r["location"], "photo": ph[0] if ph else None, "n_photos": len(ph),
            "certified": bool(r["certified"]), "changes": changes if len(changes) > 1 else [],
            "date_how": how,
            "country": sales.listing_country(r["country"], r["location"]),
        })
        photos = photos or ph
        stamp = r["last_seen"] or r["first_seen"] or ""
        raws.append((stamp, raw))
        if latest is None or stamp > latest[0]:
            latest = (stamp, r, raw)

    for r in lots:
        raw = _raw(r["raw"])
        ph = _photos(raw, r["image_url"])
        st = r["status"]
        house = r["auction_house"] or r["source"]
        label = {"sold": "Sold at ", "unsold": "Not sold at ", "live": "Live at ", "upcoming": "Coming up at "}.get(st, "Offered at ") + house
        plabel = {"sold": "Sold for", "unsold": "High bid", "live": "Current bid", "upcoming": "Estimate"}.get(st, "Price")
        d = (r["ends_at"] or "")[:10] or r["first_seen"]
        sl = sales.describe(r)
        events.append({
            "date": d, "end": None, "kind": st, "label": label, "price": _round(r["price_usd"]), "price_label": plabel,
            "estimate": [_round(r["estimate_low_usd"]), _round(r["estimate_high_usd"])] if r["estimate_low_usd"] else None,
            "where": house, "seller": house, "url": sales.lot_url(r), "mileage": r["mileage_mi"], "location": r["location"],
            "sale": sl["sale"], "sale_url": sl["sale_url"], "country": sl["country"], "online": sl["online"],
            "lot_number": r["lot_number"], "bids": r["bid_count"], "ends_at": r["ends_at"], "no_reserve": r["no_reserve"],
            "photo": ph[0] if ph else None, "n_photos": len(ph),
        })
        if len(ph) > len(photos) or (st in ("live", "upcoming") and ph):
            photos = ph
        stamp = d or ""
        raws.append((stamp, raw))
        if latest is None or stamp > latest[0]:
            latest = (stamp, r, raw)

    events = _merge_listings(events)
    events.sort(key=lambda e: (e["date"] or "", e["kind"] in ("listed", "live", "upcoming")), reverse=True)
    _, row, raw = latest
    model = taxonomy.BY_ID.get(row["model_id"])
    mid = row["model_id"]

    # where the car is now: live or upcoming auction, then for sale, then the last result
    now = next((e for e in events if e["kind"] in ("live", "upcoming")), None) \
        or next((e for e in events if e["kind"] == "listed"), None) \
        or next((e for e in events if e["kind"] in ("sold", "unsold")), None) \
        or events[0]
    status = {"listed": "for_sale", "delisted": "off_market"}.get(now["kind"], now["kind"])

    vin = next((x["vin"] for x in [*listings, *lots] if x["vin"]), None)
    raws.sort(key=lambda t: t[0], reverse=True)
    pick = lambda k: next((rw.get(k) for _, rw in raws if rw.get(k)), None)     # newest record that has it
    mileage = next((e["mileage"] for e in events if e["mileage"]), None)
    spec = [
        ("Year", row["year"]), ("Model", model.name if model else None), ("VIN", vin),
        ("Chassis", key.split(":")[-1] if key.startswith("chassis:") else None),
        ("Mileage", f"{round(mileage):,} mi" if mileage else None),
        ("Exterior", pick("exterior_color")), ("Interior", pick("interior_color")),
        ("Transmission", pick("transmission")), ("Location", now.get("location")),
        ("Lot", now.get("lot_number")),
    ]
    s = stats.get(mid) or {}
    return {
        "id": car_id(key), "title": row["title"], "year": row["year"],
        "model": {"id": mid, "name": model.name if model else mid, "family": model.family if model else None},
        "vin": vin, "photos": photos[:60], "status": status,
        "price": now["price"], "price_label": now["price_label"], "estimate": now.get("estimate"),
        "date": now["date"], "ends_at": now.get("ends_at"), "url": now["url"], "where": now["where"], "seller": now["seller"],
        "sale": now.get("sale"), "sale_url": now.get("sale_url"), "country": now.get("country"),
        "location": now.get("location"), "certified": now.get("certified", False), "no_reserve": now.get("no_reserve"),
        "specs": [[k, v] for k, v in spec if v not in (None, "")],
        "history": events,
        "comps": [c for c in comps.get(mid, []) if c["id"] != car_id(key)][:8],
        "market": {"median_ask": _round(s.get("median")), "listings": s.get("listings")},
    }


def _merge_listings(events: list[dict]) -> list[dict]:
    """One dealer listing the car on its own site, cars.com and AutoTrader is one event,
    with the sites it was seen on. The dealer's own page (or Ferrari Approved) is the link."""
    out, groups = [], defaultdict(list)
    for e in events:
        if e["kind"] in ("listed", "delisted"):
            groups[(e["seller"] or "").lower(), e["kind"]].append(e)
        else:
            out.append(e)
    for evs in groups.values():
        evs.sort(key=lambda e: (len(e["changes"]), e["n_photos"]), reverse=True)
        best = dict(evs[0])
        # link to the dealer's own page or Ferrari Approved rather than an aggregator
        own = next((e for e in evs if (e["where"] or "").lower() not in AGGREGATORS), None)
        if own:
            best["url"], best["where"] = own["url"], own["where"]
        first = min(evs, key=lambda e: e["date"] or "9999")
        best["date"], best["date_how"] = first["date"], first.get("date_how")
        ends = [e["end"] for e in evs if e["end"]]
        best["end"] = max(ends) if ends else None
        best["seen_on"] = [{"where": e["where"], "url": e["url"]} for e in evs]
        best["photo"] = next((e["photo"] for e in evs if e["photo"]), None)
        best["n_photos"] = max(e["n_photos"] for e in evs)
        best["certified"] = any(e["certified"] for e in evs)
        out.append(best)
    return out


STATUS_WORD = {"for_sale": "For sale", "off_market": "No longer listed", "sold": "Sold", "unsold": "Not sold",
               "live": "Live auction", "upcoming": "Upcoming auction"}


def search_index(pages: dict[str, dict]) -> list[list]:
    """[VIN or chassis number, car id, title, status, price] for every car that has one,
    including cars no longer for sale. The site's search box loads it on first use."""
    out = []
    for cid, p in pages.items():
        chassis = next((v for k, v in p.get("specs") or [] if k == "Chassis"), None)
        key = re.sub(r"[^A-Z0-9]", "", str(p.get("vin") or chassis or "").upper())
        if len(key) >= 4:
            out.append([key, cid, p.get("title"), STATUS_WORD.get(p.get("status"), p.get("status")), p.get("price")])
    out.sort(key=lambda r: r[0])
    return out


def write(pages: dict[str, dict], site_dir: Path) -> int:
    d = site_dir / "data" / "cars"
    if d.exists():
        shutil.rmtree(d)            # cars that left the data must not linger
    d.mkdir(parents=True)
    for cid, page in pages.items():
        (d / f"{cid}.json").write_text(json.dumps(page, separators=(",", ":"), default=str), encoding="utf-8")
    (site_dir / "data" / "search.json").write_text(json.dumps(search_index(pages), separators=(",", ":"), default=str),
                                                   encoding="utf-8")
    return len(pages)
