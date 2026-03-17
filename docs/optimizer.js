// ═══════════════════════════════════════════════════════════
// Centopassi Route Optimizer — Client-Side JavaScript Port
// ═══════════════════════════════════════════════════════════

const CONFIG = {
    TARGET_WAYPOINTS: 100,
    MAX_KM_PER_DAY: 600,
    MIN_TOTAL_KM: 1600,
    MAX_START_DISTANCE_KM: 450,
    MIN_START_DISTANCE_KM: 15,
    DRIVING_HOURS: [13.25, 16.25, 16.25, 12.5],
    DAY1_START_TIME: '08:00',
    OTHER_DAYS_START_TIME: '05:00',
    FINISH_WINDOW_START: '17:15',
    REST_START_TIME: '21:15',
    AVERAGE_SPEED_KMH: 50,
    HAVERSINE_ROAD_FACTOR: 1.35,
    BONUS_100TH_DISTANCE_KM: 50,
    POINTS_PER_PASS: 5000,
    POINTS_PER_GOLDEN: 15000,
    POINTS_PER_GOLDEN_NO_100: 5000,
    BONUS_100TH_PASS: 100000,
};

function haversineKm(lat1, lon1, lat2, lon2) {
    const R = 6371.0;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2)**2 + Math.cos(lat1*Math.PI/180)*Math.cos(lat2*Math.PI/180)*Math.sin(dLon/2)**2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}

function estimateDistance(wp1, wp2) {
    return haversineKm(wp1.lat, wp1.lon, wp2.lat, wp2.lon) * CONFIG.HAVERSINE_ROAD_FACTOR;
}

// ── OSRM Routing ────────────────────────────────────────────

const OSRM_BASE = 'https://router.project-osrm.org';
let _osrmCache = {};

async function queryOSRM(wp1, wp2) {
    const key = `${wp1.lat.toFixed(5)},${wp1.lon.toFixed(5)}|${wp2.lat.toFixed(5)},${wp2.lon.toFixed(5)}`;
    if (_osrmCache[key]) return _osrmCache[key];
    try {
        const url = `${OSRM_BASE}/route/v1/car/${wp1.lon},${wp1.lat};${wp2.lon},${wp2.lat}?overview=full&geometries=geojson`;
        const res = await fetch(url);
        const data = await res.json();
        if (data.code === 'Ok' && data.routes && data.routes.length) {
            const r = data.routes[0];
            const geom = (r.geometry?.coordinates || []).map(c => [c[1], c[0]]);
            const result = { distanceKm: r.distance / 1000, durationHours: r.duration / 3600, geometry: geom, source: 'osrm' };
            _osrmCache[key] = result;
            return result;
        } else { console.warn('OSRM response:', data); }
    } catch(e) { console.warn('OSRM error:', e); }
    // Fallback to estimate
    const d = estimateDistance(wp1, wp2);
    return { distanceKm: d, durationHours: d / CONFIG.AVERAGE_SPEED_KMH, geometry: [[wp1.lat,wp1.lon],[wp2.lat,wp2.lon]], source: 'estimate' };
}

async function recalcDayOSRM(day, progressCb, label) {
    day.segments = []; day.totalKm = 0; day.totalHours = 0; day.elevGain = 0; day.elevLoss = 0; day.unpavedSegs = 0;
    for (let i = 0; i < day.waypoints.length - 1; i++) {
        const w1 = day.waypoints[i], w2 = day.waypoints[i+1];
        // Rate limit: 1.1s between requests
        await new Promise(r => setTimeout(r, 1100));
        if (progressCb) progressCb(`${label}: OSRM ${i+1}/${day.waypoints.length-1}`, -1);
        const route = await queryOSRM(w1, w2);
        day.segments.push({fromWp:w1, toWp:w2, distanceKm:route.distanceKm, durationHours:route.durationHours, isUnpaved:w2.is_unpaved, geometry:route.geometry});
        day.totalKm += route.distanceKm; day.totalHours += route.durationHours;
        if (w2.is_unpaved) day.unpavedSegs++;
        const diff = (w2.elevation||0) - (w1.elevation||0);
        if (diff > 0) day.elevGain += diff; else day.elevLoss += Math.abs(diff);
    }
}

