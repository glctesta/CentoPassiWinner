"""
Centopassi Route Planner - Configuration
Based on official CENTOPASSI® 2026 rules.
"""

# ── Event Dates ──────────────────────────────────────────────
EVENT_START_DATE = "2026-05-29"
EVENT_END_DATE = "2026-06-01"
TOTAL_DRIVING_DAYS = 4  # 29, 30, 31 May + 1 June (half day)

# ── Daily Time Windows ──────────────────────────────────────
DAY1_START_TIME = "05:00"       # Tutti i giorni: partenza alle 05:00
OTHER_DAYS_START_TIME = "05:00" # Days 2-4: early start
REST_START_TIME = "21:15"       # Sosta obbligatoria (art. 4.3)
REST_END_TIME = "05:00"         # Giorno successivo
FINISH_WINDOW_START = "17:15"   # Finestra arrivo ultimo giorno (art. 4.7)
FINISH_WINDOW_END = "17:45"     # Finestra arrivo ultimo giorno

# ── Driving Hours per Day ────────────────────────────────────
# Day 1: 05:00 → 21:15 = 16.25 hours
# Day 2: 05:00 → 21:15 = 16.25 hours
# Day 3: 05:00 → 21:15 = 16.25 hours
# Day 4: 05:00 → 17:30 = 12.5 hours (arrivo 17:15-17:45)
DRIVING_HOURS = [16.25, 16.25, 16.25, 12.5]

# ── Speed & Distance ────────────────────────────────────────
# REGOLA 6.2: penalità 1pt/km/h per ogni km/h di velocità MEDIA giornaliera oltre il limite
MAX_ALLOWED_AVG_SPEED_KMH = 47  # Limite regolamentare: velocità media giornaliera max
AVERAGE_SPEED_KMH = 48          # Velocità media di pianificazione (reale su strade italiane)
MAX_ROAD_SPEED_KMH = 120        # Limite di velocità di punta su strade statali
BREAK_TIME_HOURS = 1.5          # 90 minuti di pause giornaliere (rifornimento, pasti, ecc.)

# ── Hard cap giornaliero: max 650 km/giorno (regola operativa pilota) ──
MAX_KM_DAY_HARD_CAP = 650       # Mai superare questo valore per nessun giorno

# Per-day max km pianificati: min(650, (ore_guida - pause) * velocità_media)
# Day 1: min(650, (16.25 - 1.5) * 48) = min(650, 708) = 650 km
# Day 2: min(650, (16.25 - 1.5) * 48) = min(650, 708) = 650 km
# Day 3: min(650, (16.25 - 1.5) * 48) = min(650, 708) = 650 km
# Day 4: min(650, (12.50 - 1.5) * 48) = min(650, 528) = 528 km
MAX_KM_PER_DAY_LIST = [
    min(MAX_KM_DAY_HARD_CAP, (h - BREAK_TIME_HOURS) * AVERAGE_SPEED_KMH)
    for h in DRIVING_HOURS
]

# Hard cap regolamentare: km × ore ≤ MAX_ALLOWED_AVG_SPEED_KMH
MAX_KM_HARD_LIMIT_PER_DAY = [MAX_ALLOWED_AVG_SPEED_KMH * h for h in DRIVING_HOURS]
MIN_TOTAL_KM = 1600             # Minimum total route (art. 1.1)
MAX_START_DISTANCE_KM = 450     # Max start distance from finish (art. 6.2)

# ── Day-to-Day Bridging ────────────────────────────────────
BRIDGE_MIN_KM = 20              # Min road distance between last WP day N and first WP day N+1
BRIDGE_MAX_KM = 40              # Max road distance between last WP day N and first WP day N+1

# ── Alternative (optional) Waypoints ──────────────────────
NUM_ALTERNATIVES = 10           # WP di riserva totali
# Distribuzione: concentrata negli ultimi 2 giorni (più utile quando si è in gara)
# G1: 0,  G2: 0,  G3: 5,  G4: 5
ALTERNATIVES_PER_DAY = [0, 0, 5, 5]  # Indice 0=G1, 1=G2, 2=G3, 3=G4

