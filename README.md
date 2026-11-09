# Rosso Market

Daily Ferrari asking prices and market signals for the US, with auction results from around the world.

Cars for sale are the US market only, except hypercars: a listing whose location is outside the
US (a Montreal dealer on duPont Registry, a Dutch car on ClassicCars.com) is rejected when read and
purged if already stored (`normalize.outside_market`, `clean._drop_outside_market`), unless its
model is in the "Hypercars and limited series" family (F40, LaFerrari, 812 Competizione, SF90 XX
and the rest), which is tracked for sale anywhere. Every other model outside the US shows only
through auction results, which stay international. Ferrari Approved still reads its Canadian
dealers, for their hypercars. AutoTrader.ca, which listed only Canadian cars, was removed on
2026-11-04.

Two parts:

1. **`pipeline/`** collects listings and auction lots every day, cleans them, counts each car once, stores daily price observations in SQLite and writes the JSON the site reads.
2. **`site/`** is the website. It is a static page that reads `site/data/site.json` and `site/data/models/*.json`, so it can be hosted anywhere (GitHub Pages, Netlify, Cloudflare Pages, S3).

## Quick start

```bash
pip install -r requirements.txt
playwright install chromium          # only needed for the browser based sources

python -m pytest -q tests            # 151 tests, 23 of them against real responses captured from each site

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
| Ferrari Approved | page data on `preowned.ferrari.com/.../rfc?pl=N` | Every car at a US Ferrari dealer, ~1,200, with VIN, price, mileage, dealer and a certified flag | Verified, on |
| Bring a Trailer | `auctionsCurrentInitialData` on /auctions/, results from `/wp-json/bringatrailer/1.0/data/listings-filter` | ~40 live Ferrari lots, ~7,000 results | Verified, on |
| Cars & Bids | headless browser captures `/v2/autos/auctions` (requests are signed) | Live lots and recent results | Verified, on |
| Sotheby's Motorsport | `/api/auctions/listings?type=live,upcoming,sold` | Live, preview and sold lots with hammer and fee | Verified, on |
| duPont Registry Live | `/api/auctions/listings` | Live and sold lots | Verified, on |
| RM Sotheby's | `POST /api/search/SearchLots` | ~3,200 Ferrari results worldwide, upcoming lots with estimates | Verified, on |
| Mecum | Algolia index `wp_posts_lot` (public key read from the page) | ~1,850 Ferrari lots | Verified, on |
| MarketCheck | licensed API | AutoTrader, Cars.com, CarGurus and dealer sites, US | Built, needs a paid key |
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

Every auction lot also says where it was offered (`pipeline/sales.py`): the house's sale by
name, linked to the sale's own page, and the country it was held in (for online auctions,
where the car is). RM Sotheby's sale pages come from the lot link; Bonhams' names, venues and
countries from its sales index; Broad Arrow's from its auction list; Gooding's pages from its
sitemap; Mecum's from the sale name (always in the US). The adapters keep these lookups in kv.

Every car we have ever seen keeps its page, including cars that sold or left the market, and
`data/search.json` lists each one's VIN (or chassis number) so the search box in the header
finds it. Cars no longer for sale drop off the lists but stay findable by VIN.

Each model file carries `points` for the market chart: sold, high bid, for sale and last
asking price, each with its car id, title and photo, so hovering a dot shows the car and
clicking it opens the car page.


## Dealer marketplaces

`pipeline/sources/dealer_marketplaces.py` and `classiccars.py` read these marketplaces, checked on 2026-11-01:

| Source | Cars | Runner | Notes |
| --- | --- | --- | --- |
| AutoTrader.com | off | | blocks automated access ("page unavailable") from plain requests and headless browsers; the reader is kept for a licensed route |
| duPont Registry | ~1,800 used | cloud | schema.org list on each search page, VIN, dealer |
| AutosToday | off | | `autostoday.py` is kept but off: it answers automated readers with 403 from any address |
| ClassicCars.com | ~180 | cloud | `classiccars.py`: 12 search pages, then each listing page once for odometer, colours, VIN or chassis and photos. Cars consigned to auctions are skipped |

Each one's robots.txt allows general crawlers on the pages read here.

Listing dates: duPont Registry publishes the day each car was listed on the
car's own page ("Date Listed"). Each page is read once per car
(`date_pages` caps a run) and the date is kept with the listing. A car listed on several
sites takes the earliest published date, so a Ferrari Approved car (whose feed date is
refreshed daily and means nothing) shows the date the same VIN went up on duPont Registry.
A marketplace listing with no VIN gets, from `dedupe.link_listings`, the VIN of the one car at
the same dealer with the same model, year and mileage (within 2%): the car is counted once and
takes the earliest date either listing gives. ClassicCars.com numbers its listings in order
(`pipeline/idclock.py`): archived captures of its home page pin the highest number to a day
(1,676,304 on Jan 1 2023), each run pins today's, and a car's number read against those pins
dates it to within 10 days. The few captures needed are read once and kept in kv. Ferrari
Approved's archived ad addresses used to end in the VIN and the ad's creation time
(`.../ZFF99SLA2R0300845-1724919183952`, `pipeline/wayback.py`), which dates older stock; since
Jan 2026 its addresses carry an opaque token and the archive no longer captures them, so
newer Ferrari Approved cars are dated from the same car elsewhere. A car is dated from the
start of its latest unbroken stretch of archive evidence (no gap over 180 days, reaching to
today), so a car that sold and came back is not dated by its old listing. Archive requests
go 12 seconds apart (its index server refuses quicker ones) and three failures in a row put
the rest off to the next run. Cars that still have no date are looked up on CLASSIC.COM by VIN
(`pipeline/classic_com.py`): it tracks dealer inventory and shows the day each listing was
posted. Only a card for the same VIN that is for sale now counts, each VIN is asked once (again
after a week if CLASSIC.COM did not have it yet), and a bot check stops the step for the run.
It is off: CLASSIC.COM answers automated requests with 403 from any address we have, so it
waits for a permitted route. CarGurus is not used: it sits behind DataDome bot protection. Cars.com, AutoTrader.com and CarGurus show a car's first listing date and every price change
but sit behind bot checks, so what the owner reads there can be entered per VIN in
`config/listing_history.yaml` (`pipeline/overrides.py`): the date wins when it is earlier than
any a site publishes, and the price history shows in the seller's box on the car page. Cars with no evidence anywhere keep
the first day we saw them. Cars.com is off: every page, robots.txt included, sits behind a
Cloudflare bot check, which this pipeline never works around. Cars that appear on
several sites are one car (matched by VIN, or by fingerprint when a site shows none), so
adding sources widens coverage without double counting. These sites' terms of use
govern automated access; MarketCheck (`marketcheck`, needs a paid key) is the licensed
route to the same inventory.

Also checked on 2026-11-01 and left out: CarGurus and Carfax (DataDome), TrueCar
(PerimeterX), JamesEdition (Cloudflare challenge), iSeeCars (nationwide results need a
token its own script makes), Autolist (its results are CarGurus inventory) and
Exotic Car Trader (about 20 live Ferraris), carsforsale.com (Cloudflare challenge). eBay Motors has an official free API that
needs the owner's developer keys.

## Reserve not met (RNM)

A lot that closes without selling is stored as `unsold` and shown as RNM (reserve not met), with its high bid
where the site publishes one:

- Bring a Trailer, Cars & Bids and PCARMARKET list them in their results with the high bid.
- Bonhams, Gooding, Mecum and Broad Arrow list them as not sold, without a bid.
- RM Sotheby's marks them "Not Sold" and shows the estimate in place of a price (stored as the estimate).
- Sotheby's Motorsport and duPont Registry Live only publish cars that sold: a lot that misses its reserve is
  pulled from the site. After a successful run, a live lot whose auction ended more than a day ago and that the
  feed no longer lists is recorded as RNM at its last bid (`db.mark_ended`). If a sale shows up later, the next
  run sets it to sold.

RNM results count in sell through and appear as open dots on the market chart, never as sale prices.

## Manual and F1

The F355, 360, F430, 575M, 599 and 612 were sold with a factory six speed manual or the F1 gearbox, and their
model pages split into the two (`pipeline/taxonomy.py`, `gearbox`). A car counts as manual only when one of its
listings or lots says so. A conversion, or a car whose listings never mention the gearbox, counts as F1. When the
owner knows a car's gearbox and its listings stay silent, it goes in `config/gearbox.yaml` by VIN or chassis
number (`ZFFEW58A350141572: manual`), which wins over the listings on every page.

## Fair value

Every car for sale, and every lot coming up at auction, carries a fair value: what it should sell for at
auction today (`pipeline/fairvalue.py`).

    ln fair value = model level
                  + year slope    x (year, from the model's median year) / 5
                  + mileage slope x ln(1 + miles/1000), from the model's typical mileage
                  + the model's own colour effect against red
                  + special order paint
                  + manual (F355, 360, F430, 575M, 599, 612)
                  + auction effect at the model's usual mix of auctions
                  + 0.75 x n/(n+2) x how far the model's latest n sales (up to six, last six months) landed from the fit
                  + 0.75 x how far the car's own last auction sale landed from the fit, when it has one

One regression on log price runs over every model at once, from three years of auction sales and today's asks.
Year, mileage and the trend over time have a market wide slope and a per model adjustment pulled toward it, and
each model's colour effects are pulled toward the market wide ones until it has the cars to say otherwise.
Asking or sold, months since the sale and project cars are in the fit but not in a fair value (it is a sale,
today, of a usable car). Auction lots with no colour or mileage field get them from the catalogue text. Cars far
off the fit are dropped and it is run again. The range is the 80% band of how far that model's sales land from it.

The model's current median ask was tested as a term too: on sales of the last four months it made fair values
further off at every weight (15.7% median miss without it, 16.4% at 0.25, 17.3% at 0.5), so it carries no weight
and is shown beside the fair value instead.

Each model's sales are valued in turn by a fit that never saw them (five folds). A model's fair values are
shown only when it has at least 6 sales in three years and that held out median miss is 20% or less. In
November 2026 that is 35 models and about 1,430 of the 1,750 cars for sale. An ask over 2.5 times, or under 0.4
times, the fair value is most likely another car or a placeholder price, so it shows none.

## Market panel periods

Every model's market panel (and car page) runs over 30 days, 90 days, 1 year, 3 years or 5 years, and opens with
how far the market moved over that period (`pulse.model_moves`): 30 days from asking prices, model year by model
year, since the snapshot a month ago (our first snapshot while there is less than a month); the longer periods
from auction sales set against the fair value fit with time taken out, the newest sales (90 days, 180 days for a
year, a year beyond) against as many days that ended the period ago, with at least 3 sales on each side. Models
sold with both gearboxes have the same for each gearbox, and their 6-Speed and F1 pages carry their own 7 day
change and year on year sale change.

## Market movers and Value my car

The overview's movers switch between 30 days, 90 days and 12 months (`pipeline/pulse.py`). 30 days compares
asking prices model year by model year against the snapshot a month back (our first snapshot while there is
less than a month). 90 days and 12 months compare auction sales in the period with the period before, each
sale set against the fair value fit with the passing of time taken out, so a move is in what the same car
sells for. The hero shows the same comparison over the whole market, and its chart is that adjusted price
month by month over two years.

Value my car (`#value`) offers every model with sales or asks, not only those whose fair values pass the
test: the others are marked limited data, with a range widened to their held out miss (capped at about half to
twice the estimate). Only names that cover cars too different to price together (a held out sale off by more
than double, like the 250) are left out. It prices a car in the browser from `site/data/valuer.json`, the fair value fit's
coefficients for each model whose fair values are shown: model, year, mileage, the model's own colour effects,
gearbox, special order paint and the model's recent sales.