// Fetch real road geometry for an entire day (1 OSRM request with all WPs)
async function fetchDayGeometry(day, progressCb, label) {
    if (day.waypoints.length < 2) return;
    const coords = day.waypoints.map(wp => `${wp.lon},${wp.lat}`).join(';');
    try {
        if (progressCb) progressCb(`${label}: caricamento percorso reale...`, -1);
        const url = `${OSRM_BASE}/route/v1/car/${coords}?overview=full&geometries=geojson&steps=true`;
        console.log(`[OSRM] ${label}: fetching ${day.waypoints.length} waypoints...`);
        const res = await fetch(url);
        if (!res.ok) { console.warn(`[OSRM] ${label}: HTTP ${res.status}`); return; }
        const data = await res.json();
        console.log(`[OSRM] ${label}: response code=${data.code}, routes=${data.routes?.length}`);
        if (data.code === 'Ok' && data.routes && data.routes.length) {
            const route = data.routes[0];
            const legs = route.legs || [];
            console.log(`[OSRM] ${label}: ${legs.length} legs, ${day.segments.length} segments`);
            // Update each segment with real geometry and distance from OSRM legs
            for (let i = 0; i < Math.min(legs.length, day.segments.length); i++) {
                const leg = legs[i];
                // Collect geometry from leg steps
                let legGeom = [];
                if (leg.steps) {
                    leg.steps.forEach(step => {
                        if (step.geometry && step.geometry.coordinates) {
                            step.geometry.coordinates.forEach(c => legGeom.push([c[1], c[0]]));
                        }
                    });
                }
                if (legGeom.length >= 2) {
                    day.segments[i].geometry = legGeom;
                }
                // Update distance and duration from OSRM
                day.segments[i].distanceKm = leg.distance / 1000;
                day.segments[i].durationHours = leg.duration / 3600;
            }
            // Recalculate totals
            day.totalKm = day.segments.reduce((s, seg) => s + seg.distanceKm, 0);
            day.totalHours = day.segments.reduce((s, seg) => s + seg.durationHours, 0);
            console.log(`[OSRM] ${label}: updated ${legs.length} segments, total=${day.totalKm.toFixed(0)} km`);
        } else {
            console.warn(`[OSRM] ${label}: failed - code=${data.code}, message=${data.message}`);
        }
    } catch(e) { console.error('[OSRM] multi-point error for ' + label + ':', e); }
}

async function optimizeRouteAsync(allWaypoints, finishLat, finishLon, finishName, unpavedMode, maxUnpaved, useOSRM, progressCb) {
    // Phase 1: build route using haversine estimates (fast)
    const route = optimizeRoute(allWaypoints, finishLat, finishLon, finishName, unpavedMode, maxUnpaved, progressCb);
    if (!route) return route;

    // Phase 2: ALWAYS fetch real road geometries for display (4 requests, fast)
    progressCb('Caricamento percorso stradale reale...', 93);
    for (let i = 0; i < route.days.length; i++) {
        await fetchDayGeometry(route.days[i], progressCb, `Giorno ${i+1}`);
        // Rate limit between day requests
        if (i < route.days.length - 1) await new Promise(r => setTimeout(r, 1200));
    }

    // Phase 3: if OSRM checkbox is on, also do segment-by-segment recalculation
    // (for even more accurate distances — slower)
    if (useOSRM) {
        progressCb('Ricalcolo distanze OSRM segmento per segmento...', 95);
        for (let i = 0; i < route.days.length; i++) {
            await recalcDayOSRM(route.days[i], progressCb, `Giorno ${i+1}`);
        }
    }

    enforceDailyKmLimit(route);
    progressCb(`Percorso completato: ${route.totalKm.toFixed(0)} km`, 100);
    return route;
}

// ── Main Optimizer ──────────────────────────────────────────

