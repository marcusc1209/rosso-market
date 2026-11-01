"""Config driven adapters that cover most sites without site specific code.

BrowserJson    JavaScript heavy sites (Cars & Bids, RM Sotheby's, SOMO,
               duPont Registry Live, Mecum, Broad Arrow, Gooding, Bonhams,
               Cars.com, duPont Registry, Ferrari Approved ...). Opens each
               start URL in headless Chromium, captures the JSON the page
               loads, and maps every vehicle shaped object it finds.
StaticPage     Plain HTTP for sites that publish JSON-LD, __NEXT_DATA__ or
               embedded JSON. Used for dealer inventory pages, which are
               mostly built on a handful of platforms that all emit JSON-LD.

Both fall back to the LLM extractor when structured data comes back empty.
Adding a new auction house is a few lines of YAML in config/sources.yaml.
"""
from __future__ import annotations

from typing import Iterator
from urllib.parse import urljoin

from .base import Source
from ..extract import json_ld, next_data, js_vars, walk, map_generic, llm_extract, page_text
from ..records import RawRecord, AUCTION
from .. import taxonomy


def _finish(rec: RawRecord, cfg: dict, page_url: str) -> RawRecord:
    if not rec.url and cfg.get("url_template"):
        rec.url = cfg["url_template"].format(id=rec.source_id)
    if rec.url and not rec.url.startswith("http"):
        rec.url = urljoin(cfg.get("base_url") or page_url, rec.url)
    if rec.kind == AUCTION:
        rec.auction_house = rec.auction_house or cfg.get("label")
        rec.seller_type = "auction"
        if rec.status is None:
            rec.status = cfg.get("default_status")
    else:
        rec.seller_type = rec.seller_type or cfg.get("seller_type", "dealer")
        rec.marketplace = rec.marketplace or cfg.get("label")
        rec.certified = rec.certified or bool(cfg.get("certified"))
    if not rec.currency:
        rec.currency = cfg.get("currency", "USD")
    return rec


def _from_structured(blobs, cfg, source, kind, page_url) -> list[RawRecord]:
    out = []
    for blob in blobs:
        for d in walk(blob):
            rec = map_generic(d, source, kind, cfg.get("base_url", ""))
            if rec and taxonomy.is_ferrari(rec.title, rec.make):
                out.append(_finish(rec, cfg, page_url))
    return out


class BrowserJson(Source):
    def fetch(self) -> Iterator[RawRecord]:
        needles = [n.lower() for n in self.cfg.get("json_url_contains", [])]
        flt = (lambda u: any(n in u.lower() for n in needles)) if needles else (lambda u: True)
        seen = set()
        for url in self.cfg["start_urls"]:
            payloads, html = self.ctx.browser.sniff(
                url, flt, scrolls=int(self.cfg.get("scrolls", 4)), wait_ms=int(self.cfg.get("wait_ms", 2500)),
                max_pages=int(self.cfg.get("pages", 1)), next_selector=self.cfg.get("next_selector"),
                click_text=self.cfg.get("click_text"))
            recs = _from_structured([p["body"] for p in payloads], self.cfg, self.name, self.kind, url)
            if not recs:
                nd = next_data(html)
                recs = _from_structured([x for x in [nd, *json_ld(html), *js_vars(html).values()] if x],
                                        self.cfg, self.name, self.kind, url)
            if not recs:
                recs = [_finish(r, self.cfg, url) for r in llm_extract(page_text(html), self.name, self.kind, self.ctx.llm_cfg)]
            self.log.info("%s: %d payloads, %d records", url, len(payloads), len(recs))
            for r in recs:
                if r.source_id not in seen:
                    seen.add(r.source_id)
                    yield r


class StaticPage(Source):
    def fetch(self) -> Iterator[RawRecord]:
        seen = set()
        for start in self.cfg["start_urls"]:
            pages = int(self.cfg.get("pages", 1))
            for p in range(1, pages + 1):
                url = start.format(page=p) if "{page}" in start else start
                html = self.ctx.fetcher.get(url).text
                recs = _from_structured([x for x in [next_data(html), *json_ld(html), *js_vars(html).values()] if x],
                                        self.cfg, self.name, self.kind, url)
                if not recs:
                    recs = [_finish(r, self.cfg, url) for r in llm_extract(page_text(html), self.name, self.kind, self.ctx.llm_cfg)]
                new = [r for r in recs if r.source_id not in seen]
                for r in new:
                    seen.add(r.source_id)
                    yield r
                if not new or "{page}" not in start:
                    break


class StaticThenBrowser(Source):
    """Try plain HTTP first (cheap), use the browser only if that finds nothing."""

    def fetch(self) -> Iterator[RawRecord]:
        got = False
        try:
            for r in StaticPage(self.name, self.cfg, self.ctx).fetch():
                got = True
                yield r
        except Exception as e:  # blocked, 403, parse failure
            self.log.info("static fetch failed (%s), switching to browser", e)
        if not got:
            yield from BrowserJson(self.name, self.cfg, self.ctx).fetch()
