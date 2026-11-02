# Rosso Market

Daily Ferrari asking prices, auction results and market signals for the US and Canada.

Two parts:

1. **`pipeline/`** collects listings and auction lots every day, cleans them, counts each car once, stores daily price observations in SQLite and writes the JSON the site reads.
2. **`site/`** is the website. It is a static page that reads `site/data/site.json` and `site/data/models/*.json`, so it can be hosted anywhere (GitHub Pages, Netlify, Cloudflare Pages, S3).

## Quick start

```bash
pip install -r requirements.txt
playwright install chromium          # only needed for the browser based sources

python -m pytest -q tests            # 80 tests, 23 of them against real responses captured from each site

# real data (needs normal internet access)
python -m pipeline.run               # every verified source, today

# offline demo (a simulated market, no network)
python -m pipeline.run --config config/demo.yaml --backfill 120

python scripts/build_site.py         # wraps the page for static hosting into public/
cd public && python -m http.server 8000   # open http://localhost:8000
```

The demo writes to `data/demo.db` and the site shows a yellow "Sample data" bar. Real runs write to `data/ferrari.db`.

## How the pipeline works

```
source adapters  ->  normalize  ->  dedupe  ->  SQLite  ->  analytics  ->  site JSON
   (fetch.py)       (normalize.py) (dedupe.py)  (db.py)   (analytics.py)   (export.py)
                         |
                   taxonomy.py  (every title resolved to one of 80+ Ferrari models)
```

**Fetching (`fetch.py`).** All traffic goes through one client that rate limits per host, honours robots.txt, retries with backoff, detects bot challenges and archives every raw page under `data/raw/<date>/<source>/`. When a site changes its markup you fix the parser and replay it against the archived pages without hitting the site again.

**Extraction (`extract.py`).** Three layers, tried in order:

1. Structured data the site already publishes: JSON-LD, Next.js `__NEXT_DATA__`, embedded `var x = {...}` objects, and for JavaScript sites the JSON feeds the page itself downloads (the headless browser records them). These change far less often than page markup.
2. A generic mapper that recognises a vehicle in any JSON shape, whatever the site calls its fields (`sold_price`, `hammerPrice`, `current_bid`, `askingPrice` and so on).
3. Optional: Claude reads the page text and returns the fields, used only when 1 and 2 find nothing. Off by default.

**Model resolution (`taxonomy.py`).** Titles are matched to canonical models with the most specific variant first, so "488 Pista Spider" never lands in "488 Spider". A year outside a model's production run lowers confidence and the record is rejected rather than guessed. Parts, wheels, scale models, brochures and replicas are filtered out.

**Normalisation (`normalize.py`).** Prices in USD, CAD, EUR, GBP and others with live exchange rates, mileage in miles (km converted), Ferrari VIN detection with check digit validation, chassis numbers for classics, US or Canada from the location.

**Counting each car once (`dedupe.py`).** The same car often sits on the dealer's site, AutoTrader, Cars.com and Ferrari Approved at once. Cars are matched by VIN, then chassis number, then a fingerprint of model, year, mileage band, colour and seller. Where one car shows two prices, the dealer's own page or Ferrari Approved wins, because marketplaces lag behind price cuts.

**Storage (`db.py`).** Prices are stored as daily observations, so any statistic can be recomputed for any past day. A source that fails never makes cars disappear: a listing is only marked gone after its source has run successfully twice without seeing it.

**Health checks (`health.py`).** Scrapers usually break quietly, returning 20 cars instead of 600. Every run is compared with the source's recent history and marked ok, degraded or down. A degraded source does not remove any listings, and its state shows in the site footer.

**Analytics (`analytics.py`).**

| Figure | Definition |
|---|---|
| Tracked listing | one physical car for sale, however many sites list it |
| Median ask | median asking price of tracked listings for a model on a day |
| Mover | median ask moved 1% or more in 7 days, at least 5 cars on both days, and the cars listed on both days mostly repriced the same way |
| Price cut | a car whose ask is lower than 7 days earlier |
| Overnight changes | new cars plus cars gone plus cars repriced since yesterday |
| Sell through | sold lots divided by closed lots |
| Ask index | chain linked index of model medians, weighted by listings, so a shift in model mix does not move it |
| Auction vs ask | each hammer price compared with the model's median dealer ask that day |

The mover rule is stricter than the reference site's. A batch of cheap cars arriving drags a median down without anyone cutting a price, and that does not count here.

## Sources

Each verified source was inspected in a real browser on 2026-10-31, and its adapter is tested against responses captured that day (`tests/fixtures/`, `tests/test_live_samples.py`).

