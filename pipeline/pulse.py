"""Market pulse: which models moved over the last 30 days, 90 days and 12 months.

30 days: asking prices, model year by model year, today against the snapshot a month ago (or our first
         snapshot while we have less than a month), as in analytics.week_over_week.
90 days and 12 months: auction sale prices, the last period against the one before it. Each sale is first
         set against the fair value fit (pipeline/fairvalue.py) with the passing of time taken out, so the
         change is in what the same car (year, mileage, colour, gearbox, auction platform) sells for, not in
         which cars came up for sale.

The market as a whole is the same comparison over every model's sales at once, and the index drawn behind
the overview is that adjusted price, month by month, over two years.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import median

from . import analytics as A
from . import taxonomy

PERIODS = {"90d": 90, "12m": 365}
MIN_SALES = {"90d": 4, "12m": 5}       # sales of the model in each of the two periods


def _adjusted(fit) -> list[tuple]:
    """(days ago, model, adjusted log price, price) for every sale the fit kept."""
    out = []
    for p in fit.rows:
        if p["sold"]:
            r = math.log(p["price"]) - fit._predict_log(fit.beta, dict(p, months=0.0))
            out.append((p["months"] * 30.44, p["model"], r, p["price"]))
    return out


def sales_change(fit, day: date, key: str, stats: dict, covers: dict) -> dict:
    days = PERIODS[key]
    adj = _adjusted(fit)
    now = [a for a in adj if a[0] <= days]
    then = [a for a in adj if days < a[0] <= 2 * days]
    out = []
    for mid in {a[1] for a in now}:
        n = [a for a in now if a[1] == mid]
        t = [a for a in then if a[1] == mid]
        m = taxonomy.BY_ID.get(mid)
        if not m or len(n) < MIN_SALES[key] or len(t) < MIN_SALES[key]:
            continue
        out.append({"id": mid, "name": m.name, "family": m.family,
                    "change": round((math.exp(median(a[2] for a in n) - median(a[2] for a in t)) - 1) * 100, 2),
                    "sales": len(n), "sales_last": len(t), "median": round(median(a[3] for a in n)),
                    "median_last": round(median(a[3] for a in t)), "image": covers.get(mid),
                    "listings": (stats.get(mid) or {}).get("listings", 0)})
    out.sort(key=lambda x: x["change"], reverse=True)
    counts = {}
    for a in now:
        counts.setdefault(a[1], []).append(a[3])
    act = sorted(((mid, ps) for mid, ps in counts.items() if taxonomy.BY_ID.get(mid)), key=lambda x: (-len(x[1]), -sum(x[1])))
    active = None
    if act:
        mid, ps = act[0]
        m = taxonomy.BY_ID[mid]
        active = {"id": mid, "name": m.name, "family": m.family, "total": len(ps), "sold": len(ps), "dollar": round(sum(ps)),
                  "median": round(median(ps)), "image": covers.get(mid), "listings": (stats.get(mid) or {}).get("listings", 0)}
    market = None
    if len(now) >= 20 and len(then) >= 20:
        market = round((math.exp(median(a[2] for a in now) - median(a[2] for a in then)) - 1) * 100, 2)
    return {"basis": "sold", "since": (day - timedelta(days=days)).isoformat(), "all": out,
            "gainer": out[0] if out and out[0]["change"] > 0 else None,
            "decliner": out[-1] if out and out[-1]["change"] < 0 else None,
            "active": active, "market": market, "sales": len(now), "sales_last": len(then)}


def index(fit, day: date, months: int = 24, window: int = 90) -> list[list]:
    """The adjusted sale price of the whole market, month by month: [[day, level]], the first month = 100."""
    adj = _adjusted(fit)
    pts = []
    for k in range(months, -1, -1):
        back = k * 30.44
        win = [a[2] for a in adj if back <= a[0] < back + window]
        if len(win) >= 20:
            pts.append([(day - timedelta(days=round(back))).isoformat(), median(win)])
    if not pts:
        return []
    base = pts[0][1]
    return [[d, round(math.exp(v - base) * 100, 2)] for d, v in pts]


def build(con, day: date, fit, stats: dict, covers: dict) -> dict:
    mm = A.market_movers(con, day, stats, covers, back=30)
    mm["basis"] = "ask"
    out = {"30d": mm}
    if fit is not None:
        for key in PERIODS:
            out[key] = sales_change(fit, day, key, stats, covers)
    return out
