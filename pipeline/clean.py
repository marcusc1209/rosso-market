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
import math
import statistics as st
from datetime import date

from . import db, taxonomy
from .normalize import extract_year, is_price_range, outside_market

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
    counts["outside_us"] = _drop_outside_market(con)
    counts["estimate_as_price"] = _drop_estimate_prices(con)
    counts.update(_apply_auction_policy(con))
    counts["chassis_rekeyed"] = _one_chassis_spelling(con)
    counts["reclassified"] = _reclassify(con)
    counts["titles_tidied"] = _tidy_titles(con)
    counts["placeholder_photos"] = _drop_placeholder_photos(con)
    counts["archive_undated"] = _undate_old_rm(con)
    con.commit()
    if any(counts.values()):
        log.info("purged rows that fail the current filters: %s", counts)
    return counts


RETIRED_SOURCES = ("autotrader_ca",)      # removed 2026-11-04: it listed only Canadian cars


def _drop_outside_market(con) -> int:
    """Cars for sale outside the US (Canada, the Netherlands, the UK) leave the database, hypercars
    excepted, and every daily snapshot is rebuilt without them, so medians, movers and charts are US
    prices throughout (and world prices for hypercars). Auction lots are not touched: results stay
    international."""
    bad = [(r["uid"], r["source"], r["title"], where) for r in con.execute("SELECT uid, source, title, country, location, model_id FROM sightings")
           if (where := outside_market(r["country"], r["location"], r["model_id"]))]
    # a source that was removed never runs again, so its cars would never be marked gone
    bad += [(r["uid"], r["source"], r["title"], "source removed") for r in con.execute(
        f"SELECT uid, source, title FROM sightings WHERE source IN ({','.join('?' * len(RETIRED_SOURCES))})", tuple(RETIRED_SOURCES))
        if r["uid"] not in {b[0] for b in bad}]
    for uid, source, title, where in bad:
        con.execute("INSERT INTO rejects (run_id, source, source_id, title, reason) VALUES (NULL, ?, ?, ?, ?)",
                    (source, uid.split(":", 1)[-1], title, f"purged: listed outside the US ({where})"))
        con.execute("DELETE FROM sighting_prices WHERE uid=?", (uid,))
        con.execute("DELETE FROM sightings WHERE uid=?", (uid,))
    if bad:
        for (day,) in con.execute("SELECT DISTINCT day FROM daily_asks").fetchall():
            db.build_daily_asks(con, date.fromisoformat(day))
    return len(bad)


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
        # a sale whose site shows it but hides the price (price_hidden, Sotheby's Motorsport) is kept without one
        ("sold_without_price", "status='sold' AND price_usd IS NULL AND COALESCE(raw, '') NOT LIKE '%\"price_hidden\": true%'",
         "purged: sold without a published price"),
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


def _remember_moves(con, moved: dict) -> None:
    """Old vehicle key -> new one, kept so a link to a car's old page still finds it (cars.write)."""
    if not moved:
        return
    from . import db
    old = json.loads(db.kv_get(con, "car_moves") or "{}")
    old.update(moved)
    for k, v in list(old.items()):          # a car that moved twice points straight at its latest key
        seen = set()
        while v in old and v not in seen:
            seen.add(v); v = old[v]
        old[k] = v
    db.kv_set(con, "car_moves", json.dumps(old, separators=(",", ":")))


