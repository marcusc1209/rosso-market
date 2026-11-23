"""Ferrari model taxonomy.

Every record that enters the pipeline is resolved to one canonical model id here.
Patterns are tried in order, so the most specific variant of a model must come
before the general one (488 Pista Spider before 488 Pista before 488 Spider
before 488 GTB). Add new models by appending a Model row in the right place.
"""
from __future__ import annotations

import re
import unicodedata
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
    line: Optional[str] = None   # the model line a version belongs to ("308" for the 308 GTSi), for classics


FAMILY_ORDER = [
    "Mid engine V8",
    "Hybrid V6 and V8",
    "Front engine V12",
    "Front engine V8 GT",
    "Four seat GT and SUV",
    "Hypercars and limited series",
    "Classics",
    "Classic racing cars",
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
    M("f40-lm", "F40 LM", "Hypercars and limited series", "coupe", (1989, 1994), (r"f40 (lm|competizione|gte)\b",), tracked=False),
    M("f40", "F40", "Hypercars and limited series", "coupe", (1987, 1992), (r"\bf40\b",)),
    M("fxx-k", "FXX K", "Hypercars and limited series", "coupe", (2015, 2017), (r"fxx ?k\b",), tracked=False),
    M("fxx", "FXX", "Hypercars and limited series", "coupe", (2005, 2007), (r"\bfxx\b",), tracked=False),
    M("daytona-sp3", "Daytona SP3", "Hypercars and limited series", "targa", (2022, 2024), (r"daytona sp3", r"\bsp3\b")),
    M("monza-sp2", "Monza SP2", "Hypercars and limited series", "spider", (2019, 2022), (r"monza sp2",)),
    M("monza-sp1", "Monza SP1", "Hypercars and limited series", "spider", (2019, 2022), (r"monza sp1",)),
    M("812-competizione-a", "812 Competizione A", "Hypercars and limited series", "targa", (2022, 2024), (r"812 compet\w* a\b", r"812 compet\w* aperta", r"812 comp a\b"), "812-competizione"),
    M("812-competizione", "812 Competizione", "Hypercars and limited series", "coupe", (2021, 2024), (r"812 compet\w*", r"812 comp\b"), "812-competizione-a"),
    M("sf90-xx-spider", "SF90 XX Spider", "Hypercars and limited series", "spider", (2024, None), (r"sf90 xx spider", r"sf90 xx spyder"), "sf90-xx-stradale"),
    M("sf90-xx-stradale", "SF90 XX Stradale", "Hypercars and limited series", "coupe", (2024, None), (r"sf90 xx",), "sf90-xx-spider"),
    M("f12tdf", "F12tdf", "Hypercars and limited series", "coupe", (2016, 2017), (r"f12 ?tdf", r"(?<!250 gt )(?<!250 )(?<!berlinetta )tour de france")),
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
    M("360-modena", "360 Modena", "Mid engine V8", "coupe", (1999, 2005), (r"360 modena", r"\b360\b", r"^\d{4} ferrari modena\b"), "360-spider"),

    # Front engine V12
    M("12cilindri-spider", "12Cilindri Spider", "Front engine V12", "spider", (2025, None), (r"12 ?cilindri spider",), "12cilindri"),
    M("12cilindri", "12Cilindri", "Front engine V12", "coupe", (2025, None), (r"12 ?cilindri",), "12cilindri-spider"),
    M("812-gts", "812 GTS", "Front engine V12", "spider", (2020, 2023), (r"812 gts",), "812-superfast"),
    M("812-superfast", "812 Superfast", "Front engine V12", "coupe", (2018, 2023), (r"812 superfast", r"\b812\b"), "812-gts"),
    M("f12berlinetta", "F12berlinetta", "Front engine V12", "coupe", (2013, 2017), (r"f12 ?berlinetta", r"\bf12\b")),
    M("599-gtb", "599 GTB Fiorano", "Front engine V12", "coupe", (2007, 2012), (r"599 gtb", r"\b599\b")),
    M("575m", "575M Maranello", "Front engine V12", "coupe", (2002, 2006), (r"\b575m?\b", r"(?<!400 )(?<!410 )super ?america")),
    M("550-barchetta", "550 Barchetta", "Front engine V12", "spider", (2001, 2001), (r"550 barchetta",)),
    M("550-maranello", "550 Maranello", "Front engine V12", "coupe", (1997, 2001), (r"\b550\b",)),

    # Front engine V8 GT
    M("amalfi", "Amalfi", "Front engine V8 GT", "coupe", (2026, None), (r"\bamalfi\b",)),
    M("roma-spider", "Roma Spider", "Front engine V8 GT", "spider", (2024, None), (r"roma spider", r"roma spyder"), "roma"),
    M("roma", "Roma", "Front engine V8 GT", "coupe", (2021, None), (r"\broma\b",), "roma-spider"),
    M("portofino-m", "Portofino M", "Front engine V8 GT", "spider", (2021, 2024), (r"portofino m\b",)),
    M("portofino", "Portofino", "Front engine V8 GT", "spider", (2018, 2020), (r"portofino",)),
    M("california-t", "California T", "Front engine V8 GT", "spider", (2015, 2017), (r"california t\b", r"california turbo")),
    M("california", "California", "Front engine V8 GT", "spider", (2009, 2014), (r"(?<!250 gt )(?<!250 )(?<!365 )\bcalifornia\b(?! spider| spyder)",)),

    # Four seat GT and SUV
    M("luce", "Luce", "Four seat GT and SUV", "gt", (2026, None), (r"\bluce\b",), tracked=False),
    M("purosangue", "Purosangue", "Four seat GT and SUV", "suv", (2023, None), (r"purosangue",)),
    M("gtc4lusso-t", "GTC4Lusso T", "Four seat GT and SUV", "gt", (2017, 2020), (r"gtc4 ?lusso t\b",)),
    M("gtc4lusso", "GTC4Lusso", "Four seat GT and SUV", "gt", (2017, 2020), (r"gtc4 ?lusso", r"(?<!365 )gtc4")),
    M("ff", "FF", "Four seat GT and SUV", "gt", (2012, 2016), (r"\bff\b",)),
    M("612-scaglietti", "612 Scaglietti", "Four seat GT and SUV", "gt", (2005, 2010), (r"\b612\b",)),
    M("456m-gta", "456M GTA", "Four seat GT and SUV", "gt", (2000, 2003), (r"456 ?m ?gt ?a\b", r"456 ?m .*\bgta\b", r"(?#y)456 .*\bgta\b", r"(?#y)456 gta\b"), line="456"),
    M("456m-gt", "456M GT", "Four seat GT and SUV", "gt", (2000, 2003), (r"456 ?m\b", r"(?#y)456 gt\b", r"(?#y)\b456\b"), line="456"),
    M("456-gta", "456 GTA", "Four seat GT and SUV", "gt", (1996, 1998), (r"456 .*\bgta\b", r"456 gta\b"), line="456"),
    M("456-gt", "456 GT", "Four seat GT and SUV", "gt", (1993, 1998), (r"456 gt\b", r"\b456\b"), line="456"),

    # Classic racing cars: each its own model, so none of them counts toward a road car's prices
    M("375-mm", "375 MM", "Classic racing cars", "spider", (1953, 1955), (r"375 mm\b",), tracked=False),
    M("375-plus", "375 Plus", "Classic racing cars", "spider", (1954, 1955), (r"375 plus\b",), tracked=False),
    M("500-mondial", "500 Mondial", "Classic racing cars", "spider", (1954, 1955), (r"500 mondial\b",), tracked=False),
    M("750-monza", "750 Monza", "Classic racing cars", "spider", (1954, 1955), (r"750 monza\b",), tracked=False),
    M("735-lm", "735 LM", "Classic racing cars", "spider", (1955, 1955), (r"\b(735|121|118) lm\b",), tracked=False),
    M("410-s", "410 S", "Classic racing cars", "spider", (1955, 1956), (r"410 (s|sport)\b",), tracked=False),
    M("290-mm", "290 MM", "Classic racing cars", "spider", (1956, 1956), (r"290 mm\b",), tracked=False),
    M("500-trc", "500 TRC", "Classic racing cars", "spider", (1956, 1957), (r"500 trc?\b",), tracked=False),
    M("335-s", "335 S", "Classic racing cars", "spider", (1957, 1958), (r"335 (s|sport)\b",), tracked=False),
    M("dino-sp", "Dino SP", "Classic racing cars", "spider", (1961, 1962), (r"\b(196|246|248|268|286) sp\b", r"dino sp\b"), tracked=False),
    M("412-p", "412 P", "Classic racing cars", "coupe", (1966, 1967), (r"412 p\b",), tracked=False),
    M("312-pb", "312 PB", "Classic racing cars", "spider", (1971, 1973), (r"312 (pb|p)\b",), tracked=False),

    # Classics, grouped as Classic.com groups them: a model line ("308") and its versions ("308 GTSi").
    # Within a line the explicit names come first, then the year decides: a model whose years are far
    # from the car's is skipped, so a "1984 308 GTS" falls through to the Quattrovalvole.
    M("166-mm", "166 MM", "Classics", "spider", (1948, 1953), (r"166 mm\b",), tracked=False, line="166"),
    M("166-s", "166 S", "Classics", "coupe", (1948, 1953), (r"166 (s|sc|sport)\b",), tracked=False, line="166"),
    M("166-inter", "166 Inter", "Classics", "coupe", (1948, 1950), (r"166 inter\b", r"\b166\b"), tracked=False, line="166"),
    M("195-inter", "195 Inter", "Classics", "coupe", (1950, 1951), (r"\b195\b",), tracked=False),
    M("212-export", "212 Export", "Classics", "spider", (1951, 1952), (r"212 (export|e)\b",), tracked=False, line="212"),
    M("212-europa", "212 Europa", "Classics", "coupe", (1952, 1953), (r"212 europa\b",), tracked=False, line="212"),
    M("212-inter", "212 Inter", "Classics", "coupe", (1951, 1953), (r"212 inter\b", r"\b212\b"), tracked=False, line="212"),
    M("225-s", "225 S", "Classics", "spider", (1952, 1952), (r"\b225\b",), tracked=False),
    M("340-mm", "340 MM", "Classics", "spider", (1953, 1953), (r"340 mm\b",), tracked=False, line="America"),
    M("340-america", "340 America", "Classics", "coupe", (1950, 1952), (r"\b340\b",), tracked=False, line="America"),
    M("342-america", "342 America", "Classics", "coupe", (1952, 1952), (r"\b342\b",), tracked=False, line="America"),
    M("375-america", "375 America", "Classics", "coupe", (1953, 1954), (r"375 america\b",), tracked=False, line="America"),
    M("410-superamerica", "410 Superamerica", "Classics", "coupe", (1955, 1959), (r"410 (super ?america|sa)\b",), tracked=False, line="America"),
    M("400-superamerica", "400 Superamerica", "Classics", "coupe", (1960, 1964), (r"400 (super ?america|sa)\b",), tracked=False, line="America"),
    M("500-superfast", "500 Superfast", "Classics", "coupe", (1964, 1966), (r"500 superfast\b",), tracked=False, line="America"),

    M("250-recreation", "250 Re-creation", "Classics", "coupe", (1952, 2030), (r"\b250 .*\b(re ?creation|continuation)\b", r"\b(re ?creation|continuation)\b.*\b250\b"), tracked=False, line="250"),
    M("250-replica", "250 Replica", "Classics", "coupe", (1952, 2030), (r"\b250 .*\b(replica|tribute|evocation|homage)\b", r"\b(replica|tribute|evocation|homage)\b.*\b250\b"), tracked=False, line="250"),
    M("250-gto", "250 GTO", "Classics", "coupe", (1962, 1964), (r"250 gto\b",), tracked=False, line="250"),
    M("250-lm", "250 LM", "Classics", "coupe", (1964, 1965), (r"250 lm\b",), tracked=False, line="250"),
    M("250-testa-rossa", "250 Testa Rossa", "Classics", "spider", (1957, 1961), (r"250 (tr\w*|testa ?rossa)\b",), tracked=False, line="250"),
    M("250-california-lwb", "250 GT California Spider LWB", "Classics", "spider", (1957, 1959), (r"\b250 (?!.*\bswb\b).*\bcalifornia\b",), tracked=False, line="250"),
    M("250-california-swb", "250 GT California Spider SWB", "Classics", "spider", (1960, 1963), (r"\b250 .*\bcalifornia\b",), tracked=False, line="250"),
    M("250-swb", "250 GT Berlinetta SWB", "Classics", "coupe", (1959, 1963), (r"\b250 .*\bswb\b", r"passo corto", r"250 gt sef\b", r"\b250 .*\bshort wheelbase\b", r"(?#y)\b250 gt berlinetta\b"), tracked=False, line="250"),
    M("250-lusso", "250 GT Berlinetta Lusso", "Classics", "coupe", (1962, 1964), (r"250 gt ?l\b", r"\b250 .*\blusso\b", r"\blusso berlinetta\b"), tracked=False, line="250"),
    M("250-gte", "250 GT/E 2+2", "Classics", "gt", (1960, 1963), (r"250 gt ?e\b", r"250 gt 2 ?2\b", r"250 2 ?2\b"), tracked=False, line="250"),
    M("250-cabriolet-s1", "250 GT Cabriolet Pinin Farina Series I", "Classics", "spider", (1957, 1958), (r"\b250 .*\bcabriolet\b.*\bseri\w* (i|1)\b", r"(?#y)\b250 (?!.*\bseri\w* (ii|2)\b).*\bcabriolet\b"), tracked=False, line="250"),
    M("250-cabriolet-s2", "250 GT Cabriolet Pinin Farina Series II", "Classics", "spider", (1959, 1962), (r"\b250 .*\bcabriolet\b",), tracked=False, line="250"),
    M("250-pf-speciale", "250 GT Coupe PF Speciale", "Classics", "coupe", (1956, 1958), (r"\b250 .*\b(pf|pinin ?farina) speciale\b", r"\b250 .*\bcoupe speciale\b"), tracked=False, line="250"),
    M("250-boano-ellena", "250 GT Coupe Boano and Ellena", "Classics", "coupe", (1956, 1957), (r"\b250 .*\b(boano|ellena)", r"(?#y)\b250 gt (alloy |low roof )?coupe\b"), tracked=False, line="250"),
    M("250-tdf", "250 GT Berlinetta Tour de France", "Classics", "coupe", (1955, 1959), (r"\b250 .*\b(tour de france|tdf)\b", r"250 gt (lwb )?berlinetta (lwb|scaglietti)\b", r"250 gt lwb\b", r"(?#y)\b250 gt berlinetta\b"), tracked=False, line="250"),
    M("250-pf-coupe", "250 GT Coupe Pinin Farina", "Classics", "coupe", (1958, 1960), (r"\b250 .*\b(pinin ?farina|pf)\b.*\bcoupe\b", r"\b250 gt (pf |alloy |low roof )?coupe\b", r"\b250 gt .*\bseries (i|ii|1|2) coupe\b", r"\b250 gt (series (i|ii|1|2) )?(pinin ?farina )?coupe\b", r"\b250 gt pf\b"), tracked=False, line="250"),
    M("250-competizione", "250 GT Berlinetta Competizione", "Classics", "coupe", (1955, 1955), (r"250 gt berlinetta competizione\b",), tracked=False, line="250"),
    M("250-monza", "250 Monza", "Classics", "spider", (1954, 1954), (r"250 monza\b",), tracked=False, line="250"),
    M("250-mm", "250 MM", "Classics", "coupe", (1953, 1954), (r"250 (mm|mille miglia)\b",), tracked=False, line="250"),
    M("250-europa-gt", "250 Europa GT", "Classics", "coupe", (1954, 1956), (r"250 (gt )?europa gt\b", r"250 gt europa\b", r"\beuropa gt\b"), tracked=False, line="250"),
    M("250-europa", "250 Europa", "Classics", "coupe", (1953, 1954), (r"250 europa\b",), tracked=False, line="250"),
    M("250-other", "250, version not stated", "Classics", "coupe", (1952, 1964), (r"\b250\b(?! p\b| ?gtb? ?4)",), tracked=False, line="250"),

    M("275-gtb-c-speciale", "275 GTB Competizione Speciale", "Classics", "coupe", (1964, 1965), (r"275 gtb ?c speciale\b", r"\b275 .*\bcompetizione speciale\b"), tracked=False, line="275"),
    M("275-gtb-c", "275 GTB/C", "Classics", "coupe", (1966, 1966), (r"275 gtb ?c\b", r"275 gtb competizione\b"), tracked=False, line="275"),
    M("275-nart", "275 GTS/4 NART Spider", "Classics", "spider", (1967, 1968), (r"\b275 .*\bn ?a ?r ?t\b",), tracked=False, line="275"),
    M("275-gtb4", "275 GTB/4", "Classics", "coupe", (1966, 1968), (r"275 gtb ?4\b",), tracked=False, line="275"),
    M("275-gts", "275 GTS", "Classics", "spider", (1964, 1966), (r"275 gts\b",), tracked=False, line="275"),
    M("275", "275 GTB", "Classics", "coupe", (1964, 1966), (r"275 gtb\b", r"\b275\b(?! p\b)"), line="275"),

    M("330-america", "330 America", "Classics", "gt", (1963, 1964), (r"330 america\b",), tracked=False, line="330"),
    M("330-gtc", "330 GTC", "Classics", "coupe", (1966, 1968), (r"330 gtc\b",), line="330"),
    M("330-gts", "330 GTS", "Classics", "spider", (1966, 1968), (r"330 gts\b",), tracked=False, line="330"),
    M("330-gt-s1", "330 GT 2+2 Series I", "Classics", "gt", (1964, 1965), (r"\b330 gt.*\bseri\w* (i|1)\b", r"\binterim\b", r"\b330 (gt )?2 ?2\b(?!.*\bseri\w* (ii|2)\b)", r"\b330 gt\b(?!.*\bseri\w* (ii|2)\b)"), tracked=False, line="330"),
    M("330-gt-s2", "330 GT 2+2 Series II", "Classics", "gt", (1966, 1967), (r"\b330 (gt )?2 ?2\b", r"\b330 gt\b"), tracked=False, line="330"),
    M("330-other", "330, version not stated", "Classics", "gt", (1963, 1968), (r"\b330\b(?! p\b| p ?\d| tri\b| lm\b)",), tracked=False, line="330"),

    M("365-california", "365 California", "Classics", "spider", (1966, 1967), (r"365 california\b",), tracked=False, line="365"),
    M("365-gtc4", "365 GTC/4", "Classics", "coupe", (1971, 1972), (r"365 gtc ?4\b",), tracked=False, line="365"),
    M("365-gtc", "365 GTC", "Classics", "coupe", (1968, 1970), (r"365 gtc\b",), tracked=False, line="365"),
    M("365-gts", "365 GTS", "Classics", "spider", (1968, 1970), (r"365 gts\b(?! ?4)",), tracked=False, line="365"),
    M("365-gt-2-2", "365 GT 2+2", "Classics", "gt", (1967, 1971), (r"365 gt 2 ?2\b", r"365 2 ?2\b", r"365 gt\b(?! ?4)"), tracked=False, line="365"),

    M("daytona-replica", "Daytona Replica", "Classics", "spider", (1969, 2030), (r"\bdaytona\b.*\b(replica|recreation|tribute|conversion|converted)\b", r"\b365 gt[bs] ?4\b.*\b(replica|recreation|conversion|converted)\b"), tracked=False, line="Daytona"),
    M("daytona-competizione", "Daytona Competizione", "Classics", "coupe", (1969, 1973), (r"\bdaytona competizione\b", r"365 gtb ?4 (competizione|c)\b", r"\bdaytona group 4\b"), tracked=False, line="Daytona"),
    M("daytona-gts4", "365 GTS/4 Daytona Spider", "Classics", "spider", (1971, 1973), (r"365 gts ?4\b", r"\bdaytona spider\b"), line="Daytona"),
    M("daytona", "365 GTB/4 Daytona", "Classics", "coupe", (1968, 1973), (r"365 gtb ?4\b", r"\bdaytona\b(?! sp3)"), line="Daytona"),

    M("dino-206-s", "Dino 206 S", "Classics", "spider", (1966, 1967), (r"\b206 (s|sp)\b",), tracked=False, line="Dino"),
    M("dino-206-gt", "Dino 206 GT", "Classics", "coupe", (1967, 1969), (r"\b206 gt\b", r"\bdino 206\b", r"\b206 dino\b"), tracked=False, line="Dino"),
    M("dino-246-gts", "Dino 246 GTS", "Classics", "targa", (1972, 1974), (r"\b246 gts\b",), line="Dino"),
    M("dino-246-gt", "Dino 246 GT", "Classics", "coupe", (1969, 1974), (r"\b246 gtb?\b", r"\bdino 246\b", r"\b246 dino\b", r"^(dino )?\d{4} (ferrari )?dino$"), line="Dino"),
    M("dino-308-gt4", "Dino 308 GT4", "Classics", "coupe", (1974, 1980), (r"308 gt ?4\b", r"\bdino 308\b", r"^(dino )?\d{4} (ferrari )?dino$"), line="Dino"),
    M("dino-208-gt4", "Dino 208 GT4", "Classics", "coupe", (1975, 1980), (r"208 gt ?4\b",), tracked=False, line="Dino"),

    M("365-gt4-2-2", "365 GT4 2+2", "Classics", "gt", (1972, 1976), (r"365 gt ?4 2 ?2\b", r"365 gt ?4 2 ?plus ?2\b"), tracked=False, line="365 GT4 2+2, 400 and 412"),
    M("meera-s", "Meera S", "Classics", "gt", (1983, 1983), (r"\bmeera\b",), tracked=False, line="365 GT4 2+2, 400 and 412"),
    M("412-automatic", "412 Automatic", "Classics", "gt", (1985, 1989), (r"\b412i? .*\b(a|automatic|auto)\b",), tracked=False, line="365 GT4 2+2, 400 and 412"),
    M("412-manual", "412 Manual", "Classics", "gt", (1985, 1989), (r"\b412i?\b(?! p\b| t\b| s\b)",), tracked=False, line="365 GT4 2+2, 400 and 412"),
    M("400-automatic", "400 Automatic", "Classics", "gt", (1976, 1978), (r"\b400 (gt ?)?(a|automatic|auto)\b", r"\b400 gta\b"), tracked=False, line="365 GT4 2+2, 400 and 412"),
    M("400i-automatic", "400i Automatic", "Classics", "gt", (1980, 1985), (r"\b400 ?i\b.*\b(a|automatic|auto)\b", r"\b400 ?i ?a\b", r"\b400\b.*\b(automatic|auto)\b", r"\b400 gta\b"), tracked=False, line="365 GT4 2+2, 400 and 412"),
    M("400-manual", "400 Manual", "Classics", "gt", (1976, 1978), (r"\b400\b(?! ?i\b| super| sa\b| ?gta\b)",), tracked=False, line="365 GT4 2+2, 400 and 412"),
    M("400i-manual", "400i Manual", "Classics", "gt", (1980, 1985), (r"\b400 ?i\b", r"\b400\b(?! super| sa\b)"), tracked=False, line="365 GT4 2+2, 400 and 412"),

    M("365-gt4-bb", "365 GT4 BB", "Classics", "coupe", (1973, 1976), (r"365 gt ?4 ?bb\b", r"\b365 bb\b", r"365 berlinetta boxer\b", r"(?#y)\bberlinetta boxer\b", r"(?#y)(?<!512 )\bbb\b(?! ?512| ?lm| ?i\b)"), line="Berlinetta Boxer"),
    M("512-bblm", "512 BB/LM", "Classics", "coupe", (1978, 1982), (r"512 ?bb ?lm\b", r"\bbb ?lm\b"), tracked=False, line="Berlinetta Boxer"),
    M("512-bb", "BB 512", "Classics", "coupe", (1976, 1980), (r"512 ?bb\b", r"\bbb 512\b", r"\bberlinetta boxer\b", r"\bboxer\b"), line="Berlinetta Boxer"),
    M("512-bbi", "BB 512i", "Classics", "coupe", (1981, 1984), (r"512 ?bb ?i\b", r"\bbb 512 ?i\b", r"512 ?bb\b", r"\bbb 512\b", r"\bberlinetta boxer\b", r"\bboxer\b"), line="Berlinetta Boxer"),

    M("308-gtb-vetroresina", "308 GTB Vetroresina", "Classics", "coupe", (1975, 1977), (r"\b308 .*\b(vetroresina|fib(er|re) ?glass)\b", r"\bvetroresina\b"), line="308"),
    M("308-gtb-qv", "308 GTB Quattrovalvole", "Classics", "coupe", (1984, 1985), (r"308 gtb ?i? .*\b(qv|quattro ?valvole)\b", r"308 gtb ?i? ?qv\b", r"(?#y)308 gtb\b", r"(?#y)308 gtb ?i\b"), line="308"),
    M("308-gts-qv", "308 GTS Quattrovalvole", "Classics", "targa", (1984, 1985), (r"308 gts ?i? .*\b(qv|quattro ?valvole)\b", r"308 gts ?i? ?qv\b", r"(?#y)308 gts\b", r"(?#y)308 gts ?i\b"), line="308"),
    M("308-gtbi", "308 GTBi", "Classics", "coupe", (1981, 1981), (r"308 gtb ?i\b", r"(?#y)308 gtb\b"), line="308"),
    M("308-gtsi", "308 GTSi", "Classics", "targa", (1981, 1981), (r"308 gts ?i\b", r"(?#y)308 gts\b"), line="308"),
    M("308-gtb", "308 GTB", "Classics", "coupe", (1975, 1979), (r"308 gtb\b",), line="308"),
    M("308-gts", "308 GTS", "Classics", "targa", (1977, 1979), (r"308 gts\b",), line="308"),
    M("308-other", "308, version not stated", "Classics", "targa", (1975, 1985), (r"\b308\b(?! ?gt ?4| gt ?m\b)",), tracked=False, line="308"),
    M("208-turbo", "208 and GTB/GTS Turbo", "Classics", "targa", (1980, 1989), (r"\b208\b(?! ?gt ?4)", r"\bgt[bs] turbo\b")),

    M("328-gtb", "328 GTB", "Classics", "coupe", (1986, 1989), (r"328 gtb\b",), line="328"),
    M("328-gts", "328 GTS", "Classics", "targa", (1986, 1989), (r"328 gts ?i?\b",), line="328"),
    M("328-other", "328, version not stated", "Classics", "targa", (1985, 1989), (r"\b328\b",), tracked=False, line="328"),

    M("mondial-8", "Mondial 8", "Classics", "gt", (1980, 1981), (r"\bmondial 8\b", r"(?#y)\bmondial\b(?!.*\b(t|3 ?2|qv|quattro ?valvole|cab\w*|convertible|spider)\b)"), tracked=False, line="Mondial"),
    M("mondial-qv-cabriolet", "Mondial QV Cabriolet", "Classics", "spider", (1983, 1984), (r"\bmondial (qv|quattro ?valvole) (cab\w*|convertible|spider)\b", r"\bmondial (cab\w*|convertible|spider) (qv|quattro ?valvole)\b", r"(?#y)\bmondial\b.*\b(cab\w*|convertible|spider)\b"), tracked=False, line="Mondial"),
    M("mondial-qv-coupe", "Mondial QV Coupe", "Classics", "gt", (1983, 1984), (r"\bmondial (qv|quattro ?valvole)\b", r"(?#y)\bmondial\b.*\bcoupe\b"), tracked=False, line="Mondial"),
    M("mondial-32-cabriolet", "Mondial 3.2 Cabriolet", "Classics", "spider", (1986, 1987), (r"\bmondial 3 ?2 (cab\w*|convertible|spider)\b", r"\bmondial (cab\w*|convertible|spider) 3 ?2\b", r"(?#y)\bmondial\b.*\b(cab\w*|convertible|spider)\b"), tracked=False, line="Mondial"),
    M("mondial-32-coupe", "Mondial 3.2 Coupe", "Classics", "gt", (1986, 1987), (r"\bmondial 3 ?2\b", r"(?#y)\bmondial\b.*\bcoupe\b"), tracked=False, line="Mondial"),
    M("mondial-t-cabriolet", "Mondial t Cabriolet", "Classics", "spider", (1990, 1993), (r"\bmondial t (cab\w*|convertible|spider)\b", r"\bmondial (cab\w*|convertible|spider) t\b", r"\bmondial\b.*\b(cab\w*|convertible|spider)\b"), tracked=False, line="Mondial"),
    M("mondial-t-coupe", "Mondial t Coupe", "Classics", "gt", (1990, 1993), (r"\bmondial t\b", r"\bmondial\b.*\bcoupe\b"), tracked=False, line="Mondial"),
    M("mondial-other", "Mondial, version not stated", "Classics", "gt", (1980, 1993), (r"\bmondial\b",), tracked=False, line="Mondial"),

    M("testarossa", "Testarossa", "Classics", "coupe", (1985, 1991), (r"testarossa", r"\btesta rossa\b")),
    M("512-tr", "512 TR", "Classics", "coupe", (1992, 1994), (r"512 ?tr\b",)),
    M("f512m", "F512 M", "Classics", "coupe", (1995, 1996), (r"f ?512 ?m\b",)),

    M("348-zagato", "348 Zagato Elaborazione", "Classics", "coupe", (1990, 1992), (r"\b348 .*\b(zagato|elaborazione)\b",), tracked=False, line="348"),
    M("348-serie-speciale", "348 Serie Speciale", "Classics", "coupe", (1992, 1993), (r"\b348 .*\b(serie speciale|ss|speciale)\b",), tracked=False, line="348"),
    M("348-gt-competizione", "348 GT Competizione", "Classics", "coupe", (1993, 1994), (r"348 gt ?(competizione|c)\b", r"\b348 .*\bcompetizione\b", r"\b348 lm\b"), tracked=False, line="348"),
    M("348-spider", "348 Spider", "Classics", "spider", (1993, 1995), (r"348 ?(spider|convertible)\b",), line="348"),
    M("348-gtb", "348 GTB", "Classics", "coupe", (1994, 1995), (r"348 ?gtb\b",), tracked=False, line="348"),
    M("348-gts", "348 GTS", "Classics", "targa", (1994, 1995), (r"348 ?gts\b",), tracked=False, line="348"),
    M("348-tb", "348 tb", "Classics", "coupe", (1989, 1993), (r"348 ?tb\b", r"348 ?gtb\b"), line="348"),
    M("348-ts", "348 ts", "Classics", "targa", (1989, 1993), (r"348 ?ts\b", r"348 ?gts\b"), line="348"),
    M("348-other", "348, version not stated", "Classics", "targa", (1989, 1995), (r"\b348",), tracked=False, line="348"),

    M("f355-serie-fiorano", "F355 Spider Serie Fiorano", "Classics", "spider", (1999, 1999), (r"355 .*\bserie fiorano\b", r"355 spider fiorano\b"), tracked=False, line="F355"),
    M("f355-spider", "F355 Spider", "Classics", "spider", (1995, 1999), (r"355 (f1 )?spider",), "f355", line="F355"),
    M("f355-gts", "F355 GTS", "Classics", "targa", (1995, 1999), (r"355 (f1 )?gts\b",), "f355", line="F355"),
    M("f355", "F355 Berlinetta", "Classics", "coupe", (1995, 1999), (r"355 (berlinetta|gtb)\b", r"\b(f ?)?355\b"), "f355-spider", line="F355"),
    M("classic-other", "Other classics before 1975", "Classics", "coupe", (1947, 1975), (r"\b(330 tri|412 s|857 s|860 monza|625|860 monza|315 s|330 p ?\d?|365 p ?\d?|196 s|246 s|250 p|275 p|512 [sm]|312 p|212 mm)\b",), tracked=False),
]

