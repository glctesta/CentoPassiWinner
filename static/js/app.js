/**
 * Centopassi Route Planner - Frontend Application
 * Interactive Leaflet map with route optimization controls.
 */

// ── State ───────────────────────────────────────────────────
const state = {
    map: null,
    allWaypoints: [],
    waypointMarkers: [],
    routeLayers: [],
    finishMarker: null,
    finishCoords: null,
    selectedDay: null,
    routeResult: null,
    isOptimizing: false,
    pollInterval: null,
    mode: 'explore', // 'explore' | 'set-finish' | 'results'
    unpavedMode: 'limit', // 'allow' | 'limit' | 'exclude'
};

// ── Day Colors ──────────────────────────────────────────────
const DAY_COLORS = ['#6366f1', '#06b6d4', '#f59e0b', '#ef4444'];
const DAY_NAMES = ['Giorno 1 (29 Mag)', 'Giorno 2 (30 Mag)', 'Giorno 3 (31 Mag)', 'Giorno 4 (1 Giu)'];

// ── Init ────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initMap();
    loadWaypoints();
    initControls();
});

// ── Basemap Themes ───────────────────────────────────────────

const BASEMAPS = [
    {
        id: 'dark',
        label: 'Scuro',
        preview: '#1a1a2e',
        url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        attr: '&copy; OSM &copy; CARTO',
        sub: 'abcd',
    },
    {
        id: 'light',
        label: 'Chiaro',
        preview: '#f0f0e8',
        url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
        attr: '&copy; OSM &copy; CARTO',
        sub: 'abcd',
    },
    {
        id: 'satellite',
        label: 'Satellite',
        preview: '#2d4a1e',
        url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attr: '&copy; Esri, Maxar, Earthstar Geographics',
        sub: '',
    },
    {
        id: 'topo',
        label: 'Topografica',
        preview: '#d4c5a9',
        url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
        attr: '&copy; OpenTopoMap contributors',
        sub: 'abc',
    },
    {
        id: 'terrain',
        label: 'Terrain',
        preview: '#8fbc8f',
        url: 'https://stamen-tiles-{s}.a.ssl.fastly.net/terrain/{z}/{x}/{y}{r}.png',
        attr: '&copy; Stamen Design, &copy; OSM contributors',
        sub: 'abcd',
    },
    {
        id: 'voyage',
        label: 'Voyage',
        preview: '#cce0f5',
        url: 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
        attr: '&copy; OSM &copy; CARTO',
        sub: 'abcd',
    },
    {
        id: 'osm',
        label: 'OpenStreetMap',
        preview: '#a8c8a0',
        url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        attr: '&copy; OpenStreetMap contributors',
        sub: 'abc',
    },
    {
        id: 'midnight',
        label: 'Midnight',
        preview: '#0f0f1a',
        url: 'https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png',
        attr: '&copy; OSM &copy; CARTO',
        sub: 'abcd',
    },
];

let _currentBasemapLayer = null;
let _currentBasemapId = localStorage.getItem('basemap') || 'dark';

function initBasemapSwitcher() {
    const grid = document.getElementById('basemap-grid');
    BASEMAPS.forEach(bm => {
        const btn = document.createElement('button');
        btn.className = 'basemap-option' + (bm.id === _currentBasemapId ? ' active' : '');
        btn.dataset.id = bm.id;
        btn.innerHTML = `
            <span class="basemap-swatch" style="background:${bm.preview};"></span>
            <span class="basemap-label">${bm.label}</span>
        `;
        btn.onclick = () => setBasemap(bm.id);
        grid.appendChild(btn);
    });
}

function setBasemap(id) {
    const bm = BASEMAPS.find(b => b.id === id);
    if (!bm) return;

    if (_currentBasemapLayer) {
        state.map.removeLayer(_currentBasemapLayer);
    }
    _currentBasemapLayer = L.tileLayer(bm.url, {
        attribution: bm.attr,
        subdomains: bm.sub || 'abc',
        maxZoom: 19,
    }).addTo(state.map);
    // Send to back so route layers stay on top
    _currentBasemapLayer.bringToBack();

    // Update active state in panel
    document.querySelectorAll('.basemap-option').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.id === id);
    });
    _currentBasemapId = id;
    localStorage.setItem('basemap', id);

    // Close panel
    closeBasemapPanel();
}

function toggleBasemapPanel() {
    const panel = document.getElementById('basemap-panel');
    const isOpen = panel.style.display !== 'none';
    panel.style.display = isOpen ? 'none' : 'block';
}

function closeBasemapPanel() {
    const panel = document.getElementById('basemap-panel');
    if (panel) panel.style.display = 'none';
}

// Close panel when clicking outside
document.addEventListener('click', (e) => {
    if (!e.target.closest('#basemap-switcher')) closeBasemapPanel();
});

function initMap() {
    state.map = L.map('map', {
        center: [41.5, 13.0],
        zoom: 6,
        zoomControl: true,
        attributionControl: true,
    });

    // Apply saved (or default) basemap
    initBasemapSwitcher();
    setBasemap(_currentBasemapId);

    // Click handler for setting finish point
    state.map.on('click', (e) => {
        if (state.mode === 'set-finish') {
            setFinishPoint(e.latlng.lat, e.latlng.lng);
        }
    });
}

// ── Load Waypoints ──────────────────────────────────────────

async function loadWaypoints() {
    try {
        const res = await fetch('/api/waypoints');
        const data = await res.json();
        state.allWaypoints = data.waypoints;
        displayWaypoints(data.waypoints);
        updateStats(data.stats);
        
        // Show loaded file info
        if (data.loaded_file) {
            showLoadedFile(data.loaded_file);
        }
    } catch (err) {
        console.error('Error loading waypoints:', err);
    }
}

// ── File Upload ─────────────────────────────────────────────

function handleFileDrop(event) {
    event.preventDefault();
    event.target.closest('.upload-zone').classList.remove('dragover');
    const file = event.dataTransfer.files[0];
    if (file) handleFileSelect(file);
}

