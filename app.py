"""
Centopassi Route Planner - Flask Web Application
"""
import os
import io
import json
import zipfile
import threading
from flask import Flask, render_template, jsonify, request, send_file
from gpx_parser import parse_gpx, get_waypoint_stats, get_waypoints_by_region
from xlsx_parser import parse_xlsx, get_xlsx_sheet_names
from routing_service import RoutingService
from route_optimizer import RouteOptimizer, generate_gpx_export, generate_gpx_day
from models import haversine_km
from config import GPX_FILE, FLASK_HOST, FLASK_PORT, FLASK_DEBUG

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB max upload

# ── Upload folder ─────────────────────────────────────────────
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ── Global State ──────────────────────────────────────────────
_waypoints = None
_stats = None
_regions = None
_loaded_filename = None  # Track which file is loaded
_optimization_status = {'running': False, 'message': '', 'percent': 0, 'result': None, 'error': None}
_current_optimizer = None      # Reference to running optimizer (for cancellation)

# Route editor state (populated after optimization)
_current_route = None          # Route object for live editing
_available_wp_ids = set()      # WP IDs not yet assigned to any day

OPTIMIZATION_TIMEOUT_SEC = 300  # 5 minutes max

# Road routing computation status (async, separate from optimization)
_routing_status = {
    'running': False, 'message': '', 'percent': 0,
    'error': None, 'stats': None, 'compliance': None,
}

# AI Day Optimizer status (per-day, async)
_ai_opt_status = {
    'running': False, 'day_num': None, 'message': '',
    'percent': 0, 'result': None, 'error': None,
}


def get_waypoints():
    """Lazy-load and cache waypoints from default GPX."""
    global _waypoints, _stats, _regions, _loaded_filename
    if _waypoints is None:
        gpx_path = os.path.join(os.path.dirname(__file__), GPX_FILE)
        if os.path.exists(gpx_path):
            _waypoints = parse_gpx(gpx_path)
            _loaded_filename = GPX_FILE
            _stats = get_waypoint_stats(_waypoints)
            _regions = get_waypoints_by_region(_waypoints)
            print(f"[App] Loaded {len(_waypoints)} waypoints from {GPX_FILE}")
        else:
            _waypoints = []
            _stats = {'total': 0, 'golden_points': 0, 'unpaved': 0,
                       'road_types': {}, 'bounds': {}, 'elevation': {'min': 0, 'max': 0, 'avg': 0}}
            _regions = {}
            _loaded_filename = None
            print("[App] Nessun file GPX predefinito trovato")
    return _waypoints


def _reload_waypoints(waypoints: list, filename: str):
    """Replace cached waypoints with new data."""
    global _waypoints, _stats, _regions, _loaded_filename
    _waypoints = waypoints
    _loaded_filename = filename
    _stats = get_waypoint_stats(waypoints)
    _regions = get_waypoints_by_region(waypoints)
    print(f"[App] Waypoints aggiornati: {len(waypoints)} da {filename}")


# ── Routes ────────────────────────────────────────────────────

@app.route('/')
def index():
    """Main page with map."""
    return render_template('index.html')


@app.route('/api/waypoints')
def api_waypoints():
    """Return all waypoints as JSON."""
    wps = get_waypoints()
    return jsonify({
        'waypoints': [wp.to_dict() for wp in wps],
        'stats': _stats,
        'loaded_file': _loaded_filename,
    })


@app.route('/api/waypoints/regions')
def api_regions():
    """Return waypoints grouped by region."""
    get_waypoints()
    result = {}
    for region, wps in _regions.items():
        result[region] = {
            'count': len(wps),
            'golden_points': sum(1 for wp in wps if wp.is_golden_point),
            'waypoints': [wp.to_dict() for wp in wps],
        }
    return jsonify(result)


