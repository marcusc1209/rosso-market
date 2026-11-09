"""Write the JSON the website reads: site/data/site.json plus one file per model."""
from __future__ import annotations

import json
import statistics as st
import re
from datetime import date, timedelta
from pathlib import Path

from . import analytics as A
from . import cars, clean, colors, fairvalue, mileage, pulse, sales, taxonomy, weekly


def _takes(content_dir: Path) -> list[dict]:
    out = []
    for p in sorted((content_dir / "takes").glob("*.md"), reverse=True):
        text = p.read_text(encoding="utf-8")
        m = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
        if not m:
            continue
        meta = {}
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"')
        body = m.group(2).strip()
        meta["slug"] = p.stem
        meta["summary"] = meta.get("summary") or body.split("\n\n")[0]
        meta["body"] = body
        meta["sample"] = meta.get("sample", "false").lower() == "true"
        out.append(meta)
    return out


def _weekly(series: list[dict], key: str) -> list:
    """Thin a daily series to one point per week (plus the last day) to keep the JSON small."""
    if not series:
        return []
    out = series[::7]
    if out[-1] is not series[-1]:
        out.append(series[-1])
    return [[p["day"], p[key]] for p in out]


def build(con, day: date, cfg: dict, health: list[dict]) -> dict:
    stats = A.model_stats(con, day)
    mv = A.movers(stats)
    covers = A.cover_photos(con)
    mm = A.market_movers(con, day, stats, covers)
    ovn = A.overnight(con, day)
    auc = A.auctions(con, day, stats)
    ins = A.insights(stats, auc, day, A.certified_split(con, day))
    history_days = con.execute("SELECT COUNT(DISTINCT day) FROM daily_asks WHERE day <= ?", (day.isoformat(),)).fetchone()[0]
    br = A.brief(stats, mv, ovn, auc, history_days)
    index = A.market_index(con, day, 90)
    sample = bool(con.execute("SELECT 1 FROM sightings WHERE sample=1 AND removed_on IS NULL LIMIT 1").fetchone()
                  or con.execute("SELECT 1 FROM lots WHERE sample=1 LIMIT 1").fetchone())

    au = auction_stats(con, day)
    models = []
    for m in taxonomy.MODELS:
        s, a = stats.get(m.id), au.get(m.id)
        if not s and not a:
            continue
        if not s:
            # no cars for sale right now: the model still gets a page from its auction record
            s = dict(id=m.id, name=m.name, family=m.family, body=m.body, tracked=m.tracked, pair=m.pair, listings=0,
                     median=None, p25=None, p75=None, low=None, high=None, median_7d_ago=None, change_7d=None,
                     median_30d_ago=None, change_30d=None, same_cars=0, cut_7d=0, raised_7d=0, new_7d=0, days_on_market=None)
        hist = A.history(con, m.id, day, 90)
        models.append(dict(s, spark=[p["median"] for p in hist[::3]][-30:], history=_weekly(hist, "median"),
                           sell_through=auc["sell_through"].get(m.id), auction=a or {}))

    families = []
    for fam in taxonomy.FAMILY_ORDER:
        ms = [x for x in models if x["family"] == fam]
        if not ms:
            continue
        n = sum(x["listings"] for x in ms)
        sold_12m = sum((x.get("auction") or {}).get("sold_12m", 0) for x in ms)
        movers_in = [x for x in mv if x["family"] == fam]
        if movers_in:
            top = movers_in[0]
            signal = f"{top['name']} asks {'softening' if top['direction'] == 'down' else 'firming'} this week"
            change = top["change_7d"]
        elif n:
            signal, change = "Asks mostly steady across tracked models this week", None
        else:
            signal, change = f"{sold_12m:,} sold at auction in the last 12 months", None
        families.append({"name": fam, "listings": n, "sold_12m": sold_12m, "models": [x["id"] for x in ms], "signal": signal, "change": change,
                         "slug": re.sub(r"[^a-z0-9]+", "-", fam.lower()).strip("-")})

    news = [dict(r) for r in con.execute("SELECT title, url, outlet, published, image FROM news ORDER BY published DESC LIMIT 12")]
    # Every insight and headline gets a picture: its own, or the model's cover photo
    for i in ins:
        if not i.get("image") and covers.get(i.get("model")):
            i["image"] = covers[i["model"]]
    names = sorted(((m.name, m.id) for m in taxonomy.MODELS if m.id in covers), key=lambda x: -len(x[0]))
    fallback = covers.get(next((m["id"] for m in sorted(models, key=lambda m: -m["listings"]) if covers.get(m["id"])), None))
    for n in news:
        if not n.get("image"):
            t = " " + (n["title"] or "").lower() + " "
            hit = next((mid for name, mid in names if f" {name.lower()} " in t or f" {name.lower()}," in t), None)
            n["image"] = covers.get(hit) or fallback

    return {
        "brand": "Rosso Market",
        "as_of": day.isoformat(),
        "region": "US market, hypercars worldwide",
        "sample": sample,
        "brief": br,
        "movers": mv,
        "market_movers": mm,
        "covers": covers,
        "models": models,
        "families": families,
        "index": [[p["day"], p["index"]] for p in index],
        "insights": ins,
        "auctions": {
            "live": auc["live"][:60],
            "upcoming": auc["upcoming"][:30],
            "results": auc["results"][:80],
            "houses": auc["houses"],
        },
        "takes": _takes(Path(cfg["settings"].get("content_dir", "content"))),
        "news": news,
        "health": health,
        "coverage": coverage(con, cfg, models),
        "rules": {"mover_min_pct": A.MOVER_MIN_PCT, "mover_min_cars": A.MOVER_MIN_CARS},
    }


