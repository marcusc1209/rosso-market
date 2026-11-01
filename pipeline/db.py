"""SQLite storage. One file, no server. Swap for Postgres later by porting these statements.

Key idea: prices are stored as daily observations, so any statistic can be
recomputed for any past day, and a source that fails for a day never makes
cars disappear (a sighting is only removed after its source has run
successfully twice without seeing it).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from .records import Normalized, LISTING, AUCTION
from .dedupe import representative_price

SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
  uid TEXT PRIMARY KEY, source TEXT, vehicle_key TEXT, model_id TEXT, year INT, title TEXT, url TEXT,
  marketplace TEXT, dealer_name TEXT, seller_type TEXT, certified INT, country TEXT, location TEXT,
  mileage_mi REAL, vin TEXT, exterior_color TEXT, image_url TEXT,
  first_seen TEXT, last_seen TEXT, removed_on TEXT, missed_runs INT DEFAULT 0, sample INT DEFAULT 0, raw TEXT
);
CREATE INDEX IF NOT EXISTS ix_sightings_vehicle ON sightings(vehicle_key);
CREATE INDEX IF NOT EXISTS ix_sightings_model ON sightings(model_id);
CREATE TABLE IF NOT EXISTS sighting_prices (
  uid TEXT, day TEXT, price_usd REAL, price_native REAL, currency TEXT, PRIMARY KEY (uid, day)
);
CREATE TABLE IF NOT EXISTS daily_asks (
  day TEXT, vehicle_key TEXT, model_id TEXT, price_usd REAL, n_sources INT, certified INT,
  PRIMARY KEY (day, vehicle_key)
);
CREATE INDEX IF NOT EXISTS ix_daily_model ON daily_asks(model_id, day);
CREATE TABLE IF NOT EXISTS lots (
  uid TEXT PRIMARY KEY, source TEXT, vehicle_key TEXT, model_id TEXT, year INT, title TEXT, url TEXT,
  auction_house TEXT, status TEXT, price_usd REAL, estimate_low_usd REAL, estimate_high_usd REAL,
  no_reserve INT, bid_count INT, ends_at TEXT, location TEXT, country TEXT, mileage_mi REAL, vin TEXT,
  lot_number TEXT, image_url TEXT, first_seen TEXT, last_seen TEXT, sample INT DEFAULT 0, raw TEXT
);
CREATE INDEX IF NOT EXISTS ix_lots_model ON lots(model_id, ends_at);
CREATE TABLE IF NOT EXISTS lot_observations (
  uid TEXT, day TEXT, status TEXT, price_usd REAL, PRIMARY KEY (uid, day)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, source TEXT, started_at TEXT, finished_at TEXT,
  status TEXT, fetched INT, accepted INT, rejected INT, requests INT, error TEXT
);
CREATE TABLE IF NOT EXISTS rejects (run_id INT, source TEXT, source_id TEXT, title TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS news (url TEXT PRIMARY KEY, title TEXT, outlet TEXT, published TEXT, image TEXT, first_seen TEXT);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def kv_get(con, key: str):
    row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return None if row is None else row[0]


def kv_set(con, key: str, value: str):
    con.execute("INSERT OR REPLACE INTO kv (key, value, updated) VALUES (?, ?, ?)", (key, value, now()))
    con.commit()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- runs

def start_run(con, day: date, source: str) -> int:
    cur = con.execute("INSERT INTO runs(day, source, started_at, status) VALUES (?,?,?, 'running')", (day.isoformat(), source, now()))
    return cur.lastrowid


def finish_run(con, run_id: int, status: str, fetched: int, accepted: int, rejected: int, requests: int, error: str | None = None):
    con.execute("UPDATE runs SET finished_at=?, status=?, fetched=?, accepted=?, rejected=?, requests=?, error=? WHERE id=?",
                (now(), status, fetched, accepted, rejected, requests, error, run_id))
    con.commit()


def log_reject(con, run_id, source, source_id, title, reason):
    con.execute("INSERT INTO rejects VALUES (?,?,?,?,?)", (run_id, source, source_id, (title or "")[:200], reason))


# ---------------------------------------------------------------- writes

def upsert(con, n: Normalized, day: date):
    r, d = n.raw, day.isoformat()
    sample = 1 if r.extra.get("sample") else 0
    raw_json = json.dumps(r.to_dict(), default=str)[:20000]
    if r.kind == LISTING:
        con.execute("""
          INSERT INTO sightings(uid, source, vehicle_key, model_id, year, title, url, marketplace, dealer_name, seller_type,
            certified, country, location, mileage_mi, vin, exterior_color, image_url, first_seen, last_seen, removed_on, missed_runs, sample, raw)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,0,?,?)
          ON CONFLICT(uid) DO UPDATE SET vehicle_key=excluded.vehicle_key, model_id=excluded.model_id, year=excluded.year,
            title=excluded.title, url=excluded.url, mileage_mi=COALESCE(excluded.mileage_mi, mileage_mi),
            image_url=COALESCE(excluded.image_url, image_url), last_seen=excluded.last_seen, removed_on=NULL, missed_runs=0,
            raw=excluded.raw""",
            (n.uid, r.source, n.vehicle_key, n.model_id, n.year, r.title, r.url, r.marketplace, r.dealer_name, r.seller_type,
             int(r.certified), n.country, r.location, n.mileage_mi, n.vin, r.exterior_color, r.image_url, d, d, sample, raw_json))
        con.execute("INSERT OR REPLACE INTO sighting_prices VALUES (?,?,?,?,?)", (n.uid, d, n.price_usd, r.price, r.currency))
    else:
        con.execute("""
          INSERT INTO lots(uid, source, vehicle_key, model_id, year, title, url, auction_house, status, price_usd,
            estimate_low_usd, estimate_high_usd, no_reserve, bid_count, ends_at, location, country, mileage_mi, vin, lot_number,
            image_url, first_seen, last_seen, sample, raw)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(uid) DO UPDATE SET status=COALESCE(excluded.status, status), price_usd=CASE WHEN excluded.status IN ('sold','unsold','upcoming') THEN excluded.price_usd ELSE COALESCE(excluded.price_usd, price_usd) END,
            estimate_low_usd=COALESCE(excluded.estimate_low_usd, estimate_low_usd), estimate_high_usd=COALESCE(excluded.estimate_high_usd, estimate_high_usd),
            bid_count=COALESCE(excluded.bid_count, bid_count), ends_at=COALESCE(excluded.ends_at, ends_at), last_seen=excluded.last_seen,
            image_url=COALESCE(excluded.image_url, image_url),
            vehicle_key=excluded.vehicle_key, model_id=excluded.model_id, raw=excluded.raw""",
            (n.uid, r.source, n.vehicle_key, n.model_id, n.year, r.title, r.url, r.auction_house, r.status, n.price_usd,
             n.estimate_low_usd, n.estimate_high_usd, None if r.no_reserve is None else int(r.no_reserve), r.bid_count, r.ends_at,
             r.location, n.country, n.mileage_mi, n.vin, r.lot_number, r.image_url, d, d, sample, raw_json))
        con.execute("INSERT OR REPLACE INTO lot_observations VALUES (?,?,?,?)", (n.uid, d, r.status, n.price_usd))


def mark_missing(con, source: str, day: date, after_runs: int = 2) -> int:
    """Called only after `source` ran successfully. Returns how many sightings were removed."""
    d = day.isoformat()
    con.execute("UPDATE sightings SET missed_runs = missed_runs + 1 WHERE source=? AND removed_on IS NULL AND last_seen < ?", (source, d))
    cur = con.execute("UPDATE sightings SET removed_on=? WHERE source=? AND removed_on IS NULL AND missed_runs >= ?", (d, source, after_runs))
    return cur.rowcount


def build_daily_asks(con, day: date):
    """Deduplicated snapshot: one asking price per physical car that is still for sale."""
    d = day.isoformat()
    con.execute("DELETE FROM daily_asks WHERE day=?", (d,))
    rows = con.execute("""
      SELECT s.uid, s.vehicle_key, s.model_id, s.source, s.certified,
        (SELECT price_usd FROM sighting_prices p WHERE p.uid=s.uid AND p.day<=? ORDER BY p.day DESC LIMIT 1) AS price
      FROM sightings s WHERE s.first_seen<=? AND (s.removed_on IS NULL OR s.removed_on>?)""", (d, d, d)).fetchall()
    groups: dict[str, list] = {}
    for r in rows:
        if r["price"]:
            groups.setdefault(r["vehicle_key"], []).append(r)

    class _S:  # minimal shim for dedupe.representative_price
        def __init__(self, r):
            self.price_usd = r["price"]
            self.raw = type("R", (), {"source": r["source"]})

    for key, rs in groups.items():
        # a car seen with two different model ids (rare parse disagreement): use the most common
        model = max({r["model_id"] for r in rs}, key=lambda m: sum(1 for r in rs if r["model_id"] == m))
        price = representative_price([_S(r) for r in rs])
        con.execute("INSERT INTO daily_asks VALUES (?,?,?,?,?,?)",
                    (d, key, model, price, len(rs), int(any(r["certified"] for r in rs))))
    con.commit()
    return len(groups)


def save_news(con, items, day: date):
    for it in items:
        con.execute("INSERT OR IGNORE INTO news VALUES (?,?,?,?,?,?)",
                    (it["url"], it["title"], it["outlet"], it["published"], it.get("image"), day.isoformat()))
    con.commit()
