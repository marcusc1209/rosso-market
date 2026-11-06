"""Listing dates and price histories entered by hand (config/listing_history.yaml).

Cars.com, AutoTrader.com and CarGurus show when a car was first listed and every price change,
but all three sit behind bot checks this pipeline never works around. What the owner reads
there can be entered per VIN instead:

    ZFF90HLA3L0251840:
      listed_on: 2026-08-29
      source: cars.com
      url: https://www.cars.com/vehicledetail/ecec9220-20e5-4b3f-a2b1-37d519200453/
      prices:                      # optional: the price history, oldest first
        - [2026-08-29, 1199000]
        - [2026-10-02, 1149000]

A car from before 17 character VINs goes by the site's key instead, such as chassis:308:021695.

An entry gives every listing of that car its date (when the date is earlier than what a site
published) and the price history before we saw the car. It applies to stored listings at once
(so a republish shows it) and to each new record a run reads.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import yaml

from .records import LISTING

log = logging.getLogger("overrides")


def load(config_path) -> dict:
    p = Path(config_path).with_name("listing_history.yaml")
    if not p.exists():
        return {}
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        log.warning("listing_history.yaml not read: %s", e)
        return {}
    out = {}
    for vin, v in raw.items():
        if not isinstance(v, dict):
            continue
        day = str(v.get("listed_on") or "")[:10] or None
        prices = [[str(d)[:10], float(pr)] for d, pr in (v.get("prices") or []) if d and pr]
        k = str(vin).strip()
        # a VIN, or for cars from before VINs the site's own key: chassis:<model>:<number>
        out[k.lower() if k.lower().startswith("chassis:") else k.upper()] = {"listed_on": day, "how": str(v.get("source") or "entered by hand"),
                                         "url": v.get("url"), "prices": sorted(prices)}
    return out


def _merge(extra: dict, ov: dict) -> bool:
    changed = False
    if ov["listed_on"] and (not extra.get("listed_on") or ov["listed_on"] < extra["listed_on"]):
        extra["listed_on"], extra["listed_how"] = ov["listed_on"], ov["how"]
        extra.pop("listed_unknown", None)
        changed = True
    if ov["prices"] and extra.get("price_history") != ov["prices"]:
        extra["price_history"] = ov["prices"]
        changed = True
    return changed


def apply(rec, entries: dict) -> None:
    if rec.kind == LISTING and rec.vin and rec.vin.upper() in entries:
        _merge(rec.extra, entries[rec.vin.upper()])


def apply_db(con, entries: dict) -> int:
    """Bring stored listings in line with the file, so a republish shows the entries."""
    n = 0
    for vin, ov in entries.items():
        vk = vin if vin.startswith("chassis:") else "vin:" + vin
        for uid, raw in con.execute("SELECT uid, raw FROM sightings WHERE vehicle_key=? AND json_valid(raw)", (vk,)).fetchall():
            r = json.loads(raw)
            extra = r.setdefault("extra", {})
            if _merge(extra, ov):
                con.execute("UPDATE sightings SET raw=? WHERE uid=?", (json.dumps(r), uid))
                n += 1
    con.commit()
    return n
