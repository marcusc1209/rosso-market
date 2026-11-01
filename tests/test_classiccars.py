"""ClassicCars.com, from the page shapes seen on 2026-11-01."""
from datetime import date

from pipeline.sources.classiccars import parse_search, parse_listing, map_listing

CARD = """
<div class="search-result-item w100 featured">
 <a href="/listings/view/2119733/1987-ferrari-328-gts-for-sale-in-beaconsfield-quebec-h9w-3l5" class="x">
  <img data-src="https://photos.classiccars.com/cc-temp/listing/211/9733/61828944-1987-ferrari-328-gts-thumb.jpg" class="img-fluid lazyload"></a>
 <div class="red-text fs-12 b">CC-2119733&nbsp;</div>
 <a href="/listings/view/2119733/1987-ferrari-328-gts-for-sale-in-beaconsfield-quebec-h9w-3l5" class="d-block">
  <div class="dark mrg-b-sm b fs-20 w100 height-rw h-sri-car-title">1987 Ferrari 328 GTS</div>
  <div class="b fs-18 mrg-b-sri-price green"><span>$132,000</span></div></a>
</div>
<div class="search-result-item w100 ">
 <a href="/listings/view/2122984/2025-ferrari-daytona-for-sale-in-las-vegas-nevada-8910">x</a>
 <div class="h-sri-car-title">2025 Ferrari Daytona</div>
 <div class="b fs-18 mrg-b-sri-price green">Auction Vehicle</div>
</div>"""

LISTING = """
<ul>
<li class="border-btm p-address"> <span class="w40">Location:</span><span class="w40 gray">Beverly Hills, California</span> <div></div></li>
<li class="border-btm p-model"> <span class="w40">Model:</span><span class="w40 gray">328 GTS</span> <div>Get Collector Car Insurance | Hagerty</div></li>
<li class="border-btm p-color"> <span class="w40">Exterior Color:</span><span class="w50 gray">Rosso Corsa</span></li>
<li class="border-btm p-color"> <span class="w40">Interior Color:</span><span class="w50 gray">Tan</span></li>
<li class="border-btm p-odometer"> <span class="w40">Odometer:</span><span class="w50 gray">18583</span></li>
<li class="border-btm p-vin"> <span class="w40">VIN:</span><span class="w40 gray">ZFFXA20A1H0071234</span></li>
</ul>
<div id="seller-information"><b>Listed By:</b> <div class="b" aria-label="Seller or Company Name">Beverly Hills Car Club</div></div>
<script>var items = [ { src: "https://photos.classiccars.com/cc-temp/listing/211/9733/1-std.jpg", w: 1280, h: 960 } ];
items.push( { src: "https://photos.classiccars.com/cc-temp/listing/211/9733/2-std.jpg", w: 1280, h: 960 })</script>
<input type="hidden" id="PageAdParams" value="[{&quot;TargetName&quot;:&quot;CountryCode&quot;,&quot;TargetValues&quot;:[&quot;US&quot;]},{&quot;TargetName&quot;:&quot;CompanyType&quot;,&quot;TargetValues&quot;:[&quot;Dealer&quot;]}]" />
"""


def test_search_cards():
    cards = parse_search(CARD)
    assert [c["id"] for c in cards] == ["2119733", "2122984"]
    assert cards[0]["title"] == "1987 Ferrari 328 GTS" and cards[0]["price_text"] == "$132,000"
    assert cards[0]["image"].endswith("-thumb.jpg")
    assert cards[1]["price_text"] == "Auction Vehicle"


def test_listing_maps_to_a_record():
    card = parse_search(CARD)[0]
    d = parse_listing(LISTING)
    assert d["seller"] == "Beverly Hills Car Club" and d["ads"]["CompanyType"] == "Dealer"
    r = map_listing(card, d, "classiccars", date(2026, 11, 1))
    assert r.price == 132000 and r.year == 1987 and r.mileage == 18583
    assert r.model_text == "328 GTS" and r.exterior_color == "Rosso Corsa" and r.interior_color == "Tan"
    assert r.vin == "ZFFXA20A1H0071234" and r.location == "Beverly Hills, California"
    assert r.seller_type == "dealer" and r.dealer_name == "Beverly Hills Car Club"
    assert r.image_url.endswith("1-std.jpg") and len(r.extra["photos"]) == 2 and r.extra["detail"]


def test_auction_vehicles_and_blank_odometers_are_skipped():
    cards = parse_search(CARD)
    assert map_listing(cards[1], {}, "classiccars", date(2026, 11, 1)) is None
    d = parse_listing(LISTING.replace(">18583<", ">0<"))
    assert map_listing(cards[0], d, "classiccars", date(2026, 11, 1)).mileage is None
