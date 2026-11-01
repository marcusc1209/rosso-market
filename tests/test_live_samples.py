"""Adapters run against real responses captured from each site on 2026-10-31.

The fixtures keep only the fields each adapter reads, rebuilt into the site's own
shape below. When a site changes its format, capture a fresh sample, update the
fixture, and these tests show exactly what broke.
"""
import json
from pathlib import Path

import pytest

from pipeline.normalize import normalize, Reject
from pipeline.sources.bringatrailer import parse_item
from pipeline.sources.ferrari_approved import map_ad
from pipeline.sources.mecum import map_hit
from pipeline.sources.online_auctions import map_somo, map_dupont
from pipeline.sources.rmsothebys import map_item

FIX = Path(__file__).parent / "fixtures"
FX = {"USD": 1.0, "CAD": 0.73, "EUR": 1.08, "GBP": 1.27, "CHF": 1.12, "AED": 0.2723}
NOW = 1793480000  # 2026-10-31


def load(name):
    return json.loads((FIX / name).read_text())


def outcome(raw):
    try:
        n = normalize(raw, FX)
        return n.model_id, n.price_usd, n.mileage_mi
    except Reject as e:
        return "REJECT", str(e), None


# ------------------------------------------------------------------ Bring a Trailer

def bat_completed():
    return {r[0]: parse_item({"id": r[0], "title": r[1], "current_bid": r[2], "sold_text": r[3], "timestamp_end": r[4],
                              "noreserve": r[5], "country_code": r[6], "currency": "GBP" if "GBP" in r[3] else "USD",
                              "url": f"https://bringatrailer.com/listing/x-{r[0]}/"}) for r in load("bat_completed.json")}


def test_bat_results_status_price_currency():
    recs = bat_completed()
    assert recs[124901803].status == "unsold" and recs[124901803].price == 130000
    assert recs[125376088].status == "sold" and recs[125376088].price == 305000
    assert recs[124547639].currency == "GBP"
    assert recs[123233577].ends_at.startswith("2026-10")


@pytest.mark.parametrize("bat_id,expected", [
    (125567676, ("f430", 191000.0, 14000.0)),          # "14k-Mile 2006 Ferrari F430"
    (123233577, ("812-gts", 767000.0, 578.0)),
    (124909567, ("f355-spider", 153000.0, 29826.0)),   # 48k kilometers
    (124937358, ("456", 95000.0, 12000.0)),            # 456M GTA
    (122251628, ("599-gtb", 280000.0, 11000.0)),
    (125195874, ("328", 101311.0, None)),
    (124648484, ("400-412", 40000.0, None)),
])
def test_bat_cars_resolve(bat_id, expected):
    assert outcome(bat_completed()[bat_id]) == expected


@pytest.mark.parametrize("bat_id", [124547639, 125507756, 125481809, 123989666, 125393226, 123590957, 125270547, 124544171])
def test_bat_non_cars_and_recreations_rejected(bat_id):
    assert outcome(bat_completed()[bat_id])[0] == "REJECT"


def test_bat_live():
    recs = {r[0]: parse_item({"id": r[0], "title": r[1], "current_bid": r[2], "timestamp_end": r[3], "noreserve": r[4], "year": r[5],
                              "country": r[6], "country_code": r[7], "active": r[8], "url": f"https://bringatrailer.com/listing/l-{r[0]}/"})
            for r in load("bat_live.json")}
    assert all(r.status == "live" for r in recs.values())
    assert outcome(recs[124706331]) == ("f12berlinetta", 760000.0, 1200.0)
    assert outcome(recs[123929277])[0] == "f8-spider" and outcome(recs[123929277])[2] == 3107.0
    assert outcome(recs[125172952])[0] == "365-gt-2-2"
    assert outcome(recs[124932135])[0] == "348"
    for junk in (125581346, 125881747, 125787948, 125194712):
        assert outcome(recs[junk])[0] == "REJECT", recs[junk].title


# ------------------------------------------------------------------ Ferrari Approved

