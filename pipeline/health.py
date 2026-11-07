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
"""
from __future__ import annotations

import statistics as st


# reasons a record is turned away by design (pipeline/normalize.py)
DELIBERATE = ("not a Ferrari", "not a car", "another make's car", "track only car", "listed outside the US",
              "private or sealed sale")


def check(con, source: str, run_id: int) -> dict:
    r = con.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
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
