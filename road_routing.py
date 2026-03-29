"""
Centopassi Route Planner - Professional Road Routing Module
Handles batch routing computation for full GPX tracks.
Validates route compliance against contest rules.
Integrates Claude AI for live road condition checks.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Optional

from models import Route, DaySegment, RouteSegment, haversine_km
from routing_service import RoutingService
from config import AVERAGE_SPEED_KMH, FORBIDDEN_ROAD_KEYWORDS


class RoadRouter:
    """
    Computes complete, road-following routing for every segment.

    Workflow:
      1. compute_full_routing(route)  → updates all seg.geometry with full polylines
      2. validate_compliance(route)   → checks for forbidden roads in each segment
      3. get_routing_stats(route)     → quality report for UI display
      4. claude_road_check(route)     → live road conditions via Claude AI
    """

    def __init__(self,
                 routing_service: RoutingService,
                 progress_cb: Optional[Callable[[str, int], None]] = None):
        self.routing      = routing_service
        self._progress_cb = progress_cb

    # ── Progress helper ───────────────────────────────────────

    def _progress(self, msg: str, pct: int):
        if self._progress_cb:
            self._progress_cb(msg, pct)
        try:
            print(f"[RoadRouter] {pct:3d}% - {msg}")
        except UnicodeEncodeError:
            print(f"[RoadRouter] {pct:3d}%")

    # ── Main: compute full road geometry ──────────────────────

    def compute_full_routing(self, route: Route) -> dict:
        """
        Query OSRM/GraphHopper for every segment and store full polylines.
        Returns a stats dict suitable for the frontend.
        """
        all_segments = [(day, seg)
                        for day in route.days
                        for seg in day.segments]
        total = len(all_segments)
        if total == 0:
            return self._empty_stats()

        done = failed = violations_total = 0

        self._progress("Avvio routing professionale…", 2)

        for day, seg in all_segments:
            pct = 5 + int(90 * done / total)
            self._progress(
                f"G{day.day_number}: WP{seg.from_wp.number}->{seg.to_wp.number}",
                pct
            )

            try:
                result = self.routing.get_route_with_compliance(
                    seg.from_wp, seg.to_wp
                )

                if result['source'] != 'estimate':
                    seg.geometry       = result['geometry']
                    seg.distance_km    = result['distance_km']
                    seg.duration_hours = result['duration_hours']
                    seg.road_distance_km = result['distance_km']
                    seg.road_violations  = result.get('road_violations', [])
                else:
                    failed += 1
                    seg.road_violations = []

                if result.get('road_violations'):
                    violations_total += len(result['road_violations'])

            except Exception as e:
                print(f"[RoadRouter] Segment error: {e}")
                failed += 1
                seg.road_violations = []

            done += 1

        # Recompute per-day totals from updated segments
        for day in route.days:
            day.total_km    = sum(s.distance_km    for s in day.segments)
            day.total_hours = sum(s.duration_hours for s in day.segments)
            day.total_elevation_gain = sum(
                max(s.to_wp.elevation - s.from_wp.elevation, 0)
                for s in day.segments
            )
            day.total_elevation_loss = sum(
                max(s.from_wp.elevation - s.to_wp.elevation, 0)
                for s in day.segments
            )

        self.routing.save()
        self._progress("Routing completato.", 100)

        quality = int(100 * (done - failed) / max(done, 1))
        return {
            'done':             done,
            'total':            total,
            'failed':           failed,
            'quality_pct':      quality,
            'violations_total': violations_total,
            'routed_segments':  done - failed,
            'estimated_segments': failed,
        }

    # ── Compliance validation ─────────────────────────────────

    def validate_compliance(self, route: Route) -> dict:
        """
        Check all segments for forbidden road types.
        Returns a compliance report with per-day breakdown.
        """
        report = {
            'compliant':   True,
            'violations':  [],
            'days':        {},
        }

        for day in route.days:
            day_violations = []
            for seg in day.segments:
                viols = getattr(seg, 'road_violations', [])
                for v in viols:
                    entry = {
                        'from_wp': seg.from_wp.number,
                        'to_wp':   seg.to_wp.number,
                        'road':    v,
                        'day':     day.day_number,
                    }
                    day_violations.append(entry)
                    report['violations'].append(entry)

            report['days'][day.day_number] = {
                'violations': day_violations,
                'compliant':  len(day_violations) == 0,
            }

        if report['violations']:
            report['compliant'] = False

        return report

    # ── Routing quality stats ─────────────────────────────────

    def get_routing_stats(self, route: Route) -> dict:
        """
        Return a quality report on current route geometry.
        Used by the frontend to show routing status.
        """
        total = routed = estimated = total_pts = viol_count = 0

        for day in route.days:
            for seg in day.segments:
                total += 1
                pts = len(seg.geometry)
                total_pts += pts
                if pts > 2:
                    routed += 1
                else:
                    estimated += 1
                viol_count += len(getattr(seg, 'road_violations', []))

        quality = int(100 * routed / max(total, 1))
        avg_pts = int(total_pts  / max(total, 1))

        return {
            'total_segments':       total,
            'routed_segments':      routed,
            'estimated_segments':   estimated,
            'quality_pct':          quality,
            'avg_points_per_segment': avg_pts,
            'violations_count':     viol_count,
            'needs_routing':        estimated > 0,
            'status': (
                'optimal'   if quality == 100 else
                'good'      if quality >= 75  else
                'partial'   if quality >= 40  else
                'estimated'
            ),
        }

    # ── Claude AI road condition check ────────────────────────

    def claude_road_check(self, route: Route) -> dict:
        """
        Use Claude AI to verify current road conditions for the route regions
        and specific mountain passes.
        Returns {'warnings': [...], 'ok': bool}
        """
        try:
            from road_intelligence import RoadIntelligence
            ri = RoadIntelligence()
            if not ri.enabled:
                return {'warnings': [], 'ok': True,
                        'error': 'ANTHROPIC_API_KEY non configurata'}

            # Collect unique regions and pass names
            regions = list({
                loc
                for day in route.days
                for wp in day.waypoints
                for loc in [wp.province, wp.city, wp.name]
                if loc
            })

            # Also include actual road step names found during routing
            step_names = list({
                name
                for day in route.days
                for seg in day.segments
                for name in getattr(seg, 'step_names', [])[:3]   # first 3 steps per seg
                if name and len(name) > 3
            })[:30]   # cap at 30 names

            warnings = ri.check_road_closures(regions, extra_context=step_names)

            # Attach warnings to affected waypoints
            if warnings:
                affected = ri.get_affected_waypoints(route.all_waypoints)
                for day in route.days:
                    day.road_warnings = []
                    for wp in day.waypoints:
                        if wp.id in affected:
                            wp.road_warnings = affected[wp.id]
                            day.road_warnings.extend(affected[wp.id])
                route.road_warnings = warnings

            return {
                'warnings': warnings,
                'ok':       len(warnings) == 0,
                'regions_checked': len(regions),
            }

        except Exception as e:
            return {'warnings': [], 'ok': True, 'error': str(e)}

    # ── Helpers ───────────────────────────────────────────────

    @staticmethod
    def _empty_stats() -> dict:
        return {
            'done': 0, 'total': 0, 'failed': 0,
            'quality_pct': 100, 'violations_total': 0,
            'routed_segments': 0, 'estimated_segments': 0,
        }