# ── Waypoint Rules ──────────────────────────────────────────
TARGET_WAYPOINTS = 100           # Obiettivo: 100 passi
WP_VALIDATION_RADIUS_M = 300    # Passo validato entro 300m di diametro (raggio 150m)
GP_VALIDATION_RADIUS_M = 200    # Golden Point validato entro 200m di diametro (raggio 100m)
MIN_START_DISTANCE_KM = 15      # Partenza a >15 km dal primo passo (art. 6.2)
BONUS_100TH_DISTANCE_KM = 50    # 100° passo entro 50 km (aria) dal traguardo per bonus

# ── Unpaved / Sterrato ──────────────────────────────────────
UNPAVED_MIN_OFFROAD_M = 500     # Tratto sterrato rilevante se off-road ≥ 500m
                                 # o distante da strada asfaltata
# Modalità default: limita gli sterrati a massimo 5 (0 è ideale)
DEFAULT_UNPAVED_MODE = "limit"
DEFAULT_MAX_UNPAVED = 5

# ── Scoring ─────────────────────────────────────────────────
POINTS_PER_PASS = 5000           # 5000 pts per regular pass
POINTS_PER_GOLDEN = 15000        # 15000 pts per Golden Point (only at 100th pass reached)
POINTS_PER_GOLDEN_NO_100 = 5000  # GP without 100 passes = same as regular
POINTS_PER_METER_ELEVATION = 1   # 1 pt per meter of elevation (up + down)
BONUS_100TH_PASS = 100000        # 100K bonus at 100th pass if within 50km from finish

# ── Penalties ───────────────────────────────────────────────
PENALTY_PER_KM_UNDER_MIN = 100   # 100 pts per km under 1600
PENALTY_EARLY_LATE_FINISH = 100000  # Arrive before 17:15 or after 17:45

# ── Road Type Classification ───────────────────────────────
# Roads that are FORBIDDEN (esclusione dalla classifica)
FORBIDDEN_ROAD_KEYWORDS = [
    'autostrada', 'motorway', 'highway',
    'tangenziale', 'ring road', 'bypass',
    'circonvallazione',
    'scorrimento veloce', 'SSV',
    'grande comunicazione', 'SGC',
]

# Roads that indicate UNPAVED/DIRT (to be highlighted)
UNPAVED_ROAD_KEYWORDS = [
    'bridleway', 'footpath', 'track', 'dirt road',
    'dirt', 'sterrato', 'sentiero', 'mulattiera',
    'path', 'trail',
]

# ── OSRM Routing ────────────────────────────────────────────
OSRM_BASE_URL = "https://router.project-osrm.org"
OSRM_PROFILE = "car"
OSRM_EXCLUDE = "motorway"  # Exclude motorways from routing (OSRM param)
OSRM_RATE_LIMIT_SEC = 1.1  # Seconds between requests (demo server)
HAVERSINE_ROAD_FACTOR = 1.35  # Multiply haversine by this for road estimate

# ── GraphHopper Routing (optional, richer avoid rules) ──────
# Free tier: 500 req/day. Set GRAPHHOPPER_API_KEY in .env to enable.
import os as _os
GRAPHHOPPER_API_KEY  = _os.environ.get('GRAPHHOPPER_API_KEY', '')
GRAPHHOPPER_BASE_URL = 'https://graphhopper.com/api/1'
GRAPHHOPPER_PROFILE  = 'car'
GRAPHHOPPER_AVOID    = ['motorway', 'toll']  # Maps to Italian autostrade + pedaggi

# ── Flask ───────────────────────────────────────────────────
FLASK_HOST = "0.0.0.0"
FLASK_PORT = int(_os.environ.get('PORT', 5000))
FLASK_DEBUG = _os.environ.get('FLASK_ENV', 'development') != 'production'

# ── Road Intelligence (Claude API) ─────────────────────────
ANTHROPIC_API_KEY_ENV = 'ANTHROPIC_API_KEY'
ROAD_INTELLIGENCE_MODEL = 'claude-sonnet-4-20250514'
ROAD_INTELLIGENCE_MAX_TOKENS = 1024
ROAD_INTELLIGENCE_ENABLED = True  # Can be toggled off

# ── GPX File ────────────────────────────────────────────────
GPX_FILE = "Generale 2026.GPX"