def fa_records():
    out = []
    for r in load("ferrari_approved_rows.json"):
        ad = {"id": r[8], "year": r[0], "carName": r[1], "price": r[2], "priceUnit": r[3], "requestPrice": r[4],
              "ferrariApproved": r[5], "odometer": r[6], "odometerUnit": r[7], "vin": r[8], "exteriorColor": r[9],
              "dealer": {"name": r[10], "city": r[11]}, "model": {"name": r[1]}}
        out.append(map_ad(ad, "ferrari_approved", "usa"))
    return {r.vin: r for r in out}


def test_ferrari_approved_prices_and_flags():
    recs = fa_records()
    roma = recs["ZFF09RPA9T0333957"]
    assert roma.price == 311585 and roma.certified and roma.dealer_name == "Ferrari of Fort Lauderdale"
    assert outcome(roma)[0] == "roma-spider"
    assert outcome(recs["ZFF10XLA0S0319016"])[0] == "12cilindri"
    assert outcome(recs["ZFF02RPA4P0293393"])[0] == "portofino-m"
    # price on request (0, 212, 1685 placeholders) -> no price -> not counted
    for vin in ("ZFF10XLA3S0322654", "ZFF99SLA2S0318607", "ZFF91HMAXL0255976", "ZFFPR48A3S0104074"):
        assert recs[vin].price is None
        assert outcome(recs[vin]) == ("REJECT", "listing without a price", None)
    # 488 Challenge is a track car
    assert outcome(recs["ZFF87EXX000283659"]) == ("REJECT", "track only car", None)


# ------------------------------------------------------------------ RM Sotheby's

def rm_records(status):
    out = []
    for r in load("rm_results.json")[status]:
        x = {"publicName": r[0], "value": r[1], "valueType": r[2], "preSaleEstimate": r[3], "sold": r[4], "isStillForSale": r[5],
             "header": r[6], "location": r[7], "countryCode": r[8], "lot": r[9], "link": r[10], "id": r[10]}
        out.append(map_item(x, "rmsothebys", status))
    return out


def test_rm_results():
    recs = [r for r in rm_records("Results") if r]
    titles = {r.title: r for r in recs}
    # sealed bid results with no published price, and private sales marked only "Sold", are skipped
    assert "2015 Ferrari 458 Speciale A" not in titles and "2003 Ferrari Enzo" not in titles
    sp3 = titles["2022 Ferrari Daytona SP3"]
    assert sp3.status == "sold" and sp3.currency == "GBP" and sp3.price == 8161250
    assert outcome(sp3) == ("daytona-sp3", 10364787.5, None)
    tr = titles["1985 Ferrari Testarossa 'Monospecchio'"]
    assert tr.currency == "CHF" and outcome(tr)[0] == "testarossa"
    assert outcome(titles["2009 Ferrari Scuderia Spider 16M"])[0] == "16m"
    assert outcome(titles["1971 Ferrari 365 GTB/4 Daytona Berlinetta by Scaglietti"])[0] == "daytona"
    assert outcome(titles["2017 Ferrari F12berlinetta 70th Anniversary 'Tailor Made'"])[0] == "f12berlinetta"


def test_rm_on_offer_estimates():
    recs = {r.title: r for r in rm_records("On Offer") if r}
    s = recs["2015 Ferrari 458 Speciale"]
    assert s.status == "upcoming" and s.price is None and (s.estimate_low, s.estimate_high, s.currency) == (520000, 620000, "EUR")
    assert recs["2006 Ferrari 612 Scaglietti"].no_reserve is True


# ------------------------------------------------------------------ SOMO and duPont Registry Live

def test_somo_sold():
    recs = []
    for r in load("somo_sold.json"):
        a = {"auctionId": f"{r[0]}{r[1]}{r[4]}", "vehicleData": {"year": r[0], "make": "Ferrari", "model": r[1], "slug": "x"},
             "bidDetails": {"value": r[2]}, "buyerFeeGross": r[3], "soldDate": r[4] + "T21:00:00.000Z", "hasReservePrice": r[5],
             "reservePrice": {"currency": "USD"}}
        recs.append(map_somo(a, "somo", "sold"))
    got = [outcome(r)[0] for r in recs]
    assert all(r.status == "sold" for r in recs)
    assert got.count("REJECT") == 0, [(r.title, outcome(r)) for r in recs if outcome(r)[0] == "REJECT"]
    assert "430-scuderia" in got and "512-tr" in got and "gtc4lusso" in got and "308" in got
    assert outcome(recs[14]) == ("550-maranello", 477500.0, 11000.0)   # "550 Maranello — 11k Miles"


