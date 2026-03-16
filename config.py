"""
Centopassi Route Planner - Configuration
Based on official CENTOPASSI® 2026 rules.
"""

# ── Event Dates ──────────────────────────────────────────────
EVENT_START_DATE = "2026-05-29"
EVENT_END_DATE = "2026-06-01"
TOTAL_DRIVING_DAYS = 4  # 29, 30, 31 May + 1 June (half day)

# ── Daily Time Windows ──────────────────────────────────────
DAY1_START_TIME = "08:00"      # First day: later start
OTHER_DAYS_START_TIME = "05:00" # Days 2-4: early start
REST_START_TIME = "21:15"       # Mandatory stop (art. 4.3)
REST_END_TIME = "05:00"         # Next day
FINISH_WINDOW_START = "17:15"   # Last day arrival window (art. 4.7)
FINISH_WINDOW_END = "17:45"     # Last day arrival window

# ── Driving Hours per Day ────────────────────────────────────
# Day 1: 08:00 → 21:15 = 13.25 hours
# Day 2: 05:00 → 21:15 = 16.25 hours
# Day 3: 05:00 → 21:15 = 16.25 hours
# Day 4: 05:00 → 17:30 = 12.5 hours (finish ~17:15-17:45)
DRIVING_HOURS = [13.25, 16.25, 16.25, 12.5]

# ── Speed & Distance ────────────────────────────────────────
AVERAGE_SPEED_KMH = 50          # Range 45-55 km/h
MIN_SPEED_KMH = 45
MAX_SPEED_KMH = 55
MAX_KM_PER_DAY = 600            # Soft limit per day
MIN_TOTAL_KM = 1600             # Minimum total route (art. 1.1)
MAX_START_DISTANCE_KM = 450     # Max start distance from finish (art. 6.2)

# ── Waypoint Rules ──────────────────────────────────────────
TARGET_WAYPOINTS = 100           # Must reach exactly 100 passes
WP_VALIDATION_RADIUS_M = 300    # Pass validated within 300m diameter (150m radius)
GP_VALIDATION_RADIUS_M = 200    # Golden Point validated within 200m diameter (100m radius)
MIN_START_DISTANCE_KM = 15      # Start must be >15 km from first pass (art. 6.2)
BONUS_100TH_DISTANCE_KM = 50    # 100th pass must be within 50 km (air) from finish for bonus

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
OSRM_EXCLUDE = "motorway"  # Exclude motorways from routing
OSRM_RATE_LIMIT_SEC = 1.1  # Seconds between requests (demo server)
HAVERSINE_ROAD_FACTOR = 1.35  # Multiply haversine by this for road estimate

# ── Flask ───────────────────────────────────────────────────
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000
FLASK_DEBUG = True

# ── GPX File ────────────────────────────────────────────────
GPX_FILE = "Generale 2026.GPX"