def auction_stats(con, day: date) -> dict:
    """Per model auction record: what sold and for how much over the last 12 months."""
    since, since_prev = (day - timedelta(days=365)).isoformat(), (day - timedelta(days=730)).isoformat()
    out = {}
    rows = con.execute("""SELECT uid, model_id, status, price_usd, substr(ends_at,1,10) AS d FROM lots
                          WHERE status IN ('sold','unsold') AND ends_at IS NOT NULL AND substr(ends_at,1,10) <= ?""",
                       (day.isoformat(),)).fetchall()
    skip = clean.implausible_lots(con)
    by = {}
    for r in rows:
        if r["uid"] in skip:
            continue
        by.setdefault(r["model_id"], []).append(r)
    for mid, rs in by.items():
        now = [r for r in rs if r["d"] > since]
        prev = [r for r in rs if since_prev < r["d"] <= since]
        sold = sorted(r["price_usd"] for r in now if r["status"] == "sold" and r["price_usd"])
        sold_prev = sorted(r["price_usd"] for r in prev if r["status"] == "sold" and r["price_usd"])
        med = st.median(sold) if sold else None
        med_prev = st.median(sold_prev) if len(sold_prev) >= 8 else None
        last = max((r for r in rs if r["status"] == "sold"), key=lambda r: r["d"], default=None)
        out[mid] = {
            "sold_12m": len(sold), "closed_12m": len(now),
            "median_sold_12m": None if med is None else round(med),
            "low_12m": sold[0] if sold else None, "high_12m": sold[-1] if sold else None,
            # the middle half of sale prices, drawn against the middle half of asks on the site
            "p25_12m": round(A._q(sold, 0.25)) if len(sold) >= 4 else None,
            "p75_12m": round(A._q(sold, 0.75)) if len(sold) >= 4 else None,
            "change_yoy": None if not (med and med_prev and len(sold) >= 8) else round((med / med_prev - 1) * 100, 1),
            "sell_through_12m": round(len(sold) / len(now) * 100) if now else None,
            "last_sold": None if not last else {"day": last["d"], "price": last["price_usd"]},
        }
    return out


