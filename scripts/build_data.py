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
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Eastern time = UTC-5 standard, UTC-4 daylight. NYC observes DST. We pick a
# fixed UTC offset for the Saturday-night window — close enough; the exact
# DST boundary day will be off by an hour but the editorial point holds.
ET_OFFSET_HOURS = 4  # treat ET ~ UTC-4 (EDT)

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

# Known 311 reporting artifacts to exclude. Each entry is a (lat_rounded, lng_rounded)
# block at LOC_ROUND precision. These addresses produce complaint volumes that are
# physically impossible (e.g., 8,000+/month at a single address), reflecting data-
# quality issues (geocoding fallbacks, automated repeat callers, default-address
# fill-ins) rather than real noise events. Documented in methodology.md.
ARTIFACT_BLOCKLIST: set[tuple[float, float]] = {
    (40.892, -73.86),  # 655 East 230 Street, Bronx — ~128k complaints / 16 months
}


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


# Time-of-day buckets in local Eastern hours
# 0 = Morning (6–12), 1 = Afternoon (12–18), 2 = Evening (18–24), 3 = Late night (0–6)
NUM_BUCKETS = 4

def bucket_for(created_utc: str) -> int | None:
    if not created_utc or len(created_utc) < 16:
        return None
    try:
        t = datetime.strptime(created_utc[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    hour = (t - timedelta(hours=ET_OFFSET_HOURS)).hour
    if 6 <= hour < 12:
        return 0
    if 12 <= hour < 18:
        return 1
    if 18 <= hour < 24:
        return 2
    return 3


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

    # Aggregate hexes per period.
    # New shape: hex_data[period][hex_id] = list of length NUM_BUCKETS, each a
    # list of length len(subtypes) of counts. Total/per-subtype counts can be
    # summed at render time. This adds a fourth dimension (time-of-day) at
    # ~4x the previous storage but the absolute size stays small.
    periods = ["2025", "2026ytd", "combined"]
    hex_data: dict[str, dict[str, list]] = {p: {} for p in periods}
    chronic_data: dict[str, dict[tuple, dict]] = {p: {} for p in periods}

    def new_hex_entry():
        return [[0] * len(subtypes) for _ in range(NUM_BUCKETS)]

    skipped_geo = 0
    skipped_artifact = 0
    skipped_no_bucket = 0
    for r in rows:
        try:
            lat = float(r["latitude"]); lng = float(r["longitude"])
        except (KeyError, TypeError, ValueError):
            skipped_geo += 1; continue
        if not (40.4 < lat < 41.0 and -74.3 < lng < -73.6):
            skipped_geo += 1; continue
        # Skip known reporting artifacts (see ARTIFACT_BLOCKLIST above)
        if loc_key(lat, lng) in ARTIFACT_BLOCKLIST:
            skipped_artifact += 1; continue
        per = period_for(r.get("created_date", ""))
        if per is None:
            continue
        sub = r.get("complaint_type", "")
        si = sub_idx.get(sub)
        if si is None:
            continue
        bi = bucket_for(r.get("created_date", ""))
        if bi is None:
            skipped_no_bucket += 1; continue

        hex_id = h3.latlng_to_cell(lat, lng, HEX_RES)
        for p in (per, "combined"):
            entry = hex_data[p].get(hex_id)
            if entry is None:
                entry = new_hex_entry()
                hex_data[p][hex_id] = entry
            entry[bi][si] += 1

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
                    "buc": [[0] * len(subtypes) for _ in range(NUM_BUCKETS)],
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
            loc["buc"][bi][si] += 1
            d = r.get("descriptor") or ""
            if d:
                loc["descriptors"][d] += 1

    sys.stderr.write(f"Skipped (bad/out-of-bounds geo): {skipped_geo:,}\n")
    sys.stderr.write(f"Skipped (artifact blocklist): {skipped_artifact:,}\n")

    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)

    # Write hex files
    bucket_labels = ["morning", "afternoon", "evening", "late_night"]
    for p in periods:
        hexes = []
        for hex_id, buckets in hex_data[p].items():
            total = sum(sum(row) for row in buckets)
            hexes.append([hex_id, total, buckets])
        hexes.sort(key=lambda h: -h[1])
        payload = {
            "period": p,
            "subtypes": subtypes,
            "buckets": bucket_labels,
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
            # subtype_totals derived from buckets so the new and old fields stay consistent
            sub_totals = [0] * len(subtypes)
            for row in loc["buc"]:
                for i, c in enumerate(row):
                    sub_totals[i] += c
            subs = {subtypes[i]: sub_totals[i] for i in range(len(subtypes)) if sub_totals[i]}
            locs.append({
                "lat": round(loc["lat_sum"] / n, 6),
                "lng": round(loc["lng_sum"] / n, 6),
                "n": n,
                "addr": title_case(top_addr),
                "boro": title_case(top_boro),
                "cb": top_cb,
                "subs": subs,
                "buc": loc["buc"],  # [4 buckets][len(subtypes)]
                "top_desc": top_desc,
            })
        locs.sort(key=lambda x: -x["n"])
        payload = {
            "period": p,
            "min_count": MIN_CHRONIC,
            "subtypes": subtypes,
            "buckets": bucket_labels,
            "locations": locs,
        }
        path = out_dir / f"chronic_{p}.json"
        path.write_text(json.dumps(payload, separators=(",", ":")))
        sys.stderr.write(f"Wrote {path} ({len(locs):,} chronic locations, {path.stat().st_size/1024:.0f}KB)\n")

    # ---- Last Saturday night ----
    # Window: Saturday 6:00 PM ET through Sunday 6:00 AM ET (12-hour window).
    # We anchor on UTC-now and walk back to the most recent past Saturday.
    now_utc = datetime.now(timezone.utc)
    days_back = (now_utc.weekday() - 5) % 7  # weekday(): Mon=0 .. Sat=5
    if days_back == 0 and now_utc.hour < (18 + ET_OFFSET_HOURS) % 24:
        # If it's Saturday before the window starts, use the prior Saturday
        days_back = 7
    last_sat_date = (now_utc - timedelta(days=days_back)).date()
    sat_start_utc = datetime(last_sat_date.year, last_sat_date.month, last_sat_date.day,
                             18 + ET_OFFSET_HOURS, 0, 0, tzinfo=timezone.utc)
    sat_end_utc = sat_start_utc + timedelta(hours=12)
    sat_start_str = sat_start_utc.strftime("%Y-%m-%dT%H:%M:%S")
    sat_end_str = sat_end_utc.strftime("%Y-%m-%dT%H:%M:%S")
    sys.stderr.write(f"\nFetching last Saturday-night window: {sat_start_str} → {sat_end_str} UTC\n")

    sat_rows: list[dict] = []
    sat_offset = 0
    sat_select = "created_date,complaint_type,descriptor,latitude,longitude,incident_address,borough"
    sat_where = (
        f"complaint_type LIKE 'Noise%' "
        f"AND created_date >= '{sat_start_str}' "
        f"AND created_date < '{sat_end_str}' "
        f"AND latitude IS NOT NULL"
    )
    while True:
        qs = urllib.parse.urlencode({
            "$select": sat_select, "$where": sat_where,
            "$order": "created_date", "$limit": PAGE_SIZE, "$offset": sat_offset,
        })
        page = http_get_json(f"{API_BASE}?{qs}")
        if not page:
            break
        sat_rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        sat_offset += PAGE_SIZE

    sat_points = []
    for r in sat_rows:
        try:
            lat = float(r["latitude"]); lng = float(r["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (40.4 < lat < 41.0 and -74.3 < lng < -73.6):
            continue
        if loc_key(lat, lng) in ARTIFACT_BLOCKLIST:
            continue
        sub = r.get("complaint_type", "")
        si = sub_idx.get(sub)
        if si is None:
            continue
        # Compact format: [lat, lng, subtype_index, hour_local, address, descriptor]
        created = r.get("created_date", "")
        try:
            t = datetime.strptime(created[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
            hour_local = (t - timedelta(hours=ET_OFFSET_HOURS)).hour
        except ValueError:
            hour_local = -1
        sat_points.append([
            round(lat, 5), round(lng, 5), si, hour_local,
            (r.get("incident_address") or "").title()[:80],
            r.get("descriptor") or "",
        ])
    sys.stderr.write(f"Saturday-night fetched: {len(sat_rows):,}, points kept: {len(sat_points):,}\n")

    saturday_payload = {
        "subtypes": subtypes,
        "date_local": last_sat_date.isoformat(),
        "window_label": f"Sat 6:00 PM – Sun 6:00 AM, {last_sat_date.strftime('%b %-d, %Y')}",
        "start_utc": sat_start_str,
        "end_utc": sat_end_str,
        "points": sat_points,
    }
    (out_dir / "saturday_night.json").write_text(json.dumps(saturday_payload, separators=(",", ":")))
    sys.stderr.write(f"Wrote {out_dir/'saturday_night.json'} ({len(sat_points):,} points)\n")

    meta = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "since": since, "until": until,
        "total_rows": len(rows),
        "rows_excluded_artifact": skipped_artifact,
        "rows_excluded_geo": skipped_geo,
        "subtypes": subtypes,
        "subtype_counts": {s: subtype_counts[s] for s in subtypes},
        "min_chronic": MIN_CHRONIC,
        "hex_resolution": HEX_RES,
        "saturday_date": last_sat_date.isoformat(),
        "saturday_count": len(sat_points),
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    sys.stderr.write(f"Wrote {out_dir/'meta.json'}\n")
    sys.stderr.write("Done.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
