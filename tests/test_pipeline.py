import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from pipeline import taxonomy, db, analytics
from pipeline.normalize import parse_price, parse_mileage, extract_vin, vin_check_digit_ok, normalize, Reject, infer_country
from pipeline.extract import json_ld, js_vars, map_generic, walk
from pipeline.records import RawRecord, LISTING, AUCTION
from pipeline.dedupe import vehicle_key
from pipeline.sources.bringatrailer import parse_item

FX = {"USD": 1.0, "CAD": 0.73, "EUR": 1.08, "GBP": 1.27}


@pytest.mark.parametrize("title,expected", [
    ("2019 Ferrari 488 Pista Spider", "488-pista-spider"),
    ("2019 Ferrari 488 Pista", "488-pista"),
    ("2017 Ferrari 488 Spider", "488-spider"),
    ("2016 Ferrari 488 GTB", "488-gtb"),
    ("2018 Ferrari 488", "488-gtb"),
    ("2014 Ferrari 458 Spider", "458-spider"),
    ("2015 Ferrari 458 Speciale A", "458-speciale-a"),
    ("2021 Ferrari F8 Spider", "f8-spider"),
    ("2022 Ferrari SF90 Stradale Assetto Fiorano", "sf90-stradale"),
    ("2024 Ferrari SF90 XX Spider", "sf90-xx-spider"),
    ("2022 Ferrari 812 Competizione A", "812-competizione-a"),
    ("2021 Ferrari 812 GTS", "812-gts"),
    ("2016 Ferrari F12tdf", "f12tdf"),
    ("2014 Ferrari F12berlinetta", "f12berlinetta"),
    ("2015 Ferrari California T", "california-t"),
    ("2012 Ferrari California", "california"),
    ("2021 Ferrari Portofino M", "portofino-m"),
    ("2023 Ferrari Roma Spider", "roma-spider"),
    ("2017 Ferrari GTC4Lusso T", "gtc4lusso-t"),
    ("2004 Ferrari 575M Maranello", "575m"),
    ("2000 Ferrari 550 Maranello", "550-maranello"),
    ("1972 Dino 246 GTS", "dino-246"),
    ("1971 Ferrari 365 GTB/4 Daytona", "daytona"),
    ("2023 Ferrari Daytona SP3", "daytona-sp3"),
    ("1987 Ferrari Testarossa", "testarossa"),
    ("1992 Ferrari 512 TR", "512-tr"),
    ("1985 Ferrari 308 GTS QV", "308"),
    ("1978 Ferrari 308 GT4", "dino-308-gt4"),
    ("2015 Ferrari LaFerrari", "laferrari"),
    ("2017 Ferrari LaFerrari Aperta", "laferrari-aperta"),
    ("2009 Ferrari 430 Scuderia", "430-scuderia"),
    ("2009 Ferrari 430 Spider", "f430-spider"),
    ("2023 Ferrari Purosangue", "purosangue"),
    ("2018 Ferrari 488 GTB Rosso Scuderia", "488-gtb"),
    ("2016 Ferrari California T in Rosso Scuderia", "california-t"),
])
def test_model_resolution(title, expected):
    assert taxonomy.resolve(title).model.id == expected


def test_year_outside_range_lowers_confidence():
    assert taxonomy.resolve("1999 Ferrari 488 GTB", 1999).confidence < 0.5


def test_non_cars_rejected():
    for t in ["Ferrari 488 Pista wheels set of four", "1:18 scale model Ferrari F40", "Ferrari F40 replica on Pontiac Fiero"]:
        assert not taxonomy.looks_like_car(t)


@pytest.mark.parametrize("text,amount,cur", [
    ("US$334,900", 334900, "USD"), ("$1.2M", 1_200_000, "USD"), ("CA$ 299,000", 299000, "CAD"),
    ("£913,500", 913500, "GBP"), ("€ 1.234.567", 1234567, "EUR"), ("Sold for USD $325,000", 325000, "USD"),
    ("Call for price", None, "USD"), (450000, 450000, "USD"),
])
def test_parse_price(text, amount, cur):
    a, c = parse_price(text)
    assert a == amount and c == cur


def test_parse_mileage():
    assert parse_mileage("13,758 mi") == 13758
    assert parse_mileage("5k Miles") == 5000
    assert parse_mileage("10,000 km") == 6214
    assert parse_mileage("TMU") is None


def test_vin():
    vin, ok = extract_vin("Chassis ZFF79ALA4J0233545 offered by")
    assert vin == "ZFF79ALA4J0233545"
    assert vin_check_digit_ok("1M8GDM9AXKP042788")  # textbook example


def test_country():
    assert infer_country("Toronto, ON") == "CA"
    assert infer_country("Scottsdale, Arizona") == "US"


