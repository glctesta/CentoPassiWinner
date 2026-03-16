"""
Centopassi Route Planner - Route Optimization Engine
Selects 100 waypoints and organizes them into a 4-day route.
"""
import math
import random
from typing import Optional
from models import Waypoint, RouteSegment, DaySegment, Route, haversine_km
from routing_service import RoutingService
from config import (
    TARGET_WAYPOINTS, MAX_KM_PER_DAY, MIN_TOTAL_KM,
    MAX_START_DISTANCE_KM, MIN_START_DISTANCE_KM,
    DRIVING_HOURS, DAY1_START_TIME, OTHER_DAYS_START_TIME,
    REST_START_TIME, FINISH_WINDOW_START, AVERAGE_SPEED_KMH,
    BONUS_100TH_DISTANCE_KM, HAVERSINE_ROAD_FACTOR,
)

# Unpaved mode constants
UNPAVED_ALLOW = "allow"       # Include unpaved, highlight them
UNPAVED_LIMIT = "limit"       # Include max N unpaved WPs
UNPAVED_EXCLUDE = "exclude"   # Fully exclude unpaved WPs


class RouteOptimizer:
    """Optimizes a route selecting 100 waypoints across 4 days."""
    
    def __init__(self, waypoints: list[Waypoint], routing: RoutingService = None):
        self.all_waypoints = waypoints
        self.routing = routing or RoutingService(use_osrm=False)
        self._progress_callback = None
    
    def set_progress_callback(self, callback):
        """Set a callback function(message, percent) for progress updates."""
        self._progress_callback = callback
    
    def _progress(self, message: str, percent: int = 0):
        """Report progress."""
        if self._progress_callback:
            self._progress_callback(message, percent)
        else:
            print(f"[Optimizer] {message} ({percent}%)")
    
    def optimize(self, finish_lat: float, finish_lon: float, 
                 finish_name: str = "Traguardo",
                 use_osrm_routing: bool = False,
                 unpaved_mode: str = UNPAVED_LIMIT,
                 max_unpaved: int = 10) -> Route:
        """
        Main optimization: find best route of 100 waypoints in 4 days.
        
        Args:
            finish_lat, finish_lon: arrival/finish point coordinates
            finish_name: name of finish location
            use_osrm_routing: if True, use OSRM for exact distances (slower)
            unpaved_mode: 'allow', 'limit', or 'exclude'
            max_unpaved: max unpaved WPs when mode='limit'
        
        Returns:
            Route object with 4 DaySegments
        """
        self._progress("Inizializzazione ottimizzazione...", 0)
        
        finish_point = {'lat': finish_lat, 'lon': finish_lon, 'name': finish_name}
        
        # Step 1: Find valid start points (within 450 km air from finish)
        self._progress("Ricerca punti di partenza validi...", 5)
        start_candidates = self._find_start_candidates(finish_lat, finish_lon)
        
        if not start_candidates:
            raise ValueError(
                f"Nessun waypoint trovato entro {MAX_START_DISTANCE_KM} km dal traguardo. "
                f"Verificare le coordinate del traguardo."
            )
        
        self._progress(f"Trovati {len(start_candidates)} punti di partenza candidati", 10)
        
        # Step 2: Try multiple start points and pick the best route
        best_route = None
        best_score = -1
        
        # Try top N start candidates (sorted by distance from finish, farthest first)
        start_candidates.sort(key=lambda wp: -wp.distance_to(
            Waypoint(id=-1, name="finish", lat=finish_lat, lon=finish_lon)
        ))
        
        # Take candidates spread across the distance range
        candidates_to_try = self._select_diverse_starts(
            start_candidates, finish_lat, finish_lon, max_tries=5
        )
        
        for idx, start_wp in enumerate(candidates_to_try):
            pct = 10 + int(70 * idx / len(candidates_to_try))
            self._progress(
                f"Tentativo {idx+1}/{len(candidates_to_try)}: "
                f"partenza da WP {start_wp.number} ({start_wp.city or start_wp.description[:30]})",
                pct
            )
            
            try:
                route = self._build_route(
                    start_wp, finish_lat, finish_lon, finish_name,
                    use_osrm=use_osrm_routing,
                    unpaved_mode=unpaved_mode,
                    max_unpaved=max_unpaved,
                )
                
                if route and route.total_waypoints >= TARGET_WAYPOINTS and route.total_km >= MIN_TOTAL_KM:
                    score = self._evaluate_route(route)
                    if score > best_score:
                        best_score = score
                        best_route = route
                        self._progress(
                            f"  → Percorso trovato: {route.total_km:.0f} km, "
                            f"{route.total_waypoints} WP, score={score:.0f}",
                            pct
                        )
                elif route and route.total_waypoints >= TARGET_WAYPOINTS:
                    # Route has enough WPs but under 1600 km — keep as fallback
                    self._progress(
                        f"  → Percorso sotto i 1600 km ({route.total_km:.0f} km), scartato",
                        pct
                    )
            except Exception as e:
                self._progress(f"  → Errore: {e}", pct)
                continue
        
        if not best_route:
            # Fallback: relax constraints and try again
            self._progress("Rilassamento vincoli e nuovo tentativo...", 80)
            best_route = self._build_route_relaxed(
                start_candidates, finish_lat, finish_lon, finish_name,
                unpaved_mode=unpaved_mode, max_unpaved=max_unpaved,
            )
        
        if not best_route:
            raise ValueError(
                "Impossibile costruire un percorso valido con i vincoli dati. "
                "Provare con un punto di arrivo diverso o consentire più sterrati."
            )
        
        self._progress("Ottimizzazione percorso con 2-opt...", 85)
        best_route = self._optimize_2opt(best_route)
        
        self._progress("Selezione WP alternativi...", 90)
        self._select_alternatives(best_route, num_alternatives=10)
        
        self._progress("Calcolo distanze finali...", 92)
        if use_osrm_routing:
            self._recalculate_with_osrm(best_route)
        
        self._progress(
            f"Percorso ottimizzato: {best_route.total_km:.0f} km, "
            f"{best_route.total_waypoints} WP, "
            f"{best_route.total_golden_points} GP, "
            f"{len(best_route.alternatives)} alternativi",
            100
        )
        
        return best_route
    
    def _find_start_candidates(self, finish_lat: float, finish_lon: float) -> list[Waypoint]:
        """Find waypoints within MAX_START_DISTANCE_KM (air) from finish."""
        candidates = []
        for wp in self.all_waypoints:
            dist = haversine_km(wp.lat, wp.lon, finish_lat, finish_lon)
            if dist <= MAX_START_DISTANCE_KM and dist >= MIN_START_DISTANCE_KM:
                candidates.append(wp)
        return candidates
    
    def _select_diverse_starts(self, candidates: list[Waypoint],
                                finish_lat: float, finish_lon: float,
                                max_tries: int = 5) -> list[Waypoint]:
        """Select diverse start points from candidates (different directions)."""
        if len(candidates) <= max_tries:
            return candidates
        
        # Group by quadrant relative to finish
        quadrants = {'NE': [], 'NW': [], 'SE': [], 'SW': []}
        for wp in candidates:
            q = ('N' if wp.lat > finish_lat else 'S') + ('E' if wp.lon > finish_lon else 'W')
            quadrants[q].append(wp)
        
        selected = []
        for q, wps in quadrants.items():
            if wps:
                # Pick the waypoint farthest from finish in each quadrant
                wps.sort(key=lambda wp: -haversine_km(wp.lat, wp.lon, finish_lat, finish_lon))
                selected.append(wps[0])
                if len(wps) > 1:
                    # Also pick one mid-distance
                    selected.append(wps[len(wps)//2])
        
        return selected[:max_tries]
    
    def _build_route(self, start_wp: Waypoint, 
                     finish_lat: float, finish_lon: float,
                     finish_name: str, use_osrm: bool = False,
                     unpaved_mode: str = UNPAVED_LIMIT,
                     max_unpaved: int = 10) -> Optional[Route]:
        """
        Build a complete route starting from start_wp heading toward finish.
        Uses greedy nearest-neighbor with direction bias.
        """
        route = Route()
        route.start_point = {
            'lat': start_wp.lat, 'lon': start_wp.lon, 
            'name': f"Partenza - WP {start_wp.number}"
        }
        route.finish_point = {
            'lat': finish_lat, 'lon': finish_lon, 'name': finish_name
        }
        
        # Available waypoints (excluding start)
        available = set(range(len(self.all_waypoints)))
        available.discard(start_wp.id)
        
        # Track selected WPs — initially empty, day_wps includes start_wp for day 1
        all_selected_ids = {start_wp.id}
        total_selected_count = 0  # Will include start_wp via day 1
        unpaved_count = 1 if start_wp.is_unpaved else 0
        
        # Build route day by day
        current_wp = start_wp
        
        for day_num in range(1, 5):  # 4 days
            day = DaySegment(day_number=day_num)
            
            if day_num == 1:
                day.start_time = DAY1_START_TIME
                max_hours = DRIVING_HOURS[0]
            elif day_num == 4:
                day.start_time = OTHER_DAYS_START_TIME
                day.end_time = FINISH_WINDOW_START
                max_hours = DRIVING_HOURS[3]
            else:
                day.start_time = OTHER_DAYS_START_TIME
                max_hours = DRIVING_HOURS[day_num - 1]
            
            # estimate_distance already includes HAVERSINE_ROAD_FACTOR (1.35)
            # so day_km tracks road-estimated km — cap at actual MAX_KM_PER_DAY
            max_km_today = min(MAX_KM_PER_DAY, max_hours * AVERAGE_SPEED_KMH)
            day_km = 0.0
            day_hours = 0.0
            day_wps = []
            day_segments = []
            
            # Day 1: include start waypoint as first WP
            if day_num == 1:
                day_wps.append(start_wp)
            
            # Add waypoints greedily
            # Calculate ideal km per WP to ensure we reach MIN_TOTAL_KM overall
            remaining_wps = TARGET_WAYPOINTS - (total_selected_count + len(day_wps))
            remaining_days = 4 - day_num + 1
            
            while (day_km < max_km_today * 0.92 and 
                   day_hours < max_hours * 0.92 and
                   total_selected_count + len(day_wps) < TARGET_WAYPOINTS and
                   available):
                
                # Target per-WP spacing that ensures MIN_TOTAL_KM is reached
                # If running short on km, prefer more distant WPs
                progress_km_ratio = day_km / max(max_km_today, 1)
                progress_wp_ratio = len(day_wps) / max(TARGET_WAYPOINTS / 4, 1)
                need_more_km = progress_wp_ratio > progress_km_ratio + 0.15
                
                # Find next best waypoint
                next_wp = self._find_next_waypoint(
                    current_wp, available, 
                    finish_lat, finish_lon,
                    day_km, max_km_today,
                    day_num, total_selected_count + len(day_wps),
                    unpaved_mode=unpaved_mode,
                    max_unpaved=max_unpaved,
                    current_unpaved_count=unpaved_count,
                    need_more_km=need_more_km,
                )
                
                if next_wp is None:
                    break
                
                # Calculate distance
                if use_osrm:
                    route_info = self.routing.get_route(current_wp, next_wp)
                    seg_km = route_info['distance_km']
                    seg_hours = route_info['duration_hours']
                    seg_geometry = route_info['geometry']
                else:
                    seg_km = self.routing.estimate_distance(current_wp, next_wp)
                    seg_hours = seg_km / AVERAGE_SPEED_KMH
                    seg_geometry = [[current_wp.lat, current_wp.lon], [next_wp.lat, next_wp.lon]]
                
                # Check if adding this segment exceeds daily limits
                if day_km + seg_km > max_km_today or day_hours + seg_hours > max_hours:
                    if day_wps:
                        break
                    # If first WP of the day (after start), allow it
                
                # Add segment
                segment = RouteSegment(
                    from_wp=current_wp,
                    to_wp=next_wp,
                    distance_km=seg_km,
                    duration_hours=seg_hours,
                    is_unpaved=next_wp.is_unpaved,
                    geometry=seg_geometry,
                    air_distance_km=haversine_km(current_wp.lat, current_wp.lon, next_wp.lat, next_wp.lon),
                    road_distance_km=seg_km,
                )
                
                day_segments.append(segment)
                day_wps.append(next_wp)
                day_km += seg_km
                day_hours += seg_hours
                current_wp = next_wp
                available.discard(next_wp.id)
                all_selected_ids.add(next_wp.id)
                
                if next_wp.is_unpaved:
                    unpaved_count += 1
                
                # Compute elevation changes
                prev_wp = day_wps[-2] if len(day_wps) >= 2 else start_wp
                elev_diff = next_wp.elevation - prev_wp.elevation
                
                if elev_diff > 0:
                    day.total_elevation_gain += elev_diff
                else:
                    day.total_elevation_loss += abs(elev_diff)
            
            day.waypoints = day_wps
            day.segments = day_segments
            day.total_km = day_km
            day.total_hours = day_hours
            day.unpaved_segments = sum(1 for s in day_segments if s.is_unpaved)
            day.unpaved_km = sum(s.distance_km for s in day_segments if s.is_unpaved)
            
            total_selected_count += len(day_wps)
            route.days.append(day)
            
            if total_selected_count >= TARGET_WAYPOINTS:
                break
        
        return route if total_selected_count >= TARGET_WAYPOINTS else None
    
    def _find_next_waypoint(self, current: Waypoint, available: set,
                            finish_lat: float, finish_lon: float,
                            day_km: float, max_km_today: float,
                            day_number: int, total_selected: int,
                            unpaved_mode: str = UNPAVED_LIMIT,
                            max_unpaved: int = 10,
                            current_unpaved_count: int = 0,
                            need_more_km: bool = False) -> Optional[Waypoint]:
        """
        Find the next best waypoint using a scoring function that balances:
        - Proximity (nearest neighbor)
        - Direction toward finish (for later days)
        - GP bonus (slight preference for golden points)
        - Unpaved penalty (controlled by unpaved_mode)
        """
        if not available:
            return None
        
        remaining_wps = TARGET_WAYPOINTS - total_selected
        
        # Progress through the route (0=start, 1=finish)
        progress = total_selected / TARGET_WAYPOINTS
        
        # Distance from current to finish
        dist_to_finish = haversine_km(current.lat, current.lon, finish_lat, finish_lon)
        
        best_wp = None
        best_score = -float('inf')
        
        # Can we still add unpaved WPs?
        unpaved_budget_left = True
        if unpaved_mode == UNPAVED_EXCLUDE:
            unpaved_budget_left = False
        elif unpaved_mode == UNPAVED_LIMIT:
            unpaved_budget_left = current_unpaved_count < max_unpaved
        
        candidates = []
        for wp_id in available:
            wp = self.all_waypoints[wp_id]
            
            # Skip unpaved if budget exhausted or excluded
            if wp.is_unpaved and not unpaved_budget_left:
                continue
            
            dist = current.distance_to(wp)
            # Pre-filter: skip waypoints too far for a single hop
            remaining_km = max_km_today - day_km
            if dist * HAVERSINE_ROAD_FACTOR > remaining_km + 20:
                continue
            candidates.append((wp, dist))
        
        # Sort by distance for efficiency, evaluate top candidates
        candidates.sort(key=lambda x: x[1])
        
        # Evaluate more candidates early, fewer later
        max_eval = min(len(candidates), max(20, 50 - int(progress * 30)))
        
        for wp, dist in candidates[:max_eval]:
            road_dist = dist * HAVERSINE_ROAD_FACTOR
            
            # Would this exceed daily limit?
            if day_km + road_dist > max_km_today:
                continue
            
            # Score components
            # 1. Proximity score (prefer closer waypoints, normalized)
            proximity_score = 100.0 / (1.0 + road_dist)
            
            # 2. Direction score (prefer waypoints that move toward finish)
            wp_dist_to_finish = haversine_km(wp.lat, wp.lon, finish_lat, finish_lon)
            
            if progress < 0.3:
                # Early: explore away from finish to build distance
                direction_score = (wp_dist_to_finish - dist_to_finish) * 0.1
            elif progress < 0.7:
                # Mid: neutral to slight towards finish
                direction_score = (dist_to_finish - wp_dist_to_finish) * 0.05
            else:
                # Late: strongly toward finish
                direction_score = (dist_to_finish - wp_dist_to_finish) * 0.3
                
                # Extra bonus if within 50km of finish near 100th WP
                if total_selected >= 95 and wp_dist_to_finish <= BONUS_100TH_DISTANCE_KM:
                    direction_score += 50
            
            # 3. Golden Point bonus (slight preference, but not overwhelming)
            gp_bonus = 5.0 if wp.is_golden_point else 0.0
            
            # 4. Unpaved penalty (scaled by mode)
            unpaved_penalty = 0.0
            if wp.is_unpaved:
                if unpaved_mode == UNPAVED_EXCLUDE:
                    continue  # Already filtered above, but just in case
                elif unpaved_mode == UNPAVED_LIMIT:
                    # Strong penalty as we approach the limit
                    ratio = current_unpaved_count / max(max_unpaved, 1)
                    unpaved_penalty = -10.0 * (1 + ratio * 3)
                else:  # ALLOW
                    unpaved_penalty = -3.0
            
            # 5. Spread score (prefer keeping travel regular)
            # If running short on km for 1600 minimum, prefer more distant WPs
            ideal_dist = 25.0 if need_more_km else 18.0
            spread_score = -abs(road_dist - ideal_dist) * 0.1
            
            # If need more km, also reduce proximity weight to avoid hugging nearby WPs
            if need_more_km:
                proximity_score *= 0.5
            
            total_score = proximity_score + direction_score + gp_bonus + unpaved_penalty + spread_score
            
            if total_score > best_score:
                best_score = total_score
                best_wp = wp
        
        return best_wp
    
    def _evaluate_route(self, route: Route) -> float:
        """Score a complete route for comparison."""
        score = 0.0
        
        # Must have 100 WPs
        if route.total_waypoints < TARGET_WAYPOINTS:
            return -1000000
        
        # Must be >= 1600 km
        if route.total_km < MIN_TOTAL_KM:
            score -= (MIN_TOTAL_KM - route.total_km) * 100
        else:
            score += route.total_km * 0.5
        
        # Golden points are valuable
        score += route.total_golden_points * 1000
        
        # Elevation points
        score += route.total_elevation_delta * 0.5
        
        # Balanced days are better
        if route.days:
            km_values = [d.total_km for d in route.days]
            km_mean = sum(km_values) / len(km_values)
            km_variance = sum((k - km_mean)**2 for k in km_values) / len(km_values)
            score -= km_variance * 0.01
            
            # Penalize any day exceeding 600 km hard
            for km in km_values:
                if km > MAX_KM_PER_DAY:
                    score -= (km - MAX_KM_PER_DAY) * 200
        
        # Fewer unpaved segments is better
        total_unpaved = sum(d.unpaved_segments for d in route.days)
        score -= total_unpaved * 50
        
        # 100th WP close to finish for bonus
        if route.all_waypoints:
            last_wp = route.all_waypoints[-1]
            finish_lat = route.finish_point.get('lat', 0)
            finish_lon = route.finish_point.get('lon', 0)
            dist_to_finish = haversine_km(last_wp.lat, last_wp.lon, finish_lat, finish_lon)
            if dist_to_finish <= BONUS_100TH_DISTANCE_KM:
                score += 50000  # Big bonus for qualifying for the 100K
        
        return score
    
    def _build_route_relaxed(self, start_candidates: list[Waypoint],
                             finish_lat: float, finish_lon: float,
                             finish_name: str,
                             unpaved_mode: str = UNPAVED_LIMIT,
                             max_unpaved: int = 10) -> Optional[Route]:
        """Try building with relaxed constraints if strict fails."""
        for wp in start_candidates[:10]:
            try:
                route = self._build_route(
                    wp, finish_lat, finish_lon, finish_name,
                    unpaved_mode=unpaved_mode, max_unpaved=max_unpaved
                )
                if route and route.total_waypoints >= TARGET_WAYPOINTS * 0.95:
                    return route
            except:
                continue
        return None
    
    def _optimize_2opt(self, route: Route) -> Route:
        """
        Apply 2-opt local search within each day to improve route.
        Preserves first and last WP of each day as inter-day bridge points
        to maintain route continuity between consecutive days.
        """
        for day in route.days:
            if len(day.waypoints) < 5:
                continue
            
            improved = True
            iterations = 0
            max_iterations = 100
            
            while improved and iterations < max_iterations:
                improved = False
                iterations += 1
                wps = day.waypoints
                n = len(wps)
                
                # Skip first (i>=1) and last (j<=n-2) to preserve
                # inter-day connection points
                for i in range(1, n - 2):
                    for j in range(i + 2, n - 1):
                        # Calculate current distance
                        d_current = (wps[i].distance_to(wps[i+1]) + 
                                    wps[j].distance_to(wps[j+1]))
                        
                        # Calculate distance after 2-opt swap
                        d_new = (wps[i].distance_to(wps[j]) + 
                                wps[i+1].distance_to(wps[j+1]))
                        
                        if d_new < d_current:
                            # Reverse the segment between i+1 and j
                            day.waypoints[i+1:j+1] = reversed(day.waypoints[i+1:j+1])
                            improved = True
            
            # Recalculate day statistics after optimization
            self._recalculate_day(day)
        
        # Post-2opt: enforce MAX_KM_PER_DAY by moving excess WPs to next day
        self._enforce_daily_km_limit(route)
        
        return route
    
    def _enforce_daily_km_limit(self, route: Route):
        """
        After 2-opt, ensure no day exceeds MAX_KM_PER_DAY.
        Trims excess WPs from the end of an over-limit day
        and prepends them to the start of the next day.
        """
        for day_idx in range(len(route.days) - 1):
            day = route.days[day_idx]
            next_day = route.days[day_idx + 1]
            
            while day.total_km > MAX_KM_PER_DAY and len(day.waypoints) > 3:
                # Remove last WP from this day, add to start of next day
                moved_wp = day.waypoints.pop()
                next_day.waypoints.insert(0, moved_wp)
                
                # Recalculate both days
                self._recalculate_day(day)
                self._recalculate_day(next_day)
    
    def _select_alternatives(self, route: Route, num_alternatives: int = 10):
        """
        Select alternative/backup waypoints near the route.
        These are WPs not included in the main 100 that are close
        to the route and can substitute a primary WP if needed.
        """
        # Collect all selected WP IDs
        selected_ids = set()
        for day in route.days:
            for wp in day.waypoints:
                selected_ids.add(wp.id)
        
        # For each non-selected WP, find its minimum distance to any route WP
        route_wps = route.all_waypoints
        candidates = []
        
        for wp in self.all_waypoints:
            if wp.id in selected_ids:
                continue
            
            # Find nearest route WP and which day it belongs to
            min_dist = float('inf')
            near_day = 1
            
            for day in route.days:
                for rwp in day.waypoints:
                    d = haversine_km(wp.lat, wp.lon, rwp.lat, rwp.lon)
                    if d < min_dist:
                        min_dist = d
                        near_day = day.day_number
            
            candidates.append((wp, min_dist, near_day))
        
        # Sort by distance to route (closest first)
        candidates.sort(key=lambda x: x[1])
        
        # Pick top N, spread across days if possible
        alternatives = []
        day_counts = {1: 0, 2: 0, 3: 0, 4: 0}
        max_per_day = (num_alternatives // 4) + 1  # ~3 per day
        
        # First pass: balanced distribution
        for wp, dist, near_day in candidates:
            if len(alternatives) >= num_alternatives:
                break
            if day_counts.get(near_day, 0) < max_per_day:
                wp.description = f"ALT Giorno {near_day} — {wp.description}" if wp.description else f"WP alternativo Giorno {near_day}"
                alternatives.append(wp)
                day_counts[near_day] = day_counts.get(near_day, 0) + 1
        
        # Second pass: fill remaining slots regardless of day
        if len(alternatives) < num_alternatives:
            alt_ids = set(wp.id for wp in alternatives)
            for wp, dist, near_day in candidates:
                if len(alternatives) >= num_alternatives:
                    break
                if wp.id not in alt_ids:
                    wp.description = f"ALT Giorno {near_day} — {wp.description}" if wp.description else f"WP alternativo Giorno {near_day}"
                    alternatives.append(wp)
        
        route.alternatives = alternatives
        print(f"[Optimizer] Selezionati {len(alternatives)} WP alternativi")
    
    def _recalculate_day(self, day: DaySegment):
        """Recalculate day statistics after WP reordering."""
        if not day.waypoints:
            return
        
        day.segments = []
        day.total_km = 0
        day.total_hours = 0
        day.total_elevation_gain = 0
        day.total_elevation_loss = 0
        day.unpaved_segments = 0
        day.unpaved_km = 0
        
        for i in range(len(day.waypoints) - 1):
            wp1 = day.waypoints[i]
            wp2 = day.waypoints[i + 1]
            
            dist = self.routing.estimate_distance(wp1, wp2)
            hours = dist / AVERAGE_SPEED_KMH
            
            segment = RouteSegment(
                from_wp=wp1, to_wp=wp2,
                distance_km=dist,
                duration_hours=hours,
                is_unpaved=wp2.is_unpaved,
                geometry=[[wp1.lat, wp1.lon], [wp2.lat, wp2.lon]],
                air_distance_km=haversine_km(wp1.lat, wp1.lon, wp2.lat, wp2.lon),
                road_distance_km=dist,
            )
            day.segments.append(segment)
            day.total_km += dist
            day.total_hours += hours
            
            elev_diff = wp2.elevation - wp1.elevation
            if elev_diff > 0:
                day.total_elevation_gain += elev_diff
            else:
                day.total_elevation_loss += abs(elev_diff)
            
            if segment.is_unpaved:
                day.unpaved_segments += 1
                day.unpaved_km += dist
    
    def _recalculate_with_osrm(self, route: Route):
        """Recalculate all segments using OSRM for accurate distances."""
        self._progress("Calcolo distanze OSRM...", 90)
        
        total_segments = sum(len(d.segments) for d in route.days)
        done = 0
        
        for day in route.days:
            day.total_km = 0
            day.total_hours = 0
            
            for seg in day.segments:
                route_info = self.routing.get_route(seg.from_wp, seg.to_wp)
                seg.distance_km = route_info['distance_km']
                seg.duration_hours = route_info['duration_hours']
                seg.geometry = route_info['geometry']
                seg.road_distance_km = route_info['distance_km']
                
                day.total_km += seg.distance_km
                day.total_hours += seg.duration_hours
                
                done += 1
                pct = 90 + int(10 * done / total_segments)
                self._progress(f"OSRM: {done}/{total_segments} segmenti", pct)
        
        self.routing.save()


def generate_gpx_export(route: Route, filename: str = "percorso_centopassi.gpx"):
    """Export route to GPX file with waypoints and track."""
    import xml.etree.ElementTree as ET
    from datetime import datetime
    
    gpx = ET.Element('gpx', {
        'xmlns': 'http://www.topografix.com/GPX/1/1',
        'version': '1.1',
        'creator': 'Centopassi Route Planner',
    })
    
    # Metadata
    metadata = ET.SubElement(gpx, 'metadata')
    name = ET.SubElement(metadata, 'name')
    name.text = f"Centopassi 2026 - Percorso Ottimizzato"
    time_elem = ET.SubElement(metadata, 'time')
    time_elem.text = datetime.now().isoformat()
    
    # Waypoints
    for day in route.days:
        for i, wp in enumerate(day.waypoints):
            wpt = ET.SubElement(gpx, 'wpt', {
                'lat': str(wp.lat),
                'lon': str(wp.lon),
            })
            ele = ET.SubElement(wpt, 'ele')
            ele.text = str(wp.elevation)
            wp_name = ET.SubElement(wpt, 'name')
            wp_name.text = wp.display_name
            desc = ET.SubElement(wpt, 'desc')
            desc.text = f"Giorno {day.day_number} - {wp.description}"
            sym = ET.SubElement(wpt, 'sym')
            if wp.is_golden_point:
                sym.text = 'Flag, Red'
            elif wp.is_unpaved:
                sym.text = 'Flag, Orange'
            else:
                sym.text = 'Flag, Blue'
    
    # Track for each day
    for day in route.days:
        trk = ET.SubElement(gpx, 'trk')
        trk_name = ET.SubElement(trk, 'name')
        trk_name.text = f"Giorno {day.day_number} ({day.total_km:.0f} km)"
        trkseg = ET.SubElement(trk, 'trkseg')
        
        for seg in day.segments:
            for point in seg.geometry:
                trkpt = ET.SubElement(trkseg, 'trkpt', {
                    'lat': str(point[0]),
                    'lon': str(point[1]),
                })
    
    # Alternative waypoints
    for wp in route.alternatives:
        wpt = ET.SubElement(gpx, 'wpt', {
            'lat': str(wp.lat),
            'lon': str(wp.lon),
        })
        ele = ET.SubElement(wpt, 'ele')
        ele.text = str(wp.elevation)
        wp_name = ET.SubElement(wpt, 'name')
        wp_name.text = f"ALT {wp.number}"
        desc = ET.SubElement(wpt, 'desc')
        desc.text = f"ALTERNATIVO - {wp.description}"
        sym = ET.SubElement(wpt, 'sym')
        sym.text = 'Flag, Green'
    
    tree = ET.ElementTree(gpx)
    ET.indent(tree, space="  ")
    tree.write(filename, encoding='utf-8', xml_declaration=True)
    return filename
