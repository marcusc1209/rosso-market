"""Photo URLs, from the shapes each source returned on 2026-11-01."""
from pipeline.images import first_image, find_image, gooding, sized
from pipeline.sources.online_auctions import map_somo, map_dupont
from pipeline.sources.rmsothebys import map_item
from pipeline.sources.gooding import map_vehicle
from pipeline.sources.bonhams import map_lot
from pipeline.sources.pcarmarket import parse_cards
from pipeline.sources.broadarrow import parse_lot
from pipeline.sources.ferrari_approved import map_ad
from pipeline.extract import map_generic
from datetime import date, datetime, timezone

SOMO_IMG = "https://d3sm3e0zl6r9d1.cloudfront.net/sothebysmotor/vehicles/455B/images/03B4.webp"


def test_first_image_shapes():
    assert first_image("https://x.com/a.jpg") == "https://x.com/a.jpg"
    assert first_image("//x.com/a.jpg") == "https://x.com/a.jpg"
    assert first_image("/media/flags/us.png") is None
    assert first_image({"url": "https://images1.bonhams.com/image?src=a.jpeg"}).startswith("https://images1")
    assert first_image({"base_url": "media.carsandbids.com", "path": "abc/photos/x.jpg?t=1"}) == \
        "https://media.carsandbids.com/cdn-cgi/image/width=640,quality=70/abc/photos/x.jpg?t=1"
    assert first_image({"fullUrl": "https://c/full.webp", "resizedImages": {"small": "https://c/s.webp", "medium": SOMO_IMG}}) == SOMO_IMG
    assert first_image({"homenetImages": ["https://gcb.evs.onl/d4/1.jpg", "https://gcb.evs.onl/d4/2.jpg"]}) == "https://gcb.evs.onl/d4/1.jpg"
    assert first_image(None) is None and first_image([]) is None
    assert find_image({"title": "x", "images": {"thumbnail": {"url": "https://cdn.mecum.com/a.jpg"}}}) == "https://cdn.mecum.com/a.jpg"
    assert find_image({"title": "x", "logo": "https://a/logo.png"}) is None
    assert gooding("Prod/GN26 - Geneva 2026/119_1971 Ferrari Dino 246 GT/x_vtmti9") == \
        "https://media.goodingco.com/image/upload/c_fill,g_auto,q_80,w_640/v1/Prod/GN26%20-%20Geneva%202026/119_1971%20Ferrari%20Dino%20246%20GT/x_vtmti9"
    assert sized("https://a/b?src=c.jpg", width=640) == "https://a/b?src=c.jpg&width=640"


def test_auction_sources_carry_a_photo():
    somo = map_somo({"auctionId": 1, "vehicleData": {"make": "Ferrari", "year": 2019, "model": "488 Pista", "slug": "s",
                                                     "files": {"titleImage": {"fullUrl": "https://c/full.webp",
                                                                              "resizedImages": {"medium": SOMO_IMG}}}}},
                    "somo", "sold")
    assert somo.image_url == SOMO_IMG
    dp = map_dupont({"auctionId": 2, "slug": "d", "title": "2015 Ferrari 458", "vehicle": {"make": "Ferrari"},
                     "image": "https://d3nennihyoygko.cloudfront.net/dprauctions/vehicles/0F/images/fc.jpg"}, "dupont_live", "live")
    assert dp.image_url.endswith("fc.jpg")
    rm = map_item({"id": 3, "publicName": "1973 Ferrari 365 GT4 2+2", "value": "$224,000 USD", "valueType": "Sold", "sold": True,
                   "link": "/auctions/mu25/lots/r0013-x/", "crop": "https://cdn.rmsothebys.com/9/e/0/9e05.webp"}, "rmsothebys", "Results")
    assert rm.image_url == "https://cdn.rmsothebys.com/9/e/0/9e05.webp"
    g = map_vehicle({"objectID": "o", "itemType": "Cars", "title": "1971 Ferrari Dino 246 GT", "salePrice": "450000", "currency": "USD",
                     "auctionEndDate": "1760000000000", "slug": "x",
                     "cloudinaryImages": [{"public_id": "Prod/GN26/119/x_vtmti9"}]}, "gooding", datetime.now(timezone.utc))
    assert g.image_url == "https://media.goodingco.com/image/upload/c_fill,g_auto,q_80,w_640/v1/Prod/GN26/119/x_vtmti9"
    b = map_lot({"status": "SOLD", "title": "2004 Ferrari Enzo", "price": {"hammerPremium": 6000000}, "currency": {"iso_code": "USD"},
                 "auctionId": 1, "lotId": 2, "image": {"url": "https://images1.bonhams.com/image?src=Images/live/a.jpeg"}}, "bonhams")
    assert b.image_url == "https://images1.bonhams.com/image?src=Images/live/a.jpeg&width=640"


def test_pages_carry_a_photo():
    card = ('<a href="/results/2012-ferrari-458-spider-1"><article><img src="https://assets.basta.app/a/images/86?width=500">'
            '<h3>2012 Ferrari 458 Spider</h3><span>Westhampton, NY</span><span>102 Bids</span><span>Sold price</span>'
            '<span>$450,000</span></article></a>')
    assert parse_cards(card)[0]["image"] == "https://assets.basta.app/a/images/86?width=500"
    page = ('<html><head><meta property="og:image" content="https://cdn.dealeraccelerate.com/bagauction/37/5849/273794/790x1024/x">'
            '</head><body><div class="vdp-header"><h1>2020 Ferrari 488 Pista Spider</h1>'
            '<div class="price-row"><span>Sold Price:</span><span>$650,000</span></div></div></body></html>')
    rec, _ = parse_lot(page, "https://www.broadarrowauctions.com/vehicles/ql26_r0071/2020-ferrari-488-pista-spider", {}, date(2026, 11, 1))
    assert rec.image_url.endswith("/790x1024/x")
    ad = map_ad({"id": 9, "carName": "F8 Tributo", "year": 2021, "price": 300000, "dealer": {"name": "Ferrari X"},
                 "cardImages": {"homenetImages": ["https://gcb.evs.onl/d4/o/2e9e.jpg"]}}, "ferrari_approved", "usa")
    assert ad.image_url == "https://gcb.evs.onl/d4/o/2e9e.jpg?height=400"
    cb = map_generic({"id": "abc", "title": "2015 Ferrari 458 Speciale", "current_bid": 400000, "auction_end": "2026-11-02",
                      "main_photo": {"base_url": "media.carsandbids.com", "path": "46/photos/x.jpg"}}, "carsandbids", "auction")
    assert cb.image_url == "https://media.carsandbids.com/cdn-cgi/image/width=640,quality=70/46/photos/x.jpg"
