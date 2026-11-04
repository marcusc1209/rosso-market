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
    # an auction in Canada stays (results are international), converted to US dollars
    r = RawRecord("t", AUCTION, "1", "u", "2020 Ferrari F8 Tributo", price=400000, currency="CAD", location="Toronto, ON", status="sold")
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


FX_AUCTION = {"USD": 1.0, "GBP": 1.27, "EUR": 1.08}


def _lot(**kw):
    base = dict(source="x", kind=AUCTION, source_id="1", url="https://example.com/lot/1", title="2022 Ferrari Daytona SP3",
                auction_house="Example House", status="sold", price=3_000_000.0)
    base.update(kw)
    return RawRecord(**base)


@pytest.mark.parametrize("kw", [
    dict(auction_house="RM Sotheby's Private Sales"),
    dict(url="https://sealed.rmsothebys.com/auctions/s1226/lots/r0006-2024-ferrari-sf90-xx-spider/"),
    dict(extra={"auction": "Sealed December"}),
    dict(auction_house="Broad Arrow Private Treaty"),
    dict(extra={"private_sale": True}),
])
def test_private_and_sealed_sales_are_never_counted(kw):
    with pytest.raises(Reject, match="private or sealed"):
        normalize(_lot(**kw), FX_AUCTION)


def test_sold_without_price_is_dropped_and_upcoming_keeps_only_its_estimate():
    with pytest.raises(Reject, match="sold without"):
        normalize(_lot(price=None), FX_AUCTION)
    n = normalize(_lot(status="upcoming", price=None, price_text="$3,000,000 - $3,500,000",
                       estimate_low=3_000_000, estimate_high=3_500_000), FX_AUCTION)
    assert n.price_usd is None and n.estimate_low_usd == 3_000_000 and n.estimate_high_usd == 3_500_000
    n = normalize(_lot(status="upcoming", price=3_000_000.0), FX_AUCTION)
    assert n.price_usd is None


def test_a_result_replaces_an_earlier_price(tmp_path):
    con = db.connect(tmp_path / "t.db")
    db.upsert(con, normalize(_lot(status="live", price=2_000_000.0), FX_AUCTION), date(2026, 10, 1))
    db.upsert(con, normalize(_lot(status="unsold", price=None), FX_AUCTION), date(2026, 10, 2))
    assert tuple(con.execute("SELECT status, price_usd FROM lots").fetchone()) == ("unsold", None)


def test_clean_applies_the_auction_policy_to_stored_rows(tmp_path):
    from pipeline import clean
    con = db.connect(tmp_path / "t.db")
    rows = [("a:1", "RM Sotheby's Private Sales", "sold", 1_000_000, "https://rmsothebys.com/ps00/inventory/r1/"),
            ("a:2", "RM Sotheby's", "sold", None, "https://rmsothebys.com/auctions/mo25/lots/r2/"),
            ("a:3", "RM Sotheby's", "upcoming", 1_800_000, "https://rmsothebys.com/auctions/lf26/lots/r3/"),
            ("a:4", "RM Sotheby's", "sold", 2_000_000, "https://rmsothebys.com/auctions/mo25/lots/r4/")]
    for uid, house, status, price, url in rows:
        con.execute("INSERT INTO lots (uid, source, model_id, title, auction_house, status, price_usd, url) VALUES (?,?,?,?,?,?,?,?)",
                    (uid, "a", "f40", "1990 Ferrari F40", house, status, price, url))
    out = clean.purge(con)
    assert out["private_or_sealed"] == 1 and out["sold_without_price"] == 1 and out["upcoming_price_cleared"] == 1
    assert [tuple(r) for r in con.execute("SELECT uid, price_usd FROM lots ORDER BY uid")] == [("a:3", None), ("a:4", 2_000_000)]


def test_waf_challenge_and_empty_runs_are_not_ok(tmp_path):
    import httpx
    from pipeline.fetch import Fetcher, Blocked
    from pipeline import health
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(202, text="<html><script src='challenge.js'></script></html>")

    f = Fetcher({"min_interval_seconds": 0, "max_retries": 0}, tmp_path, "t")
    f.client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(Blocked):
        f.get("https://example.com/sitemap.xml.gz")

    con = db.connect(tmp_path / "t.db")
    rid = db.start_run(con, date(2026, 11, 1), "x")
    db.finish_run(con, rid, "ok", 0, 0, 0, 3)
    assert health.check(con, "x", rid)["state"] == "down"


def test_empty_proxy_secret_means_no_proxy(tmp_path, monkeypatch):
    from pipeline.fetch import Fetcher
    monkeypatch.setenv("RESIDENTIAL_PROXY_URL", "")
    f = Fetcher({"proxy_env": "RESIDENTIAL_PROXY_URL"}, tmp_path, "t")
    assert f.proxy is None