# Variants shown side by side on a model page. A model in no group is compared with its family.
VARIANT_GROUPS = [
    ["296-gtb", "296-gts", "296-speciale"], ["sf90-stradale", "sf90-spider", "sf90-xx-stradale"], ["f8-tributo", "f8-spider"],
    ["488-gtb", "488-spider", "488-pista", "488-pista-spider"], ["458-italia", "458-spider", "458-speciale", "458-speciale-a"],
    ["f430", "f430-spider", "430-scuderia", "16m"], ["360-modena", "360-spider", "360-challenge-stradale"],
    ["12cilindri", "12cilindri-spider"], ["812-superfast", "812-gts", "812-competizione", "812-competizione-a"],
    ["f12berlinetta", "f12tdf"], ["599-gtb", "599-gto", "sa-aperta"], ["550-maranello", "550-barchetta", "575m"],
    ["roma", "roma-spider"], ["portofino", "portofino-m"], ["california", "california-t"], ["ff", "gtc4lusso", "gtc4lusso-t"],
    ["f355", "f355-spider"], ["testarossa", "512-tr", "f512m"], ["laferrari", "laferrari-aperta"], ["monza-sp1", "monza-sp2"],
]
US_STATES = {"Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA", "Colorado": "CO", "Connecticut": "CT",
    "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI",
    "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH",
    "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN",
    "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI",
    "Wyoming": "WY", "District of Columbia": "DC"}
_ABBR = set(US_STATES.values())


# what each source is called on the site
SOURCE_NAMES = {
    "ferrari_approved": "Ferrari Approved", "dupont_registry": "duPont Registry", "classiccars": "ClassicCars.com",
    "bringatrailer": "Bring a Trailer", "carsandbids": "Cars & Bids", "somo": "Sotheby's Motorsport",
    "dupont_live": "duPont Registry Live", "rmsothebys": "RM Sotheby's", "mecum": "Mecum", "broadarrow": "Broad Arrow",
    "gooding": "Gooding Christie's", "bonhams": "Bonhams", "pcarmarket": "PCARMARKET", "marketcheck": "MarketCheck",
    "autotrader_us": "Autotrader", "cars_com": "Cars.com",
}


def coverage(con, cfg: dict, models: list) -> dict:
    """How much of the market the site covers: cars seen, models, and every source it reads."""
    cars_n = con.execute("""SELECT COUNT(*) FROM (SELECT vehicle_key FROM sightings WHERE model_id IS NOT NULL AND vehicle_key IS NOT NULL
                            UNION SELECT COALESCE(vehicle_key, uid) FROM lots WHERE model_id IS NOT NULL)""").fetchone()[0]
    names = [SOURCE_NAMES.get(k, (v or {}).get("label") or k) for k, v in (cfg.get("sources") or {}).items()
             if (v or {}).get("enabled") and k in SOURCE_NAMES]
    if cfg.get("_tracker"):
        names.append("CarGurus")             # the owner's weekly CarGurus checks (pipeline/tracker.py)
    return {"cars": cars_n, "models": sum(1 for m in models if m.get("listings") or (m.get("auction") or {}).get("sold_12m")),
            "sources": names}


def city_states(con) -> dict:
    """city -> state, learned from listings that give both ("Fort Lauderdale, FL"), for sites that give only the city."""
    seen = {}
    for (loc,) in con.execute("SELECT DISTINCT location FROM sightings WHERE location LIKE '%,%'"):
        city, _, st_ = loc.rpartition(",")
        st_ = st_.strip()
        st_ = US_STATES.get(st_, st_)
        if st_ in _ABBR:
            seen.setdefault(city.strip().lower(), {}).setdefault(st_, 0)
            seen[city.strip().lower()][st_] += 1
    return {c: max(v, key=v.get) for c, v in seen.items()}


def us_state(location, cities: dict):
    loc = (location or "").strip()
    if not loc:
        return None
    if "," in loc:
        tail = loc.rpartition(",")[2].strip()
        if tail in _ABBR:
            return tail
        if tail in US_STATES:
            return US_STATES[tail]
    for name, ab in US_STATES.items():
        if loc.endswith(" " + name) or loc == name:
            return ab
    return cities.get(loc.lower()) or cities.get(loc.rpartition(",")[0].strip().lower())


