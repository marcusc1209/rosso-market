# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Inferred from the repository and the owner's requests, not yet confirmed in an interview: Ferrari enthusiasts, collectors and prospective buyers in the US who want to know what a specific Ferrari model or car is really worth today, and the owner, who checks the market daily and corrects listing histories by hand. Typical jobs: see where a model's asking prices and auction results sit, follow one car's history across sellers and price cuts, watch live and upcoming auctions, and spot which models are moving.

## Product Purpose

Rosso Market tracks every Ferrari for sale in the US (and every hypercar for sale worldwide) once per day, counts each physical car once however many sites list it, and puts asking prices next to auction results from houses around the world. Success is a visitor trusting the numbers enough to price a car from them.

## Positioning

One car counted once across dealers, marketplaces, Ferrari Approved and auction houses, with its true listing date and every price change, and asking prices set beside real hammer prices on the same chart. Neighbouring listing sites show one seller's ad; auction databases show only sales.

## Operating Context

Static site on GitHub Pages reading JSON written by a daily pipeline (cloud run at 11:17 UTC and a home Mac run at 14:37 UTC). Routes: overview, model families, model pages, car pages, hypercars, auctions, by house, news, search, methodology, privacy. Visitors use it on desktop and phone.

## Capabilities and Constraints

- Single file front end, `site/index.html`, hash routes, no build framework. Data in `site/data/site.json`, `site/data/models/*.json`, `site/data/cars/*.json`, `site/data/search.json`.
- Charts are drawn in SVG by the page itself from data.
- Photos come from source sites and are often missing, so every card must hold up with no photo.
- Data honesty: figures carry their method (median ask, sell through, listed date and how it was found). Sample data shows a visible "Sample data" bar.
- Copy avoids dashes and semicolons (owner preference).

## Brand Commitments

- Name: Rosso Market. Not affiliated with or endorsed by Ferrari S.p.A., and the site must say so. No Ferrari logos or prancing horse marks.
- Plain, factual voice: says what was measured and since when.

## Evidence on Hand

Real daily data from about 20 sources (dealer marketplaces, Ferrari Approved, Bring a Trailer, RM Sotheby's, Gooding, Bonhams, Broad Arrow, Mecum and others), source health per run, and hand entered cars.com histories in `config/listing_history.yaml`. No testimonials, users or press exist and none may be invented.

## Product Principles

1. Every number says what it is and where it came from.
2. One car, one record: the car is the unit, not the ad.
3. Asks and sales side by side, never one without the other where both exist.
4. Works with missing photos and thin data without looking broken.
5. Fast to scan on a phone at an auction or a dealer.
