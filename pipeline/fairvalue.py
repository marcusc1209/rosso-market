"""Fair value: what a car should sell for today, from its model, year, mileage, colour and gearbox.

Data: every auction sale of the last three years (not sold high bids are left out) and every car for
sale today at its asking price. Colour and mileage come from the record's own fields, or, for auction
lots that have none, from the catalogue text ("finished in Rosso Corsa", "20k miles indicated").

Fit: one regression on log price over every model at once, so a model with few sales borrows what the
others show about mileage, colour and the gap between asks and sales:

    ln price = model level
             + (year spread, miles, months since the sale, asking not sold) each with a market wide
               slope and a per model adjustment pulled toward it (ridge penalty)
             + colour family against red, special order, mileage unknown   (market wide)
             + factory manual, per model, for the models sold with both gearboxes

Cars far off the fit (a race car, a wreck, a typo) are dropped and the fit run again. The fair value of
a car is the fitted sale price today (sold, zero months ago). Its range is the 80% band of how far
sales of that model land from the fit.

Accuracy: each model's sales are valued in turn by a fit that never saw them (five folds). A model's
fair values are published only when it has at least MIN_SOLD sales and that held out error (median
miss) is at most MAX_MISS.
"""
from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Optional

import numpy as np

from . import colors, normalize, taxonomy

log = logging.getLogger("fairvalue")

SOLD_DAYS = 1096           # three years of sales
MIN_SOLD = 6               # sales of the model in that window before its fair values are shown
MAX_MISS = 0.20            # held out median miss above which a model's fair values are not shown
MAX_MILES = 250_000
Z80 = 1.2816
LAMBDA = 2.0               # pull of each model's slopes toward the market wide slope
FOLDS = 5
NO_VALUE = {"classic-other"}          # a catch all bucket of unrelated cars
FAR = 2.5                  # an ask over 2.5 times (or under 1/2.5 of) the fair value is most likely another car or a placeholder price

_FINISH_RX = re.compile(r"(?:finished|refinished|painted|repainted|wears|wearing|presented)\s+(?:in\s+)?"
                        r"((?:[A-Z][\w'’]*)(?:[\s-]+[A-Z][\w'’]*){0,4})")
_MILES_RX = re.compile(r"\b(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?k?)\s*(?:actual\s+)?miles\s+(?:are\s+|were\s+)?(?:indicated|shown|showing)", re.I)
_SHOWS_RX = re.compile(r"\b(?:shows|indicates|displays)\s+(?:just\s+|only\s+|roughly\s+|~)?(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?k?)\s*(?:miles|mi\b)", re.I)


_PROJECT = re.compile(r"\b(project|salvage|fire[- ]damaged|flood|rolling chassis|barn find|non[- ]running|needs work)\b", re.I)
_EDITION = re.compile(r"\b(anniversary|tailor[- ]made|one[- ]off|special edition|vetroresina|limited edition|\bfuori serie)\b", re.I)


def title_flags(title: str) -> dict:
    """A project car sells far below a usable one, a special edition far above."""
    t = title or ""
    return {"project": int(bool(_PROJECT.search(t))), "edition": int(bool(_EDITION.search(t)))}


def _text(raw: dict) -> str:
    ex = raw.get("extra") or {}
    return " ".join(str(x) for x in (ex.get("excerpt"), ex.get("subtitle"), ex.get("description"), raw.get("description")) if x)


def colour_from_text(text: str) -> Optional[str]:
    """The paint a catalogue names: 'finished in Grigio Alloy Metallic over Blu Scuro leather'."""
    for m in _FINISH_RX.finditer(text or ""):
        name = m.group(1).strip()
        if colors.family(name) or colors.special(name):
            return name
    return None


def miles_from_text(text: str) -> Optional[float]:
    for rx in (_MILES_RX, _SHOWS_RX):
        m = rx.search(text or "")
        if m:
            s = m.group(1).replace(",", "").lower()
            v = float(s[:-1]) * 1000 if s.endswith("k") else float(s)
            if 0 <= v < MAX_MILES:
                return v
    return None


def _gear_of(gb: dict, table: str, vk, uid) -> Optional[str]:
    vk = vk or ""
    if vk.startswith(("vin:", "chassis:")):
        k = vk
    else:
        k = ("s:" + vk) if table == "sightings" and vk else "uid:" + uid
    return gb.get(k)