async function handleFileSelect(file) {
    if (!file) return;
    
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['gpx', 'xlsx', 'xls'].includes(ext)) {
        alert('Formato non supportato. Usare file .GPX o .XLSX');
        return;
    }
    
    // Show uploading state
    const uploadZone = document.getElementById('upload-zone');
    const origHTML = uploadZone.innerHTML;
    uploadZone.innerHTML = '<div class="spinner"></div><div class="upload-text">Caricamento in corso...</div>';
    
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        const res = await fetch('/api/upload', {
            method: 'POST',
            body: formData,
        });
        
        const data = await res.json();
        
        if (!res.ok) {
            throw new Error(data.error || 'Errore nel caricamento');
        }
        
        // Success — reload waypoints on map
        showLoadedFile(data.filename);
        
        // Refresh waypoints
        const wpRes = await fetch('/api/waypoints');
        const wpData = await wpRes.json();
        state.allWaypoints = wpData.waypoints;
        displayWaypoints(wpData.waypoints);
        updateStats(wpData.stats);
        
        // Reset upload zone
        uploadZone.innerHTML = origHTML;
        
    } catch (err) {
        alert(`Errore: ${err.message}`);
        uploadZone.innerHTML = origHTML;
    }
}

function showLoadedFile(filename) {
    const ext = filename.split('.').pop().toLowerCase();
    const badge = document.getElementById('file-badge');
    const nameEl = document.getElementById('loaded-filename');
    const infoDiv = document.getElementById('loaded-file-info');
    
    badge.textContent = ext.toUpperCase();
    badge.className = `file-badge ${ext}`;
    nameEl.textContent = filename;
    infoDiv.style.display = 'flex';
}

// ── Search Waypoints ────────────────────────────────────────

let _searchHighlights = [];
let _searchDebounce = null;

function searchWaypoints(query) {
    clearTimeout(_searchDebounce);
    _searchDebounce = setTimeout(() => _doSearch(query), 200);
}

function _doSearch(query) {
    const resultsDiv = document.getElementById('search-results');
    const countDiv = document.getElementById('search-count');
    const clearBtn = document.getElementById('search-clear');
    
    // Clear old highlights
    _searchHighlights.forEach(l => state.map.removeLayer(l));
    _searchHighlights = [];
    
    // Restore all markers to normal opacity
    state.waypointMarkers.forEach(m => {
        m.setStyle({ opacity: 0.4, fillOpacity: 0.8 });
    });
    
    query = (query || '').trim().toLowerCase();
    
    if (!query) {
        resultsDiv.innerHTML = '';
        countDiv.style.display = 'none';
        clearBtn.style.display = 'none';
        return;
    }
    
    clearBtn.style.display = 'block';
    
    // Search across all WP fields
    const matches = state.allWaypoints.filter(wp => {
        const fields = [
            wp.number, wp.name, wp.description,
            wp.city, wp.province, wp.display_name
        ].map(f => (f || '').toLowerCase());
        return fields.some(f => f.includes(query));
    });
    
    // Show count
    countDiv.style.display = 'block';
    countDiv.textContent = `${matches.length} risultat${matches.length === 1 ? 'o' : 'i'} trovat${matches.length === 1 ? 'o' : 'i'}`;
    
    // Dim non-matching markers
    if (matches.length > 0 && matches.length < state.allWaypoints.length) {
        const matchIds = new Set(matches.map(m => m.id));
        state.waypointMarkers.forEach(m => {
            if (!matchIds.has(m.wpData.id)) {
                m.setStyle({ opacity: 0.08, fillOpacity: 0.08 });
            } else {
                m.setStyle({ opacity: 1, fillOpacity: 1 });
            }
        });
    }
    
    // Build results HTML (max 25)
    const displayed = matches.slice(0, 25);
    resultsDiv.innerHTML = displayed.map(wp => {
        let badges = '';
        if (wp.is_golden_point) badges += '<span class="sr-badge gp">GP</span>';
        if (wp.is_unpaved) badges += '<span class="sr-badge unpaved">🔶</span>';
        const desc = wp.city || wp.description?.substring(0, 30) || '';
        return `<div class="search-result-item" onclick="zoomToSearchResult(${wp.lat}, ${wp.lon}, ${wp.id})">
            <span class="sr-number">WP ${wp.number}</span>
            <span class="sr-desc">${desc}</span>
            <span class="sr-badges">${badges}</span>
        </div>`;
    }).join('');
    
    if (matches.length > 25) {
        resultsDiv.innerHTML += `<div class="search-count" style="padding:6px; text-align:center;">...e altri ${matches.length - 25}</div>`;
    }
    
    // Highlight matching WPs on map with pulsing rings
    matches.forEach(wp => {
        const ring = L.circleMarker([wp.lat, wp.lon], {
            radius: 14,
            fillColor: 'transparent',
            fillOpacity: 0,
            color: '#6366f1',
            weight: 2,
            opacity: 0.8,
            className: 'search-highlight-ring',
        }).addTo(state.map);
        _searchHighlights.push(ring);
    });
    
    // Auto-fit map if multiple results
    if (matches.length > 1 && matches.length <= 50) {
        const bounds = L.latLngBounds(matches.map(wp => [wp.lat, wp.lon]));
        state.map.fitBounds(bounds, { padding: [60, 60], maxZoom: 12 });
    } else if (matches.length === 1) {
        state.map.setView([matches[0].lat, matches[0].lon], 13, { animate: true });
    }
}

function zoomToSearchResult(lat, lon, wpId) {
    state.map.setView([lat, lon], 15, { animate: true });
    
    // Open popup of matching marker
    const marker = state.waypointMarkers.find(m => m.wpData.id === wpId);
    if (marker) marker.openPopup();
}

function clearSearch() {
    document.getElementById('wp-search').value = '';
    searchWaypoints('');
}


function displayWaypoints(waypoints) {
    // Clear existing markers
    state.waypointMarkers.forEach(m => state.map.removeLayer(m));
    state.waypointMarkers = [];

    waypoints.forEach(wp => {
        const color = wp.is_golden_point ? '#fbbf24' : (wp.is_unpaved ? '#fb923c' : '#3b82f6');
        const radius = wp.is_golden_point ? 6 : 4;
        const opacity = 0.8;

        const marker = L.circleMarker([wp.lat, wp.lon], {
            radius: radius,
            fillColor: color,
            fillOpacity: opacity,
            color: '#fff',
            weight: 1,
            opacity: 0.4,
        });

        // Popup
        let badges = '';
        if (wp.is_golden_point) badges += '<span class="popup-badge gp">⭐ Golden Point</span>';
        if (wp.is_unpaved) badges += '<span class="popup-badge unpaved">🔶 Sterrato</span>';

        marker.bindPopup(`
            <div class="popup-title">WP ${wp.number}</div>
            ${badges}
            <div class="popup-info">
                ${wp.description ? `<div>📍 ${wp.description}</div>` : ''}
                ${wp.city ? `<div>🏘️ ${wp.city}${wp.province ? `, ${wp.province}` : ''}</div>` : ''}
                <div>📐 ${wp.elevation.toFixed(0)}m</div>
                <div>📌 ${wp.lat.toFixed(5)}, ${wp.lon.toFixed(5)}</div>
            </div>
        `, { maxWidth: 280 });

        marker.addTo(state.map);
        marker.wpData = wp;
        state.waypointMarkers.push(marker);
    });

    // Fit bounds
    if (waypoints.length > 0) {
        const bounds = L.latLngBounds(waypoints.map(wp => [wp.lat, wp.lon]));
        state.map.fitBounds(bounds, { padding: [50, 50] });
    }
}