## Market panel on model pages

Each model page has one market panel, like classic.com's price tracker: a time range (1, 3 or 5 years, or all),
the sale figures for it (average sale, number of sales, dollar volume, lowest, top and most recent sale, the
median ask and the cars for sale now) and a tab per view:

- Sales: every sale, high bid and car for sale on a timeline, with the running median sale and the median ask.
- Mileage: price against mileage with the fitted curve.
- Years: sales and asks by model year, with each year's median.
- Variants: the model's sibling versions (export.VARIANT_GROUPS, or its family) as running medians of their sales.
- Volume: auction results month by month, sold and not sold.
- Locations: cars for sale by US state, and sales by country and by auction house.
- Listings: cars for sale week by week, with new, gone and the median ask (pipeline/weekly.py).
- Colours: the colour premiums against red.

### The CarGurus price tracker

The owner's tracker (a Google Sheet, one tab per model, a weekly asking price per VIN since Mar 2, 2026) is
stored in config/tracker/<model id>.csv and joined by VIN with our own listing dates for the weekly counts.
Weeks before our daily snapshots began are a floor and show lighter. Models the tracker does not cover start
at our first snapshot. To refresh the tabs from a workbook export:

    python -m pipeline.tracker path/to/export.txt

When the sheet is shared as "anyone with the link can view", each run reads the tabs itself (settings.tracker).

## Colour premiums

`pipeline/colors.py` measures what each exterior colour is worth against red. Paint names
are put in families first (Rosso Corsa, Rosso Scuderia and Red are all red, Grigio
Silverstone is grey). Ferrari Approved's "Extra Sample" (special order paint) names no colour,
so such a car takes its colour from any other listing of the same VIN and carries a special
order flag, fitted as its own term. For each
model it fits log price to year, mileage, sold or asking, special order, and colour family, with red left
out as the baseline, so a colour whose cars happen to be newer is not credited with the
year. Each family gets a premium in percent and a 90% range, and is called a premium or a
discount only when that range stays on one side of red. A family needs 3 cars and red needs
3 cars. The figure for every model gives each model and three year band its own level, so
cars are only ever compared within their model. The result sits in each model file under
`colors` and in `site.json` under `colors`, and the model page draws it.
