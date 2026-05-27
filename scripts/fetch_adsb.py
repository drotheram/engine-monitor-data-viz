#!/usr/bin/env python3
"""
fetch_adsb.py — Fetch ADS-B track data for engine monitor flights

Primary source: FlightAware AeroAPI v4 (queries by registration, no ICAO24 needed)
Fallback:       OpenSky Network (requires ICAO24 in config + credentials for history)

For each processed flight JSON in docs/data/{AIRCRAFT}/, checks whether a
corresponding _adsb.json already exists. If not, attempts to fetch the
track from the configured provider.

Environment variables (set as GitHub Secrets):
  FLIGHTAWARE_API_KEY   — FlightAware AeroAPI key (free tier at flightaware.com/aeroapi)
  OPENSKY_USER          — OpenSky username (fallback, optional)
  OPENSKY_PASS          — OpenSky password (fallback, optional)

ADS-B JSON format written:
  {
    "registration": "C-GJYY",
    "flight_id": "Flt0916_20260427",
    "fa_flight_id": "CGJYY-1234567-...",
    "source": "flightaware",
    "start_ts": 1777319274,
    "end_ts":   1777330074,
    "track": [
      { "ts": 1777319274, "lat": 45.123, "lon": -75.456,
        "alt_m": 1234, "heading": 270, "gs_kts": 120 },
      ...
    ]
  }

Costs (FlightAware free tier ~$5-10/month):
  - 1 history search  = $0.05
  - 1 track fetch     = $0.01
  - Per flight total  ≈ $0.06  →  80+ flights/month within free tier
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent

FLIGHTAWARE_BASE  = "https://aeroapi.flightaware.com/aeroapi"
OPENSKY_BASE      = "https://opensky-network.org/api"
REQUEST_TIMEOUT   = 30
RATE_LIMIT_DELAY  = 2   # seconds between API calls


# ===========================================================================
# Time helpers
# ===========================================================================

def flight_time_window(flight_json: dict) -> tuple[int, int]:
    """
    Return (start_unix, end_unix) for the flight's Zulu time window.
    Adds a ±15 minute margin to account for taxiing before/after the log.
    """
    meta     = flight_json.get("meta", {})
    date_str = meta.get("date", "")           # "2026-04-27"
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

    # Add margin: start 15 min early, end 15 min late
    start_ts = int((start_dt - timedelta(minutes=15)).timestamp())
    end_ts   = int((start_dt + timedelta(minutes=duration) + timedelta(minutes=15)).timestamp())
    return start_ts, end_ts


def ts_to_iso(ts: int) -> str:
    return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


# ===========================================================================
# FlightAware AeroAPI v4
# ===========================================================================

def fa_get(path: str, session: requests.Session, params: dict = None) -> dict | None:
    """Make a GET request to the FlightAware AeroAPI."""
    url = f"{FLIGHTAWARE_BASE}{path}"
    try:
        resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 401:
            log.error("FlightAware: Invalid API key (401). Check your FLIGHTAWARE_API_KEY secret.")
            return None
        if resp.status_code == 403:
            log.error("FlightAware: Access denied (403). Your plan may not include this endpoint.")
            return None
        if resp.status_code == 404:
            return {}   # Not found — not an error, just no data
        if resp.status_code == 429:
            log.warning("FlightAware: Rate limited (429). Waiting 60s.")
            time.sleep(60)
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.warning(f"FlightAware request failed ({url}): {e}")
        return None


def find_fa_flight(
    registration: str,
    start_ts: int,
    end_ts: int,
    session: requests.Session,
    tolerance_min: int = 30,
) -> str | None:
    """
    Search FlightAware for a flight by registration within a time window.
    Returns the fa_flight_id of the best matching flight, or None.
    """
    # FlightAware uses registration without hyphen for Canadian aircraft
    ident = registration.replace("-", "")

    log.info(f"  Searching FlightAware for {registration} between "
             f"{ts_to_iso(start_ts)} and {ts_to_iso(end_ts)}")

    data = fa_get(f"/flights/{ident}", session, params={
        "start": ts_to_iso(start_ts - 3600),   # look 1h before log start
        "end":   ts_to_iso(end_ts   + 3600),   # look 1h after log end
        "max_pages": 1,
    })

    if data is None:
        return None
    if not data:
        log.info(f"  No FlightAware flights found for {registration}")
        return None

    flights = data.get("flights", [])
    if not flights:
        log.info(f"  No flights in response for {registration}")
        return None

    log.info(f"  Found {len(flights)} candidate flight(s)")

    # Pick the flight whose departure time is closest to our log start
    tolerance_s = tolerance_min * 60
    best = None
    best_delta = float("inf")

    for flt in flights:
        # Try both filed and actual departure times
        for key in ("actual_off", "actual_out", "filed_off", "filed_out", "scheduled_off"):
            dep_str = flt.get(key)
            if not dep_str:
                continue
            try:
                dep_ts = int(datetime.fromisoformat(
                    dep_str.replace("Z", "+00:00")
                ).timestamp())
                delta = abs(dep_ts - start_ts)
                if delta < best_delta and delta < tolerance_s:
                    best_delta = delta
                    best = flt.get("fa_flight_id")
                    break
            except (ValueError, TypeError):
                continue

    if best:
        log.info(f"  Matched fa_flight_id={best} (Δ{best_delta//60:.0f} min from log start)")
    else:
        log.info(f"  No flight within ±{tolerance_min} min tolerance")
    return best


def fetch_fa_track(fa_flight_id: str, session: requests.Session) -> list[dict]:
    """
    Fetch the track positions for a specific FlightAware flight.
    Returns a list of track-point dicts.
    """
    data = fa_get(f"/flights/{fa_flight_id}/track", session)
    if not data:
        return []

    raw_positions = data.get("positions", [])
    track = []
    for p in raw_positions:
        ts = p.get("timestamp")
        if isinstance(ts, str):
            try:
                ts = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
            except (ValueError, TypeError):
                continue
        lat = p.get("latitude")
        lon = p.get("longitude")
        if lat is None or lon is None:
            continue
        alt_ft = p.get("altitude")   # FlightAware returns feet
        alt_m  = round(alt_ft * 0.3048, 1) if alt_ft is not None else None
        track.append({
            "ts":      int(ts),
            "lat":     round(float(lat), 6),
            "lon":     round(float(lon), 6),
            "alt_m":   alt_m,
            "heading": p.get("heading"),
            "gs_kts":  p.get("groundspeed"),
        })

    log.info(f"  Fetched {len(track)} track points from FlightAware")
    return track


# ===========================================================================
# OpenSky Network (fallback)
# ===========================================================================

def fetch_opensky_track(
    icao24: str,
    start_ts: int,
    session: requests.Session,
) -> list[dict]:
    """Fetch flight track from OpenSky Network (requires ICAO24 + credentials for history)."""
    url    = f"{OPENSKY_BASE}/tracks/all"
    params = {"icao24": icao24.lower(), "time": start_ts}

    try:
        resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            log.info(f"  No track on OpenSky for ICAO24={icao24}")
            return []
        if resp.status_code in (401, 403):
            log.warning("  OpenSky: credentials required for historical data")
            return []
        if resp.status_code == 429:
            log.warning("  OpenSky rate limited, backing off 60s")
            time.sleep(60)
            return []
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        log.warning(f"  OpenSky request failed: {e}")
        return []

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


# ===========================================================================
# Per-aircraft processing
# ===========================================================================

def process_aircraft_adsb(
    aircraft_id: str,
    cfg: dict,
    fa_session: requests.Session | None,
    os_session: requests.Session | None,
    dry_run: bool = False,
):
    ac_cfg     = cfg["aircraft"].get(aircraft_id, {})
    adsb_cfg   = cfg.get("adsb", {})
    registration = ac_cfg.get("registration", aircraft_id)
    tolerance    = adsb_cfg.get("time_match_tolerance_minutes", 30)

    if not ac_cfg.get("adsb_enabled", True):
        log.info(f"{aircraft_id}: ADS-B disabled in config")
        return

    data_dir = REPO_ROOT / "docs" / "data" / aircraft_id
    if not data_dir.exists():
        log.info(f"{aircraft_id}: No processed data directory, skipping")
        return

    manifest_path = data_dir / "manifest.json"
    if not manifest_path.exists():
        log.info(f"{aircraft_id}: No manifest found — run process_logs.py first")
        return

    manifest = json.loads(manifest_path.read_text())
    flights  = manifest.get("flights", [])

    log.info(f"\n{'='*60}")
    log.info(f"ADS-B fetch for {aircraft_id} ({registration}) — {len(flights)} flights")
    log.info(f"{'='*60}")

    updated = False

    for flight in flights:
        flight_id  = flight["id"]
        adsb_path  = data_dir / f"{flight_id}_adsb.json"

        if adsb_path.exists():
            log.info(f"  {flight_id}: cached, skipping")
            continue

        flight_json_path = data_dir / flight["file"]
        if not flight_json_path.exists():
            continue

        flight_json      = json.loads(flight_json_path.read_text())
        start_ts, end_ts = flight_time_window(flight_json)
        if start_ts == 0:
            log.warning(f"  {flight_id}: cannot determine time window, skipping")
            continue

        log.info(f"\n  ── {flight_id}  ({ts_to_iso(start_ts)} → {ts_to_iso(end_ts)}) ──")

        track     = []
        source    = "none"
        fa_flt_id = None

        # ── Try FlightAware first ──────────────────────────────────────────
        if fa_session and not dry_run:
            fa_flt_id = find_fa_flight(
                registration, start_ts, end_ts, fa_session, tolerance_min=tolerance
            )
            time.sleep(RATE_LIMIT_DELAY)   # polite pause between search + track calls

            if fa_flt_id:
                track  = fetch_fa_track(fa_flt_id, fa_session)
                source = "flightaware"
                time.sleep(RATE_LIMIT_DELAY)

        # ── Fallback: OpenSky ──────────────────────────────────────────────
        if not track and os_session and not dry_run:
            # OpenSky needs ICAO24 — skip if not configured
            icao24 = ac_cfg.get("icao24", "").strip()
            if icao24 and icao24.upper() != "TODO_LOOKUP":
                log.info("  Trying OpenSky fallback…")
                track  = fetch_opensky_track(icao24, start_ts, os_session)
                source = "opensky"
                time.sleep(RATE_LIMIT_DELAY)
            else:
                log.info("  OpenSky fallback skipped (no ICAO24 configured)")

        if dry_run:
            log.info(f"  [dry-run] would fetch track here")
            continue

        if not track:
            log.info(f"  No track data found, skipping")
            continue

        # Write _adsb.json
        adsb_payload = {
            "registration": registration,
            "flight_id":    flight_id,
            "fa_flight_id": fa_flt_id,
            "source":       source,
            "start_ts":     start_ts,
            "end_ts":       end_ts,
            "track":        track,
        }
        adsb_path.write_text(
            json.dumps(adsb_payload, separators=(",", ":")),
            encoding="utf-8",
        )
        log.info(f"  ✓ Written: {adsb_path.name}  ({len(track)} points)")

        # Update flight JSON + manifest
        flight_json["meta"]["has_adsb"] = True
        flight_json_path.write_text(
            json.dumps(flight_json, separators=(",", ":")), encoding="utf-8"
        )
        flight["has_adsb"] = True
        updated = True

    if updated and not dry_run:
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.info(f"\nManifest updated for {aircraft_id}")
    else:
        log.info(f"\nNo new ADS-B data fetched for {aircraft_id}")


# ===========================================================================
# Entry point
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(description="Fetch ADS-B track data for processed flights")
    parser.add_argument("--config",   default="config.json")
    parser.add_argument("--dry-run",  action="store_true", help="Show what would be fetched, don't write files")
    parser.add_argument("--aircraft", help="Only process this aircraft (e.g. CGJYY)")
    parser.add_argument("--refetch",  action="store_true", help="Re-fetch even if _adsb.json already exists")
    args = parser.parse_args()

    config_path = REPO_ROOT / args.config
    if not config_path.exists():
        log.error(f"Config not found: {config_path}")
        sys.exit(1)
    cfg = json.loads(config_path.read_text())

    # ── Build FlightAware session ──────────────────────────────────────────
    fa_key     = os.environ.get("FLIGHTAWARE_API_KEY", "").strip()
    fa_session = None
    if fa_key:
        fa_session = requests.Session()
        fa_session.headers.update({
            "x-apikey":   fa_key,
            "User-Agent": "engine-monitor-data-viz/1.0 (github-actions)",
        })
        log.info("FlightAware AeroAPI session ready")
    else:
        log.warning("FLIGHTAWARE_API_KEY not set — FlightAware fetch disabled")
        log.warning("Get a free key at: https://www.flightaware.com/aeroapi/signup/personal")

    # ── Build OpenSky session (fallback) ───────────────────────────────────
    os_user    = os.environ.get("OPENSKY_USER", "").strip()
    os_pass    = os.environ.get("OPENSKY_PASS", "").strip()
    os_session = None
    if os_user and os_pass:
        os_session = requests.Session()
        os_session.auth = (os_user, os_pass)
        os_session.headers["User-Agent"] = "engine-monitor-data-viz/1.0"
        log.info("OpenSky fallback session ready")
    else:
        log.info("OpenSky fallback disabled (no credentials)")

    if not fa_session and not os_session:
        log.error("No ADS-B providers configured. Set FLIGHTAWARE_API_KEY to enable.")
        sys.exit(0)   # exit 0 so CI doesn't fail — just no ADS-B data

    # ── If --refetch, delete existing _adsb.json files first ──────────────
    if args.refetch:
        for ac_id in cfg["aircraft"]:
            if args.aircraft and ac_id != args.aircraft:
                continue
            for f in (REPO_ROOT / "docs" / "data" / ac_id).glob("*_adsb.json"):
                f.unlink()
                log.info(f"Deleted {f.name} for re-fetch")

    aircraft_list = list(cfg["aircraft"].keys())
    if args.aircraft:
        if args.aircraft not in cfg["aircraft"]:
            log.error(f"Unknown aircraft '{args.aircraft}'")
            sys.exit(1)
        aircraft_list = [args.aircraft]

    for aircraft_id in aircraft_list:
        process_aircraft_adsb(
            aircraft_id, cfg,
            fa_session=fa_session,
            os_session=os_session,
            dry_run=args.dry_run,
        )

    log.info("\nADS-B fetch complete.")


if __name__ == "__main__":
    main()