@app.route('/api/upload', methods=['POST'])
def api_upload():
    """Upload GPX or XLSX file to load waypoints."""
    if 'file' not in request.files:
        return jsonify({'error': 'Nessun file caricato'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Nessun file selezionato'}), 400
    
    filename = file.filename
    ext = os.path.splitext(filename)[1].lower()
    
    if ext not in ('.gpx', '.xlsx', '.xls'):
        return jsonify({'error': 'Formato non supportato. Usare .gpx o .xlsx'}), 400
    
    # Save to upload folder
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)
    
    try:
        if ext == '.gpx':
            waypoints = parse_gpx(filepath)
        elif ext in ('.xlsx', '.xls'):
            sheet_name = request.form.get('sheet_name', None)
            waypoints = parse_xlsx(filepath, sheet_name)
        else:
            return jsonify({'error': 'Formato non supportato'}), 400
        
        if not waypoints:
            return jsonify({'error': 'Nessun waypoint trovato nel file'}), 400
        
        _reload_waypoints(waypoints, filename)
        
        stats = get_waypoint_stats(waypoints)
        return jsonify({
            'status': 'ok',
            'filename': filename,
            'count': len(waypoints),
            'stats': stats,
            'message': f'Caricati {len(waypoints)} waypoints da {filename}',
        })
    
    except Exception as e:
        return jsonify({'error': f'Errore nel parsing: {str(e)}'}), 400


@app.route('/api/upload/sheets', methods=['POST'])
def api_upload_sheets():
    """Preview sheet names from an XLSX file (to let user pick)."""
    if 'file' not in request.files:
        return jsonify({'error': 'Nessun file caricato'}), 400
    
    file = request.files['file']
    ext = os.path.splitext(file.filename)[1].lower()
    
    if ext not in ('.xlsx', '.xls'):
        return jsonify({'sheets': []})
    
    filepath = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(filepath)
    
    sheets = get_xlsx_sheet_names(filepath)
    return jsonify({'sheets': sheets, 'filename': file.filename})


@app.route('/api/optimize', methods=['POST'])
def api_optimize():
    """Start route optimization (async)."""
    global _optimization_status
    
    if _optimization_status['running']:
        return jsonify({'error': 'Ottimizzazione già in corso'}), 409
    
    data = request.json
    if not data or 'finish_lat' not in data or 'finish_lon' not in data:
        return jsonify({'error': 'Specificare finish_lat e finish_lon'}), 400
    
    finish_lat = float(data['finish_lat'])
    finish_lon = float(data['finish_lon'])
    finish_name = data.get('finish_name', 'Traguardo')
    use_osrm = data.get('use_osrm', False)
    unpaved_mode = data.get('unpaved_mode', 'limit')
    max_unpaved = int(data.get('max_unpaved', 5))  # default 5 (era 10)
    check_roads = data.get('check_road_closures', False)

    # Punto di partenza personalizzato (opzionale)
    start_lat = float(data['start_lat']) if 'start_lat' in data and data['start_lat'] is not None else None
    start_lon = float(data['start_lon']) if 'start_lon' in data and data['start_lon'] is not None else None
    start_name = data.get('start_name', None)
    
    _optimization_status = {
        'running': True, 'message': 'Avvio...', 'percent': 0,
        'result': None, 'error': None
    }
    
    def run_optimization():
        global _optimization_status, _current_route, _available_wp_ids, _current_optimizer
        try:
            wps = get_waypoints()
            if not wps:
                raise ValueError("Nessun waypoint caricato. Caricare prima un file GPX o XLSX.")

            routing = RoutingService(use_osrm=use_osrm)
            optimizer = RouteOptimizer(wps, routing)
            _current_optimizer = optimizer

            if check_roads:
                from road_intelligence import RoadIntelligence
                ri = RoadIntelligence()
                if ri.enabled:
                    optimizer.road_intelligence = ri

            def progress_cb(message, percent):
                print(f"[PROGRESS] {percent}% — {message}", flush=True)
                _optimization_status['message'] = message
                _optimization_status['percent'] = percent

            optimizer.set_progress_callback(progress_cb)
            route = optimizer.optimize(finish_lat, finish_lon, finish_name,
                                       use_osrm_routing=use_osrm,
                                       unpaved_mode=unpaved_mode,
                                       max_unpaved=max_unpaved,
                                       start_lat=start_lat,
                                       start_lon=start_lon,
                                       start_name=start_name)

            _optimization_status['result'] = route.to_dict()
            _optimization_status['message'] = 'Completato!'
            _optimization_status['percent'] = 100

            # Store route object for live editing
            _current_route = route
            selected_ids = {wp.id for wp in route.all_waypoints}
            _available_wp_ids = {wp.id for wp in wps if wp.id not in selected_ids}

            # Save GPX (legacy single-file)
            export_path = os.path.join(os.path.dirname(__file__), 'percorso_centopassi.gpx')
            generate_gpx_export(route, export_path)

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[OPTIMIZE ERROR] {e}", flush=True)
            _optimization_status['error'] = str(e)
            _optimization_status['message'] = f'Errore: {e}'
            _optimization_status['percent'] = -1  # Signal error to frontend
        finally:
            _optimization_status['running'] = False
            _current_optimizer = None
            print(f"[OPTIMIZE DONE] status={_optimization_status}", flush=True)

    thread = threading.Thread(target=run_optimization, daemon=True)
    thread.start()

    # Watchdog: cancel optimizer if it exceeds timeout
    def watchdog():
        thread.join(timeout=OPTIMIZATION_TIMEOUT_SEC)
        if thread.is_alive() and _current_optimizer:
            _current_optimizer.cancel()

    threading.Thread(target=watchdog, daemon=True).start()

    return jsonify({'status': 'started'})


@app.route('/api/optimize/status')
def api_optimize_status():
    """Get current optimization status (lightweight — no route data)."""
    return jsonify({
        'running': _optimization_status['running'],
        'message': _optimization_status['message'],
        'percent': _optimization_status['percent'],
        'error':   _optimization_status.get('error'),
        'has_result': _optimization_status.get('result') is not None,
    })


@app.route('/api/optimize/result')
def api_optimize_result():
    """Get full optimization result (called once after completion)."""
    result = _optimization_status.get('result')
    if result:
        return jsonify(result)
    # Log what's in status to help diagnose 404s
    print(f"[RESULT 404] running={_optimization_status.get('running')}, "
          f"error={_optimization_status.get('error')}, "
          f"result_is_none={result is None}", flush=True)
    return jsonify({'error': 'Nessun risultato disponibile. '
                    'Probabilmente l\'ottimizzazione è ancora in corso o è fallita.'}), 404


@app.route('/api/optimize/reset', methods=['POST'])
def api_optimize_reset():
    """Force-reset a stuck optimization."""
    global _optimization_status, _current_optimizer
    if _current_optimizer:
        _current_optimizer.cancel()
        _current_optimizer = None
    _optimization_status = {
        'running': False, 'message': 'Reset manuale', 'percent': 0,
        'result': None, 'error': None
    }
    return jsonify({'status': 'reset'})


@app.route('/api/road-check', methods=['POST'])
def api_road_check():
    """Check road closures for given regions via Claude AI."""
    from road_intelligence import RoadIntelligence
    data = request.json or {}
    regions = data.get('regions', [])
    ri = RoadIntelligence()
    if not ri.enabled:
        return jsonify({'error': 'ANTHROPIC_API_KEY non configurata', 'warnings': []})
    warnings = ri.check_road_closures(regions)
    return jsonify({'warnings': warnings})


@app.route('/api/export')
def api_export():
    """Download the generated GPX file (legacy single file)."""
    filepath = os.path.join(os.path.dirname(__file__), 'percorso_centopassi.gpx')
    if not os.path.exists(filepath):
        return jsonify({'error': 'Nessun percorso generato'}), 404
    return send_file(filepath, as_attachment=True, download_name='percorso_centopassi_2026.gpx')


# ── Route Editor Endpoints ─────────────────────────────────────

@app.route('/api/route/edit/state')
def api_edit_state():
    """Return current editable route state: days + available WPs."""
    global _current_route, _available_wp_ids
    if not _current_route:
        return jsonify({'error': 'Nessun percorso ottimizzato disponibile'}), 404

    wps = get_waypoints()
    available = [wps[i].to_dict() for i in sorted(_available_wp_ids) if i < len(wps)]

    return jsonify({
        'route': _current_route.to_dict(),
        'available_waypoints': available,
        'total_waypoints': _current_route.total_waypoints,
    })


@app.route('/api/route/edit/day/<int:day_num>/add', methods=['POST'])
def api_edit_add(day_num):
    """Add a waypoint to a day at the optimal position."""
    global _current_route, _available_wp_ids
    if not _current_route:
        return jsonify({'error': 'Nessun percorso disponibile'}), 404

    data = request.json or {}
    wp_id = data.get('wp_id')
    if wp_id is None:
        return jsonify({'error': 'wp_id mancante'}), 400

    wps = get_waypoints()
    if wp_id not in _available_wp_ids or wp_id >= len(wps):
        return jsonify({'error': 'WP non disponibile'}), 400

    day_idx = day_num - 1
    if day_idx < 0 or day_idx >= len(_current_route.days):
        return jsonify({'error': f'Giorno {day_num} non esiste'}), 400

    new_wp = wps[wp_id]
    day = _current_route.days[day_idx]

    # Find optimal insertion position (minimizes detour)
    if not day.waypoints:
        day.waypoints.insert(0, new_wp)
    else:
        best_pos = 1
        best_cost = float('inf')
        for i in range(len(day.waypoints) + 1):
            if i == 0:
                cost = haversine_km(new_wp.lat, new_wp.lon,
                                    day.waypoints[0].lat, day.waypoints[0].lon)
            elif i == len(day.waypoints):
                cost = haversine_km(day.waypoints[-1].lat, day.waypoints[-1].lon,
                                    new_wp.lat, new_wp.lon)
            else:
                prev_wp = day.waypoints[i - 1]
                next_wp = day.waypoints[i]
                old_dist = haversine_km(prev_wp.lat, prev_wp.lon, next_wp.lat, next_wp.lon)
                new_dist = (haversine_km(prev_wp.lat, prev_wp.lon, new_wp.lat, new_wp.lon) +
                            haversine_km(new_wp.lat, new_wp.lon, next_wp.lat, next_wp.lon))
                cost = new_dist - old_dist
            if cost < best_cost:
                best_cost = cost
                best_pos = i
        day.waypoints.insert(best_pos, new_wp)

    # Recalculate day
    from routing_service import RoutingService
    routing = RoutingService(use_osrm=False)
    _recalculate_day_simple(day, routing)
    _available_wp_ids.discard(wp_id)

    return jsonify({
        'status': 'ok',
        'route': _current_route.to_dict(),
        'total_waypoints': _current_route.total_waypoints,
    })


@app.route('/api/route/edit/day/<int:day_num>/remove', methods=['POST'])
def api_edit_remove(day_num):
    """Remove a waypoint from a day and return it to the available pool."""
    global _current_route, _available_wp_ids
    if not _current_route:
        return jsonify({'error': 'Nessun percorso disponibile'}), 404

    data = request.json or {}
    wp_id = data.get('wp_id')
    if wp_id is None:
        return jsonify({'error': 'wp_id mancante'}), 400

    day_idx = day_num - 1
    if day_idx < 0 or day_idx >= len(_current_route.days):
        return jsonify({'error': f'Giorno {day_num} non esiste'}), 400

    day = _current_route.days[day_idx]
    wp_to_remove = next((wp for wp in day.waypoints if wp.id == wp_id), None)
    if not wp_to_remove:
        return jsonify({'error': 'WP non trovato nel giorno'}), 400

    day.waypoints = [wp for wp in day.waypoints if wp.id != wp_id]
    _available_wp_ids.add(wp_id)

    from routing_service import RoutingService
    routing = RoutingService(use_osrm=False)
    _recalculate_day_simple(day, routing)

    return jsonify({
        'status': 'ok',
        'route': _current_route.to_dict(),
        'total_waypoints': _current_route.total_waypoints,
        'available_wp': wp_to_remove.to_dict(),
    })


def _recalculate_day_simple(day, routing):
    """
    Recalculate day segments after a WP add/remove.
    Uses routing.get_route() so real OSRM geometry is fetched when available.
    """
    from models import RouteSegment
    from config import AVERAGE_SPEED_KMH
    day.segments = []
    day.total_km = 0.0
    day.total_hours = 0.0
    day.total_elevation_gain = 0.0
    day.total_elevation_loss = 0.0
    day.unpaved_segments = 0
    day.unpaved_km = 0.0

    for i in range(len(day.waypoints) - 1):
        wp1 = day.waypoints[i]
        wp2 = day.waypoints[i + 1]
        result = routing.get_route(wp1, wp2)
        dist  = result['distance_km']
        hours = result['duration_hours']
        geo   = result['geometry']
        seg = RouteSegment(
            from_wp=wp1, to_wp=wp2,
            distance_km=dist, duration_hours=hours,
            is_unpaved=wp2.is_unpaved,
            geometry=geo,
            air_distance_km=haversine_km(wp1.lat, wp1.lon, wp2.lat, wp2.lon),
            road_distance_km=dist,
            routing_source=result.get('source', 'estimate'),
            road_violations=result.get('road_violations', []),
            step_names=result.get('step_names', []),
        )
        day.segments.append(seg)
        day.total_km += dist
        day.total_hours += hours
        elev = wp2.elevation - wp1.elevation
        if elev > 0:
            day.total_elevation_gain += elev
        else:
            day.total_elevation_loss += abs(elev)
        if seg.is_unpaved:
            day.unpaved_segments += 1
            day.unpaved_km += dist


# ── GPX Export per day ─────────────────────────────────────────

@app.route('/api/export/day/<int:day_num>')
def api_export_day(day_num):
    """Download GPX for a single day."""
    global _current_route
    if not _current_route:
        return jsonify({'error': 'Nessun percorso generato'}), 404
    if day_num < 1 or day_num > len(_current_route.days):
        return jsonify({'error': f'Giorno {day_num} non esiste'}), 400

    gpx_bytes = generate_gpx_day(_current_route, day_num)
    buf = io.BytesIO(gpx_bytes)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name=f'centopassi_2026_G{day_num}.gpx',
        mimetype='application/gpx+xml',
    )


