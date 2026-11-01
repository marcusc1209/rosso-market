"""Write the JSON the website reads: site/data/site.json plus one file per model."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

from . import analytics as A
from . import taxonomy


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
    br = A.brief(stats, mv, ovn, auc)
    index = A.market_index(con, day, 90)
    sample = bool(con.execute("SELECT 1 FROM sightings WHERE sample=1 AND removed_on IS NULL LIMIT 1").fetchone()
                  or con.execute("SELECT 1 FROM lots WHERE sample=1 LIMIT 1").fetchone())

    models = []
    for m in taxonomy.MODELS:
        s = stats.get(m.id)
        if not s:
            continue
        hist = A.history(con, m.id, day, 90)
        models.append(dict(s, spark=[p["median"] for p in hist[::3]][-30:], history=_weekly(hist, "median"),
                           sell_through=auc["sell_through"].get(m.id)))

    families = []
    for fam in taxonomy.FAMILY_ORDER:
        ms = [x for x in models if x["family"] == fam]
        if not ms:
            continue
        n = sum(x["listings"] for x in ms)
        movers_in = [x for x in mv if x["family"] == fam]
        if movers_in:
            top = movers_in[0]
            signal = f"{top['name']} asks {'softening' if top['direction'] == 'down' else 'firming'} this week"
            change = top["change_7d"]
        else:
            signal, change = "Asks mostly steady across tracked models this week", None
        families.append({"name": fam, "listings": n, "models": [x["id"] for x in ms], "signal": signal, "change": change,
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


def model_detail(con, model_id: str, day: date) -> dict:
    hist = A.history(con, model_id, day, 365)
    rows = con.execute("""
      SELECT a.vehicle_key, a.price_usd, s.title, s.url, s.mileage_mi, s.location, s.dealer_name, s.marketplace, s.certified,
             s.exterior_color, s.year
      FROM daily_asks a JOIN sightings s ON s.vehicle_key = a.vehicle_key AND s.removed_on IS NULL
      WHERE a.day=? AND a.model_id=? GROUP BY a.vehicle_key ORDER BY a.price_usd""", (day.isoformat(), model_id)).fetchall()
    lots = con.execute("""SELECT title, url, auction_house, status, price_usd, ends_at FROM lots
                          WHERE model_id=? AND status IN ('sold','unsold') ORDER BY ends_at DESC LIMIT 40""", (model_id,)).fetchall()
    return {"id": model_id, "history": [[p["day"], p["median"], p["n"]] for p in hist],
            "listings": [dict(r) for r in rows], "results": [dict(r) for r in lots]}


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
