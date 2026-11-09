"""Catch a broken source before it corrupts the numbers.

A scraper rarely fails loudly. More often a site changes its markup and the
adapter quietly returns 20 cars instead of 600. Each run's accepted count is
compared with that source's recent history:

  ok        within normal range
  degraded  under 50% of its trailing median, or more than 30% of its Ferrari cars rejected

Records turned away on purpose (memorabilia, race cars, another make's car, a car outside the US, a private
sale) are not counted against a source: an auction full of race cars and watches is working as it should.
Only what points to a broken page or a gap in the taxonomy counts: unknown model, a low confidence match,
a missing or impossible price.
  down      raised an error, or returned nothing when it normally returns something

A source that reads only what changed (Broad Arrow: finished results are never read again) reads a lot after
an auction and almost nothing in a quiet week, so its volume says nothing about its health. It reports what
it can see instead (Source.coverage) and is judged on that:

  down      raised an error, or the site listed nothing
  degraded  the site lists under 50% of its usual number of items, more than 30% of the pages read could not
            be made sense of (at least 5 read), or more than 30% of its Ferrari cars rejected
"""
from __future__ import annotations

import json

import statistics as st


# reasons a record is turned away by design (pipeline/normalize.py)
DELIBERATE = ("not a Ferrari", "not a car", "another make's car", "track only car", "listed outside the US",
              "private or sealed sale")


def _coverage_history(con, source: str, listed: int) -> list:
    """The items listed on the last runs (newest last), with this run's added."""
    try:
        row = con.execute("SELECT value FROM kv WHERE key=?", ("coverage:" + source,)).fetchone()
        hist = json.loads(row[0]) if row and row[0] else []
    except Exception:
        hist = []
    try:
        con.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", ("coverage:" + source, json.dumps((hist + [listed])[-7:])))
    except Exception:
        pass
    return hist


def check(con, source: str, run_id: int, coverage: dict | None = None) -> dict:
    r = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if coverage is not None and r["status"] != "error":
        return _check_coverage(con, source, run_id, r, coverage)
    hist = [x[0] for x in con.execute(
        "SELECT accepted FROM runs WHERE source=? AND id<? AND status IN ('ok','degraded') ORDER BY id DESC LIMIT 7", (source, run_id))]
    baseline = st.median(hist) if hist else None
    accepted, fetched = r["accepted"] or 0, r["fetched"] or 0
    meant = 0
    try:
        meant = sum(1 for (why,) in con.execute("SELECT reason FROM rejects WHERE run_id=? AND source=?", (run_id, source))
                    if (why or "").startswith(DELIBERATE))
    except Exception:
        pass
    considered = fetched - meant
    if r["status"] == "error":
        state, why = "down", r["error"]
    elif accepted == 0 and (baseline or fetched == 0):
        state, why = "down", f"0 records, usually {baseline:.0f}" if baseline else "returned nothing"
    elif baseline and accepted < 0.5 * baseline:
        state, why = "degraded", f"{accepted} records, usually {baseline:.0f}"
    elif considered >= 20 and (considered - accepted) / considered > 0.3:
        state, why = "degraded", f"{considered - accepted} of {considered} cars rejected"
    else:
        state, why = "ok", ""
    return {"source": source, "state": state, "detail": why, "accepted": accepted, "fetched": fetched, "baseline": baseline}


def _rejected_share(con, source: str, run_id: int, fetched: int, accepted: int):
    meant = 0
    try:
        meant = sum(1 for (why,) in con.execute("SELECT reason FROM rejects WHERE run_id=? AND source=?", (run_id, source))
                    if (why or "").startswith(DELIBERATE))
    except Exception:
        pass
    return fetched - meant


def _check_coverage(con, source: str, run_id: int, r, cov: dict) -> dict:
    accepted, fetched = r["accepted"] or 0, r["fetched"] or 0
    listed, read, failed = int(cov.get("listed") or 0), int(cov.get("read") or 0), int(cov.get("failed") or 0)
    hist = _coverage_history(con, source, listed)
    baseline = st.median(hist) if hist else None
    considered = _rejected_share(con, source, run_id, fetched, accepted)
    if listed == 0:
        state, why = "down", "the site listed nothing"
    elif baseline and listed < 0.5 * baseline:
        state, why = "degraded", f"site lists {listed}, usually {baseline:.0f}"
    elif read >= 5 and failed / read > 0.3:
        state, why = "degraded", f"{failed} of {read} pages not understood"
    elif considered >= 20 and (considered - accepted) / considered > 0.3:
        state, why = "degraded", f"{considered - accepted} of {considered} cars rejected"
    else:
        state, why = "ok", f"{listed} listed, {read} read"
    return {"source": source, "state": state, "detail": why, "accepted": accepted, "fetched": fetched, "baseline": baseline,
            "listed": listed, "read": read}
