"""
Centopassi Route Planner - Flask Web Application
"""
import os
import json
import threading
from flask import Flask, render_template, jsonify, request, send_file
from gpx_parser import parse_gpx, get_waypoint_stats, get_waypoints_by_region
from xlsx_parser import parse_xlsx, get_xlsx_sheet_names
from routing_service import RoutingService
from route_optimizer import RouteOptimizer, generate_gpx_export
from config import GPX_FILE, FLASK_HOST, FLASK_PORT, FLASK_DEBUG

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
    max_unpaved = int(data.get('max_unpaved', 10))
    
    _optimization_status = {
        'running': True, 'message': 'Avvio...', 'percent': 0,
        'result': None, 'error': None
    }
    
    def run_optimization():
        global _optimization_status
        try:
            wps = get_waypoints()
            if not wps:
                raise ValueError("Nessun waypoint caricato. Caricare prima un file GPX o XLSX.")
            
            routing = RoutingService(use_osrm=use_osrm)
            optimizer = RouteOptimizer(wps, routing)
            
            def progress_cb(message, percent):
                _optimization_status['message'] = message
                _optimization_status['percent'] = percent
            
            optimizer.set_progress_callback(progress_cb)
            route = optimizer.optimize(finish_lat, finish_lon, finish_name,
                                       use_osrm_routing=use_osrm,
                                       unpaved_mode=unpaved_mode,
                                       max_unpaved=max_unpaved)
            
            _optimization_status['result'] = route.to_dict()
            _optimization_status['message'] = 'Completato!'
            _optimization_status['percent'] = 100
            
            # Save GPX
            export_path = os.path.join(os.path.dirname(__file__), 'percorso_centopassi.gpx')
            generate_gpx_export(route, export_path)
            
        except Exception as e:
            _optimization_status['error'] = str(e)
            _optimization_status['message'] = f'Errore: {e}'
        finally:
            _optimization_status['running'] = False
    
    thread = threading.Thread(target=run_optimization, daemon=True)
    thread.start()
    
    return jsonify({'status': 'started'})


@app.route('/api/optimize/status')
def api_optimize_status():
    """Get current optimization status."""
    return jsonify(_optimization_status)


@app.route('/api/optimize/reset', methods=['POST'])
def api_optimize_reset():
    """Force-reset a stuck optimization."""
    global _optimization_status
    _optimization_status = {
        'running': False, 'message': 'Reset manuale', 'percent': 0,
        'result': None, 'error': None
    }
    return jsonify({'status': 'reset'})


@app.route('/api/export')
def api_export():
    """Download the generated GPX file."""
    filepath = os.path.join(os.path.dirname(__file__), 'percorso_centopassi.gpx')
    if not os.path.exists(filepath):
        return jsonify({'error': 'Nessun percorso generato'}), 404
    return send_file(filepath, as_attachment=True, download_name='percorso_centopassi_2026.gpx')


if __name__ == '__main__':
    get_waypoints()  # Preload
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)