function updateStats(stats) {
    document.getElementById('stat-total').textContent = stats.total;
    document.getElementById('stat-gp').textContent = stats.golden_points;
    document.getElementById('stat-unpaved').textContent = stats.unpaved;
    document.getElementById('stat-elevation').textContent = `${stats.elevation.max.toFixed(0)}m`;
}

// ── Finish Point ────────────────────────────────────────────

function enableSetFinish() {
    state.mode = 'set-finish';
    document.getElementById('map-instruction').style.display = 'block';
    document.getElementById('map-instruction').querySelector('.inst-text').textContent = 
        '🎯 Clicca sulla mappa per posizionare il Traguardo';
    state.map.getContainer().style.cursor = 'crosshair';
}

function setFinishPoint(lat, lng) {
    state.finishCoords = { lat, lng };
    state.mode = 'explore';
    document.getElementById('map-instruction').style.display = 'none';
    state.map.getContainer().style.cursor = '';

    // Update inputs
    document.getElementById('finish-lat').value = lat.toFixed(6);
    document.getElementById('finish-lon').value = lng.toFixed(6);

    // Remove old marker
    if (state.finishMarker) state.map.removeLayer(state.finishMarker);

    // Add finish marker (🏁)
    const finishIcon = L.divIcon({
        html: '<div style="font-size:28px;text-shadow:0 2px 8px rgba(0,0,0,0.5);">🏁</div>',
        iconSize: [32, 32],
        iconAnchor: [16, 32],
        className: 'finish-icon',
    });

    state.finishMarker = L.marker([lat, lng], { icon: finishIcon, draggable: true })
        .addTo(state.map)
        .bindPopup(`<div class="popup-title">🏁 Traguardo</div>
            <div class="popup-info">${lat.toFixed(5)}, ${lng.toFixed(5)}</div>`)
        .openPopup();

    state.finishMarker.on('dragend', (e) => {
        const pos = e.target.getLatLng();
        state.finishCoords = { lat: pos.lat, lng: pos.lng };
        document.getElementById('finish-lat').value = pos.lat.toFixed(6);
        document.getElementById('finish-lon').value = pos.lng.toFixed(6);
    });

    document.getElementById('btn-optimize').disabled = false;
}

function setFinishFromInputs() {
    const lat = parseFloat(document.getElementById('finish-lat').value);
    const lon = parseFloat(document.getElementById('finish-lon').value);
    if (!isNaN(lat) && !isNaN(lon)) {
        setFinishPoint(lat, lon);
        state.map.setView([lat, lon], 10);
    }
}

async function geocodeFinish() {
    const query = document.getElementById('finish-name').value.trim();
    if (!query) { alert('Inserisci un nome città o indirizzo'); return; }
    
    const statusEl = document.getElementById('geocode-status');
    statusEl.style.display = 'block';
    statusEl.textContent = '🔍 Ricerca in corso...';
    statusEl.style.color = '#94a3b8';
    
    try {
        const url = `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(query)}&countrycodes=it&limit=5&addressdetails=1`;
        const res = await fetch(url, { headers: { 'Accept-Language': 'it' } });
        const results = await res.json();
        
        if (results.length === 0) {
            // Retry without country restriction
            const url2 = `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(query)}&limit=5&addressdetails=1`;
            const res2 = await fetch(url2, { headers: { 'Accept-Language': 'it' } });
            const results2 = await res2.json();
            if (results2.length === 0) {
                statusEl.textContent = '❌ Nessun risultato trovato';
                statusEl.style.color = '#ef4444';
                return;
            }
            applyGeocodeResult(results2[0], statusEl);
        } else {
            applyGeocodeResult(results[0], statusEl);
        }
    } catch(e) {
        statusEl.textContent = '❌ Errore di rete: ' + e.message;
        statusEl.style.color = '#ef4444';
    }
}

function applyGeocodeResult(result, statusEl) {
    const lat = parseFloat(result.lat);
    const lon = parseFloat(result.lon);
    document.getElementById('finish-lat').value = lat.toFixed(6);
    document.getElementById('finish-lon').value = lon.toFixed(6);
    
    const displayName = result.display_name.split(',').slice(0, 3).join(',');
    statusEl.textContent = `✅ ${displayName}`;
    statusEl.style.color = '#22c55e';
    
    setFinishPoint(lat, lon);
    state.map.setView([lat, lon], 12, { animate: true });
}

// ── Optimize ────────────────────────────────────────────────

async function startOptimization() {
    if (state.isOptimizing) return;
    if (!state.finishCoords) {
        alert('Selezionare prima il punto di arrivo (Traguardo)');
        return;
    }

    state.isOptimizing = true;
    const useOsrm = document.getElementById('use-osrm').checked;
    const finishName = document.getElementById('finish-name').value || 'Traguardo';
    const maxUnpaved = parseInt(document.getElementById('max-unpaved').value) || 10;

    // UI updates
    document.getElementById('btn-optimize').disabled = true;
    document.getElementById('btn-optimize').innerHTML = '<span class="spinner"></span> Ottimizzazione...';
    const progContainer = document.getElementById('progress-container');
    progContainer.classList.add('active');

    try {
        const res = await fetch('/api/optimize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                finish_lat: state.finishCoords.lat,
                finish_lon: state.finishCoords.lng,
                finish_name: finishName,
                use_osrm: useOsrm,
                unpaved_mode: state.unpavedMode,
                max_unpaved: maxUnpaved,
                check_road_closures: document.getElementById('check-roads').checked,
            }),
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.error || 'Errore server');
        }

        // Poll for status
        state.pollInterval = setInterval(pollOptimizationStatus, 1000);
    } catch (err) {
        alert(`Errore: ${err.message}`);
        resetOptimizeButton();
    }
}

