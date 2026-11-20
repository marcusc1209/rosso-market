"""A 1971 365 GTC/4 listed as "365 GTC4" is a classic, not a GTC4Lusso, and is one car across sites."""
import sqlite3

from pipeline import clean, db, taxonomy
from pipeline.dedupe import vehicle_key
from pipeline.normalize import chassis_number, normalize, padded_chassis
from pipeline.records import LISTING, RawRecord


def test_365_gtc4_is_the_classic():
    for t in ("Ferrari 365 GTC4", "1972 Ferrari 365 GTC/4", "1971 Ferrari 365 GTC 4"):
        assert taxonomy.resolve(t).model.id == "365-gt-2-2", t
    for t in ("2018 Ferrari GTC4Lusso", "2019 Ferrari GTC4", "Ferrari GTC4 Lusso"):
        assert taxonomy.resolve(t).model.id == "gtc4lusso", t


def test_chassis_spellings_agree():
    assert chassis_number("015343") == chassis_number("15343") == "15343"
    assert padded_chassis("ZFF00000000015343") == "15343"
    assert padded_chassis("ZFF79ALA4J0233391") is None
    assert taxonomy.fixed_year("chassis:365-gt-2-2:15343") == 1971


def _raw(source, sid, vin, title):
    return RawRecord(source=source, kind=LISTING, source_id=sid, url="https://x/" + sid, title=title, vin=vin,
                     price=271915.0, currency="USD", mileage=41636, mileage_unit="mi", dealer_name="Ferrari of San Antonio")


def test_the_same_car_on_two_sites_is_one_car():
    keys = set()
    for raw in (_raw("ferrari_approved", "015343", "015343", "Ferrari 365 GTC4"),
                _raw("dupont_registry", "406472", "ZFF00000000015343", "1972 Ferrari 365 GTC4")):
        n = normalize(raw, {"USD": 1.0})
        assert (n.model_id, n.year) == ("365-gt-2-2", 1971)
        keys.add(vehicle_key(n))
    assert keys == {"chassis:365-gt-2-2:15343"}


def test_stored_records_are_corrected(tmp_path):
    con = db.connect(tmp_path / "t.db")
    con.execute("INSERT INTO sightings (uid, source, vehicle_key, model_id, year, title) VALUES (?,?,?,?,?,?)",
                ("ferrari_approved:015343", "ferrari_approved", "chassis:gtc4lusso:015343", "gtc4lusso", None, "Ferrari 365 GTC4"))
    con.execute("INSERT INTO sightings (uid, source, vehicle_key, model_id, year, title) VALUES (?,?,?,?,?,?)",
                ("x:1", "x", "vin:ZFF79ALA4J0233391", "gtc4lusso", 2018, "2018 Ferrari GTC4Lusso"))
    con.execute("INSERT INTO daily_asks VALUES ('2026-11-05', 'chassis:gtc4lusso:015343', 'gtc4lusso', 271915, 1, 0)")
    con.execute("INSERT INTO daily_asks VALUES ('2026-11-05', 'vin:ZFF00000000015343', 'classic-other', 271915, 1, 0)")
    clean.purge(con)
    rows = dict(con.execute("SELECT uid, model_id || ' ' || vehicle_key || ' ' || year FROM sightings").fetchall())
    assert rows["ferrari_approved:015343"] == "365-gt-2-2 chassis:365-gt-2-2:15343 1971"
    assert rows["x:1"].startswith("gtc4lusso ")
    assert [tuple(r) for r in con.execute("SELECT vehicle_key, model_id FROM daily_asks")] == [("chassis:365-gt-2-2:15343", "365-gt-2-2")]
