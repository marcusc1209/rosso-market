"""Colour premiums against red: paint names to families, and the fit."""
import math
import random
from datetime import date

from pipeline import colors, db


def test_paint_names_fall_into_families():
    cases = {"Rosso Corsa": "red", "RED": "red", "Russo Corsa": "red", "Rosso Corsa red": "red", "Rubino Micalizzato": "red",
             "Blu Corsa": "blue", "Blu NART": "blue", "Azzurro California": "blue", "Celeste F169S": "blue",
             "Grigio Silverstone": "grey", "Canna di Fucile": "grey", "Silver grey": "grey",
             "Argento Nurburgring": "silver", "Alluminio Opaco": "silver",
             "Nero Daytona": "black", "BLK": "black", "Black/gold": "black", "Bianco Avus": "white", "Avorio": "white",
             "Giallo Modena Yellow": "yellow", "Verde British": "green", "Viola Hong Kong": "purple",
             "Extra Sample": "special"}
    for name, fam in cases.items():
        assert colors.family(name) == fam, name
    for name in (None, "", "Other", "N/A"):
        assert colors.family(name) is None


def _cars(model, n, seed, premiums, base=300000):
    """Cars whose price follows year and mileage, plus a set premium per colour family."""
    rnd = random.Random(seed)
    fams = list(premiums)
    out = []
    for i in range(n):
        f = fams[i % len(fams)]
        yr = rnd.randint(2019, 2024)
        mi = rnd.uniform(500, 25000)
        p = base * 1.06 ** (yr - 2019) * math.exp(-0.06 * math.log1p(mi / 1000)) * (1 + premiums[f]) * math.exp(rnd.gauss(0, 0.02))
        out.append({"model": model, "year": yr, "miles": mi, "price": p, "sold": 0, "family": f, "paint": f.title()})
    return out


def test_premiums_are_measured_against_red_and_adjusted_for_year():
    pts = _cars("f8-tributo", 120, 1, {"red": 0, "blue": 0.08, "black": -0.05, "white": 0})
    # the blue cars are also the newest: a raw median would credit blue with the year
    for p in pts:
        if p["family"] == "blue":
            p["year"] += 2
            p["price"] *= 1.06 ** 2
    r = colors._premiums(pts, lambda p: p["model"])
    got = {f["family"]: f for f in r["families"]}
    assert got["red"]["premium_pct"] == 0 and got["red"]["baseline"]
    assert abs(got["blue"]["premium_pct"] - 8) < 2 and got["blue"]["verdict"] == "premium"
    assert abs(got["black"]["premium_pct"] + 5) < 2 and got["black"]["verdict"] == "discount"
    assert got["white"]["verdict"] == "no clear difference"


def test_small_families_and_too_little_red_are_not_reported():
    pts = _cars("roma", 30, 2, {"red": 0, "blue": 0.05})
    pts += _cars("roma", 2, 3, {"green": 0.2})
    r = colors._premiums(pts, lambda p: p["model"])
    assert "green" not in {f["family"] for f in r["families"]}
    assert r["too_few"] == [["Green", 2]]
    assert colors._premiums(_cars("roma", 20, 4, {"blue": 0, "black": 0}), lambda p: p["model"]) is None


def test_market_wide_compares_within_each_model():
    # a dear model painted mostly blue must not make blue look dear
    pts = _cars("sf90-stradale", 60, 5, {"blue": 0, "red": 0}, base=500000) + _cars("sf90-stradale", 10, 6, {"blue": 0}, base=500000)
    pts += _cars("roma", 60, 7, {"red": 0, "blue": 0}, base=200000)
    r = colors.market(None, date(2026, 11, 1), pts)
    blue = next(f for f in r["families"] if f["family"] == "blue")
    assert abs(blue["premium_pct"]) < 3


def test_points_read_listings_today_and_coloured_sales(tmp_path):
    con = db.connect(tmp_path / "t.db")
    rows = [("a:1", "vin:A", "Rosso Corsa", 5000, 290000), ("a:2", "vin:B", "Nero", 9000, 270000),
            ("a:3", "vin:C", None, 7000, 280000),                  # no colour
            ("c:1", "fp:x", "Rosso Corsa", 5000, 290000)]          # vin:A again on another site
    for uid, key, col, mi, price in rows:
        con.execute("""INSERT INTO sightings (uid, source, vehicle_key, model_id, year, title, mileage_mi, exterior_color, first_seen, last_seen)
                       VALUES (?, 'a', ?, 'f8-tributo', 2021, 'F8', ?, ?, '2026-10-01', '2026-11-01')""", (uid, key, mi, col))
        con.execute("INSERT OR IGNORE INTO daily_asks (day, vehicle_key, model_id, price_usd) VALUES ('2026-11-01', ?, 'f8-tributo', ?)", (key, price))
    con.execute("""INSERT INTO lots (uid, source, model_id, year, title, status, price_usd, ends_at, mileage_mi, raw)
                   VALUES ('p:1', 'pcarmarket', 'f8-tributo', 2020, 'F8', 'sold', 260000, '2026-09-01T20:00:00Z', 8000, '{"exterior_color": "Giallo Modena"}')""")
    pts = colors.points(con, date(2026, 11, 1), "f8-tributo")
    assert sorted((p["family"], p["sold"]) for p in pts) == [("black", 0), ("red", 0), ("yellow", 1)]
