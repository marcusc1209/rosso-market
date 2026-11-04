"""AutoTrader and duPont Registry, from the shapes seen on 2026-11-01."""
import json
from datetime import date

from pipeline.sources.dealer_marketplaces import (
    flight_data, parse_autotrader, map_autotrader, parse_dupont, map_dupont_registry)

AT_LISTING = {
    "id": 773331441, "vin": "ZFF79ALA2K0245448", "year": 2019, "title": "Used 2019 Ferrari 488 GTB",
    "make": {"code": "FER", "name": "Ferrari"}, "model": {"code": "FER488GTB", "name": "488 GTB"},
    "mileage": {"label": "Mileage", "value": "11,741"},
    "color": {"exteriorColor": "Bianco Avus", "interiorColor": "Nero"},
    "specifications": {"transmission": {"label": "Transmission", "value": "7-Speed Automatic"}},
    "pricingDetail": {"dealerFeesTotal": 3080, "displayPrice": 291076, "preFeeDerivedPrice": 287996, "salePrice": 287996},
    "pricingHistory": [{"dateUpdated": "10.02.2026", "price": "$$303,126"}, {"dateUpdated": "10.05.2026", "price": "$$296,076"},
                       {"dateUpdated": "Price Today", "price": "$$291,076"}],
    "daysOnSite": 100, "listingType": "USED", "ownerId": 75818391, "ownerName": "McLaren Charlotte",
    "images": {"primary": 0, "sources": [{"src": "https://images.autotrader.com/hn/c/e595b9cf.jpg"},
                                         {"src": "https://images.autotrader.com/hn/c/b52de980.jpg"}]},
    "stockId": "K0245448",
}
OWNERS = {"75818391": {"id": 75818391, "location": {"address": {"city": "Charlotte", "state": "NC"}}}}


def _page(obj: str) -> str:
    # the page splits its data over several pushed chunks
    half = len(obj) // 2
    return ("<html><script>self.__next_f.push([1," + json.dumps(obj[:half]) + "])</script>"
            "<script>self.__next_f.push([1," + json.dumps(obj[half:]) + "])</script></html>")


def test_autotrader_page_and_listing():
    body = '0:{"x":1}\n5:{"inventory":' + json.dumps({"773331441": AT_LISTING}) + ',"owners":' + json.dumps(OWNERS) + ',"totalResultCount":2283}'
    html = _page(body)
    assert flight_data(html) == body
    inv, owners, total = parse_autotrader(html)
    assert total == 2283 and list(inv) == ["773331441"] and "75818391" in owners
    r = map_autotrader(inv["773331441"], owners, "autotrader_us", date(2026, 11, 1))
    assert r.title == "2019 Ferrari 488 GTB" and r.price == 287996 and r.vin == "ZFF79ALA2K0245448"
    assert r.mileage == 11741 and r.location == "Charlotte, NC" and r.dealer_name == "McLaren Charlotte"
    assert r.exterior_color == "Bianco Avus" and r.transmission == "7-Speed Automatic"
    assert r.image_url == "https://images.autotrader.com/scaler/640/480/hn/c/e595b9cf.jpg"
    assert r.extra["listed_on"] == "2026-07-24"
    assert r.extra["price_history"] == [["2026-10-02", 303126], ["2026-10-05", 296076], ["2026-11-01", 291076]]
    assert len(r.extra["photos"]) == 2 and r.certified is False
    assert map_autotrader({**AT_LISTING, "listingType": "NEW"}, owners, "x", date(2026, 11, 1)) is None
    assert map_autotrader({**AT_LISTING, "pricingDetail": {}}, owners, "x", date(2026, 11, 1)).price is None


