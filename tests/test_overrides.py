"""Listing dates and price histories entered by hand (pipeline/overrides.py)."""
import json

from pipeline import db, overrides
from pipeline.records import RawRecord, LISTING

VIN = "ZFF90HLA3L0251840"


def test_entries_date_listings_and_carry_the_price_history(tmp_path):
    (tmp_path / "sources.yaml").write_text("sources: {}\n")
    (tmp_path / "listing_history.yaml").write_text(f"""
{VIN}:
  listed_on: 2026-08-29
  source: cars.com
  prices:
    - [2026-09-20, 1149000]
    - [2026-08-29, 1199000]
""")
    entries = overrides.load(tmp_path / "sources.yaml")
    assert entries[VIN]["prices"] == [["2026-08-29", 1199000.0], ["2026-09-20", 1149000.0]]

    con = db.connect(tmp_path / "t.db")
    for uid, extra in [("dupont_registry:1", {"listed_on": "2026-10-16"}), ("ferrari_approved:2", {})]:
        con.execute("INSERT INTO sightings(uid, source, vehicle_key, raw) VALUES (?,?,?,?)",
                    (uid, uid.split(":")[0], "vin:" + VIN, json.dumps({"extra": extra})))
    assert overrides.apply_db(con, entries) == 2
    for (raw,) in con.execute("SELECT raw FROM sightings"):
        e = json.loads(raw)["extra"]
        assert e["listed_on"] == "2026-08-29" and e["listed_how"] == "cars.com" and len(e["price_history"]) == 2
    assert overrides.apply_db(con, entries) == 0                     # nothing left to change

    # a later date from the file never replaces an earlier one a site published
    rec = RawRecord(source="dupont_registry", kind=LISTING, source_id="1", url="u", title="t", vin=VIN,
                    extra={"listed_on": "2026-08-01"})
    overrides.apply(rec, entries)
    assert rec.extra["listed_on"] == "2026-08-01"
