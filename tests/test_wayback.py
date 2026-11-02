"""Listing dates from the Wayback Machine (pipeline/wayback.py)."""
from datetime import date, timedelta

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
    down.PAUSE = 0
    assert down.first_capture("classiccars.com/listings/view/1/") is None and not down.down   # one failure: go on
    assert db.kv_get(con, "wb:page:classiccars.com/listings/view/1/") is None    # not cached: tried again next run
    for i in range(2, 6):
        down.first_capture(f"classiccars.com/listings/view/{i}/")
    assert down.down and down.asked == 3                  # three in a row: the archive is down for this run


class ArchiveLookups:
    """A fake archive: the home page on day d shows listings up to 1,000,000 + 300 a day since 2023."""
    START = date(2023, 1, 1)

    def __init__(self):
        self.asked = self.answered = 0
        self.reads = []

    def captures(self, url):
        return [(self.START + timedelta(days=i)).strftime("%Y%m%d120000") for i in range(0, 1400, 3)]

    def snapshot(self, ts, url):
        self.reads.append(ts)
        d = date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))
        top = 1_000_000 + 300 * (d - self.START).days
        return " ".join(f'<a href="/listings/view/{top - k}/x">' for k in range(5))


def test_listing_number_clock_narrows_to_within_ten_days(tmp_path):
    from pipeline import idclock
    con = db.connect(tmp_path / "t.db")
    lk = ArchiveLookups()
    clock = idclock.Clock(con, lk, "classiccars", "https://classiccars.com/", r"/listings/view/(\d+)/", TODAY)
    clock.pin(TODAY.isoformat(), 1_000_000 + 300 * (TODAY - lk.START).days)
    listed = date(2024, 6, 15)
    day = clock.date_of(1_000_000 + 300 * (listed - lk.START).days)
    assert day and 0 <= (date.fromisoformat(day) - listed).days <= idclock.TOL
    n = len(lk.reads)
    assert n <= idclock.STEPS
    # a second car listed a few days later is dated from the pins already read
    day2 = clock.date_of(1_000_000 + 300 * (listed - lk.START).days + 600)
    assert day2 and len(lk.reads) <= n + 2
    # pins are kept for the next run
    again = idclock.Clock(con, lk, "classiccars", "https://classiccars.com/", r"/listings/view/(\d+)/", TODAY)
    assert again.pins == clock.pins