_COMPILED = [(m, [re.compile(p) for p in m.patterns]) for m in MODELS]
BY_ID = {m.id: m for m in MODELS}
YEAR_ONLY = "(?#y)"      # a pattern that names no version, so it decides only together with the year


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("+", " ")).strip("-")


# model lines: line name -> its versions, in taxonomy order. A line's id never equals a model's id.
LINES: dict[str, list[str]] = {}
for _m in MODELS:
    if _m.line:
        LINES.setdefault(_m.line, []).append(_m.id)
LINE_ID = {n: (_slug(n) if _slug(n) not in BY_ID else _slug(n) + "-models") for n in LINES}


# model pages that became a line page or another model when the classics were regrouped
ALIASES = {"dino-246": LINE_ID["Dino"], "330-gt-2-2": LINE_ID["330"], "400-412": LINE_ID["365 GT4 2+2, 400 and 412"]}


def is_catch_all(model_id: str) -> bool:
    """A line's "version not stated" bucket."""
    return model_id.endswith("-other")

_STRIP = re.compile(r"[^a-z0-9 ]+")
_MILE_PHRASE = re.compile(r"\b\d[\d ]*k? ?(mile|miles|km|kilometers|kilometres)\b")
_SPACES = re.compile(r"\s+")


def clean_title(text: str) -> str:
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))       # "coupé" is "coupe"
    t = t.replace("/", " ").replace("-", " ").replace("_", " ")
    t = t.replace("spyder", "spider")
    t = re.sub(r"\b(\d{3})([a-z])", r"\1 \2", t)                   # "250GT", "348tb", "246GTS"
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
            if year is None and p.pattern.startswith(YEAR_ONLY):
                continue
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
    r"go ?kart|pedal car|engine only|transmission only|suitcases?|schedoni|briefcase|(?<!gated )(?<!manual )(?<!speed )gearbox|tool kit|toolkit|luggage|helmet|watch|"
    r"memorabilia|steering wheel|hardtop only|parts|literature|replica|tribute|re ?creation|kit car|simulator|bumper|"
    r"junior|childs?|children|kids?|ride on|ride in|neon|lighted|illuminated|clock|artwork|painting|sculpture|book|jacket|chair|desk|"
    r"engine stand|display engine|continuation|evocation|recreation|powered|arcade|game|table|display model|"
    r"half scale|scale|(?<!speed )(?<!gated )(?<!factory )manuals?|passports?|booklets?|accessories|headlights?|taillights?|tail lights?|lights|exhaust|"
    r"mirrors?|badges?|emblems?|key fob|keys|tool roll|car cover|luggage set|wheel set|brake kit|"
    r"engine(?! out| no\b| number)|engines|shell|body shell|style body|body panels|wall art|intake|plenum|manifolds?|windshield|"
    r"chronograph|perpetual calendar|glass hardtop|helmets|motorcycle|flight box|speedform|amalgam|model$|(?<!carbon )body$|body (?:and |& )?chassis)\b"
)


