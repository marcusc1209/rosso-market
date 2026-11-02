"""AutosToday, from the page shape seen on 2026-11-01."""
import json
from datetime import date

from pipeline.sources.autostoday import parse_page, map_listing, vin_model
from pipeline import taxonomy

ROW = {"id": "ZFF80AMA1J0232198-5de60ffb-2db2", "sponsored": False, "fields": {
    "title": "2018 Ferrari 488 Base", "vin": "ZFF80AMA1J0232198", "price": 254900, "trim": "Base",
    "mileage": 12450, "location": "Costa Mesa, CA",
    "photo": "https://automanager.blob.core.windows.net/wmphotos/042615/abc_1280.jpg", "year": 2018,
    "dealer": "eurocar", "dealer_code": "eurocar-costa-mesa-ca", "new_used": "used", "make": "Ferrari",
    "model": "488", "dom": 19, "exterior_color": "Rosso Corsa", "interior_color": "Other"}}


def _page(rows, count):
    data = {"props": {"pageProps": {"listingsData": {"count": count, "results": rows}}}}
    return '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + "</script></html>"


def test_page_and_listing():
    rows, n = parse_page(_page([ROW], 2406))
    assert n == 2406 and len(rows) == 1
    r = map_listing(rows[0], "autostoday", date(2026, 11, 1))
    assert r.source_id == "ZFF80AMA1J0232198" and r.price == 254900 and r.mileage == 12450
    assert r.url.endswith("/listing/ZFF80AMA1J0232198")
    assert r.dealer_name == "Eurocar" and r.location == "Costa Mesa, CA" and r.interior_color is None
    assert r.extra["listed_on"] == "2026-10-13" and r.image_url.endswith("_1280.jpg")
    # "488 Base" is a 488 Spider by its VIN
    assert r.model_text == "488 Spider"
    assert taxonomy.resolve(r.title, r.year, r.model_text).model.id == "488-spider"


def test_vin_model_only_for_modern_ferraris():
    assert vin_model("ZFF93LMA2P0294614") == "F8 Spider"
    assert vin_model("ZFFYU51A040135000") is None           # 2004: older numbering
    assert vin_model("WP0AB2A99KS000000") is None


def test_new_cars_and_other_makes_are_skipped():
    new = json.loads(json.dumps(ROW)); new["fields"]["new_used"] = "new"
    other = json.loads(json.dumps(ROW)); other["fields"]["make"] = "Lamborghini"
    assert map_listing(new, "autostoday", date(2026, 11, 1)) is None
    assert map_listing(other, "autostoday", date(2026, 11, 1)) is None
