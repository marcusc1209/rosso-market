from pipeline import health, taxonomy as T


def test_road_cars_named_like_race_cars_or_manuals_are_kept():
    for t in ("1961 Ferrari 250 GTE", "1963 Ferrari 250 GTE 2+2 Series III", "1966 Ferrari 330 GTE",
              '2007 Ferrari 599 GTB Fiorano "Six-Speed Manual"', '2005 Ferrari 612 Scaglietti "Six-Speed Manual"',
              "1999 Ferrari F355 Spider with gated gearbox", "2006 Ferrari F430 Factory Manual"):
        assert T.looks_like_car(t) and not T.is_track_car(t), t


def test_parts_race_cars_and_literature_still_turned_away():
    assert T.is_track_car("2019 Ferrari 488 GTE") and T.is_track_car("1997 Ferrari F355 Challenge")
    for t in ("Ferrari F40 owner's manual", "Ferrari 355 gearbox", "Ferrari 250 GTE 2+2 Body",
              "2025 Ferrari Daytona SP3 Speedform Model", "Hublot Titanium MP-05 LaFerrari Watch"):
        assert not T.looks_like_car(t), t


def test_deliberate_rejects_do_not_degrade_a_source():
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, source TEXT, status TEXT, fetched INT, accepted INT, error TEXT)")
    con.execute("CREATE TABLE rejects (run_id INT, source TEXT, source_id TEXT, title TEXT, reason TEXT)")
    con.execute("INSERT INTO runs VALUES (1, 'ba', 'ok', 26, 20, NULL)")
    con.executemany("INSERT INTO rejects VALUES (1, 'ba', ?, 't', ?)",
                    [(str(i), r) for i, r in enumerate(["track only car"] * 4 + ["not a car (parts, memorabilia, replica)"] * 2)])
    assert health.check(con, "ba", 1)["state"] == "ok"
    con.execute("INSERT INTO runs VALUES (2, 'ba', 'ok', 26, 10, NULL)")
    con.executemany("INSERT INTO rejects VALUES (2, 'ba', ?, 't', 'unknown model')", [(str(i),) for i in range(16)])
    assert health.check(con, "ba", 2)["state"] == "degraded"


def test_812_competizione_names_with_misspellings_and_short_forms():
    mid = lambda t, y: T.resolve(t, y).model.id
    assert mid("2022 Ferrari 812 Competitzione", 2022) == "812-competizione"
    assert mid("2022 Ferrari 812 Comp", 2022) == "812-competizione"
    assert mid("2023 Ferrari 812 Comp A", 2023) == "812-competizione-a"
    assert mid("2023 Ferrari 812 Competizione Aperta", 2023) == "812-competizione-a"
    assert mid("2018 Ferrari 812 Superfast Competition Package", 2018) == "812-superfast"
