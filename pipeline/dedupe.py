"""Work out which records are the same physical car.

The same Ferrari is often listed on the dealer's own site, on AutoTrader, on
Cars.com and in the Ferrari Approved catalogue at the same time, and the same
car can go to auction more than once. Counting it once is what keeps medians
honest.

Priority:
  1. VIN (17 characters, Ferrari WMI)      -> vin:ZFF...
  2. Chassis number (pre 1981 cars)        -> chassis:<model>:<number>
  3. Fingerprint: model, year, mileage band, colour, seller
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Iterable

from .records import Normalized, LISTING

_COLOR = re.compile(r"[^a-z]")


def _color(c):
    return _COLOR.sub("", (c or "").lower())[:12]


def vehicle_key(n: Normalized) -> str:
    if n.vin:
        return f"vin:{n.vin}"
    if n.chassis:
        return f"chassis:{n.model_id}:{n.chassis}"
    miles_band = "na" if n.mileage_mi is None else str(int(n.mileage_mi // 250))
    seller = (n.raw.dealer_name or n.raw.source or "").lower().strip()
    basis = "|".join([n.model_id, str(n.year or ""), miles_band, _color(n.raw.exterior_color), seller])
    return "fp:" + hashlib.sha1(basis.encode()).hexdigest()[:16]


def assign_keys(records: Iterable[Normalized]) -> list[Normalized]:
    out = []
    for n in records:
        n.vehicle_key = vehicle_key(n)
        out.append(n)
    return out


def collapse_listings(records: list[Normalized]) -> dict[str, list[Normalized]]:
    """Group listing sightings by vehicle. Auctions are kept separate on purpose."""
    groups: dict[str, list[Normalized]] = defaultdict(list)
    for n in records:
        if n.raw.kind == LISTING:
            groups[n.vehicle_key].append(n)
    return groups


def representative_price(sightings: list[Normalized]) -> float | None:
    """One asking price per car. Prefer the selling dealer's own page, then Ferrari Approved,
    then the lowest price seen on a marketplace that day (marketplaces lag price cuts)."""
    prices = [s for s in sightings if s.price_usd]
    if not prices:
        return None
    direct = [s for s in prices if s.raw.source in ("dealer_sites", "ferrari_approved")]
    pool = direct or prices
    return min(s.price_usd for s in pool)