function optimizeRoute(allWaypoints, finishLat, finishLon, finishName, unpavedMode, maxUnpaved, progressCb) {
    progressCb = progressCb || (() => {});
    unpavedMode = unpavedMode || 'limit';
    maxUnpaved = maxUnpaved || 10;

    progressCb('Ricerca punti di partenza...', 5);
    const startCandidates = findStartCandidates(allWaypoints, finishLat, finishLon);
    if (startCandidates.length === 0) throw new Error('Nessun punto di partenza valido trovato.');

    const tries = selectDiverseStarts(startCandidates, finishLat, finishLon, 5);
    let bestRoute = null, bestScore = -Infinity;

    for (let t = 0; t < tries.length; t++) {
        const pct = 10 + Math.round(70 * t / tries.length);
        progressCb(`Tentativo ${t+1}/${tries.length}: partenza WP ${tries[t].number}`, pct);
        try {
            const route = buildRoute(allWaypoints, tries[t], finishLat, finishLon, finishName, unpavedMode, maxUnpaved);
            if (route && route.totalWaypoints >= CONFIG.TARGET_WAYPOINTS && route.totalKm >= CONFIG.MIN_TOTAL_KM) {
                const score = evaluateRoute(route);
                if (score > bestScore) { bestScore = score; bestRoute = route; }
            }
        } catch(e) { console.warn('Try failed:', e); }
    }

    if (!bestRoute) throw new Error('Impossibile costruire un percorso valido.');

    progressCb('Ottimizzazione 2-opt...', 85);
    optimize2opt(bestRoute);
    enforceDailyKmLimit(bestRoute);

    progressCb('Selezione WP alternativi...', 92);
    selectAlternatives(bestRoute, allWaypoints, 10);

    progressCb(`Percorso: ${bestRoute.totalKm.toFixed(0)} km, ${bestRoute.totalWaypoints} WP, ${bestRoute.totalGP} GP`, 100);
    return bestRoute;
}

// ── Start Candidates ────────────────────────────────────────

function findStartCandidates(wps, fLat, fLon) {
    return wps.filter(wp => {
        const d = haversineKm(wp.lat, wp.lon, fLat, fLon);
        return d >= CONFIG.MIN_START_DISTANCE_KM && d <= CONFIG.MAX_START_DISTANCE_KM;
    });
}

function selectDiverseStarts(candidates, fLat, fLon, maxTries) {
    if (candidates.length <= maxTries) return candidates;
    const quads = {NE:[], NW:[], SE:[], SW:[]};
    candidates.forEach(wp => {
        const q = (wp.lat > fLat ? 'N':'S') + (wp.lon > fLon ? 'E':'W');
        quads[q].push(wp);
    });
    const sel = [];
    Object.values(quads).forEach(arr => {
        if (arr.length) {
            arr.sort((a,b) => haversineKm(b.lat,b.lon,fLat,fLon) - haversineKm(a.lat,a.lon,fLat,fLon));
            sel.push(arr[0]);
            if (arr.length > 1) sel.push(arr[Math.floor(arr.length/2)]);
        }
    });
    return sel.slice(0, maxTries);
}

// ── Build Route ─────────────────────────────────────────────