def dataset(con, day: date, gb: Optional[dict] = None, skip=frozenset()) -> list[dict]:
    """One row per car: sales of the last three years and today's asks."""
    gb = gb or {}
    today, since = day.isoformat(), (day - timedelta(days=SOLD_DAYS)).isoformat()
    rows = []
    for r in con.execute("""SELECT uid, source, vehicle_key, model_id, year, title, price_usd, mileage_mi, substr(ends_at,1,10) d, raw FROM lots
                            WHERE status='sold' AND price_usd > 0 AND ends_at IS NOT NULL AND substr(ends_at,1,10) BETWEEN ? AND ?""",
                         (since, today)):
        if r["uid"] in skip or not r["model_id"]:
            continue
        try:
            raw = json.loads(r["raw"]) if r["raw"] else {}
        except ValueError:
            raw = {}
        text = _text(raw)
        col = raw.get("exterior_color") or colour_from_text(text)
        miles = r["mileage_mi"]
        if miles is None:
            miles = normalize.mileage_from_title(r["title"] or "") or miles_from_text(text)
        year = r["year"] or raw.get("year")
        rows.append({"kind": "lot", "id": r["uid"], "key": r["vehicle_key"], "model": r["model_id"], "year": year,
                     "miles": miles, "paint": col, "price": float(r["price_usd"]), "sold": 1,
                     "months": (day - date.fromisoformat(r["d"])).days / 30.44, "house": r["source"], **title_flags(r["title"]),
                     "gear": _gear_of(gb, "lots", r["vehicle_key"], r["uid"])})
    rows += for_sale(con, day, gb)
    out = []
    for p in rows:
        try:
            p["year"] = int(p["year"]) if p["year"] else None
        except (TypeError, ValueError):
            p["year"] = None
        if p["miles"] is not None and not 0 <= p["miles"] < MAX_MILES:
            p["miles"] = None
        p["family"] = colors.family(p["paint"]) if p["paint"] else None
        p["special"] = int(bool(p["paint"]) and colors.special(p["paint"]))
        if p["model"] in taxonomy.DUAL_GEARBOX:
            p["gear"] = p["gear"] or "f1"
        else:
            p["gear"] = None
        if p["year"] and p["price"] > 0:
            out.append(p)
    return out


def for_sale(con, day: date, gb: Optional[dict] = None) -> list[dict]:
    """Every car for sale today with its asking price, year, mileage and colour."""
    gb = gb or {}
    today = day.isoformat()
    names = defaultdict(list)
    for r in con.execute("""SELECT vehicle_key, exterior_color FROM sightings WHERE exterior_color IS NOT NULL
                            AND vehicle_key IN (SELECT vehicle_key FROM daily_asks WHERE day=?)""", (today,)):
        names[r["vehicle_key"]].append(r["exterior_color"])
    out = []
    for r in con.execute("""SELECT a.vehicle_key, a.model_id, a.price_usd, MAX(s.year) yr, MAX(s.mileage_mi) mi, MAX(s.title) title
                            FROM daily_asks a JOIN sightings s ON s.vehicle_key = a.vehicle_key AND s.removed_on IS NULL
                            WHERE a.day=? AND a.price_usd > 0 GROUP BY a.vehicle_key""", (today,)):
        ns = names.get(r["vehicle_key"], [])
        named = [c for c in ns if colors.family(c)]
        paint = Counter(named).most_common(1)[0][0] if named else next((c for c in ns if colors.special(c)), None)
        out.append({"kind": "ask", "id": r["vehicle_key"], "key": r["vehicle_key"], "model": r["model_id"], "year": r["yr"],
                    "miles": r["mi"], "paint": paint, "price": float(r["price_usd"]), "sold": 0, "months": 0.0, "house": None,
                    **title_flags(r["title"]),
                    "gear": _gear_of(gb, "sightings", r["vehicle_key"], "")})
    return out