def test_dupont_sold():
    recs = []
    for r in load("dupont_sold.json"):
        c = {"auctionId": r[0], "title": r[0], "highestBid": r[1], "outcome": {"vehicleSold": r[2], "vehicleUnsold": r[3]},
             "endTime": 1793300000000, "vehicle": {"year": int(r[0][:4]), "make": "Ferrari", "model": r[5]}, "slug": "x"}
        recs.append(map_dupont(c, "dupont_live", "sold"))
    got = {r.title: outcome(r)[0] for r in recs}
    assert got["2017 Ferrari F12tdf"] == "f12tdf" and got["2017 Ferrari F12 TDF"] == "f12tdf"
    assert got["2004 Ferrari 360 Challenge Stradale"] == "360-challenge-stradale"
    assert got["2007 Ferrari 599 GTB"] == "599-gtb"


# ------------------------------------------------------------------ Mecum

def test_mecum():
    recs = {}
    for r in load("mecum_past.json"):
        h = {"objectID": r[0], "post_title": r[0], "sold": r[1], "bid_goes_on": r[2], "highest_bid_or_price": r[3],
             "auction_end_date_meta": r[4], "taxonomies": {"lot_status": [{"name": x} for x in r[5].split(";")],
                                                           "auction_tax": [{"name": "Kissimmee 2027|1|2"}]}}
        recs[r[0]] = map_hit(h, "mecum", NOW)
    f8 = recs["2021 Ferrari F8 Spider"]
    assert f8.status == "sold" and outcome(f8) == ("f8-spider", 478500.0, None)
    # "bid goes on": unsold, and the number shown is never used as a price
    s458 = recs["2015 Ferrari 458 Spider"]
    assert s458.status == "unsold" and s458.price is None and s458.extra["bid_goes_on_figure"] == 600000
    assert outcome(recs["1965 Ferrari 330 2+2"])[0] == "330-gt-2-2"
    assert outcome(recs["2014 Ferrari F12 Berlinetta"])[0] == "f12berlinetta"
    assert outcome(recs["2024 Ferrari 812 Competizione Aperta"])[0] == "812-competizione-a"
    assert recs["2025 Ferrari SF90 XX Stradale"].status == "upcoming"
    for junk in ("Custom Ferrari Single-Sided Tin Neon Sign", "2016 Ferrari Crest Single-Sided Plastic Lighted Sign",
                 "2022 Harrington Junior Ferrari 365 Daytona"):
        assert outcome(recs[junk])[0] == "REJECT"


# ------------------------------------------------------------------ Cars & Bids (generic mapper on the observed feed shape)

def test_carsandbids_feed_shape():
    from pipeline.extract import walk, map_generic
    from pipeline.sources.generic import _finish
    feed = {"count": 3, "total": 3, "auctions": [
        {"id": "KVzMmnmM", "title": "2017 Ferrari 488 Spider", "sub_title": "8k Miles", "status": "reserve_not_met", "current_bid": 235000,
         "sale_amount": None, "auction_end": "2026-10-30T20:17:14.889+00:00", "mileage": "8,100 Miles", "location": "Brentwood, CA 94513",
         "no_reserve": False, "main_photo": {"base_url": "x", "path": "y"}},
        {"id": "AbC12345", "title": "2015 Ferrari 458 Italia", "status": "sold", "current_bid": 231000, "sale_amount": 231000,
         "auction_end": "2026-10-29T20:00:00+00:00", "mileage": "12,400 Miles", "location": "Austin, TX 78701", "no_reserve": True},
        {"id": "LiVe0001", "title": "2008 Ferrari F430 Spider", "status": "live", "current_bid": 98000, "sale_amount": None,
         "auction_end": "2026-11-03T20:00:00+00:00", "mileage": "21k Miles", "location": "Miami, FL", "no_reserve": False}]}
    cfg = {"label": "Cars & Bids", "url_template": "https://carsandbids.com/auctions/{id}"}
    recs = {r.source_id: _finish(r, cfg, "") for r in (map_generic(d, "carsandbids", "auction") for d in walk(feed))}
    assert recs["KVzMmnmM"].status == "unsold" and recs["KVzMmnmM"].price == 235000
    assert recs["AbC12345"].status == "sold" and recs["AbC12345"].no_reserve is True
    assert recs["LiVe0001"].status == "live" and recs["LiVe0001"].url == "https://carsandbids.com/auctions/LiVe0001"
    assert outcome(recs["KVzMmnmM"]) == ("488-spider", 235000.0, 8100.0)
    assert outcome(recs["LiVe0001"])[2] == 21000.0


