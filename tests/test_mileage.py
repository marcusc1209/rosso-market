"""The mileage curve: which cars feed it, and the fit."""
import math
import random
from datetime import date

from pipeline import db, mileage


def _pts(fn, n=40, seed=1, noise=0.04):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        m = rnd.uniform(500, 30000)
        out.append([round(m), round(fn(m) * math.exp(rnd.gauss(0, noise))), "l", None, "", None, "", "2026-10-01"])
    return out


def test_fit_recovers_a_steady_decline():
    pts = _pts(lambda m: 300000 * math.exp(-0.00001 * m))         # about 1% per 1,000 miles
    f = mileage.fit(pts)
    assert f and f["n"] >= 36
    expect = (math.exp(-0.00001 * (f["high_miles"] - f["low_miles"])) - 1) * 100
    assert abs(f["change_pct"] - expect) < 4
    assert f["change_pct"] < -10


def test_outliers_are_dropped_and_do_not_bend_the_curve():
    pts = _pts(lambda m: 250000 * math.exp(-0.000008 * m), seed=3)
    pts += [[20000, 900000, "s", None, "race car", None, "", "2026-09-01"],
            [25000, 40000, "s", None, "wreck", None, "", "2026-09-01"]]
    f = mileage.fit(pts)
    assert f["dropped"] >= 2
    assert f["change_pct"] < -10


def test_no_curve_without_enough_cars_or_spread():
    assert mileage.fit(_pts(lambda m: 200000, n=6)) is None
    flat = [[5000 + i, 200000, "l", None, "", None, "", "2026-10-01"] for i in range(20)]
    assert mileage.fit(flat) is None


def test_points_use_listings_now_and_recent_sales_only(tmp_path):
    con = db.connect(tmp_path / "t.db")
    day = date(2026, 11, 1)
    con.execute("""INSERT INTO sightings (uid, source, vehicle_key, model_id, title, mileage_mi, first_seen, last_seen)
                   VALUES ('a:1','a','vin:X1','f8-tributo','2021 Ferrari F8 Tributo',5000,'2026-10-01','2026-11-01')""")
    con.execute("INSERT INTO daily_asks (day, vehicle_key, model_id, price_usd) VALUES ('2026-11-01','vin:X1','f8-tributo',290000)")
    # the same car again under a fingerprint key, same mileage and price: counted once
    con.execute("""INSERT INTO sightings (uid, source, vehicle_key, model_id, title, mileage_mi, first_seen, last_seen)
                   VALUES ('c:1','c','fp:x','f8-tributo','2021 Ferrari F8 Tributo',5000,'2026-10-02','2026-11-01')""")
    con.execute("INSERT INTO daily_asks (day, vehicle_key, model_id, price_usd) VALUES ('2026-11-01','fp:x','f8-tributo',290000)")
    rows = [("b:1", "2026-06-01", 8000, "sold"), ("b:2", "2024-01-15", 9000, "sold"),     # too old
            ("b:3", "2026-07-01", 7000, "unsold"), ("b:4", "2026-08-01", None, "sold")]   # not sold, no mileage
    for uid, d, mi, st in rows:
        con.execute("""INSERT INTO lots (uid, source, model_id, title, status, price_usd, ends_at, mileage_mi)
                       VALUES (?, 'b', 'f8-tributo', 'F8', ?, 280000, ?, ?)""", (uid, st, d + "T20:00:00Z", mi))
    pts = mileage.points(con, "f8-tributo", day)
    assert sorted((p[2], p[0]) for p in pts) == [("l", 5000), ("s", 8000)]
