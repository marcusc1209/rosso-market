import math
import random

from pipeline import fairvalue as F


def test_colour_and_miles_from_catalogue_text():
    t = ("This 2005 Ferrari F430 Spider is finished in Grigio Alloy Metallic over Blu Scuro leather. "
         "The seller has since added ~7,500 of the 20k miles indicated.")
    assert F.colour_from_text(t) == "Grigio Alloy Metallic"
    assert F.miles_from_text(t) == 20000
    assert F.miles_from_text("The odometer shows 12,904 miles.") == 12904
    assert F.colour_from_text("A car with no paint named") is None


def test_title_flags():
    assert F.title_flags("2004 Ferrari 360 Spider Project")["project"] == 1
    assert F.title_flags("2021 Ferrari 812 GTS") == {"project": 0}


def _car(model, year, miles, family, price, sold=1, months=3.0, house="bringatrailer", gear=None):
    return {"kind": "lot" if sold else "ask", "id": f"{model}{year}{miles}{price}", "key": None, "model": model, "year": year,
            "miles": miles, "paint": family, "family": family, "special": 0, "price": price, "sold": sold, "months": months,
            "house": house if sold else None, "gear": gear, "project": 0}


def test_fit_recovers_mileage_and_ranks_cars():
    rnd = random.Random(3)
    rows = []
    for model, base in (("a", 300000), ("b", 600000)):
        for i in range(60):
            miles = rnd.choice([1000, 3000, 8000, 15000, 30000])
            year = rnd.choice([2019, 2020, 2021])
            p = base * math.exp(-0.12 * math.log1p(miles / 1000) + 0.05 * (year - 2020) + rnd.gauss(0, 0.04))
            rows.append(_car(model, year, miles, rnd.choice(["red", "black", "grey"]), p, sold=int(i % 3 != 0)))
    f = F.Fit(rows)
    low = f.value(_car("a", 2020, 1000, "red", 0))
    high = f.value(_car("a", 2020, 30000, "red", 0))
    assert low["value"] > high["value"]
    assert 230000 < low["value"] < 330000
    assert low["low"] < low["value"] < low["high"]
    assert any(k == "Mileage" for k, _ in low["adjustments"])
    assert f.value(_car("zz", 2020, 1000, "red", 0)) is None


def test_colour_by_model_and_own_sale():
    rnd = random.Random(9)
    rows = []
    # yellow sells for more on model a, less on model b
    for model, yellow in (("a", 1.25), ("b", 0.8)):
        for i in range(120):
            fam = rnd.choice(["red", "yellow", "black"])
            p = 300000 * (yellow if fam == "yellow" else 1.0) * math.exp(rnd.gauss(0, 0.03))
            rows.append(_car(model, 2020, 5000, fam, p, months=rnd.uniform(0, 30)))
    # one car sold twice: well above the model, so its own sale should lift its value
    rows.append(dict(_car("a", 2020, 5000, "red", 360000, months=10), key="vin:ZFF00000000000001"))
    f = F.Fit(rows)
    an = F.Anchors(f)
    ya = f.value(_car("a", 2020, 5000, "yellow", 0))["value"] / f.value(_car("a", 2020, 5000, "red", 0))["value"]
    yb = f.value(_car("b", 2020, 5000, "yellow", 0))["value"] / f.value(_car("b", 2020, 5000, "red", 0))["value"]
    assert ya > 1.1 and yb < 0.9
    plain = f.value(dict(_car("a", 2020, 5000, "red", 0), key="vin:ZFF00000000000002"), an)["value"]
    known = f.value(dict(_car("a", 2020, 5000, "red", 0), key="vin:ZFF00000000000001"), an)
    assert known["value"] > plain and any(k == "This car's last sale" for k, _ in known["adjustments"])
