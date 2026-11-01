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


# ---- Gooding Christie's: getVehicles records as served 2026-10-31
GOODING = [
    {"objectID": "4YASVkhagzZtay8EjNSyMd", "title": "1969 Ferrari 365 GTC", "modelYear": 1969, "model": "365 GTC",
     "itemType": "Cars", "auctionType": "Live Auction", "auctionName": "Pebble Beach Auctions", "lotNumber": 150,
     "lowEstimate": 750000, "highEstimate": 850000, "uniqueEstimate": None, "salePrice": "841000",
     "privateSalesPrice": False, "currency": "USD", "auctionEndDate": "1786838400000",
     "upcomingAuctionEndDate": "9999999999999", "slug": "1969-ferrari-365-gtc-pb26", "hasReservePrice": True},
    {"objectID": "6bxA31H1XZt5mMKPUNId3", "title": "1971 Ferrari Dino 246 GT", "modelYear": 1971, "model": "Dino 246 GT",
     "itemType": "Cars", "auctionType": "Live Auction", "auctionName": "Geneva Auction", "lotNumber": 7,
     "lowEstimate": 275000, "highEstimate": 325000, "uniqueEstimate": None, "salePrice": None,
     "privateSalesPrice": None, "currency": "CHF", "auctionEndDate": None, "upcomingAuctionEndDate": "1794175200000",
     "slug": "1971-ferrari-dino-246-gt-gn26", "hasReservePrice": True},
    {"objectID": "4SeHAicDeeFI5wL9RMK5WD", "title": "2024 Ferrari 499P Modificata", "modelYear": 2024,
     "itemType": "Cars", "auctionType": "Live Auction", "auctionName": "Geneva Auction", "lotNumber": 17,
     "lowEstimate": None, "highEstimate": None, "uniqueEstimate": "In excess of CHF 7,500,000", "salePrice": None,
     "currency": "CHF", "auctionEndDate": None, "upcomingAuctionEndDate": "1794175200000", "slug": "2024-ferrari-499p-modificata"},
    {"objectID": "5uVM5xJFu8fnfshxIWZnJm", "title": "2017 Ferrari F12 Berlinetta 70th Anniversary Edition", "modelYear": 2017,
     "itemType": "Cars", "auctionType": "Live Auction", "auctionName": "Scottsdale 2020", "lotNumber": 164,
     "lowEstimate": 375000, "highEstimate": 450000, "salePrice": None, "privateSalesPrice": False, "currency": "USD",
     "auctionEndDate": "1579365000000", "upcomingAuctionEndDate": "9999999999999",
     "slug": "2017-ferrari-f12-berlinetta-70th-anniversary-edition-3", "hasReservePrice": True},
    {"objectID": "NVJEGkiTYG1V00ZPGs93O", "title": "1984 Ferrari 512 BBi", "itemType": "Cars", "auctionType": "Private Sale",
     "askingPrice": 475000, "currency": "USD", "upcomingAuctionEndDate": "9999999999998", "slug": "1984-ferrari-512-bbi-ps"},
    {"objectID": "x1", "title": "1957 Ferrari 250 Testa Rossa", "itemType": "Cars", "auctionType": "Live Auction",
     "salePrice": "1", "privateSalesPrice": True, "currency": "USD", "auctionEndDate": "1579365000000", "slug": "x"},
]


def test_gooding_vehicles():
    from datetime import datetime, timezone
    from pipeline.sources.gooding import map_vehicle
    now = datetime(2026, 11, 1, tzinfo=timezone.utc)
    sold, upcoming, unique, unsold, private, withheld = [map_vehicle(v, "gooding", now) for v in GOODING]
    assert sold.status == "sold" and sold.price == 841_000 and sold.ends_at.startswith("2026-08-16")
    assert sold.url == "https://www.goodingco.com/lot/1969-ferrari-365-gtc-pb26/" and sold.estimate_low == 750_000
    assert upcoming.status == "upcoming" and upcoming.price is None and upcoming.currency == "CHF"
    assert (upcoming.estimate_low, upcoming.estimate_high) == (275_000, 325_000) and upcoming.ends_at.startswith("2026-11-08")
    assert unique.estimate_low == 7_500_000 and unique.estimate_high is None
    assert unsold.status == "unsold" and unsold.price is None and unsold.ends_at.startswith("2020-01-18")
    assert private is None and withheld is None


# ---- Bonhams: Typesense documents as served 2026-10-31
BONHAMS_SOLD = {"id": "32045-145", "title": "1968 Ferrari Dino 206 GT Chassis no. 00150 Engine no. 135B 29", "status": "SOLD",
                "auctionType": "PUBLIC", "price": {"hammerPremium": 563500, "hammerPrice": 490000, "estimateLow": 500000,
                                                   "estimateHigh": 550000},
                "currency": {"iso_code": "EUR"}, "auctionEndDate": {"timestamp": 1791755999}, "auctionId": "32045",
                "lotId": "145", "slug": "1968-ferrari-dino-206-gt-chassis-no-00150-engine-no-135b-29",
                "country": {"code": "BE", "name": "Belgium"}, "flags": {"isResultPublished": True, "isWithoutReserve": False},
                "department": {"name": "Cars"}}