OTHER_MAKES = ("abarth", "fiat", "lancia", "alfa romeo", "maserati", "lamborghini", "porsche", "ford", "chevrolet",
               "pontiac", "toyota", "mercedes", "bmw", "jaguar", "shelby", "de tomaso", "iso", "bizzarrini", "lego")
_OTHER = re.compile(r"\b(" + "|".join(OTHER_MAKES) + r")\b")
_TRACK = re.compile(r"\b(challenge(?! stradale)|xx programme|corse clienti|gt3|(?:f ?)?(?:360|430|458|488|296) (?:gtc|gt2?|gtlm|n gt|ngt)\b|grand am|308 gt m\b|(?<!250 )(?<!330 )(?<!250 gt )gte|gt2 pro|competizione gt|333 sp|"
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


# ---------------------------------------------------------------- specials: cars that are not a standard example
# A conversion, a prototype or a one off carries its model's name but not its market, so it is listed on the
# model's page with a Special tag and left out of its medians, averages, trends and fair values (clean.special_lots).
_ONE_OFF = re.compile(r"\b(one[- ]off|1 of 1\b|one of one\b|sole example|prototype|pre[- ]production|pre[- ]series|development car|"
                      r"show car|press car|launch car|concept|art car|special projects?|sp\d{2}\b|fuori serie|ex[- ]works)\b", re.I)
_MODIFIED = re.compile(r"\b(novitec|mansory|liberty walk|widebody|wide body|twin[- ]turbo(?:charged)? conversion|restomod|resto[- ]mod|"
                       r"koenig|hamann|engine swap|ls[- ]swap(?:ped)?)\b", re.I)
SPECIAL_LABELS = {"conversion": "Manual conversion", "one_off": "One off or prototype", "modified": "Modified",
                  "outlier": "Price far from the model's other sales"}


def special_kind(title: str, text: str = "") -> "str | None":
    """'conversion', 'one_off' or 'modified' for a car that is not a standard example of its model, else None."""
    both = f"{title or ''} {text or ''}"
    if _CONVERSION.search(both):
        return "conversion"
    if _ONE_OFF.search(title or ""):
        return "one_off"
    if _MODIFIED.search(title or ""):
        return "modified"
    return None


# ---------------------------------------------------------------- one spelling of each model's name
# Sites write the same model several ways ("Ferrari 430", "F 430", "La Ferrari", "12 Cilindri"). Titles are
# shown with Ferrari's own spelling, so a model's cars read alike on its page.
_NAME_FIXES = [(re.compile(p, re.I), r) for p, r in (
    (r"\bFerrari 430\b(?! Scuderia)", "Ferrari F430"), (r"\bF[ -]430\b", "F430"), (r"\bFerrari 355\b", "Ferrari F355"),
    (r"\b365 ?GTC ?4\b", "365 GTC/4"), (r"\bF[ -]355\b", "F355"), (r"\bF12[ -]?Berlinetta\b", "F12berlinetta"), (r"\bF12[ -]?TDF\b", "F12tdf"),
    (r"\bLa Ferrari\b", "LaFerrari"), (r"\b12[ -]Cilindri\b", "12Cilindri"), (r"\bGTC4[ -]Lusso\b", "GTC4Lusso"),
    (r"\bGTC 4 ?Lusso\b", "GTC4Lusso"), (r"\bSpyder\b", "Spider"), (r"\bF8[ -]?Tributo\b", "F8 Tributo"),
    (r"\bF8[ -]?Spider\b", "F8 Spider"), (r"\bSF[ -]90\b", "SF90"), (r"\bPuro[ -]sangue\b", "Purosangue"),
    (r"\bF512[ -]?M\b", "F512 M"), (r"\b512[ -]?TR\b", "512 TR"), (r"\bFerrari Ferrari\b", "Ferrari"))]


def tidy_title(title: str) -> str:
    t = title or ""
    for rx, to in _NAME_FIXES:
        t = rx.sub(to, t)
    return re.sub(r"\s{2,}", " ", t).strip()


# ---------------------------------------------------------------- gearbox: factory manual or F1
# Models Ferrari sold with both a gated six speed manual and the F1 paddle gearbox. Their manual
# cars trade at a clear premium, so they are counted as their own submarket.
DUAL_GEARBOX = {"f355", "f355-spider", "f355-gts", "360-modena", "360-spider", "f430", "f430-spider",
                "575m", "599-gtb", "612-scaglietti"}
_CONVERSION = re.compile(
    r"(?:6|six)[- ]?speed conversion|manual conversion|conver(?:ted|sion) to (?:a )?(?:gated )?(?:six[- ]speed |6[- ]speed )?manual"
    r"|converted to manual|manual swap|swapped to (?:a )?(?:gated )?manual|retro-?fitted (?:with )?(?:a )?(?:gated )?manual", re.I)
_MANUAL_TITLE = re.compile(r"\b(?:6|six)[- ]?speed\b(?![- ]*(?:f1|automated|sequential|paddle|dual))|\bmanual\b|\bgated\b", re.I)
_MANUAL_TEXT = re.compile(r"(?:six|6)[- ]speed (?:gated )?manual|manual (?:transaxle|gearbox|transmission)|gated (?:manual|shifter|six|gearbox)"
                          r"|three[- ]pedal", re.I)
_F1 = re.compile(r"\bF1\b(?![- ]?(?:team|race|driver|world|champion))|paddle[- ]shift|automated (?:manual|transaxle|gearbox)", re.I)


def gearbox(model_id, year=None, title="", transmission="", text=""):
    """'manual' for a factory six speed manual, 'f1' otherwise, None for models offered with one gearbox.

    A car counts as manual only when its listing says so (titles like "F355 Spider 6-Speed", or text such as
    "six-speed manual transaxle"). A manual conversion of an F1 car is an F1 car, and so is any car whose
    listing does not mention its gearbox. F355s from before the 1998 model year are manual: the F1 gearbox
    was not offered yet."""
    if model_id not in DUAL_GEARBOX:
        return None
    title, text, tr = title or "", text or "", (transmission or "").strip().lower()
    if _CONVERSION.search(title) or _CONVERSION.search(text):
        return "f1"
    if _F1.search(title):
        return "f1"
    if _MANUAL_TITLE.search(title):
        return "manual"
    if tr in ("manual", "6-speed manual", "manual 6-speed", "6 speed manual", "manual transmission"):
        return "manual"
    if _MANUAL_TEXT.search(text) and not _F1.search(text):
        return "manual"
    if model_id.startswith("f355") and year and int(year) <= 1997:
        return "manual"
    return "f1"


_FIXES_FILE = __import__("pathlib").Path(__file__).resolve().parent.parent / "config" / "gearbox.yaml"


def _load_fixes() -> dict:
    """config/gearbox.yaml: VIN or chassis number -> 'manual' or 'f1', set by hand."""
    try:
        import yaml
        d = yaml.safe_load(_FIXES_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, ImportError):
        return {}
    out = {}
    for k, v in d.items():
        v = str(v).strip().lower()
        v = "manual" if v in ("manual", "6-speed", "6 speed", "six speed") else "f1" if v == "f1" else None
        if v:
            out[str(k).strip().upper()] = v
    return out


GEARBOX_FIXES = _load_fixes()


def _load_years() -> dict:
    """config/years.yaml: VIN or chassis number -> model year, set by hand."""
    try:
        import yaml
        d = yaml.safe_load((_FIXES_FILE.parent / "years.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, ImportError):
        return {}
    return {str(k).strip().upper().lstrip("0"): int(v) for k, v in d.items() if str(v).strip().isdigit()}


YEAR_FIXES = _load_years()


def fixed_year(vin_or_key) -> "int | None":
    """The year set by hand for a car, from its VIN, chassis number or vehicle key."""
    k = str(vin_or_key or "")
    k = k.rsplit(":", 1)[1] if k.startswith(("vin:", "chassis:")) else k
    return YEAR_FIXES.get(k.strip().upper().lstrip("0")) if k else None


def fixed_gearbox(vehicle_key) -> "str | None":
    """The gearbox set by hand for a car, from its vehicle key ("vin:..." or "chassis:...") or bare VIN."""
    k = str(vehicle_key or "")
    k = k.split(":", 1)[1] if k.startswith(("vin:", "chassis:")) else k
    return GEARBOX_FIXES.get(k.strip().upper()) if k else None


def gearbox_from(model_id, year, items) -> "str | None":
    """One car's gearbox from all its listings and lots: items of (title, transmission, text)."""
    if model_id not in DUAL_GEARBOX:
        return None
    titles = " | ".join(t for t, _, _ in items if t)
    texts = " | ".join(x for _, _, x in items if x)
    trs = [tr for _, tr, _ in items if tr]
    tr = next((t for t in trs if (t or "").strip().lower().startswith("manual") or "manual" == (t or "").strip().lower()), trs[0] if trs else "")
    return gearbox(model_id, year, titles, tr, texts)