@app.route('/api/export/all')
def api_export_all():
    """Download all days as a ZIP archive of separate GPX files."""
    global _current_route
    if not _current_route:
        return jsonify({'error': 'Nessun percorso generato'}), 404

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for day_num in range(1, len(_current_route.days) + 1):
            gpx_bytes = generate_gpx_day(_current_route, day_num)
            zf.writestr(f'centopassi_2026_G{day_num}.gpx', gpx_bytes)
    zip_buf.seek(0)
    return send_file(
        zip_buf,
        as_attachment=True,
        download_name='centopassi_2026_tutti.zip',
        mimetype='application/zip',
    )


# ── Professional Road Routing Endpoints ───────────────────────

@app.route('/api/route/full-routing', methods=['POST'])
def api_full_routing():
    """
    Trigger async full road routing for current route.
    Uses OSRM (overview=full) / GraphHopper to compute real road tracks.
    Optionally runs Claude AI road condition check.
    """
    global _routing_status, _current_route
    if not _current_route:
        return jsonify({'error': 'Nessun percorso disponibile. Esegui prima l\'ottimizzazione.'}), 404
    if _routing_status.get('running'):
        return jsonify({'error': 'Routing già in esecuzione'}), 409

    data = request.json or {}
    use_osrm       = data.get('use_osrm', True)
    claude_check   = data.get('claude_check', False)

    _routing_status = {
        'running': True, 'message': 'Avvio routing professionale…',
        'percent': 0, 'error': None, 'stats': None, 'compliance': None,
    }

    def run_routing():
        global _routing_status, _current_route
        try:
            from road_routing import RoadRouter

            def progress_cb(msg, pct):
                _routing_status['message'] = msg
                _routing_status['percent'] = pct

            routing = RoutingService(use_osrm=use_osrm)
            router  = RoadRouter(routing, progress_cb=progress_cb)

            stats      = router.compute_full_routing(_current_route)
            compliance = router.validate_compliance(_current_route)

            road_check_result = None
            if claude_check:
                _routing_status['message'] = 'Verifica condizioni stradali (Claude AI)…'
                road_check_result = router.claude_road_check(_current_route)

            # Regenerate legacy GPX with new geometry
            export_path = os.path.join(os.path.dirname(__file__), 'percorso_centopassi.gpx')
            generate_gpx_export(_current_route, export_path)

            _routing_status.update({
                'running':    False,
                'message':    'Routing completato!',
                'percent':    100,
                'stats':      stats,
                'compliance': compliance,
                'road_check': road_check_result,
                'route':      _current_route.to_dict(),
            })

        except Exception as e:
            import traceback
            _routing_status.update({
                'running': False,
                'message': f'Errore routing: {e}',
                'error':   str(e),
            })
            print(traceback.format_exc())

    threading.Thread(target=run_routing, daemon=True).start()
    return jsonify({'status': 'started'})