def _one_chassis_spelling(con) -> int:
    """Stored keys use one spelling of each chassis number, so a car listed as "015343" on one site and
    "15343" (or a padded "ZFF00000000015343") on another is one car. The asking price history follows."""
    from .normalize import chassis_number, padded_chassis
    n = 0
    moved: dict[str, tuple[str, str]] = {}                 # old key -> (new key, model)
    for table in ("lots", "sightings"):
        rows = con.execute(f"""SELECT uid, vehicle_key, model_id, raw FROM {table} WHERE vehicle_key LIKE 'chassis:%:0%'
                               OR vehicle_key LIKE 'vin:Z__0000%' OR (vehicle_key LIKE 'fp:%' AND raw LIKE '%"vin": "Z__0000%')""").fetchall()
        for uid, vk, model, raw in rows:
            if vk.startswith("chassis:"):
                new = f"{vk.rsplit(':', 1)[0]}:{chassis_number(vk.rsplit(':', 1)[1])}"
            else:
                c = padded_chassis(vk[4:] if vk.startswith("vin:") else (json.loads(raw or "{}").get("vin") if raw else None))
                new = f"chassis:{model}:{c}" if c else vk
            if new != vk:
                con.execute(f"UPDATE {table} SET vehicle_key=? WHERE uid=?", (new, uid))
                moved[vk] = (new, model)
                n += 1
    # price history kept under a padded VIN that no current record uses any more
    for (vk,) in con.execute("SELECT DISTINCT vehicle_key FROM daily_asks WHERE vehicle_key LIKE 'vin:Z__0000%'").fetchall():
        c = padded_chassis(vk[4:])
        if not c or vk in moved:
            continue
        hit = con.execute("SELECT vehicle_key, model_id FROM sightings WHERE vehicle_key LIKE ? UNION ALL "
                          "SELECT vehicle_key, model_id FROM lots WHERE vehicle_key LIKE ? LIMIT 1",
                          (f"chassis:%:{c}", f"chassis:%:{c}")).fetchone()
        if hit:
            moved[vk] = (hit[0], hit[1])
    for old, (new, model) in moved.items():
        con.execute("UPDATE OR REPLACE daily_asks SET vehicle_key=?, model_id=? WHERE vehicle_key=?", (new, model, old))
    _remember_moves(con, {old: new for old, (new, _) in moved.items()})
    return n


def _reclassify(con) -> int:
    """Stored cars the current taxonomy names differently: those parked in the catch all bucket, and those
    whose title names a model of another family altogether ("365 GTC4" once read as a GTC4Lusso). A change
    within one family is left alone, because the listing's own model field may have settled it."""
    n = 0
    for table in ("lots", "sightings"):
        for r in con.execute(f"SELECT uid, title, year, model_id, vehicle_key FROM {table}").fetchall():
            year = taxonomy.fixed_year(r["vehicle_key"]) or r["year"]
            m = taxonomy.resolve(r["title"] or "", year or extract_year(r["title"]))
            if not m.model or m.model.id == r["model_id"]:
                if year and year != r["year"]:
                    con.execute(f"UPDATE {table} SET year=? WHERE uid=?", (year, r["uid"]))
                continue
            old = taxonomy.BY_ID.get(r["model_id"])
            if r["model_id"] == "classic-other" and m.model.id != "classic-other" and m.confidence >= 0.5:
                pass
            elif not (old and m.model.family != old.family and m.confidence >= 1.0):
                continue
            vk = r["vehicle_key"] or ""
            if vk.startswith("chassis:"):
                vk = f"chassis:{m.model.id}:{vk.rsplit(':', 1)[1]}"     # a chassis key carries the model
            con.execute(f"UPDATE {table} SET model_id=?, vehicle_key=?, year=? WHERE uid=?",
                        (m.model.id, vk or r["vehicle_key"], year or r["year"], r["uid"]))
            if vk and vk != r["vehicle_key"]:
                _remember_moves(con, {r["vehicle_key"]: vk})
            n += 1
            if table == "sightings" and r["vehicle_key"]:
                # the asking price history moves with the car, out of the old model's medians
                con.execute("UPDATE OR REPLACE daily_asks SET model_id=?, vehicle_key=? WHERE vehicle_key=?",
                            (m.model.id, vk or r["vehicle_key"], r["vehicle_key"]))
    return n