def test_cars_for_sale_are_the_us_market_only():
    us = normalize(RawRecord("dupont", LISTING, "1", "u", "2021 Ferrari F8 Tributo", price=290000, location="Greenwich, CT", country="US"), FX)
    assert us.model_id == "f8-tributo"
    # a US site's stamp does not make a Montreal or Bradford dealer's car a US car
    for loc, code, where in (("Montreal, QC", "US", "Canada"), ("Toronto, ON", None, "Canada"), ("Bradford, England", "US", "United Kingdom"),
                             ("Katwijk, Netherlands", None, "Netherlands")):
        with pytest.raises(Reject, match=f"outside the US \\({where}\\)"):
            normalize(RawRecord("dupont", LISTING, "2", "u", "2021 Ferrari F8 Tributo", price=290000, location=loc, country=code), FX)
    # no location and no country: every source is a US site, so the car stays
    assert normalize(RawRecord("dupont", LISTING, "3", "u", "2021 Ferrari F8 Tributo", price=290000), FX)
    # hypercars and limited series cars are tracked for sale anywhere
    for title, loc in (("2014 Ferrari LaFerrari", "Bradford, England"), ("2022 Ferrari 812 Competizione", "Montreal, QC"),
                       ("2011 Ferrari 599 GTO", "Calgary")):
        assert normalize(RawRecord("dupont", LISTING, "4", "u", title, price=3000000, location=loc, country="US"), FX).country


def test_stored_cars_outside_the_us_are_purged_and_snapshots_rebuilt(tmp_path):
    from datetime import date
    from pipeline import clean, db
    con = db.connect(tmp_path / "t.db")
    for uid, key, loc, code, price, mid in (("a:1", "vin:US1", "Charlotte", "US", 300000, "roma"), ("a:2", "vin:CA1", "Calgary", "CA", 100000, "roma"),
                                            ("d:3", "vin:CA2", "Montreal, QC", "US", 120000, "roma"),
                                            ("d:4", "vin:UK1", "Bradford, England", "US", 3500000, "laferrari"),    # a hypercar stays
                                            ("autotrader_ca:5", "vin:CA3", "Toronto, ON", "CA", 3000000, "laferrari")):   # but not from a removed source
        con.execute("""INSERT INTO sightings (uid, source, vehicle_key, model_id, title, country, location, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, 'x', ?, ?, '2026-11-01', '2026-11-02')""", (uid, uid.split(":")[0], key, mid, code, loc))
        for d in ("2026-11-01", "2026-11-02"):
            con.execute("INSERT INTO sighting_prices (uid, day, price_usd) VALUES (?, ?, ?)", (uid, d, price))
    con.execute("""INSERT INTO lots (uid, source, model_id, title, status, price_usd, ends_at, country)
                   VALUES ('b:1', 'rm', 'roma', 'Roma', 'sold', 200000, '2026-10-01T12:00:00Z', 'CA')""")
    for d in ("2026-11-01", "2026-11-02"):
        db.build_daily_asks(con, date.fromisoformat(d))
    assert con.execute("SELECT COUNT(*) FROM daily_asks").fetchone()[0] == 10
    assert clean.purge(con)["outside_us"] == 3
    assert sorted(r[0] for r in con.execute("SELECT uid FROM sightings")) == ["a:1", "d:4"]
    assert con.execute("SELECT COUNT(*) FROM sighting_prices").fetchone()[0] == 4
    assert {r[0] for r in con.execute("SELECT vehicle_key FROM daily_asks")} == {"vin:US1", "vin:UK1"}
    assert con.execute("SELECT COUNT(*) FROM lots").fetchone()[0] == 1          # the Canadian auction result stays
    assert con.execute("SELECT COUNT(*) FROM rejects WHERE reason LIKE 'purged: listed outside the US%'").fetchone()[0] == 3


def test_data_sources_list_every_enabled_source_whichever_runner_ran(tmp_path):
    from pipeline import run
    con = db.connect(tmp_path / "t.db")
    for src, d, st in (("broadarrow", "2026-11-03", "degraded"), ("ferrari_approved", "2026-11-03", "ok"),
                       ("bonhams", "2026-11-03", "ok"), ("autotrader_ca", "2026-11-03", "ok")):
        con.execute("INSERT INTO runs (day, source, status, accepted) VALUES (?, ?, ?, 5)", (d, src, st))
    cfg = {"sources": {"broadarrow": {"enabled": True}, "ferrari_approved": {"enabled": True}, "bonhams": {"enabled": True},
                       "autostoday": {"enabled": False}, "pcarmarket": {"enabled": True}}}
    # a cloud run read only Bonhams today: the Mac's sources still show, with their own day
    h = run.source_health(con, cfg, [dict(source="bonhams", state="ok", detail="", accepted=1402)], date(2026, 11, 4))
    assert [(r["source"], r["state"], r["day"]) for r in h] == [
        ("broadarrow", "degraded", "2026-11-03"), ("ferrari_approved", "ok", "2026-11-03"), ("bonhams", "ok", "2026-11-04")]