| Source | How it is read | What it gives | Status |
|---|---|---|---|
| Ferrari Approved | page data on `preowned.ferrari.com/.../rfc?pl=N` | Every car at a Ferrari dealer: US ~1,200, Canada ~100, with VIN, price, mileage, dealer and a certified flag | Verified, on |
| Bring a Trailer | `auctionsCurrentInitialData` on /auctions/, results from `/wp-json/bringatrailer/1.0/data/listings-filter` | ~40 live Ferrari lots, ~7,000 results | Verified, on |
| Cars & Bids | headless browser captures `/v2/autos/auctions` (requests are signed) | Live lots and recent results | Verified, on |
| Sotheby's Motorsport | `/api/auctions/listings?type=live,upcoming,sold` | Live, preview and sold lots with hammer and fee | Verified, on |
| duPont Registry Live | `/api/auctions/listings` | Live and sold lots | Verified, on |
| RM Sotheby's | `POST /api/search/SearchLots` | ~3,200 Ferrari results worldwide, upcoming lots with estimates | Verified, on |
| Mecum | Algolia index `wp_posts_lot` (public key read from the page) | ~1,850 Ferrari lots | Verified, on |
| MarketCheck | licensed API | AutoTrader, Cars.com, CarGurus and dealer sites, US and Canada | Built, needs a paid key |
| Broad Arrow | sitemap, then one page per lot, 10 s apart (robots.txt bars the results search) | ~370 Ferrari lots: results with price, unsold lots, upcoming lots with estimates | Verified, on (Mac runner: GitHub's servers get an AWS WAF challenge) |
| Barrett-Jackson | | Results show "Register to View Price" | Off: prices are only shown to signed in users |
| Gooding Christie's | GraphQL `getVehicles` at `cdn.goodingco.com/graphql` | ~940 Ferrari lots: results, unsold, upcoming with estimates | Verified, on |
| Bonhams | Typesense `multi_search` at `api01.bonhams.com/search-proxy` (the public search key is read from the site's own script each run) | ~1,800 Ferrari car lots | Verified, on |
| PCARMARKET | server rendered search, then one page per car lot for the date, VIN and mileage | Live lots and results (most of its ~1,400 Ferrari items are parts, which are skipped) | Verified, on (Mac runner: Cloudflare answers GitHub's servers with 403) |
| duPont Registry dealer listings | generic browser adapter | | Off, unverified |
| AutoTrader, Cars.com direct | | | Off on purpose (terms, bot protection) |

### Quirks found in the live data, and how they are handled

* **Mecum "The Bid Goes On" lots** show a number that is not a high bid (a 2015 458 Spider showed $600,000). Those lots count as unsold and the number is never used as a price.
* **Ferrari Approved "price on request"** cars carry placeholder prices like 0, 212 or 1,685. They are excluded.
* **Ferrari Approved includes track cars** (488 Challenge). Track only cars are excluded everywhere.
* **duPont Registry Live** reports some results that look implausible (an F12tdf at $2.66M). Any result outside 0.4 to 2.5 times the model's median ask is kept on the model page but left out of insights.
* **RM Sotheby's results have no sale date in the search API.** Each lot link names its auction (`/auctions/lf26/...`), and every auction page carries a schema.org Event with its dates. The adapter reads each auction page once, caches the dates in the `kv` table and dates results by the auction's last day. RM also publishes some sold results with only the estimate showing; those are skipped, because the estimate is not a price. Private sales and sealed bid sales are skipped.
* **What counts as an auction result, for every house.** A result needs a published price. Private sales, private treaty offers and sealed bid sales are left out entirely, because no hammer price is ever made public. A lot marked sold with no price is left out. An upcoming lot appears only under upcoming auctions with its estimate, and never counts as a sale or an asking price. An unsold lot with no published high bid still counts toward sell through, since it really did fail to sell, but never toward any price.
* **Broad Arrow** prints the price as text ("Sold Price: $15,185,000", "CHF 75'000", "€184.250"). Its JSON-LD price is ten times the real figure and is ignored. "Sold After Auction" and "Sold Before Auction" publish no price and are skipped. A lot still showing only an estimate after its sale date published no result and is skipped. Results are dated from the auction list on /past-auctions, whose names are spelled slightly differently from the lot pages.
* **Gooding** marks withheld results with `privateSalesPrice: true` and a sale price of "1"; those and its private sales are skipped. A past lot with no sale price counts as unsold.
* **Bonhams** statuses: SOLD (price with premium), BI (bought in, unsold, no bid published), WD (withdrawn) and CS (conditional sale), both skipped, NEW (upcoming). Bonhams titles carry "Chassis no." and "Engine no.", so the parts filter ignores "engine no".
* **PCARMARKET** has no API. Its robots.txt allows general crawlers and sets the content signal ai-train=no, which this project respects (it trains nothing). "Bid to" means the reserve was not met.
* **Stored rows are rechecked on every run.** `pipeline/clean.py` passes saved titles through the current filters (parts, toys, track cars), drops anything that now fails into `rejects` with a `purged:` reason, renames cars the taxonomy has learned to recognize, and keeps sold prices that are wildly below the same model's sales from the same period out of the medians.
* **Auction titles carry mileage** ("20k-Mile 2003 Ferrari 360 Spider", "48k-Kilometer 1996 F355"). It is read and converted to miles.
* **Non cars share the make**: luggage, wheels, signs, arcade games, kids' cars, half scale models, re-creations, and other makers' cars with a Ferrari link (Abarth 695 Tributo Ferrari). All filtered.
* **Price basis differs by house.** BaT, Cars & Bids, SOMO and duPont show the hammer bid. RM and Mecum include the buyer's premium. Each record keeps `price_basis` so this can be normalised later.

### When a source breaks

The daily run opens a GitHub issue labelled `data-source` when a source returns far fewer cars than usual or errors. To fix it:

1. Download the run's `raw-pages` artifact and look at the failing source's saved responses.
2. Open the site in a browser, compare what it now loads, and update the adapter.
3. Capture a fresh sample into `tests/fixtures/`, update the test, and run `python -m pytest`.
4. Meanwhile, switch on the matching `apify_*` backup in `config/sources.yaml` if one exists.

Listings are never removed because a source broke: removal needs two successful runs without the car.

### Turning on an unverified source

Set `enabled: true`, run `python -m pipeline.run --only <name> -v`, and check fetched, accepted and rejected in the log. `sqlite3 data/ferrari.db "select reason, count(*) from rejects group by 1"` shows why records were dropped. If nothing is accepted, open the saved responses in `data/raw/<date>/<name>/`, find the feed that holds the cars, and set `json_url_contains` or write a small adapter like `pipeline/sources/online_auctions.py`.

### Keys and cost

| Key | Used by | Rough cost |
|---|---|---|
| `MARKETCHECK_API_KEY` | asking prices beyond the Ferrari dealer network | paid plans, see marketcheck.com |
| `APIFY_TOKEN` | backup auction scrapers | about $2 to $16 per thousand records |
| `ANTHROPIC_API_KEY` | optional page reading fallback | cents per page |

Everything marked verified and on above runs free.

## Running it every day

Two workflows share the work:

| Workflow | Runs on | Sources | When |
|---|---|---|---|
| Daily market run (`daily.yml`) | GitHub's servers | Bring a Trailer, Cars & Bids, duPont Registry Live, RM Sotheby's, Mecum, Gooding, Bonhams | 4:17 am Pacific |
| Home sources (`home.yml`) | the owner's Mac (self hosted runner) | Ferrari Approved, SOMO, Broad Arrow, PCARMARKET | 7:37 am Pacific, or when the Mac wakes |

Ferrari Approved and SOMO refuse GitHub's server addresses, so they run from a home connection. Sources are assigned with `runner: home` in `config/sources.yaml`. Each workflow merges in anything the other saved before writing the database (`pipeline/merge.py`), and the home run asks the daily workflow to republish the site when it finishes.

### Setting up the Mac runner

1. In the repository go to Settings, Actions, Runners, New self hosted runner, and pick macOS and ARM64.
2. Open Terminal and paste the Download and Configure commands GitHub shows. Accept the defaults (the labels `self-hosted` and `macOS` are added automatically).
3. Instead of `./run.sh`, install it as a background service so it starts at login: `./svc.sh install` then `./svc.sh start`.
4. Keep the repository setting "Require approval for all outside collaborators" for fork pull request workflows. A self hosted runner on a public repository must never run code from strangers' pull requests. These workflows only run on a schedule or by hand.

If the Mac is asleep at 7:37 am, the job waits in the queue and starts when it wakes (GitHub keeps it for up to 24 hours).

`.github/workflows/daily.yml` runs at 11:17 UTC: tests, pipeline, site build, deploy to GitHub Pages. It keeps the database on a `market-data` branch so the price history survives between runs, and opens an issue when a source breaks.

`.github/workflows/update.yml` ("Apply update") applies a code change sent as a compressed patch: `git diff | gzip -9 | base64 -w0`, pasted into the workflow's form. It runs the tests before committing. Changes under `.github/` have to be made in GitHub's web editor, because workflow tokens are not allowed to modify workflows.

First run: start the workflow by hand with "Import extra auction history" ticked. That pages much deeper into every results feed, so the site opens with months of auction results instead of a few days.

The asking price history only starts on the first run: medians, movers and price cuts need about a week of daily runs before they mean anything, and the 90 day charts need 90 days.

## Things to know

**Terms of use.** Most marketplaces and several auction sites prohibit automated collection in their terms, and AutoTrader and Cars.com run heavy bot protection. That is why asking prices default to MarketCheck, which licenses that data, and why the direct marketplace adapters ship switched off. Before enabling any direct scraper, read that site's terms, keep the polite defaults (robots.txt on, one request every few seconds per host), and set a real contact address in `user_agent`. This is not legal advice.

**The name.** The brand is Rosso Market, so "Ferrari", a trademark Ferrari S.p.A. enforces actively, is used only to describe what the site covers. Keep it out of the domain, logo and social handles, keep the footer's non affiliation notice, and avoid Ferrari's logos, the prancing horse and the Scuderia shield. A quick trademark search on your chosen domain is still worth doing before launch.

**Photos.** The reference site uses car photos. This build uses price charts instead, because photos scraped from listings belong to the dealers and photographers. Licensed or your own photography can be added later.

## Project layout

```
config/sources.yaml        every source, on or off, with its settings
content/takes/*.md         editorial takes (front matter plus paragraphs)
pipeline/                  the data pipeline (see above)
pipeline/sources/          one adapter per kind of source
site/index.html            the website (fragment, wrapped by scripts/build_site.py)
site/data/                 written by the pipeline
tests/                     model matching, parsing, dedupe, mover rule, removal safety, end to end run
```


## Photos, car pages and the market chart

Every source's main photo is stored with each record (`image_url`), and where a source
offers more, up to 40 more in `extra.photos` (RM, Gooding, Ferrari Approved, Broad Arrow
and PCARMARKET lot pages). `pipeline/images.py` lists where each source keeps them.
Photos load from each source's own image server and every page links back to the source.

`pipeline/cars.py` writes one file per car to `site/data/cars/<id>.json`, like a
classic.com vehicle page: gallery, specs, where the car is now, and its history. Records
that share a VIN or chassis number are one car wherever they came from. A dealer's listing
of one car on several sites is one history event. Auction lots without a VIN or chassis
stand alone, because loose fingerprints would join different cars.

Each model file also carries `mileage` (`pipeline/mileage.py`): the model's cars for sale
now and auction sales from the last 18 months with a known mileage, and a curve fitted to
log price (a steady percentage per mile, or a log of miles shape, whichever misses the cars
by less), refitted after setting aside cars far off it. Each car page draws it with the car
ringed and says how far above or below the curve the car is priced.

Each model file carries `points` for the market chart: sold, high bid, for sale and last
asking price, each with its car id, title and photo, so hovering a dot shows the car and
clicking it opens the car page.


## Dealer marketplaces

`pipeline/sources/dealer_marketplaces.py` and `classiccars.py` read these marketplaces, checked on 2026-11-01:

| Source | Cars | Runner | Notes |
| --- | --- | --- | --- |
| AutoTrader.com | off | | blocks automated access ("page unavailable") from plain requests and headless browsers; the reader is kept for a licensed route |
| duPont Registry | ~1,800 used | cloud | schema.org list on each search page, VIN, dealer |
| AutoTrader.ca | ~330 | Mac | AutoScout24 platform, prices in CAD, no VIN |
| AutosToday | ~2,400 used | Mac | `autostoday.py`: dealers' own inventory feeds, VIN, days on market. The model a VIN encodes is used when the title is loose ("488 Base") |
| ClassicCars.com | ~180 | cloud | `classiccars.py`: 12 search pages, then each listing page once for odometer, colours, VIN or chassis and photos. Cars consigned to auctions are skipped |

Each one's robots.txt allows general crawlers on the search pages read here, and
nothing else is read. Cars.com is off: every page, robots.txt included, sits behind a
Cloudflare bot check, which this pipeline never works around. Cars that appear on
several sites are one car (matched by VIN, or by fingerprint for AutoTrader.ca), so
adding sources widens coverage without double counting. These sites' terms of use
govern automated access; MarketCheck (`marketcheck`, needs a paid key) is the licensed
route to the same inventory.

Also checked on 2026-11-01 and left out: CarGurus and Carfax (DataDome), TrueCar
(PerimeterX), JamesEdition (Cloudflare challenge), iSeeCars (nationwide results need a
token its own script makes), Autolist (its results are CarGurus inventory) and
Exotic Car Trader (about 20 live Ferraris), carsforsale.com (Cloudflare challenge). eBay Motors has an official free API that
needs the owner's developer keys.
