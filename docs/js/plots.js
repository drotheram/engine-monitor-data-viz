/**
 * plots.js — Plotly chart definitions for engine monitor data
 *
 * Exposes:
 *   PlotController.renderEngine(divId, flightData)
 *   PlotController.renderElectrical(divId, flightData)
 *   PlotController.renderFuel(divId, flightData)
 *   PlotController.renderAltitude(divId, adsbData, flightMeta)
 *   PlotController.linkCharts(divIds, onHoverCallback)
 *   PlotController.purge(divId)
 */

const PlotController = (() => {

  // -------------------------------------------------------
  // Shared Plotly layout base
  // -------------------------------------------------------
  const BASE_LAYOUT = {
    paper_bgcolor: "transparent",
    plot_bgcolor:  "#0d1117",
    font:  { color: "#8b949e", size: 11, family: "inherit" },
    margin: { l: 58, r: 14, t: 10, b: 36 },
    showlegend: true,
    legend: {
      bgcolor:    "rgba(22,27,34,.85)",
      bordercolor: "#30363d",
      borderwidth: 1,
      font: { size: 10, color: "#e6edf3" },
      orientation: "h",
      yanchor: "bottom",
      y: 1.02,
      xanchor: "left",
      x: 0,
    },
    hovermode: "x unified",
    hoverlabel: {
      bgcolor:     "#1c2128",
      bordercolor: "#388bfd",
      font: { color: "#e6edf3", size: 11 },
    },
    xaxis: {
      type: "linear",
      tickformat: _secToHMS,
      color:       "#484f58",
      gridcolor:   "#21262d",
      zerolinecolor: "#30363d",
      showspikes: true,
      spikecolor:  "#388bfd",
      spikethickness: 1,
      spikedash:   "dot",
      spikemode:   "across+marker",
      rangeslider: { visible: false },
    },
    yaxis: {
      color:     "#484f58",
      gridcolor: "#21262d",
      zerolinecolor: "#30363d",
    },
    modebar: {
      bgcolor:    "transparent",
      color:      "#484f58",
      activecolor:"#388bfd",
    },
  };

  const PLOTLY_CONFIG = {
    responsive:    true,
    displaylogo:   false,
    modeBarButtonsToRemove: [
      "select2d", "lasso2d", "autoScale2d",
      "hoverClosestCartesian", "hoverCompareCartesian",
      "toggleSpikelines",
    ],
    toImageButtonOptions: { format: "png", scale: 2 },
  };

  // -------------------------------------------------------
  // Colour palette
  // -------------------------------------------------------
  const C = {
    egt1: "#ff4d4d", egt2: "#ff8c00", egt3: "#ffd700", egt4: "#ff6a3d",
    cht1: "#4fc3f7", cht2: "#00e5ff", cht3: "#26c6da", cht4: "#80deea",
    rpm:  "#e6edf3", flow: "#69db7c",
    volts:"#ffd43b", amps: "#ffa94d",
    fuelL:"#74c0fc", fuelR:"#f783ac",
    oil_p:"#b197fc", oil_t:"#ff8fab",
    oat:  "#a9e34b", carb: "#e7c07b",
    alt:  "#388bfd",
  };

  // -------------------------------------------------------
  // Helpers
  // -------------------------------------------------------
  function _secToHMS(s) {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = Math.floor(s % 60);
    if (h > 0) return `${h}:${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}`;
    return `${String(m).padStart(2,"0")}:${String(sec).padStart(2,"0")}`;
  }

  function _makeHoverTemplate(label, unit, decimals = 0) {
    const fmt = decimals > 0 ? `.${decimals}f` : "d";
    return `<b>${label}</b>: %{y:${fmt}} ${unit}<extra></extra>`;
  }

  function _trace(name, x, y, color, opts = {}) {
    return {
      name,
      x,
      y,
      type:          opts.type || "scatter",
      mode:          opts.mode || "lines",
      line:          { color, width: opts.width || 1.5, dash: opts.dash || "solid" },
      yaxis:         opts.yaxis || "y",
      hovertemplate: opts.hoverTemplate || `<b>${name}</b>: %{y}<extra></extra>`,
      connectgaps:   true,
      visible:       opts.visible !== undefined ? opts.visible : true,
    };
  }

  function _chartHeight() {
    // Adaptive height: ~30% of viewport, min 200
    return Math.max(200, Math.round(window.innerHeight * 0.28));
  }

  // -------------------------------------------------------
  // Engine Chart
  // -------------------------------------------------------
  function renderEngine(divId, d) {
    const ts = d.ts;

    const traces = [
      // EGTs — left y-axis (°F)
      _trace("EGT 1", ts, d.egt1, C.egt1, { hoverTemplate: _makeHoverTemplate("EGT 1","°F") }),
      _trace("EGT 2", ts, d.egt2, C.egt2, { hoverTemplate: _makeHoverTemplate("EGT 2","°F") }),
      _trace("EGT 3", ts, d.egt3, C.egt3, { hoverTemplate: _makeHoverTemplate("EGT 3","°F") }),
      _trace("EGT 4", ts, d.egt4, C.egt4, { hoverTemplate: _makeHoverTemplate("EGT 4","°F") }),
      // CHTs — left y-axis (°F), dashed
      _trace("CHT 1", ts, d.cht1, C.cht1, { dash:"dash", hoverTemplate: _makeHoverTemplate("CHT 1","°F") }),
      _trace("CHT 2", ts, d.cht2, C.cht2, { dash:"dash", hoverTemplate: _makeHoverTemplate("CHT 2","°F") }),
      _trace("CHT 3", ts, d.cht3, C.cht3, { dash:"dash", hoverTemplate: _makeHoverTemplate("CHT 3","°F") }),
      _trace("CHT 4", ts, d.cht4, C.cht4, { dash:"dash", hoverTemplate: _makeHoverTemplate("CHT 4","°F") }),
      // RPM — right y-axis
      _trace("RPM",   ts, d.rpm,  C.rpm,  { yaxis:"y2", width:2, hoverTemplate: _makeHoverTemplate("RPM","RPM") }),
      // Fuel Flow — right y-axis
      _trace("Fuel Flow", ts, d.flow, C.flow, {
        yaxis: "y3",
        hoverTemplate: _makeHoverTemplate("Flow","GPH",1),
      }),
      // Oil P — hidden y-axis (PSI)
      _trace("Oil P",  ts, d.oil_p, C.oil_p, {
        yaxis: "y4", dash: "dot",
        hoverTemplate: _makeHoverTemplate("Oil P","PSI"),
      }),
      // Oil T — hidden y-axis (°F)
      _trace("Oil T",  ts, d.oil_t, C.oil_t, {
        yaxis: "y5", dash: "dot",
        hoverTemplate: _makeHoverTemplate("Oil T","°F"),
      }),
      // Carb T — hidden y-axis (°C), legend-only by default
      _trace("Carb T", ts, d.carb_t, C.carb, {
        yaxis: "y6", dash: "dot",
        visible: "legendonly",
        hoverTemplate: _makeHoverTemplate("Carb T","°C",1),
      }),
    ];

    const layout = {
      ...BASE_LAYOUT,
      height: _chartHeight(),
      yaxis: {
        ...BASE_LAYOUT.yaxis,
        title: { text: "Temperature (°F)", font: { size: 10 } },
        range: [0, 1600],
      },
      yaxis2: {
        ...BASE_LAYOUT.yaxis,
        title: { text: "RPM", font: { size: 10 }, standoff: 4 },
        overlaying: "y",
        side: "right",
        range: [0, 3200],
        showgrid: false,
      },
      yaxis3: {
        ...BASE_LAYOUT.yaxis,
        title: { text: "GPH", font: { size: 10 }, standoff: 4 },
        overlaying: "y",
        side: "right",
        position: 0.98,
        range: [0, 20],
        showgrid: false,
        visible: false,
      },
      yaxis4: {
        ...BASE_LAYOUT.yaxis,
        overlaying: "y",
        side: "right",
        showgrid: false,
        visible: false,   // Oil P axis (PSI) — hidden, shows in hover/legend
      },
      yaxis5: {
        ...BASE_LAYOUT.yaxis,
        overlaying: "y",
        side: "right",
        showgrid: false,
        visible: false,   // Oil T axis (°F) — hidden, shows in hover/legend
      },
      yaxis6: {
        ...BASE_LAYOUT.yaxis,
        overlaying: "y",
        side: "right",
        showgrid: false,
        visible: false,   // Carb T axis (°C) — hidden, shows in hover/legend
      },
    };

    Plotly.react(divId, traces, layout, PLOTLY_CONFIG);
  }

  // -------------------------------------------------------
  // Electrical Chart
  // -------------------------------------------------------
  function renderElectrical(divId, d) {
    const ts = d.ts;

    const traces = [
      _trace("Volts", ts, d.volts, C.volts, {
        width: 2,
        hoverTemplate: _makeHoverTemplate("Volts","V",1),
      }),
      _trace("Amps",  ts, d.amps,  C.amps,  {
        yaxis: "y2",
        width: 2,
        hoverTemplate: _makeHoverTemplate("Amps","A",1),
      }),
    ];

    const layout = {
      ...BASE_LAYOUT,
      height: Math.max(160, Math.round(window.innerHeight * 0.18)),
      yaxis: {
        ...BASE_LAYOUT.yaxis,
        title: { text: "Volts (V)", font: { size: 10 } },
        autorange: true,
      },
      yaxis2: {
        ...BASE_LAYOUT.yaxis,
        title: { text: "Amps (A)", font: { size: 10 }, standoff: 4 },
        overlaying: "y",
        side: "right",
        autorange: true,
        showgrid: false,
      },
    };

    Plotly.react(divId, traces, layout, PLOTLY_CONFIG);
  }

  // -------------------------------------------------------
  // Fuel Chart
  // -------------------------------------------------------
  function renderFuel(divId, d) {
    const ts = d.ts;

    const traces = [
      _trace("Fuel L", ts, d.fuel_l, C.fuelL, {
        hoverTemplate: _makeHoverTemplate("Fuel L","gal",1),
      }),
      _trace("Fuel R", ts, d.fuel_r, C.fuelR, {
        hoverTemplate: _makeHoverTemplate("Fuel R","gal",1),
      }),
      _trace("Fuel Flow", ts, d.flow, C.flow, {
        yaxis: "y2",
        hoverTemplate: _makeHoverTemplate("Flow","GPH",1),
      }),
      _trace("OAT",    ts, d.oat,    C.oat,  {
        yaxis: "y3", dash: "dot",
        hoverTemplate: _makeHoverTemplate("OAT","°C",1),
      }),
    ];

    const layout = {
      ...BASE_LAYOUT,
      height: _chartHeight(),
      yaxis: {
        ...BASE_LAYOUT.yaxis,
        title: { text: "Fuel (gal)", font: { size: 10 } },
        range: [0, 30],
      },
      yaxis2: {
        ...BASE_LAYOUT.yaxis,
        title: { text: "Flow (GPH)", font: { size: 10 } },
        overlaying: "y", side: "right",
        range: [0, 20], showgrid: false,
      },
      yaxis3: {
        ...BASE_LAYOUT.yaxis,
        title: { text: "OAT (°C)", font: { size: 10 } },
        overlaying: "y", side: "right",
        position: 0.93, visible: false, showgrid: false,
        range: [-30, 50],
      },
    };

    Plotly.react(divId, traces, layout, PLOTLY_CONFIG);
  }

  // -------------------------------------------------------
  // Altitude Chart (from ADS-B)
  // -------------------------------------------------------
  function renderAltitude(divId, adsbData, flightMeta, epochStart) {
    const track = (adsbData.track || []).filter(p => p.alt_m != null);
    if (track.length === 0) return;

    // epochStart: Unix timestamp of engine-log ts=0 (first data point after
    // idle-tail crop). Subtracting it from each ADS-B point's absolute
    // timestamp converts to the same "seconds from log start" axis used by
    // the engine/electrical/fuel charts.  Falls back to adsbData.start_ts
    // (= file-header Zulu − 15 min) for older data files without epochStart.
    const startTs = epochStart || adsbData.start_ts || 0;
    const ts = track.map(p => p.ts - startTs);

    // Altitude unit detection: the FlightAware API returns altitude in thousands
    // of feet. Early versions of fetch_adsb.py stored this raw value directly
    // (so alt_m=4.6 really means 4,600 ft). Newer versions will store proper
    // metres (alt_m≈1402 for 4,600 ft). Detect by whether max(alt_m) < 100.
    const maxAltM = Math.max(...track.map(p => p.alt_m || 0));
    const altFt = maxAltM < 100
      ? track.map(p => Math.round((p.alt_m || 0) * 1000))      // legacy kft → ft
      : track.map(p => Math.round((p.alt_m || 0) * 3.28084));  // metres → ft

    const traces = [{
      name: "Altitude",
      x: ts, y: altFt,
      type: "scatter", mode: "lines",
      fill: "tozeroy",
      fillcolor: "rgba(56,139,253,.12)",
      line: { color: C.alt, width: 1.5 },
      hovertemplate: "<b>Altitude</b>: %{y} ft<extra></extra>",
      connectgaps: true,
    }];

    const layout = {
      ...BASE_LAYOUT,
      height: 260,
      margin: { l: 52, r: 10, t: 6, b: 36 },
      legend: { ...BASE_LAYOUT.legend, visible: false },
      yaxis: {
        ...BASE_LAYOUT.yaxis,
        title: { text: "Altitude (ft)", font: { size: 10 } },
      },
    };

    Plotly.react(divId, traces, layout, PLOTLY_CONFIG);
  }

  // -------------------------------------------------------
  // Linked crosshair across all charts + map
  // -------------------------------------------------------
  function linkCharts(divIds, onHoverCallback) {
    let _syncing = false;
    let _rafId   = null;

    // Lightweight CSS crosshair — avoids the expensive Plotly.Fx.hover() relay
    function _showCrosshair(xval) {
      divIds.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        const fl = el._fullLayout;
        if (!fl || !fl.xaxis || !fl.margin) return;
        let xh = el.querySelector(".x-crosshair");
        if (!xh) {
          xh = document.createElement("div");
          xh.className = "x-crosshair";
          el.appendChild(xh);
        }
        // Hide if xval falls outside this chart's visible x range
        // (e.g. altitude chart has no data during ground taxi at ts=0)
        const pxOffset = fl.xaxis.l2p(xval);
        const plotW    = fl.width - fl.margin.l - fl.margin.r;
        if (pxOffset < 0 || pxOffset > plotW) {
          xh.style.display = "none";
          return;
        }
        xh.style.left    = (fl.margin.l + pxOffset) + "px";
        xh.style.top     = fl.margin.t + "px";
        xh.style.height  = (fl.height - fl.margin.t - fl.margin.b) + "px";
        xh.style.display = "block";
      });
    }

    function _hideCrosshair() {
      divIds.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        const xh = el.querySelector(".x-crosshair");
        if (xh) xh.style.display = "none";
      });
    }

    // Cascade prevention: Plotly fires plotly_relayout on target charts via
    // requestAnimationFrame (rAF). async/await clears _syncing when the Promise
    // resolves — but Promise resolution is a microtask that runs *before* the
    // next rAF, so cascade events still slip through with _syncing=false.
    //
    // Fix: use setTimeout(0) to clear _syncing. setTimeout is a macrotask that
    // fires AFTER the current rendering phase (rAF → microtasks → rendering →
    // next macrotask), guaranteeing _syncing stays true through the entire rAF
    // cycle where Plotly emits the cascade plotly_relayout events.
    let _clearTimer = null;

    const handler = (evt, sourceId) => {
      if (_syncing) return;

      let update = null;
      if (evt["xaxis.range[0]"] !== undefined) {
        update = {
          "xaxis.range[0]": evt["xaxis.range[0]"],
          "xaxis.range[1]": evt["xaxis.range[1]"],
        };
      } else if (evt["xaxis.autorange"]) {
        update = { "xaxis.autorange": true };
      } else {
        return;
      }

      _syncing = true;
      if (_clearTimer) { clearTimeout(_clearTimer); _clearTimer = null; }

      const targets = divIds.filter(id => id !== sourceId);
      Promise.all(targets.map(id => Plotly.relayout(id, update)))
        .finally(() => {
          _clearTimer = setTimeout(() => {
            _clearTimer = null;
            _syncing = false;
          }, 0);
        });
    };

    divIds.forEach(id => {
      const el = document.getElementById(id);
      if (!el) return;

      el.on("plotly_relayout", evt => handler(evt, id));

      el.on("plotly_hover", data => {
        if (!data.points || !data.points.length) return;
        const xval = data.points[0].x;
        if (_rafId) cancelAnimationFrame(_rafId);
        _rafId = requestAnimationFrame(() => {
          _showCrosshair(xval);
          if (onHoverCallback) onHoverCallback(xval);
        });
      });

      el.on("plotly_unhover", () => {
        if (_rafId) cancelAnimationFrame(_rafId);
        _rafId = requestAnimationFrame(_hideCrosshair);
      });
    });
  }

  function purge(divId) {
    const el = document.getElementById(divId);
    if (el) Plotly.purge(el);
  }

  return {
    renderEngine,
    renderElectrical,
    renderFuel,
    renderAltitude,
    linkCharts,
    purge,
  };
})();
