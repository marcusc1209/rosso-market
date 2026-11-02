"""Listing dates from the Wayback Machine (pipeline/wayback.py)."""
from datetime import date

from pipeline import db, wayback

TODAY = date(2026, 11, 2)


def test_listing_start_takes_the_latest_unbroken_stretch():
    assert wayback.listing_start(["2026-08-01", "2026-09-15", "2026-10-30"], TODAY) == "2026-08-01"
    # listed in 2024, sold, back in 2026: dated 2026
    assert wayback.listing_start(["2024-03-01", "2024-05-01", "2026-09-01"], TODAY) == "2026-09-01"
    # nothing archived for over half a year: no archive date
    assert wayback.listing_start(["2025-01-10"], TODAY) is None
    assert wayback.listing_start([], TODAY) is None


def test_ferrari_approved_ads_carry_vin_and_creation_time():
    rows = [("20250715095331", "https://preowned.ferrari.com/en-US/a/north-america/used-ferrari/usa/boardwalk-ferrari/296-gtb/ZFF99SLA2R0300845-1724919183952"),
            ("20251117005929", "https://preowned.ferrari.com/en-US/a/north-america/used-ferrari/usa/boardwalk-ferrari/296-gtb/ZFF99SLA2R0300845-1760955150531"),
            ("20260101000000", "https://preowned.ferrari.com/en-US/a/north-america/used-ferrari/usa/boardwalk-ferrari/296-gtb/IljixbcgVnJ")]
    ev = wayback.fa_evidence(rows)
    assert list(ev) == ["ZFF99SLA2R0300845"]
    assert sorted(ev["ZFF99SLA2R0300845"]) == ["2024-08-29", "2025-07-15", "2025-10-20", "2025-11-17"]


class FakeFetcher:
    def __init__(self, rows=None, fail=False):
        self.rows, self.fail, self.calls = rows or [], fail, 0

    def get_json(self, url):
        self.calls += 1
        if self.fail:
            raise RuntimeError("503 Temporarily Offline")
        return [["timestamp", "original"], *self.rows]


def test_lookups_ask_once_and_retry_when_the_archive_is_down(tmp_path):
    con = db.connect(tmp_path / "t.db")
    url = "https://preowned.ferrari.com/en-US/a/north-america/used-ferrari/usa/boardwalk-ferrari/296-gtb/ZFF99SLA2R0300845-"
    f = FakeFetcher([["20260801000000", url + "1785000000000"], ["20261020000000", url + "1785000000000"]])
    lk = wayback.Lookups(con, f)
    assert lk.ferrari_approved("usa", "boardwalk-ferrari", "ZFF99SLA2R0300845", TODAY) == "2026-07-25"
    assert lk.ferrari_approved("usa", "boardwalk-ferrari", "ZFF99SLA2R0300845", TODAY) == "2026-07-25"
    assert f.calls == 1                                   # the dealer's archive was read once
    down = wayback.Lookups(con, FakeFetcher(fail=True))
    assert down.first_capture("classiccars.com/listings/view/1/") is None and down.down
    assert db.kv_get(con, "wb:page:classiccars.com/listings/view/1/") is None    # not cached: tried again next run
