"""MarketCheck: licensed, daily dealer and private party listings for the US and Canada.

This is the backbone for asking prices. MarketCheck already collects the
dealer inventory that appears on AutoTrader, Cars.com, CarGurus and dealer
websites, deduplicated by VIN, with the marketplace each listing came from.
Scraping those marketplaces directly is fragile (they run aggressive bot
protection) and against most of their terms, so the direct adapters for them
ship disabled and this one is the default.

Needs MARKETCHECK_API_KEY in the environment.
Docs: https://docs.marketcheck.com/docs/api/cars/inventory/inventory-search
"""
from __future__ import annotations

import os
from typing import Iterator

from .base import Source
from ..records import RawRecord, LISTING, AUCTION

BASE = "https://api.marketcheck.com/v2/search/car"
ENDPOINTS = {"dealer": "/active", "private": "/fsbo/active", "auction": "/auction/active"}


def map_listing(d: dict, source: str, channel: str) -> RawRecord:
    build = d.get("build") or {}
    dealer = d.get("dealer") or {}
    media = d.get("media") or {}
    loc = ", ".join(x for x in [dealer.get("city"), dealer.get("state")] if x) or None
    return RawRecord(
        source=source, kind=AUCTION if channel == "auction" else LISTING,
        source_id=str(d.get("id") or d.get("vin")), url=d.get("vdp_url") or "",
        title=d.get("heading") or " ".join(str(x) for x in [build.get("year"), build.get("make"), build.get("model"), build.get("trim")] if x),
        price=d.get("price"), currency="CAD" if (dealer.get("country") or "").lower() in ("ca", "canada") else "USD",
        year=build.get("year"), make=build.get("make"),
        model_text=" ".join(x for x in [build.get("model"), build.get("trim")] if x) or None,
        mileage=d.get("miles"), vin=d.get("vin"),
        exterior_color=d.get("exterior_color"), interior_color=d.get("interior_color"),
        transmission=build.get("transmission"), location=loc, country=dealer.get("country"),
        seller_type="private" if channel == "private" else "auction" if channel == "auction" else "dealer",
        dealer_name=dealer.get("name"), certified=bool(d.get("is_certified")),
        marketplace=d.get("source"), status="live" if channel == "auction" else None,
        image_url=(media.get("photo_links") or [None])[0],
        extra={"dom": d.get("dom"), "first_seen": d.get("first_seen_at_date"), "inventory_type": d.get("inventory_type")},
    )


class MarketCheck(Source):
    def fetch(self) -> Iterator[RawRecord]:
        key = os.environ.get(self.cfg.get("api_key_env", "MARKETCHECK_API_KEY"))
        if not key:
            raise RuntimeError("MARKETCHECK_API_KEY is not set")
        rows = 50
        cap = int(self.cfg.get("max_rows_per_channel", 5000))
        for channel in self.cfg.get("channels", ["dealer", "private"]):
            for country in self.cfg.get("countries", ["us", "ca"]):
                start = 0
                while start < cap:
                    params = {"api_key": key, "make": "ferrari", "rows": rows, "start": start, "country": country,
                              "sort_by": "dom", "sort_order": "asc"}
                    if channel == "dealer":
                        params["car_type"] = self.cfg.get("car_type", "used")
                    data = self.ctx.fetcher.get_json(BASE + ENDPOINTS[channel], params=params)
                    listings = data.get("listings") or []
                    for d in listings:
                        yield map_listing(d, self.name, channel)
                    start += rows
                    if not listings or start >= int(data.get("num_found", 0)):
                        break
