"""AutoTrader, AutoTrader.ca and duPont Registry, from the shapes seen on 2026-11-01."""
import json
from datetime import date

from pipeline.sources.dealer_marketplaces import (
    flight_data, parse_autotrader, map_autotrader, parse_dupont, map_dupont_registry,
    parse_autotrader_ca, map_autotrader_ca)

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


def test_autotrader_ca_listing():
    x = {"id": "dc7d9b81", "title": "2007 Ferrari 430", "url": "https://www.autotrader.ca/offers/ferrari-430-spider-dc7d9b81",
         "images": ["https://prod.pictures.autoscout24.net/listing-images/dc7d9b81_25bc.jpg/250x188.webp"],
         "price": {"priceRaw": 189900},
         "vehicle": {"make": "Ferrari", "model": "F430", "modelVersionInput": "Spider", "offerType": "U", "modelYear": 2007,
                     "transmission": "Automatic", "mileageInKm": "25,343 km"},
         "location": {"countryCode": "CA", "provinceCode": "ON", "city": "North York"},
         "seller": {"type": "Dealer", "companyName": "Vehicle-Direct"}}
    html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(
        {"props": {"pageProps": {"listings": [x], "numberOfPages": 17}}}) + "</script>"
    rows, pages = parse_autotrader_ca(html)
    assert pages == 17
    r = map_autotrader_ca(rows[0], "autotrader_ca")
    assert r.title == "2007 Ferrari F430 Spider" and r.price == 189900 and r.currency == "CAD"
    assert r.mileage == 25343 and r.mileage_unit == "km" and r.location == "North York, ON" and r.vin is None
    assert r.image_url.endswith("/640x480.webp") and r.extra["photos"][0].endswith("/1280x960.webp")
    assert map_autotrader_ca({**x, "vehicle": {**x["vehicle"], "offerType": "N"}}, "x") is None


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
