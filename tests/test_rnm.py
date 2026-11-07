import sqlite3
from datetime import date

from pipeline import db
from pipeline.sources.rmsothebys import map_item


def test_rm_not_sold_is_reserve_not_met_with_its_estimate():
    x = {"publicName": "2009 Ferrari Scuderia Spider 16M", "value": "£650,000 - £750,000 GBP", "valueType": "Not Sold",
         "sold": False, "isStillForSale": False, "header": "The London Auction 2026",
         "link": "/auctions/lf26/lots/r0123-2009-ferrari-scuderia-spider-16m/"}
    rec = map_item(x, "rmsothebys", "Results")
    assert rec and rec.status == "unsold" and rec.price is None
    assert rec.estimate_low == 650000 and rec.estimate_high == 750000


def test_rm_sold_and_withheld_unchanged():
    sold = map_item({"publicName": "2022 Ferrari Daytona SP3", "value": "£8,161,250 GBP", "valueType": "Sold", "sold": True,
                     "header": "The London Auction 2026", "link": "/auctions/lf26/lots/r1/"}, "rmsothebys", "Results")
    assert sold.status == "sold" and sold.price == 8161250
    assert map_item({"publicName": "2003 Ferrari Enzo", "value": "Sold", "valueType": "", "sold": True,
                     "header": "Some Auction", "link": "/auctions/x/lots/r2/"}, "rmsothebys", "Results") is None


def test_live_lot_pulled_after_its_end_is_recorded_as_rnm():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    db.init(con) if hasattr(db, "init") else con.executescript(db.SCHEMA)
    con.execute("""INSERT INTO lots (uid, source, status, price_usd, ends_at, last_seen, title)
                   VALUES ('somo:a', 'somo', 'live', 410000, '2026-11-01T20:00:00+00:00', '2026-11-01', 'ended, pulled'),
                          ('somo:b', 'somo', 'live', 300000, '2026-11-01T20:00:00+00:00', '2026-11-07', 'still listed today'),
                          ('somo:c', 'somo', 'live', 200000, '2099-01-01T20:00:00+00:00', '2026-11-01', 'not ended')""")
    n = db.mark_ended(con, "somo", date(2026, 11, 7))
    st = {r["uid"]: (r["status"], r["price_usd"]) for r in con.execute("SELECT uid, status, price_usd FROM lots")}
    assert n == 1 and st["somo:a"] == ("unsold", 410000) and st["somo:b"][0] == "live" and st["somo:c"][0] == "live"