function buildRoute(allWps, startWp, fLat, fLon, fName, unpavedMode, maxUnpaved) {
    const route = {
        days: [], startPoint: {lat:startWp.lat, lon:startWp.lon, name:`Partenza WP ${startWp.number}`},
        finishPoint: {lat:fLat, lon:fLon, name:fName}, alternatives: [],
        get totalKm() { return this.days.reduce((s,d)=>s+d.totalKm,0); },
        get totalWaypoints() { return this.days.reduce((s,d)=>s+d.waypoints.length,0); },
        get totalGP() { return this.days.reduce((s,d)=>s+d.waypoints.filter(w=>w.is_golden_point).length,0); },
        get allWaypoints() { return this.days.flatMap(d=>d.waypoints); },
        get totalElevGain() { return this.days.reduce((s,d)=>s+d.elevGain,0); },
        get totalElevLoss() { return this.days.reduce((s,d)=>s+d.elevLoss,0); },
    };

    const available = new Set(allWps.map(w=>w.id));
    let currentWp = startWp;
    let totalSelected = 0, unpavedCount = 0;

    for (let dayNum = 1; dayNum <= 4; dayNum++) {
        const day = { dayNumber: dayNum, waypoints:[], segments:[], totalKm:0, totalHours:0, elevGain:0, elevLoss:0, unpavedSegs:0,
            startTime: dayNum===1 ? CONFIG.DAY1_START_TIME : CONFIG.OTHER_DAYS_START_TIME,
            endTime: dayNum===4 ? CONFIG.FINISH_WINDOW_START : CONFIG.REST_START_TIME };
        const maxHours = CONFIG.DRIVING_HOURS[dayNum-1];
        const maxKm = Math.min(CONFIG.MAX_KM_PER_DAY, maxHours * CONFIG.AVERAGE_SPEED_KMH);
        let dayKm = 0, dayHours = 0;

        if (dayNum === 1) { day.waypoints.push(startWp); available.delete(startWp.id); }

        while (dayKm < maxKm * 0.92 && dayHours < maxHours * 0.92 &&
               totalSelected + day.waypoints.length < CONFIG.TARGET_WAYPOINTS && available.size > 0) {
            const progressKm = dayKm / Math.max(maxKm, 1);
            const progressWp = day.waypoints.length / Math.max(CONFIG.TARGET_WAYPOINTS / 4, 1);
            const needMoreKm = progressWp > progressKm + 0.15;

            const next = findNextWaypoint(currentWp, available, allWps, fLat, fLon, dayKm, maxKm,
                dayNum, totalSelected + day.waypoints.length, unpavedMode, maxUnpaved, unpavedCount, needMoreKm);
            if (!next) break;

            const segKm = estimateDistance(currentWp, next);
            const segHours = segKm / CONFIG.AVERAGE_SPEED_KMH;
            if (dayKm + segKm > maxKm || dayHours + segHours > maxHours) { if (day.waypoints.length) break; }

            day.segments.push({ fromWp: currentWp, toWp: next, distanceKm: segKm, durationHours: segHours,
                isUnpaved: next.is_unpaved, geometry: [[currentWp.lat,currentWp.lon],[next.lat,next.lon]] });
            day.waypoints.push(next);
            dayKm += segKm; dayHours += segHours;
            if (next.is_unpaved) { unpavedCount++; day.unpavedSegs++; }

            const prevElev = currentWp.elevation || 0, nextElev = next.elevation || 0;
            const diff = nextElev - prevElev;
            if (diff > 0) day.elevGain += diff; else day.elevLoss += Math.abs(diff);

            currentWp = next; available.delete(next.id);
        }
        day.totalKm = dayKm; day.totalHours = dayHours;
        totalSelected += day.waypoints.length;
        route.days.push(day);
    }
    return totalSelected >= CONFIG.TARGET_WAYPOINTS ? route : null;
}

// ── Find Next Waypoint ──────────────────────────────────────

function findNextWaypoint(current, available, allWps, fLat, fLon, dayKm, maxKm, dayNum, totalSel, unpavedMode, maxUnpaved, unpavedCount, needMoreKm) {
    const progress = totalSel / CONFIG.TARGET_WAYPOINTS;
    const distToFinish = haversineKm(current.lat, current.lon, fLat, fLon);
    const candidates = [];

    for (const wp of allWps) {
        if (!available.has(wp.id)) continue;
        if (unpavedMode === 'exclude' && wp.is_unpaved) continue;
        if (unpavedMode === 'limit' && wp.is_unpaved && unpavedCount >= maxUnpaved) continue;
        const d = haversineKm(current.lat, current.lon, wp.lat, wp.lon);
        if (d < 200) candidates.push([wp, d]);
    }
    candidates.sort((a,b) => a[1] - b[1]);
    const maxEval = Math.min(candidates.length, Math.max(20, 50 - Math.floor(progress * 30)));

    let bestWp = null, bestScore = -Infinity;
    for (let i = 0; i < maxEval; i++) {
        const [wp, dist] = candidates[i];
        const roadDist = dist * CONFIG.HAVERSINE_ROAD_FACTOR;
        if (dayKm + roadDist > maxKm) continue;

        let proximity = 100 / (1 + roadDist);
        const wpDistFinish = haversineKm(wp.lat, wp.lon, fLat, fLon);
        let direction = 0;
        if (progress < 0.3) direction = (wpDistFinish - distToFinish) * 0.1;
        else if (progress < 0.7) direction = (distToFinish - wpDistFinish) * 0.05;
        else { direction = (distToFinish - wpDistFinish) * 0.3;
            if (totalSel >= 95 && wpDistFinish <= CONFIG.BONUS_100TH_DISTANCE_KM) direction += 50; }

        const gpBonus = wp.is_golden_point ? 5 : 0;
        let unpavedPenalty = 0;
        if (wp.is_unpaved) {
            if (unpavedMode === 'limit') unpavedPenalty = -10 * (1 + (unpavedCount/Math.max(maxUnpaved,1)) * 3);
            else unpavedPenalty = -3;
        }
        const idealDist = needMoreKm ? 25 : 18;
        const spread = -Math.abs(roadDist - idealDist) * 0.1;
        if (needMoreKm) proximity *= 0.5;

        const score = proximity + direction + gpBonus + unpavedPenalty + spread;
        if (score > bestScore) { bestScore = score; bestWp = wp; }
    }
    return bestWp;
}