class Fit:
    """The fitted regression and what each model needs to value a car."""

    def __init__(self, rows: list[dict], lam: float = LAMBDA):
        self.lam = lam
        self.models = sorted({p["model"] for p in rows if p["model"] not in NO_VALUE})
        rows = [p for p in rows if p["model"] in set(self.models)]
        fam = Counter(p["family"] for p in rows if p["family"])
        self.fams = sorted(f for f, n in fam.items() if f != colors.BASELINE and n >= 8)
        self.dual = sorted({p["model"] for p in rows if p["gear"] == "manual"})
        by = defaultdict(list)
        for p in rows:
            by[p["model"]].append(p)
        self.year_mid = {m: median(p["year"] for p in ps) for m, ps in by.items()}
        self.miles_mid = {}
        for m, ps in by.items():
            mi = [p["miles"] for p in ps if p["miles"] is not None]
            self.miles_mid[m] = median(mi) if mi else None
        hc = Counter(p["house"] for p in rows if p.get("house"))
        self.houses = sorted(h for h, n in hc.items() if n >= 15)
        # the sales mix of each model across houses: a fair value is the price at the model's usual mix
        self.house_mix = {}
        for m, ps in by.items():
            hs = Counter(p["house"] for p in ps if p.get("house") in self.houses)
            tot = sum(hs.values())
            self.house_mix[m] = {h: n / tot for h, n in hs.items()} if tot else {}
        all_mi = [p["miles"] for p in rows if p["miles"] is not None]
        self.miles_all = median(all_mi) if all_mi else 10000.0
        self._build_cols()
        keep = rows
        for _ in range(2):                      # fit, drop cars far off it, fit again
            beta = self._solve(keep)
            res = np.array([math.log(p["price"]) - self._predict_log(beta, p) for p in keep])
            # one market wide cut: held out sales are valued better this way than with a cut per model
            mid = float(np.median(res))
            mad = float(np.median(np.abs(res - mid))) * 1.4826
            cut = max(3.0 * mad, 0.25)
            nxt = [p for p, r in zip(keep, res) if abs(r - mid) <= cut]
            if len(nxt) == len(keep):
                break
            keep = nxt
        self.beta = beta
        self.rows = keep
        self.dropped = len(rows) - len(keep)
        # spread of sales around the fit, per model, pulled toward the market wide spread when sales are few
        sres = defaultdict(list)
        for p in keep:
            if p["sold"]:
                sres[p["model"]].append(math.log(p["price"]) - self._predict_log(beta, p))
        allr = [r for rs in sres.values() for r in rs]
        g = float(np.sqrt(np.mean(np.square(allr)))) if allr else 0.2
        self.sigma = {}
        for m in self.models:
            rs = sres.get(m, [])
            n = len(rs)
            own = float(np.sum(np.square(rs))) if rs else 0.0
            self.sigma[m] = math.sqrt((own + 5 * g * g) / (n + 5))
        self.n_sold = Counter(p["model"] for p in keep if p["sold"])

    # columns: model levels, market wide slopes, per model slope adjustments, colour, manual
    def _build_cols(self):
        cols = [("level", m) for m in self.models]
        self.slopes = ("year", "miles", "months", "ask")
        cols += [("g", s) for s in self.slopes] + [("g", "miles_unknown"), ("g", "special"), ("g", "project"), ("g", "edition")]
        cols += [("fam", f) for f in self.fams] + [("g", "colour_unknown")]
        cols += [("g", "manual")] + [("manual", m) for m in self.dual]
        cols += [("house", h) for h in self.houses]
        cols += [(s, m) for s in self.slopes for m in self.models]
        self.cols = cols
        self.ix = {c: i for i, c in enumerate(cols)}
        pen = np.zeros(len(cols))
        for i, (a, b) in enumerate(cols):
            if a in self.slopes or a == "manual":
                pen[i] = self.lam
            elif a in ("fam", "g", "house"):
                pen[i] = 0.5                     # a light pull, so a rare colour does not swing wildly
        self.pen = pen

    def _feats(self, p: dict) -> dict:
        m = p["model"]
        mi = p["miles"]
        mid = self.miles_mid.get(m) if self.miles_mid.get(m) is not None else self.miles_all
        x = {"year": (p["year"] - self.year_mid.get(m, p["year"])) / 5.0,
             "miles": math.log1p((mi if mi is not None else mid) / 1000) - math.log1p(mid / 1000),
             "months": p["months"] / 12.0, "ask": 1.0 - p["sold"]}
        f = {("level", m): 1.0}
        for s, v in x.items():
            f[("g", s)] = v
            f[(s, m)] = v
        if mi is None:
            f[("g", "miles_unknown")] = 1.0
        if p["special"]:
            f[("g", "special")] = 1.0
        for k in ("project", "edition"):
            if p.get(k):
                f[("g", k)] = 1.0
        if p["family"] in self.fams:
            f[("fam", p["family"])] = 1.0
        elif not p["family"] and not p["special"]:
            f[("g", "colour_unknown")] = 1.0
        if p.get("house") in self.houses:
            f[("house", p["house"])] = 1.0
        elif p.get("house") == "mix":
            for h, w in self.house_mix.get(m, {}).items():
                f[("house", h)] = w
        if p["gear"] == "manual":
            f[("g", "manual")] = 1.0
            if m in self.dual:
                f[("manual", m)] = 1.0
        return f

    def _solve(self, rows):
        X = np.zeros((len(rows), len(self.cols)))
        y = np.empty(len(rows))
        for i, p in enumerate(rows):
            for c, v in self._feats(p).items():
                j = self.ix.get(c)
                if j is not None:
                    X[i, j] = v
            y[i] = math.log(p["price"])
        A = X.T @ X + np.diag(self.pen) + np.eye(len(self.cols)) * 1e-6
        return np.linalg.solve(A, X.T @ y)

    def _predict_log(self, beta, p) -> float:
        return float(sum(beta[self.ix[c]] * v for c, v in self._feats(p).items() if c in self.ix))

    def coef(self, kind, name) -> float:
        j = self.ix.get((kind, name))
        return float(self.beta[j]) if j is not None else 0.0

    def value(self, car: dict) -> Optional[dict]:
        """Fair value of a car today: {value, low, high, adjustments [[label, pct]]}."""
        m = car["model"]
        if ("level", m) not in self.ix:
            return None
        if not car.get("year"):
            return None
        q = dict(car, sold=1, months=0.0, house="mix")
        lv = self._predict_log(self.beta, q)
        s = self.sigma.get(m, 0.2)
        # what moved this car off a typical one of its model: mileage, colour, gearbox
        base = dict(q, miles=self.miles_mid.get(m) or self.miles_all, family=colors.BASELINE, special=0,
                    gear="f1" if car.get("gear") else None)
        parts = []
        if car.get("miles") is not None:
            d = self._predict_log(self.beta, dict(base, miles=car["miles"])) - self._predict_log(self.beta, base)
            parts.append(["Mileage", round((math.exp(d) - 1) * 100, 1)])
        if car.get("family") or car.get("special"):
            d = self._predict_log(self.beta, dict(base, family=car.get("family"), special=car.get("special", 0))) \
                - self._predict_log(self.beta, base)
            parts.append(["Colour", round((math.exp(d) - 1) * 100, 1)])
        if car.get("gear") == "manual":
            d = self._predict_log(self.beta, dict(base, gear="manual")) - self._predict_log(self.beta, base)
            parts.append(["Manual gearbox", round((math.exp(d) - 1) * 100, 1)])
        return {"value": _round(math.exp(lv)), "low": _round(math.exp(lv - Z80 * s)), "high": _round(math.exp(lv + Z80 * s)),
                "adjustments": parts}


