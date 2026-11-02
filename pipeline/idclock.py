"""Listing dates from listing numbers.

ClassicCars.com numbers its listings in order (1,676,304 was the newest on Jan 1 2023, about
2,123,465 on Nov 2 2026: roughly 320 new listings a day across the site). Its home page shows
the newest listings, so any capture of the home page pins a number to a day: everything up
to the highest number on it was listed by then. The Wayback Machine has captured the home page
most days since 2018, and each run of ours adds today's highest number.

A car's listing number then falls between two pinned days. When they are far apart, the
capture nearest the interpolated day is read, which narrows the gap; a few reads date a car
to within TOL days. Captures read are kept (kv), so each is read once for every car.

The date given is the first pinned day at or above the car's number: it was listed by then.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Optional

from . import db

TOL = 10                 # days: a car is dated once its number sits between pins this close
STEPS = 6                # captures read for one car at most per run
REFRESH = 30             # days before the list of captures is asked for again


class Clock:
    def __init__(self, con, lookups, name: str, page_url: str, id_pattern: str, today: date):
        self.con, self.lk, self.page, self.today = con, lookups, page_url, today
        self.key = f"clock:{name}"
        self.id_re = re.compile(id_pattern)
        raw = db.kv_get(con, self.key) if con is not None else None
        self.pins: dict[str, Optional[int]] = json.loads(raw) if raw else {}
        self._snaps: Optional[list[str]] = None

    # ------------------------------------------------------------ pins
    def max_id(self, html: str) -> Optional[int]:
        ids = [int(x) for x in self.id_re.findall(html or "")]
        return max(ids) if ids else None

    def pin(self, day: str, top: Optional[int]):
        if top is None and day in self.pins:
            return
        self.pins[day] = max(top, self.pins.get(day) or 0) if top else top
        if self.con is not None:
            db.kv_set(self.con, self.key, json.dumps(self.pins, sort_keys=True))

    def _line(self) -> list[tuple[str, int]]:
        """Pins in date order, each at least as high as the ones before it."""
        out, top = [], 0
        for d in sorted(self.pins):
            v = self.pins[d]
            if v:
                top = max(top, v)
                out.append((d, top))
        return out

    # ------------------------------------------------------------ captures
    def snapshots(self) -> list[str]:
        """Timestamps of the archive's captures of the page, one a day."""
        if self._snaps is not None:
            return self._snaps
        key = "wb:snaps:" + self.page
        raw = db.kv_get(self.con, key) if self.con is not None else None
        got = json.loads(raw) if raw else None
        fresh = got and (self.today - date.fromisoformat(got["asked"])).days < REFRESH
        if not fresh:
            rows = self.lk.captures(self.page)
            if rows is not None:
                got = {"asked": self.today.isoformat(), "ts": rows}
                if self.con is not None:
                    db.kv_set(self.con, key, json.dumps(got))
        self._snaps = (got or {}).get("ts") or []
        return self._snaps

    # ------------------------------------------------------------ dating
    def date_of(self, number: int) -> Optional[str]:
        """Day by which listing `number` was up, or None when it can not be pinned closely yet."""
        for _ in range(STEPS + 1):
            line = self._line()
            hi = next(((d, v) for d, v in line if v >= number), None)
            if hi is None:
                return None
            lo = next(((d, v) for d, v in reversed(line) if v < number and d < hi[0]), None)
            hd = date.fromisoformat(hi[0])
            if lo and (hd - date.fromisoformat(lo[0])).days <= TOL:
                return hi[0]
            ld = date.fromisoformat(lo[0]) if lo else None
            cands = [ts for ts in self.snapshots()
                     if (ld is None or _day(ts) > ld) and _day(ts) < hd and _day(ts).isoformat() not in self.pins]
            if not cands:
                # nothing archived in between: as close as it gets (no lower pin: older than the archive)
                return hi[0] if lo else None
            if lo:
                frac = (number - lo[1]) / max(1, hi[1] - lo[1])
                target = ld + timedelta(days=(hd - ld).days * frac)
                ts = min(cands, key=lambda t: abs((_day(t) - target).days))
            else:
                ts = cands[len(cands) // 2]
            body = self.lk.snapshot(ts, self.page)
            if body is None:
                return None                     # archive busy or out of budget: try next run
            self.pin(_day(ts).isoformat(), self.max_id(body))
        return None


def _day(ts: str) -> date:
    return date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))
