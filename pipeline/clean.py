"""Re-check stored cars against today's filters.

The filters in taxonomy.py get stricter as new junk shows up ("Ferrari F40
Headlights", a children's ride on car). Rows saved before a rule existed would
otherwise stay in the database forever, so every run (including publish only
runs) passes the stored titles through the same checks normalize() uses and
drops whatever now fails. Each dropped row is written to the rejects table with
the reason, so nothing disappears silently.

It also finds auction prices that cannot be right for the model (a $14k sale
filed under F40), which export leaves out of the medians and charts.
"""
from __future__ import annotations

import json
import re
import logging
import statistics as st
from datetime import date

from . import taxonomy
from .normalize import extract_year, is_price_range

log = logging.getLogger("clean")

# a sold price under this share of the model's median sale is treated as a
# mislabeled lot (parts, a toy, a typo) rather than a real result
IMPLAUSIBLE_SHARE = 0.15
IMPLAUSIBLE_MAX_USD = 60_000      # parts, toys and typos are cheap: a $350k 250 GT is a real car
IMPLAUSIBLE_MIN_SALES = 5
IMPLAUSIBLE_WINDOW_DAYS = 730      # compare with sales of the same model within two years either side


def reason(title: str) -> str | None:
    t = title or ""
    if not taxonomy.looks_like_car(t):
        return "purged: not a car (parts, memorabilia, replica)"
    if taxonomy.other_make_first(t):
        return "purged: another make's car with a Ferrari connection"
    if taxonomy.is_track_car(t):
        return "purged: track only car"
    return None


def purge(con) -> dict:
    counts = {"lots": 0, "sightings": 0}
    for table, child in (("lots", "lot_observations"), ("sightings", "sighting_prices")):
        bad = [(r["uid"], r["source"], r["title"], why) for r in con.execute(f"SELECT uid, source, title FROM {table}")
               if (why := reason(r["title"]))]
        for uid, source, title, why in bad:
            con.execute("INSERT INTO rejects (run_id, source, source_id, title, reason) VALUES (NULL, ?, ?, ?, ?)",
                        (source, uid.split(":", 1)[-1], title, why))
            con.execute(f"DELETE FROM {child} WHERE uid=?", (uid,))
            con.execute(f"DELETE FROM {table} WHERE uid=?", (uid,))
        counts[table] = len(bad)
    counts["estimate_as_price"] = _drop_estimate_prices(con)
    counts.update(_apply_auction_policy(con))
    counts["reclassified"] = _reclassify(con)
    counts["archive_undated"] = _undate_old_rm(con)
    con.commit()
    if any(counts.values()):
        log.info("purged rows that fail the current filters: %s", counts)
    return counts


_RM_CODE = re.compile(r"rmsothebys\.com/auctions/[a-z]+(\d{2})/", re.I)


def rm_code_year(url: str):
    """RM Sotheby's sale codes end in the sale's year: mo06 is Monterey 2006, lf26 London 2026."""
    m = _RM_CODE.search(url or "")
    return 2000 + int(m.group(1)) if m else None


def _undate_old_rm(con) -> int:
    """RM keeps adding archive results (Monterey 2006, Monaco 2010). Ones whose sale page carries
    no date were dated by the day we first saw them, which put old sales among recent results.
    Their sale code names the year: when that disagrees with the date, the date is cleared."""
    n = 0
    for r in con.execute("SELECT uid, url, ends_at FROM lots WHERE source='rmsothebys' AND ends_at IS NOT NULL"):
        y = rm_code_year(r["url"])
        if y and abs(int(r["ends_at"][:4]) - y) > 1:
            con.execute("UPDATE lots SET ends_at=NULL WHERE uid=?", (r["uid"],))
            n += 1
    return n