def variant_ids(model_id: str, models: list) -> list:
    for g in VARIANT_GROUPS:
        if model_id in g:
            return [m for m in g if any(x["id"] == m for x in models)]
    fam = next((x["family"] for x in models if x["id"] == model_id), None)
    return [x["id"] for x in models if x["family"] == fam][:8]


def gearboxes(con) -> dict:
    """vehicle key (or "uid:<lot uid>" for a lot with none) -> 'manual' or 'f1', for the models sold with both.

    Every listing and lot of the car counts, so a car that crossed the block as "F430 6-Speed" is manual on
    the dealer listing that never said so (taxonomy.gearbox)."""
    recs, meta = {}, {}
    ids = tuple(sorted(taxonomy.DUAL_GEARBOX))
    marks = ",".join("?" * len(ids))
    for table, textkeys in (("sightings", ("detail", "description")), ("lots", ("excerpt", "subtitle", "description"))):
        for r in con.execute(f"SELECT uid, vehicle_key, model_id, year, title, raw FROM {table} WHERE model_id IN ({marks})", ids):
            k = gb_key(table, r["vehicle_key"], r["uid"])
            try:
                raw = json.loads(r["raw"]) if r["raw"] else {}
            except ValueError:
                raw = {}
            ex = raw.get("extra") or {}
            text = " ".join(str(ex.get(t) or raw.get(t) or "") for t in textkeys)
            recs.setdefault(k, []).append((r["title"] or raw.get("title") or "", str(raw.get("transmission") or ""), text))
            meta.setdefault(k, (r["model_id"], r["year"]))
    # config/gearbox.yaml wins over what the listings say
    return {k: (taxonomy.fixed_gearbox(k) if k.startswith(("vin:", "chassis:")) else None)
            or taxonomy.gearbox_from(meta[k][0], meta[k][1], items) for k, items in recs.items()}


def gb_key(table: str, vehicle_key, uid: str) -> str:
    """A car's records share a gearbox only when they are matched by VIN or chassis number: a looser
    fingerprint can join different cars (several 2007 599s sold without a VIN)."""
    vk = vehicle_key or ""
    if vk.startswith(("vin:", "chassis:")):
        return vk
    return ("s:" + vk) if table == "sightings" and vk else "uid:" + uid


def lot_gearbox(gb: dict, r) -> "str | None":
    return gb.get(gb_key("lots", r["vehicle_key"], r["uid"]))


def listing_gearbox(gb: dict, vk: str) -> "str | None":
    return gb.get(vk) if (vk or "").startswith(("vin:", "chassis:")) else gb.get("s:" + (vk or ""))


def sold_points(con, skip, gb=None) -> dict:
    """model id -> [[day, price, gearbox], ...] of every dated auction sale, for the variants chart."""
    out, gb = {}, gb or {}
    for r in con.execute("""SELECT uid, vehicle_key, model_id, substr(ends_at,1,10) d, price_usd FROM lots
                            WHERE status='sold' AND price_usd > 0 AND ends_at IS NOT NULL ORDER BY ends_at"""):
        if r["uid"] not in skip:
            out.setdefault(r["model_id"], []).append([r["d"], round(r["price_usd"]), lot_gearbox(gb, r)])
    return out


def gearbox_variants(con, day: date, v: dict, sold: list, gb: dict) -> list:
    """A model sold with both gearboxes as two submarkets, manual and F1, with their own figures."""
    asks = {"manual": [], "f1": []}
    for vk, price in con.execute("SELECT vehicle_key, price_usd FROM daily_asks WHERE day=? AND model_id=? AND price_usd > 0",
                                 (day.isoformat(), v["id"])):
        asks[listing_gearbox(gb, vk) or "f1"].append(price)
    since = (day - timedelta(days=365)).isoformat()
    out = []
    for g, label in (("manual", "manual"), ("f1", "F1")):
        s12 = [p[1] for p in sold if p[2] == g and p[0] >= since]
        out.append(dict(v, name=f"{v['name']} {label}", gearbox=g, listings=len(asks[g]),
                        median=round(st.median(asks[g])) if asks[g] else None,
                        sold_12m=len(s12), median_sold_12m=round(st.median(s12)) if s12 else None,
                        sold=[p for p in sold if p[2] == g]))
    return out


