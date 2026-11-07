"""Daily pipeline entry point.

  python -m pipeline.run                       run every enabled source for today, then export
  python -m pipeline.run --only bringatrailer  one source (handy when fixing an adapter)
  python -m pipeline.run --date 2026-10-30     run as of a past date
  python -m pipeline.run --backfill 120        replay the last 120 days (demo source only)
  python -m pipeline.run --export-only         rebuild the site JSON from the database
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import yaml

from . import classic_com, clean, db, dedupe, export, overrides, tracker, health as H
from .dedupe import vehicle_key
from .fetch import Fetcher
from .normalize import normalize, Reject
from .sources import ADAPTERS
from .sources.base import Context
from .sources.news import fetch_news

log = logging.getLogger("pipeline")


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("settings", {})
    return cfg


def fx_rates(cfg: dict, fetcher: Fetcher | None) -> dict:
    fx = {k.upper(): float(v) for k, v in cfg["settings"].get("fx", {"USD": 1.0}).items()}
    if cfg["settings"].get("fx_live") and fetcher:
        try:
            data = fetcher.get_json(cfg["settings"].get("fx_url", "https://api.frankfurter.app/latest?from=USD"))
            for cur, per_usd in data.get("rates", {}).items():
                fx[cur.upper()] = 1 / float(per_usd)
            fx["USD"] = 1.0
        except Exception as e:
            log.warning("live FX unavailable (%s), using the static table", e)
    return fx


def build_sources(cfg: dict, day: date, only: set | None, where: str = "all"):
    settings = cfg["settings"]
    raw_root = Path(settings.get("raw_dir", "data/raw"))
    out = []
    for name, scfg in (cfg.get("sources") or {}).items():
        if only and name not in only:
            continue
        if not only and not scfg.get("enabled", False):
            continue
        # runner: home marks sources that refuse data center addresses and run on the owner's Mac
        runner = scfg.get("runner", "cloud")
        if not only and where != "all" and runner != where:
            continue
        adapter = ADAPTERS.get(scfg.get("adapter"))
        if adapter is None:
            log.error("source %s: unknown adapter %r", name, scfg.get("adapter"))
            continue
        fcfg = {**settings, **(scfg.get("fetch") or {})}
        fetcher = Fetcher(fcfg, raw_root / day.isoformat(), name)
        ctx = Context(fetcher=fetcher, run_date=day, llm_cfg=cfg.get("llm_fallback") or {})
        out.append((name, adapter(name, scfg, ctx), ctx))
    return out


def run_source(con, name, source, ctx, day: date, fx: dict, cfg: dict) -> dict:
    # Some houses (RM Sotheby's) publish results without a sale date. After a source's first
    # successful run, a result that appears for the first time is dated by the run day.
    # Results present on the first import stay undated and are kept out of time based stats.
    has_history = con.execute("SELECT 1 FROM runs WHERE source=? AND status='ok' LIMIT 1", (name,)).fetchone() is not None
    run_id = db.start_run(con, day, name)
    fetched = accepted = rejected = 0
    t0 = time.time()
    classic = classic_com.known(con)
    entered = cfg.get("_listing_history") or {}
    try:
        for raw in source.fetch():
            fetched += 1
            classic_com.apply(raw, classic)
            overrides.apply(raw, entered)
            if raw.kind == "auction" and raw.ends_at is None and raw.status in ("sold", "unsold") \
                    and not (raw.extra or {}).get("keep_undated"):
                known = con.execute("SELECT ends_at FROM lots WHERE uid=?", (f"{raw.source}:{raw.source_id}",)).fetchone()
                old_sale = clean.rm_code_year(raw.url) is not None and clean.rm_code_year(raw.url) < day.year - 1
                if known is None and has_history and not old_sale:
                    raw.ends_at = f"{day.isoformat()}T12:00:00+00:00"
                    raw.extra["date_inferred"] = True
            try:
                n = normalize(raw, fx, float(cfg["settings"].get("min_model_confidence", 0.5)))
            except Reject as e:
                rejected += 1
                db.log_reject(con, run_id, name, raw.source_id, raw.title, str(e))
                continue
            n.vehicle_key = vehicle_key(n)
            db.upsert(con, n, day)
            accepted += 1
        db.finish_run(con, run_id, "ok", fetched, accepted, rejected, ctx.fetcher.requests)
    except Exception as e:
        log.exception("source %s failed", name)
        con.commit()
        db.finish_run(con, run_id, "error", fetched, accepted, rejected, ctx.fetcher.requests, f"{type(e).__name__}: {e}"[:500])
    report = H.check(con, name, run_id)
    if report["state"] == "ok":
        removed = db.mark_missing(con, name, day, int(cfg["settings"].get("removal_after_missed_runs", 2)))
        report["removed"] = removed
    else:
        con.execute("UPDATE runs SET status=? WHERE id=?", (report["state"], run_id))
    con.commit()
    log.info("%-18s %-8s fetched=%d accepted=%d rejected=%d %.1fs %s", name, report["state"], fetched, accepted,
             rejected, time.time() - t0, report["detail"])
    return report


def run_day(con, cfg, day: date, sources, fx, where: str = "all") -> list[dict]:
    reports = []
    for name, src, ctx in sources:
        ctx.run_date = day
        reports.append(run_source(con, name, src, ctx, day, fx, cfg))
    log.info("%d listings without a VIN linked to the same car at the same dealer", dedupe.link_listings(con))
    if where == "all" or (cfg.get("classic_com") or {}).get("runner", "cloud") == where:
        try:
            classic_com.run(con, cfg, Path(cfg["settings"].get("raw_dir", "data/raw")) / day.isoformat(), day)
        except Exception as e:                      # listing dates are a nicety: never fail the run
            log.warning("CLASSIC.COM listing dates skipped: %s", e)
    n = db.build_daily_asks(con, day)
    log.info("%s: %d tracked cars in snapshot", day, n)
    return reports


def already_ran(con, day: date, names: list) -> bool:
    """GitHub starts scheduled workflows hours late, so each day's runs are started on time from
    outside GitHub (a scheduled task dispatches them). The schedule in the workflow stays as a backup,
    and when it fires after that day's run has happened, it skips instead of running twice."""
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
        return False
    row = con.execute(f"SELECT COUNT(*) FROM runs WHERE day=? AND source IN ({','.join('?' * len(names))})",
                      (day.isoformat(), *names)).fetchone()
    return bool(row and row[0])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.environ.get("FM_CONFIG", "config/sources.yaml"))
    ap.add_argument("--date", default=None)
    ap.add_argument("--only", default=None, help="comma separated source names")
    ap.add_argument("--backfill", type=int, default=0)
    ap.add_argument("--export-only", action="store_true")
    ap.add_argument("--where", choices=["all", "cloud", "home"], default="all",
                    help="cloud: GitHub's servers, home: sources marked runner: home (the Mac)")
    ap.add_argument("--report", default=None, help="write each source's health as JSON to this path")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

    cfg = load_config(a.config)
    cfg["_listing_history"] = overrides.load(a.config)      # dates and price histories entered by hand
    tcfg = (cfg.get("settings") or {}).get("tracker") or {}
    if tcfg.get("sheet_id") and tcfg.get("gids"):           # the owner's CarGurus tracker, when the sheet is shared publicly
        try:
            n = tracker.fetch_sheet(tcfg["sheet_id"], tcfg["gids"], Path(a.config).with_name("tracker"))
            if n:
                log.info("tracker: %d tabs read from the sheet", n)
        except Exception as e:                               # the stored tabs are used instead
            log.info("tracker sheet not read: %s", e)
    cfg["_tracker"] = tracker.load(a.config)
    hist = (cfg.get("settings") or {}).get("history_import")
    if os.environ.get("FM_HISTORY") and hist:
        # one off import of older auction results, only for the sources settings.history_import names
        for name, over in hist.items():
            scfg = (cfg.get("sources") or {}).get(name)
            if scfg is not None:
                scfg.update(over or {})
                log.info("history import: %s %s", name, over)
    elif os.environ.get("FM_HISTORY"):
        # one off import of older auction results: deeper paging on every results feed
        for scfg in (cfg.get("sources") or {}).values():
            for k in ("result_pages", "sold_pages"):
                if k in scfg:
                    scfg[k] = max(int(scfg[k]), 60)
            if "lookback_days" in scfg:
                scfg["lookback_days"] = 1500
            if "lot_pages" in scfg:
                scfg["lot_pages"] = max(int(scfg["lot_pages"]), 600)
    day = date.fromisoformat(a.date) if a.date else date.today()
    con = db.connect(Path(cfg["settings"].get("db", "data/ferrari.db")))
    if a.export_only and not a.date:
        # republishing: build the site for the last day the market was collected, not the calendar
        # day (a republish just after midnight UTC would otherwise show no cars for sale)
        last = con.execute("SELECT MAX(day) FROM daily_asks").fetchone()[0]
        if last:
            day = date.fromisoformat(last)
    clean.purge(con)        # stored rows that today's filters would reject

    reports: list[dict] = []
    only = set(a.only.split(",")) if a.only else None
    if not a.export_only:
        sources = build_sources(cfg, day, only, a.where)
        for _, _, ctx in sources:
            ctx.con = con
        if not sources:
            log.error("no sources enabled")
            return 1
        if already_ran(con, day, [s[0] for s in sources]):
            # GitHub's late scheduled run: the day's run already happened, so only republish
            log.info("these sources already ran today (%s): this late scheduled run only republishes", day)
            for _, _, ctx in sources:
                ctx.close()
            a.export_only = True
    if not a.export_only:
        fx = fx_rates(cfg, sources[0][2].fetcher if cfg["settings"].get("fx_live") else None)
        for back in range(a.backfill, -1, -1):
            reports = run_day(con, cfg, day - timedelta(days=back), sources, fx, a.where)
        feeds = cfg.get("news_feeds") or []
        if feeds and not only and a.where != "home":
            try:
                db.save_news(con, list(fetch_news(sources[0][2].fetcher, feeds)), day)
            except Exception as e:
                log.warning("news feeds failed: %s", e)
        for _, _, ctx in sources:
            ctx.close()
        from .fetch import Browser
        Browser.shutdown()
    else:
        # only sources that are switched on: retired and disabled ones (autotrader_ca, autotrader_us) stay off the site
        live = {n for n, sc in (cfg.get("sources") or {}).items() if sc.get("enabled")}
        reports = [dict(source=r["source"], state=r["status"], detail=r["error"] or "", accepted=r["accepted"])
                   for r in con.execute("SELECT * FROM runs WHERE id IN (SELECT MAX(id) FROM runs GROUP BY source)")
                   if r["source"] in live]

    if cfg["_listing_history"]:
        log.info("%d stored listings updated from listing_history.yaml", overrides.apply_db(con, cfg["_listing_history"]))
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(__import__("json").dumps(reports, default=str, indent=1))
    out = export.write(con, day, cfg, reports)
    log.info("wrote %s", out)
    bad = [r for r in reports if r["state"] != "ok"]
    if bad:
        log.warning("attention needed: %s", ", ".join(f"{r['source']} ({r['state']}: {r['detail']})" for r in bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