async function pollOptimizationStatus() {
    try {
        const res = await fetch('/api/optimize/status');
        const status = await res.json();

        // Update progress
        document.getElementById('progress-fill').style.width = `${status.percent}%`;
        document.getElementById('progress-text').textContent = status.message;

        if (!status.running) {
            clearInterval(state.pollInterval);
            state.isOptimizing = false;

            if (status.error) {
                alert(`Errore: ${status.error}`);
                resetOptimizeButton();
            } else if (status.result) {
                state.routeResult = status.result;
                displayRoute(status.result);
                resetOptimizeButton();
                document.getElementById('btn-export').style.display = 'flex';
                // Show routing + editor sections
                document.getElementById('routing-section').style.display = 'block';
                document.getElementById('editor-section').style.display = 'block';
                editor.reset();
                // Load initial routing stats
                fetch('/api/route/routing-stats').then(r=>r.json()).then(s=>displayRoutingStats(s)).catch(()=>{});
            }
        }
    } catch (err) {
        console.error('Poll error:', err);
    }
}

function resetOptimizeButton() {
    document.getElementById('btn-optimize').disabled = false;
    document.getElementById('btn-optimize').innerHTML = '🚀 Calcola Percorso';
    state.isOptimizing = false;
}

// ── Display Route ───────────────────────────────────────────

function displayRoute(route) {
    // Clear old route layers
    state.routeLayers.forEach(l => state.map.removeLayer(l));
    state.routeLayers = [];

    // Dim non-selected waypoint markers
    state.waypointMarkers.forEach(m => {
        m.setStyle({ opacity: 0.15, fillOpacity: 0.15 });
    });

    // Display each day
    route.days.forEach((day, idx) => {
        const color = DAY_COLORS[idx] || '#ffffff';

        // Draw route segments
        day.segments.forEach(seg => {
            const coords = seg.geometry.map(p => [p[0], p[1]]);
            if (coords.length >= 2) {
                const polyline = L.polyline(coords, {
                    color: seg.is_unpaved ? '#fb923c' : color,
                    weight: seg.is_unpaved ? 4 : 3,
                    opacity: seg.is_unpaved ? 0.9 : 0.7,
                    dashArray: seg.is_unpaved ? '8 6' : null,
                }).addTo(state.map);
                state.routeLayers.push(polyline);
            }
        });

        // Mark selected waypoints
        day.waypoints.forEach((wp, wpIdx) => {
            const marker = L.circleMarker([wp.lat, wp.lon], {
                radius: wp.is_golden_point ? 8 : 6,
                fillColor: wp.is_golden_point ? '#fbbf24' : color,
                fillOpacity: 0.9,
                color: '#fff',
                weight: 2,
                opacity: 0.8,
            });

            marker.bindPopup(`
                <div class="popup-title">WP ${wp.number} ${wp.is_golden_point ? '⭐' : ''}</div>
                <div class="popup-info">
                    <div>📅 ${DAY_NAMES[idx]}</div>
                    ${wp.description ? `<div>📍 ${wp.description}</div>` : ''}
                    <div>📐 ${wp.elevation.toFixed(0)}m</div>
                    ${wp.road_warnings && wp.road_warnings.length > 0 ?
                        wp.road_warnings.map(w => `<div class="popup-warning">⚠️ ${w}</div>`).join('') : ''}
                </div>
            `);

            marker.addTo(state.map);
            state.routeLayers.push(marker);
        });

        // Start marker for day 1
        if (idx === 0 && day.waypoints.length > 0) {
            const startWp = day.waypoints[0];
            const startIcon = L.divIcon({
                html: '<div style="font-size:24px;text-shadow:0 2px 8px rgba(0,0,0,0.5);">🟢</div>',
                iconSize: [28, 28],
                iconAnchor: [14, 14],
                className: 'start-icon',
            });
            const startMarker = L.marker([startWp.lat, startWp.lon], { icon: startIcon })
                .addTo(state.map)
                .bindPopup(`<div class="popup-title">🟢 Partenza</div>
                    <div class="popup-info">WP ${startWp.number}</div>`);
            state.routeLayers.push(startMarker);
        }
    });

    // Fit map to route 
    const allCoords = [];
    route.days.forEach(d => d.waypoints.forEach(wp => allCoords.push([wp.lat, wp.lon])));
    if (allCoords.length > 0) {
        state.map.fitBounds(L.latLngBounds(allCoords), { padding: [50, 50] });
    }

    // Display alternative waypoints (green markers)
    if (route.alternatives && route.alternatives.length > 0) {
        route.alternatives.forEach(wp => {
            const marker = L.circleMarker([wp.lat, wp.lon], {
                radius: 7,
                fillColor: '#22c55e',
                fillOpacity: 0.7,
                color: '#fff',
                weight: 2,
                opacity: 0.8,
            });
            marker.bindPopup(`
                <div class="popup-title">🟢 ALT WP ${wp.number}</div>
                <div class="popup-badge" style="background:rgba(34,197,94,0.15);color:#22c55e;">ALTERNATIVO</div>
                <div class="popup-info">
                    ${wp.description ? `<div>📍 ${wp.description}</div>` : ''}
                    <div>📐 ${wp.elevation.toFixed(0)}m</div>
                </div>
            `);
            marker.addTo(state.map);
            state.routeLayers.push(marker);
        });
    }

    // Update sidebar
    displayRouteDetails(route);
}

