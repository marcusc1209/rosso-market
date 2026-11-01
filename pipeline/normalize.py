"""Turn messy source values into clean, comparable numbers."""
from __future__ import annotations

import re
from typing import Optional, Tuple

from . import taxonomy
from .records import RawRecord, Normalized, AUCTION

# ---------------------------------------------------------------- prices

_CURRENCY_HINTS = [
    (re.compile(r"\bca\s?\$|\bc\$|\bcad\b", re.I), "CAD"),
    (re.compile(r"\bus\s?\$|\busd\b", re.I), "USD"),
    (re.compile(r"£|\bgbp(?![a-z])", re.I), "GBP"),
    (re.compile(r"€|\beur(?![a-z])", re.I), "EUR"),
    (re.compile(r"\baed(?![a-z])", re.I), "AED"),
    (re.compile(r"\bchf(?![a-z])", re.I), "CHF"),
    (re.compile(r"\ba\$|\baud\b", re.I), "AUD"),
    (re.compile(r"¥|\bjpy\b", re.I), "JPY"),
    (re.compile(r"\$"), "USD"),
]
_NUM = re.compile(r"(\d[\d,\.\s]*\d|\d)\s*(k|m|mm|million|thousand)?\b", re.I)


def detect_currency(text: str, default: str = "USD") -> str:
    for pattern, code in _CURRENCY_HINTS:
        if pattern.search(text or ""):
            return code
    return default


def parse_price(value, default_currency: str = "USD") -> Tuple[Optional[float], str]:
    """'US$334,900' -> (334900.0, 'USD'); '$1.2M' -> (1200000.0, 'USD'); 'CA$ 299,000' -> (299000.0, 'CAD')."""
    if value is None:
        return None, default_currency
    if isinstance(value, (int, float)):
        return (float(value) if value > 0 else None), default_currency
    text = str(value).strip()
    if not text:
        return None, default_currency
    low = text.lower()
    if any(w in low for w in ("call", "inquire", "request", "contact", "n/a", "tbd", "poa")):
        return None, detect_currency(text, default_currency)
    currency = detect_currency(text, default_currency)
    m = _NUM.search(text)
    if not m:
        return None, currency
    digits, suffix = m.group(1), (m.group(2) or "").lower()
    digits = digits.replace(" ", "")
    # European style 1.234.567 or 1.234,56
    if digits.count(".") > 1 or (digits.count(".") == 1 and digits.count(",") == 1 and digits.index(".") < digits.index(",")):
        digits = digits.replace(".", "").replace(",", ".")
    else:
        digits = digits.replace(",", "")
    try:
        amount = float(digits)
    except ValueError:
        return None, currency
    if suffix in ("k", "thousand"):
        amount *= 1_000
    elif suffix in ("m", "mm", "million"):
        amount *= 1_000_000
    return (amount if amount > 0 else None), currency


def to_usd(amount: Optional[float], currency: str, fx: dict) -> Optional[float]:
    if amount is None:
        return None
    rate = fx.get((currency or "USD").upper())
    if rate is None:
        return None
    return round(amount * rate, 2)


# ---------------------------------------------------------------- mileage

_MILES = re.compile(r"(\d[\d,\.]*)\s*(k(?!m))?\s*(mi|miles|mile|km|kms|kilometers|kilometres)?", re.I)


