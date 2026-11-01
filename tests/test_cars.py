"""Car pages: which records are one car, its history, and its photos."""
import json
from datetime import date

from pipeline import cars, db
from pipeline.sources.broadarrow import gallery
from pipeline.sources.pcarmarket import parse_detail


def _db(tmp_path):
    con = db.connect(tmp_path / "t.db")
    S = """INSERT INTO sightings(uid, source, vehicle_key, model_id, year, title, url, marketplace, dealer_name, seller_type,
           certified, location, mileage_mi, vin, image_url, first_seen, last_seen, removed_on, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    L = """INSERT INTO lots(uid, source, vehicle_key, model_id, year, title, url, auction_house, status, price_usd, ends_at,
           mileage_mi, vin, image_url, first_seen, last_seen, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    photos = json.dumps({"extra": {"photos": ["https://p/1.jpg", "https://p/2.jpg"]}, "exterior_color": "Rosso Corsa"})
    # one car with a VIN: on two sites by the same dealer, and sold at BaT last year
    con.execute(S, ("a:1", "autotrader", "vin:ZFF1", "488-spider", 2017, "2017 Ferrari 488 Spider", "https://autotrader/1",
                    "autotrader.com", "Prestige", "dealer", 0, "Boston", 20000, "ZFF1", None, "2026-10-01", "2026-11-01", None, "{}"))
    con.execute(S, ("fa:1", "ferrari_approved", "vin:ZFF1", "488-spider", 2017, "2017 Ferrari 488 Spider", "https://fa/1",
                    "Ferrari Approved", "Prestige", "dealer", 1, "Boston", 20000, "ZFF1", "https://p/1.jpg", "2026-10-03", "2026-11-01", None, photos))
    for d, p in (("2026-10-01", 260000), ("2026-10-15", 255000), ("2026-11-01", 255000)):
        con.execute("INSERT INTO sighting_prices VALUES (?,?,?,?,?)", ("fa:1", d, p, p, "USD"))
    con.execute(L, ("bat:9", "bringatrailer", "vin:ZFF1", "488-spider", 2017, "2017 Ferrari 488 Spider", "https://bat/9",
                    "Bring a Trailer", "sold", 240000, "2025-09-01T18:00:00+00:00", 18000, "ZFF1", "https://bat/9.jpg", "2025-08-25", "2025-09-01", "{}"))
    # two RM lots with no VIN that share a loose fingerprint: two different cars
    for uid, d in (("rm:1", "2024-05-01"), ("rm:2", "2025-05-01")):
        con.execute(L, (uid, "rmsothebys", "fp:same", "488-spider", 2017, "2017 Ferrari 488 Spider", f"https://rm/{uid}",
                        "RM Sotheby's", "sold", 230000, d + "T18:00:00+00:00", None, None, None, d, d, "{}"))
    con.commit()
    return con


def test_one_car_across_dealers_and_auctions(tmp_path):
    con = _db(tmp_path)
    pages, idx = cars.build(con, date(2026, 11, 1), {"488-spider": {"median": 250000, "listings": 9}})
    vin_car = cars.car_id("vin:ZFF1")
    assert idx.lot("bat:9") == vin_car == idx.listing("vin:ZFF1")
    c = pages[vin_car]
    assert c["status"] == "for_sale" and c["price"] == 255000 and c["vin"] == "ZFF1"
    kinds = [e["kind"] for e in c["history"]]
    assert kinds == ["listed", "sold"]                       # two sites by one dealer are one event
    listed = c["history"][0]
    assert len(listed["seen_on"]) == 2 and listed["where"] == "Ferrari Approved"   # not the aggregator
    assert listed["changes"] == [["2026-10-01", 260000], ["2026-10-15", 255000]]
    assert c["photos"] == ["https://p/1.jpg", "https://p/2.jpg"]
    assert ["Exterior", "Rosso Corsa"] in c["specs"]
    # loose fingerprints never join auction lots
    assert idx.lot("rm:1") != idx.lot("rm:2")
    assert {x["id"] for x in c["comps"]} >= {idx.lot("rm:1"), idx.lot("rm:2")}
    assert all(x["id"] != vin_car for x in c["comps"])


def test_galleries_from_lot_pages():
    html = ('<img src="https://cdn.dealeraccelerate.com/bagauction/37/5849/1/790x1024/2020-ferrari-488-pista-spider">'
            '<img src="https://cdn.dealeraccelerate.com/bagauction/37/5849/2/1920x1440/2020-ferrari-488-pista-spider">'
            '<img src="https://cdn.dealeraccelerate.com/bagauction/37/5849/3/400x300/1990-ferrari-testarossa">')
    assert gallery(html, "2020-ferrari-488-pista-spider") == [
        "https://cdn.dealeraccelerate.com/bagauction/37/5849/1/1280x960/2020-ferrari-488-pista-spider",
        "https://cdn.dealeraccelerate.com/bagauction/37/5849/2/1280x960/2020-ferrari-488-pista-spider"]
    page = ('<main><img src="https://assets.basta.app/accounts/a-1/images/b-2?width=500">'
            '<a href="/auction/other-car"><img src="https://assets.basta.app/accounts/a-1/images/c-3?width=500"></a>'
            '<p>Ended Oct 21, 2026, 7:12 PM</p></main>')
    d = parse_detail(page)
    assert d["photos"] == ["https://assets.basta.app/accounts/a-1/images/b-2?width=1280&quality=80&format=webp"]
    assert d["ends_at"].startswith("2026-10-21")