// ── 2-Opt ───────────────────────────────────────────────────

function optimize2opt(route) {
    route.days.forEach(day => {
        if (day.waypoints.length < 5) return;
        let improved = true, iters = 0;
        while (improved && iters < 100) {
            improved = false; iters++;
            const wps = day.waypoints, n = wps.length;
            for (let i = 1; i < n-2; i++) {
                for (let j = i+2; j < n-1; j++) {
                    const dCur = haversineKm(wps[i].lat,wps[i].lon,wps[i+1].lat,wps[i+1].lon) +
                                 haversineKm(wps[j].lat,wps[j].lon,wps[j+1].lat,wps[j+1].lon);
                    const dNew = haversineKm(wps[i].lat,wps[i].lon,wps[j].lat,wps[j].lon) +
                                 haversineKm(wps[i+1].lat,wps[i+1].lon,wps[j+1].lat,wps[j+1].lon);
                    if (dNew < dCur) { day.waypoints = [...wps.slice(0,i+1), ...wps.slice(i+1,j+1).reverse(), ...wps.slice(j+1)]; improved = true; }
                }
            }
        }
        recalcDay(day);
    });
}

function recalcDay(day) {
    day.segments = []; day.totalKm = 0; day.totalHours = 0; day.elevGain = 0; day.elevLoss = 0; day.unpavedSegs = 0;
    for (let i = 0; i < day.waypoints.length - 1; i++) {
        const w1 = day.waypoints[i], w2 = day.waypoints[i+1];
        const d = estimateDistance(w1, w2), h = d / CONFIG.AVERAGE_SPEED_KMH;
        day.segments.push({fromWp:w1, toWp:w2, distanceKm:d, durationHours:h, isUnpaved:w2.is_unpaved, geometry:[[w1.lat,w1.lon],[w2.lat,w2.lon]]});
        day.totalKm += d; day.totalHours += h;
        if (w2.is_unpaved) day.unpavedSegs++;
        const diff = (w2.elevation||0) - (w1.elevation||0);
        if (diff > 0) day.elevGain += diff; else day.elevLoss += Math.abs(diff);
    }
}

function enforceDailyKmLimit(route) {
    for (let i = 0; i < route.days.length - 1; i++) {
        const day = route.days[i], next = route.days[i+1];
        while (day.totalKm > CONFIG.MAX_KM_PER_DAY && day.waypoints.length > 3) {
            next.waypoints.unshift(day.waypoints.pop());
            recalcDay(day); recalcDay(next);
        }
    }
}

// ── Alternatives ────────────────────────────────────────────

function selectAlternatives(route, allWps, num) {
    const selectedIds = new Set(route.allWaypoints.map(w=>w.id));
    const candidates = [];
    for (const wp of allWps) {
        if (selectedIds.has(wp.id)) continue;
        let minDist = Infinity, nearDay = 1;
        for (const day of route.days) {
            for (const rwp of day.waypoints) {
                const d = haversineKm(wp.lat,wp.lon,rwp.lat,rwp.lon);
                if (d < minDist) { minDist = d; nearDay = day.dayNumber; }
            }
        }
        candidates.push({wp, dist:minDist, nearDay});
    }
    candidates.sort((a,b) => a.dist - b.dist);
    // Ascending distribution: more alternatives on later days
    // Day 1: 1, Day 2: 2, Day 3: 3, Day 4: 4 = 10 total
    const maxPerDay = {1: 1, 2: 2, 3: 3, 4: 4};
    const alts = [], dayCounts = {1:0,2:0,3:0,4:0};
    for (const c of candidates) {
        if (alts.length >= num) break;
        if ((dayCounts[c.nearDay]||0) < (maxPerDay[c.nearDay]||1)) {
            c.wp.altDay = c.nearDay;
            alts.push(c.wp); dayCounts[c.nearDay] = (dayCounts[c.nearDay]||0)+1;
        }
    }
    if (alts.length < num) {
        const altIds = new Set(alts.map(w=>w.id));
        for (const c of candidates) { if (alts.length >= num) break; if (!altIds.has(c.wp.id)) { c.wp.altDay = c.nearDay; alts.push(c.wp); } }
    }
    route.alternatives = alts;
}