function displayRouteDetails(route) {
    const resultsDiv = document.getElementById('results-section');
    resultsDiv.style.display = 'block';

    // Score card
    const score = route.score;
    document.getElementById('score-total').textContent = score.total_points.toLocaleString();
    document.getElementById('score-passes').textContent = `${score.regular_passes} × 5000 = ${score.pass_points.toLocaleString()}`;
    document.getElementById('score-golden').textContent = `${score.golden_passes} × ${score.reached_100 ? '15000' : '5000'} = ${score.golden_points.toLocaleString()}`;
    document.getElementById('score-elevation').textContent = score.elevation_points.toLocaleString();
    document.getElementById('score-bonus').textContent = score.bonus_100th > 0 ? `+${score.bonus_100th.toLocaleString()}` : '—';

    // Route summary
    document.getElementById('result-total-km').textContent = `${route.total_km.toFixed(0)} km`;
    document.getElementById('result-total-wp').textContent = route.total_waypoints;
    document.getElementById('result-total-gp').textContent = route.total_golden_points;
    document.getElementById('result-total-elev').textContent = `${route.total_elevation_gain.toFixed(0)}m`;

    // Road warnings section
    const roadWarningsContainer = document.getElementById('road-warnings-section');
    if (roadWarningsContainer) {
        if (route.road_warnings && route.road_warnings.length > 0) {
            roadWarningsContainer.style.display = 'block';
            roadWarningsContainer.innerHTML = `
                <div class="card" style="border-left:3px solid #f59e0b;">
                    <div class="card-title"><span class="icon">⚠️</span> Avvisi Stradali (AI)</div>
                    ${route.road_warnings.map(w => `
                        <div class="road-warning ${w.severity}">
                            <span class="rw-severity">${w.severity.toUpperCase()}</span>
                            <span class="rw-area">${w.area}</span>
                            <span class="rw-type">${w.type}</span>
                            <div class="rw-desc">${w.description}</div>
                            ${w.affected_roads && w.affected_roads.length > 0 ?
                                `<div class="rw-roads">Strade: ${w.affected_roads.join(', ')}</div>` : ''}
                        </div>
                    `).join('')}
                </div>`;
        } else {
            roadWarningsContainer.style.display = 'none';
            roadWarningsContainer.innerHTML = '';
        }
    }

    // Day cards
    const daysContainer = document.getElementById('days-container');
    daysContainer.innerHTML = '';

    route.days.forEach((day, idx) => {
        const dayDiv = document.createElement('div');
        dayDiv.className = `day-card day-${idx + 1}`;
        dayDiv.innerHTML = `
            <div class="day-header">
                <span class="day-name">${DAY_NAMES[idx]}</span>
                <span class="day-badge">${day.total_km.toFixed(0)} km</span>
            </div>
            <div class="day-stats">
                <span>🗺️ ${day.waypoint_count} WP</span>
                <span>⭐ ${day.golden_points_count} GP</span>
                <span>⏱️ ${day.total_hours.toFixed(1)}h</span>
                <span>⏰ ${day.start_time}–${day.end_time}</span>
                ${day.unpaved_segments > 0 ? `<span class="warning-badge unpaved">🔶 ${day.unpaved_segments} sterrati</span>` : ''}
                ${day.road_warnings && day.road_warnings.length > 0 ? `<span class="warning-badge road-closure">⚠️ ${day.road_warnings.length} avvisi</span>` : ''}
            </div>
            <div class="wp-list" id="wp-list-${idx}">
                ${day.waypoints.map((wp, i) => `
                    <div class="wp-item" onclick="flyToWP(${wp.lat}, ${wp.lon})">
                        <span class="wp-dot ${wp.is_golden_point ? 'golden' : (wp.is_unpaved ? 'unpaved' : 'normal')}"></span>
                        <span class="wp-name">WP ${wp.number}</span>
                        <span class="wp-desc">${wp.description ? wp.description.substring(0, 30) : ''}</span>
                        ${i > 0 && day.segments[i-1] ? `<span class="wp-km">${day.segments[i-1].distance_km.toFixed(0)}km</span>` : ''}
                    </div>
                `).join('')}
            </div>
        `;

        dayDiv.addEventListener('click', (e) => {
            if (!e.target.closest('.wp-item')) {
                const wpList = dayDiv.querySelector('.wp-list');
                wpList.classList.toggle('expanded');
                highlightDay(idx);
            }
        });

        daysContainer.appendChild(dayDiv);
    });

    // Alternative waypoints section
    if (route.alternatives && route.alternatives.length > 0) {
        const altDiv = document.createElement('div');
        altDiv.className = 'day-card';
        altDiv.style.borderLeftColor = '#22c55e';
        altDiv.innerHTML = `
            <div class="day-header">
                <span class="day-name">🟢 WP Alternativi</span>
                <span class="day-badge" style="background:rgba(34,197,94,0.15);color:#22c55e;">${route.alternatives.length} WP</span>
            </div>
            <div class="day-stats">
                <span>Di riserva, lungo il percorso</span>
            </div>
            <div class="wp-list" id="wp-list-alt">
                ${route.alternatives.map(wp => `
                    <div class="wp-item" onclick="flyToWP(${wp.lat}, ${wp.lon})">
                        <span class="wp-dot" style="background:#22c55e;"></span>
                        <span class="wp-name">ALT ${wp.number}</span>
                        <span class="wp-desc">${wp.description ? wp.description.substring(0, 35) : ''}</span>
                    </div>
                `).join('')}
            </div>
        `;
        altDiv.addEventListener('click', (e) => {
            if (!e.target.closest('.wp-item')) {
                altDiv.querySelector('.wp-list').classList.toggle('expanded');
            }
        });
        daysContainer.appendChild(altDiv);
    }
}

function highlightDay(dayIdx) {
    state.selectedDay = dayIdx;
    // TODO: Could dim other days' layers
}

function flyToWP(lat, lon) {
    state.map.setView([lat, lon], 14, { animate: true });
}

// ── Export ───────────────────────────────────────────────────

function downloadGPX() {
    window.open('/api/export', '_blank');
}

function downloadDay(dayNum) {
    window.open(`/api/export/day/${dayNum}`, '_blank');
}

function downloadAllDays() {
    window.open('/api/export/all', '_blank');
}

// ── Professional Road Routing ────────────────────────────────

let _routingPollInterval = null;

async function computeFullRouting() {
    if (_routingPollInterval) return;   // already running

    const claudeCheck = document.getElementById('routing-claude-check')?.checked || false;
    const btn = document.getElementById('btn-compute-routing');
    btn.disabled = true;
    btn.innerHTML = '⏳ Calcolo in corso…';

    // Show progress UI
    document.getElementById('routing-progress-wrap').style.display = 'block';
    document.getElementById('routing-compliance').style.display = 'none';
    document.getElementById('routing-road-warnings').style.display = 'none';
    document.getElementById('routing-quality-bar').style.display = 'none';

    try {
        const res = await fetch('/api/route/full-routing', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ use_osrm: true, claude_check: claudeCheck }),
        });
        if (!res.ok) {
            const err = await res.json();
            alert(err.error || 'Errore avvio routing');
            btn.disabled = false;
            btn.innerHTML = '🛣️ Calcola Routing Reale';
            return;
        }
    } catch(e) {
        alert('Errore di rete: ' + e.message);
        btn.disabled = false;
        btn.innerHTML = '🛣️ Calcola Routing Reale';
        return;
    }

    _routingPollInterval = setInterval(pollRoutingStatus, 1200);
}