def _round(x: float) -> int:
    step = 1000 if x < 1_000_000 else 5000
    return int(round(x / step) * step)


def backtest(rows: list[dict], lam: float = LAMBDA, folds: int = FOLDS, seed: int = 7) -> dict:
    """Held out miss of each sale, valued by a fit that did not see it: model -> [|ln miss|, ...]."""
    rng = np.random.default_rng(seed)
    sold_ix = [i for i, p in enumerate(rows) if p["sold"]]
    fold = {i: int(f) for i, f in zip(sold_ix, rng.integers(0, folds, len(sold_ix)))}
    miss = defaultdict(list)
    for k in range(folds):
        test = [i for i in sold_ix if fold[i] == k]
        train = [p for i, p in enumerate(rows) if fold.get(i, -1) != k]
        f = Fit(train, lam)
        for i in test:
            p = rows[i]
            if ("level", p["model"]) not in f.ix:
                continue
            q = dict(p)
            pred = f._predict_log(f.beta, q)
            miss[p["model"]].append(abs(math.log(p["price"]) - pred))
    return miss


def build(con, day: date, gb: Optional[dict] = None, skip=frozenset()) -> dict:
    """{"cars": {listing vehicle key or lot uid: fair value}, "models": {model id: accuracy}}"""
    rows = dataset(con, day, gb, skip)
    if not rows:
        return {"cars": {}, "lots": {}, "models": {}, "fit": None}
    fit = Fit(rows)
    miss = backtest(rows)
    models = {}
    for m in fit.models:
        ms = miss.get(m, [])
        err = math.exp(median(ms)) - 1 if ms else None
        ok = fit.n_sold[m] >= MIN_SOLD and err is not None and err <= MAX_MISS
        models[m] = {"sold": fit.n_sold[m], "miss_pct": round(err * 100, 1) if err is not None else None, "shown": ok}
    out = {}
    for p in rows:
        if p["kind"] != "ask" or not models.get(p["model"], {}).get("shown"):
            continue
        v = fit.value(p)
        if v and 1 / FAR <= p["price"] / v["value"] <= FAR:
            v["ask_pct"] = round((p["price"] / v["value"] - 1) * 100, 1)
            out[p["key"]] = v
    lots = {}
    for p in upcoming(con, day, gb):
        if models.get(p["model"], {}).get("shown"):
            v = fit.value(p)
            if v:
                lots[p["id"]] = v
    log.info("fair value: %d cars for sale valued, %d of %d models shown, %d outliers left out",
             len(out), sum(m["shown"] for m in models.values()), len(models), fit.dropped)
    return {"cars": out, "lots": lots, "models": models, "fit": fit}