// ── Scoring ─────────────────────────────────────────────────

function evaluateRoute(route) {
    const reached100 = route.totalWaypoints >= CONFIG.TARGET_WAYPOINTS;
    const gpCount = route.totalGP;
    const regularCount = route.totalWaypoints - gpCount;
    const passPoints = regularCount * CONFIG.POINTS_PER_PASS;
    const gpPoints = gpCount * (reached100 ? CONFIG.POINTS_PER_GOLDEN : CONFIG.POINTS_PER_GOLDEN_NO_100);
    const elevPoints = Math.floor(route.totalElevGain + route.totalElevLoss);
    let bonus = 0;
    if (reached100) {
        const allWps = route.allWaypoints;
        const last = allWps[allWps.length-1];
        if (last && haversineKm(last.lat,last.lon,route.finishPoint.lat,route.finishPoint.lon) <= CONFIG.BONUS_100TH_DISTANCE_KM) bonus = CONFIG.BONUS_100TH_PASS;
    }
    return passPoints + gpPoints + elevPoints + bonus;
}

function calculateScore(route) {
    const reached100 = route.totalWaypoints >= CONFIG.TARGET_WAYPOINTS;
    const gpCount = route.totalGP;
    const regularCount = route.totalWaypoints - gpCount;
    const passPoints = regularCount * CONFIG.POINTS_PER_PASS;
    const gpPoints = gpCount * (reached100 ? CONFIG.POINTS_PER_GOLDEN : CONFIG.POINTS_PER_GOLDEN_NO_100);
    const elevPoints = Math.floor(route.totalElevGain + route.totalElevLoss);
    let bonus = 0;
    if (reached100) {
        const allWps = route.allWaypoints;
        const last = allWps[allWps.length-1];
        if (last && haversineKm(last.lat,last.lon,route.finishPoint.lat,route.finishPoint.lon) <= CONFIG.BONUS_100TH_DISTANCE_KM) bonus = CONFIG.BONUS_100TH_PASS;
    }
    return { passPoints, gpPoints: gpPoints, elevPoints, bonus, total: passPoints+gpPoints+elevPoints+bonus,
        reached100, regularCount, gpCount };
}

// ── GPX Export ──────────────────────────────────────────────

function generateGPX(route) {
    let gpx = `<?xml version="1.0" encoding="UTF-8"?>\n<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1" creator="Centopassi Route Planner">\n`;
    gpx += `<metadata><name>Centopassi 2026 - Percorso Ottimizzato</name><time>${new Date().toISOString()}</time></metadata>\n`;
    route.days.forEach(day => {
        day.waypoints.forEach(wp => {
            const sym = wp.is_golden_point ? 'Flag, Red' : (wp.is_unpaved ? 'Flag, Orange' : 'Flag, Blue');
            gpx += `<wpt lat="${wp.lat}" lon="${wp.lon}"><ele>${wp.elevation||0}</ele><name>WP ${wp.number}${wp.is_golden_point?' GP':''}</name><desc>Giorno ${day.dayNumber} - ${wp.description||''}</desc><sym>${sym}</sym></wpt>\n`;
        });
    });
    (route.alternatives||[]).forEach(wp => {
        gpx += `<wpt lat="${wp.lat}" lon="${wp.lon}"><ele>${wp.elevation||0}</ele><name>ALT ${wp.number}</name><desc>ALTERNATIVO Giorno ${wp.altDay||'?'}</desc><sym>Flag, Green</sym></wpt>\n`;
    });
    route.days.forEach(day => {
        gpx += `<trk><name>Giorno ${day.dayNumber} (${day.totalKm.toFixed(0)} km)</name><trkseg>\n`;
        day.waypoints.forEach(wp => { gpx += `<trkpt lat="${wp.lat}" lon="${wp.lon}"></trkpt>\n`; });
        gpx += `</trkseg></trk>\n`;
    });
    gpx += `</gpx>`;
    return gpx;
}

function downloadGPXFile(route) {
    const blob = new Blob([generateGPX(route)], {type:'application/gpx+xml'});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'percorso_centopassi_2026.gpx';
    a.click();
}
