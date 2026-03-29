"""
Centopassi Route Planner - Professional Routing Service
Provides real road routing via OSRM (primary) and GraphHopper (optional),
with compliance validation against contest rules (no motorways, tangenziali, SSV/SGC).
"""
import requests
import time
import json
import os
from typing import Optional
from models import Waypoint, haversine_km
from config import (
    OSRM_BASE_URL, OSRM_PROFILE, OSRM_EXCLUDE,
    OSRM_RATE_LIMIT_SEC, HAVERSINE_ROAD_FACTOR, AVERAGE_SPEED_KMH,
    FORBIDDEN_ROAD_KEYWORDS, GRAPHHOPPER_API_KEY, GRAPHHOPPER_BASE_URL,
    GRAPHHOPPER_PROFILE, GRAPHHOPPER_AVOID,
)

# Road names that trigger compliance warnings (Italian context)
_FORBIDDEN_NAME_PATTERNS = [kw.lower() for kw in FORBIDDEN_ROAD_KEYWORDS]


def _geometry_from_geojson(coordinates: list) -> list:
    """Convert GeoJSON [lon,lat] coordinates → [[lat,lon], ...] for GPX."""
    return [[c[1], c[0]] for c in coordinates if len(c) >= 2]


def _check_road_compliance(steps: list) -> list[str]:
    """
    Inspect OSRM/GraphHopper step names for forbidden road types.
    Returns list of offending road names (empty = compliant).
    """
    violations = []
    for step in steps:
        name = (step.get('name') or step.get('street_name') or '').lower()
        ref  = (step.get('ref')  or '').lower()
        combined = f"{name} {ref}"
        for kw in _FORBIDDEN_NAME_PATTERNS:
            if kw in combined:
                label = step.get('name') or step.get('ref') or kw
                if label not in violations:
                    violations.append(label)
                break
    return violations


