"""
Centopassi Route Planner - Route Optimization Engine
Selects 100 waypoints and organizes them into a 4-day route.
"""
import io
import math
import random
import threading
from typing import Optional
from models import Waypoint, RouteSegment, DaySegment, Route, haversine_km
from routing_service import RoutingService
from config import (
    TARGET_WAYPOINTS, MAX_KM_PER_DAY_LIST, MAX_KM_HARD_LIMIT_PER_DAY, MIN_TOTAL_KM,
    MAX_START_DISTANCE_KM, MIN_START_DISTANCE_KM,
    DRIVING_HOURS, DAY1_START_TIME, OTHER_DAYS_START_TIME,
    REST_START_TIME, FINISH_WINDOW_START, AVERAGE_SPEED_KMH, MAX_ALLOWED_AVG_SPEED_KMH,
    BONUS_100TH_DISTANCE_KM, HAVERSINE_ROAD_FACTOR,
    BRIDGE_MIN_KM, BRIDGE_MAX_KM,
    NUM_ALTERNATIVES, OSRM_RATE_LIMIT_SEC,
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
        self.road_intelligence = None  # Set externally for road closure checks
        self._cancelled = threading.Event()
    
    def set_progress_callback(self, callback):
        """Set a callback function(message, percent) for progress updates."""
        self._progress_callback = callback
    
    def cancel(self):
        """Signal the optimizer to stop as soon as possible."""
        self._cancelled.set()

    def _check_cancelled(self):
        """Raise if cancellation was requested."""
        if self._cancelled.is_set():
            raise RuntimeError("Ottimizzazione annullata.")

    def _progress(self, message: str, percent: int = 0):
        """Report progress."""
        self._check_cancelled()
        if self._progress_callback:
            self._progress_callback(message, percent)
        else:
            print(f"[Optimizer] {message} ({percent}%)")
    
    def optimize(self, finish_lat: float, finish_lon: float,
                 finish_name: str = "Traguardo",
                 use_osrm_routing: bool = False,
                 unpaved_mode: str = UNPAVED_LIMIT,
                 max_unpaved: int = 10,
                 start_lat: float = None,
                 start_lon: float = None,
                 start_name: str = None) -> Route:
        """
        Main optimization: find best route of 100 waypoints in 4 days.

        Args:
            finish_lat, finish_lon: arrival/finish point coordinates
            finish_name: name of finish location
            use_osrm_routing: if True, use OSRM for exact distances (slower)
            unpaved_mode: 'allow', 'limit', or 'exclude'
            max_unpaved: max unpaved WPs when mode='limit'
            start_lat, start_lon: optional custom start coordinates (user-defined)
            start_name: optional label for the custom start point

        Returns:
            Route object with 4 DaySegments
        """
        self._progress("Inizializzazione ottimizzazione...", 0)

        finish_point = {'lat': finish_lat, 'lon': finish_lon, 'name': finish_name}

        # ── Step 1: Determine start candidates ─────────────────────────
        if start_lat is not None and start_lon is not None:
            # User provided a custom start → find the nearest valid waypoint to it
            self._progress("Ricerca waypoint più vicino al punto di partenza indicato...", 5)
            custom_start_wp = self._find_nearest_waypoint_to_start(
                start_lat, start_lon, finish_lat, finish_lon, start_name
            )
            if custom_start_wp is None:
                raise ValueError(
                    "Il punto di partenza indicato non è valido: nessun waypoint nelle vicinanze "
                    f"rispetta le regole (distanza dal traguardo ≤ {MAX_START_DISTANCE_KM} km, "
                    f"distanza dal 1° passo ≥ {MIN_START_DISTANCE_KM} km)."
                )
            candidates_to_try = [custom_start_wp]
            self._progress(
                f"Partenza fissata: WP {custom_start_wp.number} "
                f"({custom_start_wp.city or custom_start_wp.description[:30]}) "
                f"— {haversine_km(start_lat, start_lon, custom_start_wp.lat, custom_start_wp.lon):.1f} km dal punto indicato",
                10
            )
        else:
            # Auto-select start from valid candidates
            self._progress("Ricerca punti di partenza validi...", 5)
            start_candidates = self._find_start_candidates(finish_lat, finish_lon)
            if not start_candidates:
                raise ValueError(
                    f"Nessun waypoint trovato entro {MAX_START_DISTANCE_KM} km dal traguardo. "
                    f"Verificare le coordinate del traguardo."
                )
            self._progress(f"Trovati {len(start_candidates)} punti di partenza candidati", 10)
            start_candidates.sort(key=lambda wp: -wp.distance_to(
                Waypoint(id=-1, name="finish", lat=finish_lat, lon=finish_lon)
            ))
            candidates_to_try = self._select_diverse_starts(
                start_candidates, finish_lat, finish_lon, max_tries=5
            )

        # ── Step 2: Try start candidates and pick the best route ────────
        best_route = None
        best_score = -1
        best_fallback = None       # Best partial route (< 100 WP or < 1600 km)
        best_fallback_wps = 0

        for idx, start_wp in enumerate(candidates_to_try):
            pct = 10 + int(70 * idx / len(candidates_to_try))
            self._progress(
                f"Tentativo {idx+1}/{len(candidates_to_try)}: "
                f"partenza da WP {start_wp.number} ({start_wp.city or start_wp.description[:30]})",
                pct
            )

            try:
                # Always build with haversine (fast); OSRM recalc happens only on best route
                route = self._build_route(
                    start_wp, finish_lat, finish_lon, finish_name,
                    use_osrm=False,
                    unpaved_mode=unpaved_mode,
                    max_unpaved=max_unpaved,
                )

                if not route or not route.days:
                    self._progress(f"  → Nessun percorso generato", pct)
                    continue

                n_wps = route.total_waypoints
                r_km = route.total_km

                if n_wps >= TARGET_WAYPOINTS and r_km >= MIN_TOTAL_KM:
                    score = self._evaluate_route(route)
                    if score > best_score:
                        best_score = score
                        best_route = route
                        self._progress(
                            f"  → Percorso trovato: {r_km:.0f} km, "
                            f"{n_wps} WP, score={score:.0f}",
                            pct
                        )
                else:
                    self._progress(
                        f"  → Percorso parziale: {n_wps} WP, {r_km:.0f} km",
                        pct
                    )
                    # Keep best partial route as fallback
                    if n_wps > best_fallback_wps:
                        best_fallback_wps = n_wps
                        best_fallback = route
            except Exception as e:
                self._progress(f"  → Errore: {e}", pct)
                continue

        if not best_route and best_fallback:
            n = best_fallback.total_waypoints
            km = best_fallback.total_km
            self._progress(
                f"Nessun percorso perfetto trovato. "
                f"Uso il migliore disponibile: {n} WP, {km:.0f} km",
                80
            )
            best_route = best_fallback
        
        if not best_route:
            raise ValueError(
                "Impossibile costruire un percorso valido con i vincoli dati. "
                "Provare con un punto di arrivo diverso o consentire più sterrati."
            )
        
        self._progress("Ottimizzazione percorso con 2-opt...", 85)
        best_route = self._optimize_2opt(best_route)
        
        self._progress("Selezione WP alternativi...", 90)
        self._select_alternatives(best_route)
        
        # Road intelligence: check for closures/hazards
        if self.road_intelligence and self.road_intelligence.enabled:
            self._progress("Verifica chiusure stradali (AI)...", 91)
            regions = self._extract_regions(best_route)
            warnings = self.road_intelligence.check_road_closures(regions)
            best_route.road_warnings = warnings

            if warnings:
                affected = self.road_intelligence.get_affected_waypoints(best_route.all_waypoints)
                for day in best_route.days:
                    day_warnings = []
                    for wp in day.waypoints:
                        if wp.id in affected:
                            wp.road_warnings = affected[wp.id]
                            day_warnings.extend(affected[wp.id])
                    day.road_warnings = day_warnings
                self._progress(f"Trovati {len(warnings)} avvisi stradali", 92)

        if use_osrm_routing:
            self._progress("Ricalcolo distanze stradali con OSRM (può richiedere qualche minuto)...", 85)
            self._recalculate_with_osrm(best_route)
        
        self._progress(
            f"Percorso ottimizzato: {best_route.total_km:.0f} km, "
            f"{best_route.total_waypoints} WP, "
            f"{best_route.total_golden_points} GP, "
            f"{len(best_route.alternatives)} alternativi",
            100
        )

        # ── Step final: Compliance check velocità media 47 km/h ─────────
        best_route.compliance = self._check_speed_compliance(best_route)

        return best_route

    def _find_nearest_waypoint_to_start(self, start_lat: float, start_lon: float,
                                         finish_lat: float, finish_lon: float,
                                         start_name: str = None) -> Optional['Waypoint']:
        """
        Trova il waypoint più vicino alle coordinate di partenza indicate dall'utente,
        verificando che rispetti le regole del regolamento:
        - distanza dal traguardo ≤ MAX_START_DISTANCE_KM (450 km)
        - distanza minima ≥ MIN_START_DISTANCE_KM (15 km) dal traguardo
        Restituisce il waypoint valido più vicino (entro 100 km dal punto indicato).
        """
        SEARCH_RADIUS_KM = 100  # Raggio di ricerca attorno al punto indicato
        candidates = []
        for wp in self.all_waypoints:
            dist_to_user_point = haversine_km(wp.lat, wp.lon, start_lat, start_lon)
            dist_to_finish = haversine_km(wp.lat, wp.lon, finish_lat, finish_lon)
            if (dist_to_user_point <= SEARCH_RADIUS_KM and
                    dist_to_finish <= MAX_START_DISTANCE_KM and
                    dist_to_finish >= MIN_START_DISTANCE_KM):
                candidates.append((dist_to_user_point, wp))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        nearest_wp = candidates[0][1]
        # Sovrascrive il nome del punto di partenza se fornito
        if start_name:
            nearest_wp._custom_start_label = start_name
        return nearest_wp

    def _check_speed_compliance(self, route: 'Route') -> dict:
        """
        Verifica la compliance con la regola della velocità media massima di 47 km/h.

        Regola 6.2: penalità = 1 punto × (km/h_eccesso) per ogni km/h oltre il limite.
        La velocità media giornaliera = km_totali_giorno / ore_guida_disponibili.

        Returns:
            dict con per ogni giorno lo stato di compliance e l'eventuale penalità stimata.
        """
        result = {
            'ok': True,
            'max_avg_speed_kmh': MAX_ALLOWED_AVG_SPEED_KMH,
            'days': [],
            'total_penalty_points': 0,
            'warnings': [],
        }
        for i, day in enumerate(route.days):
            driving_hours = DRIVING_HOURS[i]
            day_km = day.total_km
            avg_speed = day_km / driving_hours if driving_hours > 0 else 0
            excess_kmh = max(0.0, avg_speed - MAX_ALLOWED_AVG_SPEED_KMH)
            # Penalità stimata: 1pt × km/h eccesso (il regolamento dice "per ogni km/h oltre")
            penalty = int(excess_kmh)
            day_result = {
                'day': i + 1,
                'km': round(day_km, 1),
                'driving_hours': driving_hours,
                'avg_speed_kmh': round(avg_speed, 2),
                'excess_kmh': round(excess_kmh, 2),
                'penalty_points': penalty,
                'compliant': excess_kmh == 0,
            }
            result['days'].append(day_result)
            if excess_kmh > 0:
                result['ok'] = False
                result['total_penalty_points'] += penalty
                result['warnings'].append(
                    f"Giorno {i+1}: velocità media {avg_speed:.1f} km/h "
                    f"(limite {MAX_ALLOWED_AVG_SPEED_KMH} km/h) "
                    f"— eccesso {excess_kmh:.1f} km/h → penalità stimata {penalty} pt"
                )
        return result

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
            
            # Per-day km limit based on (driving_hours - breaks) * avg_speed
            max_km_today = MAX_KM_PER_DAY_LIST[day_num - 1]
            day_km = 0.0
            day_hours = 0.0
            day_wps = []
            day_segments = []

            # Day 1: include start waypoint as first WP
            # Days 2-4: find a bridge waypoint within BRIDGE_MIN/MAX_KM of previous day's last WP
            if day_num == 1:
                day_wps.append(start_wp)
            elif route.days and route.days[-1].waypoints:
                prev_last_wp = route.days[-1].waypoints[-1]
                bridge_wp = self._find_bridge_waypoint(
                    prev_last_wp, available, finish_lat, finish_lon,
                    unpaved_mode=unpaved_mode, max_unpaved=max_unpaved,
                    current_unpaved_count=unpaved_count,
                )
                if bridge_wp:
                    day_wps.append(bridge_wp)
                    available.discard(bridge_wp.id)
                    all_selected_ids.add(bridge_wp.id)
                    current_wp = bridge_wp
                    if bridge_wp.is_unpaved:
                        unpaved_count += 1
            
            # Add waypoints greedily
            # Calculate ideal km per WP to ensure we reach MIN_TOTAL_KM overall
            remaining_wps = TARGET_WAYPOINTS - (total_selected_count + len(day_wps))
            remaining_days = 4 - day_num + 1
            
            while (day_km < max_km_today * 0.92 and
                   day_hours < max_hours * 0.92 and
                   total_selected_count + len(day_wps) < TARGET_WAYPOINTS and
                   available):
                self._check_cancelled()
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
        
        if total_selected_count < TARGET_WAYPOINTS:
            self._progress(
                f"  ⚠ Percorso incompleto: {total_selected_count}/{TARGET_WAYPOINTS} WP, "
                f"{route.total_km:.0f} km, {len(available)} WP rimasti non raggiungibili",
                0
            )
        return route
    
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
    
    def _find_bridge_waypoint(self, last_wp: Waypoint, available: set,
                               finish_lat: float, finish_lon: float,
                               unpaved_mode: str = UNPAVED_LIMIT,
                               max_unpaved: int = 10,
                               current_unpaved_count: int = 0) -> Optional[Waypoint]:
        """
        Find the best waypoint within BRIDGE_MIN_KM to BRIDGE_MAX_KM (road distance)
        from last_wp to start the next day. Uses OSRM if available, else haversine estimate.
        """
        unpaved_budget_left = True
        if unpaved_mode == UNPAVED_EXCLUDE:
            unpaved_budget_left = False
        elif unpaved_mode == UNPAVED_LIMIT:
            unpaved_budget_left = current_unpaved_count < max_unpaved

        candidates = []
        for wp_id in available:
            wp = self.all_waypoints[wp_id]
            if wp.is_unpaved and not unpaved_budget_left:
                continue

            road_dist = self.routing.estimate_distance(last_wp, wp)
            if BRIDGE_MIN_KM <= road_dist <= BRIDGE_MAX_KM:
                # Score: prefer direction toward finish + golden point bonus
                wp_dist_to_finish = haversine_km(wp.lat, wp.lon, finish_lat, finish_lon)
                last_dist_to_finish = haversine_km(last_wp.lat, last_wp.lon, finish_lat, finish_lon)
                direction_score = (last_dist_to_finish - wp_dist_to_finish) * 0.1
                gp_bonus = 5.0 if wp.is_golden_point else 0.0
                unpaved_penalty = -5.0 if wp.is_unpaved else 0.0
                # Prefer mid-range bridge distance (ideal ~30 km)
                ideal_bridge = (BRIDGE_MIN_KM + BRIDGE_MAX_KM) / 2
                dist_score = -abs(road_dist - ideal_bridge) * 0.5
                score = direction_score + gp_bonus + unpaved_penalty + dist_score
                candidates.append((wp, score))

        if not candidates:
            # Fallback: relax to nearest available within 2x range
            for wp_id in available:
                wp = self.all_waypoints[wp_id]
                if wp.is_unpaved and not unpaved_budget_left:
                    continue
                road_dist = self.routing.estimate_distance(last_wp, wp)
                if road_dist <= BRIDGE_MAX_KM * 2:
                    candidates.append((wp, -road_dist))
            if not candidates:
                return None

        candidates.sort(key=lambda x: -x[1])
        return candidates[0][0]

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
            
            # Penalize any day exceeding its dynamic km limit
            for day_idx, km in enumerate(km_values):
                day_limit = MAX_KM_PER_DAY_LIST[day_idx] if day_idx < len(MAX_KM_PER_DAY_LIST) else MAX_KM_PER_DAY_LIST[-1]
                if km > day_limit:
                    score -= (km - day_limit) * 200

            # Penalize bridging violations (days 2-4: first WP must be 20-40 km from prev day's last)
            for i in range(1, len(route.days)):
                prev_day = route.days[i - 1]
                curr_day = route.days[i]
                if prev_day.waypoints and curr_day.waypoints:
                    bridge_dist = self.routing.estimate_distance(
                        prev_day.waypoints[-1], curr_day.waypoints[0]
                    )
                    if bridge_dist < BRIDGE_MIN_KM or bridge_dist > BRIDGE_MAX_KM:
                        score -= 5000
        
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
        After 2-opt, ensure no day exceeds its dynamic km limit.
        Trims excess WPs from the end of an over-limit day
        and prepends them to the start of the next day.
        Also respects the bridging constraint (20-40 km between days).
        """
        for day_idx in range(len(route.days) - 1):
            day = route.days[day_idx]
            next_day = route.days[day_idx + 1]
            day_limit = MAX_KM_PER_DAY_LIST[day_idx] if day_idx < len(MAX_KM_PER_DAY_LIST) else MAX_KM_PER_DAY_LIST[-1]

            max_moves = len(day.waypoints)
            moves = 0
            while day.total_km > day_limit and len(day.waypoints) > 3 and moves < max_moves:
                moves += 1
                # Remove last WP from this day, add to start of next day
                moved_wp = day.waypoints.pop()
                next_day.waypoints.insert(0, moved_wp)

                # Recalculate both days
                self._recalculate_day(day)
                self._recalculate_day(next_day)

                # Check bridging constraint: if violated, stop moving
                if day.waypoints and next_day.waypoints:
                    bridge_dist = self.routing.estimate_distance(
                        day.waypoints[-1], next_day.waypoints[0]
                    )
                    if bridge_dist < BRIDGE_MIN_KM or bridge_dist > BRIDGE_MAX_KM:
                        break
    
    def _select_alternatives(self, route: Route, num_alternatives: int = NUM_ALTERNATIVES):
        """
        Select alternative/backup waypoints near the route.

        Distribution rule: inversely proportional to days remaining
        (= proportional to day_num, triangular weighting).

        For num_alternatives=10 and 4 days:
          triangular(4) = 4*5/2 = 10
          day 1 quota = round(10 * 1/10) = 1
          day 2 quota = round(10 * 2/10) = 2
          day 3 quota = round(10 * 3/10) = 3
          day 4 quota = round(10 * 4/10) = 4   → total = 10

        Works for any number of days and any num_alternatives.
        """
        n_days = len(route.days)
        tri = n_days * (n_days + 1) // 2        # triangular number = sum(1..n_days)

        # Compute per-day quota; ensure sum == num_alternatives
        raw = [num_alternatives * d / tri for d in range(1, n_days + 1)]
        quotas = [max(0, round(v)) for v in raw]
        # Fix rounding drift: add/remove from the last (heaviest) day
        diff = num_alternatives - sum(quotas)
        quotas[-1] += diff

        max_per_day = {day.day_number: quotas[idx] for idx, day in enumerate(route.days)}
        day_counts  = {day.day_number: 0 for day in route.days}

        print(f"[Optimizer] Alternative quota per giorno: "
              + ", ".join(f"G{d}:{max_per_day[d]}" for d in sorted(max_per_day)))

        # Collect all selected WP IDs
        selected_ids = {wp.id for day in route.days for wp in day.waypoints}

        # For each non-selected WP, find its minimum distance to any route WP
        candidates = []
        for wp in self.all_waypoints:
            if wp.id in selected_ids:
                continue
            min_dist = float('inf')
            near_day = route.days[0].day_number
            for day in route.days:
                for rwp in day.waypoints:
                    d = haversine_km(wp.lat, wp.lon, rwp.lat, rwp.lon)
                    if d < min_dist:
                        min_dist = d
                        near_day = day.day_number
            candidates.append((wp, min_dist, near_day))

        # Sort by proximity to route (closest first within each day bucket)
        candidates.sort(key=lambda x: x[1])

        alternatives = []

        # First pass: fill each day up to its quota (closest WPs first)
        for wp, dist, near_day in candidates:
            if len(alternatives) >= num_alternatives:
                break
            quota = max_per_day.get(near_day, 0)
            if day_counts.get(near_day, 0) < quota:
                wp.description = (
                    f"ALT Giorno {near_day} — {wp.description}"
                    if wp.description else f"WP alternativo Giorno {near_day}"
                )
                alternatives.append(wp)
                day_counts[near_day] = day_counts.get(near_day, 0) + 1

        # Second pass: fill remaining slots (e.g. sparse days) with closest unused WPs
        if len(alternatives) < num_alternatives:
            alt_ids = {wp.id for wp in alternatives}
            for wp, dist, near_day in candidates:
                if len(alternatives) >= num_alternatives:
                    break
                if wp.id not in alt_ids:
                    wp.description = (
                        f"ALT Giorno {near_day} — {wp.description}"
                        if wp.description else f"WP alternativo Giorno {near_day}"
                    )
                    alternatives.append(wp)
                    alt_ids.add(wp.id)

        route.alternatives = alternatives
        dist_summary = {d: sum(1 for wp in alternatives
                               if f"Giorno {d}" in (wp.description or ""))
                        for d in range(1, n_days + 1)}
        print(f"[Optimizer] Selezionati {len(alternatives)} WP alternativi: "
              + ", ".join(f"G{d}:{dist_summary[d]}" for d in sorted(dist_summary)))
    
    def _extract_regions(self, route: Route) -> list[str]:
        """Extract unique region/province names from route waypoints."""
        regions = set()
        for wp in route.all_waypoints:
            if wp.province:
                regions.add(wp.province)
        return sorted(regions)

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
        total_segments = sum(len(d.segments) for d in route.days)
        cached = sum(1 for d in route.days for s in d.segments
                     if self.routing._cache_key(s.from_wp.lat, s.from_wp.lon,
                                                s.to_wp.lat, s.to_wp.lon) in self.routing.cache)
        to_fetch = total_segments - cached
        eta_sec = to_fetch * OSRM_RATE_LIMIT_SEC if to_fetch > 0 else 0
        self._progress(
            f"OSRM: {total_segments} segmenti ({cached} in cache, "
            f"~{int(eta_sec)}s per i restanti {to_fetch})...", 85
        )
        done = 0

        for day in route.days:
            day.total_km = 0
            day.total_hours = 0

            for seg in day.segments:
                self._check_cancelled()
                route_info = self.routing.get_route(seg.from_wp, seg.to_wp)
                seg.distance_km = route_info['distance_km']
                seg.duration_hours = route_info['duration_hours']
                seg.geometry = route_info['geometry']
                seg.road_distance_km = route_info['distance_km']

                day.total_km += seg.distance_km
                day.total_hours += seg.duration_hours

                done += 1
                pct = 85 + int(14 * done / total_segments)
                self._progress(f"OSRM: {done}/{total_segments} segmenti calcolati", pct)
        
        self.routing.save()


def generate_gpx_day(route: Route, day_num: int) -> bytes:
    """
    Generate GPX bytes for a single day.
    Waypoint naming: WP{number} G{day} {pos_in_day}/{total_global}
    e.g.  "WP001 G1 05/100"
    """
    import xml.etree.ElementTree as ET
    from datetime import datetime

    day_idx = day_num - 1
    if day_idx < 0 or day_idx >= len(route.days):
        raise ValueError(f"Giorno {day_num} non esiste")

    day = route.days[day_idx]
    total_global = route.total_waypoints

    # Global position counter: sum of WPs in previous days
    global_offset = sum(len(route.days[i].waypoints) for i in range(day_idx))

    gpx = ET.Element('gpx', {
        'xmlns': 'http://www.topografix.com/GPX/1/1',
        'version': '1.1',
        'creator': 'Centopassi Route Planner',
    })

    metadata = ET.SubElement(gpx, 'metadata')
    name_el = ET.SubElement(metadata, 'name')
    name_el.text = f"Centopassi 2026 - Giorno {day_num} ({day.total_km:.0f} km)"
    time_el = ET.SubElement(metadata, 'time')
    time_el.text = datetime.now().isoformat()

    # Waypoints with full naming
    for seq_in_day, wp in enumerate(day.waypoints, start=1):
        global_pos = global_offset + seq_in_day
        wpt = ET.SubElement(gpx, 'wpt', {'lat': str(wp.lat), 'lon': str(wp.lon)})
        ET.SubElement(wpt, 'ele').text = str(wp.elevation)
        ET.SubElement(wpt, 'name').text = f"WP{wp.number} G{day_num} {seq_in_day:02d}/{total_global}"
        ET.SubElement(wpt, 'desc').text = (
            f"Giorno {day_num} | Pos {seq_in_day}/{len(day.waypoints)} | "
            f"Gara {global_pos}/{total_global}"
            + (f" | {wp.city}" if wp.city else "")
            + (f" ({wp.province})" if wp.province else "")
            + (f" | {wp.description}" if wp.description else "")
        )
        sym = ET.SubElement(wpt, 'sym')
        if wp.is_golden_point:
            sym.text = 'Flag, Red'
        elif wp.is_unpaved:
            sym.text = 'Flag, Orange'
        else:
            sym.text = 'Flag, Blue'

    # Track
    trk = ET.SubElement(gpx, 'trk')
    ET.SubElement(trk, 'name').text = f"Giorno {day_num} - {day.start_time}/{day.end_time} - {day.total_km:.0f} km"
    trkseg = ET.SubElement(trk, 'trkseg')
    for seg in day.segments:
        for point in seg.geometry:
            ET.SubElement(trkseg, 'trkpt', {'lat': str(point[0]), 'lon': str(point[1])})

    tree = ET.ElementTree(gpx)
    ET.indent(tree, space="  ")
    buf = io.BytesIO()
    tree.write(buf, encoding='utf-8', xml_declaration=True)
    return buf.getvalue()


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