def parse_mileage(value, unit_hint: str = "mi") -> Optional[float]:
    """Return miles. Handles '13,758 mi', '5k Miles', '12,000 km', 'TMU' (None)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        miles = float(value)
        return round(miles * 0.621371, 0) if unit_hint == "km" else miles
    text = str(value).strip().lower()
    if not text or "tmu" in text or "exempt" in text or "unknown" in text:
        return None
    m = _MILES.search(text)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    if m.group(2):
        n *= 1000
    unit = (m.group(3) or unit_hint).lower()
    if unit.startswith("k"):
        n *= 0.621371
    return round(n, 0)


_TITLE_MILES = re.compile(r"\b(\d[\d,\.]*)\s*(k)?[\s-]*(mile|kilometer|kilometre|km)s?\b", re.I)


def mileage_from_title(title: str) -> Optional[float]:
    """Auction titles often carry mileage: '20k-Mile 2003 Ferrari 360 Spider', '1,200-Mile 2017 F12'."""
    m = _TITLE_MILES.search(title or "")
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    if m.group(2):
        n *= 1000
    if m.group(3).lower().startswith("k"):
        n *= 0.621371
    return round(n, 0)


# ---------------------------------------------------------------- VIN / chassis

_VIN = re.compile(r"\b([A-HJ-NPR-Z0-9]{17})\b")
_TRANSLIT = {**{str(d): d for d in range(10)},
             **dict(zip("ABCDEFGH", range(1, 9))), **dict(zip("JKLMN", range(1, 6))), "P": 7, "R": 9,
             **dict(zip("STUVWXYZ", range(2, 10)))}
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def vin_check_digit_ok(vin: str) -> bool:
    """North American VINs carry a check digit in position 9. European market cars often do not."""
    try:
        total = sum(_TRANSLIT[c] * w for c, w in zip(vin, _WEIGHTS))
    except KeyError:
        return False
    check = total % 11
    return vin[8] == ("X" if check == 10 else str(check))


def extract_vin(*texts) -> Tuple[Optional[str], bool]:
    for t in texts:
        if not t:
            continue
        for m in _VIN.finditer(str(t).upper()):
            vin = m.group(1)
            if vin.startswith("ZFF") or vin.startswith("ZSG"):   # Ferrari WMIs
                return vin, vin_check_digit_ok(vin)
    return None, False


_CHASSIS = re.compile(r"chassis\s*(?:no\.?|number|#)?\s*:?\s*([0-9]{3,6}[A-Z]{0,3})\b", re.I)


def extract_chassis(*texts) -> Optional[str]:
    for t in texts:
        if t:
            m = _CHASSIS.search(str(t))
            if m:
                return m.group(1).upper()
    return None


# ---------------------------------------------------------------- year / place

_YEAR = re.compile(r"\b(19[4-9]\d|20[0-3]\d)\b")


def extract_year(*texts) -> Optional[int]:
    for t in texts:
        if t:
            m = _YEAR.search(str(t))
            if m:
                return int(m.group(1))
    return None


_CA = {"ab", "bc", "mb", "nb", "nl", "ns", "nt", "nu", "on", "pe", "qc", "sk", "yt", "alberta", "british columbia",
       "manitoba", "new brunswick", "newfoundland", "nova scotia", "ontario", "quebec", "québec", "saskatchewan", "canada"}
_US_STATES = set("al ak az ar ca co ct de fl ga hi id il in ia ks ky la me md ma mi mn ms mo mt ne nv nh nj nm ny nc nd oh ok or pa ri sc sd tn tx ut vt va wa wv wi wy dc".split())
_US_NAMES = {"alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware", "florida", "georgia",
             "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana", "maine", "maryland",
             "massachusetts", "michigan", "minnesota", "mississippi", "missouri", "montana", "nebraska", "nevada",
             "new hampshire", "new jersey", "new mexico", "new york", "north carolina", "north dakota", "ohio", "oklahoma",
             "oregon", "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
             "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming", "usa", "united states"}


def infer_country(location: Optional[str], explicit: Optional[str] = None) -> Optional[str]:
    if explicit:
        e = explicit.strip().lower()
        if e in ("us", "usa", "united states", "united states of america"):
            return "US"
        if e in ("ca", "can", "canada"):
            return "CA"
        return explicit.upper()[:2]
    if not location:
        return None
    parts = [p.strip().lower() for p in re.split(r"[,\(\)]", location) if p.strip()]
    for p in reversed(parts):
        if p in _CA:
            return "CA"
        if p in _US_STATES or p in _US_NAMES:
            return "US"
    return None


# ---------------------------------------------------------------- record normalization

_RANGE = re.compile(r"\d[\d,\.]*\s*[kKmM]?\s*(?:-|\u2013|\u2014|to)\s*\D{0,4}\d")


def is_price_range(text) -> bool:
    return bool(text and _RANGE.search(str(text)))


# Private sales, private treaty offers and sealed bid sales never publish a hammer
# price, so they are not auction results and are left out entirely (any house).
_PRIVATE = re.compile(r"private (sale|sales|treaty|offer)|\bsealed\b|/inventory/|//sealed\.", re.I)


def is_private_or_sealed(raw) -> bool:
    extra = raw.extra or {}
    text = " ".join(str(x or "") for x in (raw.auction_house, raw.url, extra.get("auction"), extra.get("sale_type")))
    return bool(extra.get("private_sale") or extra.get("sealed") or _PRIVATE.search(text))


class Reject(Exception):
    pass


def normalize(raw: RawRecord, fx: dict, min_confidence: float = 0.5) -> Normalized:
    if not raw.title and not raw.model_text:
        raise Reject("no title")
    if not taxonomy.is_ferrari(raw.title or "", raw.make):
        raise Reject("not a Ferrari")
    if not taxonomy.looks_like_car(raw.title or ""):
        raise Reject("not a car (parts, memorabilia, replica)")
    if taxonomy.other_make_first(raw.title or ""):
        raise Reject("another make's car with a Ferrari connection")
    if taxonomy.is_track_car(raw.title or ""):
        raise Reject("track only car")

    year = raw.year or extract_year(raw.title, raw.model_text)
    match = taxonomy.resolve(raw.title or "", year, raw.model_text)
    if match.model is None:
        raise Reject("unknown model")
    if match.confidence < min_confidence:
        raise Reject("low model confidence: " + "; ".join(match.reasons))

    if raw.price is not None:
        price, cur = raw.price, raw.currency
    elif is_price_range(raw.price_text):
        price, cur = None, raw.currency     # "$80,000 - $100,000" is an estimate, not a price
    else:
        price, cur = parse_price(raw.price_text, raw.currency)
    price_usd = to_usd(price, cur or "USD", fx)
    est_lo = to_usd(raw.estimate_low, raw.currency, fx)
    est_hi = to_usd(raw.estimate_high, raw.currency, fx)

    flags = []
    if raw.kind == AUCTION:
        if is_private_or_sealed(raw):
            raise Reject("private or sealed sale: no public price")
        if raw.status == "upcoming":
            price, price_usd = None, None        # an upcoming lot has an estimate, never a price
        if raw.status == "sold" and price_usd is None:
            raise Reject("sold without a published price")
    if raw.kind != AUCTION and price_usd is None:
        raise Reject("listing without a price")
    if price_usd is not None and (price_usd < 10_000 or price_usd > 60_000_000):
        raise Reject(f"price out of range: {price_usd}")
    if price is not None and price_usd is None:
        flags.append(f"no FX rate for {cur}")

    miles = parse_mileage(raw.mileage, raw.mileage_unit) if raw.mileage is not None else mileage_from_title(raw.title)
    vin, vin_ok = (raw.vin.upper(), True) if raw.vin and len(raw.vin) == 17 else extract_vin(raw.vin, raw.title, raw.extra.get("description"))
    if vin and raw.vin:
        vin_ok = vin_check_digit_ok(vin) or not vin.startswith("ZFF")
    chassis = None if vin else (raw.vin if raw.vin and len(raw.vin) < 17 else extract_chassis(raw.title, raw.extra.get("description")))

    m = match.model
    return Normalized(
        raw=raw,
        uid=f"{raw.source}:{raw.source_id}",
        model_id=m.id, model_name=m.name, family=m.family, body=m.body,
        model_confidence=match.confidence,
        year=year,
        price_usd=price_usd,
        estimate_low_usd=est_lo, estimate_high_usd=est_hi,
        mileage_mi=miles,
        vin=vin, vin_valid=bool(vin and vin_ok), chassis=chassis,
        country=infer_country(raw.location, raw.country),
        flags=flags,
    )
