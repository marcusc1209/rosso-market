"""Merge a database saved by the other runner into ours before saving.

The GitHub workflow (cloud sources) and the Mac workflow (home sources) both
restore the database, add their own data and push it back. If the other one
saved in between, this pulls its rows in so neither overwrites the other:

  python -m pipeline.merge data/remote.db --where cloud

Rows from sources this run did not touch are taken from the remote copy.
Observations (daily prices, lot statuses) are unioned. Today's deduplicated
snapshot is rebuilt afterwards.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

from . import db
from .run import load_config


def mine(cfg: dict, where: str) -> list[str]:
    return [n for n, s in (cfg.get("sources") or {}).items() if s.get("runner", "cloud") == where]


def merge(local: Path, remote: Path, our_sources: list[str], day: date) -> dict:
    con = db.connect(local)
    con.execute("ATTACH DATABASE ? AS r", (str(remote),))
    q = ",".join("?" * len(our_sources)) or "''"
    counts = {}
    for table in ("sightings", "lots"):
        cur = con.execute(f"INSERT OR REPLACE INTO main.{table} SELECT * FROM r.{table} WHERE source NOT IN ({q})", our_sources)
        counts[table] = cur.rowcount
    for table in ("sighting_prices", "lot_observations", "news"):
        counts[table] = con.execute(f"INSERT OR IGNORE INTO main.{table} SELECT * FROM r.{table}").rowcount
    cols = "day, source, started_at, finished_at, status, fetched, accepted, rejected, requests, error"
    counts["runs"] = con.execute(
        f"INSERT INTO main.runs ({cols}) SELECT {cols} FROM r.runs rr WHERE rr.source NOT IN ({q}) "
        f"AND NOT EXISTS (SELECT 1 FROM main.runs m WHERE m.source = rr.source AND m.started_at = rr.started_at)",
        our_sources).rowcount
    con.commit()
    con.execute("DETACH DATABASE r")
    db.build_daily_asks(con, day)
    con.close()
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("remote")
    ap.add_argument("--where", choices=["cloud", "home"], required=True)
    ap.add_argument("--config", default="config/sources.yaml")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    local = Path(cfg["settings"].get("db", "data/ferrari.db"))
    print("merged", merge(local, Path(a.remote), mine(cfg, a.where), date.today()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