def model_detail(con, model_id: str, day: date, idx=None, paint=None, extra=None) -> dict:
    extra = extra or {}
    hist = A.history(con, model_id, day, 365)
    car = (lambda uid: idx.lot(uid)) if idx else (lambda uid: None)
    gb = extra.get("gearbox") or {}
    rows = con.execute("""
      SELECT a.vehicle_key, a.price_usd, s.title, s.url, s.mileage_mi, s.location, MAX(s.country) AS country, s.dealer_name, s.marketplace, s.certified,
             s.exterior_color, s.year, MAX(s.image_url) AS image,
             MIN(MIN(s.first_seen, COALESCE(CASE WHEN json_valid(s.raw) THEN json_extract(s.raw, '$.extra.listed_on') END, s.first_seen))) AS first_seen
      FROM daily_asks a JOIN sightings s ON s.vehicle_key = a.vehicle_key AND s.removed_on IS NULL
      WHERE a.day=? AND a.model_id=? GROUP BY a.vehicle_key ORDER BY a.price_usd""", (day.isoformat(), model_id)).fetchall()
    cities = extra.get("cities") or {}
    fair = extra.get("fair") or {}
    listings = [dict(r, car=idx.listing(r["vehicle_key"]) if idx else None,
                     country=sales.listing_country(r["country"], r["location"]),
                     state=us_state(r["location"], cities), gearbox=listing_gearbox(gb, r["vehicle_key"]),
                     fair=fair.get(r["vehicle_key"])) for r in rows]
    skip = clean.implausible_lots(con)
    lots = [r for r in con.execute("""SELECT uid, vehicle_key, title, url, auction_house, status, price_usd, ends_at, mileage_mi, image_url AS image,
                                 source, country, location, raw FROM lots
                          WHERE model_id=? AND status IN ('sold','unsold') ORDER BY ends_at IS NULL, ends_at DESC LIMIT 80""",
                       (model_id,)) if r["uid"] not in skip][:60]
    upcoming = con.execute("""SELECT uid, vehicle_key, title, url, auction_house, status, price_usd, estimate_low_usd, estimate_high_usd, ends_at, location,
                                     image_url AS image, source, country, raw FROM lots
                              WHERE model_id=? AND status IN ('live','upcoming') AND last_seen >= ? ORDER BY ends_at""",
                           (model_id, (day - timedelta(days=1)).isoformat())).fetchall()
    lot_out = lambda r: {k: r[k] for k in r.keys() if k not in ("uid", "raw", "source", "country", "vehicle_key")} | A.sale_fields(r) \
        | {"car": car(r["uid"]), "gearbox": lot_gearbox(gb, r)} \
        | ({"fair": (extra.get("fair_lots") or {})[r["uid"]]} if r["uid"] in (extra.get("fair_lots") or {}) else {})

    # Market chart, like classic.com: every dated auction result (sold, or not sold at its high bid),
    # every car for sale now (at the day it was first listed) and cars that left the market (last ask).
    # Each point: [day, price, kind s|u|f|a, car id, title, photo, where]
    pts = []
    for r in con.execute("""SELECT uid, vehicle_key, substr(ends_at,1,10) d, price_usd, status, title, image_url, auction_house,
                                   source, url, country, location, ends_at, raw, year FROM lots
                            WHERE model_id=? AND status IN ('sold','unsold') AND price_usd > 0 AND ends_at IS NOT NULL
                              AND substr(ends_at,1,10) <= ? ORDER BY ends_at""", (model_id, day.isoformat())):
        if r["uid"] not in skip:
            sl = sales.describe(r)
            pts.append([r["d"], round(r["price_usd"]), "s" if r["status"] == "sold" else "u", car(r["uid"]),
                        r["title"], r["image_url"], r["auction_house"], sl["sale"], sl["country"], r["year"],
                        (lot_gearbox(gb, r) or "")[:1] or None])
    for l in listings:
        if l["price_usd"]:
            pts.append([l["first_seen"] or day.isoformat(), round(l["price_usd"]), "f", l["car"], l["title"], l["image"],
                        l["dealer_name"] or l["marketplace"], None, None, l["year"], (l["gearbox"] or "")[:1] or None])
    live = {l["vehicle_key"] for l in listings}
    for r in con.execute("""SELECT s.vehicle_key, MAX(s.removed_on) d, s.title, MAX(s.image_url) img, s.dealer_name, s.marketplace,
                                   (SELECT p.price_usd FROM sighting_prices p WHERE p.uid = s.uid ORDER BY p.day DESC LIMIT 1) price
                            FROM sightings s WHERE s.model_id=? AND s.removed_on IS NOT NULL GROUP BY s.vehicle_key""", (model_id,)):
        if r["vehicle_key"] not in live and r["price"]:
            pts.append([r["d"], round(r["price"]), "a", idx.listing(r["vehicle_key"]) if idx else None, r["title"], r["img"],
                        r["dealer_name"] or r["marketplace"]])
    pts.sort(key=lambda p: p[0])
    return {"id": model_id, "history": [[p["day"], p["median"], p["n"]] for p in hist],
            "gearbox_split": model_id in taxonomy.DUAL_GEARBOX,
            "listings": listings,
            "results": [lot_out(r) for r in lots],
            "points": pts,
            "auction_points": [[p[0], p[1], 1 if p[2] == "s" else 0] for p in pts if p[2] in "su"],
            "upcoming": [lot_out(r) for r in upcoming],
            "mileage": mileage.curve(con, model_id, day, idx, skip,
                                     (lambda vk: listing_gearbox(gb, vk), lambda vk, uid: gb.get(gb_key("lots", vk, uid)))
                                     if model_id in taxonomy.DUAL_GEARBOX else None),
            # cars for sale week by week, with the owner's CarGurus tracker where it covers the model
            "weekly": weekly.series(con, model_id, day, (extra.get("tracker") or {}).get(model_id), extra.get("since")),
            "moves": extra.get("moves") or {}, "moves_gear": extra.get("moves_gear"),
            # sibling variants side by side: their figures and dated sales
            "variants": [x for v in extra.get("variants", []) for x in
                         (gearbox_variants(con, day, v, (extra.get("sold") or {}).get(v["id"], []), gb) if v["id"] in taxonomy.DUAL_GEARBOX
                          else [dict(v, sold=(extra.get("sold") or {}).get(v["id"], []))])],
            # colour premiums against red, adjusted for year and mileage (pipeline/colors.py)
            "colors": {"model": (paint["models"].get(model_id) if paint else colors.model(con, model_id, day)),
                       "market": paint["market"] if paint else None}}


