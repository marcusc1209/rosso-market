"""How mileage moves price, per model: the mileage curve on each car page.

Data: every car of the model for sale now (today's asking price, with the mileage any of
its listings gives) and every auction sale in the last 18 months that published a
mileage. Older sales are left out: the market has moved since, so they would say more
about when a car sold than about its miles. Unsold high bids are left out too.

Fit: log price against mileage, in one of two shapes, whichever fits the data better
(smaller median miss over every car, same number of parameters):
  exp   ln p = a + b * miles                 a steady percentage per mile
  log   ln p = a + b * ln(1 + miles / 1000)  the first miles cost the most, then it flattens
Cars far off the curve (more than 2.5 robust standard deviations, a race car, a wreck, a
typo) are dropped and the curve fitted again. sigma is the typical spread around the
curve, drawn as a band.

No curve without at least 8 cars that span at least 3,000 miles (p10 to p90).
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import median
from typing import Optional

SOLD_DAYS = 548            # 18 months
MIN_POINTS = 8
MIN_SPREAD = 3000
MAX_MILES = 250_000


def points(con, model_id: str, day: date, idx=None, skip=frozenset()) -> list[list]:
    """[miles, price, kind l (for sale) or s (sold), car id, title, photo, where, date]"""
    today, since = day.isoformat(), (day - timedelta(days=SOLD_DAYS)).isoformat()
    out, seen = [], set()
    for r in con.execute("""
        SELECT a.vehicle_key, a.price_usd, MAX(s.mileage_mi) mi, MAX(s.title) title, MAX(s.image_url) img,
               MAX(COALESCE(s.dealer_name, s.marketplace, s.source)) who, MIN(s.first_seen) d
        FROM daily_asks a JOIN sightings s ON s.vehicle_key = a.vehicle_key AND s.removed_on IS NULL
        WHERE a.day=? AND a.model_id=? GROUP BY a.vehicle_key""", (today, model_id)):
        if r["mi"] is not None and r["price_usd"] and 0 <= r["mi"] < MAX_MILES:
            same = (round(r["mi"]), round(r["price_usd"]))
            if same in seen:
                continue          # one car listed twice without a shared VIN (a marketplace and the dealer, say)
            seen.add(same)
            out.append([round(r["mi"]), round(r["price_usd"]), "l", idx.listing(r["vehicle_key"]) if idx else None,
                        r["title"], r["img"], r["who"], r["d"]])
    for r in con.execute("""
        SELECT uid, mileage_mi mi, price_usd, title, image_url img, auction_house who, substr(ends_at,1,10) d FROM lots
        WHERE model_id=? AND status='sold' AND price_usd > 0 AND mileage_mi IS NOT NULL
          AND ends_at IS NOT NULL AND substr(ends_at,1,10) BETWEEN ? AND ?""", (model_id, since, today)):
        if r["uid"] not in skip and 0 <= r["mi"] < MAX_MILES:
            out.append([round(r["mi"]), round(r["price_usd"]), "s", idx.lot(r["uid"]) if idx else None,
                        r["title"], r["img"], r["who"], r["d"]])
    return out


def _x(shape: str, m: float) -> float:
    return m / 1000 if shape == "exp" else math.log1p(m / 1000)


def _ols(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b


def _pct(sorted_vals, q):
    return sorted_vals[min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))]


def fit(pts: list[list]) -> Optional[dict]:
    if len(pts) < MIN_POINTS:
        return None
    miles = sorted(p[0] for p in pts)
    lo, hi = _pct(miles, 0.1), _pct(miles, 0.9)
    if hi - lo < MIN_SPREAD:
        return None
    ys_all = [math.log(p[1]) for p in pts]
    best = None
    for shape in ("exp", "log"):
        xs = [_x(shape, p[0]) for p in pts]
        ab = _ols(xs, ys_all)
        if not ab:
            continue
        res = [y - (ab[0] + ab[1] * x) for x, y in zip(xs, ys_all)]
        mid = median(res)
        mad = median(abs(r - mid) for r in res) * 1.4826
        keep = [i for i, r in enumerate(res) if abs(r - mid) <= max(2.5 * mad, 0.12)]
        if len(keep) >= MIN_POINTS and len(keep) < len(pts):
            ab = _ols([xs[i] for i in keep], [ys_all[i] for i in keep]) or ab
        kx, ky = [xs[i] for i in keep], [ys_all[i] for i in keep]
        sse = sum((y - (ab[0] + ab[1] * x)) ** 2 for x, y in zip(kx, ky))
        my = sum(ky) / len(ky)
        sst = sum((y - my) ** 2 for y in ky)
        # the shapes are compared on every car, by the median miss, so outliers cannot pick the shape
        crit = median(abs(y - (ab[0] + ab[1] * x)) for x, y in zip(xs, ys_all))
        cand = {"shape": shape, "a": ab[0], "b": ab[1], "crit": crit, "n": len(keep), "dropped": len(pts) - len(keep),
                "sigma": math.sqrt(sse / max(1, len(keep) - 2)), "r2": 1 - sse / sst if sst > 0 else 0.0}
        if best is None or crit < best["crit"]:
            best = cand
    if best is None:
        return None
    pred = lambda m: math.exp(best["a"] + best["b"] * _x(best["shape"], m))
    return {
        "shape": best["shape"], "a": round(best["a"], 6), "b": round(best["b"], 8),
        "sigma": round(best["sigma"], 4), "r2": round(best["r2"], 3), "n": best["n"], "dropped": best["dropped"],
        "low_miles": lo, "high_miles": hi, "low_price": round(pred(lo)), "high_price": round(pred(hi)),
        "change_pct": round((pred(hi) / pred(lo) - 1) * 100, 1),
        "x_max": _pct(miles, 0.98),
    }


def curve(con, model_id: str, day: date, idx=None, skip=frozenset()) -> dict:
    pts = points(con, model_id, day, idx, skip)
    return {"since": (day - timedelta(days=SOLD_DAYS)).isoformat(), "points": pts, "fit": fit(pts),
            "for_sale": sum(p[2] == "l" for p in pts), "sold": sum(p[2] == "s" for p in pts)}
