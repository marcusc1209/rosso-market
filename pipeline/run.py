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

from . import db, export, health as H
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


def build_sources(cfg: dict, day: date, only: set | None):
    settings = cfg["settings"]
    raw_root = Path(settings.get("raw_dir", "data/raw"))
    out = []
    for name, scfg in (cfg.get("sources") or {}).items():
        if only and name not in only:
            continue
        if not only and not scfg.get("enabled", False):
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
    try:
        for raw in source.fetch():
            fetched += 1
            if raw.kind == "auction" and raw.ends_at is None and raw.status in ("sold", "unsold"):
                known = con.execute("SELECT ends_at FROM lots WHERE uid=?", (f"{raw.source}:{raw.source_id}",)).fetchone()
                if known is None and has_history:
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


def run_day(con, cfg, day: date, sources, fx) -> list[dict]:
    reports = []
    for name, src, ctx in sources:
        ctx.run_date = day
        reports.append(run_source(con, name, src, ctx, day, fx, cfg))
    n = db.build_daily_asks(con, day)
    log.info("%s: %d tracked cars in snapshot", day, n)
    return reports


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.environ.get("FM_CONFIG", "config/sources.yaml"))
    ap.add_argument("--date", default=None)
    ap.add_argument("--only", default=None, help="comma separated source names")
    ap.add_argument("--backfill", type=int, default=0)
    ap.add_argument("--export-only", action="store_true")
    ap.add_argument("--report", default=None, help="write each source's health as JSON to this path")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

    cfg = load_config(a.config)
    if os.environ.get("FM_HISTORY"):
        # one off import of older auction results: deeper paging on every results feed
        for scfg in (cfg.get("sources") or {}).values():
            for k in ("result_pages", "sold_pages"):
                if k in scfg:
                    scfg[k] = max(int(scfg[k]), 60)
            if "lookback_days" in scfg:
                scfg["lookback_days"] = 1500
    day = date.fromisoformat(a.date) if a.date else date.today()
    con = db.connect(Path(cfg["settings"].get("db", "data/ferrari.db")))

    reports: list[dict] = []
    if not a.export_only:
        only = set(a.only.split(",")) if a.only else None
        sources = build_sources(cfg, day, only)
        if not sources:
            log.error("no sources enabled")
            return 1
        fx = fx_rates(cfg, sources[0][2].fetcher if cfg["settings"].get("fx_live") else None)
        for back in range(a.backfill, -1, -1):
            reports = run_day(con, cfg, day - timedelta(days=back), sources, fx)
        feeds = cfg.get("news_feeds") or []
        if feeds and not only:
            try:
                db.save_news(con, list(fetch_news(sources[0][2].fetcher, feeds)), day)
            except Exception as e:
                log.warning("news feeds failed: %s", e)
        for _, _, ctx in sources:
            ctx.close()
        from .fetch import Browser
        Browser.shutdown()
    else:
        reports = [dict(source=r["source"], state=r["status"], detail=r["error"] or "", accepted=r["accepted"])
                   for r in con.execute("SELECT * FROM runs WHERE id IN (SELECT MAX(id) FROM runs GROUP BY source)")]

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
