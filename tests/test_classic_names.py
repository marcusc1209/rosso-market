"""A 1971 365 GTC/4 listed as "365 GTC4" is a classic, not a GTC4Lusso, and is one car across sites."""
import sqlite3

from pipeline import clean, db, taxonomy
from pipeline.dedupe import vehicle_key
from pipeline.normalize import chassis_number, normalize, padded_chassis
from pipeline.records import LISTING, RawRecord


def test_365_gtc4_is_the_classic():
    for t in ("Ferrari 365 GTC4", "1972 Ferrari 365 GTC/4", "1971 Ferrari 365 GTC 4"):
        assert taxonomy.resolve(t).model.id == "365-gtc4", t
    for t in ("2018 Ferrari GTC4Lusso", "2019 Ferrari GTC4", "Ferrari GTC4 Lusso"):
        assert taxonomy.resolve(t).model.id == "gtc4lusso", t


def test_chassis_spellings_agree():
    assert chassis_number("015343") == chassis_number("15343") == "15343"
    assert padded_chassis("ZFF00000000015343") == "15343"
    assert padded_chassis("ZFF79ALA4J0233391") is None
    assert taxonomy.fixed_year("chassis:365-gtc4:15343") == 1971


def _raw(source, sid, vin, title):
    return RawRecord(source=source, kind=LISTING, source_id=sid, url="https://x/" + sid, title=title, vin=vin,
                     price=271915.0, currency="USD", mileage=41636, mileage_unit="mi", dealer_name="Ferrari of San Antonio")


def test_the_same_car_on_two_sites_is_one_car():
    keys = set()
    for raw in (_raw("ferrari_approved", "015343", "015343", "Ferrari 365 GTC4"),
                _raw("dupont_registry", "406472", "ZFF00000000015343", "1972 Ferrari 365 GTC4")):
        n = normalize(raw, {"USD": 1.0})
        assert (n.model_id, n.year) == ("365-gtc4", 1971)
        keys.add(vehicle_key(n))
    assert keys == {"chassis:365-gtc4:15343"}


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
    assert rows["ferrari_approved:015343"] == "365-gtc4 chassis:365-gtc4:15343 1971"
    assert rows["x:1"].startswith("gtc4lusso ")
    assert [tuple(r) for r in con.execute("SELECT vehicle_key, model_id FROM daily_asks")] == [("chassis:365-gtc4:15343", "365-gtc4")]


def test_classic_versions_follow_classic_com():
    """Classics are grouped as Classic.com groups them: a line, then its versions, told apart by name and year."""
    from pipeline.normalize import extract_year
    cases = {
        "1984 Ferrari 308 GTS": "308-gts-qv", "1979 Ferrari 308 GTS": "308-gts", "1981 Ferrari 308 GTSi": "308-gtsi",
        "1976 Ferrari 308 GTB Vetroresina": "308-gtb-vetroresina", "1984 Ferrari 308": "308-other",
        "1976 Ferrari 512 BB": "512-bb", "1974 Ferrari 365 GT4 BB": "365-gt4-bb", "1983 Ferrari 512 BBi": "512-bbi",
        "1990 Ferrari 348tb": "348-tb", "1994 Ferrari 348 Spider": "348-spider",
        "1986 Ferrari Mondial Cabriolet": "mondial-32-cabriolet", "Ferrari Mondial t Cabriolet": "mondial-t-cabriolet",
        "1972 Ferrari Dino 246GTS": "dino-246-gts", "1968 Ferrari Dino 206 GT": "dino-206-gt",
        "1960 Ferrari 250 GT Berlinetta": "250-swb", "1957 Ferrari 250 GT Berlinetta": "250-tdf",
        "1958 Ferrari 250 GT California Spider": "250-california-lwb", "1962 Ferrari 250 GT California Spider": "250-california-swb",
        "1957 Ferrari 250 GT Tour de France": "250-tdf", "2016 Ferrari F12tdf": "f12tdf",
        "1962 Ferrari 400 Superamerica": "400-superamerica", "2005 Ferrari Superamerica": "575m",
        "1966 Ferrari 365 California": "365-california", "2010 Ferrari California": "california",
        "1999 Ferrari F355 F1 Spider": "f355-spider", "1996 Ferrari F355 GTS": "f355-gts",
        "2000 Ferrari 456 GTA": "456m-gta", "1996 Ferrari 456 GTA": "456-gta",
        "1967 Ferrari 275 GTB/4": "275-gtb4", "1971 Ferrari 365 GTS/4 Daytona": "daytona-gts4",
        "1954 Ferrari 375 MM": "375-mm", "1991 Ferrari F40 LM": "f40-lm",
    }
    for title, want in cases.items():
        assert taxonomy.resolve(title, extract_year(title)).model.id == want, title
    assert taxonomy.LINE_ID["308"] == "308" and "308-gtsi" in taxonomy.LINES["308"]
    assert not set(taxonomy.LINE_ID.values()) & set(taxonomy.BY_ID)          # a line's page never shadows a model's
