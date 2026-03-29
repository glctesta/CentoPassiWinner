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
AVERAGE_SPEED_KMH = 46          # Realistic average speed on mountain roads
MIN_SPEED_KMH = 45
MAX_SPEED_KMH = 55
BREAK_TIME_HOURS = 1.5          # 90 minutes total breaks per day (eat, drink, fuel)
# Per-day max km: (driving_hours - breaks) * avg_speed
# Day 1: (13.25 - 1.5) * 46 = 540.5 km
# Day 2: (16.25 - 1.5) * 46 = 678.5 km
# Day 3: (16.25 - 1.5) * 46 = 678.5 km
# Day 4: (12.50 - 1.5) * 46 = 506.0 km
MAX_KM_PER_DAY_LIST = [(h - BREAK_TIME_HOURS) * AVERAGE_SPEED_KMH for h in DRIVING_HOURS]
MIN_TOTAL_KM = 1600             # Minimum total route (art. 1.1)
MAX_START_DISTANCE_KM = 450     # Max start distance from finish (art. 6.2)

# ── Day-to-Day Bridging ────────────────────────────────────
BRIDGE_MIN_KM = 20              # Min road distance between last WP day N and first WP day N+1
BRIDGE_MAX_KM = 40              # Max road distance between last WP day N and first WP day N+1

# ── Alternative (optional) Waypoints ──────────────────────
NUM_ALTERNATIVES = 10           # Total backup WPs to select after optimization
# Distribution: triangular weight = day_num (inversely proportional to remaining days)
# For 4 days with 10 alts: 1 + 2 + 3 + 4 = 10  →  G1:1, G2:2, G3:3, G4:4
# Formula: alt_per_day[d] = round(NUM_ALTERNATIVES * d / triangular(n_days))
# where triangular(n) = n*(n+1)/2

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
FLASK_PORT = 5000
FLASK_DEBUG = True

# ── Road Intelligence (Claude API) ─────────────────────────
ANTHROPIC_API_KEY_ENV = 'ANTHROPIC_API_KEY'
ROAD_INTELLIGENCE_MODEL = 'claude-sonnet-4-20250514'
ROAD_INTELLIGENCE_MAX_TOKENS = 1024
ROAD_INTELLIGENCE_ENABLED = True  # Can be toggled off

# ── GPX File ────────────────────────────────────────────────
GPX_FILE = "Generale 2026.GPX"
