"""Build the static site from site/index.html and the exported data, ready for any static host.

    python scripts/build_site.py                       ->  public/
    python scripts/build_site.py --url http://localhost:8768/ --site /tmp/site --out /tmp/pub

What the build adds to the single source page:

  * the styles and the script move into assets/app.<hash>.css and assets/app.<hash>.js, so a browser
    keeps them between visits and fetches them again only when they change
  * every model gets a real address, models/<id>/, with its own title, description, canonical link,
    Open Graph tags and structured data
  * the overview and every model page carry their figures and listings as plain HTML, so the page reads
    without JavaScript and a search engine sees the content. The script replaces that HTML when it starts
  * sitemap.xml, robots.txt and a 404 page that hands unknown addresses to the app
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
SITE_NAME = "Rosso Market"
TAGLINE = "Ferrari prices, auctions and fair values"
DESCRIPTION = ("Daily asking prices for every Ferrari for sale in the US and every hypercar for sale worldwide, "
               "with auction results from around the world and a fair value for each car.")
MAX_ROWS = 60                      # listings and results written into a model page's plain HTML

esc = lambda s: html.escape(str(s if s is not None else ""), quote=True)


def money(n) -> str:
    return "" if n is None else "${:,.0f}".format(n)


def pct(n) -> str:
    if n is None:
        return ""
    return f'{"up" if n > 0 else "down" if n < 0 else "no change,"} {abs(n):.1f}%'


def day(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso[:10])
    except ValueError:
        return ""
    return f"{d:%b} {d.day}, {d.year}"


def load(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def split_source(text: str) -> tuple[str, str, str, str]:
    """site/index.html is head tags, one <style>, the body markup, one <script>."""
    s0, s1 = text.index("<style>"), text.index("</style>")
    j0, j1 = text.rindex("<script>"), text.rindex("</script>")
    return text[:s0].strip(), text[s0 + 7:s1].strip(), text[s1 + 8:j0].strip(), text[j0 + 8:j1].strip()


def table(caption: str, head: list[str], rows: list[list[str]]) -> str:
    """rows hold ready HTML. A column whose header starts with '>' is right aligned."""
    right = [h.startswith(">") for h in head]
    th = "".join(f'<th{" class=r" if r else ""}>{esc(h.lstrip(">"))}</th>' for h, r in zip(head, right))
    body = "".join("<tr>" + "".join(('<td class="r num">' if r else "<td>") + f"{c}</td>" for c, r in zip(row, right)) + "</tr>"
                   for row in rows)
    return (f'<div class="tbl-wrap"><table class="sheet"><caption class="sr">{esc(caption)}</caption>'
            f"<thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>")


def menu_html(site: dict, by_id: dict) -> str:
    out = []
    for fam in site.get("families", []):
        links = "".join(
            f'<a href="models/{esc(m["id"])}/"><span>{esc(m["name"])}</span>'
            f'<span class="num">{(m.get("listings") or 0) + (m.get("at_auction") or 0) or ""}</span></a>'
            for m in (by_id.get(i) for i in fam["models"]) if m)
        out.append(f'<div><h4>{esc(fam["name"])}</h4>{links}</div>')
    return "".join(out)


def over(lot: dict, now: datetime) -> bool:
    """A lot whose end time has passed is no longer live, whatever the last run recorded."""
    try:
        return datetime.fromisoformat(lot["ends_at"].replace("Z", "+00:00")) < now
    except (KeyError, AttributeError, ValueError, TypeError):
        return False


NOSCRIPT = ('<noscript><p class="flat" style="margin:0 0 20px">Charts, search, the watchlist and valuations need JavaScript. '
            "The prices and listings on this page are complete without it.</p></noscript>")


def overview_html(site: dict, ov: dict, auc: dict, by_id: dict, now: datetime) -> str:
    brief, cov = ov.get("brief") or {}, site.get("coverage") or {}
    live = [l for l in auc.get("live", []) if not over(l, now)]
    parts = [f'<div class="page-head"><div class="wrap"><h1>{esc(brief.get("headline") or TAGLINE)}</h1>'
             f'<p class="flat" style="margin:12px 0 0;max-width:70ch">{esc(DESCRIPTION)} Data as of {day(site.get("as_of"))}.</p>'
             f'<div class="kpis"><div class="kpi"><b>{cov.get("for_sale", 0):,}</b><span>Ferraris for sale today</span></div>'
             f'<div class="kpi"><b>{len(live)}</b><span>Live auctions now</span></div>'
             f'<div class="kpi"><b>{cov.get("on_record", 0):,}</b><span>Cars on record</span></div>'
             f'<div class="kpi"><b>{cov.get("models", 0)}</b><span>Models followed</span></div></div></div></div>',
             '<section class="block"><div class="wrap">', NOSCRIPT]
    if brief.get("bullets"):
        parts.append("<h2>What changed in the last 7 days</h2><ul>"
                     + "".join(f'<li><b>{esc(b["label"])}.</b> {esc(b["text"])}</li>' for b in brief["bullets"]) + "</ul>")
    rows = []
    for fam in site.get("families", []):
        for m in (by_id.get(i) for i in fam["models"]):
            if not m:
                continue
            a = m.get("auction") or {}
            rows.append([f'<a href="models/{esc(m["id"])}/">Ferrari {esc(m["name"])}</a>', esc(fam["name"]), str(m.get("listings") or ""),
                         money(m.get("median")), pct(m.get("change_7d")) if m.get("listings") else "",
                         str(a.get("sold_12m") or ""), money(a.get("median_sold_12m"))])
    parts.append("<h2>Every model</h2>" + table(
        "Every Ferrari model we follow: cars for sale, median ask and auction results over 12 months",
        ["Model", "Family", ">For sale", ">Median ask", ">Ask change, 7 days", ">Sold, 12 months", ">Median sold, 12 months"], rows))
    if live:
        live.sort(key=lambda l: l.get("ends_at") or "")
        parts.append("<h2>Live auctions</h2>" + table("Ferrari lots open for bidding now", ["Lot", "Auction", ">Current bid", "Ends (UTC)"], [
            [f'<a href="{esc(l["url"])}" rel="noopener">{esc(l["title"])}</a>', esc(l.get("auction_house")),
             money(l.get("price_usd")) or "No bids yet", esc((l.get("ends_at") or "")[:16].replace("T", " "))] for l in live[:MAX_ROWS]]))
    if ov.get("news"):
        parts.append("<h2>News</h2><ul>" + "".join(
            f'<li><a href="{esc(n["url"])}" rel="noopener">{esc(n["title"])}</a>, {esc(n.get("outlet"))}, {day(n.get("published"))}</li>'
            for n in ov["news"][:12]) + "</ul>")
    parts.append("</div></section>")
    return "".join(parts)


def model_summary(m: dict) -> str:
    a = m.get("auction") or {}
    bits = []
    if m.get("listings"):
        bits.append(f'{m["listings"]} for sale, median ask {money(m.get("median"))}')
    if a.get("sold_12m"):
        bits.append(f'{a["sold_12m"]} sold at auction in 12 months, median {money(a.get("median_sold_12m"))}')
    return ". ".join(bits) or "Auction results and asking prices"


def line_html(line: dict, by_id: dict) -> str:
    """A classic model line: its versions and their figures."""
    rows = []
    for i in line["models"]:
        m = by_id.get(i)
        if not m:
            continue
        a = m.get("auction") or {}
        rows.append([f'<a href="models/{esc(i)}/">Ferrari {esc(m["name"])}</a>', str(m.get("listings") or ""), money(m.get("median")),
                     str(a.get("sold_12m") or ""), money(a.get("median_sold_12m"))])
    return (f'<div class="page-head"><div class="wrap"><div class="crumbs"><a href="./">Overview</a> / {esc(line["family"])}</div>'
            f'<h1>Ferrari {esc(line["name"])}</h1><p class="flat" style="margin:12px 0 0">{line["years"][0]} to {line["years"][1]}.</p></div></div>'
            '<section class="block"><div class="wrap">' + NOSCRIPT + "<h2>Versions</h2>" + table(
                f'Ferrari {line["name"]} versions', ["Version", ">For sale", ">Median ask", ">Sold, 12 months", ">Median sold, 12 months"], rows)
            + "</div></section>")


def model_html(m: dict, d: dict, family: str, now: datetime) -> str:
    a = m.get("auction") or {}
    kpis = []
    if m.get("listings"):
        kpis += [(money(m.get("median")), "Median ask"), (pct(m.get("change_7d")), "Asks, 7 days"), (m["listings"], "For sale now")]
    if a.get("sold_12m"):
        kpis += [(money(a.get("median_sold_12m")), "Median sold, 12 months"), (a["sold_12m"], "Sold, 12 months")]
        if a.get("sell_through_12m") is not None:
            kpis.append((f'{a["sell_through_12m"]}%', "Sell through, 12 months"))
    parts = [f'<div class="page-head"><div class="wrap"><div class="crumbs"><a href="./">Overview</a> / {esc(family)}</div>'
             f'<h1>Ferrari {esc(m["name"])}</h1><p class="flat" style="margin:12px 0 0;max-width:70ch">'
             f'Asking prices, auction results and fair values for the Ferrari {esc(m["name"])}.</p><div class="kpis">'
             + "".join(f'<div class="kpi"><b>{esc(v)}</b><span>{esc(l)}</span></div>' for v, l in kpis)
             + '</div></div></div><section class="block"><div class="wrap">', NOSCRIPT]
    fv = m.get("fv") or {}
    if fv.get("shown") and fv.get("ask_gap") is not None and abs(fv["ask_gap"]) >= 3:
        parts.append(f'<p style="max-width:70ch">Fair values rest on {fv.get("n")} auction sales in 3 years. Dealers ask a median '
                     f'{abs(round(fv["ask_gap"]))}% {"above" if fv["ask_gap"] >= 0 else "below"} fair value for this model.</p>')
    listings = sorted(d.get("listings") or [], key=lambda l: l.get("price_usd") or 1e12)
    if listings:
        parts.append(f'<h2>{len(listings)} for sale</h2>' + table(f'Ferrari {m["name"]} cars for sale', [
            "Car", ">Mileage", "Colour", "Seller", "Location", ">Asking", ">Fair value"], [
            [f'<a href="{esc(l.get("url"))}" rel="noopener">{esc(l.get("title"))}</a>',
             f'{l["mileage_mi"]:,.0f} mi' if l.get("mileage_mi") is not None else "", esc(l.get("exterior_color")),
             esc(l.get("dealer_name") or l.get("marketplace")), esc(l.get("location")), money(l.get("price_usd")) or "On request",
             money((l.get("fair") or {}).get("value"))] for l in listings[:MAX_ROWS]]))
    lots = [l for l in d.get("upcoming") or [] if not over(l, now)]
    if lots:
        parts.append("<h2>At auction</h2>" + table(f'Ferrari {m["name"]} lots live or coming up', ["Lot", "Auction", ">Bid or estimate", "Ends (UTC)"], [
            [f'<a href="{esc(l.get("url"))}" rel="noopener">{esc(l.get("title"))}</a>', esc(l.get("auction_house")),
             money(l.get("price_usd")) or (f'{money(l["estimate_low_usd"])} to {money(l["estimate_high_usd"])}' if l.get("estimate_low_usd") and l.get("estimate_high_usd") else ""),
             esc((l.get("ends_at") or "")[:16].replace("T", " "))] for l in lots[:MAX_ROWS]]))
    results = [r for r in d.get("results") or [] if r.get("status") in ("sold", "unsold")]
    if results:
        results.sort(key=lambda r: r.get("ends_at") or "", reverse=True)
        parts.append("<h2>Auction results</h2>" + table(f'Ferrari {m["name"]} auction results, newest first', ["Lot", "Auction", "Date", ">Mileage", ">Result"], [
            [f'<a href="{esc(r.get("url"))}" rel="noopener">{esc(r.get("title"))}</a>', esc(r.get("auction_house")), day(r.get("ends_at")),
             f'{r["mileage_mi"]:,.0f} mi' if r.get("mileage_mi") is not None else "",
             (money(r.get("price_usd")) or "Price not published") if r["status"] == "sold"
             else f'Reserve not met{", high bid " + money(r["price_usd"]) if r.get("price_usd") else ""}'] for r in results[:MAX_ROWS]]))
    parts.append("</div></section>")
    return "".join(parts)


def json_ld(obj) -> str:
    return '<script type="application/ld+json">' + json.dumps(obj, separators=(",", ":")).replace("</", "<\\/") + "</script>"


def document(*, head: str, css: str, js: str, body: str, prerender: str, menu: str, base: str, url: str, title: str,
             description: str, og_image: str, ld: list, index: bool = True) -> str:
    body = body.replace('<div class="wrap loading">Loading market data</div>',
                        f'<div class="wrap loading js-only">Loading market data</div><div id="prerender">{prerender}</div>', 1)
    body = body.replace('id="menu-grid"></div>', f'id="menu-grid">{menu}</div>', 1)
    # the source page's own title and description give way to this page's
    keep = "\n".join(l for l in head.splitlines() if not l.startswith(("<title>", '<meta name="description"')))
    meta = [f"<title>{esc(title)}</title>", f'<meta name="description" content="{esc(description)}">',
            f'<link rel="canonical" href="{esc(url)}">' if index else '<meta name="robots" content="noindex">',
            '<meta name="theme-color" content="#111316">',
            '<link rel="apple-touch-icon" href="apple-touch-icon.png">',
            f'<meta property="og:site_name" content="{SITE_NAME}">', '<meta property="og:type" content="website">',
            f'<meta property="og:title" content="{esc(title)}">', f'<meta property="og:description" content="{esc(description)}">',
            f'<meta property="og:url" content="{esc(url)}">', f'<meta property="og:image" content="{esc(og_image)}">',
            '<meta property="og:image:width" content="1200">', '<meta property="og:image:height" content="630">',
            '<meta name="twitter:card" content="summary_large_image">']
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
            f'<base href="{esc(base)}">\n' + "\n".join(meta) + "\n" + keep + "\n"
            '<script>document.documentElement.className="js"</script>\n'
            f'<link rel="stylesheet" href="{css}">\n<script src="{js}" defer></script>\n'
            + "\n".join(json_ld(x) for x in ld) + "\n</head>\n<body>\n" + body + "\n</body>\n</html>\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=str(ROOT / "config" / "sources.yaml"))
    ap.add_argument("--site", default=str(ROOT / "site"), help="folder holding index.html and data/")
    ap.add_argument("--out", default=str(ROOT / "public"))
    ap.add_argument("--url", help="the address the site is served from (default: settings.site_url)")
    args = ap.parse_args()

    settings = (yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}).get("settings", {})
    site_url = (args.url or settings.get("site_url") or "http://localhost:8000/").rstrip("/") + "/"
    base = urlparse(site_url).path or "/"
    src, data_src, out = ROOT / "site", Path(args.site), Path(args.out)

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    if (data_src / "data").exists():
        shutil.copytree(data_src / "data", out / "data")
    for extra in ("og.png", "favicon.svg", "apple-touch-icon.png"):
        if (src / extra).exists():
            shutil.copy2(src / extra, out / extra)

    head, css, body, js = split_source((src / "index.html").read_text(encoding="utf-8"))
    css += "\n.js #prerender, html:not(.js) .js-only { display: none; }\n#prerender h2 { margin: 36px 0 14px; }\n#prerender ul { padding-left: 20px; max-width: 80ch; }\n"
    (out / "assets").mkdir()
    names = {}
    for ext, text in (("css", css), ("js", js)):
        names[ext] = f"assets/app.{hashlib.sha256(text.encode()).hexdigest()[:10]}.{ext}"
        (out / names[ext]).write_text(text + "\n", encoding="utf-8")

    site = load(out / "data" / "site.json", {}) or {}
    ov = load(out / "data" / "overview.json", {}) or {}
    auc = load(out / "data" / "auctions.json", {}) or {}
    by_id = {m["id"]: m for m in site.get("models", [])}
    family = {i: f["name"] for f in site.get("families", []) for i in f["models"]}
    menu = menu_html(site, by_id)
    now = datetime.now(timezone.utc)
    og = site_url + "og.png"
    common = dict(head=head, css=names["css"], js=names["js"], body=body, menu=menu, base=base, og_image=og)

    home_ld = [{"@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME, "url": site_url, "description": DESCRIPTION},
               {"@context": "https://schema.org", "@type": "ItemList", "name": "Ferrari models", "itemListElement": [
                   {"@type": "ListItem", "position": i + 1, "name": f'Ferrari {m["name"]}', "url": f'{site_url}models/{m["id"]}/'}
                   for i, m in enumerate(site.get("models", []))]}]
    (out / "index.html").write_text(document(
        **common, prerender=overview_html(site, ov, auc, by_id, now), url=site_url, title=f"{SITE_NAME}: {TAGLINE}",
        description=DESCRIPTION, ld=home_ld), encoding="utf-8")

    pages = [site_url]
    for m in site.get("models", []):
        d = load(out / "data" / "models" / f'{m["id"]}.json')
        if d is None:
            continue
        url = f'{site_url}models/{m["id"]}/'
        title = f'Ferrari {m["name"]} prices: {model_summary(m).split(". ")[0]} | {SITE_NAME}'
        desc = f'Ferrari {m["name"]}: {model_summary(m)}. Every car for sale, every auction result and a fair value for each, updated daily.'
        ld = [{"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": SITE_NAME, "item": site_url},
            {"@type": "ListItem", "position": 2, "name": f'Ferrari {m["name"]}', "item": url}]}]
        if m.get("listings") and m.get("low") and m.get("high"):
            ld.append({"@context": "https://schema.org", "@type": "Product", "name": f'Ferrari {m["name"]}', "category": "Cars",
                       "brand": {"@type": "Brand", "name": "Ferrari"}, "description": desc,
                       "offers": {"@type": "AggregateOffer", "priceCurrency": "USD", "lowPrice": round(m["low"]),
                                  "highPrice": round(m["high"]), "offerCount": m["listings"], "itemCondition": "https://schema.org/UsedCondition"}})
        page = out / "models" / m["id"]
        page.mkdir(parents=True, exist_ok=True)
        (page / "index.html").write_text(document(
            **common, prerender=model_html(m, d, family.get(m["id"], m.get("family") or ""), now), url=url, title=title, description=desc, ld=ld),
            encoding="utf-8")
        pages.append(url)

    for line in site.get("lines", []):
        url = f'{site_url}models/{line["id"]}/'
        n = sum((by_id.get(i) or {}).get("listings") or 0 for i in line["models"])
        title = f'Ferrari {line["name"]} prices, every version | {SITE_NAME}'
        desc = (f'Every version of the Ferrari {line["name"]} ({line["years"][0]} to {line["years"][1]}): '
                f'{n} for sale now, auction results and asking prices for each, updated daily.')
        ld = [{"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": SITE_NAME, "item": site_url},
            {"@type": "ListItem", "position": 2, "name": f'Ferrari {line["name"]}', "item": url}]}]
        page = out / "models" / line["id"]
        page.mkdir(parents=True, exist_ok=True)
        (page / "index.html").write_text(document(**common, prerender=line_html(line, by_id), url=url, title=title,
                                                  description=desc, ld=ld), encoding="utf-8")
        pages.append(url)

    # model pages that moved when the classics were regrouped forward to their new address
    for old, new in (site.get("aliases") or {}).items():
        if (out / "models" / old).exists():
            continue
        target = f"{site_url}models/{new}/"
        page = out / "models" / old
        page.mkdir(parents=True, exist_ok=True)
        (page / "index.html").write_text(
            f'<!doctype html><meta charset="utf-8"><title>Moved | {SITE_NAME}</title><link rel="canonical" href="{esc(target)}">'
            f'<meta http-equiv="refresh" content="0; url={esc(target)}"><meta name="robots" content="noindex">'
            f'<p><a href="{esc(target)}">This page has moved</a></p>', encoding="utf-8")

    # unknown addresses still open the app, which shows its own "not found" (or a car, for an old link)
    (out / "404.html").write_text(document(
        **common, prerender='<div class="wrap" style="padding:60px 0"><h1>Page not found</h1><p><a href="./">Back to the overview</a></p></div>',
        url=site_url, title=f"Page not found | {SITE_NAME}", description=DESCRIPTION, ld=[], index=False), encoding="utf-8")

    stamp = site.get("as_of") or f"{now:%Y-%m-%d}"
    (out / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"<url><loc>{esc(u)}</loc><lastmod>{stamp}</lastmod></url>\n" for u in pages) + "</urlset>\n", encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {site_url}sitemap.xml\n", encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"built {out}: {len(pages)} pages, {names['css']}, {names['js']}, served from {site_url}")


if __name__ == "__main__":
    main()