async function pollRoutingStatus() {
    try {
        const res = await fetch('/api/route/routing-status');
        const status = await res.json();

        // Update progress bar
        const pct = status.percent || 0;
        document.getElementById('routing-progress-bar').style.width = pct + '%';
        document.getElementById('routing-progress-msg').textContent = status.message || '…';

        if (!status.running && (status.stats || status.error)) {
            clearInterval(_routingPollInterval);
            _routingPollInterval = null;

            const btn = document.getElementById('btn-compute-routing');
            btn.disabled = false;
            btn.innerHTML = '🔄 Ricalcola Routing';

            document.getElementById('routing-progress-wrap').style.display = 'none';

            if (status.error) {
                document.getElementById('routing-progress-msg').textContent = '❌ ' + status.error;
                document.getElementById('routing-progress-wrap').style.display = 'block';
            } else {
                displayRoutingStats(status.stats);
                if (status.compliance) displayComplianceReport(status.compliance);
                if (status.road_check) displayRoadCheckWarnings(status.road_check);
                // Refresh map with new geometries
                if (status.route) {
                    state.routeResult = status.route;
                    displayRoute(status.route);
                }
            }
        }
    } catch(e) {
        console.error('Routing poll error:', e);
    }
}

function displayRoutingStats(stats) {
    if (!stats) return;
    const bar  = document.getElementById('routing-quality-bar');
    const fill = document.getElementById('routing-quality-fill');
    const pct  = document.getElementById('routing-quality-pct');
    const badge = document.getElementById('routing-quality-badge');
    const meta  = document.getElementById('routing-meta');

    bar.style.display  = 'block';
    fill.style.width   = stats.quality_pct + '%';
    pct.textContent    = stats.quality_pct;

    const statusMap = {
        optimal:   { text: 'ottimale',  color: '#22c55e' },
        good:      { text: 'buono',     color: '#86efac' },
        partial:   { text: 'parziale',  color: '#f59e0b' },
        estimated: { text: 'stimato',   color: '#ef4444' },
    };
    const s = statusMap[stats.status] || statusMap.estimated;
    badge.textContent         = s.text;
    badge.style.background    = s.color + '22';
    badge.style.color         = s.color;
    badge.style.borderColor   = s.color + '55';
    fill.style.background     = `linear-gradient(90deg, ${s.color}, ${s.color}bb)`;

    const viols = stats.violations_count > 0
        ? ` · ⚠️ ${stats.violations_count} strade vietate`
        : ' · ✅ nessuna violazione';
    meta.textContent = (
        `${stats.routed_segments} segmenti con routing reale, ` +
        `${stats.estimated_segments} stimati · ` +
        `media ${stats.avg_points_per_segment} punti/segmento${viols}`
    );
}

function displayComplianceReport(compliance) {
    const wrap  = document.getElementById('routing-compliance');
    const title = document.getElementById('routing-compliance-title');
    const list  = document.getElementById('routing-violations-list');

    wrap.style.display = 'block';
    if (compliance.compliant) {
        title.innerHTML = '✅ Percorso conforme al regolamento';
        title.style.color = '#22c55e';
        list.innerHTML = '';
    } else {
        const n = compliance.violations.length;
        title.innerHTML = `⚠️ ${n} possibili strade vietate rilevate`;
        title.style.color = '#f59e0b';
        list.innerHTML = compliance.violations.map(v =>
            `<div class="compliance-violation">
                <span class="cv-day">G${v.day}</span>
                <span class="cv-wps">WP${v.from_wp}→${v.to_wp}</span>
                <span class="cv-road">${v.road}</span>
             </div>`
        ).join('');
    }
}

function displayRoadCheckWarnings(roadCheck) {
    const wrap = document.getElementById('routing-road-warnings');
    if (!roadCheck || !roadCheck.warnings || roadCheck.warnings.length === 0) {
        return;
    }
    wrap.style.display = 'block';
    const sevColor = { alta: '#ef4444', media: '#f59e0b', bassa: '#94a3b8' };
    wrap.innerHTML = `
        <div class="road-check-title">🤖 Condizioni Stradali (Claude AI)</div>
        ${roadCheck.warnings.map(w => `
            <div class="road-warning" style="border-left-color:${sevColor[w.severity]||'#94a3b8'}">
                <div class="rw-header">
                    <span class="rw-area">${w.area}</span>
                    <span class="rw-badge" style="color:${sevColor[w.severity]||'#94a3b8'}">${w.severity?.toUpperCase()}</span>
                    <span class="rw-type">${w.type}</span>
                </div>
                <div class="rw-desc">${w.description}</div>
                ${w.affected_roads?.length ? `<div class="rw-roads">Strade: ${w.affected_roads.join(', ')}</div>` : ''}
            </div>
        `).join('')}
    `;
}

// ── Route Editor ─────────────────────────────────────────────

const DAY_COLORS_EDITOR = ['#6366f1', '#06b6d4', '#f59e0b', '#ef4444'];