def write(con, day: date, cfg: dict, health: list[dict]) -> Path:
    site_dir = Path(cfg["settings"].get("site_dir", "site"))
    data_dir = site_dir / "data"
    (data_dir / "models").mkdir(parents=True, exist_ok=True)
    sales.prime(con)                     # sale names and links the adapters keep in kv
    payload = build(con, day, cfg, health)
    # one page per car, and every card on the site points at it
    stats = A.model_stats(con, day)
    pages, idx = cars.build(con, day, stats)
    gb = gearboxes(con)
    # what each car for sale (and each lot coming up) should sell for (pipeline/fairvalue.py)
    fv = fairvalue.build(con, day, gb, clean.implausible_lots(con))
    for vk, v in fv["cars"].items():
        cid = idx.listing(vk)
        if cid in pages and pages[cid].get("status") == "for_sale":
            pages[cid]["fair_value"] = v
    for uid, v in fv["lots"].items():
        cid = idx.lot(uid)
        if cid in pages and pages[cid].get("status") in ("live", "upcoming"):
            pages[cid]["fair_value"] = v
    cars.write(pages, site_dir)
    for key in ("live", "upcoming", "results"):
        for l in payload["auctions"][key]:
            l["car"] = idx.lot(l["uid"])
            if key != "results" and l["uid"] in fv["lots"]:
                l["fair"] = fv["lots"][l["uid"]]
    for i in payload["insights"]:
        if i.get("uid"):
            i["car"] = idx.lot(i["uid"])
    paint = colors.all_models(con, day)
    times = colors.selling_times(con, day, cfg.get("_tracker"))
    colors.add_selling_times(paint["market"], times["market"])
    for mid, block in paint["models"].items():
        colors.add_selling_times(block, times["models"].get(mid, {}))
    payload["colors"] = paint["market"]
    # movers over 30 days (asks), 90 days and 12 months (sale prices, adjusted by the fair value fit)
    payload["pulse"] = pulse.build(con, day, fv.get("fit"), stats, payload.get("covers") or {})
    if fv.get("fit") is not None:
        payload["index"] = pulse.index(fv["fit"], day)
        payload["market_change"] = {k: payload["pulse"][k].get("market") for k in pulse.PERIODS}
    # the models a car can be valued for, for Value my car (pipeline/fairvalue.py)
    if fv.get("fit") is not None:
        (data_dir / "valuer.json").write_text(json.dumps(fairvalue.valuer(fv["fit"], fv["models"], day, fv.get("anchors")),
                                                         separators=(",", ":"), default=str), encoding="utf-8")
    # cars for sale by gearbox, for the Models menu's 6-Speed and F1 entries, and each gearbox's 7 day change
    gear_of = lambda vk, mid: f"{mid}|{listing_gearbox(gb, vk) or 'f1'}" if mid in taxonomy.DUAL_GEARBOX else None
    _, gear_7d = A.weekly_change(con, day, key_of=gear_of)
    for m in payload["models"]:
        if m["id"] in taxonomy.DUAL_GEARBOX:
            n = {"manual": 0, "f1": 0}
            for (vk,) in con.execute("SELECT vehicle_key FROM daily_asks WHERE day=? AND model_id=?", (day.isoformat(), m["id"])):
                n[listing_gearbox(gb, vk) or "f1"] += 1
            m["gearbox_listings"] = n
            m["gearbox_change_7d"] = {g: gear_7d.get(f"{m['id']}|{g}") for g in ("manual", "f1")}
    # how far each model (and each gearbox) moved over the market panel's periods (pipeline/pulse.py)
    moves = pulse.model_moves(con, day, fv.get("fit"), gb, clean.implausible_lots(con), gear_of)
    out = data_dir / "site.json"
    out.write_text(json.dumps(payload, separators=(",", ":"), default=str), encoding="utf-8")
    since = (con.execute("SELECT MIN(day) FROM daily_asks").fetchone() or [None])[0]
    shared = {"cities": city_states(con), "sold": sold_points(con, clean.implausible_lots(con), gb), "gearbox": gb,
              "tracker": cfg.get("_tracker") or {}, "since": since, "fair": fv["cars"], "fair_lots": fv["lots"]}
    brief = {m["id"]: {"id": m["id"], "name": m["name"], "listings": m.get("listings") or 0, "median": m.get("median"),
                       "sold_12m": (m.get("auction") or {}).get("sold_12m") or 0,
                       "median_sold_12m": (m.get("auction") or {}).get("median_sold_12m")} for m in payload["models"]}
    for m in payload["models"]:
        ext = dict(shared, variants=[brief[v] for v in variant_ids(m["id"], payload["models"]) if v in brief],
                   moves=moves.get(m["id"], {}),
                   moves_gear={g: moves.get(f"{m['id']}|{g}", {}) for g in ("manual", "f1")} if m["id"] in taxonomy.DUAL_GEARBOX else None)
        (data_dir / "models" / f"{m['id']}.json").write_text(
            json.dumps(model_detail(con, m["id"], day, idx, paint, ext), separators=(",", ":"), default=str), encoding="utf-8")
    return out
