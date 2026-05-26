# ✈ Engine Monitor Dashboard

Interactive visualization of CGR-30P flight data for **C-GJYY** and **C-FHTI**.

**Live site:** `https://<your-github-username>.github.io/engine-monitor-data-viz/`

---

## Features

| Feature | Description |
|---|---|
| 🔥 **Engine panel** | EGT 1–4, CHT 1–4, RPM, Fuel Flow — linked time axis |
| ⚡ **Electrical panel** | Voltage and Amperage over flight time |
| ⛽ **Fuel panel** | Fuel tank levels (L/R), fuel flow, OAT, oil pressure/temp |
| 🗺 **ADS-B map** | Live track from OpenSky Network — aircraft position synced with chart hover |
| 📡 **Height profile** | Barometric altitude from ADS-B data, linked with engine charts |
| 🔗 **Linked charts** | Zoom/pan any chart and all panels follow; crosshair cursor updates map position |
| ✈ **Two aircraft** | Toggle between C-GJYY and C-FHTI via the nav bar |

---

## Repository Structure

```
engine-monitor-data-viz/
├── .github/
│   └── workflows/
│       └── process_and_deploy.yml   # CI/CD pipeline
├── raw_data/
│   ├── CGJYY/                       # Drop CGR-30P CSV files here
│   └── CFHTI/                       # Drop CGR-30P CSV files here
├── scripts/
│   ├── process_logs.py              # Data processing pipeline
│   └── fetch_adsb.py                # ADS-B data fetcher (OpenSky)
├── docs/                            # GitHub Pages root
│   ├── index.html                   # Aircraft selector landing page
│   ├── viewer.html                  # Main dashboard (loads via ?aircraft=)
│   ├── css/app.css
│   ├── js/
│   │   ├── app.js                   # App logic, routing, flight loading
│   │   ├── plots.js                 # Plotly chart definitions
│   │   └── map.js                   # Leaflet + ADS-B cursor
│   └── data/
│       ├── CGJYY/
│       │   ├── manifest.json        # Generated — flight index
│       │   ├── Flt0916_20260427.json
│       │   └── Flt0916_20260427_adsb.json  (if fetched)
│       └── CFHTI/
├── config.json                      # Aircraft config (ICAO24 etc.)
├── requirements.txt
└── README.md
```

---

## Workflow: Adding New Flights

```
1. Create a new git branch
   git checkout -b flights/may-2026

2. Copy raw CGR-30P CSV files into the correct folder
   cp /path/to/downloads/*.csv raw_data/CGJYY/

3. Push the branch and open a Pull Request
   git add raw_data/
   git commit -m "feat: add May 2026 flights"
   git push -u origin flights/may-2026
   # → open PR on GitHub

4. Merge the PR into main
   The GitHub Action triggers automatically and:
   a) Filters out ground-only logs (max RPM < 2000, duration < 20 min)
   b) Crops pre/post-flight idle data
   c) Detects and merges any split continuation files
   d) Downsamples to 5-second intervals and writes JSON to docs/data/
   e) Attempts to fetch ADS-B track data from OpenSky Network
   f) Deploys the updated docs/ to GitHub Pages
```

---

## First-Time Setup

### 1. Enable GitHub Pages

In your repository → **Settings → Pages**:
- **Source:** `GitHub Actions` (not a branch)

### 2. Configure ICAO24 Addresses (for ADS-B)

Look up the ICAO24 hex address for each aircraft at:
- https://www.airframes.io
- https://www.planespotters.net

Then edit `config.json`:

```json
{
  "aircraft": {
    "CGJYY": {
      "icao24": "c07d2e"   ← replace with actual address
    },
    "CFHTI": {
      "icao24": "c0xxxx"   ← replace with actual address
    }
  }
}
```

### 3. Add OpenSky Credentials (for historical ADS-B)

Register at https://opensky-network.org/ (free).

Add as repository secrets (**Settings → Secrets → Actions**):

| Secret | Value |
|--------|-------|
| `OPENSKY_USER` | Your OpenSky username |
| `OPENSKY_PASS` | Your OpenSky password |

> **Note:** Without credentials, OpenSky only returns data for the most recent flight. With a free account, you can query historical data up to 30 days old.

---

## Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Process all raw logs
python scripts/process_logs.py

# (Optional) Fetch ADS-B data
export OPENSKY_USER=your_username
export OPENSKY_PASS=your_password
python scripts/fetch_adsb.py

# Serve the dashboard
cd docs && python -m http.server 8080
# → open http://localhost:8080/viewer.html?aircraft=CGJYY
```

---

## Data Processing Details

### Filter (discard non-flight logs)

A log is discarded if **either** condition is met:
- Max RPM < 2,000 (engine never reached flight power)
- Duration < 20 minutes (short ground run or data corruption)

Configurable in `config.json → processing`.

### Tail Cropping

After landing, the CGR-30P often logs idle data for many minutes. The processor:
1. Finds the last row where RPM > 200
2. Keeps 60 seconds of rundown after that point
3. Removes everything beyond the first sustained idle period (>5 minutes of RPM < 200)

### Split-File Merging

If the logger creates a continuation file (no metadata header, timestamps immediately follow the previous file's last entry), the processor automatically concatenates the two files into a single flight record.

### Downsampling

Raw data is logged at ~3 Hz (0.3-second intervals). Data is resampled to **5-second** intervals before export, reducing file sizes by ~15× while preserving all meaningful trends.

---

## CGR-30P Column Reference

| JSON Field | CGR-30P Column | Unit |
|---|---|---|
| `rpm` | `RPM;***` | RPM |
| `egt1`–`egt4` | `EGT1;*F`–`EGT4;*F` | °F |
| `cht1`–`cht4` | `CHT1;*F`–`CHT4;*F` | °F |
| `flow` | `FLOW;GPH` | GPH |
| `volts` | `VOLTS;V` | V |
| `amps` | `AMPS;A` | A |
| `fuel_l` / `fuel_r` | `FUEL L;GAL` / `FUEL R;GAL` | gal |
| `oil_p` | `OIL P;PSI` | PSI |
| `oil_t` | `OIL T;*F` | °F |
| `oat` | `OAT;*C` | °C |
| `carb_t` | `CARB T;*C` | °C |

---

## Tech Stack

| Component | Technology |
|---|---|
| Data processing | Python 3.11, pandas, numpy |
| Charts | Plotly.js 2.x (WebGL-accelerated) |
| Map | Leaflet.js 1.9, OpenStreetMap tiles |
| ADS-B data | OpenSky Network REST API |
| Hosting | GitHub Pages (static) |
| CI/CD | GitHub Actions |
