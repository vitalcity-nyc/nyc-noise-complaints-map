# Methodology — New York City noise complaints + DEP violations map

This document describes how the data behind the map is collected, filtered, aggregated, and rendered. Nothing here should be a black box.

The map combines two distinct datasets: 311 noise complaints (who *calls* about noise) and DEP-issued noise-code violations (where the city actually *fines* for it). They are not the same thing — and the gap between them is part of what the map is for.

## Data source 1 — 311 noise complaints

- **Dataset:** NYC 311 Service Requests on NYC Open Data — Socrata ID `erm2-nwe9`.
- **API:** `https://data.cityofnewyork.us/resource/erm2-nwe9.json`.
- **Date field used:** `created_date` (when the complaint was filed). We do not use `closed_date` or any other field.
- **Filter:** complaint records where `complaint_type` begins with the string "Noise". This pulls every NYC 311 noise category and excludes everything else.
- **Geographic filter:** records with non-null `latitude` and `longitude`, inside the rough NYC bounding box (40.4–41.0 N, -74.3 to -73.6 W). Records without coordinates or with off-map coordinates are dropped.

## Time window

- **Full year 2025:** complaints with `created_date` from 2025-01-01 through 2025-12-31.
- **Year-to-date 2026:** complaints with `created_date` from 2026-01-01 through the most recent refresh.
- **Combined view:** the union of the two periods.

The exact `until` cutoff for the most recent build is recorded in `data/meta.json`.

## Noise types (subtypes)

We use the dataset's own `complaint_type` values unchanged for filtering and counting. Each 311 service request carries exactly one `complaint_type`, so the categories below do not overlap and their counts sum to the total. As of the current build:

| complaint_type | Panel label | Why it shows up |
| --- | --- | --- |
| Noise - Residential | Noise - Residential | Loud music/parties, banging, talking, TV in apartments — the largest category by far. Routed to the NYPD. |
| Noise - Street/Sidewalk | Noise - Street/Sidewalk | Loud music or talking on sidewalks, idle car stereos, street fairs. Routed to the NYPD. |
| Noise - Commercial | Noise - Commercial | Bars, clubs, restaurants, retail. Routed to the NYPD. |
| Noise | Noise – Construction/equipment (DEP) | The category routed to the Dept. of Environmental Protection (DEP) rather than the NYPD. **Not** an "other/unspecified" bucket — its descriptors are construction before/after hours, construction and lawn equipment, jackhammering, HVAC/ventilation, alarms, manufacturing, barking dogs and ice-cream trucks. (Only ~38 records carry the genuine "Other Noise Sources" descriptor.) Relabeled in the panel because a bare "Noise" entry reads like a parent or catch-all next to the "Noise - X" types when it is a distinct peer. |
| Noise - Vehicle | Noise - Vehicle | Car alarms, modified exhausts, motorcycles. Routed to the NYPD. |
| Noise - Helicopter | Noise - Helicopter | Helicopter overflight complaints. **See caveat below.** |
| Noise - Park | Noise - Park | Music, parties, gatherings in parks. |
| Noise - House of Worship | Noise - House of Worship | Bells, amplified worship, etc. |

The "Noise type" filter in the panel switches between these. Only the displayed label differs from the raw `complaint_type`; the bare "Noise" type is shown as "Noise – Construction/equipment (DEP)" and each option carries a hover definition.

## Aggregation

The raw data is roughly 1 million records over the time window. We do not load raw points into the browser. Instead, an offline Python script (`scripts/build_data.py`) aggregates the data twice:

