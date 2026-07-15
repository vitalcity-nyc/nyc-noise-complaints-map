# New York City noise complaints + DEP violations map

Interactive map of every NYC 311 noise complaint filed in 2025 and year-to-date 2026 — about 1.1 million records — plus every DEP-issued noise-code violation in the same window, including noise-camera tickets for loud vehicles. Complaints show as a hex grid; chronic addresses and violations as overlay markers.

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
- `data/saturday_night.json` (every complaint from the most recent Sat 6 PM – Sun 6 AM window)
- `data/violations.json` (geocoded DEP noise-code violations)
- `data/charts.json` (aggregates behind the three standalone charts in `charts/`)
- `data/meta.json` (generation timestamp + row counts + subtype list)

Run `python3 scripts/build_data.py --dry-run` to see record counts by complaint type without writing any files.

## Refresh

`.github/workflows/refresh.yml` runs the build daily (09:40 UTC) and commits any changes. The window's end date is computed at run time — it is always the last complete day — so nothing needs editing to keep the site current.

## Embedding

Add `?embed=1` to the map URL and it renders as a self-contained embed: no page chrome, a fixed-height shell (600px, 620px on phones), and a **Full screen** button in the top-right that expands the map to fill the screen. Paste this into a Ghost HTML card:

```html
<iframe id="noise-map-frame" src="https://vitalcity-nyc.github.io/nyc-noise-complaints-map/?embed=1"
        title="Where New York calls 311 about noise" allow="fullscreen" loading="lazy"
        style="width:100%;height:600px;border:0;display:block;"></iframe>
<script>
window.addEventListener('message', function (e) {
  var d = e.data;
  if (d && d.type === 'vc-embed-height' && d.id === 'noise-map') {
    document.getElementById('noise-map-frame').style.height = d.height + 'px';
  }
});
</script>
```

`allow="fullscreen"` is required — without it browsers block the Fullscreen API inside a cross-origin iframe. If it is missing, the button falls back to opening the standalone map in a new tab, so the click still does something. The embed posts its height to the parent (`vc-embed-height`), which is what the listener above uses so the iframe resizes itself on phones.

The map framing adapts to the container: the panel is a left overlay on wide screens and a bottom sheet under 720px, and the city is fitted to whatever space is left.

Alternatively, the map UI sits inside `<div class="ncm-root">` with all CSS scoped under `.ncm-root`, so the block can be copied wholesale into another page with the data fetches pointed at this repo's Pages URL.

### Charts

Each chart under `charts/` embeds the same way with `?embed=1`, posting `vc-embed-height` with its own id:

- `by-type.html` — share by complaint category (donut) — `noise-by-type`
- `by-time.html` — time-of-day split by category (heatmap) — `noise-by-time`
- `by-borough.html` — total and per-capita by borough (bars) — `noise-by-borough`
- `by-year.html` — complaints per year since 2020 (bars) — `noise-by-year`

To avoid stacking separate cards, `charts/all.html` wraps all four in one click-through card — a segmented tab control (By type · When it peaks · By borough · Over time) with prev/next arrows and arrow-key support, showing one chart at a time and resizing to it. It reuses the chart pages as inner frames, so they stay accurate as the data refreshes. It posts `vc-embed-height` with id `noise-charts`. Embed the consolidated card with:

```html
<iframe id="noise-charts-frame" src="https://vitalcity-nyc.github.io/nyc-noise-complaints-map/charts/all.html?embed=1"
        title="New York's 311 noise complaints, three ways" loading="lazy"
        style="width:100%;height:800px;border:0;display:block;"></iframe>
<script>
window.addEventListener('message', function (e) {
  if (e.data && e.data.type === 'vc-embed-height' && e.data.id === 'noise-charts') {
    document.getElementById('noise-charts-frame').style.height = e.data.height + 'px';
  }
});
</script>
```

## Methodology

See [methodology.md](methodology.md) for data sources, filters, aggregation rules, the helicopter-complaint caveat, and the limits of what 311 data can tell you about how loud the city actually is.

## Stack

- [Leaflet](https://leafletjs.com/) + [Leaflet.heat](https://github.com/Leaflet/Leaflet.heat) for the map and heatmap layer.
- [H3](https://h3geo.org/) for hex aggregation.
- CARTO Voyager basemap.
- Static HTML, no build step. Data refreshed daily via GitHub Actions.

## Data source

NYC 311 Service Requests, dataset `erm2-nwe9` on [NYC Open Data](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9).
