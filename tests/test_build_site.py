"""The static build: cached assets, a real page per model, content that reads without JavaScript."""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _site(tmp: Path) -> Path:
    data = tmp / "site" / "data"
    (data / "models").mkdir(parents=True)
    now = datetime.now(timezone.utc)
    model = {"id": "f430", "name": "F430", "family": "Mid engine V8", "listings": 2, "median": 200000, "low": 150000, "high": 250000,
             "change_7d": -1.5, "at_auction": 1, "auction": {"sold_12m": 9, "median_sold_12m": 180000, "sell_through_12m": 80},
             "fv": {"n": 40, "miss": 9.0, "conf": "high", "shown": True, "ask_gap": 12.0}}
    (data / "site.json").write_text(json.dumps({
        "as_of": "2026-11-13", "models": [model], "families": [{"name": "Mid engine V8", "models": ["f430"]}],
        "coverage": {"for_sale": 2, "on_record": 30, "models": 1}}))
    (data / "overview.json").write_text(json.dumps({
        "brief": {"headline": "F430 asks soften", "bullets": [{"label": "Biggest drop, 7 days", "text": "F430 down 1.5%."}]},
        "news": [{"title": "A <b>story</b>", "url": "https://example.com/a", "outlet": "Outlet", "published": "2026-11-12"}]}))
    lot = lambda title, hours: {"title": title, "url": "https://example.com/" + title.replace(" ", "-"), "auction_house": "Bring a Trailer",
                                "price_usd": 100000, "ends_at": (now + timedelta(hours=hours)).isoformat()}
    (data / "auctions.json").write_text(json.dumps({"live": [lot("Open lot", 5), lot("Closed lot", -5)]}))
    (data / "models" / "f430.json").write_text(json.dumps({
        "listings": [{"title": "2007 Ferrari F430", "url": "https://example.com/1", "price_usd": 150000, "mileage_mi": 12000,
                      "exterior_color": "Red", "dealer_name": "A Dealer", "location": "Miami, FL", "fair": {"value": 160000}}],
        "upcoming": [lot("Model open lot", 3), lot("Model closed lot", -3)],
        "results": [{"title": "2006 Ferrari F430", "url": "https://example.com/2", "auction_house": "Mecum", "status": "sold",
                     "price_usd": 170000, "ends_at": "2026-10-01T00:00:00+00:00"},
                    {"title": "2005 Ferrari F430 hidden", "url": "https://example.com/3", "auction_house": "Sotheby's Motorsport",
                     "status": "sold", "price_usd": None, "ends_at": "2026-11-12T00:00:00+00:00"}]}))
    return tmp / "site"


def _build(tmp: Path) -> Path:
    out = tmp / "public"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_site.py"), "--site", str(_site(tmp)), "--out", str(out),
                    "--url", "https://example.org/market/"], check=True, capture_output=True)
    return out


def test_assets_are_separate_cached_files(tmp_path):
    out = _build(tmp_path)
    home = (out / "index.html").read_text()
    css, js = list((out / "assets").glob("app.*.css")), list((out / "assets").glob("app.*.js"))
    assert len(css) == 1 and len(js) == 1
    assert f'href="assets/{css[0].name}"' in home and f'src="assets/{js[0].name}"' in home
    assert "<style>" not in home                      # nothing inline but the one line that marks JavaScript as on
    assert '<base href="/market/">' in home
    assert "viewport-fit-sides" not in home


def test_each_model_has_its_own_page(tmp_path):
    out = _build(tmp_path)
    page = (out / "models" / "f430" / "index.html").read_text()
    assert "<title>Ferrari F430 prices: 2 for sale, median ask $200,000 | Rosso Market</title>" in page
    assert '<link rel="canonical" href="https://example.org/market/models/f430/">' in page
    assert '<meta property="og:image" content="https://example.org/market/og.png">' in page
    assert '"@type":"AggregateOffer"' in page and '"offerCount":2' in page
    assert "https://example.org/market/models/f430/" in (out / "sitemap.xml").read_text()
    assert (out / "404.html").exists() and "noindex" in (out / "404.html").read_text()


def test_content_reads_without_javascript(tmp_path):
    out = _build(tmp_path)
    home = (out / "index.html").read_text()
    page = (out / "models" / "f430" / "index.html").read_text()
    assert "F430 asks soften" in home and 'href="models/f430/"' in home
    assert "A &lt;b&gt;story&lt;/b&gt;" in home          # text from the feeds is escaped
    assert "2007 Ferrari F430" in page and "$150,000" in page and "12,000 mi" in page
    assert "Price not published" in page
    assert "12% above fair value" in page


def test_ended_auctions_are_left_out_of_the_built_pages(tmp_path):
    out = _build(tmp_path)
    home = (out / "index.html").read_text()
    page = (out / "models" / "f430" / "index.html").read_text()
    assert "Open lot" in home and "Closed lot" not in home
    assert "Model open lot" in page and "Model closed lot" not in page
