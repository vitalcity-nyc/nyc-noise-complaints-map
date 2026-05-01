# New York City noise complaints + DEP violations map

Interactive map of every NYC 311 noise complaint filed in 2025 and year-to-date 2026 — about 1 million records — plus every DEP-issued noise-code violation in the same window, including noise-camera tickets for loud vehicles. Complaints show as a hex grid; chronic addresses and violations as overlay markers.

**Live:** https://vitalcity-nyc.github.io/nyc-noise-complaints-map/

## Features

- Hex grid (~1 sq km cells) showing complaint density across all five boroughs, colored by percentile rank so internal differentiation is visible.
- Filter by noise type: residential, street/sidewalk, commercial, vehicle, helicopter, park, house of worship, or general.
- Period toggle: 2025, year-to-date 2026, or combined.
- Time-of-day filter (morning / afternoon / evening / late night) with a small bar chart showing complaint counts per bucket.
- "Last Saturday night" mode showing every individual noise complaint from the most recent Sat 6 PM – Sun 6 AM ET window, color-coded by type.
- Chronic-address overlay with selectable threshold (25+, 50+, 100+, 200+ complaints in the period).
- DEP noise-violations overlay (separate dataset) showing every noise-code ticket issued, including noise-camera vehicle-sound tickets.
- Click any cell, dot, or marker for a per-type breakdown and details.

## Build

```bash
pip install h3
python3 scripts/build_data.py
```

The script writes:

- `data/hex_2025.json`, `data/hex_2026ytd.json`, `data/hex_combined.json`
- `data/chronic_2025.json`, `data/chronic_2026ytd.json`, `data/chronic_combined.json`
- `data/meta.json` (generation timestamp + row counts + subtype list)

Run `python3 scripts/build_data.py --dry-run` to see record counts by complaint type without writing any files.

## Refresh

`.github/workflows/refresh.yml` runs the build nightly and commits any changes.

## Embedding

The page is structured so that the map UI sits inside `<div class="ncm-root">`, with all CSS scoped under `.ncm-root`. To embed in another site, copy the `.ncm-root` block (HTML + scoped CSS + script) into your page, point the data fetches at this repo's GitHub Pages URL, and size the container however you want.

## Methodology

See [methodology.md](methodology.md) for data sources, filters, aggregation rules, the helicopter-complaint caveat, and the limits of what 311 data can tell you about how loud the city actually is.

## Stack

- [Leaflet](https://leafletjs.com/) + [Leaflet.heat](https://github.com/Leaflet/Leaflet.heat) for the map and heatmap layer.
- [H3](https://h3geo.org/) for hex aggregation.
- CARTO Voyager basemap.
- Static HTML, no build step. Data refreshed nightly via GitHub Actions.

## Data source

NYC 311 Service Requests, dataset `erm2-nwe9` on [NYC Open Data](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9).
