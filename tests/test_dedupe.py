

def test_listing_without_vin_joins_the_same_car_at_the_same_dealer(tmp_path):
    from pipeline import db, dedupe
    con = db.connect(tmp_path / "t.db")
    rows = [("ferrari_approved:1", "vin:ZFF00000000000001", "f8-spider", 2021, 3410, "Ferrari of Alberta"),
            ("autotrader_ca:a", "fp:aaaa", "f8-spider", 2021, 3420, "Ferrari of Alberta"),        # same car
            ("autotrader_ca:b", "fp:bbbb", "f8-spider", 2021, 9000, "Ferrari of Alberta"),        # other miles
            ("autotrader_ca:c", "fp:cccc", "f8-spider", 2021, 3410, "Ferrari Québec"),            # other dealer
            ("ferrari_approved:2", "vin:ZFF00000000000002", "roma", 2022, 1000, "Ferrari Québec"),
            ("ferrari_approved:3", "vin:ZFF00000000000003", "roma", 2022, 1010, "Ferrari Quebec"),
            ("autotrader_ca:d", "fp:dddd", "roma", 2022, 1005, "Ferrari Québec")]                # two fit: left alone
    for uid, vk, mid, y, mi, d in rows:
        con.execute("INSERT INTO sightings(uid, source, vehicle_key, model_id, year, mileage_mi, dealer_name) VALUES (?,?,?,?,?,?,?)",
                    (uid, uid.split(":")[0], vk, mid, y, mi, d))
    assert dedupe.link_listings(con) == 1
    keys = dict(con.execute("SELECT uid, vehicle_key FROM sightings"))
    assert keys["autotrader_ca:a"] == "vin:ZFF00000000000001"
    assert keys["autotrader_ca:b"] == "fp:bbbb" and keys["autotrader_ca:c"] == "fp:cccc" and keys["autotrader_ca:d"] == "fp:dddd"