class GraphHopperService:
    """
    Optional routing via GraphHopper API.
    Supports richer avoid options (motorway + toll) and Italian road names.
    Requires GRAPHHOPPER_API_KEY in .env  — free tier: 500 req/day.
    """

    _BASE = "https://graphhopper.com/api/1/route"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def get_route(self, wp1: Waypoint, wp2: Waypoint) -> Optional[dict]:
        """Return routing dict compatible with RoutingService.get_route()."""
        try:
            payload = {
                "points": [[wp1.lon, wp1.lat], [wp2.lon, wp2.lat]],
                "profile": GRAPHHOPPER_PROFILE,
                "locale": "it",
                "instructions": True,       # needed for compliance check
                "calc_points": True,
                "points_encoded": False,    # GeoJSON geometry
                "avoid": GRAPHHOPPER_AVOID, # ["motorway","toll"]
            }
            resp = requests.post(
                f"{self._BASE}?key={self.api_key}",
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("paths"):
                return None

            path = data["paths"][0]
            dist_km  = path["distance"] / 1000.0
            dur_hrs  = path["time"] / 3_600_000.0  # ms → hours
            coords   = path["points"]["coordinates"]
            geometry = _geometry_from_geojson(coords)

            # Extract step names for compliance check
            steps = []
            for instr in path.get("instructions", []):
                steps.append({
                    "name": instr.get("street_name", ""),
                    "ref":  instr.get("street_ref", ""),
                })
            violations = _check_road_compliance(steps)

            return {
                "distance_km":    round(dist_km, 2),
                "duration_hours": round(dur_hrs, 3),
                "geometry":       geometry,
                "source":         "graphhopper",
                "road_violations": violations,
                "step_names":     [s["name"] for s in steps if s["name"]],
            }
        except Exception as e:
            print(f"[GraphHopper] Error: {e}")
            return None


class RoutingService:
    """
    Professional routing client.
    Provider chain:  GraphHopper (if key set) → OSRM → haversine estimate.
    Uses overview=full for complete road-following GPX tracks.
    """

    def __init__(self, use_osrm: bool = True,
                 cache_file: str = "route_cache.json"):
        self.use_osrm     = use_osrm
        self.cache_file   = cache_file
        self.cache        = {}
        self._last_req_t  = 0.0
        self._req_count   = 0

        # Optional GraphHopper provider
        self._gh: Optional[GraphHopperService] = None
        if GRAPHHOPPER_API_KEY:
            self._gh = GraphHopperService(GRAPHHOPPER_API_KEY)
            print("[RoutingService] GraphHopper enabled (primary provider)")
        else:
            print("[RoutingService] Using OSRM (GraphHopper key not set)")

        self._load_cache()

    # ── Cache ─────────────────────────────────────────────────

    def _load_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    self.cache = json.load(f)
                print(f"[RoutingService] Loaded {len(self.cache)} cached routes")
            except (json.JSONDecodeError, IOError):
                self.cache = {}

    def _save_cache(self):
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f)
        except IOError:
            pass

    def _cache_key(self, lat1, lon1, lat2, lon2) -> str:
        return f"{lat1:.6f},{lon1:.6f}|{lat2:.6f},{lon2:.6f}"

    # ── Rate limiting (OSRM public server) ────────────────────

    def _rate_limit(self):
        elapsed = time.time() - self._last_req_t
        if elapsed < OSRM_RATE_LIMIT_SEC:
            time.sleep(OSRM_RATE_LIMIT_SEC - elapsed)
        self._last_req_t = time.time()

    # ── Public API ────────────────────────────────────────────

    def get_route(self, wp1: Waypoint, wp2: Waypoint) -> dict:
        """
        Get complete road route between two waypoints.

        Returns:
            distance_km     : float   – road distance
            duration_hours  : float   – estimated driving time
            geometry        : list    – [[lat,lon], ...] full polyline
            source          : str     – 'graphhopper'|'osrm'|'cache'|'estimate'
            road_violations : list    – forbidden road names found (may be empty)
        """
        key = self._cache_key(wp1.lat, wp1.lon, wp2.lat, wp2.lon)

        if key in self.cache:
            r = dict(self.cache[key])
            r['source'] = 'cache'
            return r

        result = None

        # 1. GraphHopper (if available)
        if self._gh and self.use_osrm:
            result = self._gh.get_route(wp1, wp2)

        # 2. OSRM
        if result is None and self.use_osrm:
            for attempt in range(3):
                try:
                    result = self._query_osrm(wp1, wp2)
                    if result:
                        break
                except Exception as e:
                    print(f"[OSRM] Attempt {attempt+1} failed: {e}")
                    if attempt < 2:
                        time.sleep(1.5 * (attempt + 1))

        # 3. Haversine fallback
        if result is None:
            result = self._estimate_route(wp1, wp2)

        # Cache only real routing results
        if result.get('source') not in ('estimate',):
            self.cache[key] = result
            self._req_count += 1
            if self._req_count % 50 == 0:
                self._save_cache()

        return result

    def get_route_with_compliance(self, wp1: Waypoint, wp2: Waypoint) -> dict:
        """
        Like get_route but also attempts rerouting if forbidden roads detected.
        Adds 'compliance_ok' and 'road_violations' keys.
        """
        result = self.get_route(wp1, wp2)
        violations = result.get('road_violations', [])

        if violations and self.use_osrm:
            # Try OSRM with an intermediate waypoint to detour around the violation
            mid_lat = (wp1.lat + wp2.lat) / 2 + 0.02   # slight northward shift
            mid_lon = (wp1.lon + wp2.lon) / 2
            alt = self._query_osrm_via(wp1, wp2, mid_lat, mid_lon)
            if alt and not alt.get('road_violations'):
                alt['rerouted'] = True
                result = alt

        result['compliance_ok'] = len(result.get('road_violations', [])) == 0
        return result

    def estimate_distance(self, wp1: Waypoint, wp2: Waypoint) -> float:
        """Quick haversine-based estimate (no API call)."""
        return haversine_km(wp1.lat, wp1.lon, wp2.lat, wp2.lon) * HAVERSINE_ROAD_FACTOR

    def get_distance_matrix(self, waypoints: list,
                            max_distance_km: float = None) -> dict:
        """Distance matrix for a list of waypoints."""
        matrix = {}
        n = len(waypoints)
        for i in range(n):
            for j in range(i + 1, n):
                air = waypoints[i].distance_to(waypoints[j])
                if max_distance_km and air * HAVERSINE_ROAD_FACTOR > max_distance_km:
                    continue
                route = self.get_route(waypoints[i], waypoints[j])
                matrix[(i, j)] = matrix[(j, i)] = route
        return matrix

    def save(self):
        self._save_cache()
        print(f"[RoutingService] Saved {len(self.cache)} cached routes")

    # ── OSRM internals ────────────────────────────────────────

    def _query_osrm(self, wp1: Waypoint, wp2: Waypoint) -> Optional[dict]:
        """
        Query OSRM with overview=full for complete road track.
        Also requests steps=true for road name compliance checking.
        """
        self._rate_limit()

        coords = f"{wp1.lon},{wp1.lat};{wp2.lon},{wp2.lat}"
        url    = f"{OSRM_BASE_URL}/route/v1/{OSRM_PROFILE}/{coords}"
        params = {
            "overview":    "full",    # Complete polyline, not simplified
            "geometries":  "geojson",
            "steps":       "true",    # Road names per step → compliance check
            "annotations": "false",
            # NOTE: the public OSRM demo server does NOT support 'exclude';
            # compliance is enforced post-hoc via step name inspection.
        }

        resp = requests.get(url, params=params, timeout=12)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != "Ok" or not data.get("routes"):
            return None

        route     = data["routes"][0]
        dist_km   = route["distance"] / 1000.0
        dur_hrs   = route["duration"] / 3600.0
        geometry  = _geometry_from_geojson(
            route.get("geometry", {}).get("coordinates", [])
        )

        # Collect step names for compliance check
        steps = []
        for leg in route.get("legs", []):
            for step in leg.get("steps", []):
                steps.append({
                    "name": step.get("name", ""),
                    "ref":  step.get("ref", ""),
                })
        violations = _check_road_compliance(steps)

        return {
            "distance_km":    round(dist_km, 2),
            "duration_hours": round(dur_hrs, 3),
            "geometry":       geometry,
            "source":         "osrm",
            "road_violations": violations,
            "step_names":     [s["name"] for s in steps if s["name"]],
        }

    def _query_osrm_via(self, wp1: Waypoint, wp2: Waypoint,
                        via_lat: float, via_lon: float) -> Optional[dict]:
        """Query OSRM with an intermediate waypoint to detour forbidden roads."""
        self._rate_limit()
        coords = (f"{wp1.lon},{wp1.lat};"
                  f"{via_lon},{via_lat};"
                  f"{wp2.lon},{wp2.lat}")
        url = f"{OSRM_BASE_URL}/route/v1/{OSRM_PROFILE}/{coords}"
        params = {
            "overview": "full", "geometries": "geojson",
            "steps": "true", "annotations": "false",
        }
        try:
            resp = requests.get(url, params=params, timeout=12)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != "Ok" or not data.get("routes"):
                return None
            route    = data["routes"][0]
            geometry = _geometry_from_geojson(
                route.get("geometry", {}).get("coordinates", [])
            )
            steps = [
                {"name": s.get("name",""), "ref": s.get("ref","")}
                for leg in route.get("legs", [])
                for s in leg.get("steps", [])
            ]
            return {
                "distance_km":    round(route["distance"] / 1000, 2),
                "duration_hours": round(route["duration"] / 3600, 3),
                "geometry":       geometry,
                "source":         "osrm_via",
                "road_violations": _check_road_compliance(steps),
            }
        except Exception as e:
            print(f"[OSRM-via] Error: {e}")
            return None

    def _estimate_route(self, wp1: Waypoint, wp2: Waypoint) -> dict:
        """Haversine fallback — straight line geometry, no compliance check."""
        air   = haversine_km(wp1.lat, wp1.lon, wp2.lat, wp2.lon)
        dist  = air * HAVERSINE_ROAD_FACTOR
        dur   = dist / AVERAGE_SPEED_KMH
        return {
            "distance_km":    round(dist, 2),
            "duration_hours": round(dur, 3),
            "geometry":       [[wp1.lat, wp1.lon], [wp2.lat, wp2.lon]],
            "source":         "estimate",
            "road_violations": [],
        }
