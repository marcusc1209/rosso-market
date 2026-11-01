"""The two record shapes that move through the pipeline.

RawRecord is what a source adapter emits: whatever it could read, in the
source's own currency and units. Normalized is what the rest of the pipeline
works with: USD, miles, a canonical model id and a stable vehicle key.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

LISTING = "listing"   # a car offered for sale at an asking price (dealer or private)
AUCTION = "auction"   # an auction lot (live, sold, unsold, upcoming)

AUCTION_STATUSES = {"upcoming", "live", "sold", "unsold", "withdrawn"}


@dataclass
class RawRecord:
    source: str
    kind: str
    source_id: str
    url: str
    title: str
    price: Optional[float] = None          # asking price, current bid, or final price
    currency: str = "USD"
    price_text: Optional[str] = None       # unparsed price, kept for debugging
    year: Optional[int] = None
    make: Optional[str] = None
    model_text: Optional[str] = None
    mileage: Optional[float] = None
    mileage_unit: str = "mi"
    vin: Optional[str] = None
    exterior_color: Optional[str] = None
    interior_color: Optional[str] = None
    transmission: Optional[str] = None
    location: Optional[str] = None
    country: Optional[str] = None
    seller_type: Optional[str] = None      # dealer, private, auction
    dealer_name: Optional[str] = None
    certified: bool = False                # Ferrari Approved / CPO
    marketplace: Optional[str] = None      # where an aggregated listing was seen (autotrader.com, cars.com ...)
    status: Optional[str] = None           # auctions only, see AUCTION_STATUSES
    ends_at: Optional[str] = None          # ISO 8601
    estimate_low: Optional[float] = None
    estimate_high: Optional[float] = None
    no_reserve: Optional[bool] = None
    bid_count: Optional[int] = None
    lot_number: Optional[str] = None
    auction_house: Optional[str] = None
    image_url: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Normalized:
    raw: RawRecord
    uid: str                     # source:source_id
    model_id: str
    model_name: str
    family: str
    body: str
    model_confidence: float
    year: Optional[int]
    price_usd: Optional[float]
    estimate_low_usd: Optional[float]
    estimate_high_usd: Optional[float]
    mileage_mi: Optional[float]
    vin: Optional[str]
    vin_valid: bool
    chassis: Optional[str]
    country: Optional[str]
    vehicle_key: str = ""
    flags: list = field(default_factory=list)