def test_dupont_registry_item():
    item = {"@type": ["Car", "Product"], "url": "https://www.dupontregistry.com/car/ferrari/f12--berlinetta/2014/ZFF74UFA9E0199438/647401",
            "name": "2014 Ferrari F12 Berlinetta", "mileageFromOdometer": {"value": 6947, "unitCode": "SMI"},
            "model": "F12 Berlinetta", "vehicleModelDate": 2014, "color": "Other", "vehicleInteriorColor": "Black",
            "image": "https://d2ujs0u2ekt0bo.cloudfront.net/cache/width_1920/car/car-647401/photo/x.webp",
            "vehicleIdentificationNumber": "ZFF74UFA9E0199438",
            "offers": {"priceCurrency": "USD", "itemCondition": "https://schema.org/UsedCondition", "price": 520699,
                       "seller": {"@type": "AutoDealer", "name": "Miller Motorcars",
                                  "address": {"addressLocality": "Greenwich", "addressRegion": "CT", "addressCountry": "US"}}},
            "additionalProperty": [{"name": "Certified", "value": True}]}
    html = ('<script type="application/ld+json" data-srp-structured-data="true">'
            + json.dumps({"@type": "ItemList", "itemListElement": [{"position": 1, "item": item}]}) + "</script>")
    items = parse_dupont(html)
    r = map_dupont_registry(items[0], "dupont_registry")
    assert r.source_id == "647401" and r.price == 520699 and r.vin == "ZFF74UFA9E0199438" and r.mileage == 6947
    assert r.exterior_color is None and r.interior_color == "Black" and r.location == "Greenwich, CT"
    assert r.dealer_name == "Miller Motorcars" and r.marketplace == "duPont Registry" and r.extra["dealer_certified"]
    new = dict(item, offers={**item["offers"], "itemCondition": "https://schema.org/NewCondition"})
    assert map_dupont_registry(new, "x") is None


def test_autotrader_switches_to_browser_when_page_has_no_listings():
    import logging
    from types import SimpleNamespace
    from pipeline.sources.dealer_marketplaces import AutoTrader
    body = '5:{"inventory":' + json.dumps({"773331441": AT_LISTING}) + ',"owners":' + json.dumps(OWNERS) + ',"totalResultCount":1}'
    asked = []

    class F:
        def get(self, url):
            asked.append(("plain", url))
            return SimpleNamespace(text="<html><title>Autotrader</title><body>stub</body></html>")

    class B:
        def html(self, url):
            asked.append(("browser", url))
            return _page(body)

    src = AutoTrader.__new__(AutoTrader)
    src.name, src.cfg, src.log = "autotrader_us", {"browser_pause_seconds": 0}, logging.getLogger("t")
    src.ctx = SimpleNamespace(fetcher=F(), browser=B(), run_date=date(2026, 11, 1))
    out = list(src.fetch())
    assert [r.vin for r in out] == ["ZFF79ALA2K0245448"]
    assert [k for k, _ in asked] == ["plain", "browser"]


def test_listing_dates_are_read_once_and_remembered(tmp_path):
    from pipeline import db
    from pipeline.records import RawRecord, LISTING
    from pipeline.sources.dealer_marketplaces import DupontRegistry, dupont_listed_on
    from pipeline.sources.base import Context

    assert dupont_listed_on('{"@type":"PropertyValue","name":"Date Listed","value":"1785998213000"}') == "2026-08-06"
    assert dupont_listed_on(r'\"name\":\"Date Listed\",\"value\":\"1785998213000\"') == "2026-08-06"

    con = db.connect(tmp_path / "t.db")
    con.execute("""INSERT INTO sightings (uid, source, title, first_seen, last_seen, raw)
                   VALUES ('dupont_registry:old', 'dupont_registry', 'x', '2026-11-01', '2026-11-01', ?)""",
                (json.dumps({"extra": {"listed_on": "2026-05-01"}}),))
    ctx = Context(fetcher=None, run_date=date(2026, 11, 2), con=con)
    src = DupontRegistry("dupont_registry", {"date_pages": 5}, ctx)
    pages = []
    src._html = lambda url: pages.append(url) or '{"name":"Date Listed","value":"1785998213000"}'
    recs = [RawRecord(source="dupont_registry", kind=LISTING, source_id=s, url="https://x/" + s, title="t") for s in ("old", "new")]
    out = list(src._with_listing_dates(recs, dupont_listed_on))
    assert [r.extra["listed_on"] for r in out] == ["2026-05-01", "2026-08-06"]
    assert pages == ["https://x/new"]                      # the stored date was not read again
    # a car the pipeline rejected (never stored as a listing) is not read again either
    recs = [RawRecord(source="dupont_registry", kind=LISTING, source_id="new", url="https://x/new", title="t")]
    out = list(src._with_listing_dates(recs, dupont_listed_on))
    assert out[0].extra["listed_on"] == "2026-08-06" and pages == ["https://x/new"]
