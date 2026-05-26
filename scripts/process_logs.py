#!/usr/bin/env python3
"""
process_logs.py - CGR-30P Engine Monitor Log Processor

Reads raw CSV files from raw_data/{AIRCRAFT}/ and writes processed JSON
files to docs/data/{AIRCRAFT}/.

Processing pipeline:
  1. Parse the CGR-30P proprietary CSV format (metadata header + data rows)
  2. Detect and merge continuation files (no-header splits of the same flight)
  3. Filter out non-flight logs (max RPM < threshold OR duration < minimum)
  4. Crop leading/trailing idle data
  5. Downsample to ~5-second intervals
  6. Write per-flight JSON + manifest index

Usage:
  python scripts/process_logs.py [--config config.json] [--force]
"""

import argparse
import io
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# CGR-30P column → clean name mapping
# ---------------------------------------------------------------------------
COLUMN_MAP = {
    "TIME": "time",
    "RPMLEFT;RPM": "rpm_left",
    "RPMRIGHT;RPM": "rpm_right",
    "RPM;***": "rpm",
    "OAT;*C": "oat",
    "VOLTS;V": "volts",
    "AMPS;A": "amps",
    "FLOW;GPH": "flow",
    "EGT1;*F": "egt1",
    "EGT2;*F": "egt2",
    "EGT3;*F": "egt3",
    "EGT4;*F": "egt4",
    "EGT:;***": "egt_max",
    "CHT1;*F": "cht1",
    "CHT2;*F": "cht2",
    "CHT3;*F": "cht3",
    "CHT4;*F": "cht4",
    "CHT:;***": "cht_max",
    "FUEL L;GAL": "fuel_l",
    "FUEL R;GAL": "fuel_r",
    "OIL P;PSI": "oil_p",
    "OIL T;*F": "oil_t",
    "CARB T;*C": "carb_t",
    "FLT;HRS": "flt_hrs",
    "TACH;HRS": "tach_hrs",
}

# Numeric columns to export to JSON (everything except 'time')
NUMERIC_COLS = [
    "rpm_left", "rpm_right", "rpm",
    "egt1", "egt2", "egt3", "egt4", "egt_max",
    "cht1", "cht2", "cht3", "cht4", "cht_max",
    "flow", "volts", "amps",
    "fuel_l", "fuel_r",
    "oil_p", "oil_t", "oat", "carb_t",
]

META_RE = {
    "aircraft_id":    re.compile(r"Aircraft ID[.\s]*:\s*(.+)"),
    "unit_id":        re.compile(r"Unit ID[.\s]*:\s*(.+)"),
    "local_time":     re.compile(r"Local Time:\s*(\S+ \S+)"),
    "zulu_time":      re.compile(r"Zulu Time\.:\s*(\S+ \S+)"),
    "flight_number":  re.compile(r"Flight Number:\s*(\d+)"),
    "engine_hours":   re.compile(r"Engine Hours[.\s]*:\s*([\d.]+)"),
    "tach_time":      re.compile(r"Tach Time[.\s]*:\s*([\d.]+)"),
}


# ===========================================================================
# Parsing
# ===========================================================================

def read_raw_file(path: Path) -> tuple[dict, Optional[str], list[str]]:
    """
    Read a CGR-30P CSV file.

    Returns:
        metadata   – dict of header fields (empty for headerless files)
        col_header – the "TIME,SEL TANK QTY,..." line (or None)
        data_lines – raw CSV data lines (strings)
    """
    text = path.read_bytes().decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    metadata: dict = {}
    col_header: Optional[str] = None
    data_start = 0

    has_meta_header = lines[0].strip().startswith("Electronics International")

    if has_meta_header:
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            # Parse metadata key: value pairs
            for key, pattern in META_RE.items():
                m = pattern.match(line_stripped)
                if m:
                    metadata[key] = m.group(1).strip()
            # The column header line starts with TIME,
            if line_stripped.startswith("TIME,"):
                col_header = line_stripped
                data_start = i + 1
                break
    else:
        # Headerless continuation file – look for column header or first data row
        for i, line in enumerate(lines):
            line_stripped = line.strip()
            if line_stripped.startswith("TIME,"):
                col_header = line_stripped
                data_start = i + 1
                break
            elif re.match(r"^\d{2}:\d{2}:\d{2}", line_stripped):
                data_start = i
                break

    data_lines = [
        l.strip() for l in lines[data_start:]
        if re.match(r"^\d{2}:\d{2}:\d{2}", l.strip())
    ]

    return metadata, col_header, data_lines


