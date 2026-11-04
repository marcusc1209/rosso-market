"""Which exterior colours sell for more or less than red, per model and across the market.

Colours. Every site names paint its own way ("Rosso Corsa", "Red", "RED", "Russo Corsa",
"Corsa Red"), so each name is put in one family first: red, black, white, grey, silver,
blue, yellow, green, purple, brown or beige, or orange. Names that say nothing ("Other",
"N/A") are skipped.

Special order paint. Ferrari Approved calls a paint outside the standard range "Extra
Sample" (Ferrari's extra campionario) and names no colour. The paint is still red, blue or
green, so it is not a family of its own: the car takes its colour from any other listing of
the same VIN (duPont Registry names it for about half of them) and carries a special order
flag. The flag is its own term in the fit, so what a buyer pays for a one off paint is not
credited to its colour. A special order car no site names a colour for is left out.

Cars. Every car for sale today with a known colour (its asking price), and every auction
sale in the last 18 months whose house published the colour (the hammer price). Each car
needs a year, a mileage and a price.

Fit. Price depends far more on year and miles than on paint, and a model's red cars may
simply be newer. So the premium is not a raw comparison of medians: for each model,

    ln price = a + b * (year - mean year) + c * ln(1 + miles / 1000) + d * sold
               + one term per colour family, red left out

and the premium for a family is exp(term) - 1, a percentage against an otherwise identical
red car. The 90% range comes from the term's standard error. A family needs at least
MIN_FAMILY cars in the model and red at least MIN_RED, or it is not reported; smaller
families still sit in the fit (as "other") so they do not lean on the rest.

The market wide figure fits every model at once, with one level per model and year band in
place of a, so a family's premium is measured within each model, never across models.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Optional

SOLD_DAYS = 548
MIN_FAMILY = 3
MIN_RED = 3
MAX_MILES = 250_000
Z90 = 1.645

BASELINE = "red"
LABELS = {"red": "Red", "black": "Black", "white": "White", "grey": "Grey", "silver": "Silver", "blue": "Blue",
          "yellow": "Yellow", "green": "Green", "purple": "Purple", "brown": "Brown or beige", "orange": "Orange"}

# Checked in this order: grey before silver ("Grigio Silverstone"), every other family before
# red, so "Blu Corsa" is blue and "Rosso Corsa red" red.
_FAMILIES = [
    ("grey", r"grigio|gr[ae]y|\bgris\b|canna di fucile|gunmetal|titanio|\bferro\b"),
    ("silver", r"argento|silver|alluminio|aluminum|aluminium|\bsil\b|nurburg"),
    ("black", r"\bnero\b|black|\bblk\b"),
    ("white", r"bianco|white|\bwht\b|avorio|ivory"),
    ("blue", r"\bblu\b|blue|azzurro|celeste|\bnart\b|n\.a\.r\.t|tour de france|mirabeau"),
    ("yellow", r"giallo|yellow|\byel\b"),
    ("green", r"verde|green"),
    ("purple", r"viola|violetto|purple|prugna"),
    ("orange", r"arancio|orange"),
    ("brown", r"marrone|brown|bronzo|bronze|beige|\btan\b|sabbia|nocciola|champagne|\boro\b|\bgold\b"),
    ("red", r"rosso|russo|\bred\b|rubino"),
]
_RX = [(f, re.compile(p, re.I)) for f, p in _FAMILIES]
_SPECIAL = re.compile(r"extra sample|extra campionario|tailor made|special order|paint to sample|\bpts\b", re.I)


def special(name: Optional[str]) -> bool:
    """A special order paint: outside the standard range, whatever colour it is."""
    return bool(name and _SPECIAL.search(name))


def family(name: Optional[str]) -> Optional[str]:
    """The colour family a paint name belongs to, or None when the name says nothing."""
    if not name:
        return None
    s = name.strip()
    if not s or s.lower() in ("other", "n/a", "na", "unknown", "none", "-", "tbd"):
        return None
    if special(s) and s.lower() in ("extra sample", "extra campionario", "tailor made", "special order", "paint to sample", "pts"):
        return None                       # names the programme, not the colour
    for f, rx in _RX:
        if rx.search(s):
            return f
    return None


def points(con, day: date, model_id: Optional[str] = None) -> list[dict]:
    """One row per car: model, year, miles, price, sold flag, special order flag, family, paint name.

    A car whose only colour is a special order label (no site names the colour) is returned
    with family None, so callers can count it, and is left out of every fit."""
    today, since = day.isoformat(), (day - timedelta(days=SOLD_DAYS)).isoformat()
    where, args = ("AND a.model_id=?", (today, model_id)) if model_id else ("", (today,))
    # every colour any site ever gave the car, removed listings included
    names_of = defaultdict(list)
    for r in con.execute("""SELECT s.vehicle_key, s.exterior_color FROM sightings s
                            WHERE s.exterior_color IS NOT NULL AND s.vehicle_key IN (SELECT vehicle_key FROM daily_asks WHERE day=?)""", (today,)):
        names_of[r["vehicle_key"]].append(r["exterior_color"])
    out, seen = [], set()
    for r in con.execute(f"""
        SELECT a.vehicle_key, a.model_id, a.price_usd, MAX(s.year) yr, MAX(s.mileage_mi) mi
        FROM daily_asks a JOIN sightings s ON s.vehicle_key = a.vehicle_key AND s.removed_on IS NULL
        WHERE a.day=? {where} GROUP BY a.vehicle_key""", args):
        all_names = names_of.get(r["vehicle_key"], [])
        sp = int(any(special(c) for c in all_names))
        names = [c for c in all_names if family(c)]
        if not names and not sp:
            continue
        if r["mi"] is not None and r["price_usd"]:
            same = (r["model_id"], round(r["mi"]), round(r["price_usd"]))
            if same in seen:
                continue          # one car listed twice without a shared VIN, as in pipeline/mileage.py
            seen.add(same)
        # the car's colour as most of its listings give it
        name = Counter(names).most_common(1)[0][0] if names else None
        out.append({"model": r["model_id"], "year": r["yr"], "miles": r["mi"], "price": r["price_usd"], "sold": 0,
                    "special": sp, "family": family(name), "paint": name.strip() if name else "Extra Sample"})
    where = "AND model_id=?" if model_id else ""
    args = (since, today, model_id) if model_id else (since, today)
    for r in con.execute(f"""
        SELECT model_id, year, mileage_mi mi, price_usd, json_extract(raw, '$.exterior_color') col FROM lots
        WHERE status='sold' AND price_usd > 0 AND json_valid(raw) AND json_extract(raw, '$.exterior_color') IS NOT NULL
          AND ends_at IS NOT NULL AND substr(ends_at,1,10) BETWEEN ? AND ? {where}""", args):
        f, sp = family(r["col"]), int(special(r["col"]))
        if f or sp:
            out.append({"model": r["model_id"], "year": r["year"], "miles": r["mi"], "price": r["price_usd"], "sold": 1,
                        "special": sp, "family": f, "paint": r["col"].strip()})
    return [p for p in out if p["model"] and p["year"] and p["price"] and p["price"] > 0
            and p["miles"] is not None and 0 <= p["miles"] < MAX_MILES]


def _solve(xs: list[list[float]], ys: list[float]):
    """Least squares by the normal equations. Returns (coefficients, their standard errors) or None."""
    n, k = len(xs), len(xs[0])
    if n <= k:
        return None
    a = [[sum(x[i] * x[j] for x in xs) for j in range(k)] for i in range(k)]
    b = [sum(x[i] * y for x, y in zip(xs, ys)) for i in range(k)]
    inv = [[float(i == j) for j in range(k)] for i in range(k)]
    for c in range(k):                       # Gauss Jordan with partial pivoting
        p = max(range(c, k), key=lambda r: abs(a[r][c]))
        if abs(a[p][c]) < 1e-9:
            return None
        a[c], a[p], inv[c], inv[p] = a[p], a[c], inv[p], inv[c]
        piv = a[c][c]
        a[c] = [v / piv for v in a[c]]
        inv[c] = [v / piv for v in inv[c]]
        for r in range(k):
            if r != c and a[r][c]:
                f = a[r][c]
                a[r] = [u - f * v for u, v in zip(a[r], a[c])]
                inv[r] = [u - f * v for u, v in zip(inv[r], inv[c])]
    beta = [sum(inv[i][j] * b[j] for j in range(k)) for i in range(k)]
    sse = sum((y - sum(c * v for c, v in zip(beta, x))) ** 2 for x, y in zip(xs, ys))
    s2 = sse / (n - k)
    return beta, [math.sqrt(max(0.0, s2 * inv[i][i])) for i in range(k)]


def _premiums(pts: list[dict], levels) -> Optional[dict]:
    """Fit one regression. levels(p) gives the intercept group of a car (one per model, or per model and year band)."""
    unnamed = sum(1 for p in pts if not p["family"])
    pts = [p for p in pts if p["family"]]
    if not pts:
        return None
    counts = Counter(p["family"] for p in pts)
    if counts[BASELINE] < MIN_RED:
        return None
    fams = sorted(f for f, n in counts.items() if f != BASELINE and n >= MIN_FAMILY)
    if not fams:
        return None
    small = any(f != BASELINE and f not in fams for f in counts)
    groups = sorted({levels(p) for p in pts})
    years = [p["year"] for p in pts]
    my = sum(years) / len(years)
    cols = groups + ["year", "miles"] + (["sold"] if any(p["sold"] for p in pts) and not all(p["sold"] for p in pts) else []) \
        + fams + (["other"] if small else []) + (["special"] if 0 < sum(p["special"] for p in pts) < len(pts) else [])
    xs, ys = [], []
    for p in pts:
        f = p["family"]
        row = [float(levels(p) == g) for g in groups] + [p["year"] - my, math.log1p(p["miles"] / 1000)]
        if "sold" in cols:
            row.append(float(p["sold"]))
        row += [float(f == g) for g in fams]
        if small:
            row.append(float(f != BASELINE and f not in fams))
        if "special" in cols:
            row.append(float(p["special"]))
        xs.append(row)
        ys.append(math.log(p["price"]))
    # columns that never vary (every car the same year) would make the fit singular
    keep = [j for j, c in enumerate(cols) if c in groups or c in fams or c in ("other", "special") or len({x[j] for x in xs}) > 1]
    xs = [[x[j] for j in keep] for x in xs]
    cols = [cols[j] for j in keep]
    fit = _solve(xs, ys)
    if not fit:
        return None
    beta, se = fit
    paints = defaultdict(Counter)
    for p in pts:
        paints[p["family"]][p["paint"].title()] += 1
    out = []
    for f in [BASELINE] + fams:
        row = {"family": f, "label": LABELS[f], "n": counts[f], "paints": [[k, v] for k, v in paints[f].most_common(4)]}
        if f == BASELINE:
            row |= {"premium_pct": 0.0, "low_pct": 0.0, "high_pct": 0.0, "baseline": True}
        else:
            j = cols.index(f)
            row |= {"premium_pct": round((math.exp(beta[j]) - 1) * 100, 1),
                    "low_pct": round((math.exp(beta[j] - Z90 * se[j]) - 1) * 100, 1),
                    "high_pct": round((math.exp(beta[j] + Z90 * se[j]) - 1) * 100, 1)}
            row["verdict"] = "premium" if row["low_pct"] > 0 else "discount" if row["high_pct"] < 0 else "no clear difference"
        out.append(row)
    out.sort(key=lambda r: -r["premium_pct"])
    n_sp = sum(p["special"] for p in pts)
    sp = None
    if "special" in cols and n_sp >= MIN_FAMILY:
        j = cols.index("special")
        sp = {"n": n_sp, "premium_pct": round((math.exp(beta[j]) - 1) * 100, 1),
              "low_pct": round((math.exp(beta[j] - Z90 * se[j]) - 1) * 100, 1),
              "high_pct": round((math.exp(beta[j] + Z90 * se[j]) - 1) * 100, 1)}
        sp["verdict"] = "premium" if sp["low_pct"] > 0 else "discount" if sp["high_pct"] < 0 else "no clear difference"
    return {"special_order": sp, "special_unnamed": unnamed,"baseline": BASELINE, "n": len(pts), "for_sale": sum(not p["sold"] for p in pts),
            "sold": sum(p["sold"] for p in pts), "families": out,
            "too_few": sorted([[LABELS[f], n] for f, n in counts.items() if f != BASELINE and f not in fams], key=lambda r: -r[1])}


def model(con, model_id: str, day: date) -> Optional[dict]:
    """Colour premiums against red for one model, or None when there are too few cars."""
    return _premiums(points(con, day, model_id), lambda p: p["model"])


def market(con, day: date, pts: Optional[list[dict]] = None) -> Optional[dict]:
    """Colour premiums against red across every model, each car compared only within its model and year band."""
    pts = points(con, day) if pts is None else pts
    by_model = Counter(p["model"] for p in pts)
    pts = [p for p in pts if by_model[p["model"]] >= 4]
    return _premiums(pts, lambda p: f"{p['model']}:{p['year'] // 3}") if pts else None


def all_models(con, day: date) -> dict:
    """{"market": ..., "models": {model_id: ...}} from one read of the database."""
    pts = points(con, day)
    per = defaultdict(list)
    for p in pts:
        per[p["model"]].append(p)
    out = {}
    for m, ps in per.items():
        r = _premiums(ps, lambda p: p["model"])
        if r:
            out[m] = r
    return {"as_of": day.isoformat(), "market": market(con, day, pts), "models": out}
