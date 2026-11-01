"""Ferrari headlines from RSS feeds. Stores headline, link, date and outlet only,
never article text, and the site always links out to the original."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from typing import Iterator


def parse_feed(xml_text: str, outlet: str) -> list[dict]:
    out = []
    root = ET.fromstring(xml_text)
    ns = {"a": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/"}
    items = root.findall(".//item") or root.findall(".//a:entry", ns)
    for it in items:
        title = (it.findtext("title") or it.findtext("a:title", namespaces=ns) or "").strip()
        link = it.findtext("link") or ""
        if not link:
            l = it.find("a:link", ns)
            link = l.get("href") if l is not None else ""
        when = it.findtext("pubDate") or it.findtext("a:updated", namespaces=ns) or it.findtext("a:published", namespaces=ns)
        try:
            published = parsedate_to_datetime(when).date().isoformat() if when and "," in when else (when or "")[:10]
        except (TypeError, ValueError):
            published = ""
        img = None
        m = it.find("media:content", ns) or it.find("media:thumbnail", ns)
        if m is not None:
            img = m.get("url")
        out.append({"title": re.sub(r"\s+", " ", title), "url": link.strip(), "published": published, "outlet": outlet, "image": img})
    return out


def fetch_news(fetcher, feeds: list[dict], keyword: str = "ferrari") -> Iterator[dict]:
    for f in feeds:
        try:
            xml_text = fetcher.get(f["url"]).text
        except Exception:
            continue
        for item in parse_feed(xml_text, f.get("name", "")):
            if keyword in item["title"].lower():
                yield item
