#!/usr/bin/env python3
"""Build aggregated NYC 311 noise-complaint data for the map.

Pulls from NYC Open Data (Socrata dataset erm2-nwe9) and writes:
  data/hex_<period>.json     — H3 res-8 hex counts, per subtype
  data/chronic_<period>.json — locations with >= MIN_CHRONIC complaints, with subtype + descriptor breakdown
  data/meta.json             — generation timestamp, totals, subtype list, descriptor lists

Periods written: 2025, 2026ytd, combined.

Usage:
  python3 build_data.py                      # full build, write data/
  python3 build_data.py --dry-run            # fetch counts only, no files written
  python3 build_data.py --since 2025-01-01   # override start date
  python3 build_data.py --cache fetched.json # cache raw fetch to disk for re-runs
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import h3
except ImportError:
    sys.stderr.write("Missing dependency. Install with: pip install h3\n")
    sys.exit(1)

API_BASE = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
DEFAULT_SINCE = "2025-01-01T00:00:00"
DEFAULT_UNTIL = "2026-05-01T00:00:00"  # exclusive
PAGE_SIZE = 50000
HEX_RES = 8
MIN_CHRONIC = 10                        # write any location with >= 10; threshold slider in UI
LOC_ROUND = 2000                        # ~50m blocks (round lat/lng to 1/2000 deg)
HERE = Path(__file__).resolve().parent.parent


def http_get_json(url: str, retries: int = 4) -> list:
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "nyc-noise-map/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            last_err = exc
            wait = 2 ** attempt
            sys.stderr.write(f"  retry {attempt+1}/{retries} after {wait}s: {exc}\n")
            time.sleep(wait)
    raise RuntimeError(f"GET failed after {retries} attempts: {last_err}")


def fetch_count(since: str, until: str) -> int:
    where = (
        f"complaint_type LIKE 'Noise%' "
        f"AND created_date >= '{since}' "
        f"AND created_date < '{until}' "
        f"AND latitude IS NOT NULL"
    )
    qs = urllib.parse.urlencode({"$select": "count(*)", "$where": where})
    rows = http_get_json(f"{API_BASE}?{qs}")
    return int(rows[0].get("count", rows[0].get("count_1", 0))) if rows else 0


def fetch_all(since: str, until: str, limit: int = PAGE_SIZE) -> list[dict]:
    select = ",".join([
        "created_date", "complaint_type", "descriptor",
        "latitude", "longitude", "incident_address", "borough", "community_board",
    ])
    where = (
        f"complaint_type LIKE 'Noise%' "
        f"AND created_date >= '{since}' "
        f"AND created_date < '{until}' "
        f"AND latitude IS NOT NULL"
    )
    out: list[dict] = []
    offset = 0
    while True:
        qs = urllib.parse.urlencode({
            "$select": select,
            "$where": where,
            "$order": "created_date",
            "$limit": limit,
            "$offset": offset,
        })
        url = f"{API_BASE}?{qs}"
        sys.stderr.write(f"  fetching offset={offset:,}…\n")
        page = http_get_json(url)
        if not page:
            break
        out.extend(page)
        if len(page) < limit:
            break
        offset += limit
        time.sleep(0.1)
    return out


def period_for(created: str) -> str | None:
    # created looks like '2025-08-23T00:00:00.000'
    if not created or len(created) < 4:
        return None
    yr = created[:4]
    if yr == "2025":
        return "2025"
    if yr == "2026":
        return "2026ytd"
    return None


def loc_key(lat: float, lng: float) -> tuple[float, float]:
    return (round(lat * LOC_ROUND) / LOC_ROUND, round(lng * LOC_ROUND) / LOC_ROUND)


def title_case(s: str) -> str:
    if not s:
        return ""
    return s.title().replace("'S", "'s")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=DEFAULT_SINCE)
    p.add_argument("--until", default=DEFAULT_UNTIL)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--cache", default=None, help="path to cache raw fetch JSON")
    p.add_argument("--out", default=str(HERE / "data"), help="output directory")
    args = p.parse_args()

    since, until = args.since, args.until
    sys.stderr.write(f"NYC 311 noise complaints | {since} → {until}\n")

    sanity = fetch_count(since, until)
    sys.stderr.write(f"Server-side count for window: {sanity:,}\n")

    if args.dry_run:
        # extra: count by complaint_type
        select = "complaint_type, count(*) AS n"
        where = (
            f"complaint_type LIKE 'Noise%' "
            f"AND created_date >= '{since}' "
            f"AND created_date < '{until}'"
        )
        qs = urllib.parse.urlencode({
            "$select": select, "$where": where,
            "$group": "complaint_type", "$order": "n DESC",
        })
        rows = http_get_json(f"{API_BASE}?{qs}")
        sys.stderr.write("By complaint_type:\n")
        for r in rows:
            sys.stderr.write(f"  {int(r['n']):>9,}  {r['complaint_type']}\n")
        return 0

    rows: list[dict]
    if args.cache and os.path.exists(args.cache):
        sys.stderr.write(f"Using cached raw fetch: {args.cache}\n")
        rows = json.loads(Path(args.cache).read_text())
    else:
        rows = fetch_all(since, until)
        sys.stderr.write(f"Fetched {len(rows):,} rows.\n")
        if args.cache:
            Path(args.cache).write_text(json.dumps(rows))

    # Discover subtypes (complaint_type) sorted by frequency
    subtype_counts: dict[str, int] = defaultdict(int)
    descriptor_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        subtype_counts[r.get("complaint_type", "")] += 1
        descriptor_counts[r.get("descriptor") or "(unknown)"] += 1
    subtypes = [s for s, _ in sorted(subtype_counts.items(), key=lambda x: -x[1]) if s]
    sub_idx = {s: i for i, s in enumerate(subtypes)}
    sys.stderr.write(f"Subtypes ({len(subtypes)}): \n")
    for s in subtypes:
        sys.stderr.write(f"  {subtype_counts[s]:>9,}  {s}\n")

    # Aggregate hexes per period
    # hex_data[period][hex_id] = [total, [counts_per_subtype]]
    periods = ["2025", "2026ytd", "combined"]
    hex_data: dict[str, dict[str, list]] = {p: {} for p in periods}
    chronic_data: dict[str, dict[tuple, dict]] = {p: {} for p in periods}

    skipped_geo = 0
    for r in rows:
        try:
            lat = float(r["latitude"]); lng = float(r["longitude"])
        except (KeyError, TypeError, ValueError):
            skipped_geo += 1; continue
        if not (40.4 < lat < 41.0 and -74.3 < lng < -73.6):
            skipped_geo += 1; continue
        per = period_for(r.get("created_date", ""))
        if per is None:
            continue
        sub = r.get("complaint_type", "")
        si = sub_idx.get(sub)
        if si is None:
            continue

        hex_id = h3.latlng_to_cell(lat, lng, HEX_RES)
        for p in (per, "combined"):
            entry = hex_data[p].get(hex_id)
            if entry is None:
                entry = [0, [0] * len(subtypes)]
                hex_data[p][hex_id] = entry
            entry[0] += 1
            entry[1][si] += 1

        # Chronic location aggregation
        key = loc_key(lat, lng)
        for p in (per, "combined"):
            loc = chronic_data[p].get(key)
            if loc is None:
                loc = {
                    "lat_sum": 0.0, "lng_sum": 0.0, "count": 0,
                    "addrs": defaultdict(int),
                    "boros": defaultdict(int),
                    "cbs": defaultdict(int),
                    "subtypes": defaultdict(int),
                    "descriptors": defaultdict(int),
                }
                chronic_data[p][key] = loc
            loc["lat_sum"] += lat; loc["lng_sum"] += lng; loc["count"] += 1
            if r.get("incident_address"):
                loc["addrs"][r["incident_address"]] += 1
            if r.get("borough"):
                loc["boros"][r["borough"]] += 1
            if r.get("community_board"):
                loc["cbs"][r["community_board"]] += 1
            loc["subtypes"][sub] += 1
            d = r.get("descriptor") or ""
            if d:
                loc["descriptors"][d] += 1

    sys.stderr.write(f"Skipped (bad/out-of-bounds geo): {skipped_geo:,}\n")

    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)

    # Write hex files
    for p in periods:
        hexes = []
        for hex_id, (total, by_sub) in hex_data[p].items():
            hexes.append([hex_id, total, by_sub])
        hexes.sort(key=lambda h: -h[1])
        payload = {
            "period": p,
            "subtypes": subtypes,
            "hexes": hexes,
        }
        path = out_dir / f"hex_{p}.json"
        path.write_text(json.dumps(payload, separators=(",", ":")))
        sys.stderr.write(f"Wrote {path} ({len(hexes):,} hexes, {path.stat().st_size/1024:.0f}KB)\n")

    # Write chronic files
    for p in periods:
        locs = []
        for (lat_r, lng_r), loc in chronic_data[p].items():
            if loc["count"] < MIN_CHRONIC:
                continue
            n = loc["count"]
            top_addr = max(loc["addrs"].items(), key=lambda x: x[1])[0] if loc["addrs"] else ""
            top_boro = max(loc["boros"].items(), key=lambda x: x[1])[0] if loc["boros"] else ""
            top_cb = max(loc["cbs"].items(), key=lambda x: x[1])[0] if loc["cbs"] else ""
            top_desc = max(loc["descriptors"].items(), key=lambda x: x[1])[0] if loc["descriptors"] else ""
            locs.append({
                "lat": round(loc["lat_sum"] / n, 6),
                "lng": round(loc["lng_sum"] / n, 6),
                "n": n,
                "addr": title_case(top_addr),
                "boro": title_case(top_boro),
                "cb": top_cb,
                "subs": dict(loc["subtypes"]),
                "top_desc": top_desc,
            })
        locs.sort(key=lambda x: -x["n"])
        payload = {
            "period": p,
            "min_count": MIN_CHRONIC,
            "subtypes": subtypes,
            "locations": locs,
        }
        path = out_dir / f"chronic_{p}.json"
        path.write_text(json.dumps(payload, separators=(",", ":")))
        sys.stderr.write(f"Wrote {path} ({len(locs):,} chronic locations, {path.stat().st_size/1024:.0f}KB)\n")

    meta = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "since": since, "until": until,
        "total_rows": len(rows),
        "subtypes": subtypes,
        "subtype_counts": {s: subtype_counts[s] for s in subtypes},
        "min_chronic": MIN_CHRONIC,
        "hex_resolution": HEX_RES,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    sys.stderr.write(f"Wrote {out_dir/'meta.json'}\n")
    sys.stderr.write("Done.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
