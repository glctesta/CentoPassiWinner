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

function initMap() {
    state.map = L.map('map', {
        center: [41.5, 13.0],
        zoom: 6,
        zoomControl: true,
        attributionControl: true,
    });

    // Dark tile layer
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>',
        subdomains: 'abcd',
        maxZoom: 19,
    }).addTo(state.map);

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
