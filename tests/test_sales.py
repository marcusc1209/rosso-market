"""Sale names, links and countries for auction lots (pipeline/sales.py)."""
import json

from pipeline import sales, clean
from pipeline.sources.bonhams import parse_sales
from pipeline.sources.broadarrow import parse_event_pages
from pipeline.sources.gooding import sale_pages


def row(source, url, country=None, location=None, house="", ends_at="2026-08-15T18:00:00+00:00", **extra):
    return {"source": source, "url": url, "country": country, "location": location, "auction_house": house,
            "ends_at": ends_at, "raw": json.dumps({"extra": extra})}


def test_rm_sothebys_sale_page_and_country():
    d = sales.describe(row("rmsothebys", "https://rmsothebys.com/auctions/id25/lots/r0001-2024-ferrari-daytona-sp3/",
                           "CH", "St. Moritz, Switzerland", "RM Sotheby's", auction="The Tailored for Speed Collection"))
    assert d == {"sale": "The Tailored for Speed Collection", "sale_url": "https://rmsothebys.com/auctions/id25/",
                 "country": "Switzerland", "online": False}


def test_bonhams_names_its_sale_from_the_sales_index():
    sales._CACHE["bonhams"] = parse_sales({"hits": [{"document": {
        "id": "32555", "auctionTitle": "La Vente de Paris", "slug": "la-vente-de-paris",
        "venue": {"name": "Paris, The Grand Palais Historique"}, "country": {"code": "FR"}}}]})
    d = sales.describe(row("bonhams", "https://www.bonhams.com/auction/32555/lot/0/2004-ferrari-enzo/", "FR", "France",
                           "Bonhams", auction="32555"))
    assert d["sale"] == "La Vente de Paris" and d["country"] == "France"
    assert d["sale_url"] == "https://cars.bonhams.com/auction/32555/la-vente-de-paris/"


def test_broad_arrow_matches_differently_spelled_sale_names():
    html = """<a class="content-col" href="/events/event/The%20Zurich%20Auction%202025"><h2 class="top">1 November 2025</h2>
              <h2 class="mid">The Zurich Auction 2025</h2><h2 class="bot">The Dolder Grand - Zurich, Switzerland</h2></a>"""
    sales._CACHE["broadarrow"] = parse_event_pages(html)
    d = sales.describe(row("broadarrow", "https://www.broadarrowauctions.com/vehicles/dg25_r0006/1987-ferrari-testarossa",
                           "CH", "The Zürich Auction 2025", "Broad Arrow", auction="The Zürich Auction 2025"))
    assert d["sale_url"] == "https://www.broadarrowauctions.com/events/event/The%20Zurich%20Auction%202025"
    assert d["country"] == "Switzerland"


def test_gooding_page_from_its_sitemap_and_country_from_the_venue():
    sales._CACHE["gooding"] = sale_pages("""<loc>https://www.goodingco.com/auction/amelia-island-auction-2025</loc>
        <loc>https://www.goodingco.com/auction/realized/amelia-island-auction-2025</loc>
        <loc>https://www.goodingco.com/auction/london-auction-2024</loc>""")
    a = sales.describe(row("gooding", "https://www.goodingco.com/lot/x/", None, "Amelia Island Auctions 2025",
                           "Gooding Christie's", ends_at="2025-03-07T00:00:00+00:00", auction="Amelia Island Auctions 2025"))
    assert a["sale_url"] == "https://www.goodingco.com/auction/amelia-island-auction-2025" and a["country"] == "United States"
    b = sales.describe(row("gooding", "https://www.goodingco.com/lot/y/", None, "London Auction",
                           "Gooding Christie's", ends_at="2024-09-04T00:00:00+00:00", auction="London Auction"))
    assert b["sale"] == "London Auction 2024" and b["country"] == "United Kingdom"
    assert b["sale_url"] == "https://www.goodingco.com/auction/london-auction-2024"


def test_mecum_and_online_auctions():
    m = row("mecum", "/lots/1182791/2017-ferrari-laferrari-aperta/", None, "Kissimmee 2027", "Mecum", auction="Kissimmee 2027")
    d = sales.describe(m)
    assert d["sale"] == "Mecum Kissimmee 2027" and d["sale_url"] == "https://www.mecum.com/auctions/kissimmee-2027/"
    assert d["country"] == "United States"
    assert sales.lot_url(m) == "https://www.mecum.com/lots/1182791/2017-ferrari-laferrari-aperta/"
    c = sales.describe(row("carsandbids", "https://carsandbids.com/auctions/x", None, "Clinton, WA 98236", "Cars & Bids"))
    assert c["sale"] == "Cars & Bids online auction" and c["country"] == "United States" and c["online"]


def test_country_names():
    assert sales.country_name("AE") == "United Arab Emirates"
    assert sales.country_name(None, "Knokke-Heist, Belgium") == "Belgium"
    assert sales.country_name(None, "Vancouver, BC") == "Canada"
    assert sales.country_name(None, "Kissimmee 2027") is None


def test_rm_archive_sales_are_not_dated_today():
    assert clean.rm_code_year("https://rmsothebys.com/auctions/mo06/lots/r35-x/") == 2006
    assert clean.rm_code_year("https://rmsothebys.com/auctions/lf26/lots/r0001-x/") == 2026
    assert clean.rm_code_year("https://www.bonhams.com/auction/1/") is None
