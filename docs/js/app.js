/**
 * app.js — Engine Monitor Dashboard
 *
 * Handles:
 *  - Reading ?aircraft= URL param
 *  - Loading manifest.json and building the flight list sidebar
 *  - Fetching individual flight JSON and rendering charts
 *  - Linking charts + map cursor together
 */

(async () => {

  // -----------------------------------------------------------------------
  // Config
  // -----------------------------------------------------------------------
  const AIRCRAFT_META = {
    CGJYY: { label: "C-GJYY", registration: "C-GJYY" },
    CFHTI: { label: "C-FHTI", registration: "C-FHTI" },
  };
  const DATA_BASE = "data";
  const CHART_IDS = ["engine-chart", "electrical-chart", "fuel-chart"];

  // -----------------------------------------------------------------------
  // State
  // -----------------------------------------------------------------------
  let currentAircraft  = "CGJYY";
  let currentFlightId  = null;
  let allFlights       = [];
  let mapInitialised   = false;

  // -----------------------------------------------------------------------
  // URL param helpers
  // -----------------------------------------------------------------------
  function getParam(key) {
    return new URLSearchParams(window.location.search).get(key);
  }

  function setParam(key, value) {
    const url = new URL(window.location);
    url.searchParams.set(key, value);
    history.replaceState({}, "", url);
  }

  // -----------------------------------------------------------------------
  // Aircraft switcher
  // -----------------------------------------------------------------------
  function buildSwitcher() {
    const switcher = document.getElementById("aircraft-switcher");
    switcher.innerHTML = Object.entries(AIRCRAFT_META).map(([id, ac]) => `
      <a class="aircraft-btn ${id === currentAircraft ? "active" : ""}"
         href="viewer.html?aircraft=${id}">
        ${ac.label}
      </a>
    `).join("");
  }

  // -----------------------------------------------------------------------
  // Load manifest
  // -----------------------------------------------------------------------
  async function loadManifest(aircraftId) {
    const url = `${DATA_BASE}/${aircraftId}/manifest.json`;
    try {
      const resp = await fetch(url, { cache: "no-cache" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return await resp.json();
    } catch (e) {
      console.warn("Could not load manifest:", url, e);
      return null;
    }
  }

  // -----------------------------------------------------------------------
  // Flight list rendering
  // -----------------------------------------------------------------------
  function formatDuration(minutes) {
    if (!minutes) return "—";
    const h = Math.floor(minutes / 60);
    const m = Math.round(minutes % 60);
    return h > 0 ? `${h}h ${m}m` : `${m}m`;
  }

  function formatDate(dateStr) {
    if (!dateStr) return "—";
    try {
      const d = new Date(dateStr + "T12:00:00");
      return d.toLocaleDateString("en-CA", {
        year: "numeric", month: "short", day: "numeric",
      });
    } catch { return dateStr; }
  }

  function renderFlightList(flights) {
    const listEl = document.getElementById("flight-list");
    if (!flights || flights.length === 0) {
      listEl.innerHTML = `<div class="no-flights">No processed flights found.<br>
        Add CSV files to <code>raw_data/${currentAircraft}/</code> and push to main.</div>`;
      return;
    }

    listEl.innerHTML = flights.map(f => `
      <div class="flight-item ${f.id === currentFlightId ? "active" : ""}"
           data-id="${f.id}"
           data-file="${f.file}"
           data-has-adsb="${f.has_adsb}"
           tabindex="0"
           role="button"
           aria-label="Flight ${f.flight_number}, ${formatDate(f.date)}">
        <div class="flight-item-date">${formatDate(f.date)}</div>
        <div class="flight-item-number">Flight #${f.flight_number}</div>
        <div class="flight-item-stats">
          <span class="flight-stat">
            <span class="flight-stat-icon">⏱</span>
            ${formatDuration(f.duration_minutes)}
          </span>
          <span class="flight-stat">
            <span class="flight-stat-icon">🔄</span>
            ${f.max_rpm ? f.max_rpm.toLocaleString() + " RPM" : "—"}
          </span>
          ${f.engine_hours ? `<span class="flight-stat">
            <span class="flight-stat-icon">⏰</span>
            ${Number(f.engine_hours).toFixed(1)}h
          </span>` : ""}
        </div>
        ${f.has_adsb ? `<span class="adsb-badge">📡 ADS-B</span>` : ""}
      </div>
    `).join("");

    // Click + keyboard navigation
    listEl.querySelectorAll(".flight-item").forEach(el => {
      const load = () => loadFlight(el.dataset.id, el.dataset.file, el.dataset.hasAdsb === "true");
      el.addEventListener("click",   load);
      el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") load(); });
    });
  }

  // -----------------------------------------------------------------------
  // Search / filter
  // -----------------------------------------------------------------------
  function setupSearch() {
    const input = document.getElementById("search-input");
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      const items = document.querySelectorAll(".flight-item");
      items.forEach(el => {
        const text = el.textContent.toLowerCase();
        el.style.display = text.includes(q) ? "" : "none";
      });
    });
  }

  // -----------------------------------------------------------------------
  // Flight loading
  // -----------------------------------------------------------------------
  async function loadFlight(flightId, flightFile, hasAdsb) {
    if (flightId === currentFlightId) return;
    currentFlightId = flightId;

    // Update sidebar active state
    document.querySelectorAll(".flight-item").forEach(el => {
      el.classList.toggle("active", el.dataset.id === flightId);
    });

    setParam("flight", flightId);
    showLoading(true);

    try {
      // Load flight data
      const url = `${DATA_BASE}/${currentAircraft}/${flightFile}`;
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      // Load ADS-B if available
      let adsbData = null;
      if (hasAdsb) {
        try {
          const adsbUrl = `${DATA_BASE}/${currentAircraft}/${flightId}_adsb.json`;
          const ar = await fetch(adsbUrl);
          if (ar.ok) adsbData = await ar.json();
        } catch (e) {
          console.warn("ADS-B load failed:", e);
        }
      }

      renderFlight(data, adsbData);

    } catch (e) {
      console.error("Failed to load flight:", e);
      showError(`Could not load flight data: ${e.message}`);
    } finally {
      showLoading(false);
    }
  }

  // -----------------------------------------------------------------------
  // Compute the Unix timestamp (seconds, UTC) of ts=0 in the engine log.
  //
  // The engine log's ts array is seconds elapsed from its first data point
  // (after idle-tail cropping). data.time[0] is that first point's Zulu
  // HH:MM:SS, combined with meta.date to give an absolute reference.
  //
  // This is needed because adsbData.start_ts is the FlightAware *search*
  // window start (= file-header Zulu time − 15 min), which can be 15–30+
  // minutes earlier than ts=0, causing the altitude track and map cursor
  // to appear temporally offset from the engine log plots.
  // -----------------------------------------------------------------------
  function computeEpochStart(data) {
    try {
      const date = data.meta && data.meta.date;         // "YYYY-MM-DD" local date
      const times = data.time;
      const firstTime = times && times.find(t => t != null); // "HH:MM:SS" LOCAL time
      if (!date || !firstTime) return null;

      // data.time[] stores LOCAL time (CGR-30P records in local clock).
      // Treat it as UTC so Date can parse it, then subtract the zulu offset
      // to shift from local → UTC.
      //   zulu_offset_hours = local − UTC  (e.g. PDT = −7)
      //   UTC unix = (local parsed as UTC) − zulu_offset_hours × 3600
      const zuluOffset = data.meta.zulu_offset_hours || 0;
      const localAsUtc = Math.floor(
        new Date(`${date}T${firstTime}Z`).getTime() / 1000
      );
      const unix = localAsUtc - zuluOffset * 3600;
      return Number.isFinite(unix) ? unix : null;
    } catch { return null; }
  }

  // -----------------------------------------------------------------------
  // Render flight data
  // -----------------------------------------------------------------------
  function renderFlight(data, adsbData) {
    const meta = data.meta || {};

    // Show/hide sections
    document.getElementById("placeholder").style.display    = "none";
    document.getElementById("flight-header").style.display  = "";
    document.getElementById("charts-container").style.display = "";

    // Update page title
    document.title = `${meta.aircraft || currentAircraft} — Flight #${meta.flight_number} — ${meta.date}`;

    // Flight header
    document.getElementById("flight-title").textContent =
      `${meta.aircraft || currentAircraft}  ·  Flight #${meta.flight_number}  ·  ${formatDate(meta.date)}`;

    const flightMeta = document.getElementById("flight-meta");
    flightMeta.innerHTML = [
      meta.local_time_start
        ? `<span class="meta-pill">🕐 <strong>${meta.local_time_start}</strong> local (${meta.zulu_time_start} Z)</span>`
        : "",
      `<span class="meta-pill">⏱ <strong>${formatDuration(meta.duration_minutes)}</strong></span>`,
      meta.max_rpm
        ? `<span class="meta-pill">🔄 <strong>${meta.max_rpm.toLocaleString()}</strong> RPM</span>`
        : "",
      meta.engine_hours_start
        ? `<span class="meta-pill">⏰ Engine <strong>${meta.engine_hours_start.toFixed(1)}h</strong></span>`
        : "",
      `<span class="meta-pill">📊 <strong>${(meta.data_points || 0).toLocaleString()}</strong> samples</span>`,
    ].filter(Boolean).join("");

    // Purge old charts
    [...CHART_IDS, "altitude-chart"].forEach(id => PlotController.purge(id));

    // Render charts
    PlotController.renderEngine("engine-chart", data);
    PlotController.renderElectrical("electrical-chart", data);
    PlotController.renderFuel("fuel-chart", data);

    // Link charts for synced zoom + crosshair
    const linkedIds = [...CHART_IDS];

    // ADS-B map + altitude
    const mapRow = document.getElementById("map-row");
    if (adsbData && adsbData.track && adsbData.track.length > 0) {
      mapRow.style.display = "";

      // epochStart: Unix timestamp of engine-log ts=0 (first data point after
      // cropping). Used to align ADS-B absolute timestamps with the relative
      // engine-log timeline.  Falls back to adsbData.start_ts if unavailable.
      const epochStart = computeEpochStart(data) || adsbData.start_ts || 0;

      // Init map once
      if (!mapInitialised) {
        MapController.init("map-leaflet");
        mapInitialised = true;
      }
      MapController.loadTrack(adsbData, meta, epochStart);
      PlotController.renderAltitude("altitude-chart", adsbData, meta, epochStart);
      linkedIds.push("altitude-chart");
    } else {
      mapRow.style.display = "none";
      MapController.clear();
    }

    // Link all charts together; on hover update map cursor
    PlotController.linkCharts(linkedIds, (tsSeconds) => {
      if (adsbData) MapController.setCursor(tsSeconds);
    });

    // Update sidebar title
    document.getElementById("sidebar-title").textContent =
      `${meta.aircraft || currentAircraft} — Flights`;
    document.getElementById("status-badge").textContent =
      `${allFlights.length} flights`;
  }

  // -----------------------------------------------------------------------
  // Panel collapse
  // -----------------------------------------------------------------------
  window.togglePanel = function(panelId) {
    const panel = document.getElementById(panelId);
    panel.classList.toggle("collapsed");
    // Resize Plotly to fill available space after animation
    setTimeout(() => {
      const chartDiv = panel.querySelector(".chart-div");
      if (chartDiv) Plotly.Plots.resize(chartDiv);
    }, 300);
  };

  // -----------------------------------------------------------------------
  // Loading / error states
  // -----------------------------------------------------------------------
  function showLoading(on) {
    let overlay = document.getElementById("loading-overlay");
    if (on && !overlay) {
      overlay = document.createElement("div");
      overlay.id = "loading-overlay";
      overlay.className = "loading-overlay";
      overlay.innerHTML = `<div class="spinner"></div>`;
      document.getElementById("charts-container").style.position = "relative";
      document.getElementById("content").appendChild(overlay);
    } else if (!on && overlay) {
      overlay.remove();
    }
  }

  function showError(msg) {
    const content = document.getElementById("content");
    const existing = document.getElementById("error-toast");
    if (existing) existing.remove();
    const toast = document.createElement("div");
    toast.id = "error-toast";
    toast.style.cssText = `
      position: fixed; bottom: 20px; right: 20px; z-index: 9999;
      background: #3d1010; border: 1px solid #9c3a3a; border-radius: 6px;
      padding: 12px 18px; color: #ff8080; font-size: .875rem; max-width: 320px;
    `;
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 6000);
  }

  // -----------------------------------------------------------------------
  // Window resize — re-flow Plotly charts
  // -----------------------------------------------------------------------
  window.addEventListener("resize", () => {
    CHART_IDS.forEach(id => {
      const el = document.getElementById(id);
      if (el && el.data) Plotly.Plots.resize(el);
    });
  });

  // -----------------------------------------------------------------------
  // Boot
  // -----------------------------------------------------------------------
  async function boot() {
    // Determine aircraft from URL param
    const acParam = getParam("aircraft");
    if (acParam && AIRCRAFT_META[acParam]) {
      currentAircraft = acParam;
    } else {
      // Default to CGJYY; update URL
      setParam("aircraft", currentAircraft);
    }

    document.title = `${AIRCRAFT_META[currentAircraft].label} — Engine Monitor`;
    buildSwitcher();
    setupSearch();

    // Load manifest
    const manifest = await loadManifest(currentAircraft);
    if (!manifest) {
      document.getElementById("flight-list").innerHTML = `
        <div class="no-flights">
          No data found for ${currentAircraft}.<br><br>
          Run the processing workflow or add raw CSV files to
          <code>raw_data/${currentAircraft}/</code>.
        </div>`;
      document.getElementById("status-badge").textContent = "No data";
      return;
    }

    allFlights = manifest.flights || [];
    renderFlightList(allFlights);
    document.getElementById("status-badge").textContent =
      `${allFlights.length} flight${allFlights.length !== 1 ? "s" : ""}`;

    // Auto-load flight from URL param, or most recent
    const flightParam = getParam("flight");
    const target = flightParam
      ? allFlights.find(f => f.id === flightParam)
      : allFlights[0];

    if (target) {
      await loadFlight(target.id, target.file, target.has_adsb);
    }
  }

  boot();

})();
