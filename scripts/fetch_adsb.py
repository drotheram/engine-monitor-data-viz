#!/usr/bin/env python3
"""
fetch_adsb.py - Fetch ADS-B track data from OpenSky Network

For each processed flight JSON in docs/data/{AIRCRAFT}/, checks whether a
corresponding _adsb.json file exists. If not, queries the OpenSky Network
REST API for the aircraft's track during that flight's time window.

Requirements:
  - OPENSKY_USER and OPENSKY_PASS environment variables (optional but needed
    for historical data > 1 hour old)
  - config.json with aircraft ICAO24 addresses filled in

ADS-B JSON format written:
  {
    "icao24": "c07d2e",
    "registration": "C-GJYY",
    "flight_id": "Flt0916_20260427",
    "source": "opensky",
    "track": [
      { "ts": 1714240074, "lat": 45.123, "lon": -75.456, "alt_m": 1234, "heading": 270 },
      ...
    ]
  }

Usage:
  python scripts/fetch_adsb.py [--config config.json] [--dry-run]
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent

OPENSKY_BASE = "https://opensky-network.org/api"
REQUEST_TIMEOUT = 30
RATE_LIMIT_DELAY = 5   # seconds between API calls (be polite)


def flight_time_window(flight_json: dict) -> tuple[int, int]:
    """
    Derive Unix timestamps for the flight start and end from the flight JSON.

    Uses the 'date' + 'zulu_time_start' fields from meta, and
    duration_minutes to compute end time.
    """
    meta = flight_json.get("meta", {})
    date_str = meta.get("date", "")          # "2026-04-27"
    zulu_str = meta.get("zulu_time_start", "") # "19:47:54"
    duration = meta.get("duration_minutes", 60)

    if not date_str or not zulu_str:
        return 0, 0

    try:
        start_dt = datetime.strptime(
            f"{date_str} {zulu_str}", "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return 0, 0

    start_ts = int(start_dt.timestamp())
    end_ts   = start_ts + int(duration * 60) + 600  # add 10-min buffer
    return start_ts, end_ts


def fetch_opensky_track(
    icao24: str,
    start_ts: int,
    session: requests.Session,
) -> list[dict]:
    """
    Fetch the flight track from OpenSky Network.

    The OpenSky `/tracks/all` endpoint returns the track for a single
    flight closest to the given `time` parameter.

    Returns a list of track point dicts, or [] on failure.
    """
    url = f"{OPENSKY_BASE}/tracks/all"
    params = {"icao24": icao24.lower(), "time": start_ts}

    try:
        resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            log.info(f"  No track found on OpenSky for ICAO24={icao24}")
            return []
        if resp.status_code == 401:
            log.warning("  OpenSky authentication required for historical data")
            return []
        if resp.status_code == 429:
            log.warning("  OpenSky rate limit hit, backing off 60s")
            time.sleep(60)
            return []
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        log.warning(f"  OpenSky request failed: {e}")
        return []

    # OpenSky response format:
    # { "icao24": "...", "callsign": "...", "startTime": ..., "endTime": ...,
    #   "path": [[time, lat, lon, baro_alt, true_track, on_ground], ...] }
    raw_path = data.get("path", [])
    track = []
    for point in raw_path:
        if len(point) < 4:
            continue
        ts, lat, lon, alt = point[0], point[1], point[2], point[3]
        if lat is None or lon is None:
            continue
        track.append({
            "ts":      int(ts),
            "lat":     round(float(lat), 6),
            "lon":     round(float(lon), 6),
            "alt_m":   round(float(alt), 1) if alt is not None else None,
            "heading": round(float(point[4]), 1) if len(point) > 4 and point[4] is not None else None,
        })
    log.info(f"  Fetched {len(track)} track points from OpenSky")
    return track


def process_aircraft_adsb(
    aircraft_id: str,
    cfg: dict,
    session: requests.Session,
    dry_run: bool = False,
):
    ac_cfg = cfg["aircraft"].get(aircraft_id, {})
    icao24 = ac_cfg.get("icao24", "").strip()

    if not icao24 or icao24.upper() == "TODO_LOOKUP":
        log.warning(
            f"{aircraft_id}: ICAO24 not configured. "
            f"Look it up at https://www.airframes.io or https://www.planespotters.net "
            f"and add it to config.json"
        )
        return

    if not ac_cfg.get("adsb_enabled", True):
        log.info(f"{aircraft_id}: ADS-B disabled in config")
        return

    data_dir = REPO_ROOT / "docs" / "data" / aircraft_id
    if not data_dir.exists():
        log.info(f"{aircraft_id}: No processed data directory yet, skipping")
        return

    manifest_path = data_dir / "manifest.json"
    if not manifest_path.exists():
        log.info(f"{aircraft_id}: No manifest found, run process_logs.py first")
        return

    manifest = json.loads(manifest_path.read_text())
    flights  = manifest.get("flights", [])

    log.info(f"\n{'='*60}")
    log.info(f"Fetching ADS-B for {aircraft_id} ({len(flights)} flights, ICAO24={icao24})")
    log.info(f"{'='*60}")

    updated = False
    for flight in flights:
        flight_id = flight["id"]
        adsb_path = data_dir / f"{flight_id}_adsb.json"

        if adsb_path.exists():
            log.info(f"  {flight_id}: ADS-B already cached, skipping")
            continue

        # Load flight JSON to get time window
        flight_json_path = data_dir / flight["file"]
        if not flight_json_path.exists():
            continue

        flight_json = json.loads(flight_json_path.read_text())
        start_ts, end_ts = flight_time_window(flight_json)

        if start_ts == 0:
            log.warning(f"  {flight_id}: Could not determine time window")
            continue

        log.info(
            f"  {flight_id}: fetching track "
            f"({datetime.utcfromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M')} UTC)"
        )

        if dry_run:
            log.info("  [dry-run] would fetch track here")
            continue

        track = fetch_opensky_track(icao24, start_ts, session)

        if track:
            adsb_payload = {
                "icao24":       icao24,
                "registration": ac_cfg.get("registration", aircraft_id),
                "flight_id":    flight_id,
                "source":       "opensky",
                "start_ts":     start_ts,
                "end_ts":       end_ts,
                "track":        track,
            }
            adsb_path.write_text(
                json.dumps(adsb_payload, separators=(",", ":")),
                encoding="utf-8",
            )
            log.info(f"  Written: {adsb_path.name}")

            # Update the flight JSON to mark has_adsb = True
            flight_json["meta"]["has_adsb"] = True
            flight_json_path.write_text(
                json.dumps(flight_json, separators=(",", ":")),
                encoding="utf-8",
            )
            # Update manifest entry
            flight["has_adsb"] = True
            updated = True

        time.sleep(RATE_LIMIT_DELAY)

    if updated and not dry_run:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.info(f"Manifest updated for {aircraft_id}")


def main():
    parser = argparse.ArgumentParser(description="Fetch ADS-B track data")
    parser.add_argument("--config",   default="config.json")
    parser.add_argument("--dry-run",  action="store_true")
    parser.add_argument("--aircraft", help="Only process this aircraft")
    args = parser.parse_args()

    config_path = REPO_ROOT / args.config
    if not config_path.exists():
        log.error(f"Config not found: {config_path}")
        sys.exit(1)
    cfg = json.loads(config_path.read_text())

    # Build authenticated session if credentials are available
    session = requests.Session()
    osky_user = os.environ.get("OPENSKY_USER", "").strip()
    osky_pass = os.environ.get("OPENSKY_PASS", "").strip()
    if osky_user and osky_pass:
        session.auth = (osky_user, osky_pass)
        log.info("Using authenticated OpenSky session")
    else:
        log.info("Using anonymous OpenSky session (historical data limited)")

    session.headers["User-Agent"] = "engine-monitor-data-viz/1.0 (github-actions)"

    aircraft_list = list(cfg["aircraft"].keys())
    if args.aircraft:
        if args.aircraft not in cfg["aircraft"]:
            log.error(f"Unknown aircraft '{args.aircraft}'")
            sys.exit(1)
        aircraft_list = [args.aircraft]

    for aircraft_id in aircraft_list:
        process_aircraft_adsb(aircraft_id, cfg, session, dry_run=args.dry_run)

    log.info("\nADS-B fetch complete.")


if __name__ == "__main__":
    main()