def upcoming(con, day: date, gb: Optional[dict] = None) -> list[dict]:
    """Live and upcoming auction lots, described the same way, to value before they sell."""
    gb = gb or {}
    out = []
    for r in con.execute("""SELECT uid, vehicle_key, model_id, year, title, mileage_mi, raw FROM lots
                            WHERE status IN ('live','upcoming') AND last_seen >= ?""", ((day - timedelta(days=1)).isoformat(),)):
        try:
            raw = json.loads(r["raw"]) if r["raw"] else {}
        except ValueError:
            raw = {}
        text = _text(raw)
        paint = raw.get("exterior_color") or colour_from_text(text)
        miles = r["mileage_mi"]
        if miles is None:
            miles = normalize.mileage_from_title(r["title"] or "") or miles_from_text(text)
        try:
            year = int(r["year"] or raw.get("year") or 0) or None
        except (TypeError, ValueError):
            year = None
        if miles is not None and not 0 <= miles < MAX_MILES:
            miles = None
        gear = (_gear_of(gb, "lots", r["vehicle_key"], r["uid"]) or "f1") if r["model_id"] in taxonomy.DUAL_GEARBOX else None
        out.append({"kind": "lot", "id": r["uid"], "key": r["vehicle_key"], "model": r["model_id"], "year": year, "miles": miles,
                    "paint": paint, "family": colors.family(paint) if paint else None,
                    "special": int(bool(paint) and colors.special(paint)), "gear": gear, **title_flags(r["title"])})
    return out


def valuer(fit: Fit, models: dict, day: date) -> dict:
    """What Value my car needs to price a car in the browser, for each model whose fair values are shown.

    ln value = c + year * (year - year_mid) / 5 + miles * (ln(1 + mi/1000) - ln(1 + miles_mid/1000))
               + colour + special paint + special edition + manual"""
    fam_n = Counter(p["family"] for p in fit.rows if p["family"])
    colours = {colors.BASELINE: 0.0}
    for f in fit.fams:
        colours[f] = round(fit.coef("fam", f), 5)
    tot = sum(fam_n[f] for f in colours) or 1
    unsure = round(sum(colours[f] * fam_n[f] for f in colours) / tot, 5)
    years = defaultdict(list)
    for p in fit.rows:
        years[p["model"]].append(p["year"])
    out = []
    for m, info in models.items():
        if not info.get("shown"):
            continue
        tm = taxonomy.BY_ID.get(m)
        ym, mm = fit.year_mid[m], fit.miles_mid.get(m) or fit.miles_all
        base = {"model": m, "year": ym, "miles": mm, "family": colors.BASELINE, "special": 0, "sold": 1, "months": 0.0,
                "house": "mix", "gear": "f1" if m in taxonomy.DUAL_GEARBOX else None, "project": 0, "edition": 0}
        row = {"id": m, "name": tm.name if tm else m, "family": tm.family if tm else None,
               "c": round(fit._predict_log(fit.beta, base), 5),
               "ym": ym, "by": round(fit.coef("g", "year") + fit.coef("year", m), 5),
               "mm": round(mm), "bm": round(fit.coef("g", "miles") + fit.coef("miles", m), 5),
               "sigma": round(fit.sigma.get(m, 0.2), 4), "years": [min(years[m]), max(years[m])],
               "sold": info["sold"], "miss_pct": info["miss_pct"]}
        if m in taxonomy.DUAL_GEARBOX:
            row["manual"] = round(fit.coef("g", "manual") + fit.coef("manual", m), 5)
        out.append(row)
    out.sort(key=lambda r: r["name"])
    return {"as_of": day.isoformat(), "colours": colours, "unsure": unsure,
            "labels": {f: colors.LABELS[f] for f in colours},
            "special": round(fit.coef("g", "special"), 5), "edition": round(fit.coef("g", "edition"), 5), "models": out}