@app.route('/api/route/routing-status')
def api_routing_status():
    """Poll routing computation progress."""
    return jsonify(_routing_status)


@app.route('/api/route/routing-stats')
def api_routing_stats():
    """Return current routing quality stats (synchronous)."""
    global _current_route
    if not _current_route:
        return jsonify({'error': 'Nessun percorso disponibile'}), 404
    from road_routing import RoadRouter
    router = RoadRouter(RoutingService(use_osrm=False))
    stats  = router.get_routing_stats(_current_route)
    return jsonify(stats)


# ── AI Day Optimizer Endpoints ────────────────────────────────

@app.route('/api/route/ai-optimize/day/<int:day_num>', methods=['POST'])
def api_ai_optimize_day(day_num: int):
    """
    Trigger async AI optimization for one day.
    Uses Claude to reorder the day's waypoints for the best route.
    """
    global _ai_opt_status, _current_route
    if not _current_route:
        return jsonify({'error': 'Nessun percorso disponibile'}), 404
    if _ai_opt_status.get('running'):
        return jsonify({'error': 'Ottimizzazione AI già in corso'}), 409

    _ai_opt_status = {
        'running': True, 'day_num': day_num,
        'message': f'Avvio ottimizzazione AI Giorno {day_num}…',
        'percent': 0, 'result': None, 'error': None,
    }

    def run_ai_opt():
        global _ai_opt_status, _current_route
        try:
            from ai_day_optimizer import AIDayOptimizer

            def progress_cb(msg, pct):
                _ai_opt_status['message'] = msg
                _ai_opt_status['percent'] = pct

            optimizer = AIDayOptimizer(progress_cb=progress_cb)
            result = optimizer.optimize_day(_current_route, day_num)

            # Regenerate full GPX with new order
            if result.get('success') and result.get('method') != 'unchanged':
                export_path = os.path.join(
                    os.path.dirname(__file__), 'percorso_centopassi.gpx'
                )
                generate_gpx_export(_current_route, export_path)

            _ai_opt_status.update({
                'running': False,
                'message': (
                    f"Completato! Risparmio: {result.get('improvement_km', 0):.1f} km "
                    f"({result.get('improvement_pct', 0):.1f}%)"
                ),
                'percent': 100,
                'result':  {**result, 'route': _current_route.to_dict()},
            })

        except Exception as e:
            import traceback
            _ai_opt_status.update({
                'running': False,
                'message': f'Errore: {e}',
                'error':   str(e),
            })
            print(traceback.format_exc())

    threading.Thread(target=run_ai_opt, daemon=True).start()
    return jsonify({'status': 'started', 'day_num': day_num})


@app.route('/api/route/ai-optimize/status')
def api_ai_optimize_status():
    """Poll AI optimization progress."""
    return jsonify(_ai_opt_status)


@app.route('/api/route/ai-optimize/reset', methods=['POST'])
def api_ai_optimize_reset():
    """Force-reset a stuck AI optimization."""
    global _ai_opt_status
    _ai_opt_status = {
        'running': False, 'day_num': None, 'message': 'Reset',
        'percent': 0, 'result': None, 'error': None,
    }
    return jsonify({'status': 'reset'})


if __name__ == '__main__':
    get_waypoints()  # Preload
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
