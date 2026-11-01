"""Write the JSON the website reads: site/data/site.json plus one file per model."""
from __future__ import annotations

import json
import statistics as st
import re
from datetime import date, timedelta
from pathlib import Path

from . import analytics as A
from . import clean, taxonomy


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

    news = [dict(r) for r in con.execute("SELECT title, url, outlet, published, image FROM news ORDER BY published DESC LIMIT 8")]

    return {
        "brand": "Rosso Market",
        "as_of": day.isoformat(),
        "region": "US and Canada",
        "sample": sample,
        "brief": br,
        "movers": mv,
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
            "change_yoy": None if not (med and med_prev and len(sold) >= 8) else round((med / med_prev - 1) * 100, 1),
            "sell_through_12m": round(len(sold) / len(now) * 100) if now else None,
            "last_sold": None if not last else {"day": last["d"], "price": last["price_usd"]},
        }
    return out


def model_detail(con, model_id: str, day: date) -> dict:
    hist = A.history(con, model_id, day, 365)
    rows = con.execute("""
      SELECT a.vehicle_key, a.price_usd, s.title, s.url, s.mileage_mi, s.location, s.dealer_name, s.marketplace, s.certified,
             s.exterior_color, s.year
      FROM daily_asks a JOIN sightings s ON s.vehicle_key = a.vehicle_key AND s.removed_on IS NULL
      WHERE a.day=? AND a.model_id=? GROUP BY a.vehicle_key ORDER BY a.price_usd""", (day.isoformat(), model_id)).fetchall()
    skip = clean.implausible_lots(con)
    lots = [r for r in con.execute("""SELECT uid, title, url, auction_house, status, price_usd, ends_at, mileage_mi FROM lots
                          WHERE model_id=? AND status IN ('sold','unsold') ORDER BY ends_at IS NULL, ends_at DESC LIMIT 80""",
                       (model_id,)) if r["uid"] not in skip][:60]
    # every dated result in the last three years, for the auction chart: [day, price, sold 1/0]
    pts = con.execute("""SELECT substr(ends_at,1,10), price_usd, status='sold', uid FROM lots
                         WHERE model_id=? AND status IN ('sold','unsold') AND price_usd > 0 AND ends_at IS NOT NULL
                           AND substr(ends_at,1,10) BETWEEN ? AND ? ORDER BY ends_at""",
                      (model_id, (day - timedelta(days=1095)).isoformat(), day.isoformat())).fetchall()
    upcoming = con.execute("""SELECT title, url, auction_house, status, price_usd, estimate_low_usd, estimate_high_usd, ends_at, location
                              FROM lots WHERE model_id=? AND status IN ('live','upcoming') AND last_seen >= ? ORDER BY ends_at""",
                           (model_id, (day - timedelta(days=1)).isoformat())).fetchall()
    return {"id": model_id, "history": [[p["day"], p["median"], p["n"]] for p in hist],
            "listings": [dict(r) for r in rows], "results": [{k: r[k] for k in r.keys() if k != "uid"} for r in lots],
            "auction_points": [[r[0], round(r[1]), int(r[2])] for r in pts if r[3] not in skip], "upcoming": [dict(r) for r in upcoming]}


def write(con, day: date, cfg: dict, health: list[dict]) -> Path:
    site_dir = Path(cfg["settings"].get("site_dir", "site"))
    data_dir = site_dir / "data"
    (data_dir / "models").mkdir(parents=True, exist_ok=True)
    payload = build(con, day, cfg, health)
    out = data_dir / "site.json"
    out.write_text(json.dumps(payload, separators=(",", ":"), default=str), encoding="utf-8")
    for m in payload["models"]:
        (data_dir / "models" / f"{m['id']}.json").write_text(
            json.dumps(model_detail(con, m["id"], day), separators=(",", ":"), default=str), encoding="utf-8")
    return out