const editor = {
    activeDay: null,        // 1-4
    editorLayers: [],       // map layers added in editor mode
    availableMarkers: [],   // green markers for available WPs
    selectedMarkers: [],    // bright markers for current day WPs

    reset() {
        this.activeDay = null;
        this._clearEditorLayers();
        document.querySelectorAll('.day-tab').forEach(t => t.classList.remove('active'));
        document.getElementById('editor-day-info').style.display = 'none';
        document.getElementById('editor-total').textContent = '';
    },

    _clearEditorLayers() {
        this.editorLayers.forEach(l => state.map.removeLayer(l));
        this.editorLayers = [];
        this.availableMarkers = [];
        this.selectedMarkers = [];
    },

    async selectDay(day) {
        this.activeDay = day;
        document.querySelectorAll('.day-tab[data-day]').forEach(t => {
            t.classList.toggle('active', parseInt(t.dataset.day) === day);
        });
        await this.refresh();
    },

    async refresh() {
        try {
            const res = await fetch('/api/route/edit/state');
            if (!res.ok) return;
            const data = await res.json();
            this._renderEditorMap(data);
            this._renderEditorSidebar(data);
            // Update main route display
            state.routeResult = data.route;
        } catch(e) {
            console.error('Editor refresh error:', e);
        }
    },

    _renderEditorMap(data) {
        this._clearEditorLayers();
        if (!this.activeDay) return;

        const route = data.route;
        const dayIdx = this.activeDay - 1;
        const dayColor = DAY_COLORS_EDITOR[dayIdx] || '#ffffff';

        // Dim all base waypoint markers
        state.waypointMarkers.forEach(m => m.setStyle({ opacity: 0.08, fillOpacity: 0.08 }));

        // Draw route polylines for all days (dimmed except active)
        state.routeLayers.forEach(l => state.map.removeLayer(l));
        state.routeLayers = [];
        route.days.forEach((day, idx) => {
            const col = DAY_COLORS_EDITOR[idx];
            const isActive = idx === dayIdx;
            day.segments.forEach(seg => {
                const coords = seg.geometry.map(p => [p[0], p[1]]);
                if (coords.length >= 2) {
                    const pl = L.polyline(coords, {
                        color: isActive ? col : '#555',
                        weight: isActive ? 3 : 1.5,
                        opacity: isActive ? 0.85 : 0.3,
                        dashArray: seg.is_unpaved ? '8 6' : null,
                    }).addTo(state.map);
                    state.routeLayers.push(pl);
                }
            });
        });

        // Active day WPs — bright, numbered, removable
        const activeDay = route.days[dayIdx];
        activeDay.waypoints.forEach((wp, i) => {
            const m = L.circleMarker([wp.lat, wp.lon], {
                radius: 9,
                fillColor: wp.is_golden_point ? '#fbbf24' : dayColor,
                fillOpacity: 0.95,
                color: '#fff',
                weight: 2,
                opacity: 1,
            });
            m.bindTooltip(`${i + 1}`, { permanent: true, className: 'wp-seq-tooltip', direction: 'center' });
            m.bindPopup(`
                <div class="popup-title">WP ${wp.number} ${wp.is_golden_point ? '⭐' : ''}</div>
                <div class="popup-info"><div>📅 Giorno ${this.activeDay} pos. ${i+1}</div>
                <div>📐 ${wp.elevation.toFixed(0)}m</div></div>
                <button class="popup-remove-btn" onclick="editor.removeWP(${wp.id},${this.activeDay})">🗑️ Rimuovi dal Giorno</button>
            `);
            m.addTo(state.map);
            this.editorLayers.push(m);
            this.selectedMarkers.push(m);
        });

        // Available WPs — green, addable
        data.available_waypoints.forEach(wp => {
            const m = L.circleMarker([wp.lat, wp.lon], {
                radius: 6,
                fillColor: '#22c55e',
                fillOpacity: 0.7,
                color: '#fff',
                weight: 1.5,
                opacity: 0.9,
            });
            m.bindPopup(`
                <div class="popup-title" style="color:#22c55e;">+ WP ${wp.number}</div>
                <div class="popup-info">
                    ${wp.description ? `<div>📍 ${wp.description}</div>` : ''}
                    <div>📐 ${wp.elevation.toFixed(0)}m</div>
                </div>
                <button class="popup-add-btn" onclick="editor.addWP(${wp.id},${this.activeDay})">➕ Aggiungi Giorno ${this.activeDay}</button>
            `);
            m.addTo(state.map);
            this.editorLayers.push(m);
            this.availableMarkers.push({ marker: m, wpId: wp.id });
        });
    },

    _renderEditorSidebar(data) {
        const route = data.route;
        const total = data.total_waypoints;
        document.getElementById('editor-total').textContent =
            `Totale percorso: ${total} WP`;

        if (!this.activeDay) return;

        const dayIdx = this.activeDay - 1;
        const day = route.days[dayIdx];
        const dayColor = DAY_COLORS_EDITOR[dayIdx];

        document.getElementById('editor-day-info').style.display = 'block';
        document.getElementById('editor-day-stats').innerHTML = `
            <span style="color:${dayColor}; font-weight:700;">Giorno ${this.activeDay}</span>
            &nbsp;|&nbsp; ${day.waypoint_count} WP
            &nbsp;|&nbsp; ${day.total_km.toFixed(0)} km
            &nbsp;|&nbsp; ${day.total_hours.toFixed(1)}h
        `;

        const listEl = document.getElementById('editor-wp-list');
        listEl.innerHTML = day.waypoints.map((wp, i) => `
            <div class="editor-wp-item">
                <span class="editor-seq" style="background:${dayColor}">${i + 1}</span>
                <span class="editor-wp-name">WP ${wp.number}</span>
                <span class="editor-wp-desc">${wp.city || wp.description?.substring(0,25) || ''}</span>
                <button class="editor-remove-btn" title="Rimuovi" onclick="editor.removeWP(${wp.id},${this.activeDay})">✕</button>
            </div>
        `).join('');
    },

    async addWP(wpId, dayNum) {
        state.map.closePopup();
        try {
            const res = await fetch(`/api/route/edit/day/${dayNum}/add`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ wp_id: wpId }),
            });
            if (!res.ok) { alert('Errore aggiunta WP'); return; }
            const data = await res.json();
            state.routeResult = data.route;
            displayRoute(data.route);
            await this.refresh();
        } catch(e) { console.error(e); }
    },

    async removeWP(wpId, dayNum) {
        state.map.closePopup();
        if (!confirm('Rimuovere questo WP dal giorno?')) return;
        try {
            const res = await fetch(`/api/route/edit/day/${dayNum}/remove`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ wp_id: wpId }),
            });
            if (!res.ok) { alert('Errore rimozione WP'); return; }
            const data = await res.json();
            state.routeResult = data.route;
            displayRoute(data.route);
            await this.refresh();
        } catch(e) { console.error(e); }
    },
};

function selectEditorDay(day) {
    editor.selectDay(day);
}

function exitEditorMode() {
    editor.reset();
    // Restore normal map display
    state.waypointMarkers.forEach(m => m.setStyle({ opacity: 0.4, fillOpacity: 0.8 }));
    if (state.routeResult) displayRoute(state.routeResult);
}

// ── AI Day Optimizer ─────────────────────────────────────────

let _aiPollInterval = null;

