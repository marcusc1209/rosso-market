"""Site independent extraction helpers.

Three layers, tried in this order by most adapters:
  1. structured data the site publishes: JSON-LD (schema.org Car / Vehicle /
     Product / Offer), Next.js __NEXT_DATA__, `var x = {...}` blobs, and the
     JSON feeds a page downloads (captured by fetch.Browser.sniff)
  2. map_generic: turns any vehicle shaped dict into a RawRecord, whatever the
     site happens to call its fields
  3. llm_extract: optional last resort that asks Claude to read a page and
     return the fields, used only when 1 and 2 find nothing
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Iterator, Optional

from bs4 import BeautifulSoup

from .records import RawRecord
from .normalize import parse_price, parse_mileage, extract_year

log = logging.getLogger("extract")

# ---------------------------------------------------------------- embedded data


def json_ld(html: str) -> list[dict]:
    out = []
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                if "@graph" in d:
                    stack.extend(d["@graph"] if isinstance(d["@graph"], list) else [d["@graph"]])
                t = d.get("@type")
                types = t if isinstance(t, list) else [t]
                if any(x in ("Car", "Vehicle", "Product", "IndividualProduct", "MotorizedBicycle") for x in types):
                    out.append(d)
                if "itemListElement" in d:
                    for el in d["itemListElement"] or []:
                        stack.append(el.get("item", el) if isinstance(el, dict) else el)
    return out


def next_data(html: str) -> Optional[dict]:
    m = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
    return None


def _balanced(text: str, start: int) -> Optional[str]:
    """Return the JSON object or array that starts at text[start]."""
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def js_vars(html: str, name_pattern: str = r"[A-Za-z_$][\w$]*") -> dict[str, object]:
    """Find `var name = {...};` or `window.name = [...]` assignments and parse them as JSON."""
    out = {}
    for m in re.finditer(r"(?:var|let|const|window\.)\s*(" + name_pattern + r")\s*=\s*([\{\[])", html):
        blob = _balanced(html, m.start(2))
        if not blob or len(blob) < 20:
            continue
        try:
            out[m.group(1)] = json.loads(blob)
        except json.JSONDecodeError:
            continue
    return out


# ---------------------------------------------------------------- generic mapping

ALIASES = {
    "title": ["title", "name", "heading", "listing_title", "lotTitle", "lot_title", "displayName", "display_name", "vehicleTitle", "headline"],
    "url": ["url", "link", "listing_url", "listingUrl", "vdp_url", "vdpUrl", "permalink", "href", "detailUrl", "lotUrl", "canonical_url"],
    "id": ["id", "listing_id", "listingId", "lot_id", "lotId", "auction_id", "auctionId", "stockNumber", "stock_number", "sku", "uuid"],
    "price": ["sold_price", "soldPrice", "sale_price", "salePrice", "sale_amount", "saleAmount", "hammer_price", "hammerPrice", "final_price", "finalPrice",
              "price", "askingPrice", "asking_price", "list_price", "listPrice", "current_bid", "currentBid", "high_bid",
              "highBid", "winning_bid", "amount", "internetPrice", "salesPrice"],
    "currency": ["currency", "priceCurrency", "currency_code", "currencyCode"],
    "year": ["year", "model_year", "modelYear", "vehicleModelDate", "productionDate"],
    "make": ["make", "brand", "manufacturer"],
    "model": ["model", "model_name", "modelName", "trim"],
    "vin": ["vin", "VIN", "vehicleIdentificationNumber", "chassis", "chassis_number", "chassisNumber", "chassisNo"],
    "mileage": ["mileage", "miles", "odometer", "mileageFromOdometer", "km", "kilometers"],
    "location": ["location", "city_state", "address", "dealer_location", "auction_location", "venue"],
    "status": ["status", "auction_status", "auctionStatus", "result", "sale_status"],
    "ends_at": ["ends_at", "end_date", "endDate", "end_time", "endTime", "timestamp_end", "auction_end", "closes_at", "closing_time", "date_end"],
    "est_low": ["estimate_low", "estimateLow", "low_estimate", "lowEstimate", "estimate_min", "estimateMin"],
    "est_high": ["estimate_high", "estimateHigh", "high_estimate", "highEstimate", "estimate_max", "estimateMax"],
    "image": ["image", "image_url", "imageUrl", "thumbnail", "thumbnail_url", "thumbnailUrl", "photo", "primaryImage", "main_photo"],
    "ext_color": ["exterior_color", "exteriorColor", "color", "colour", "base_ext_color"],
    "int_color": ["interior_color", "interiorColor", "base_int_color"],
    "transmission": ["transmission", "vehicleTransmission", "gearbox"],
    "no_reserve": ["no_reserve", "noreserve", "noReserve", "is_no_reserve"],
    "bids": ["bid_count", "bidCount", "bids", "num_bids", "number_of_bids"],
    "lot": ["lot_number", "lotNumber", "lot", "lot_no"],
    "dealer": ["dealer_name", "dealerName", "seller_name", "sellerName", "dealer", "seller"],
}


def pick(d: dict, field: str):
    for key in ALIASES[field]:
        if key in d and d[key] not in (None, "", [], {}):
            v = d[key]
            if isinstance(v, dict):
                # {"value": 12000, "unitCode": "SMI"} or {"name": "..."} or {"url": "..."}
                for k in ("value", "amount", "name", "url", "formatted", "text"):
                    if k in v and v[k] not in (None, ""):
                        return v[k]
                continue
            if isinstance(v, list):
                v = v[0]
                if isinstance(v, dict):
                    v = v.get("url") or v.get("src") or v.get("name")
            return v
    return None


def looks_like_vehicle(d: dict) -> bool:
    if not isinstance(d, dict):
        return False
    has_title = pick(d, "title") is not None or (pick(d, "make") is not None and pick(d, "model") is not None)
    has_signal = any(pick(d, f) is not None for f in ("price", "year", "vin", "mileage", "ends_at", "est_low"))
    return has_title and has_signal


def walk(obj, predicate=looks_like_vehicle, max_items: int = 50_000) -> Iterator[dict]:
    """Yield every nested dict that satisfies predicate (outermost match only)."""
    stack, n = [obj], 0
    while stack and n < max_items:
        o = stack.pop()
        if isinstance(o, dict):
            if predicate(o):
                n += 1
                yield o
                continue
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(reversed(o))


# Order matters: negative phrases ("not sold", "reserve not met") must be tested before "sold".
STATUS_WORDS = [
    ("withdrawn", "withdrawn"), ("cancel", "withdrawn"),
    ("reserve not met", "unsold"), ("rnm", "unsold"), ("not sold", "unsold"), ("unsold", "unsold"), ("bid to", "unsold"),
    ("no sale", "unsold"), ("passed", "unsold"), ("bid goes on", "unsold"),
    ("sold", "sold"), ("completed", "sold"),
    ("live", "live"), ("active", "live"), ("started", "live"), ("open", "live"), ("running", "live"),
    ("upcoming", "upcoming"), ("scheduled", "upcoming"), ("preview", "upcoming"), ("coming soon", "upcoming"),
]


def norm_status(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        return "live" if value else None
    s = re.sub(r"[_\-]+", " ", str(value).strip().lower())
    if s in ("closed", "ended", "finished"):
        return None            # closed alone does not say whether it sold
    for k, v in STATUS_WORDS:
        if re.search(r"\b" + k, s):
            return v
    return None


def map_generic(d: dict, source: str, kind: str, base_url: str = "") -> Optional[RawRecord]:
    title = pick(d, "title")
    make, model = pick(d, "make"), pick(d, "model")
    year = pick(d, "year")
    if not title and make and model:
        title = " ".join(str(x) for x in [year or "", make, model] if x)
    if not title:
        return None
    url = pick(d, "url") or ""
    if isinstance(url, str) and url.startswith("/") and base_url:
        url = base_url.rstrip("/") + url
    rid = pick(d, "id") or url or title
    price_val = pick(d, "price")
    offers = d.get("offers")
    if price_val is None and isinstance(offers, (dict, list)):
        o = offers[0] if isinstance(offers, list) and offers else offers
        if isinstance(o, dict):
            price_val = o.get("price") or o.get("lowPrice")
            d.setdefault("priceCurrency", o.get("priceCurrency"))
            if not url:
                url = o.get("url") or ""
    currency = (pick(d, "currency") or "").upper() or None
    price, cur = parse_price(price_val, currency or "USD")
    try:
        year = int(str(year)[:4]) if year else extract_year(title)
    except ValueError:
        year = extract_year(title)
    lo, _ = parse_price(pick(d, "est_low"), cur)
    hi, _ = parse_price(pick(d, "est_high"), cur)
    mileage_raw = pick(d, "mileage")
    unit = "km" if any(k in d for k in ("km", "kilometers")) or (isinstance(d.get("mileageFromOdometer"), dict)
                                                             and d["mileageFromOdometer"].get("unitCode") == "KMT") else "mi"
    nr = pick(d, "no_reserve")
    bids = pick(d, "bids")
    return RawRecord(
        source=source, kind=kind, source_id=str(rid), url=str(url), title=str(title).strip(),
        price=price, currency=cur, price_text=None if price_val is None else str(price_val),
        year=year, make=None if make is None else str(make), model_text=None if model is None else str(model),
        mileage=parse_mileage(mileage_raw, unit) if mileage_raw is not None else None,
        vin=None if pick(d, "vin") is None else str(pick(d, "vin")).strip(),
        exterior_color=_s(pick(d, "ext_color")), interior_color=_s(pick(d, "int_color")),
        transmission=_s(pick(d, "transmission")), location=_s(pick(d, "location")),
        status=norm_status(pick(d, "status")), ends_at=_s(pick(d, "ends_at")),
        estimate_low=lo, estimate_high=hi,
        no_reserve=None if nr is None else bool(nr),
        bid_count=int(bids) if isinstance(bids, (int, float)) or (isinstance(bids, str) and bids.isdigit()) else None,
        lot_number=_s(pick(d, "lot")), dealer_name=_s(pick(d, "dealer")), image_url=_s(pick(d, "image")),
    )


def _s(v):
    return None if v is None else str(v).strip() or None


# ---------------------------------------------------------------- LLM fallback

LLM_SCHEMA = """Return JSON: {"vehicles": [{"title": str, "url": str|null, "price": number|null, "currency": "USD"|"CAD"|"EUR"|"GBP"|null,
"year": int|null, "mileage": number|null, "mileage_unit": "mi"|"km", "vin": str|null, "status": "live"|"sold"|"unsold"|"upcoming"|null,
"ends_at": ISO8601|null, "estimate_low": number|null, "estimate_high": number|null, "location": str|null, "exterior_color": str|null}]}
Only include Ferrari automobiles. Use null for anything not stated on the page. Never guess a price."""


def llm_extract(page_text: str, source: str, kind: str, cfg: dict) -> list[RawRecord]:
    """Optional. Needs ANTHROPIC_API_KEY and llm_fallback.enabled: true in config."""
    if not cfg.get("enabled") or not os.environ.get("ANTHROPIC_API_KEY"):
        return []
    try:
        import anthropic
    except ImportError:
        log.warning("llm fallback enabled but the anthropic package is not installed")
        return []
    client = anthropic.Anthropic()
    text = re.sub(r"\s+", " ", page_text)[: int(cfg.get("max_chars", 60_000))]
    msg = client.messages.create(
        model=cfg.get("model", "claude-haiku-5-5"), max_tokens=4000,
        messages=[{"role": "user", "content": f"{LLM_SCHEMA}\n\nPAGE TEXT:\n{text}"}],
    )
    body = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", body, re.S)
    if not m:
        return []
    out = []
    for v in json.loads(m.group(0)).get("vehicles", []):
        r = map_generic(v, source, kind)
        if r:
            r.extra["extracted_by"] = "llm"
            out.append(r)
    return out


def page_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    return soup.get_text(" ", strip=True)