def test_rm_results_are_dated_by_their_auction_page(tmp_path):
    """Event JSON-LD as served on rmsothebys.com/auctions/lf26/ (2026-10-31)."""
    from datetime import date
    from pipeline import db
    from pipeline.sources.base import Context
    from pipeline.sources.rmsothebys import RMSothebys, auction_page, event_dates

    page = ('<script type="application/ld+json">{"@context":"https://schema.org","@type":" Event",'
            '"headline":"The London Auction 2026","startDate":"2026-10-31","endDate":"2026-10-31","organizer":{}}</script>')
    assert event_dates(page) == ("2026-10-31", "2026-10-31")
    assert auction_page("https://sealed.rmsothebys.com/auctions/s1226/lots/r0012-x/") == "https://rmsothebys.com/auctions/s1226/"
    assert auction_page("https://rmsothebys.com/ps00/inventory/r6564-2003-ferrari-enzo/") is None

    class FakeFetcher:
        requests = 0
        def get(self, url):
            self.requests += 1
            return type("R", (), {"text": page})()

    con = db.connect(tmp_path / "t.db")
    con.execute("INSERT INTO lots (uid, source, model_id, title, status, price_usd, url) VALUES (?,?,?,?,?,?,?)",
                ("rmsothebys:1", "rmsothebys", "daytona-sp3", "2022 Ferrari Daytona SP3", "sold", 10_400_000,
                 "https://rmsothebys.com/auctions/lf26/lots/r0063-2022-ferrari-daytona-sp3/"))
    f = FakeFetcher()
    src = RMSothebys("rmsothebys", {}, Context(fetcher=f, run_date=date(2026, 11, 1), con=con))
    assert src.date_stored_results() == 1
    assert con.execute("SELECT ends_at FROM lots").fetchone()[0].startswith("2026-10-31")
    # a second source instance reads the cached date instead of fetching the page again
    src2 = RMSothebys("rmsothebys", {}, Context(fetcher=f, run_date=date(2026, 11, 1), con=con))
    assert src2.auction_end("https://rmsothebys.com/auctions/lf26/lots/other/") == "2026-10-31" and f.requests == 1


# Broad Arrow lot pages, structure as served 2026-10-31 (scripts, images and links trimmed)
def _ba_page(price_row: str, extra_spans: str = "") -> str:
    return f"""<html><body><div class="vdp-header"><div class="section left"><div class="text-top">
      <h1> 2003 Ferrari Enzo </h1>
      <p class="lot-and-branch"><span> Lot 182 | </span><span> The Amelia Auction 2026 </span>
        <span class="flag-icon flag-icon-us branch-country-flag"></span>{extra_spans}</p>
      <div class="price-row mobile"><div class="custom-dropdown"><select><option>USD</option></select></div>{price_row}</div>
    </div></div></div>
    <script type="application/ld+json">{{"@type": "Car", "vehicleIdentificationNumber": "ZFFCW56A030133000",
      "offers": {{"price": "151850000", "priceCurrency": "USD"}}}}</script></body></html>"""


BA_EVENTS = """<a class="content-col show-past"><div class="event-info"><div class="title-top-wrap">
  <h2 class="top">6 - 7 March 2026</h2><h2 class="mid">The Amelia Auction 2026</h2></div></div></a>
  <a class="content-col show-past"><h2 class="top">24 - 25 May 2025</h2><h2 class="mid">The Concorso d’Eleganza Villa d’Este 2025</h2></a>
  <a class="content-col active"><h2 class="top">7 November 2026</h2><h2 class="mid">The Zurich Auction 2026</h2></a>
  <a class="content-col show-past"><h2 class="top">23 January - 1 February 2026</h2><h2 class="mid">Global Icons: Europe Online</h2></a>"""

