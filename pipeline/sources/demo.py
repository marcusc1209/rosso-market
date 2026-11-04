"""Synthetic Ferrari market for development.

Produces a stable, realistic population of cars that are listed, repriced and
sold over time, plus auction lots that go live and close, so the whole
pipeline (dedupe, storage, removals, analytics, export, site) can be run end
to end without network access. Output is deterministic for a given date.
Every record is marked sample=True and the site shows a "sample data" banner
while any demo data is present. Disable this source once real sources run.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone
import zlib
from typing import Iterator

from .base import Source
from ..records import RawRecord, LISTING, AUCTION
from .. import taxonomy

EPOCH = date(2025, 11, 1)
SPAN = 460  # days of simulated market

# rough 2026 price levels for an average example, in USD (illustrative, not market data)
BASE = {
    "360-modena": 95e3, "360-spider": 105e3, "360-challenge-stradale": 310e3, "f430": 140e3, "f430-spider": 150e3,
    "430-scuderia": 330e3, "16m": 460e3, "458-italia": 215e3, "458-spider": 245e3, "458-speciale": 480e3,
    "458-speciale-a": 660e3, "488-gtb": 235e3, "488-spider": 265e3, "488-pista": 420e3, "488-pista-spider": 520e3,
    "f8-tributo": 300e3, "f8-spider": 335e3, "296-gtb": 300e3, "296-gts": 345e3, "sf90-stradale": 430e3,
    "sf90-spider": 485e3, "roma": 200e3, "roma-spider": 265e3, "portofino": 175e3, "portofino-m": 230e3,
    "california": 95e3, "california-t": 125e3, "purosangue": 610e3, "812-superfast": 360e3, "812-gts": 470e3,
    "812-competizione": 1.2e6, "f12berlinetta": 230e3, "f12tdf": 950e3, "599-gtb": 170e3, "599-gto": 820e3,
    "gtc4lusso": 200e3, "ff": 130e3, "612-scaglietti": 88e3, "550-maranello": 150e3, "575m": 165e3,
    "f355": 135e3, "f355-spider": 120e3, "348": 88e3, "328": 112e3, "308": 86e3, "testarossa": 145e3,
    "512-tr": 235e3, "f512m": 410e3, "daytona": 760e3, "dino-246": 460e3, "f40": 3.0e6, "f50": 5.1e6,
    "enzo": 4.6e6, "laferrari": 4.1e6, "laferrari-aperta": 6.2e6, "monza-sp1": 2.3e6, "daytona-sp3": 3.9e6,
    "12cilindri": 520e3,
}
# share of listings
WEIGHT = {"488-gtb": 6, "488-spider": 9, "458-italia": 6, "458-spider": 5, "f8-tributo": 6, "f8-spider": 5,
          "roma": 8, "portofino": 6, "portofino-m": 4, "california-t": 6, "california": 4, "sf90-stradale": 7,
          "sf90-spider": 4, "296-gtb": 6, "296-gts": 5, "812-superfast": 5, "812-gts": 4, "purosangue": 4,
          "f12berlinetta": 4, "gtc4lusso": 4, "f430": 4, "f430-spider": 3, "360-modena": 3, "360-spider": 3,
          "488-pista": 2, "599-gtb": 2, "ff": 2, "612-scaglietti": 1.5, "roma-spider": 2, "f355": 1.5,
          "testarossa": 1.5, "328": 1.2, "308": 1.5, "348": 1, "550-maranello": 1, "575m": 0.8, "458-speciale": 1,
          "12cilindri": 1.2, "488-pista-spider": 0.8, "812-competizione": 0.5, "430-scuderia": 0.6, "16m": 0.3,
          "f12tdf": 0.4, "laferrari": 0.4, "enzo": 0.15, "f40": 0.3, "f50": 0.1, "daytona-sp3": 0.3,
          "monza-sp1": 0.2, "dino-246": 0.6, "daytona": 0.3, "512-tr": 0.6, "f512m": 0.2, "599-gto": 0.2,
          "458-speciale-a": 0.3, "laferrari-aperta": 0.1, "360-challenge-stradale": 0.4}
# monthly price drift per model; a few models soften late in the window so the movers logic has work to do
DRIFT = {"458-spider": -0.018, "812-gts": -0.02, "488-spider": -0.012, "sf90-stradale": -0.01, "roma": -0.008,
         "f40": 0.012, "laferrari": 0.01, "488-pista": 0.008, "296-gts": -0.006, "f12berlinetta": -0.012}
LATE_SOFT = {"458-spider", "812-gts", "f12berlinetta", "488-spider"}

COLORS = ["Rosso Corsa", "Nero Daytona", "Grigio Silverstone", "Bianco Avus", "Giallo Modena", "Blu Tour de France",
          "Grigio Titanio", "Rosso Scuderia", "Argento Nurburgring", "Blu Pozzi", "Nero", "Grigio Ferro"]
CITIES = ["Miami, FL", "Los Angeles, CA", "Newport Beach, CA", "Scottsdale, AZ", "Dallas, TX", "Houston, TX",
          "Greenwich, CT", "Chicago, IL", "Atlanta, GA", "Denver, CO", "Seattle, WA", "Las Vegas, NV",
          "Toronto, ON", "Vancouver, BC", "Montreal, QC", "Calgary, AB", "Naples, FL", "Boston, MA", "Austin, TX"]
MARKETS = ["autotrader.com", "cars.com", "cargurus.com", "dupontregistry.com", "dealer site"]
HOUSES = [("Bring a Trailer", 0.55, "online"), ("Cars & Bids", 0.12, "online"), ("Sotheby's Motorsport", 0.08, "online"),
          ("duPont Registry Live", 0.07, "online"), ("RM Sotheby's", 0.07, "event"), ("Mecum", 0.05, "event"),
          ("Broad Arrow", 0.04, "event"), ("Gooding Christie's", 0.02, "event")]


def _drift(model_id: str, day: int) -> float:
    m = DRIFT.get(model_id, 0.0)
    f = 1 + m * day / 30
    if model_id in LATE_SOFT and day > SPAN - 140:
        f *= 1 - 0.0016 * (day - (SPAN - 140))
    return f


def _vin(rng: random.Random) -> str:
    chars = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
    return "ZFF" + "".join(rng.choice(chars) for _ in range(14))


class Demo(Source):
    kind = "both"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.cars, self.lots = self._population()

    def _population(self):
        rng = random.Random(1947)
        ids = list(WEIGHT)
        weights = [WEIGHT[i] for i in ids]
        cars = []
        for day in range(-120, SPAN):
            for _ in range(rng.choices([13, 14, 15, 16], k=1)[0]):
                mid = rng.choices(ids, weights)[0]
                m = taxonomy.BY_ID[mid]
                first, last = m.years
                year = rng.randint(first, min(last or 2026, 2026))
                age = 2026 - year
                miles = max(50, int(rng.gauss(2400, 900) * max(age, 0.4))) if first > 1995 else int(rng.uniform(8e3, 45e3))
                price = BASE[mid] * _drift(mid, max(day, 0)) * rng.lognormvariate(0, 0.09)
                price *= 1 - min(miles, 60000) / 600000
                cert = year >= 2014 and rng.random() < 0.35
                dur = int(rng.expovariate(1 / 80)) + 5
                cuts = {}
                d = day + rng.randint(10, 30)
                while d < day + dur:
                    p = 0.85 if (mid in LATE_SOFT and d > SPAN - 150) else 0.45
                    if rng.random() < p:
                        cuts[d] = rng.uniform(0.015, 0.05)
                    d += rng.randint(7, 21)
                markets = rng.sample(MARKETS[:-1], k=rng.choice([1, 1, 2]))
                cars.append(dict(model=m, year=year, miles=miles, price=round(price, -2), list=day, end=day + dur,
                                 cuts=cuts, vin=_vin(rng), color=rng.choice(COLORS), city=rng.choice(CITIES),
                                 cert=cert, markets=markets, dealer=f"{rng.choice(['Prestige', 'Cavallino', 'Maranello', 'Modena', 'Collection'])} Motorcars"))
        lots = []
        for day in range(-120, SPAN):
            for _ in range(rng.choices([1, 2, 3], [0.4, 0.4, 0.2])[0]):
                c = dict(rng.choice(cars))
                house = rng.choices(HOUSES, [h[1] for h in HOUSES])[0]
                online = house[2] == "online"
                fair = c["price"] * rng.uniform(0.78, 0.98)
                sold = rng.random() < (0.55 if c["model"].id in LATE_SOFT and day > SPAN - 60 else 0.72)
                lots.append(dict(car=c, house=house[0], online=online, start=day if online else day - 21, end=day + (7 if online else 0),
                                 fair=fair, sold=sold, no_reserve=rng.random() < 0.3, lot=rng.randint(100, 399),
                                 est=(round(fair * 0.85, -4), round(fair * 1.1, -4)), id=f"{house[0][:3].upper()}{day}{rng.randint(1000, 9999)}"))
        return cars, lots

    def fetch(self) -> Iterator[RawRecord]:
        today = (self.ctx.run_date - EPOCH).days
        for c in self.cars:
            if not (c["list"] <= today < c["end"]):
                continue
            price = c["price"]
            for d, cut in sorted(c["cuts"].items()):
                if d <= today:
                    price *= 1 - cut
            price = round(price, -2) - 10
            m = c["model"]
            title = f"{c['year']} Ferrari {m.name}"
            loc = c["city"]
            cur = "CAD" if loc.endswith(("ON", "BC", "QC", "AB")) else "USD"
            p = price * (1.37 if cur == "CAD" else 1)
            common = dict(kind=LISTING, title=title, currency=cur, year=c["year"], make="Ferrari", mileage=c["miles"],
                          vin=c["vin"], exterior_color=c["color"], location=loc, dealer_name=c["dealer"],
                          seller_type="dealer", extra={"sample": True})
            for mk in c["markets"]:
                bump = 1.0 if mk == "dealer site" else 1.0 + (0.01 if (zlib.crc32((c["vin"] + mk).encode()) % 4 == 0) else 0)
                yield RawRecord(source=self.name, source_id=f"{c['vin']}:{mk}", url=f"https://example.com/{mk}/{c['vin']}",
                                price=round(p * bump), marketplace=mk, **common)
            if c["cert"]:
                yield RawRecord(source=self.name, source_id=f"{c['vin']}:approved", url=f"https://example.com/approved/{c['vin']}",
                                price=round(p), marketplace="Ferrari Approved", certified=True, **common)

        for lot in self.lots:
            if lot["start"] > today or today - lot["end"] > 120:
                continue
            c, m = lot["car"], lot["car"]["model"]
            live = lot["start"] <= today <= lot["end"]
            if not lot["online"] and today < lot["end"]:
                status, price = "upcoming", None
            elif live and lot["online"]:
                progress = (today - lot["start"] + 1) / 8
                status, price = "live", round(lot["fair"] * (0.45 + 0.4 * progress), -3)
            else:
                status, price = ("sold" if lot["sold"] else "unsold"), round(lot["fair"] * (1 if lot["sold"] else 0.88), -3)
            ends = datetime.combine(EPOCH + timedelta(days=lot["end"]), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=19, minutes=lot["lot"] % 60)
            yield RawRecord(source=self.name, kind=AUCTION, source_id=lot["id"], url=f"https://example.com/lot/{lot['id']}",
                            title=f"{'No Reserve: ' if lot['no_reserve'] else ''}{c['year']} Ferrari {m.name}",
                            price=price, currency="USD", year=c["year"], make="Ferrari", mileage=c["miles"], vin=c["vin"],
                            exterior_color=c["color"], location=c["city"], status=status, ends_at=ends.isoformat(),
                            estimate_low=None if lot["online"] else lot["est"][0], estimate_high=None if lot["online"] else lot["est"][1],
                            no_reserve=lot["no_reserve"], auction_house=lot["house"], lot_number=None if lot["online"] else str(lot["lot"]),
                            seller_type="auction", extra={"sample": True})
