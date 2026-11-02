"""Market statistics computed from the stored observations.

Definitions (shown on the site so readers know what they are looking at):

  tracked listing   one physical car for sale, counted once however many sites list it
  median ask        median of tracked listings' asking prices for a model on a day
  mover             a model whose median ask moved 1% or more versus 7 days earlier,
                    with at least 5 cars for sale on both days, and where the cars
                    listed on both days mostly repriced in the same direction
                    (so the move is real repricing, not just a different mix of cars)
  price cut         a car whose asking price is lower than it was 7 days earlier
  sell through      sold lots divided by lots that closed (sold + unsold)
"""
from __future__ import annotations

import json
import math
import statistics as st
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from . import sales, taxonomy

MOVER_MIN_PCT = 0.0             # any move counts; repricing must agree in direction
MOVER_MIN_CARS = 5
OUTLIER_LOW, OUTLIER_HIGH = 0.4, 2.5


def _d(day: date, n: int = 0) -> str:
    return (day - timedelta(days=n)).isoformat()


def _q(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * q
    f, c = math.floor(k), math.ceil(k)
    return s[f] if f == c else s[f] + (s[c] - s[f]) * (k - f)


def _pct(a, b):
    return None if not a or not b else round((a / b - 1) * 100, 1)


def snapshot(con, day: date) -> dict[str, dict[str, float]]:
    """model_id -> {vehicle_key: price}"""
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for r in con.execute("SELECT model_id, vehicle_key, price_usd FROM daily_asks WHERE day=?", (day.isoformat(),)):
        if r["price_usd"]:
            out[r["model_id"]][r["vehicle_key"]] = r["price_usd"]
    return out


def available_days(con) -> list[str]:
    return [r[0] for r in con.execute("SELECT DISTINCT day FROM daily_asks ORDER BY day")]


def listed_since(con) -> dict[str, str]:
    """The day each car was put up for sale: the earliest listing date any site publishes for it
    (duPont Registry and AutoTrader.ca do), else the first day we saw it."""
    out = {r[0]: r[1] for r in con.execute("SELECT vehicle_key, MIN(day) FROM daily_asks GROUP BY vehicle_key")}
    for k, d in con.execute("""SELECT vehicle_key, MIN(CASE WHEN json_valid(raw) THEN json_extract(raw, '$.extra.listed_on') END)
                               FROM sightings WHERE vehicle_key IS NOT NULL GROUP BY vehicle_key"""):
        if d and (k not in out or d < out[k]):
            out[k] = d
    return out


def model_stats(con, day: date) -> dict[str, dict]:
    now, wk, mo = snapshot(con, day), snapshot(con, day - timedelta(days=7)), snapshot(con, day - timedelta(days=30))
    first_seen = listed_since(con)
    week_base, week_change = weekly_change(con, day)
    stats = {}
    for mid, cars in now.items():
        m = taxonomy.BY_ID.get(mid)
        if not m:
            continue
        prices = list(cars.values())
        med = st.median(prices)
        med_wk = st.median(wk[mid].values()) if len(wk.get(mid, {})) >= MOVER_MIN_CARS else None
        med_mo = st.median(mo[mid].values()) if len(mo.get(mid, {})) >= MOVER_MIN_CARS else None
        same = [(cars[k], wk[mid][k]) for k in cars.keys() & wk.get(mid, {}).keys()]
        down = sum(1 for a, b in same if a < b * 0.999)
        up = sum(1 for a, b in same if a > b * 1.001)
        dom = [(day - date.fromisoformat(first_seen[k])).days for k in cars if k in first_seen]
        stats[mid] = dict(
            id=mid, name=m.name, family=m.family, body=m.body, tracked=m.tracked, pair=m.pair,
            change_7d_since=week_base,
            listings=len(prices), median=round(med), p25=round(_q(prices, 0.25)), p75=round(_q(prices, 0.75)),
            low=round(min(prices)), high=round(max(prices)),
            median_7d_ago=None if med_wk is None else round(med_wk), change_7d=week_change.get(mid),
            median_30d_ago=None if med_mo is None else round(med_mo), change_30d=_pct(med, med_mo) if len(prices) >= MOVER_MIN_CARS else None,
            same_cars=len(same), cut_7d=down, raised_7d=up, new_7d=len(cars.keys() - wk.get(mid, {}).keys()),
            days_on_market=None if not dom else round(st.median(dom)),
        )
    return stats


def weekly_base(con, day: date) -> str | None:
    """The day the 7 day change compares with: a week ago, else the closest earlier snapshot
    (up to 10 days back), else, while tracking is younger than a week, the oldest day we have."""
    days = [d for d in available_days(con) if d < day.isoformat()]
    if not days:
        return None
    week = _d(day, 7)
    older = [d for d in days if _d(day, 10) <= d <= week]
    return older[-1] if older else (days[0] if days[0] > week else None)


def weekly_change(con, day: date) -> tuple[str | None, dict]:
    """Each model's change in median asking price since weekly_base, compared model year by
    model year (2023 cars against 2023 cars) like Market Movers, weighted by today's cars.
    Every model with cars on both days gets a figure, however small; a model whose years do
    not overlap falls back to its plain median."""
    base = weekly_base(con, day)
    if not base:
        return None, {}
    # cars without a known model year still count, as one group of their own
    year_or_0 = lambda rows: [(k, mid, y or 0, p) for k, mid, y, p in rows]
    now = _group(year_or_0(_asks_with_year(con, day.isoformat())))
    then = _group(year_or_0(_asks_with_year(con, base)))
    out = {}
    for mid, years in now.items():
        prev = then.get(mid) or {}
        num = den = 0.0
        for y, prices in years.items():
            if prev.get(y):
                num += len(prices) * (st.median(prices) / st.median(prev[y]) - 1) * 100
                den += len(prices)
        if den:
            out[mid] = round(num / den, 2)
        elif prev:
            a = st.median([p for ps in years.values() for p in ps])
            b = st.median([p for ps in prev.values() for p in ps])
            out[mid] = round((a / b - 1) * 100, 2)
    return base, out


def movers(stats: dict) -> list[dict]:
    out = []
    for s in stats.values():
        c = s["change_7d"]
        if c is None or not s["tracked"] or abs(c) < MOVER_MIN_PCT:
            continue
        if s["listings"] < MOVER_MIN_CARS or s["median_7d_ago"] is None:
            continue
        agree, disagree = (s["cut_7d"], s["raised_7d"]) if c < 0 else (s["raised_7d"], s["cut_7d"])
        if agree == 0 or agree <= disagree:
            continue
        out.append(dict(s, direction="down" if c < 0 else "up"))
    return sorted(out, key=lambda s: abs(s["change_7d"]), reverse=True)


# ---------------------------------------------------------------- market movers

def _base_day(con, day: date, back: int = 7) -> str | None:
    """The snapshot closest to `back` days ago (the earliest one while history is short)."""
    days = [d for d in available_days(con) if d < day.isoformat()]
    if not days:
        return None
    target = _d(day, back)
    older = [d for d in days if d <= target]
    return older[-1] if older else days[0]


def new_listings(con, day: date, since: str) -> dict[str, set]:
    """model_id -> vehicle keys first listed on or after `since`.

    A car counts as new when its listing date is known to be recent: the marketplace's
    own date (duPont Registry, AutoTrader.ca), or the day we first saw it, unless that was the
    first day we read its source at all. Adding a site would otherwise make all of its
    cars look newly listed."""
    start = {r[0]: r[1] for r in con.execute(
        "SELECT source, MIN(day) FROM runs WHERE status IN ('ok','degraded') GROUP BY source")}
    published, seen, old = {}, {}, set()
    for r in con.execute("""SELECT s.vehicle_key, s.model_id, s.source, s.first_seen,
                                   CASE WHEN json_valid(s.raw) THEN json_extract(s.raw, '$.extra.listed_on') END AS listed_on
                            FROM sightings s JOIN daily_asks a ON a.vehicle_key = s.vehicle_key AND a.day = ?""", (day.isoformat(),)):
        key = (r["model_id"], r["vehicle_key"])
        if r["listed_on"]:
            published[key] = min(published.get(key, r["listed_on"]), r["listed_on"])
        elif r["first_seen"] and r["first_seen"] > (start.get(r["source"]) or ""):
            seen[key] = min(seen.get(key, r["first_seen"]), r["first_seen"])
        else:
            old.add(key)
    out = defaultdict(set)
    # a date a site publishes wins; otherwise the day we first saw the car, unless some site
    # already had it when we started reading that site
    for key in published.keys() | seen.keys():
        when = published.get(key) or (seen[key] if key not in old else None)
        if when and when >= since:
            out[key[0]].add(key[1])
    return out


def _asks_with_year(con, d: str) -> list[tuple]:
    """(vehicle_key, model_id, year, price) for every car in the snapshot of day d."""
    return [tuple(r) for r in con.execute("""
        SELECT a.vehicle_key, a.model_id, (SELECT MAX(s.year) FROM sightings s WHERE s.vehicle_key = a.vehicle_key), a.price_usd
        FROM daily_asks a WHERE a.day = ? AND a.price_usd > 0""", (d,))]


def _price_on(history: list, listed_on: str | None, target: str) -> float | None:
    """A car's asking price on `target` from a marketplace price history [[date, price], ...]."""
    if not history or not listed_on or listed_on > target:
        return None                                   # not yet listed a week ago
    before = [p for d, p in history if d <= target]
    return float(before[-1]) if before else float(history[0][1])


def _rebuilt_week_ago(con, day: date, target: str) -> tuple[list, list]:
    """Last week's asks rebuilt from the marketplaces' own price histories (AutoTrader keeps
    the date each car was listed and every price change). Returns (last week, this week),
    both limited to the cars those histories cover, so the two weeks compare like with like."""
    last, cars = {}, set()
    for r in con.execute("""SELECT vehicle_key, model_id, year, raw FROM sightings
                            WHERE model_id IS NOT NULL AND (removed_on IS NULL OR removed_on > ?)
                              AND json_valid(raw) AND json_extract(raw, '$.extra.price_history') IS NOT NULL""", (target,)):
        try:
            extra = json.loads(r["raw"]).get("extra") or {}
        except ValueError:
            continue
        cars.add(r["vehicle_key"])
        hist = [h for h in extra.get("price_history") or [] if isinstance(h, list) and len(h) == 2 and h[1]]
        p = _price_on(hist, extra.get("listed_on"), target)
        if p and r["vehicle_key"] not in last:
            last[r["vehicle_key"]] = (r["vehicle_key"], r["model_id"], r["year"], p)
    now = [x for x in _asks_with_year(con, day.isoformat()) if x[0] in cars]
    return list(last.values()), now


def week_over_week(con, day: date) -> dict:
    """Median asking price by model, this week against last week, compared model year by
    model year (a 2023 car against 2023 cars) so a different mix of years does not
    pass for a price move. Every car counts: a batch of cheaper cars arriving, or cuts on
    cars already listed, moves the median, which is the point."""
    target = _d(day, 7)
    snaps = [d for d in available_days(con) if _d(day, 10) <= d <= target]
    if snaps:
        since, how = snaps[-1], "snapshot"
        last, now = _asks_with_year(con, since), _asks_with_year(con, day.isoformat())
    else:
        since, how = target, "rebuilt"
        last, now = _rebuilt_week_ago(con, day, target)
    by = lambda rows: _group(rows)
    a, b = by(now), by(last)
    out = []
    for mid, years in a.items():
        m = taxonomy.BY_ID.get(mid)
        prev = b.get(mid, {})
        num = den = 0.0
        n_now = n_last = 0
        for y, prices in years.items():
            old = prev.get(y, [])
            if len(prices) >= 2 and len(old) >= 2:
                w = len(prices)
                num += w * (st.median(prices) / st.median(old) - 1) * 100
                den += w
                n_now += len(prices); n_last += len(old)
        if not m or not den or n_now < 5 or n_last < 5:
            continue
        all_now = [p for ps in years.values() for p in ps]
        all_last = [p for ps in prev.values() for p in ps]
        out.append({"id": mid, "name": m.name, "family": m.family, "change": round(num / den, 2),
                    "cars_now": len(all_now), "cars_last": len(all_last), "years": sum(1 for y in years if len(years[y]) >= 2 and len(prev.get(y, [])) >= 2),
                    "median": round(st.median(all_now)), "median_last": round(st.median(all_last)) if all_last else None})
    out.sort(key=lambda x: x["change"], reverse=True)
    return {"since": since, "how": how, "models": out}


def _group(rows) -> dict:
    g = defaultdict(lambda: defaultdict(list))
    for _, mid, year, price in rows:
        if mid and year is not None and price:
            g[mid][year].append(price)
    return g


def market_movers(con, day: date, stats: dict, covers: dict) -> dict:
    """Biggest gainer and decliner by asking price, week over week, and the most active model."""
    wow = week_over_week(con, day)
    base = wow["since"]
    out = {"since": base, "how": wow["how"], "gainer": None, "decliner": None, "active": None, "all": []}
    for x in wow["models"]:
        x["image"] = covers.get(x["id"])
        x["listings"] = (stats.get(x["id"]) or {}).get("listings", x["cars_now"])
    out["all"] = wow["models"]
    if out["all"]:
        if out["all"][0]["change"] > 0:
            out["gainer"] = out["all"][0]
        if out["all"][-1]["change"] < 0:
            out["decliner"] = out["all"][-1]
    then_day = _base_day(con, day)
    now = snapshot(con, day)
    then = snapshot(con, date.fromisoformat(then_day)) if then_day else {}

    new = new_listings(con, day, base)
    gone = {mid: set(cars) - set(now.get(mid, {})) for mid, cars in then.items()}
    sold = defaultdict(int)
    for r in con.execute("""SELECT model_id, COUNT(*) FROM lots WHERE status='sold' AND substr(ends_at,1,10) > ? AND substr(ends_at,1,10) <= ?
                            GROUP BY model_id""", (base, day.isoformat())):
        sold[r[0]] = r[1]
    act = []
    for mid in set(new) | set(gone) | set(sold):
        m = taxonomy.BY_ID.get(mid)
        n, g, so = len(new.get(mid, ())), len(gone.get(mid, ())), sold.get(mid, 0)
        if m and n + g + so:
            act.append({"id": mid, "name": m.name, "family": m.family, "total": n + g + so, "new": n, "removed": g,
                        "sold": so, "listings": len(now.get(mid, {})), "image": covers.get(mid)})
    act.sort(key=lambda x: x["total"], reverse=True)
    out["active"] = act[0] if act else None
    out["activity"] = act[:12]
    return out


PREFERRED_HOUSES = ("RM Sotheby's", "Gooding Christie's", "Bonhams", "Bring a Trailer", "Broad Arrow", "Cars & Bids")


def cover_photos(con) -> dict[str, str]:
    """One photo per model, for cards that have none of their own (model insights, news).
    Auction houses' own photography first, most recent sale first."""
    out = {}
    order = {h: i for i, h in enumerate(PREFERRED_HOUSES)}
    rows = con.execute("""SELECT model_id, image_url, auction_house, ends_at FROM lots
                          WHERE image_url IS NOT NULL AND model_id IS NOT NULL AND status IN ('sold','unsold','live','upcoming')
                          ORDER BY ends_at DESC""").fetchall()
    best = {}
    for r in rows:
        rank = (order.get(r["auction_house"], 99),)
        if r["model_id"] not in best or rank < best[r["model_id"]][0]:
            best[r["model_id"]] = (rank, r["image_url"])
    out.update({k: v[1] for k, v in best.items()})
    for r in con.execute("""SELECT model_id, MAX(image_url) FROM sightings WHERE image_url IS NOT NULL AND removed_on IS NULL
                            AND marketplace != 'Ferrari Approved' GROUP BY model_id"""):
        out.setdefault(r[0], r[1])
    return out


def history(con, model_id: str | None, day: date, days: int = 120) -> list[dict]:
    q = "SELECT day, price_usd FROM daily_asks WHERE day BETWEEN ? AND ?" + (" AND model_id=?" if model_id else "")
    args = [_d(day, days), day.isoformat()] + ([model_id] if model_id else [])
    by_day = defaultdict(list)
    for r in con.execute(q, args):
        by_day[r[0]].append(r[1])
    return [{"day": d, "median": round(st.median(v)), "n": len(v)} for d, v in sorted(by_day.items()) if len(v) >= 3]


def market_index(con, day: date, days: int = 90) -> list[dict]:
    """Chain linked index of median asks, base 100. Each day compares every model's
    median to the previous day's, weighted by listings, so mix changes between
    models do not move the index."""
    days_list = [d for d in available_days(con) if _d(day, days) <= d <= day.isoformat()]
    out, level, prev = [], 100.0, None
    for d in days_list:
        snap = snapshot(con, date.fromisoformat(d))
        meds = {m: (st.median(v.values()), len(v)) for m, v in snap.items() if len(v) >= MOVER_MIN_CARS}
        if prev:
            num = den = 0.0
            for m, (med, n) in meds.items():
                if m in prev:
                    num += n * math.log(med / prev[m][0]); den += n
            if den:
                level *= math.exp(num / den)
        prev = meds
        out.append({"day": d, "index": round(level, 2)})
    return out


def overnight(con, day: date) -> dict:
    a, b = snapshot(con, day), snapshot(con, day - timedelta(days=1))
    flat_a = {k: p for cars in a.values() for k, p in cars.items()}
    flat_b = {k: p for cars in b.values() for k, p in cars.items()}
    if not flat_b:
        return {"new": 0, "removed": 0, "repriced": 0, "total": 0}
    # cars from a site we only started reading are not new listings
    fresh = {vk for cars in new_listings(con, day, (day - timedelta(days=1)).isoformat()).values() for vk in cars}
    new = len((flat_a.keys() - flat_b.keys()) & fresh)
    removed = len(flat_b.keys() - flat_a.keys())
    repriced = sum(1 for k in flat_a.keys() & flat_b.keys() if abs(flat_a[k] - flat_b[k]) > 1)
    return {"new": new, "removed": removed, "repriced": repriced, "total": new + removed + repriced}


# ---------------------------------------------------------------- auctions

def _lot(r) -> dict:
    d = {k: r[k] for k in ("uid", "title", "url", "auction_house", "status", "price_usd", "estimate_low_usd",
                           "estimate_high_usd", "no_reserve", "ends_at", "location", "model_id", "year", "mileage_mi",
                           "lot_number", "bid_count")} | {"image": r["image_url"]}
    return d | sale_fields(r)


def sale_fields(r) -> dict:
    """The sale a lot was in, a link to it and the country (see sales.py)."""
    s = sales.describe(r)
    return {"url": sales.lot_url(r), "sale": s["sale"], "sale_url": s["sale_url"], "country": s["country"],
            "online": s["online"]}


def auctions(con, day: date, stats: dict) -> dict:
    now = datetime.combine(day, datetime.max.time(), tzinfo=timezone.utc)
    live, upcoming, results = [], [], []
    for r in con.execute("SELECT * FROM lots WHERE last_seen >= ? ORDER BY ends_at", (_d(day, 1),)):
        lot = _lot(r)
        if r["status"] == "live":
            ends = datetime.fromisoformat(r["ends_at"]) if r["ends_at"] else None
            lot["ending_soon"] = bool(ends and (ends - now).total_seconds() < 48 * 3600)
            live.append(lot)
        elif r["status"] == "upcoming":
            upcoming.append(lot)
    for r in con.execute("""SELECT * FROM lots WHERE status IN ('sold','unsold') AND substr(ends_at,1,10) BETWEEN ? AND ?
                            ORDER BY ends_at DESC""", (_d(day, 90), day.isoformat())):
        lot = _lot(r)
        s = stats.get(r["model_id"])
        if s and r["price_usd"]:
            lot["median_ask"] = s["median"]
            lot["vs_ask_pct"] = _pct(r["price_usd"], s["median"])
            # a hammer price far outside the model's asking range is more often a data error
            # (wrong model match, typo on the source site) than a real result
            lot["outlier"] = s["listings"] >= 5 and not (OUTLIER_LOW <= r["price_usd"] / s["median"] <= OUTLIER_HIGH)
        results.append(lot)

    # Second opinion for each result: the median of every sold result we hold for that model
    # (all houses, all dates, undated RM results included). Catches data errors on models
    # with too few dealer listings to compare against.
    sold_by_model = defaultdict(list)
    for r in con.execute("SELECT model_id, price_usd FROM lots WHERE status='sold' AND price_usd > 0"):
        sold_by_model[r[0]].append(r[1])
    for lot in results:
        prices = sold_by_model.get(lot["model_id"], [])
        if lot.get("price_usd") and len(prices) >= 5:
            med = st.median(prices)
            lot["auction_median"] = round(med)
            if not (OUTLIER_LOW <= lot["price_usd"] / med <= OUTLIER_HIGH):
                lot["outlier"] = True

    by_model = defaultdict(lambda: {"sold": 0, "unsold": 0, "sold_prev": 0, "unsold_prev": 0})
    for lot in results:
        recent = lot["ends_at"][:10] > _d(day, 30)
        by_model[lot["model_id"]][("" if lot["status"] == "sold" else "un") + "sold" + ("" if recent else "_prev")] += 1
    sell_through = {}
    for mid, c in by_model.items():
        n, n_prev = c["sold"] + c["unsold"], c["sold_prev"] + c["unsold_prev"]
        sell_through[mid] = {"closed_30d": n, "rate_30d": round(c["sold"] / n * 100) if n else None,
                             "closed_prior_60d": n_prev, "rate_prior_60d": round(c["sold_prev"] / n_prev * 100) if n_prev else None}
    houses = defaultdict(lambda: {"live": 0, "results_30d": 0})
    for lot in live:
        houses[lot["auction_house"]]["live"] += 1
    for lot in results:
        if lot["ends_at"][:10] > _d(day, 30):
            houses[lot["auction_house"]]["results_30d"] += 1
    return {"live": live, "upcoming": upcoming, "results": results, "sell_through": sell_through, "houses": dict(houses)}


# ---------------------------------------------------------------- insights

def _money(x):
    return f"${x:,.0f}"


def insights(stats: dict, auc: dict, day: date, cert: dict) -> list[dict]:
    out = []
    # body style premium
    best = None
    for s in stats.values():
        if s["body"] in ("spider", "targa") and s["pair"] in stats:
            c = stats[s["pair"]]
            if s["listings"] >= 5 and c["listings"] >= 5:
                prem = _pct(s["median"], c["median"])
                if best is None or abs(prem) > abs(best[0]):
                    best = (prem, s, c)
    if best:
        prem, s, c = best
        out.append({"label": "Body style", "headline": f"{s['name']} premium {prem:+.1f}% over the {c['name']}",
                    "body": f"Median ask {_money(s['median'])} for the open car against {_money(c['median'])} for the coupe, "
                            f"across {s['listings']} and {c['listings']} tracked cars.", "model": s["id"]})
    # auction result vs ask
    recent = [l for l in auc["results"] if l["status"] == "sold" and l.get("vs_ask_pct") is not None
              and not l.get("outlier") and l["ends_at"][:10] >= _d(day, 14)]
    if recent:
        l = max(recent, key=lambda l: abs(l["vs_ask_pct"]))
        side = "under" if l["vs_ask_pct"] < 0 else "over"
        out.append({"label": "Auction", "headline": l["title"],
                    "body": f"Sold for {_money(l['price_usd'])} at {l['auction_house']}. That is {abs(l['vs_ask_pct']):.0f}% {side} "
                            f"the median dealer ask for the model, {_money(l['median_ask'])}.", "url": l["url"], "model": l["model_id"]})
    # biggest sale of the past week
    week = [l for l in auc["results"] if l["status"] == "sold" and l.get("price_usd") and not l.get("outlier")
            and l["ends_at"][:10] > _d(day, 7)]
    if week:
        l = max(week, key=lambda l: l["price_usd"])
        out.append({"label": "Top result", "headline": l["title"],
                    "body": f"The week's highest Ferrari sale: {_money(l['price_usd'])} at {l['auction_house']}"
                            + (f", against a median of {_money(l['auction_median'])} for the model at auction." if l.get("auction_median") else "."),
                    "url": l["url"], "model": l["model_id"], "image": l.get("image"), "uid": l["uid"]})
    # sell through change
    cand = [(mid, v) for mid, v in auc["sell_through"].items() if v["closed_30d"] >= 4 and (v["closed_prior_60d"] or 0) >= 4]
    if cand:
        mid, v = min(cand, key=lambda x: (x[1]["rate_30d"] or 0) - (x[1]["rate_prior_60d"] or 0))
        name = stats[mid]["name"] if stats.get(mid) else getattr(taxonomy.BY_ID.get(mid), "name", mid)
        if v["rate_30d"] is not None and v["rate_prior_60d"] is not None and v["rate_30d"] < v["rate_prior_60d"]:
            out.append({"label": "Sell through", "headline": f"{name} sell through slips to {v['rate_30d']}%",
                        "body": f"{v['rate_30d']}% of {v['closed_30d']} lots sold in the last 30 days, against {v['rate_prior_60d']}% "
                                f"of {v['closed_prior_60d']} in the 60 days before.", "model": mid})
    # Ferrari Approved premium
    if cert:
        mid, (c_med, u_med, n_c, n_u) = max(cert.items(), key=lambda x: x[1][2] + x[1][3])
        if stats.get(mid):
            out.append({"label": "Ferrari Approved", "headline": f"Certified {stats[mid]['name']} asks {_pct(c_med, u_med):+.1f}% vs non certified",
                        "body": f"Median {_money(c_med)} across {n_c} Approved cars against {_money(u_med)} across {n_u} others.", "model": mid})
    # slowest movers
    slow = [s for s in stats.values() if s["listings"] >= 8 and s["days_on_market"] is not None]
    if slow:
        s = max(slow, key=lambda s: s["days_on_market"])
        out.append({"label": "Days on market", "headline": f"{s['name']} is the slowest seller right now",
                    "body": f"Median tracked {s['name']} has been for sale {s['days_on_market']} days, with {s['cut_7d']} price {'cut' if s['cut_7d'] == 1 else 'cuts'} this week.",
                    "model": s["id"]})
    return out


def certified_split(con, day: date) -> dict:
    out = {}
    rows = con.execute("SELECT model_id, price_usd, certified FROM daily_asks WHERE day=?", (day.isoformat(),)).fetchall()
    by = defaultdict(lambda: ([], []))
    for r in rows:
        by[r["model_id"]][0 if r["certified"] else 1].append(r["price_usd"])
    for mid, (c, u) in by.items():
        if len(c) >= 5 and len(u) >= 5:
            out[mid] = (st.median(c), st.median(u), len(c), len(u))
    return out


# ---------------------------------------------------------------- brief

def brief(stats: dict, mv: list, ovn: dict, auc: dict, history_days: int = 99) -> dict:
    tracked = sum(s["listings"] for s in stats.values())
    cuts = sum(s["cut_7d"] for s in stats.values())
    sold_30 = [l for l in auc["results"] if l["status"] == "sold"]
    if tracked == 0 or history_days < 8:
        # Not enough daily snapshots for week over week numbers yet: say so plainly.
        if tracked == 0:
            headline = "Auction results are in. Dealer asking prices start with the next inventory run"
        else:
            headline = f"Tracking {tracked:,} Ferraris for sale. Weekly price moves begin after seven days of data"
        bullets = []
        if auc["live"]:
            bullets.append({"label": "Live now", "text": f"{len(auc['live'])} Ferrari auctions are open across the houses we follow."})
        sold_30 = [l for l in sold_30 if not l.get("outlier")]
        if sold_30:
            top = max(sold_30, key=lambda l: l["price_usd"] or 0)
            bullets.append({"label": "Recent results", "text": f"{len(sold_30)} Ferraris sold at auction in the last 90 days at the houses we date results for. "
                            f"The highest was {top['title']} at {_money(top['price_usd'])}."})
        bullets.append({"label": "History", "text": f"{history_days} day{'s' if history_days != 1 else ''} of daily asking price snapshots so far."})
        return {"headline": headline, "bullets": bullets, "tracked_listings": tracked, "active_auctions": len(auc["live"]),
                "overnight_changes": ovn["total"], "overnight": ovn, "price_cuts_7d": cuts, "warming_up": True}
    # headline prefers models with real depth; thin models still appear in the movers list
    deep = [m for m in mv if m["listings"] >= 10] or mv
    down = [m for m in deep if m["direction"] == "down"]
    up = [m for m in deep if m["direction"] == "up"]
    if len(down) >= 2:
        headline = f"{down[0]['name']} and {down[1]['name']} lead this week's softer asks"
    elif down:
        headline = f"{down[0]['name']} leads this week's softer asks"
    elif up:
        headline = f"{up[0]['name']} leads a firmer week for asks"
    else:
        headline = "Ferrari asks hold steady this week"
    bullets = []
    if down:
        bullets.append({"label": "Biggest drop", "text": f"{down[0]['name']}, median ask down {abs(down[0]['change_7d']):.1f}%."})
    if up:
        bullets.append({"label": "Biggest gain", "text": f"{up[0]['name']}, median ask up {up[0]['change_7d']:.1f}%."})
    most = max(stats.values(), key=lambda s: s["cut_7d"], default=None)
    if most and most["cut_7d"]:
        bullets.append({"label": "Most price cuts", "text": f"{most['name']} sellers lowered the price on {most['cut_7d']} cars this week."})
    bullets.append({"label": "Price cuts overall", "text": f"{cuts} tracked Ferraris were reduced in the last 7 days."})
    return {"headline": headline, "bullets": bullets, "tracked_listings": tracked, "active_auctions": len(auc["live"]),
            "overnight_changes": ovn["total"], "overnight": ovn, "price_cuts_7d": cuts}
