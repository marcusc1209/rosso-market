"""Catch a broken source before it corrupts the numbers.

A scraper rarely fails loudly. More often a site changes its markup and the
adapter quietly returns 20 cars instead of 600. Each run's accepted count is
compared with that source's recent history:

  ok        within normal range
  degraded  under 50% of its trailing median, or more than 30% of records rejected
  down      raised an error, or returned nothing when it normally returns something
"""
from __future__ import annotations

import statistics as st


def check(con, source: str, run_id: int) -> dict:
    r = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    hist = [x[0] for x in con.execute(
        "SELECT accepted FROM runs WHERE source=? AND id<? AND status IN ('ok','degraded') ORDER BY id DESC LIMIT 7", (source, run_id))]
    baseline = st.median(hist) if hist else None
    accepted, fetched = r["accepted"] or 0, r["fetched"] or 0
    if r["status"] == "error":
        state, why = "down", r["error"]
    elif accepted == 0 and (baseline or fetched == 0):
        state, why = "down", f"0 records, usually {baseline:.0f}" if baseline else "returned nothing"
    elif baseline and accepted < 0.5 * baseline:
        state, why = "degraded", f"{accepted} records, usually {baseline:.0f}"
    elif fetched >= 20 and (fetched - accepted) / fetched > 0.3:
        state, why = "degraded", f"{fetched - accepted} of {fetched} records rejected"
    else:
        state, why = "ok", ""
    return {"source": source, "state": state, "detail": why, "accepted": accepted, "fetched": fetched, "baseline": baseline}
