"""Cars for sale week by week for one model: how many, how many arrived, how many left, median ask.

Two sources are joined by VIN:
  our own listings   each car from the day it was listed (the earliest date any site published, or the
                     day we first saw it) to the day it left the market
  the owner's tracker  the CarGurus weekly checks in config/tracker (pipeline/tracker.py), for the models
                     it covers, from Mar 2, 2026

Weeks run Monday to Sunday and each is read on its Monday (today for the current week). Before our own
daily snapshots began, cars that sold before we started are only known through the tracker, so those
weeks are marked as rebuilt: a floor rather than a full count.
"""
from __future__ import annotations

import json
import statistics
from datetime import date, timedelta

MAX_WEEKS = 40


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _our_cars(con, model_id: str) -> dict:
    """key -> {start, end, prices [(day, price)]} for every car of the model we have listed."""
    cars = {}
    for r in con.execute("""SELECT uid, vehicle_key, first_seen, removed_on, raw FROM sightings WHERE model_id=?""", (model_id,)):
        key = r["vehicle_key"]
        key = key[4:].upper() if key.startswith("vin:") else key
        c = cars.setdefault(key, {"start": None, "end": None, "open": False, "prices": []})
        extra = {}
        if r["raw"]:
            try:
                extra = (json.loads(r["raw"]) or {}).get("extra") or {}
            except ValueError:
                pass
        start = min(x for x in (r["first_seen"], extra.get("listed_on")) if x)[:10]
        c["start"] = min(c["start"] or start, start)
        if r["removed_on"]:
            c["end"] = max(c["end"] or r["removed_on"][:10], r["removed_on"][:10])
        else:
            c["open"] = True
        c["prices"] += [(str(d)[:10], float(p)) for d, p in (extra.get("price_history") or []) if p]
        c["prices"] += [(d, p) for d, p in con.execute("SELECT day, price_usd FROM sighting_prices WHERE uid=? AND price_usd > 0", (r["uid"],))]
    for c in cars.values():
        if c["open"]:
            c["end"] = None
        c["prices"].sort()
    return cars


def _price_on(prices, day: str):
    p = None
    for d, v in prices:
        if d > day:
            break
        p = v
    return p if p is not None else (prices[0][1] if prices else None)


def series(con, model_id: str, today: date, tracked: list | None = None, since: str | None = None) -> dict:
    """{"weeks": [[monday, listings, added, gone, median ask, rebuilt 0|1, from tracker]], "since": first daily snapshot}"""
    ours = _our_cars(con, model_id)
    tracked = tracked or []
    tweek = {}                                # monday -> {vin: price or None}
    for c in tracked:
        for d, p in c["prices"].items():
            wk = _monday(date.fromisoformat(d)).isoformat()
            tweek.setdefault(wk, {})[c["vin"]] = None if p == "NP" else p
    starts = [c["start"] for c in ours.values() if c["start"]] + list(tweek)
    if not starts:
        return {"weeks": [], "since": since}
    first = max(_monday(date.fromisoformat(min(starts))), _monday(today) - timedelta(weeks=MAX_WEEKS - 1))
    # without the tracker, weeks before our daily snapshots would count only the cars that were still for sale
    # when we started, so the count would seem to climb. Those models start at our first snapshot.
    if not tracked and since:
        first = max(first, _monday(date.fromisoformat(since)))
    # and models it covers start at its first check, for the same reason
    if tweek:
        first = max(first, date.fromisoformat(min(tweek)))
    out, prev, last_tv = [], None, {}
    wk = first
    while wk <= today:
        read = min(wk, today).isoformat() if wk < _monday(today) else today.isoformat()
        active = {}
        for k, c in ours.items():
            if c["start"] and c["start"] <= read and (c["end"] is None or c["end"] > read):
                active[k] = _price_on(c["prices"], read)
        # a week the tracker skipped (checks were not always a week apart) carries the check before it
        tv = tweek.get(wk.isoformat())
        if tv is None:
            tv = last_tv if tweek and wk.isoformat() < max(tweek) else {}
        last_tv = tv
        for vin, p in tv.items():
            if active.get(vin) is None:
                active[vin] = p
        prices = [p for p in active.values() if p]
        keys = set(active)
        out.append([wk.isoformat(), len(keys),
                    len(keys - prev) if prev is not None else None, len(prev - keys) if prev is not None else None,
                    round(statistics.median(prices)) if prices else None,
                    1 if since and read < since else 0, len(tv)])
        prev = keys
        wk += timedelta(weeks=1)
    # drop leading weeks with nothing in them
    while out and not out[0][1]:
        out.pop(0)
    if out:
        out[0][2] = out[0][3] = None
    return {"weeks": out, "since": since, "tracker": bool(tracked)}
