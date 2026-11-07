import math
import random
from datetime import date

from pipeline import fairvalue as F, pulse


def test_sales_change_sees_a_price_rise_not_a_mix_change():
    rnd = random.Random(5)
    rows = []
    for i in range(240):
        days = rnd.uniform(0, 720)
        miles = rnd.choice([1000, 5000, 20000])
        # prices 10% higher in the last 90 days than the 90 before, for every mileage
        lift = 1.10 if days <= 90 else 1.0
        p = 400000 * lift * math.exp(-0.1 * math.log1p(miles / 1000) + rnd.gauss(0, 0.03))
        rows.append({"kind": "lot", "id": str(i), "key": None, "model": "f8-tributo", "year": 2021, "miles": miles, "paint": "red",
                     "family": "red", "special": 0, "price": p, "sold": 1, "months": days / 30.44, "house": "bringatrailer",
                     "gear": None, "project": 0})
    fit = F.Fit(rows)
    out = pulse.sales_change(fit, date(2026, 11, 6), "90d", {}, {})
    assert out["basis"] == "sold"
    assert out["all"] and 5 < out["all"][0]["change"] < 15
    ix = pulse.index(fit, date(2026, 11, 6))
    assert ix and ix[0][1] == 100.0
