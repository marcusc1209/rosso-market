---
name: Rosso Market
description: Ferrari asking prices against auction sales, overprinted daily
colors:
  paper: "#f3f3f0"
  sheet: "#ffffff"
  ink: "#17171b"
  ink-2: "#44454d"
  ink-3: "#64656e"
  rule: "#d9d9d4"
  wash: "#e8e8e3"
  giallo: "#ffcc00"
  ask: "#2340c8"
  ask-deep: "#18288a"
  sold: "#e0401c"
  sold-deep: "#a52a0e"
  up: "#12704a"
  down: "#b42318"
typography:
  display:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "5.5rem"
    fontWeight: 860
    lineHeight: 0.95
    letterSpacing: "-0.035em"
  headline:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "2.5rem"
    fontWeight: 780
    lineHeight: 1.08
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 760
    lineHeight: 1.1
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "0.9375rem"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "0em"
  label:
    fontFamily: "Archivo, Helvetica Neue, Arial, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 560
    lineHeight: 1.25
    letterSpacing: "0em"
rounded:
  none: "0px"
  dot: "50%"
spacing:
  xs: "6px"
  sm: "12px"
  md: "20px"
  lg: "32px"
  xl: "48px"
components:
  button-line:
    backgroundColor: "{colors.sheet}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "10px 18px"
  button-line-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.sheet}"
  button-solid:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.sheet}"
    rounded: "{rounded.none}"
    padding: "13px"
  button-solid-hover:
    backgroundColor: "{colors.ask}"
    textColor: "{colors.sheet}"
  header-band:
    backgroundColor: "{colors.giallo}"
    textColor: "{colors.ink}"
    height: "60px"
  chip-sold:
    backgroundColor: "{colors.sold}"
    textColor: "{colors.sheet}"
    rounded: "{rounded.none}"
    padding: "5px 7px"
  chip-live:
    backgroundColor: "{colors.giallo}"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "5px 7px"
  input-search:
    backgroundColor: "#fffbe6"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "9px 12px 9px 34px"
---

# Design System: Rosso Market

## Overview

Overprint. The site is drawn like the Milan racing posters and Ferrari yearbooks of the 1950s and 60s: flat translucent primaries printed over each other on white paper. Asking prices are cobalt, auction sales are vermilion, and where the two inks overlap they turn violet, which is where the market clears. A yellow band carries the header. Everything else is white sheets, hairline ink rules and one family of type.

It is a working market tool first. The world lends type, palette, density and one signature move, the overprint strip. Navigation, tabs, tables, selects and buttons stay the standard controls people already know.

## Colors

### Primary
Cobalt `ask` (#2340c8) is every asking price: listing dots, the ask band, the median ask line (in `ask-deep`). It also marks links and the for sale status.

### Secondary
Vermilion `sold` (#e0401c) is every auction sale: sale dots, the sold band, sold chips, the running median sold line (in `sold-deep`).

### Tertiary
Giallo `giallo` (#ffcc00) is the header band, text selection, live and ending soon chips, photo counts and the "this car" marker. Never a data series.

### Neutral
`paper` is the page ground, `sheet` the white of boards, tables and cards. `ink` is type and rules, `ink-2` secondary text, `ink-3` labels. `rule` is hairlines, `wash` hover rows and empty photo grounds. `up` and `down` colour changes only.

### Named Rules
**The Two Inks Rule.** Cobalt means asking, vermilion means sold, everywhere. No third data colour. Overlap is never painted: it comes from `mix-blend-mode: multiply` (screen in dark mode) at the ink opacity of 0.62.

**The Yellow Is Not Data Rule.** Giallo marks the shell, selection, liveness and the car you are looking at. It never encodes a price.

## Typography

One family, Archivo variable (width 62 to 125, weight 100 to 900), loaded from jsDelivr's fontsource package. Page titles run at width 125 and weight 860, section titles at width 116 to 118. Data and body stay at width 100 with tabular figures on the body.

### Hierarchy
Display (model and family names, up to 88px), headline (overview opening sentence, 26 to 40px), title (section h2, 24px), body (15px), label (12 to 13px, ink-3, sentence case). Labels sit beside or above values, never above headings.

## Layout

Content runs to 1560px with a 16 to 48px gutter. Sections are separated by a hairline and 48px of space (34px on phones). The overview opens with one sentence and a facts row, then the market board fills the first screen. Boards and tables sit on white sheets ruled in ink at the top. On phones board rows stack name and medians over a full width strip, cards set the photo beside the text, and the car page keeps price and the seller link in a bottom bar.

## Elevation & Depth

Flat. Depth exists only for things that float over the page: menus, search results and chart tips, with one soft shadow (0 24px 40px -12px at 22 to 30% black). Everything else is separated by rules, not shadows.

## Shapes

Square corners throughout. Circles only for data dots, the two inks of the logo mark, and timeline points. Rules are 1px hairlines, 2px ink for section tops, 3px ink on the car price box.

## Components

### Buttons
Outline buttons (`button-line`) are ink bordered on white and fill with ink on hover. The single solid action per page (contact seller, view lot) is `button-solid`, turning cobalt on hover.

### Chips
Square, 11px, weight 640. Sold is solid vermilion, not sold is vermilion outline, ending soon and live are giallo with ink border.

### Cards / Containers
Lot and listing cards: 2px ink top rule, photo, then text on paper. No card shadows, no side stripes. A missing photo shows the two ink circles at 30% instead of a label.

### Inputs / Fields
Search lives in the yellow band: pale yellow field, 1px ink border, drawn magnifier. Selects are square with a drawn chevron.

### Navigation
Header links in ink on giallo, current page marked by a 3px ink underline. Family tabs and page tabs use the same underline language.

### Overprint Strip
The signature component. A track with a cobalt band for the middle half of asks and a vermilion band for the middle half of sales, offset by half their height so both stay visible, thin lines for the full ranges, short deep ticks for medians. On the overview board each row is scaled to its own median sale (minus 50% to plus 120%), on model pages and the car box to absolute prices. Bands print in once with a 420ms left to right clip wipe.

## Do's and Don'ts

### Do:
- Do use cobalt for every ask and vermilion for every sale, in charts, chips and legends alike.
- Do let the overlap of the inks make the violet. Never fill it as a third colour.
- Do keep numbers in tabular figures and say what each figure is and since when.
- Do draw icons as inline SVG at one 1.6 stroke.

### Don't:
- Don't put a label or kicker above a heading.
- Don't use giallo for data, or add a third accent.
- Don't round corners or add card shadows.
- Don't fill a missing photo with text. Use the two ink placeholder.