def test_normalize_converts_currency_and_rejects():
    r = RawRecord("t", LISTING, "1", "u", "2020 Ferrari F8 Tributo", price=400000, currency="CAD", location="Toronto, ON")
    n = normalize(r, FX)
    assert n.price_usd == 292000 and n.country == "CA" and n.model_id == "f8-tributo"
    with pytest.raises(Reject):
        normalize(RawRecord("t", LISTING, "2", "u", "2020 Lamborghini Huracan", price=200000), FX)
    with pytest.raises(Reject):
        normalize(RawRecord("t", LISTING, "3", "u", "2020 Ferrari F8 Tributo"), FX)   # listing with no price


def test_same_vin_same_vehicle():
    a = normalize(RawRecord("x", LISTING, "1", "u", "2019 Ferrari 488 Pista", price=420000, vin="ZFF90HLA4K0240001"), FX)
    b = normalize(RawRecord("y", LISTING, "9", "v", "Ferrari 488 Pista 2019", price=425000, vin="zff90hla4k0240001"), FX)
    assert vehicle_key(a) == vehicle_key(b)


def test_json_ld_and_generic_mapper():
    html = """<script type="application/ld+json">{"@context":"https://schema.org","@type":"Car","name":"2021 Ferrari Roma",
    "vehicleIdentificationNumber":"ZFF98RNA1M0262111","mileageFromOdometer":{"value":3025,"unitCode":"SMI"},
    "offers":{"@type":"Offer","price":"231990","priceCurrency":"USD","url":"https://dealer.example/roma"}}</script>"""
    items = json_ld(html)
    r = map_generic(items[0], "dealer_sites", LISTING)
    assert r.price == 231990 and r.mileage == 3025 and r.vin.startswith("ZFF") and r.url.endswith("/roma")


def test_js_vars_and_walk():
    html = 'var auctionsCurrentInitialData = {"items":[{"title":"2014 Ferrari 458 Spider","url":"https://bringatrailer.com/listing/x/","current_bid":200000}]};'
    blobs = js_vars(html)
    assert len(list(walk(blobs["auctionsCurrentInitialData"]))) == 1


def test_bat_item_sold_and_live():
    sold = parse_item({"id": 1, "title": "2006 Lamborghini", "url": "https://bringatrailer.com/listing/a/",
                       "sold_text": "Sold for USD $325,000 <span> on 10/29/26 </span>"})
    assert sold.status == "sold" and sold.price == 325000 and sold.ends_at.startswith("2026-10-29")
    rnm = parse_item({"id": 2, "title": "t", "url": "https://bringatrailer.com/listing/b/", "sold_text": "Bid to USD $715,000 on 10/20/26"})
    assert rnm.status == "unsold" and rnm.price == 715000
    live = parse_item({"id": 3, "title": "t", "url": "https://bringatrailer.com/listing/c/", "current_bid": 192022, "active": True,
                       "timestamp_end": 1793472120})
    assert live.status == "live" and live.price == 192022


def _seed(con, day, prices_now, prices_then, model="488-gtb"):
    for d, prices in ((day - timedelta(days=7), prices_then), (day, prices_now)):
        for k, p in prices.items():
            con.execute("INSERT INTO daily_asks VALUES (?,?,?,?,1,0)", (d.isoformat(), k, model, p))


def test_mover_requires_same_car_repricing(tmp_path):
    con = db.connect(tmp_path / "t.db")
    day = date(2026, 10, 30)
    then = {f"c{i}": 250000 for i in range(6)}
    # median falls because two cheap cars arrived, nobody cut: not a mover
    now = dict(then); now.update({f"n{i}": 200000 for i in range(6)})
    _seed(con, day, now, then)
    s = analytics.model_stats(con, day)
    assert s["488-gtb"]["change_7d"] < -1 and analytics.movers(s) == []
    # sellers actually cut: a mover
    con.execute("DELETE FROM daily_asks")
    _seed(con, day, {k: 240000 for k in then}, then)
    mv = analytics.movers(analytics.model_stats(con, day))
    assert mv and mv[0]["id"] == "488-gtb" and mv[0]["direction"] == "down"


def test_missing_source_does_not_remove_listings(tmp_path):
    con = db.connect(tmp_path / "t.db")
    day = date(2026, 10, 1)
    n = normalize(RawRecord("s", LISTING, "1", "u", "2016 Ferrari 488 GTB", price=230000, vin="ZFF79ALA4G0000001"), FX)
    n.vehicle_key = vehicle_key(n)
    db.upsert(con, n, day)
    db.mark_missing(con, "s", day + timedelta(days=1))
    assert db.build_daily_asks(con, day + timedelta(days=1)) == 1        # one miss: still counted
    db.mark_missing(con, "s", day + timedelta(days=2))
    assert db.build_daily_asks(con, day + timedelta(days=2)) == 0        # two successful runs without it: gone


