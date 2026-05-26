/**
 * map.js — Leaflet map integration for ADS-B track display
 *
 * Exposes:
 *   MapController.init(containerId)
 *   MapController.loadTrack(adsbData, flightMeta)
 *   MapController.setCursor(unixTimestamp)   ← called on chart hover
 *   MapController.clear()
 */

const MapController = (() => {
  let _map          = null;
  let _trackLayer   = null;
  let _cursorMarker = null;
  let _trackData    = null;   // sorted array of { ts, lat, lon, alt_m }
  let _startTs      = 0;      // flight start Unix timestamp (Zulu)

  // Interpolate lat/lon/alt at a given Unix timestamp
  function _interpolate(ts) {
    if (!_trackData || _trackData.length === 0) return null;
    if (ts <= _trackData[0].ts) return _trackData[0];
    if (ts >= _trackData[_trackData.length - 1].ts) return _trackData[_trackData.length - 1];
    // Binary search
    let lo = 0, hi = _trackData.length - 1;
    while (lo < hi - 1) {
      const mid = (lo + hi) >> 1;
      if (_trackData[mid].ts <= ts) lo = mid; else hi = mid;
    }
    const a = _trackData[lo], b = _trackData[hi];
    const frac = (ts - a.ts) / (b.ts - a.ts);
    return {
      lat:   a.lat   + frac * (b.lat   - a.lat),
      lon:   a.lon   + frac * (b.lon   - a.lon),
      alt_m: a.alt_m != null && b.alt_m != null
               ? a.alt_m + frac * (b.alt_m - a.alt_m)
               : null,
    };
  }

  // Custom lightweight aircraft SVG icon
  function _makeIcon(heading) {
    const svg = `
      <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24">
        <g transform="rotate(${(heading || 0) + 45}, 12, 12)">
          <path fill="#388bfd" stroke="#fff" stroke-width="1"
            d="M21 16v-2l-8-5V3.5C13 2.67 12.33 2 11.5 2S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"/>
        </g>
      </svg>`;
    return L.divIcon({
      html:        svg,
      className:   "",
      iconSize:    [28, 28],
      iconAnchor:  [14, 14],
    });
  }

  function init(containerId) {
    if (_map) { _map.remove(); _map = null; }

    _map = L.map(containerId, {
      zoomControl:    true,
      attributionControl: true,
    });

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom:     19,
      attribution: "© OpenStreetMap contributors",
    }).addTo(_map);
  }

  function loadTrack(adsbData, flightMeta) {
    if (!_map) return;
    clear();

    _trackData = (adsbData.track || [])
      .filter(p => p.lat != null && p.lon != null)
      .sort((a, b) => a.ts - b.ts);

    _startTs = adsbData.start_ts || 0;

    if (_trackData.length === 0) return;

    const latlngs = _trackData.map(p => [p.lat, p.lon]);

    // Draw track line
    _trackLayer = L.layerGroup([
      L.polyline(latlngs, {
        color:  "#388bfd",
        weight: 3,
        opacity: .85,
      }),
      // Start marker
      L.circleMarker(latlngs[0], {
        radius: 7, color: "#3fb950", fillColor: "#3fb950",
        fillOpacity: 1, weight: 2,
      }).bindPopup("Departure"),
      // End marker
      L.circleMarker(latlngs[latlngs.length - 1], {
        radius: 7, color: "#f78166", fillColor: "#f78166",
        fillOpacity: 1, weight: 2,
      }).bindPopup("Arrival"),
    ]).addTo(_map);

    // Fit bounds with padding
    const bounds = L.latLngBounds(latlngs);
    _map.fitBounds(bounds, { padding: [24, 24] });
  }

  /**
   * Move cursor marker on the map.
   * @param {number} seconds_from_start - x value from Plotly hover (seconds elapsed)
   */
  function setCursor(seconds_from_start) {
    if (!_map || !_trackData || _trackData.length === 0) return;
    const ts = _startTs + seconds_from_start;
    const pt = _interpolate(ts);
    if (!pt) return;

    if (!_cursorMarker) {
      _cursorMarker = L.marker([pt.lat, pt.lon], {
        icon: _makeIcon(pt.heading),
        zIndexOffset: 1000,
      }).addTo(_map);
    } else {
      _cursorMarker.setLatLng([pt.lat, pt.lon]);
      if (pt.heading != null) {
        _cursorMarker.setIcon(_makeIcon(pt.heading));
      }
    }
  }

  function clear() {
    if (_trackLayer) { _trackLayer.remove(); _trackLayer = null; }
    if (_cursorMarker) { _cursorMarker.remove(); _cursorMarker = null; }
    _trackData = null;
    _startTs   = 0;
  }

  return { init, loadTrack, setCursor, clear };
})();
