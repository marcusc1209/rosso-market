import csv
import io
from datetime import date

from pipeline import tracker, weekly


SHEET = """## Sheet name: 599 GTB
599 GTB Market Dashboard,,,
Year,VIN,Mileage,Location,Asking,,
2007,ZFFFC60A570150565,"21,962","Tampa, FL","$276,581",,
SUMMARY - 599 GTB (11/5)
Current listings:,,,,1,2
Year,VIN,Mileage,Location, 12/29 Asking Price , 1/5 Asking Price , 1/12 Asking Price
2007,ZFFFC60A570150565,"21,962","Tampa, FL","$299,581",,"$296,581"
2008,ZFFFC60A880163215,,"Miami, FL",,No Price Listed,"$317,926"

## Sheet name: Instructions
HOW TO UPDATE
"""


def test_parse_export_rolls_the_year_and_reads_no_price():
    tabs = tracker.split_export(SHEET)
    weeks, cars = tracker.parse_rows(tabs["599 GTB"])
    assert weeks == ["2026-12-29", "2027-01-05", "2027-01-12"]
    assert [c["vin"] for c in cars] == ["ZFFFC60A570150565", "ZFFFC60A880163215"]
    assert cars[0]["prices"] == {"2026-12-29": 299581, "2027-01-12": 296581}
    assert cars[1]["prices"]["2027-01-05"] == tracker.NP and cars[1]["mileage"] is None


def test_write_and_load_round_trip(tmp_path):
    weeks, cars = tracker.parse_rows(tracker.split_export(SHEET)["599 GTB"])
    cfg = tmp_path / "sources.yaml"
    tracker.write_tab(tmp_path / "tracker", "599-gtb", weeks, cars)
    got = tracker.load(cfg)["599-gtb"]
    assert got[1]["prices"] == {"2027-01-05": tracker.NP, "2027-01-12": 317926}


class _Con:
    """Enough of a database for weekly.series: no listings of our own."""
    def execute(self, *a):
        return iter(())


def test_weekly_counts_arrivals_departures_and_fills_skipped_weeks():
    t = [{"vin": "A" * 17, "prices": {"2026-03-02": 100, "2026-03-09": 100, "2026-03-23": 90}},
         {"vin": "B" * 17, "prices": {"2026-03-09": 200, "2026-03-23": tracker.NP}},
         {"vin": "C" * 17, "prices": {"2026-03-02": 300}}]
    w = weekly.series(_Con(), "x", date(2026, 3, 25), t, since="2026-11-01")["weeks"]
    assert [r[0] for r in w] == ["2026-03-02", "2026-03-09", "2026-03-16", "2026-03-23"]
    assert [r[1] for r in w] == [2, 2, 2, 2]          # the skipped week of 3/16 carries 3/9
    assert w[1][2:4] == [1, 1]                         # B arrived, C left
    assert w[3][4] == 90 and w[0][5] == 1              # listed without a price is counted but not priced


def test_selling_times_by_colour():
    import sqlite3
    from pipeline import colors, db
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    con.executescript(db.SCHEMA)
    rows = [("a", "vin:A", "296-gtb", "Rosso Corsa", "2026-09-01", None), ("b", "vin:B", "296-gtb", "Rosso Corsa", "2026-08-01", "2026-09-10"),
            ("c", "vin:C", "296-gtb", "Nero", "2026-10-01", None)]
    for uid, vk, m, col, fs, rem in rows:
        con.execute("INSERT INTO sightings (uid, vehicle_key, model_id, exterior_color, first_seen, removed_on) VALUES (?,?,?,?,?,?)", (uid, vk, m, col, fs, rem))
    for vk in ("vin:A", "vin:C"):
        con.execute("INSERT INTO daily_asks (day, vehicle_key, model_id, price_usd) VALUES ('2026-11-01', ?, '296-gtb', 300000)", (vk,))
    t = colors.selling_times(con, date(2026, 11, 1))["models"]["296-gtb"]
    assert t["red"] == {"n_listed": 1, "n_sold": 1, "days_listed": 61, "days_to_sell": 40}
    assert t["black"]["days_listed"] == 31 and t["black"]["n_sold"] == 0
