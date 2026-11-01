"""Ferrari model taxonomy.

Every record that enters the pipeline is resolved to one canonical model id here.
Patterns are tried in order, so the most specific variant of a model must come
before the general one (488 Pista Spider before 488 Pista before 488 Spider
before 488 GTB). Add new models by appending a Model row in the right place.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Model:
    id: str
    name: str
    family: str          # used for grouping on the site
    body: str            # coupe, spider, targa, suv, gt
    years: tuple         # (first, last) model years, last=None if still built
    patterns: tuple      # regexes matched against the cleaned title
    pair: Optional[str] = None   # coupe <-> spider partner, for body style premiums
    tracked: bool = True         # include in movers / medians


FAMILY_ORDER = [
    "Mid engine V8",
    "Hybrid V6 and V8",
    "Front engine V12",
    "Front engine V8 GT",
    "Four seat GT and SUV",
    "Hypercars and limited series",
    "Classics",
]

M = Model
MODELS: list[Model] = [
    # Hypercars and limited series (checked first: names like "SF90 XX" or "812 Competizione A")
    M("laferrari-aperta", "LaFerrari Aperta", "Hypercars and limited series", "spider", (2016, 2018), (r"laferrari aperta", r"la ferrari aperta"), "laferrari"),
    M("laferrari", "LaFerrari", "Hypercars and limited series", "coupe", (2013, 2016), (r"laferrari", r"la ferrari\b"), "laferrari-aperta"),
    M("f80", "F80", "Hypercars and limited series", "coupe", (2025, None), (r"\bf80\b",)),
    M("enzo", "Enzo", "Hypercars and limited series", "coupe", (2002, 2004), (r"\benzo\b",)),
    M("f50", "F50", "Hypercars and limited series", "spider", (1995, 1997), (r"\bf50\b",)),
    M("288-gto", "288 GTO", "Hypercars and limited series", "coupe", (1984, 1987), (r"288 gto",)),
    M("f40", "F40", "Hypercars and limited series", "coupe", (1987, 1992), (r"\bf40\b",)),
    M("fxx-k", "FXX K", "Hypercars and limited series", "coupe", (2015, 2017), (r"fxx ?k\b",), tracked=False),
    M("fxx", "FXX", "Hypercars and limited series", "coupe", (2005, 2007), (r"\bfxx\b",), tracked=False),
    M("daytona-sp3", "Daytona SP3", "Hypercars and limited series", "targa", (2022, 2024), (r"daytona sp3", r"\bsp3\b")),
    M("monza-sp2", "Monza SP2", "Hypercars and limited series", "spider", (2019, 2022), (r"monza sp2",)),
    M("monza-sp1", "Monza SP1", "Hypercars and limited series", "spider", (2019, 2022), (r"monza sp1",)),
    M("812-competizione-a", "812 Competizione A", "Hypercars and limited series", "targa", (2022, 2024), (r"812 competizione a\b", r"812 competizione aperta"), "812-competizione"),
    M("812-competizione", "812 Competizione", "Hypercars and limited series", "coupe", (2021, 2024), (r"812 competizione",), "812-competizione-a"),
    M("sf90-xx-spider", "SF90 XX Spider", "Hypercars and limited series", "spider", (2024, None), (r"sf90 xx spider", r"sf90 xx spyder"), "sf90-xx-stradale"),
    M("sf90-xx-stradale", "SF90 XX Stradale", "Hypercars and limited series", "coupe", (2024, None), (r"sf90 xx",), "sf90-xx-spider"),
    M("f12tdf", "F12tdf", "Hypercars and limited series", "coupe", (2016, 2017), (r"f12 ?tdf", r"tour de france")),
    M("599-gto", "599 GTO", "Hypercars and limited series", "coupe", (2010, 2012), (r"599 gto",)),
    M("sa-aperta", "SA Aperta", "Hypercars and limited series", "spider", (2010, 2011), (r"sa aperta",)),
    M("16m", "Scuderia Spider 16M", "Hypercars and limited series", "spider", (2009, 2009), (r"16m\b", r"scuderia spider")),

    # Hybrid V6 and V8
    M("849-testarossa-spider", "849 Testarossa Spider", "Hybrid V6 and V8", "spider", (2026, None), (r"849 testarossa spider",), "849-testarossa"),
    M("849-testarossa", "849 Testarossa", "Hybrid V6 and V8", "coupe", (2026, None), (r"849 testarossa", r"\b849\b"), "849-testarossa-spider"),
    M("sf90-spider", "SF90 Spider", "Hybrid V6 and V8", "spider", (2021, 2025), (r"sf90 spider", r"sf90 spyder"), "sf90-stradale"),
    M("sf90-stradale", "SF90 Stradale", "Hybrid V6 and V8", "coupe", (2020, 2025), (r"sf90",), "sf90-spider"),
    M("296-speciale-a", "296 Speciale A", "Hybrid V6 and V8", "spider", (2026, None), (r"296 speciale a\b",), "296-speciale"),
    M("296-speciale", "296 Speciale", "Hybrid V6 and V8", "coupe", (2026, None), (r"296 speciale",), "296-speciale-a"),
    M("296-gts", "296 GTS", "Hybrid V6 and V8", "spider", (2023, None), (r"296 gts",), "296-gtb"),
    M("296-gtb", "296 GTB", "Hybrid V6 and V8", "coupe", (2022, None), (r"296 gtb", r"\b296\b"), "296-gts"),

    # Mid engine V8
    M("f8-spider", "F8 Spider", "Mid engine V8", "spider", (2020, 2023), (r"f8 spider", r"f8 spyder", r"f8 tributo spider"), "f8-tributo"),
    M("f8-tributo", "F8 Tributo", "Mid engine V8", "coupe", (2020, 2023), (r"f8 tributo", r"\bf8\b"), "f8-spider"),
    M("488-pista-spider", "488 Pista Spider", "Mid engine V8", "spider", (2019, 2020), (r"488 pista spider", r"pista spider", r"488 pista spyder"), "488-pista"),
    M("488-pista", "488 Pista", "Mid engine V8", "coupe", (2019, 2020), (r"488 pista", r"\bpista\b"), "488-pista-spider"),
    M("488-spider", "488 Spider", "Mid engine V8", "spider", (2016, 2019), (r"488 spider", r"488 spyder"), "488-gtb"),
    M("488-gtb", "488 GTB", "Mid engine V8", "coupe", (2016, 2019), (r"488 gtb", r"\b488\b"), "488-spider"),
    M("458-speciale-a", "458 Speciale A", "Mid engine V8", "spider", (2015, 2015), (r"458 speciale a\b", r"speciale aperta"), "458-speciale"),
    M("458-speciale", "458 Speciale", "Mid engine V8", "coupe", (2014, 2015), (r"458 speciale",), "458-speciale-a"),
    M("458-spider", "458 Spider", "Mid engine V8", "spider", (2012, 2015), (r"458 spider", r"458 spyder", r"458 italia spider"), "458-italia"),
    M("458-italia", "458 Italia", "Mid engine V8", "coupe", (2010, 2015), (r"458 italia", r"\b458\b"), "458-spider"),
    M("430-scuderia", "430 Scuderia", "Mid engine V8", "coupe", (2008, 2009), (r"430 scuderia", r"f430 scuderia")),
    M("f430-spider", "F430 Spider", "Mid engine V8", "spider", (2005, 2009), (r"f430 spider", r"f430 spyder", r"430 spider"), "f430"),
    M("f430", "F430", "Mid engine V8", "coupe", (2005, 2009), (r"\bf430\b", r"\b430\b"), "f430-spider"),
    M("360-challenge-stradale", "360 Challenge Stradale", "Mid engine V8", "coupe", (2003, 2004), (r"challenge stradale",)),
    M("360-spider", "360 Spider", "Mid engine V8", "spider", (2001, 2005), (r"360 spider", r"360 spyder"), "360-modena"),
    M("360-modena", "360 Modena", "Mid engine V8", "coupe", (1999, 2005), (r"360 modena", r"\b360\b"), "360-spider"),

    # Front engine V12
    M("12cilindri-spider", "12Cilindri Spider", "Front engine V12", "spider", (2025, None), (r"12 ?cilindri spider",), "12cilindri"),
    M("12cilindri", "12Cilindri", "Front engine V12", "coupe", (2025, None), (r"12 ?cilindri",), "12cilindri-spider"),
    M("812-gts", "812 GTS", "Front engine V12", "spider", (2020, 2023), (r"812 gts",), "812-superfast"),
    M("812-superfast", "812 Superfast", "Front engine V12", "coupe", (2018, 2023), (r"812 superfast", r"\b812\b"), "812-gts"),
    M("f12berlinetta", "F12berlinetta", "Front engine V12", "coupe", (2013, 2017), (r"f12 ?berlinetta", r"\bf12\b")),
    M("599-gtb", "599 GTB Fiorano", "Front engine V12", "coupe", (2007, 2012), (r"599 gtb", r"\b599\b")),
    M("575m", "575M Maranello", "Front engine V12", "coupe", (2002, 2006), (r"\b575m?\b", r"superamerica")),
    M("550-barchetta", "550 Barchetta", "Front engine V12", "spider", (2001, 2001), (r"550 barchetta",)),
    M("550-maranello", "550 Maranello", "Front engine V12", "coupe", (1997, 2001), (r"\b550\b",)),

    # Front engine V8 GT
    M("amalfi", "Amalfi", "Front engine V8 GT", "coupe", (2026, None), (r"\bamalfi\b",)),
    M("roma-spider", "Roma Spider", "Front engine V8 GT", "spider", (2024, None), (r"roma spider", r"roma spyder"), "roma"),
    M("roma", "Roma", "Front engine V8 GT", "coupe", (2021, None), (r"\broma\b",), "roma-spider"),
    M("portofino-m", "Portofino M", "Front engine V8 GT", "spider", (2021, 2024), (r"portofino m\b",)),
    M("portofino", "Portofino", "Front engine V8 GT", "spider", (2018, 2020), (r"portofino",)),
    M("california-t", "California T", "Front engine V8 GT", "spider", (2015, 2017), (r"california t\b", r"california turbo")),
    M("california", "California", "Front engine V8 GT", "spider", (2009, 2014), (r"\bcalifornia\b(?! spider| spyder)",)),

    # Four seat GT and SUV
    M("luce", "Luce", "Four seat GT and SUV", "gt", (2026, None), (r"\bluce\b",), tracked=False),
    M("purosangue", "Purosangue", "Four seat GT and SUV", "suv", (2023, None), (r"purosangue",)),
    M("gtc4lusso-t", "GTC4Lusso T", "Four seat GT and SUV", "gt", (2017, 2020), (r"gtc4 ?lusso t\b",)),
    M("gtc4lusso", "GTC4Lusso", "Four seat GT and SUV", "gt", (2017, 2020), (r"gtc4 ?lusso", r"gtc4")),
    M("ff", "FF", "Four seat GT and SUV", "gt", (2012, 2016), (r"\bff\b",)),
    M("612-scaglietti", "612 Scaglietti", "Four seat GT and SUV", "gt", (2005, 2010), (r"\b612\b",)),
    M("456", "456 GT", "Four seat GT and SUV", "gt", (1994, 2003), (r"\b456m?\b",)),

    # Classics
    M("f355-spider", "F355 Spider", "Classics", "spider", (1995, 1999), (r"355 spider", r"355 spyder"), "f355"),
    M("f355", "F355", "Classics", "coupe", (1995, 1999), (r"\b(f ?)?355\b",), "f355-spider"),
    M("348", "348", "Classics", "coupe", (1990, 1995), (r"\b348\b",)),
    M("328", "328 GTB and GTS", "Classics", "targa", (1986, 1989), (r"\b328\b",)),
    M("308", "308 GTB and GTS", "Classics", "targa", (1975, 1985), (r"\b308 gt[bs]",  r"\b308 gtsi", r"\b308 qv", r"\b308\b(?! gt4)")),
    M("208-turbo", "208 GTB and GTS Turbo", "Classics", "targa", (1982, 1989), (r"\b208\b", r"\bgt[bs] turbo\b")),
    M("dino-308-gt4", "Dino 308 GT4", "Classics", "coupe", (1974, 1980), (r"308 gt4",)),
    M("dino-246", "Dino 246", "Classics", "coupe", (1969, 1974), (r"dino 246", r"246 gt", r"dino 206")),
    M("f512m", "F512 M", "Classics", "coupe", (1995, 1996), (r"f ?512 ?m\b",)),
    M("512-tr", "512 TR", "Classics", "coupe", (1992, 1994), (r"512 ?tr\b",)),
    M("testarossa", "Testarossa", "Classics", "coupe", (1985, 1991), (r"testarossa",)),
    M("512-bb", "365 GT4 BB and 512 BB", "Classics", "coupe", (1973, 1984), (r"512 ?bbi?\b", r"berlinetta boxer", r"365 gt4 ?bb", r"\bbb 512")),
    M("mondial", "Mondial", "Classics", "gt", (1980, 1993), (r"mondial",), tracked=False),
    M("daytona", "365 GTB/4 Daytona", "Classics", "coupe", (1968, 1973), (r"365 gt[bs] ?4", r"\bdaytona\b(?! sp3)")),
    M("330-gtc", "330 GTC and GTS", "Classics", "coupe", (1966, 1968), (r"330 gt[cs]",)),
    M("275", "275 GTB and GTS", "Classics", "coupe", (1964, 1968), (r"\b275\b",)),
    M("365-gt-2-2", "365 GT 2+2 and GTC/4", "Classics", "gt", (1967, 1972), (r"365 gt 2 2", r"365 2 2", r"365 gtc 4", r"365 gtc\b", r"365 gts\b(?! 4)"), tracked=False),
    M("330-gt-2-2", "330 GT 2+2", "Classics", "gt", (1964, 1967), (r"330 gt 2 2", r"330 2 2", r"330 gt\b", r"330 america"), tracked=False),
    M("400-412", "400 and 412", "Classics", "gt", (1976, 1989), (r"\b400i?\b", r"\b412\b"), tracked=False),
    M("250", "250 series", "Classics", "coupe", (1953, 1964), (r"\b250\b",), tracked=False),
    M("classic-other", "Other classics before 1975", "Classics", "coupe", (1947, 1975), (r"\b(166|195|212|225|340|342|375|410|500 (trc|mondial|superfast)|625|750 monza|860 monza|290 mm|315 s|335 s|330 p|365 p|206 s|196 s|246 s)\b",), tracked=False),
]

_COMPILED = [(m, [re.compile(p) for p in m.patterns]) for m in MODELS]
BY_ID = {m.id: m for m in MODELS}

_STRIP = re.compile(r"[^a-z0-9 ]+")
_MILE_PHRASE = re.compile(r"\b\d[\d ]*k? ?(mile|miles|km|kilometers|kilometres)\b")
_SPACES = re.compile(r"\s+")


def clean_title(text: str) -> str:
    t = (text or "").lower()
    t = t.replace("/", " ").replace("-", " ").replace("_", " ")
    t = t.replace("spyder", "spider")
    t = _STRIP.sub(" ", t)
    return _SPACES.sub(" ", t).strip()


@dataclass
class Match:
    model: Optional[Model]
    confidence: float
    reasons: list = field(default_factory=list)


def resolve(title: str, year: Optional[int] = None, model_hint: Optional[str] = None) -> Match:
    """Map a free text title (plus optional structured model field) to a Model."""
    text = clean_title(" ".join(x for x in [model_hint or "", title or ""] if x))
    text = _MILE_PHRASE.sub(" ", text)          # "20k mile 2003 ferrari 360 spider": drop the mileage words
    first_conflict = None
    for model, patterns in _COMPILED:
        for p in patterns:
            if p.search(text):
                conf = 1.0
                reasons = [f"matched {p.pattern}"]
                # a bare number like "488" defaults to the coupe, slightly less certain
                if p.pattern.startswith(r"\b") and p.pattern.count(" ") == 0:
                    conf -= 0.1
                if year is not None:
                    first, last = model.years
                    last = last or 2100
                    if year < first - 1 or year > last + 1:
                        # names repeat across eras ("250 GT Tour de France" 1956 vs F12tdf 2016,
                        # "400 Superamerica" 1960 vs 575 Superamerica 2005): keep looking
                        if first_conflict is None:
                            first_conflict = Match(model, max(conf - 0.6, 0.0), reasons + [f"year {year} outside {first} to {model.years[1]}"])
                        break
                return Match(model, max(conf, 0.0), reasons)
    if year is not None and year < 1975 and "ferrari" in text or (year and year < 1975 and text.startswith("dino")):
        return Match(BY_ID["classic-other"], 0.8, ["pre 1975 Ferrari without its own model page"])
    return first_conflict or Match(None, 0.0, ["no pattern matched"])


def is_ferrari(title: str, make: Optional[str] = None) -> bool:
    if make and "ferrari" in make.lower():
        return True
    t = clean_title(title)
    return "ferrari" in t or bool(re.search(r"\bdino (206|246|308)", t))


# Things that show up in Ferrari searches but are not cars.
NON_CAR = re.compile(
    r"\b(wheels?|rims?|seats?|brochure|poster|sign|model car|scale model|diecast|die cast|1 ?[:x] ?(8|12|18|43)|"
    r"go ?kart|pedal car|engine only|transmission only|gearbox|tool kit|toolkit|luggage|helmet|watch|"
    r"memorabilia|steering wheel|hardtop only|parts|literature|replica|tribute|re ?creation|kit car|simulator|bumper|"
    r"junior|childs?|kids?|neon|lighted|illuminated|clock|artwork|painting|sculpture|book|jacket|chair|desk|"
    r"engine stand|display engine|continuation|evocation|recreation|powered|arcade|game|table|display model|"
    r"half scale|scale|manuals?|passports?|booklets?|accessories|headlights?|taillights?|tail lights?|lights|exhaust|"
    r"mirrors?|badges?|emblems?|key fob|keys|tool roll|car cover|luggage set|wheel set|brake kit)\b"
)


OTHER_MAKES = ("abarth", "fiat", "lancia", "alfa romeo", "maserati", "lamborghini", "porsche", "ford", "chevrolet",
               "pontiac", "toyota", "mercedes", "bmw", "jaguar", "shelby", "de tomaso", "iso", "bizzarrini", "lego")
_OTHER = re.compile(r"\b(" + "|".join(OTHER_MAKES) + r")\b")
_TRACK = re.compile(r"\b(challenge(?! stradale)|xx programme|corse clienti|gt3|gte|gt2 pro|competizione gt|333 sp|"
                    r"f1 car|formula 1|f2004|f2003|f2002|f2001|race car|racecar|599 ?xx|xx evo|fxx|312 t\d?|312 b\d?|126 c\d?|"
                    r"641|642|643|f92a|f93a|412 t\d|f310( b)?|f300|f399|f2005|248 f1|f2007|f2008|f60|f138|f14 t)\b")


def other_make_first(title: str) -> bool:
    """'Abarth 695 Tributo Ferrari', 'Ferrari-engined Lancia Thema 8.32' name another maker's car."""
    t = clean_title(title)
    i = t.find("ferrari")
    m = _OTHER.search(t)
    return bool(m and (i < 0 or m.start() < i))


def is_track_car(title: str) -> bool:
    return bool(_TRACK.search(clean_title(title)))


def looks_like_car(title: str) -> bool:
    return not NON_CAR.search(clean_title(title))