1. **Hex grid (`data/hex_<period>.json`).** Every complaint is assigned to an Uber [H3](https://h3geo.org/) hexagon at resolution 8 (each hex covers about 0.74 sq km, roughly four to five Manhattan blocks). For each hex we record the total count and a per-subtype breakdown. The map's heatmap and grid views are driven by these aggregates.
2. **Chronic addresses (`data/chronic_<period>.json`).** Latitudes and longitudes are rounded to 1/2000 of a degree (about 50 meters), creating "blocks." Any block with at least 10 complaints in the period is written out, with its top address, top community board, and per-subtype/per-descriptor breakdowns. The map's chronic-address dots are drawn from this file.

## Heatmap

The heatmap is a kernel-density rendering (Leaflet.heat) fed by the centroids of each H3 hex, weighted by complaint count.

- The intensity scale is **log-transformed by default** because residential noise is so dominant numerically that a linear scale washes everything else out.
- The maximum intensity is **capped at the 95th percentile** of hex counts. A small number of hexes (notably around LaGuardia Airport, JFK, and Manhattan heliports) have helicopter-noise complaint volumes high enough to skew the entire map's color scale. The cap means those hexes still appear at maximum intensity but do not pull the rest of the city down to pale yellow.
- The user can switch to a linear scale via the "Heat color scale" toggle.

## Chronic addresses

A chronic address is a ~50m point on the map with at least the threshold number of complaints in the selected period. The threshold defaults to 50 and can be changed to 25, 100, or 200.

- **Top-N rendering at low zoom.** At citywide zoom, only the top 250 worst chronic addresses are drawn as dots, even when more meet the threshold. As you zoom in, more are revealed: up to 600 at zoom 12, 1,500 at zoom 13, and all of them at zoom 14 and higher. The "Chronic addresses" stat in the panel is the full count regardless of zoom.
- **Coordinate rounding.** Lat/lng are rounded to ~50m blocks before counting. Two complaints at addresses 20 meters apart are treated as the same chronic location. This produces fewer, more useful pins than treating every distinct street address as its own location.
- **Marker style.** Dots scale gently with complaint count and are deliberately small and dark so they read as editorial pinpoints on top of the heatmap rather than competing with it.

## Data source 2 — DEP noise-code violations

- **Dataset:** OATH Hearings Division Case Status on NYC Open Data — Socrata ID `jz4z-kudi`. This dataset records every administrative violation processed through the Office of Administrative Trials and Hearings.
- **Filter:** `issuing_agency = 'DEP - BUREAU OF ENV. COMPLIANC'` AND `charge_1_code` begins with "BN" (the noise-code section). Date floor: 2025-01-01.
- **Categories used in the legend:**
  - **Vehicle sound (noise-camera tickets)** — charge codes BNZ6 / BNZ7 / BNZ8: "Cause/permit sound from motor vehicle on public ROW VTL limit." These are the actual noise-camera enforcement tickets, issued under the "Stop Spreading the Noise Act" (Local Law 75 of 2021).
  - **Construction / HVAC noise** — BN14, BN17, BN20, BN29, BN30, BN32, BN5V, BNC4, etc. Construction at impermissible times, missing or unimplemented noise-mitigation plans, and HVAC ("circulation device") sound exceeding 42 dB(A).
  - **Commercial music / unreasonable noise** — BN10, BN37, BN60: music from a commercial establishment over permitted levels, or general "unreasonable noise" violations.
  - **Vehicle horn / personal audio** — BN42, BN49: unauthorized horn use, personal audio device in a motor vehicle.
  - **Other** — every other BN* code.
- **Geocoding:** the OATH dataset gives `violation_location_house` + `violation_location_street_name` + `violation_location_borough` but no lat/lng. We geocode the unique violation addresses through [NYC Planning Labs Geosearch](https://geosearch.planninglabs.nyc) (free, public, no API key). Results are cached in `scripts/.geocode_cache.json` so subsequent builds only geocode new addresses.
- **Note on noise-camera locations.** DEP does not publish the physical locations of its noise cameras (the agency cites concerns about vandalism and gaming the system). This map does not show camera locations directly. What it shows are the *addresses where noise-camera tickets were issued* — which collectively reveal where the cameras are operating.

## Why complaints and violations both matter

A 311 complaint is a resident saying "this is too loud." A DEP violation is the city saying "we agree, here's a fine." The two datasets answer different questions:

- Where do residents *call* about noise? (complaints)
- Where does the city *enforce* against it? (violations)

Some places are loud and complained about but rarely enforced (residential apartment noise; bars in nightlife corridors). Some places have heavy enforcement that 311 complaints alone don't surface (specific stretches where noise cameras have been deployed). Toggling the layers makes the gap visible.

## Helicopter complaint caveat

NYC 311 helicopter-noise complaints are filed against an address — but the helicopter is rarely directly above that address. In the dataset, helicopter complaints frequently centroid at LaGuardia Airport, JFK Airport, and the Downtown Manhattan / East 34th Street Heliports rather than where the resident heard the rotor blades. This produces a cluster artifact at those airports/heliports that is real in the sense that those are real complaints — but it is not a measurement of where helicopter sound was experienced. Use the "Noise - Helicopter" filter with that in mind.

## Known data anomaly: 655 East 230 Street

The single highest-count chronic location in the current dataset is 655 East 230 Street in the Bronx, with about 128,000 noise complaints in the 2025 + 2026 YTD window. That is roughly 8,000 complaints per month, or 270 per day, at one address — physically impossible. This is a 311 system artifact rather than a real signal. Possible explanations include a single resident filing automated repeat complaints, or a geocoding fallback writing this address whenever the original location string fails to resolve. We do not silently filter this point out; it appears in the chronic data as-is, but readers should treat it as a reporting artifact. The same caveat applies to any other implausibly large single-address total.

## What this map is not

- **It is not a measurement of how loud New York is.** It measures who calls 311. Reporting rates vary substantially by neighborhood — by language, age, housing tenure, trust in government, and many other factors. A neighborhood that calls 311 less may not be quieter; it may simply complain less.
- **It is not in real time.** The data is refreshed nightly via GitHub Actions; the freshness date is shown in the panel.
- **It is not a list of buildings to avoid.** A chronic address in this map could be a single noisy bar, a long-standing construction site, or a corner where the same neighbor has filed dozens of complaints over months. Always read the popup before drawing conclusions.

## Refresh

The build script is run nightly by `.github/workflows/refresh.yml`. The workflow:

1. Calls `scripts/build_data.py` to pull the full window from Socrata.
2. Writes new aggregated JSON files to `data/`.
3. Commits the changes if anything actually changed.

A `meta.json` file records the generation timestamp, the date range used, and the row count.

## Reproducing this analysis

```bash
pip install h3
python3 scripts/build_data.py --since 2025-01-01 --until 2026-05-01
```

The repository is designed to be self-contained — every input is documented, every output is regenerable from raw NYC Open Data, and no editorial transformations happen between fetch and render that are not described here.