async function aiOptimizeDay() {
    const day = editor.activeDay;
    if (!day) { alert('Seleziona prima un giorno dal tab.'); return; }
    if (_aiPollInterval) return;

    const btn = document.getElementById('btn-ai-optimize');
    btn.disabled = true;
    btn.innerHTML = '⏳ AI in elaborazione…';

    const progressWrap = document.getElementById('ai-opt-progress');
    const resultDiv    = document.getElementById('ai-opt-result');
    progressWrap.style.display = 'block';
    resultDiv.style.display    = 'none';
    document.getElementById('ai-progress-fill').style.width = '0%';
    document.getElementById('ai-progress-msg').textContent  = 'Avvio…';

    try {
        const res = await fetch(`/api/route/ai-optimize/day/${day}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        if (!res.ok) {
            const err = await res.json();
            alert(err.error || 'Errore avvio AI');
            _resetAiBtn();
            return;
        }
    } catch(e) {
        alert('Errore di rete: ' + e.message);
        _resetAiBtn();
        return;
    }

    _aiPollInterval = setInterval(_pollAiStatus, 900);
}

async function _pollAiStatus() {
    try {
        const res    = await fetch('/api/route/ai-optimize/status');
        const status = await res.json();

        const pct = status.percent || 0;
        document.getElementById('ai-progress-fill').style.width = pct + '%';
        document.getElementById('ai-progress-msg').textContent  = status.message || '…';

        if (!status.running && (status.result || status.error)) {
            clearInterval(_aiPollInterval);
            _aiPollInterval = null;

            document.getElementById('ai-opt-progress').style.display = 'none';
            _resetAiBtn();

            if (status.error) {
                document.getElementById('ai-opt-result').style.display = 'block';
                document.getElementById('ai-opt-result').innerHTML =
                    `<span class="ai-result-err">❌ ${status.error}</span>`;
            } else if (status.result) {
                _showAiResult(status.result);
                // Refresh map and sidebar
                if (status.result.route) {
                    state.routeResult = status.result.route;
                    displayRoute(status.result.route);
                }
                await editor.refresh();
            }
        }
    } catch(e) {
        console.error('AI poll error:', e);
    }
}

function _showAiResult(result) {
    const div    = document.getElementById('ai-opt-result');
    div.style.display = 'block';

    const method = result.method || '';
    const methodLabel = {
        'claude+2opt': '🤖 Claude AI + 2-opt',
        'claude':      '🤖 Claude AI',
        '2opt':        '⚙️ 2-opt greedy',
        'unchanged':   '↔️ Invariato',
    }[method] || method;

    const impKm  = result.improvement_km  || 0;
    const impPct = result.improvement_pct || 0;
    const impColor = impKm > 1 ? '#22c55e' : impKm > 0 ? '#86efac' : '#94a3b8';

    const b = result.before || {};
    const a = result.after  || {};

    div.innerHTML = `
        <div class="ai-result-header">
            <span class="ai-result-method">${methodLabel}</span>
            <span class="ai-result-delta" style="color:${impColor}">
                ${impKm > 0 ? '-' : ''}${Math.abs(impKm).toFixed(1)} km
                (${impPct.toFixed(1)}%)
            </span>
        </div>
        <div class="ai-result-compare">
            <div class="ai-before">
                <div class="ai-cmp-label">Prima</div>
                <div class="ai-cmp-val">${b.km?.toFixed(0) ?? '?'} km</div>
                <div class="ai-cmp-sub">${b.elev_gain?.toFixed(0) ?? '?'}m +</div>
            </div>
            <div class="ai-arrow">→</div>
            <div class="ai-after">
                <div class="ai-cmp-label">Dopo</div>
                <div class="ai-cmp-val" style="color:${impColor}">${a.km?.toFixed(0) ?? '?'} km</div>
                <div class="ai-cmp-sub">${a.elev_gain?.toFixed(0) ?? '?'}m +</div>
            </div>
        </div>
        ${result.reasoning ? `<div class="ai-reasoning">"${result.reasoning}"</div>` : ''}
    `;
}

function _resetAiBtn() {
    const btn = document.getElementById('btn-ai-optimize');
    if (btn) {
        btn.disabled = false;
        btn.innerHTML = '🤖 Ottimizza Giorno con AI';
    }
}

// ── Unpaved Mode ────────────────────────────────────────────

function setUnpavedMode(mode) {
    state.unpavedMode = mode;
    // Toggle active button
    document.querySelectorAll('.toggle-btn[data-unpaved]').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.unpaved === mode);
    });
    // Show/hide max unpaved slider
    const sliderGroup = document.getElementById('max-unpaved-group');
    if (sliderGroup) {
        sliderGroup.style.display = mode === 'limit' ? 'block' : 'none';
    }
}

// ── Controls ────────────────────────────────────────────────

function initControls() {
    document.getElementById('btn-set-finish').addEventListener('click', enableSetFinish);
    document.getElementById('btn-set-coords').addEventListener('click', setFinishFromInputs);
    document.getElementById('btn-optimize').addEventListener('click', startOptimization);
    document.getElementById('btn-export').addEventListener('click', downloadGPX);

    // Sidebar toggle (mobile)
    const toggleBtn = document.getElementById('sidebar-toggle');
    if (toggleBtn) {
        toggleBtn.addEventListener('click', () => {
            document.querySelector('.sidebar').classList.toggle('open');
        });
    }
}

// ── Help Modal ───────────────────────────────────────────────

function openHelp() {
    const modal = document.getElementById('help-modal');
    if (modal) {
        modal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    }
}

function closeHelp() {
    const modal = document.getElementById('help-modal');
    if (modal) {
        modal.style.display = 'none';
        document.body.style.overflow = '';
    }
}

function closeHelpBackdrop(event) {
    if (event.target === document.getElementById('help-modal')) {
        closeHelp();
    }
}

function switchHelpTab(tabId, btn) {
    // Hide all sections
    document.querySelectorAll('.help-section').forEach(s => s.classList.remove('active'));
    // Deactivate all tabs
    document.querySelectorAll('.help-tab').forEach(t => t.classList.remove('active'));
    // Show selected section
    const section = document.getElementById('htab-' + tabId);
    if (section) section.classList.add('active');
    // Activate clicked tab
    if (btn) btn.classList.add('active');
    // Scroll content to top
    const body = document.querySelector('.help-body');
    if (body) body.scrollTop = 0;
}

// Close help modal on Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const modal = document.getElementById('help-modal');
        if (modal && modal.style.display !== 'none') {
            closeHelp();
        }
    }
});
