"""Week over week median asks by model year."""
import json
from datetime import date

from pipeline import db
from pipeline.analytics import week_over_week, _price_on

DAY = date(2026, 11, 8)


def _con(tmp_path):
    return db.connect(tmp_path / "t.db")


def _ask(con, day, vk, model, year, price):
    con.execute("INSERT INTO daily_asks VALUES (?,?,?,?,?,?)", (day, vk, model, price, 1, 0))
    con.execute("""INSERT OR IGNORE INTO sightings(uid, source, vehicle_key, model_id, year, title, first_seen, last_seen, raw)
                   VALUES (?,?,?,?,?,?,?,?,?)""", ("s:" + vk, "x", vk, model, year, "t", day, day, "{}"))


def test_price_on_a_day_from_history():
    h = [["2026-10-20", 300000], ["2026-10-30", 290000], ["2026-11-05", 280000]]
    assert _price_on(h, "2026-10-15", "2026-11-01") == 290000
    assert _price_on(h, "2026-10-15", "2026-10-18") == 300000     # listed, no change yet
    assert _price_on(h, "2026-11-03", "2026-11-01") is None        # not listed yet


def test_week_over_week_from_snapshots(tmp_path):
    con = _con(tmp_path)
    for i in range(3):
        _ask(con, "2026-11-01", f"a{i}", "f8-tributo", 2020, 300000)
        _ask(con, "2026-11-01", f"b{i}", "f8-tributo", 2022, 400000)
    # this week: the 2020 cars fell 10% (cuts and cheaper arrivals), 2022 unchanged
    for i in range(3):
        _ask(con, "2026-11-08", f"a{i}", "f8-tributo", 2020, 270000)
        _ask(con, "2026-11-08", f"c{i}", "f8-tributo", 2022, 400000)
    w = week_over_week(con, DAY)
    assert w["how"] == "snapshot" and w["since"] == "2026-11-01"
    m = w["models"][0]
    assert m["id"] == "f8-tributo" and m["change"] == -5.0 and m["cars_now"] == 6 and m["cars_last"] == 6


def test_week_over_week_mix_of_years_is_not_a_move(tmp_path):
    con = _con(tmp_path)
    for i in range(4):
        _ask(con, "2026-11-01", f"old{i}", "f8-tributo", 2020, 300000)
    _ask(con, "2026-11-01", "new0", "f8-tributo", 2023, 450000)
    _ask(con, "2026-11-01", "new1", "f8-tributo", 2023, 450000)
    # same prices per year, but many more newer cars this week: the plain median would jump
    for i in range(2):
        _ask(con, "2026-11-08", f"old{i}", "f8-tributo", 2020, 300000)
    for i in range(6):
        _ask(con, "2026-11-08", f"n{i}", "f8-tributo", 2023, 450000)
    m = week_over_week(con, DAY)["models"][0]
    assert m["change"] == 0.0 and m["median"] == 450000 and m["median_last"] == 300000


def test_week_over_week_rebuilt_from_price_histories(tmp_path):
    con = _con(tmp_path)
    for i in range(5):
        raw = json.dumps({"extra": {"listed_on": "2026-10-01", "price_history": [["2026-10-01", 300000], ["2026-11-05", 285000]]}})
        con.execute("""INSERT INTO sightings(uid, source, vehicle_key, model_id, year, title, first_seen, last_seen, raw)
                       VALUES (?,?,?,?,?,?,?,?,?)""", (f"at:{i}", "autotrader_us", f"vin:{i}", "f8-tributo", 2021, "t", "2026-11-08", "2026-11-08", raw))
        con.execute("INSERT INTO daily_asks VALUES (?,?,?,?,?,?)", ("2026-11-08", f"vin:{i}", "f8-tributo", 285000, 1, 0))
    # a car with no history (another site) is left out of both weeks
    _ask(con, "2026-11-08", "other", "f8-tributo", 2021, 100000)
    w = week_over_week(con, DAY)
    assert w["how"] == "rebuilt" and w["since"] == "2026-11-01"
    m = w["models"][0]
    assert m["change"] == -5.0 and m["cars_now"] == 5 and m["cars_last"] == 5
