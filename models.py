"""
Centopassi Route Planner - Data Models
"""
from dataclasses import dataclass, field
from typing import Optional
import math


def _simplify_geometry(coords: list, max_points: int = 60) -> list:
    """
    Reduce geometry points using distance-based sampling.
    Keeps first and last point, samples evenly in between.
    Keeps the route shape recognizable while cutting JSON size ~90%.
    """
    if not coords or len(coords) <= max_points:
        return coords
    # Always keep first and last
    step = max(1, (len(coords) - 1) / (max_points - 1))
    result = []
    for i in range(max_points - 1):
        idx = int(i * step)
        result.append(coords[idx])
    result.append(coords[-1])
    return result


@dataclass
class Waypoint:
    """A single waypoint (pass/mountain pass) from the GPX file."""
    id: int                          # Sequential index
    name: str                        # Original name from GPX (e.g., "001", "008 GP")
    lat: float
    lon: float
    elevation: float = 0.0
    description: str = ""            # Road/location description from GPX
    city: str = ""
    province: str = ""               # State/Province from Garmin extensions
    country: str = "ITA"
    is_golden_point: bool = False    # True if name contains "GP"
    is_unpaved: bool = False         # True if road type suggests dirt/bridleway
    road_type: str = ""              # Classified road type
    symbol: str = ""                 # Garmin symbol (Flag, Blue / Flag, Red)
    road_warnings: list = field(default_factory=list)  # Warnings from road intelligence

    @property
    def number(self) -> str:
        """Extract numeric part of name (e.g., '008' from '008 GP')."""
        return self.name.split()[0] if self.name else ""
    
    @property
    def display_name(self) -> str:
        """Human-readable display name."""
        parts = [f"WP {self.number}"]
        if self.is_golden_point:
            parts.append("⭐ GP")
        if self.city:
            parts.append(f"- {self.city}")
        if self.province:
            parts.append(f"({self.province})")
        return " ".join(parts)

    def distance_to(self, other: 'Waypoint') -> float:
        """Haversine distance in km to another waypoint."""
        return haversine_km(self.lat, self.lon, other.lat, other.lon)
    
    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON."""
        return {
            'id': self.id,
            'name': self.name,
            'number': self.number,
            'lat': self.lat,
            'lon': self.lon,
            'elevation': self.elevation,
            'description': self.description,
            'city': self.city,
            'province': self.province,
            'is_golden_point': self.is_golden_point,
            'is_unpaved': self.is_unpaved,
            'road_type': self.road_type,
            'display_name': self.display_name,
            'road_warnings': self.road_warnings,
        }


@dataclass
class RouteSegment:
    """A segment between two consecutive waypoints in the route."""
    from_wp: Waypoint
    to_wp: Waypoint
    distance_km: float = 0.0           # Road distance
    duration_hours: float = 0.0        # Estimated driving time
    is_unpaved: bool = False           # Segment includes unpaved roads
    geometry: list = field(default_factory=list)    # Route polyline [[lat,lon],...]
    road_distance_km: float = 0.0      # Real road distance (from OSRM/GraphHopper)
    air_distance_km: float = 0.0       # Straight-line distance
    road_violations: list = field(default_factory=list)   # Forbidden road names found
    step_names: list = field(default_factory=list)         # Road names from routing steps
    routing_source: str = 'estimate'   # 'osrm'|'graphhopper'|'cache'|'estimate'

    @property
    def has_real_routing(self) -> bool:
        """True when geometry comes from a real routing API (not haversine estimate)."""
        return len(self.geometry) > 2

    def to_dict(self) -> dict:
        return {
            'from_wp':        self.from_wp.to_dict(),
            'to_wp':          self.to_wp.to_dict(),
            'distance_km':    round(self.distance_km, 1),
            'duration_hours': round(self.duration_hours, 2),
            'is_unpaved':     self.is_unpaved,
            'geometry':       _simplify_geometry(self.geometry),
            'routing_source': self.routing_source,
            'road_violations': self.road_violations,
            'has_real_routing': self.has_real_routing,
        }


@dataclass
class DaySegment:
    """One day's portion of the route."""
    day_number: int                  # 1-4
    waypoints: list = field(default_factory=list)   # List[Waypoint]
    segments: list = field(default_factory=list)     # List[RouteSegment]
    start_time: str = "05:00"
    end_time: str = "21:15"
    total_km: float = 0.0
    total_hours: float = 0.0
    total_elevation_gain: float = 0.0
    total_elevation_loss: float = 0.0
    unpaved_segments: int = 0
    unpaved_km: float = 0.0
    road_warnings: list = field(default_factory=list)  # Aggregated warnings for this day

    @property
    def waypoint_count(self) -> int:
        return len(self.waypoints)
    
    @property
    def golden_points_count(self) -> int:
        return sum(1 for wp in self.waypoints if wp.is_golden_point)
    
    def to_dict(self) -> dict:
        return {
            'day_number': self.day_number,
            'start_time': self.start_time,
            'end_time': self.end_time,
            'waypoint_count': self.waypoint_count,
            'golden_points_count': self.golden_points_count,
            'total_km': round(self.total_km, 1),
            'total_hours': round(self.total_hours, 2),
            'total_elevation_gain': round(self.total_elevation_gain, 0),
            'total_elevation_loss': round(self.total_elevation_loss, 0),
            'unpaved_segments': self.unpaved_segments,
            'unpaved_km': round(self.unpaved_km, 1),
            'waypoints': [wp.to_dict() for wp in self.waypoints],
            'segments': [seg.to_dict() for seg in self.segments],
            'road_warnings': self.road_warnings,
        }


