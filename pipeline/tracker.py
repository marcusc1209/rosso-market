"""The owner's CarGurus price tracker (a Google Sheet, one tab per model).

Each tab lists every VIN seen on CarGurus for one model, with one asking price column per weekly
check since Mar 2, 2026. A blank cell means the car was not listed that week, "No Price Listed"
means it was listed without a price. Prices are CarGurus totals (asking price plus fees).

The tabs are kept in config/tracker/<model id>.csv in a compact form:

    Year,VIN,Mileage,Location,2026-03-02,2026-03-09,...
    2007,ZFFFC60A570150565,21962,"Tampa, FL",,,299581,...,NP

where NP is a listing without a price. Refresh them from a workbook export or from per tab CSVs:

    python -m pipeline.tracker path/to/export.txt [more files]

and, when the sheet can be read without signing in, each run fetches the tabs itself
(settings.tracker in config/sources.yaml).
"""
from __future__ import annotations

import csv
import io
import logging
import re
import sys
from datetime import date
from pathlib import Path

log = logging.getLogger("tracker")

# tab name in the sheet -> model id on the site
TABS = {
    "599 GTB": "599-gtb", "F12 Berlinetta": "f12berlinetta", "812 Superfast": "812-superfast", "812 GTS": "812-gts",
    "12 Cilindri Coupe": "12cilindri", "F430 Coupe": "f430", "458 Spider": "458-spider", "F8 Spider": "f8-spider",
    "488 Pista": "488-pista", "488 Pista Spider": "488-pista-spider", "458 Speciale": "458-speciale",
    "458 Speciale Aperta": "458-speciale-a", "599 GTO": "599-gto", "F12 TDF": "f12tdf",
    "812 Competizione": "812-competizione", "812 Competizione Aperta": "812-competizione-a",
}


def model_for_tab(name: str):
    """The site model a tab tracks: the names above first, then any tab named after a model ("488 Spider",
    "F8 Tributo", "296 GTB"), so a new tab is imported without a code change."""
    if name in TABS:
        return TABS[name]
    from . import taxonomy
    m = taxonomy.resolve(f"Ferrari {name}", None)
    return m.model.id if m and getattr(m, "model", None) else None


START_YEAR = 2026            # the first check (3/2) was in 2026. Later headers roll the year over in January.
NP = "NP"
_HEAD = re.compile(r"^\s*(\d{1,2})/(\d{1,2})\s+Asking Price\s*$", re.I)
_VIN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


def _num(s: str):
    s = (s or "").strip()
    if not s:
        return None
    if s.lower().startswith("no price"):
        return NP
    d = re.sub(r"[^\d.]", "", s)
    return int(float(d)) if d else None


def parse_rows(rows) -> tuple[list[str], list[dict]]:
    """Find the weekly block in one tab's rows: its dated header and the VIN rows under it."""
    weeks, cars, year, last_month = [], [], START_YEAR, 0
    cols = None
    for r in rows:
        if cols is None:
            if len(r) > 4 and [c.strip() for c in r[:4]] == ["Year", "VIN", "Mileage", "Location"] and _HEAD.match(r[4] or ""):
                cols = []
                for i, c in enumerate(r[4:], start=4):
                    m = _HEAD.match(c or "")
                    if not m:
                        continue
                    mo, dy = int(m.group(1)), int(m.group(2))
                    if mo < last_month:
                        year += 1
                    last_month = mo
                    cols.append((i, date(year, mo, dy).isoformat()))
                weeks = [d for _, d in cols]
            continue
        if len(r) < 2 or not _VIN.match((r[1] or "").strip().upper()):
            if cars and not any((c or "").strip() for c in r):
                break
            continue
        prices = {d: _num(r[i]) if i < len(r) else None for i, d in cols}
        cars.append({"year": (r[0] or "").strip(), "vin": r[1].strip().upper(), "mileage": _num(r[2]) if len(r) > 2 else None,
                     "location": (r[3] or "").strip() if len(r) > 3 else "",
                     "prices": {d: p for d, p in prices.items() if p is not None}})
    return weeks, cars


def split_export(text: str) -> dict[str, list[list[str]]]:
    """A workbook export: '## Sheet name: <tab>' then that tab's rows as CSV."""
    tabs, name, buf = {}, None, []
    for line in text.splitlines():
        if line.startswith("## Sheet name:"):
            if name:
                tabs[name] = list(csv.reader(io.StringIO("\n".join(buf))))
            name, buf = line.split(":", 1)[1].strip(), []
        else:
            buf.append(line)
    if name:
        tabs[name] = list(csv.reader(io.StringIO("\n".join(buf))))
    return tabs


def write_tab(out_dir: Path, model_id: str, weeks: list[str], cars: list[dict]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{model_id}.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Year", "VIN", "Mileage", "Location", *weeks])
        for c in cars:
            w.writerow([c["year"], c["vin"], c["mileage"] if c["mileage"] not in (None, NP) else "", c["location"],
                        *[c["prices"].get(d, "") for d in weeks]])
    return p


def load(config_path) -> dict[str, list[dict]]:
    """model id -> cars, each with prices {week day: price or NP}."""
    d = Path(config_path).with_name("tracker")
    out = {}
    for p in sorted(d.glob("*.csv")) if d.exists() else []:
        rows = list(csv.reader(p.open(encoding="utf-8")))
        if not rows:
            continue
        weeks = rows[0][4:]
        cars = []
        for r in rows[1:]:
            if len(r) < 2 or not r[1]:
                continue
            prices = {}
            for wk, v in zip(weeks, r[4:]):
                if v == NP:
                    prices[wk] = NP
                elif v:
                    prices[wk] = int(v)
            cars.append({"vin": r[1].upper(), "year": r[0], "location": r[3], "prices": prices})
        out[p.stem] = cars
    return out


def fetch_sheet(sheet_id: str, gids: dict, out_dir: Path) -> int:
    """Read each tab from Google Sheets as CSV. Works only when the sheet is shared to anyone with the link."""
    import httpx
    n = 0
    for tab, gid in gids.items():
        mid = model_for_tab(tab)
        if not mid:
            continue
        url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
        try:
            r = httpx.get(url, follow_redirects=True, timeout=30)
        except httpx.HTTPError as e:
            log.info("tracker sheet not reached: %s", e)
            return n
        if r.status_code != 200 or "text/csv" not in r.headers.get("content-type", ""):
            log.info("tracker sheet is not public (HTTP %s), keeping the stored tabs", r.status_code)
            return n
        weeks, cars = parse_rows(csv.reader(io.StringIO(r.text)))
        if weeks and cars:
            write_tab(out_dir, mid, weeks, cars)
            n += 1
    return n


def main(argv):
    out = Path(__file__).resolve().parent.parent / "config" / "tracker"
    for f in argv:
        text = Path(f).read_text(encoding="utf-8")
        tabs = split_export(text) if "## Sheet name:" in text else {Path(f).stem: list(csv.reader(io.StringIO(text)))}
        for name, rows in tabs.items():
            mid = model_for_tab(name)
            if not mid:
                continue
            weeks, cars = parse_rows(rows)
            if weeks and cars:
                p = write_tab(out, mid, weeks, cars)
                print(f"{name}: {len(cars)} cars, {len(weeks)} weeks ({weeks[0]} to {weeks[-1]}) -> {p.name}")


if __name__ == "__main__":
    main(sys.argv[1:])