def test_demo_end_to_end(tmp_path, monkeypatch):
    from pipeline import run
    cfg = {"settings": {"db": str(tmp_path / "x.db"), "raw_dir": str(tmp_path / "raw"), "site_dir": str(tmp_path / "site"),
                        "content_dir": "content", "respect_robots_txt": False, "fx": FX},
           "sources": {"demo": {"adapter": "demo", "enabled": True}}}
    p = tmp_path / "c.yaml"
    import yaml
    p.write_text(yaml.safe_dump(cfg))
    assert run.main(["--config", str(p), "--date", "2026-10-30", "--backfill", "14"]) == 0
    data = json.loads((tmp_path / "site" / "data" / "site.json").read_text())
    assert data["sample"] is True and data["brief"]["tracked_listings"] > 500 and data["auctions"]["live"]
    text = json.dumps([data["brief"], data["insights"]])
    assert "—" not in text and ";" not in text


def test_merge_keeps_both_runners_data(tmp_path):
    from pipeline.merge import merge
    day = date(2026, 11, 1)
    base = tmp_path / "base.db"
    db.connect(base).close()
    import shutil
    local, remote = tmp_path / "local.db", tmp_path / "remote.db"
    shutil.copy(base, local); shutil.copy(base, remote)
    # the Mac (remote) saved a Ferrari Approved listing
    rc = db.connect(remote)
    n = normalize(RawRecord("ferrari_approved", LISTING, "V1", "u", "2021 Ferrari F8 Spider", price=380000, vin="ZFF93LMA0M0260001"), FX)
    n.vehicle_key = vehicle_key(n); db.upsert(rc, n, day); rc.commit(); rc.close()
    # GitHub (local) saved a Bring a Trailer result
    lc = db.connect(local)
    lot = normalize(RawRecord("bringatrailer", AUCTION, "L1", "u", "2017 Ferrari 488 Spider", price=305000, status="sold",
                              ends_at="2026-10-30T20:00:00+00:00"), FX)
    lot.vehicle_key = vehicle_key(lot); db.upsert(lc, lot, day); lc.commit(); lc.close()
    merge(local, remote, ["bringatrailer", "carsandbids"], day)
    c = sqlite3.connect(local)
    assert c.execute("select count(*) from sightings").fetchone()[0] == 1
    assert c.execute("select count(*) from lots").fetchone()[0] == 1
    assert c.execute("select count(*) from daily_asks where day='2026-11-01'").fetchone()[0] == 1


def test_purge_drops_rows_that_newer_filters_reject(tmp_path):
    from pipeline import clean, db
    con = db.connect(tmp_path / "t.db")
    for uid, title, price in [("bat:1", "1991 Ferrari F40", 3_000_000), ("bat:2", "Ferrari F40 Headlights", 14_739),
                              ("bat:3", "Motoi Ferrari F40-Style Children’s Ride", 13_250)]:
        con.execute("INSERT INTO lots (uid, source, model_id, title, status, price_usd, ends_at) VALUES (?,?,?,?,?,?,?)",
                    (uid, "bat", "f40", title, "sold", price, "2026-05-01"))
        con.execute("INSERT INTO lot_observations VALUES (?,?,?,?)", (uid, "2026-05-01", "sold", price))
    assert clean.purge(con)["lots"] == 2
    assert [r[0] for r in con.execute("SELECT uid FROM lots")] == ["bat:1"]
    assert con.execute("SELECT COUNT(*) FROM lot_observations").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM rejects WHERE reason LIKE 'purged%'").fetchone()[0] == 2


def test_implausible_sold_prices_are_flagged(tmp_path):
    from pipeline import clean, db
    con = db.connect(tmp_path / "t.db")
    rows = [(2_900_000, "2026-01-05"), (3_100_000, "2025-11-02"), (3_000_000, "2026-03-09"), (3_300_000, "2026-05-16"),
            (2_800_000, "2025-08-20"), (21_000, "2026-04-01"),       # a mislabeled part
            (290_000, "1999-05-01"),                                  # a real sale from another era
            (19_000, None)]                                           # undated: never charted, never flagged
    for i, (p, d) in enumerate(rows):
        con.execute("INSERT INTO lots (uid, source, model_id, title, status, price_usd, ends_at) VALUES (?,?,?,?,?,?,?)",
                    (f"x:{i}", "x", "f40", "1990 Ferrari F40", "sold", p, d))
    assert clean.implausible_lots(con) == {"x:5"}


def test_estimate_ranges_are_never_prices():
    from pipeline.normalize import is_price_range
    from pipeline.sources.rmsothebys import map_item
    assert is_price_range("\u20ac275,000 - \u20ac300,000 EUR") and not is_price_range("$275,000")
    withheld = {"publicName": "2017 Ferrari California T", "value": "\u20ac275,000 - \u20ac300,000 EUR", "valueType": "", "sold": True}
    assert map_item(withheld, "rmsothebys", "Results") is None