def parse_to_dataframe(
    col_header: str,
    data_lines: list[str],
    flight_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    Parse raw CSV lines into a tidy DataFrame.

    Args:
        col_header  – the column-name row from the file
        data_lines  – the actual data rows (strings)
        flight_date – "YYYY/MM/DD" used to build a full datetime index

    Returns a DataFrame with clean column names and a 'datetime' column.
    """
    if not col_header or not data_lines:
        return pd.DataFrame()

    csv_text = col_header + "\n" + "\n".join(data_lines)
    df = pd.read_csv(
        io.StringIO(csv_text),
        low_memory=False,
        on_bad_lines="skip",
    )
    df.columns = [c.strip() for c in df.columns]

    # Rename known columns
    df.rename(columns={k: v for k, v in COLUMN_MAP.items() if k in df.columns}, inplace=True)

    # Build datetime index
    if "time" in df.columns and flight_date:
        def _parse_ts(t):
            try:
                return datetime.strptime(f"{flight_date} {str(t).strip()}", "%Y/%m/%d %H:%M:%S")
            except Exception:
                return pd.NaT
        df["datetime"] = df["time"].apply(_parse_ts)
    else:
        df["datetime"] = pd.NaT

    # Coerce numeric columns
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


# ===========================================================================
# Processing steps
# ===========================================================================

def filter_non_flight(df: pd.DataFrame, meta: dict, cfg: dict) -> bool:
    """
    Return True if this log should be DISCARDED (not a real flight).

    Criteria (any one sufficient to discard):
    - Max RPM < min_rpm_threshold
    - Duration < min_duration_minutes
    """
    if df.empty:
        return True

    rpm_col = "rpm" if "rpm" in df.columns else None
    max_rpm = df[rpm_col].max() if rpm_col else 0

    if "datetime" in df.columns and df["datetime"].notna().sum() > 1:
        valid_dt = df["datetime"].dropna()
        duration_min = (valid_dt.iloc[-1] - valid_dt.iloc[0]).total_seconds() / 60
    else:
        duration_min = len(df) * 0.3 / 60  # rough estimate

    min_rpm   = cfg["min_rpm_threshold"]
    min_dur   = cfg["min_duration_minutes"]

    if max_rpm < min_rpm:
        log.info(f"  → DISCARD: max RPM {max_rpm:.0f} < {min_rpm}")
        return True
    if duration_min < min_dur:
        log.info(f"  → DISCARD: duration {duration_min:.1f} min < {min_dur} min")
        return True

    return False


def crop_idle_tails(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """
    Remove leading and trailing data where the engine is not running.

    Strategy:
    - Leading: find the first index where RPM > tail_rpm_cutoff, keep 30 s before.
    - Trailing: find the last index where RPM > tail_rpm_cutoff, keep 60 s after.
      Then verify the engine stays off (RPM < cutoff) for >tail_idle_seconds beyond
      that point to confirm it's a real shutdown and not a momentary dip.
    """
    if df.empty or "rpm" not in df.columns:
        return df

    cutoff = cfg.get("tail_rpm_cutoff", 200)
    idle_secs = cfg.get("tail_idle_seconds", 300)

    running = df["rpm"] > cutoff

    if running.sum() == 0:
        return df  # no engine-running data at all

    first_run = running.idxmax()
    last_run  = running[::-1].idxmax()

    # Leading crop: start 30 s before first engine-running sample
    if "datetime" in df.columns and pd.notna(df.loc[first_run, "datetime"]):
        start_time = df.loc[first_run, "datetime"] - timedelta(seconds=30)
        start_mask = (df["datetime"] >= start_time) | df["datetime"].isna()
        start_iloc = df.index.get_loc(first_run)
        # Find the iloc closest to start_time
        candidate = df[df["datetime"] >= start_time].index
        start_idx = candidate[0] if len(candidate) > 0 else first_run
    else:
        start_idx = first_run

    # Trailing crop
    if "datetime" in df.columns and pd.notna(df.loc[last_run, "datetime"]):
        # Allow 60 s of idle rundown after last high-RPM point
        end_time = df.loc[last_run, "datetime"] + timedelta(seconds=60)
        # But also check: if engine stays below cutoff for >idle_secs after last_run,
        # trim there instead of at the file end
        after_last = df.loc[last_run:]
        below_cutoff = after_last["rpm"] <= cutoff
        if below_cutoff.sum() > 0:
            # Find start of the final idle stretch
            for i in range(len(after_last)):
                segment = after_last.iloc[i:]
                if (segment["rpm"] <= cutoff).all():
                    seg_start_dt = segment["datetime"].dropna()
                    seg_end_dt   = after_last["datetime"].dropna()
                    if len(seg_start_dt) > 0 and len(seg_end_dt) > 0:
                        idle_duration = (seg_end_dt.iloc[-1] - seg_start_dt.iloc[0]).total_seconds()
                        if idle_duration > idle_secs:
                            end_time = seg_start_dt.iloc[0] + timedelta(seconds=60)
                            break

        candidate_end = df[df["datetime"] <= end_time].index
        end_idx = candidate_end[-1] if len(candidate_end) > 0 else last_run
    else:
        end_idx = last_run

    # Slice the DataFrame
    start_iloc = df.index.get_loc(start_idx)
    end_iloc   = df.index.get_loc(end_idx)
    return df.iloc[start_iloc : end_iloc + 1].copy()


def downsample(df: pd.DataFrame, interval_seconds: int = 5) -> pd.DataFrame:
    """
    Resample to a regular interval by:
    1. Rounding timestamps to whole seconds and taking the mean of duplicates
    2. Resampling to every `interval_seconds` seconds
    """
    if df.empty or "datetime" not in df.columns:
        return df

    df = df.copy()
    df = df[df["datetime"].notna()].copy()
    if df.empty:
        return df

    df = df.set_index("datetime")

    # Mean over rows with identical timestamps
    df = df.groupby(df.index).mean(numeric_only=True)

    # Resample to regular grid
    rule = f"{interval_seconds}s"
    df = df.resample(rule).mean()
    df = df.dropna(how="all")

    df = df.reset_index()
    df.rename(columns={"datetime": "datetime"}, inplace=True)

    return df


# ===========================================================================
# Continuation-file detection and merging
# ===========================================================================

def detect_and_merge_continuations(
    file_records: list[dict],
    gap_seconds: int = 30,
) -> list[list[dict]]:
    """
    Group file records into flight groups.  A file belongs to the same group
    as the previous file if:
      - It has no metadata header (headerless continuation), OR
      - Its first timestamp follows the previous file's last timestamp by
        less than `gap_seconds`.

    Returns a list of groups, where each group is a list of file_records
    to be concatenated into one flight.
    """
    if not file_records:
        return []

    groups: list[list[dict]] = []
    current_group: list[dict] = [file_records[0]]

    for rec in file_records[1:]:
        prev = current_group[-1]
        prev_last = prev.get("last_ts")
        curr_first = rec.get("first_ts")
        is_headerless = not rec.get("has_header", True)

        # Merge if headerless or timestamps are nearly adjacent
        if is_headerless and curr_first and prev_last:
            gap = (curr_first - prev_last).total_seconds()
            if gap < gap_seconds:
                log.info(
                    f"  Merging continuation: {rec['path'].name} "
                    f"(gap {gap:.1f}s)"
                )
                current_group.append(rec)
                continue

        # Otherwise start a new group
        groups.append(current_group)
        current_group = [rec]

    groups.append(current_group)
    return groups


# ===========================================================================
# JSON export
# ===========================================================================

def df_to_json_payload(df: pd.DataFrame, meta: dict, has_adsb: bool = False) -> dict:
    """Convert a processed DataFrame to the JSON structure loaded by the UI."""
    if df.empty:
        return {}

    time_col = []
    ts_col   = []

    dt_col = df.get("datetime") if "datetime" in df.columns else None
    if dt_col is not None and dt_col.notna().sum() > 0:
        t0 = dt_col.dropna().iloc[0]
        for dt in dt_col:
            if pd.notna(dt):
                time_col.append(dt.strftime("%H:%M:%S"))
                ts_col.append(int((dt - t0).total_seconds()))
            else:
                time_col.append(None)
                ts_col.append(None)
    elif "time" in df.columns:
        time_col = list(df["time"].astype(str))
        ts_col   = list(range(0, len(df) * 5, 5))

    # Duration
    dur_min = 0.0
    if ts_col and ts_col[-1] is not None:
        dur_min = ts_col[-1] / 60.0

    max_rpm = float(df["rpm"].max()) if "rpm" in df.columns else 0

    def _series(col: str) -> list:
        if col in df.columns:
            vals = df[col].round(2).tolist()
            return [None if (v is None or (isinstance(v, float) and np.isnan(v))) else v for v in vals]
        return []

    payload = {
        "meta": {
            "aircraft":           meta.get("aircraft_id", ""),
            "flight_number":      int(meta.get("flight_number", 0)),
            "date":               meta.get("date", ""),
            "local_time_start":   meta.get("local_time", "").split(" ")[-1] if " " in meta.get("local_time","") else "",
            "zulu_time_start":    meta.get("zulu_time", "").split(" ")[-1] if " " in meta.get("zulu_time","") else "",
            "zulu_offset_hours":  meta.get("zulu_offset_hours", 0),
            "duration_minutes":   round(dur_min, 1),
            "max_rpm":            int(max_rpm),
            "engine_hours_start": float(meta.get("engine_hours", 0) or 0),
            "tach_hours_start":   float(meta.get("tach_time", 0) or 0),
            "data_points":        len(time_col),
            "has_adsb":           has_adsb,
            "source_files":       meta.get("source_files", []),
        },
        "time": time_col,
        "ts":   ts_col,
        # Engine
        "rpm":       _series("rpm"),
        "rpm_left":  _series("rpm_left"),
        "rpm_right": _series("rpm_right"),
        "egt1": _series("egt1"),
        "egt2": _series("egt2"),
        "egt3": _series("egt3"),
        "egt4": _series("egt4"),
        "egt_max": _series("egt_max"),
        "cht1": _series("cht1"),
        "cht2": _series("cht2"),
        "cht3": _series("cht3"),
        "cht4": _series("cht4"),
        "cht_max": _series("cht_max"),
        "flow": _series("flow"),
        # Electrical
        "volts": _series("volts"),
        "amps":  _series("amps"),
        # Fuel & environment
        "fuel_l": _series("fuel_l"),
        "fuel_r": _series("fuel_r"),
        "oil_p":  _series("oil_p"),
        "oil_t":  _series("oil_t"),
        "oat":    _series("oat"),
        "carb_t": _series("carb_t"),
    }
    return payload


# ===========================================================================
# Main processing logic per aircraft
# ===========================================================================

def process_aircraft(aircraft_id: str, cfg: dict, force: bool = False):
    raw_dir  = REPO_ROOT / "raw_data" / aircraft_id
    out_dir  = REPO_ROOT / "docs" / "data" / aircraft_id
    out_dir.mkdir(parents=True, exist_ok=True)

    proc_cfg = cfg["processing"]
    gap_secs = proc_cfg.get("continuation_gap_seconds", 30)

    if not raw_dir.exists():
        log.warning(f"Raw data directory not found: {raw_dir}")
        return []

    csv_files = sorted(raw_dir.glob("*.csv"))
    if not csv_files:
        log.info(f"No CSV files found in {raw_dir}")
        _write_manifest([], aircraft_id, out_dir)
        return []

    log.info(f"\n{'='*60}")
    log.info(f"Processing {aircraft_id}: {len(csv_files)} raw files")
    log.info(f"{'='*60}")

    # -----------------------------------------------------------------------
    # Step 1: Read all files and collect metadata
    # -----------------------------------------------------------------------
    file_records = []
    for csv_path in csv_files:
        meta, col_header, data_lines = read_raw_file(csv_path)
        has_header = bool(meta)  # True if we parsed metadata successfully

        # Determine file date from metadata or filename
        flight_date = None
        if "local_time" in meta:
            flight_date = meta["local_time"].split(" ")[0]
        else:
            # Try to extract from filename: FltNNNN_YYYYMMDD*.csv
            m = re.search(r"_(\d{4})(\d{2})(\d{2})", csv_path.name)
            if m:
                flight_date = f"{m.group(1)}/{m.group(2)}/{m.group(3)}"

        meta["date"] = (
            datetime.strptime(flight_date, "%Y/%m/%d").strftime("%Y-%m-%d")
            if flight_date else ""
        )

        # Parse to DataFrame (lightweight parse to get timestamps)
        df = parse_to_dataframe(col_header, data_lines, flight_date)

        first_ts = last_ts = None
        if "datetime" in df.columns:
            valid = df["datetime"].dropna()
            if not valid.empty:
                first_ts = valid.iloc[0]
                last_ts  = valid.iloc[-1]

        file_records.append({
            "path":       csv_path,
            "meta":       meta,
            "col_header": col_header,
            "data_lines": data_lines,
            "has_header": has_header,
            "flight_date": flight_date,
            "first_ts":   first_ts,
            "last_ts":    last_ts,
            "df":         df,
        })
        log.debug(f"  Loaded {csv_path.name}: {len(data_lines)} rows")

    # -----------------------------------------------------------------------
    # Step 2: Detect and group continuation files
    # -----------------------------------------------------------------------
    groups = detect_and_merge_continuations(file_records, gap_seconds=gap_secs)
    log.info(f"Grouped into {len(groups)} flight candidate(s)")

    # -----------------------------------------------------------------------
    # Step 3: Process each group
    # -----------------------------------------------------------------------
    manifest_entries = []
    downsample_s = proc_cfg.get("downsample_seconds", 5)

    for group in groups:
        primary = group[0]
        source_names = [r["path"].name for r in group]

        log.info(f"\nProcessing: {', '.join(source_names)}")

        # Merge DataFrames if multiple files in group
        if len(group) == 1:
            df = primary["df"].copy()
            meta = primary["meta"].copy()
        else:
            dfs = []
            for rec in group:
                dfs.append(rec["df"])
            df = pd.concat(dfs, ignore_index=True)
            meta = primary["meta"].copy()

        meta["source_files"] = source_names

        # Compute zulu offset
        if "local_time" in meta and "zulu_time" in meta:
            try:
                lt = datetime.strptime(meta["local_time"], "%Y/%m/%d %H:%M:%S")
                zt = datetime.strptime(meta["zulu_time"],  "%Y/%m/%d %H:%M:%S")
                meta["zulu_offset_hours"] = int((lt - zt).total_seconds() / 3600)
            except Exception:
                meta["zulu_offset_hours"] = 0
        else:
            meta["zulu_offset_hours"] = 0

        # Step 3a: Filter
        if filter_non_flight(df, meta, proc_cfg):
            log.info(f"  Skipping (not a real flight)")
            continue

        # Step 3b: Crop idle tails
        before = len(df)
        df = crop_idle_tails(df, proc_cfg)
        after = len(df)
        log.info(f"  Cropped: {before} → {after} rows ({before - after} removed)")

        # Step 3c: Downsample
        df = downsample(df, interval_seconds=downsample_s)
        log.info(f"  Downsampled to {len(df)} rows @ {downsample_s}s intervals")

        # Step 3d: Build output filename (zero-padded to match source convention)
        flt_num = meta.get("flight_number", "")
        date_str = meta.get("date", "").replace("-", "")
        if flt_num and date_str:
            out_name = f"Flt{int(flt_num):04d}_{date_str}.json"
        else:
            out_name = primary["path"].stem + ".json"
        out_path = out_dir / out_name

        # Step 3e: Check if ADS-B data exists for this flight
        adsb_path  = out_dir / out_name.replace(".json", "_adsb.json")
        has_adsb = adsb_path.exists()

        # Step 3f: Write JSON
        payload = df_to_json_payload(df, meta, has_adsb=has_adsb)
        if not payload:
            log.warning(f"  Empty payload, skipping {out_name}")
            continue

        if not force and out_path.exists():
            # Only regenerate if source is newer
            src_mtime  = max(r["path"].stat().st_mtime for r in group)
            out_mtime  = out_path.stat().st_mtime
            if src_mtime <= out_mtime:
                log.info(f"  Up to date: {out_name}")
                # Still add to manifest
                pass

        out_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        log.info(f"  Written: {out_name}")

        # Manifest entry
        manifest_entries.append({
            "id":             out_name.replace(".json", ""),
            "file":           out_name,
            "flight_number":  int(meta.get("flight_number", 0)),
            "date":           meta.get("date", ""),
            "local_time_start": payload["meta"]["local_time_start"],
            "zulu_time_start":  payload["meta"]["zulu_time_start"],
            "duration_minutes": payload["meta"]["duration_minutes"],
            "max_rpm":          payload["meta"]["max_rpm"],
            "engine_hours":     payload["meta"]["engine_hours_start"],
            "data_points":      payload["meta"]["data_points"],
            "has_adsb":         has_adsb,
            "source_files":     source_names,
        })

    # Sort manifest newest first
    manifest_entries.sort(key=lambda x: (x["date"], x["flight_number"]), reverse=True)
    _write_manifest(manifest_entries, aircraft_id, out_dir)

    log.info(f"\n{aircraft_id}: {len(manifest_entries)} flights written")
    return manifest_entries


def _write_manifest(entries: list, aircraft_id: str, out_dir: Path):
    manifest = {
        "aircraft": aircraft_id,
        "generated_utc": datetime.utcnow().isoformat() + "Z",
        "flight_count": len(entries),
        "flights": entries,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    log.info(f"Manifest written: {manifest_path} ({len(entries)} flights)")


# ===========================================================================
# Entry point
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(description="Process CGR-30P engine logs")
    parser.add_argument("--config", default="config.json", help="Path to config.json")
    parser.add_argument("--force",  action="store_true",   help="Re-process all files")
    parser.add_argument("--aircraft", help="Only process this aircraft (e.g. CGJYY)")
    args = parser.parse_args()

    config_path = REPO_ROOT / args.config
    if not config_path.exists():
        log.error(f"Config not found: {config_path}")
        sys.exit(1)

    cfg = json.loads(config_path.read_text())

    aircraft_list = cfg["aircraft"].keys()
    if args.aircraft:
        if args.aircraft not in aircraft_list:
            log.error(f"Unknown aircraft '{args.aircraft}'. Options: {list(aircraft_list)}")
            sys.exit(1)
        aircraft_list = [args.aircraft]

    for aircraft_id in aircraft_list:
        process_aircraft(aircraft_id, cfg, force=args.force)

    log.info("\nAll done.")


if __name__ == "__main__":
    main()