BA_URL = "https://www.broadarrowauctions.com/vehicles/am26_r0187/2003-ferrari-enzo"


def test_broadarrow_events_and_name_matching():
    from pipeline.sources.broadarrow import parse_events, find_event, parse_event_dates
    ev = parse_events(BA_EVENTS)
    assert find_event(ev, "The Amelia Auction 2026") == ("2026-03-06", "2026-03-07")
    assert find_event(ev, "The Concorso d'Eleganza Villa d'Este Auction 2025") == ("2025-05-24", "2025-05-25")
    assert find_event(ev, "The Zürich Auction 2026") == ("2026-11-07", "2026-11-07")
    assert find_event(ev, "Global Icons: Europe Online") == ("2026-01-23", "2026-02-01")
    assert parse_event_dates("30 September - 2 October 2026") == ("2026-09-30", "2026-10-02")


def test_broadarrow_lot_pages():
    from datetime import date
    from pipeline.sources.broadarrow import parse_events, parse_lot
    ev, today = parse_events(BA_EVENTS), date(2026, 11, 1)

    rec, why = parse_lot(_ba_page('<span id="label">Sold Price:</span> <span id="convert_price">$15,185,000</span>'), BA_URL, ev, today)
    assert why == "final" and rec.status == "sold" and rec.price == 15_185_000 and rec.currency == "USD"
    assert rec.ends_at.startswith("2026-03-07") and rec.source_id == "am26_r0187" and rec.vin == "ZFFCW56A030133000"
    assert rec.lot_number == "182" and rec.country == "US"          # the JSON-LD price (x10) is ignored

    rec, _ = parse_lot(_ba_page("<span>$555,000</span>"), BA_URL, ev, today)
    assert rec.status == "sold" and rec.price == 555_000
    rec, _ = parse_lot(_ba_page('<span id="label">Sold Price:</span><span>CHF 75\'000</span>'), BA_URL, ev, today)
    assert rec.price == 75_000 and rec.currency == "CHF"
    rec, _ = parse_lot(_ba_page('<span id="label">Sold Price:</span><span>€184.250</span>'), BA_URL, ev, today)
    assert rec.price == 184_250 and rec.currency == "EUR"

    rec, _ = parse_lot(_ba_page("<span>Not Sold</span>"), BA_URL, ev, today)
    assert rec.status == "unsold" and rec.price is None

    for words in ("Sold After Auction", "Sold Before Auction", "Sold"):
        rec, why = parse_lot(_ba_page(f"<span>{words}</span>"), BA_URL, ev, today)
        assert rec is None and why == "sold, price not published"

    # an estimate before the sale: upcoming, dated by the sale day on the page, estimate only
    up = _ba_page('<span id="label">Estimate:</span><span>CHF 130\'000 - CHF 170\'000</span>',
                  "<span>Saturday, 07 November 2026</span>")
    rec, why = parse_lot(up, BA_URL, ev, today)
    assert why == "upcoming" and rec.status == "upcoming" and rec.price is None
    assert (rec.estimate_low, rec.estimate_high, rec.currency) == (130_000, 170_000, "CHF") and rec.ends_at.startswith("2026-11-07")
    # the same estimate after the sale means no result was published
    rec, why = parse_lot(_ba_page('<span id="label">Estimate:</span><span>$6,500,000 - $8,500,000</span>'), BA_URL, ev, today)
    assert rec is None and why.startswith("past lot")


def test_broadarrow_sitemap_keeps_only_ferrari_lots():
    import gzip
    from pipeline.sources.broadarrow import lot_urls
    xml = ("<urlset><url><loc>https://www.broadarrowauctions.com/vehicles/am26_r0187/2003-ferrari-enzo</loc></url>"
           "<url><loc>https://www.broadarrowauctions.com/vehicles/ql26_r0010/2025-bugatti-w16-mistral</loc></url>"
           "<url><loc>https://www.broadarrowauctions.com/events</loc></url></urlset>").encode()
    assert lot_urls(gzip.compress(xml)) == ["https://www.broadarrowauctions.com/vehicles/am26_r0187/2003-ferrari-enzo"]