def _tidy_titles(con) -> int:
    """One spelling of each model's name in stored titles (taxonomy.tidy_title)."""
    n = 0
    for table in ("lots", "sightings"):
        for r in con.execute(f"SELECT uid, title FROM {table} WHERE title IS NOT NULL").fetchall():
            t = taxonomy.tidy_title(r["title"])
            if t != r["title"]:
                con.execute(f"UPDATE {table} SET title=? WHERE uid=?", (t, r["uid"]))
                n += 1
    return n


def _drop_placeholder_photos(con) -> int:
    """A dealer's "photo coming soon" picture is not a photo of the car: the card shows no photo instead."""
    from .images import placeholder
    n = 0
    for table in ("lots", "sightings"):
        for r in con.execute(f"SELECT uid, image_url FROM {table} WHERE image_url IS NOT NULL").fetchall():
            if placeholder(r["image_url"]):
                con.execute(f"UPDATE {table} SET image_url=NULL WHERE uid=?", (r["uid"],))
                n += 1
    return n


# ---------------------------------------------------------------- specials and outliers
SPECIAL_WINDOW_DAYS = 730      # a sale is compared with its model's sales within two years either side
SPECIAL_MIN_SALES = 6
SPECIAL_FLOOR = 0.69           # ln 2: never an outlier within half to double the model's median
SPECIAL_MADS = 3.5             # and beyond 3.5 times the usual spread of the model's sales
_memo: dict = {}


def special_lots(con, gb: dict | None = None) -> dict[str, str]:
    """uid -> why a sale is not a standard example of its model, left out of medians, averages, trends, charts
    and fair values but still listed on the model's page with a Special tag:

      conversion, one_off, modified   named in the title or catalogue text (taxonomy.special_kind)
      outlier                         sold far from what the model (the same gearbox, for models sold with both)
                                      made within two years either side: beyond half or double the median and
                                      beyond 3.5 times the usual spread of its sales
    """
    key = (id(con), con.total_changes)
    if _memo.get("key") == key:
        return _memo["value"]
    if gb is None:
        from . import export
        gb = export.gearboxes(con)
    out: dict[str, str] = {}
    by: dict[tuple, list] = {}
    for r in con.execute("""SELECT uid, vehicle_key, model_id, title, status, price_usd, substr(ends_at,1,10) AS d, raw FROM lots
                            WHERE model_id IS NOT NULL AND model_id != 'classic-other'"""):
        text = ""
        if r["raw"] and ("conver" in r["raw"] or "swap" in r["raw"]):
            try:
                raw = json.loads(r["raw"])
                ex = raw.get("extra") or {}
                text = " ".join(str(x) for x in (ex.get("excerpt"), ex.get("subtitle"), ex.get("description"), raw.get("description")) if x)
            except ValueError:
                pass
        kind = taxonomy.special_kind(r["title"] or "", text)
        if kind:
            out[r["uid"]] = kind
            continue
        if r["status"] != "sold" or not r["price_usd"] or not r["d"]:
            continue
        try:
            d = date.fromisoformat(r["d"]).toordinal()
        except ValueError:
            continue
        vk = r["vehicle_key"] or ""
        k = vk if vk.startswith(("vin:", "chassis:")) else "uid:" + r["uid"]
        gear = (gb.get(k) or "f1") if r["model_id"] in taxonomy.DUAL_GEARBOX else ""
        by.setdefault((r["model_id"], gear), []).append((r["uid"], math.log(r["price_usd"]), d))
    for rows in by.values():
        for uid, lp, d in rows:
            near = [q for _, q, e in rows if abs(e - d) <= SPECIAL_WINDOW_DAYS]
            if len(near) < SPECIAL_MIN_SALES:
                continue
            med = st.median(near)
            mad = st.median(abs(q - med) for q in near) * 1.4826
            if abs(lp - med) > max(SPECIAL_FLOOR, SPECIAL_MADS * mad):
                out[uid] = "outlier"
    _memo.update(key=key, value=out)
    return out


def left_out(con, gb: dict | None = None) -> set[str]:
    """Every sale kept out of medians, averages, trends and charts: impossible prices and specials."""
    return implausible_lots(con) | set(special_lots(con, gb))


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