def test_bonhams_documents():
    import copy
    from pipeline.sources.bonhams import map_lot, find_key
    rec = map_lot(BONHAMS_SOLD, "bonhams")
    assert rec.status == "sold" and rec.price == 563_500 and rec.currency == "EUR" and rec.country == "BE"
    assert rec.ends_at.startswith("2026-10-11") and rec.no_reserve is False
    assert rec.url == "https://www.bonhams.com/auction/32045/lot/145/1968-ferrari-dino-206-gt-chassis-no-00150-engine-no-135b-29/"
    bi = copy.deepcopy(BONHAMS_SOLD); bi.update(status="BI"); bi["price"].update(hammerPremium=0, hammerPrice=0)
    assert map_lot(bi, "bonhams").status == "unsold" and map_lot(bi, "bonhams").price is None
    new = copy.deepcopy(BONHAMS_SOLD); new.update(status="NEW")
    up = map_lot(new, "bonhams")
    assert up.status == "upcoming" and up.price is None and up.estimate_low == 500_000
    for st in ("WD", "CS"):
        other = copy.deepcopy(BONHAMS_SOLD); other["status"] = st
        assert map_lot(other, "bonhams") is None
    hidden = copy.deepcopy(BONHAMS_SOLD); hidden["flags"]["isResultPublished"] = False
    assert map_lot(hidden, "bonhams") is None
    assert find_key('let i={host:"api01.bonhams.com",port:Number("443"),protocol:"https",path:"/search-proxy",apiKey:"abc123XYZ"}') == "abc123XYZ"


# ---- PCARMARKET: card markup and lot page text as served 2026-10-31
PCAR_CARDS = """
<a href="/results/2000-ferrari-550-maranello-3" class="group block"><article><div><div></div></div>
  <h2>22k-Mile 2000 Ferrari 550 Maranello</h2><p>Thousand Oaks, CA</p><div><span><span>49 Bids</span></span></div>
  <div><div><span>Bid to</span><span>$300,000</span></div></div></article></a>
<a href="/results/2012-ferrari-458-spider-1791812243893132258"><article><div><span>Sold</span></div>
  <h2>5,500-Mile 2012 Ferrari 458 Spider</h2><p>Westhampton, NY</p><div><span>102 Bids</span></div>
  <div><span>Sold price</span><span>$450,000</span></div></article></a>
<a href="/results/hachette-ferrari-collection"><article><h2>Hachette Ferrari Model Car Collection</h2>
  <p>Albuquerque, NM, United States</p><span>13 Bids</span><span>Sold price</span><span>$1,600</span></article></a>
<a href="/auction/1999-ferrari-360-modena-f1-1792436127235961256"><article><span>Current bid</span><span>$35,750</span>
  <h2>7k-Mile 1999 Ferrari 360 Modena F1</h2><p>Chilliwack, BC</p><span>16 Bids</span><span>Current bid</span>
  <span>$35,750</span><span>Ends in</span><span>2D 16H 56M</span></article></a>
"""

PCAR_DETAIL = "".join(f"<span>{t}</span>" for t in (
    "Comments | — | Bids | 49 | Bid to | $300,000 | Ended | Oct 15, 2026 | Location | Thousand Oaks, CA | United States | "
    "Comments | — | Bids | 49 | Inquire | Car Details | Year | 2000 | Make | Ferrari | Model | 550 Maranello | VIN | "
    "ZFFZS49A7Y0119584 | Mileage Shown | 22,143 mi | Transmission | 6-Speed Manual | Title | Clean | Exterior Color | "
    "Grigio Titano | Interior | Blu Scuro | Auction Details | Auction Type | Reserve | Seller Type | Dealer | Dealer Fee | "
    "$0.00 | Location | Thousand Oaks, CA | Zip Code | 91362 | Country | United States | Ended | Oct 15, 2026, 7:28 PM"
).split(" | "))


def test_pcarmarket_cards_and_lot_page():
    from pipeline.sources.pcarmarket import parse_cards, parse_detail, to_record
    cards = parse_cards(PCAR_CARDS)
    assert [c["label"] for c in cards] == ["Bid to", "Sold price", "Sold price", "Current bid"]
    c550, c458, junk, live = cards
    assert c550["title"] == "22k-Mile 2000 Ferrari 550 Maranello" and c550["location"] == "Thousand Oaks, CA" and c550["bids"] == 49
    assert c458["title"] == "5,500-Mile 2012 Ferrari 458 Spider" and c458["value"] == "$450,000"
    assert live["title"] == "7k-Mile 1999 Ferrari 360 Modena F1"

    d = parse_detail(PCAR_DETAIL)
    assert d["ends_at"].startswith("2026-10-15") and d["year"] == "2000" and d["make"] == "Ferrari"
    assert d["model"] == "550 Maranello" and d["vin"] == "ZFFZS49A7Y0119584" and d["mileage"] == "22,143 mi"

    rec = to_record(c550, d, "pcarmarket")
    assert rec.status == "unsold" and rec.price == 300_000 and rec.mileage == 22_143 and rec.vin == "ZFFZS49A7Y0119584"
    assert rec.url == "https://www.pcarmarket.com/results/2000-ferrari-550-maranello-3" and rec.year == 2000
    sold = to_record(c458, {}, "pcarmarket")
    assert sold.status == "sold" and sold.price == 450_000
    assert to_record(live, {}, "pcarmarket").status == "live"
    assert to_record(c550, {**d, "make": "Porsche"}, "pcarmarket") is None