@dataclass
class Route:
    """Complete multi-day route."""
    days: list = field(default_factory=list)  # List[DaySegment]
    start_point: dict = field(default_factory=dict)  # {lat, lon, name}
    finish_point: dict = field(default_factory=dict)  # {lat, lon, name}
    alternatives: list = field(default_factory=list)  # List[Waypoint] - backup WPs
    road_warnings: list = field(default_factory=list)  # Global road intelligence warnings
    compliance: dict = field(default_factory=dict)  # Speed compliance check results

    @property
    def total_km(self) -> float:
        return sum(d.total_km for d in self.days)
    
    @property
    def total_waypoints(self) -> int:
        return sum(d.waypoint_count for d in self.days)
    
    @property
    def total_golden_points(self) -> int:
        return sum(d.golden_points_count for d in self.days)
    
    @property
    def all_waypoints(self) -> list:
        wps = []
        for d in self.days:
            wps.extend(d.waypoints)
        return wps
    
    @property
    def total_elevation_gain(self) -> float:
        return sum(d.total_elevation_gain for d in self.days)
    
    @property
    def total_elevation_loss(self) -> float:
        return sum(d.total_elevation_loss for d in self.days)
    
    @property
    def total_elevation_delta(self) -> float:
        """Total absolute elevation change (gain + loss) for scoring."""
        return self.total_elevation_gain + self.total_elevation_loss
    
    def calculate_score(self) -> dict:
        """Calculate score based on official rules."""
        from config import (
            POINTS_PER_PASS, POINTS_PER_GOLDEN, POINTS_PER_GOLDEN_NO_100,
            POINTS_PER_METER_ELEVATION, BONUS_100TH_PASS,
            BONUS_100TH_DISTANCE_KM, TARGET_WAYPOINTS
        )
        
        reached_100 = self.total_waypoints >= TARGET_WAYPOINTS
        
        # Pass points
        regular_count = self.total_waypoints - self.total_golden_points
        pass_points = regular_count * POINTS_PER_PASS
        
        # Golden points
        if reached_100:
            gp_points = self.total_golden_points * POINTS_PER_GOLDEN
        else:
            gp_points = self.total_golden_points * POINTS_PER_GOLDEN_NO_100
        
        # Elevation points
        elevation_points = int(self.total_elevation_delta)
        
        # 100th pass bonus
        bonus = 0
        if reached_100 and self.all_waypoints:
            last_wp = self.all_waypoints[-1]
            finish_lat = self.finish_point.get('lat', 0)
            finish_lon = self.finish_point.get('lon', 0)
            dist_to_finish = haversine_km(last_wp.lat, last_wp.lon, finish_lat, finish_lon)
            if dist_to_finish <= BONUS_100TH_DISTANCE_KM:
                bonus = BONUS_100TH_PASS
        
        total = pass_points + gp_points + elevation_points + bonus
        
        return {
            'pass_points': pass_points,
            'golden_points': gp_points,
            'elevation_points': elevation_points,
            'bonus_100th': bonus,
            'total_points': total,
            'reached_100': reached_100,
            'regular_passes': regular_count,
            'golden_passes': self.total_golden_points,
        }
    
    def to_dict(self) -> dict:
        return {
            'start_point': self.start_point,
            'finish_point': self.finish_point,
            'total_km': round(self.total_km, 1),
            'total_waypoints': self.total_waypoints,
            'total_golden_points': self.total_golden_points,
            'total_elevation_gain': round(self.total_elevation_gain, 0),
            'total_elevation_loss': round(self.total_elevation_loss, 0),
            'score': self.calculate_score(),
            'days': [d.to_dict() for d in self.days],
            'alternatives': [wp.to_dict() for wp in self.alternatives],
            'road_warnings': self.road_warnings,
            'compliance': self.compliance,
        }


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great-circle distance between two points on Earth in km."""
    R = 6371.0  # Earth radius in km
    
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    
    return R * c
