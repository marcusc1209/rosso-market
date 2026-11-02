"""Listing dates from CLASSIC.COM (pipeline/classic_com.py)."""
import json
from datetime import date

from pipeline import classic_com, db
from pipeline.records import RawRecord, LISTING

TODAY = date(2026, 11, 2)
VIN = "ZFF96NMAXS0313017"


def card(vin, status, day, dealer):
    return f"""<div class="card"><a href="/veh/2025-ferrari-sf90-spider-{vin.lower()}-W9RYoGp/"><img src="x.jpg"></a>
      <div><span>San Diego, California, USA</span><h3>2025 Ferrari SF90 Spider</h3><span>1k mi</span>
      <div>{status} <b>$835,961*</b></div><div>{day}</div><span>Updated 3 days ago</span><span>{dealer}</span></div></div>"""


def test_parse_takes_the_current_listing_of_the_same_vin():
    html = "<main>" + card(VIN, "For Sale", "Oct 30, 2026", "Ferrari of San Diego") \
        + card("ZFF96NMAXS0313079", "Price Undisclosed", "Apr 17, 2026", "Wide World Ferrari") + "</main>"
    assert classic_com.parse(html, VIN, TODAY) == "2026-10-30"
    assert classic_com.parse(html, "ZFF96NMAXS0313079", TODAY) is None          # not for sale: no date taken
    ended = "<main>" + card(VIN, "Last Asking", "Sep 16, 2025", "x") + "</main>"
    assert classic_com.parse(ended, VIN, TODAY) is None                          # an earlier time on the market
    pending = "<main>" + card(VIN, "Pending Sale", "Oct 3, 2026", "Ferrari of Naples") + "</main>"
    assert classic_com.parse(pending, VIN, TODAY) == "2026-10-03"
    assert classic_com.parse("<main>nothing</main>", VIN, TODAY) is None


class FakeFetcher:
    def __init__(self, *a, **k):
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        FakeFetcher.calls.append(url)
        return type("R", (), {"text": "<main>" + card(VIN, "For Sale", "Oct 30, 2026", "Ferrari of San Diego") + "</main>"})()

    def close(self):
        pass


def test_run_dates_undated_cars_once(tmp_path, monkeypatch):
    con = db.connect(tmp_path / "t.db")
    for uid, vk, extra in [("ferrari_approved:1", "vin:" + VIN, {}),
                           ("dupont_registry:2", "vin:ZFF00000000000002", {"listed_on": "2026-09-01"}),
                           ("dupont_registry:3", "vin:00000000000019145", {})]:      # not a real VIN: never asked
        con.execute("INSERT INTO sightings(uid, source, vehicle_key, raw) VALUES (?,?,?,?)",
                    (uid, uid.split(":")[0], vk, json.dumps({"extra": extra})))
    FakeFetcher.calls = []
    monkeypatch.setattr(classic_com, "Fetcher", FakeFetcher)
    cfg = {"classic_com": {"enabled": True}}
    assert classic_com.run(con, cfg, tmp_path, TODAY) == 1
    assert FakeFetcher.calls == [classic_com.SEARCH.format(vin=VIN)]              # the dated car was not asked
    raw = json.loads(con.execute("SELECT raw FROM sightings WHERE uid='ferrari_approved:1'").fetchone()[0])
    assert raw["extra"] == {"listed_on": "2026-10-30", "listed_how": "classic.com"}
    assert classic_com.run(con, cfg, tmp_path, TODAY) == 0 and len(FakeFetcher.calls) == 1   # asked once
    # later runs give the stored date to the fresh record before it is saved
    rec = RawRecord(source="ferrari_approved", kind=LISTING, source_id="1", url="u", title="t", vin=VIN)
    classic_com.apply(rec, classic_com.known(con))
    assert rec.extra["listed_on"] == "2026-10-30"
