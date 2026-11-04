

def test_listing_without_vin_joins_the_same_car_at_the_same_dealer(tmp_path):
    from pipeline import db, dedupe
    con = db.connect(tmp_path / "t.db")
    rows = [("ferrari_approved:1", "vin:ZFF00000000000001", "f8-spider", 2021, 3410, "Ferrari of Naples"),
            ("classiccars:a", "fp:aaaa", "f8-spider", 2021, 3420, "Ferrari of Naples"),        # same car
            ("classiccars:b", "fp:bbbb", "f8-spider", 2021, 9000, "Ferrari of Naples"),        # other miles
            ("classiccars:c", "fp:cccc", "f8-spider", 2021, 3410, "Ferrari of Fort Lauderdale"),            # other dealer
            ("ferrari_approved:2", "vin:ZFF00000000000002", "roma", 2022, 1000, "Ferrari of Fort Lauderdale"),
            ("ferrari_approved:3", "vin:ZFF00000000000003", "roma", 2022, 1010, "Ferrari Fort Lauderdale"),
            ("classiccars:d", "fp:dddd", "roma", 2022, 1005, "Ferrari of Fort Lauderdale")]                # two fit: left alone
    for uid, vk, mid, y, mi, d in rows:
        con.execute("INSERT INTO sightings(uid, source, vehicle_key, model_id, year, mileage_mi, dealer_name) VALUES (?,?,?,?,?,?,?)",
                    (uid, uid.split(":")[0], vk, mid, y, mi, d))
    assert dedupe.link_listings(con) == 1
    keys = dict(con.execute("SELECT uid, vehicle_key FROM sightings"))
    assert keys["classiccars:a"] == "vin:ZFF00000000000001"
    assert keys["classiccars:b"] == "fp:bbbb" and keys["classiccars:c"] == "fp:cccc" and keys["classiccars:d"] == "fp:dddd"