def _drop_estimate_prices(con) -> int:
    """Results saved before the range check, whose 'price' was really the low estimate."""
    bad = []
    for r in con.execute("SELECT uid, source, title, raw FROM lots WHERE status IN ('sold','unsold')"):
        try:
            raw = json.loads(r["raw"] or "{}")
        except ValueError:
            continue
        if raw.get("price") is None and is_price_range(raw.get("price_text")):
            bad.append((r["uid"], r["source"], r["title"]))
    for uid, source, title in bad:
        con.execute("INSERT INTO rejects (run_id, source, source_id, title, reason) VALUES (NULL, ?, ?, ?, ?)",
                    (source, uid.split(":", 1)[-1], title, "purged: estimate saved as a result"))
        con.execute("DELETE FROM lot_observations WHERE uid=?", (uid,))
        con.execute("DELETE FROM lots WHERE uid=?", (uid,))
    return len(bad)


_PRIVATE_SQL = """(lower(auction_house) LIKE '%private%' OR url LIKE '%//sealed.%' OR url LIKE '%/inventory/%'
                    OR lower(raw) LIKE '%"private_sale": true%' OR lower(raw) LIKE '%"auction": "sealed%')"""


def _apply_auction_policy(con) -> dict:
    """Private and sealed sales are dropped, sold lots without a price are dropped,
    and upcoming lots keep their estimate but never a price."""
    out = {}
    for label, where, why in (
        ("private_or_sealed", _PRIVATE_SQL, "purged: private or sealed sale, no public price"),
        ("sold_without_price", "status='sold' AND price_usd IS NULL", "purged: sold without a published price"),
    ):
        rows = con.execute(f"SELECT uid, source, title FROM lots WHERE {where}").fetchall()
        for uid, source, title in rows:
            con.execute("INSERT INTO rejects (run_id, source, source_id, title, reason) VALUES (NULL, ?, ?, ?, ?)",
                        (source, uid.split(":", 1)[-1], title, why))
            con.execute("DELETE FROM lot_observations WHERE uid=?", (uid,))
            con.execute("DELETE FROM lots WHERE uid=?", (uid,))
        out[label] = len(rows)
    out["upcoming_price_cleared"] = con.execute(
        "UPDATE lots SET price_usd=NULL WHERE status='upcoming' AND price_usd IS NOT NULL").rowcount
    con.execute("UPDATE lot_observations SET price_usd=NULL WHERE status='upcoming'")
    return out


def _reclassify(con) -> int:
    """Cars parked in the catch all bucket that the current taxonomy can now name."""
    n = 0
    for table in ("lots", "sightings"):
        for r in con.execute(f"SELECT uid, title, year FROM {table} WHERE model_id='classic-other'").fetchall():
            m = taxonomy.resolve(r["title"] or "", r["year"] or extract_year(r["title"]))
            if m.model and m.model.id != "classic-other" and m.confidence >= 0.5:
                con.execute(f"UPDATE {table} SET model_id=? WHERE uid=?", (m.model.id, r["uid"]))
                n += 1
    return n


def implausible_lots(con) -> set[str]:
    """uids of sold lots priced far below what the same model sold for around the same time.

    Prices are only compared within a few years of each other (an F40 made $300k
    in 1999 and $3M in 2025), never in the catch all classic bucket, whose
    cars have nothing in common, and only when the gap is extreme (under 15%).
    """
    by: dict[str, list] = {}
    for r in con.execute("""SELECT uid, model_id, price_usd, substr(ends_at,1,10) AS d FROM lots
                            WHERE status='sold' AND price_usd > 0 AND model_id != 'classic-other'"""):
        try:
            d = date.fromisoformat(r["d"]).toordinal() if r["d"] else None
        except ValueError:
            d = None
        by.setdefault(r["model_id"], []).append((r["uid"], r["price_usd"], d))
    out = set()
    for rows in by.values():
        dated = [(p, d) for _, p, d in rows if d is not None]
        for uid, p, d in rows:
            if d is None:
                continue            # undated results never reach the medians or charts
            near = [q for q, e in dated if abs(e - d) <= IMPLAUSIBLE_WINDOW_DAYS]
            if p < IMPLAUSIBLE_MAX_USD and len(near) >= IMPLAUSIBLE_MIN_SALES and p < st.median(near) * IMPLAUSIBLE_SHARE:
                out.add(uid)
    return out
