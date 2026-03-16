"""
Centopassi Route Planner - OSRM Routing Service
Provides real road distances and routes via OSRM API.
"""
import requests
import time
import json
import os
from models import Waypoint, haversine_km
from config import (
    OSRM_BASE_URL, OSRM_PROFILE, OSRM_EXCLUDE,
    OSRM_RATE_LIMIT_SEC, HAVERSINE_ROAD_FACTOR, AVERAGE_SPEED_KMH
)


class RoutingService:
    """Client for OSRM routing API with caching and fallback."""
    
    def __init__(self, use_osrm=True, cache_file="route_cache.json"):
        self.use_osrm = use_osrm
        self.cache_file = cache_file
        self.cache = {}
        self._last_request_time = 0
        self._request_count = 0
        self._load_cache()
    
    def _load_cache(self):
        """Load distance cache from disk."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r') as f:
                    self.cache = json.load(f)
                print(f"[RoutingService] Loaded {len(self.cache)} cached routes")
            except (json.JSONDecodeError, IOError):
                self.cache = {}
    
    def _save_cache(self):
        """Save distance cache to disk."""
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f)
        except IOError:
            pass
    
    def _cache_key(self, lat1, lon1, lat2, lon2):
        """Generate cache key for a point pair."""
        return f"{lat1:.6f},{lon1:.6f}|{lat2:.6f},{lon2:.6f}"
    
    def _rate_limit(self):
        """Enforce rate limiting for OSRM demo server."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < OSRM_RATE_LIMIT_SEC:
            time.sleep(OSRM_RATE_LIMIT_SEC - elapsed)
        self._last_request_time = time.time()
    
    def get_route(self, wp1: Waypoint, wp2: Waypoint) -> dict:
        """
        Get route between two waypoints.
        
        Returns:
            {
                'distance_km': float,
                'duration_hours': float,
                'geometry': [[lat, lon], ...],
                'source': 'osrm' | 'cache' | 'estimate'
            }
        """
        key = self._cache_key(wp1.lat, wp1.lon, wp2.lat, wp2.lon)
        
        # Check cache first
        if key in self.cache:
            result = self.cache[key]
            result['source'] = 'cache'
            return result
        
        # Try OSRM API
        if self.use_osrm:
            try:
                result = self._query_osrm(wp1, wp2)
                if result:
                    self.cache[key] = result
                    self._request_count += 1
                    # Save cache every 50 requests
                    if self._request_count % 50 == 0:
                        self._save_cache()
                    return result
            except Exception as e:
                print(f"[RoutingService] OSRM error: {e}")
        
        # Fallback: estimate from haversine
        return self._estimate_route(wp1, wp2)
    
    def _query_osrm(self, wp1: Waypoint, wp2: Waypoint) -> dict | None:
        """Query OSRM route API."""
        self._rate_limit()
        
        # OSRM uses lon,lat order
        coords = f"{wp1.lon},{wp1.lat};{wp2.lon},{wp2.lat}"
        url = f"{OSRM_BASE_URL}/route/v1/{OSRM_PROFILE}/{coords}"
        
        params = {
            'overview': 'simplified',
            'geometries': 'geojson',
            'exclude': OSRM_EXCLUDE,
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data.get('code') != 'Ok' or not data.get('routes'):
            return None
        
        route = data['routes'][0]
        distance_km = route['distance'] / 1000.0
        duration_hours = route['duration'] / 3600.0
        
        # Extract geometry (GeoJSON is [lon, lat], we want [lat, lon])
        geometry = []
        if route.get('geometry', {}).get('coordinates'):
            for coord in route['geometry']['coordinates']:
                geometry.append([coord[1], coord[0]])  # [lat, lon]
        
        return {
            'distance_km': round(distance_km, 2),
            'duration_hours': round(duration_hours, 3),
            'geometry': geometry,
            'source': 'osrm',
        }
    
    def _estimate_route(self, wp1: Waypoint, wp2: Waypoint) -> dict:
        """Estimate route using haversine distance × road factor."""
        air_dist = haversine_km(wp1.lat, wp1.lon, wp2.lat, wp2.lon)
        road_dist = air_dist * HAVERSINE_ROAD_FACTOR
        duration = road_dist / AVERAGE_SPEED_KMH
        
        return {
            'distance_km': round(road_dist, 2),
            'duration_hours': round(duration, 3),
            'geometry': [[wp1.lat, wp1.lon], [wp2.lat, wp2.lon]],
            'source': 'estimate',
        }
    
    def get_distance_matrix(self, waypoints: list[Waypoint], 
                           max_distance_km: float = None) -> dict:
        """
        Build a distance matrix for a list of waypoints.
        Uses haversine for initial filtering, then OSRM for close pairs.
        
        Returns:
            {(i, j): {'distance_km': float, 'duration_hours': float}}
        """
        n = len(waypoints)
        matrix = {}
        
        for i in range(n):
            for j in range(i + 1, n):
                air_dist = waypoints[i].distance_to(waypoints[j])
                
                # Skip pairs that are too far apart (optimization)
                if max_distance_km and air_dist * HAVERSINE_ROAD_FACTOR > max_distance_km:
                    continue
                
                route = self.get_route(waypoints[i], waypoints[j])
                matrix[(i, j)] = route
                matrix[(j, i)] = route  # Symmetric for simplicity
        
        return matrix
    
    def estimate_distance(self, wp1: Waypoint, wp2: Waypoint) -> float:
        """Quick distance estimate (no API call)."""
        return haversine_km(wp1.lat, wp1.lon, wp2.lat, wp2.lon) * HAVERSINE_ROAD_FACTOR
    
    def save(self):
        """Save cache to disk."""
        self._save_cache()
        print(f"[RoutingService] Saved {len(self.cache)} cached routes")
