"""
Centopassi Route Planner - AI Day Optimizer
Uses Claude AI to intelligently reorder the waypoints of a single day
for the best possible route: minimal distance, elevation strategy,
no backtracking, compliance with all contest rules.
"""
from __future__ import annotations

import json
import math
import os
from typing import Optional, Callable

from models import DaySegment, Route, Waypoint, haversine_km
from config import (
    AVERAGE_SPEED_KMH, BREAK_TIME_HOURS, MAX_KM_PER_DAY_LIST,
    BRIDGE_MIN_KM, BRIDGE_MAX_KM, ROAD_INTELLIGENCE_MODEL,
    DRIVING_HOURS, FORBIDDEN_ROAD_KEYWORDS,
)

_ANTHROPIC_KEY_ENV = 'ANTHROPIC_API_KEY'


def _build_distance_matrix(waypoints: list[Waypoint]) -> list[list[float]]:
    """Build haversine distance matrix (km) for the list of waypoints."""
    n = len(waypoints)
    mat = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = haversine_km(waypoints[i].lat, waypoints[i].lon,
                             waypoints[j].lat, waypoints[j].lon)
            mat[i][j] = mat[j][i] = round(d, 1)
    return mat


def _greedy_order(waypoints: list[Waypoint],
                  fixed_first_idx: int = 0) -> list[int]:
    """
    Nearest-neighbor greedy order starting from fixed_first_idx.
    Used as fallback if Claude fails.
    """
    n = len(waypoints)
    visited = [False] * n
    order = [fixed_first_idx]
    visited[fixed_first_idx] = True
    for _ in range(n - 1):
        current = order[-1]
        best_dist = float('inf')
        best_j = -1
        for j in range(n):
            if not visited[j]:
                d = haversine_km(waypoints[current].lat, waypoints[current].lon,
                                 waypoints[j].lat, waypoints[j].lon)
                if d < best_dist:
                    best_dist = d
                    best_j = j
        if best_j >= 0:
            order.append(best_j)
            visited[best_j] = True
    return order


def _two_opt(waypoints: list[Waypoint], order: list[int]) -> list[int]:
    """Apply 2-opt improvement on a given order, keeping first element fixed."""
    n = len(order)
    if n < 4:
        return order
    improved = True
    while improved:
        improved = False
        for i in range(1, n - 2):
            for j in range(i + 2, n):
                # Reverse segment order[i..j]
                d_before = (
                    haversine_km(waypoints[order[i-1]].lat, waypoints[order[i-1]].lon,
                                 waypoints[order[i]].lat,   waypoints[order[i]].lon) +
                    haversine_km(waypoints[order[j-1]].lat, waypoints[order[j-1]].lon,
                                 waypoints[order[j % n]].lat, waypoints[order[j % n]].lon)
                    if j < n else
                    haversine_km(waypoints[order[i-1]].lat, waypoints[order[i-1]].lon,
                                 waypoints[order[i]].lat,   waypoints[order[i]].lon)
                )
                rev = order[:i] + order[i:j+1][::-1] + order[j+1:]
                d_after = (
                    haversine_km(waypoints[rev[i-1]].lat, waypoints[rev[i-1]].lon,
                                 waypoints[rev[i]].lat,   waypoints[rev[i]].lon) +
                    haversine_km(waypoints[rev[j-1]].lat, waypoints[rev[j-1]].lon,
                                 waypoints[rev[j % n]].lat, waypoints[rev[j % n]].lon)
                    if j < n else
                    haversine_km(waypoints[rev[i-1]].lat, waypoints[rev[i-1]].lon,
                                 waypoints[rev[i]].lat,   waypoints[rev[i]].lon)
                )
                if d_after < d_before - 0.01:
                    order = rev
                    improved = True
    return order


class AIDayOptimizer:
    """
    Optimizes the waypoint order of a single day using Claude AI.

    Strategy:
    1. Extract current WP list for the day
    2. Build a compact geographic context (coordinates + elevation)
    3. Pre-compute distance matrix (haversine, fast)
    4. Call Claude with the full context and constraints
    5. Claude returns an optimized ordering (JSON array of WP indices)
    6. Validate and apply the ordering
    7. Return stats (before/after distance, improvement %)
    """

    def __init__(self, progress_cb: Optional[Callable[[str, int], None]] = None):
        self._progress_cb = progress_cb
        self._api_key = os.environ.get(_ANTHROPIC_KEY_ENV, '')

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def _progress(self, msg: str, pct: int):
        if self._progress_cb:
            self._progress_cb(msg, pct)
        try:
            print(f"[AIDayOptimizer] {pct:3d}% - {msg}")
        except UnicodeEncodeError:
            print(f"[AIDayOptimizer] {pct:3d}%")

    # ── Main entry point ──────────────────────────────────────

    def optimize_day(self,
                     route: Route,
                     day_num: int) -> dict:
        """
        Reorder waypoints of day_num using Claude AI.

        Returns:
            {
                'success': bool,
                'day_num': int,
                'before': { 'km': float, 'hours': float, 'wp_count': int },
                'after':  { 'km': float, 'hours': float, 'wp_count': int },
                'improvement_km': float,
                'improvement_pct': float,
                'method': 'claude' | '2opt' | 'unchanged',
                'reasoning': str,
                'error': str | None,
            }
        """
        day_idx = day_num - 1
        if day_idx < 0 or day_idx >= len(route.days):
            return {'success': False, 'error': f'Giorno {day_num} non esiste'}

        day = route.days[day_idx]
        wps = day.waypoints

        if len(wps) < 3:
            return {
                'success': True, 'day_num': day_num,
                'before': self._day_stats(day), 'after': self._day_stats(day),
                'improvement_km': 0.0, 'improvement_pct': 0.0,
                'method': 'unchanged', 'reasoning': 'Troppo pochi waypoint da ottimizzare',
                'error': None,
            }

        before_stats = self._day_stats(day)

        # Constraints for this day
        max_km    = MAX_KM_PER_DAY_LIST[day_idx] if day_idx < len(MAX_KM_PER_DAY_LIST) else 600.0
        driv_hrs  = DRIVING_HOURS[day_idx] if day_idx < len(DRIVING_HOURS) else 12.0

        # Bridge constraint: first WP of day must stay within 20-40km of prev day's last WP
        bridge_wp = None
        if day_num > 1 and route.days[day_idx - 1].waypoints:
            bridge_wp = route.days[day_idx - 1].waypoints[-1]

        # Pre-compute distance matrix
        self._progress("Calcolo matrice distanze...", 10)
        matrix = _build_distance_matrix(wps)

        # Current total (haversine)
        before_km = sum(matrix[i][i+1] for i in range(len(wps)-1))

        reasoning = ''
        new_order_indices = None
        method = 'unchanged'

        # Try Claude AI
        if self.enabled:
            self._progress("Invio percorso a Claude AI (con regole strade vietate)...", 25)
            result = self._call_claude(
                day_num=day_num,
                wps=wps,
                matrix=matrix,
                max_km=max_km,
                driv_hrs=driv_hrs,
                bridge_wp=bridge_wp,
                before_km=before_km,
            )
            if result.get('order'):
                new_order_indices = result['order']
                reasoning = result.get('reasoning', '')
                method = 'claude'
                self._progress("Risposta Claude ricevuta, applico 2-opt...", 70)
                # Refine with 2-opt keeping first WP fixed
                new_order_indices = _two_opt(wps, new_order_indices)
                method = 'claude+2opt'
            else:
                reasoning = result.get('error', 'Claude non ha restituito un ordine valido')
                self._progress(f"Claude fallback: {reasoning[:50]}...", 55)

        # Fallback: greedy + 2-opt
        if new_order_indices is None:
            self._progress("Greedy + 2-opt (fallback)...", 55)
            first_idx = self._find_bridge_first(wps, bridge_wp)
            new_order_indices = _greedy_order(wps, first_idx)
            new_order_indices = _two_opt(wps, new_order_indices)
            method = '2opt'

        # Validate: first WP bridge constraint
        new_order_indices = self._enforce_bridge(
            wps, new_order_indices, bridge_wp
        )

        # Compute new distance
        after_km = sum(
            matrix[new_order_indices[i]][new_order_indices[i+1]]
            for i in range(len(new_order_indices)-1)
        )

        # Only apply if improvement (or first run)
        if after_km < before_km - 0.5 or method in ('claude', 'claude+2opt'):
            day.waypoints = [wps[i] for i in new_order_indices]
            # Recalculate segments with routing service (use estimates for speed)
            self._recalculate_day_estimates(day)
            self._progress("Waypoint riordinati.", 90)
        else:
            method = 'unchanged'
            reasoning = (reasoning or '') + ' (nessun miglioramento significativo)'

        after_stats = self._day_stats(day)
        improvement_km  = before_stats['km'] - after_stats['km']
        improvement_pct = (
            100 * improvement_km / before_stats['km']
            if before_stats['km'] > 0 else 0.0
        )

        self._progress("Ottimizzazione AI completata.", 100)

        return {
            'success':          True,
            'day_num':          day_num,
            'before':           before_stats,
            'after':            after_stats,
            'improvement_km':   round(improvement_km, 1),
            'improvement_pct':  round(improvement_pct, 1),
            'method':           method,
            'reasoning':        reasoning,
            'error':            None,
        }

    # ── Claude API call ───────────────────────────────────────

    def _call_claude(self,
                     day_num: int,
                     wps: list[Waypoint],
                     matrix: list[list[float]],
                     max_km: float,
                     driv_hrs: float,
                     bridge_wp: Optional[Waypoint],
                     before_km: float) -> dict:
        """
        Call Claude API with full route context.
        Returns {'order': [indices], 'reasoning': str} or {'error': str}.
        """
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._api_key)
        except Exception as e:
            return {'error': f'Anthropic SDK error: {e}'}

        # Build WP list for prompt
        wp_lines = []
        for i, wp in enumerate(wps):
            loc = wp.city or wp.province or ''
            gp  = ' [GP]' if wp.is_golden_point else ''
            unp = ' [STERRATO]' if wp.is_unpaved else ''
            wp_lines.append(
                f"  {i:3d}. WP{wp.number} | lat={wp.lat:.5f} lon={wp.lon:.5f} "
                f"| quota={wp.elevation:.0f}m | {loc}{gp}{unp}"
            )

        # Build condensed distance matrix (only ≤ 50 km pairs to keep prompt short)
        n = len(wps)
        dist_lines = []
        for i in range(n):
            for j in range(i+1, n):
                d = matrix[i][j]
                if d <= 60:
                    dist_lines.append(f"  {i}↔{j}: {d:.1f}km")

        bridge_info = ''
        if bridge_wp:
            bd = haversine_km(wps[0].lat, wps[0].lon, bridge_wp.lat, bridge_wp.lon)
            bridge_info = (
                f"\nVINCOLO BRIDGE: il PRIMO waypoint del giorno deve trovarsi a "
                f"{BRIDGE_MIN_KM}-{BRIDGE_MAX_KM}km (su strada) dall'ultimo WP del giorno "
                f"precedente (WP{bridge_wp.number} a {bridge_wp.lat:.5f},{bridge_wp.lon:.5f}, "
                f"quota {bridge_wp.elevation:.0f}m). Distanza attuale primo WP: {bd:.1f}km aria."
            )

        # Build forbidden roads section for prompt
        forbidden_str = ', '.join(f'"{k}"' for k in FORBIDDEN_ROAD_KEYWORDS)

        prompt = f"""Sei un esperto di ottimizzazione percorsi per rally a passi montani italiani (Centopassi 2026).

CONTESTO GIORNO {day_num}:
- Finestra temporale: {driv_hrs:.1f} ore di guida (velocità media {AVERAGE_SPEED_KMH} km/h, {int(BREAK_TIME_HOURS*60)} min pause)
- Distanza massima giornaliera: {max_km:.0f} km
- Distanza attuale (stima aria): {before_km:.0f} km
- Waypoint da percorrere: {n}{bridge_info}

LISTA WAYPOINT (indici 0-{n-1}):
{chr(10).join(wp_lines)}

DISTANZE HAVERSINE TRA WAYPOINT VICINI (≤60 km):
{chr(10).join(dist_lines) if dist_lines else "  (tutti distanti > 60 km)"}

REGOLE DEL CONCORSO - STRADE VIETATE (ESCLUSIONE DALLA CLASSIFICA):
E' ASSOLUTAMENTE VIETATO percorrere i seguenti tipi di strade: {forbidden_str}.
Ciò significa che tra un waypoint e l'altro il percorso deve obbligatoriamente utilizzare
strade ordinarie (strade provinciali SP, strade statali SS, strade comunali, strade di
montagna). L'uso di autostrade, tangenziali, scorrimenti veloci, raccordi o strade a
carreggiata separata comporta l'esclusione dalla gara. Tieni conto di questo nella scelta
dell'ordine: preferisci sequenze di WP collegati da strade minori evitando tratte che
tipicamente richiedono l'uso di strade ad alto scorrimento (es. WP molto distanti in
pianura padana o vicini a grandi città).

OBIETTIVO: Trova l'ordine OTTIMALE dei {n} waypoint che:
1. MINIMIZZA la distanza totale evitando backtracking
2. Rispetta il vincolo bridge (primo WP deve essere raggiungibile dal giorno precedente)
3. Privilegia percorsi su strade secondarie e di montagna (SP, SS, strade comunali)
4. EVITA sequenze che costringono a transitare su autostrade o tangenziali
5. Privilegia una strategia altimetrica logica (salita mattino, discesa sera)
6. Massimizza il punteggio: quota alta = più punti; i [GP] valgono 3× punti
7. Mantiene i [STERRATO] in condizioni favorevoli (evita tardi sera o condizioni difficili)

IMPORTANTE: Devi includere TUTTI e {n} i waypoint nell'ordine che proponi.

Rispondi SOLO con JSON (nessun testo aggiuntivo):
{{
  "order": [indice0, indice1, ..., indice{n-1}],
  "reasoning": "spiegazione sintetica in 2-3 frasi della logica adottata, incluso come eviti le strade vietate"
}}"""

        try:
            response = client.messages.create(
                model=ROAD_INTELLIGENCE_MODEL,
                max_tokens=800,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()

            # Strip markdown code block if present
            if text.startswith('```'):
                text = text.split('\n', 1)[1] if '\n' in text else text[3:]
                text = text.rsplit('```', 1)[0].strip()

            data = json.loads(text)
            order = data.get('order', [])
            reasoning = data.get('reasoning', '')

            # Validate order
            if (isinstance(order, list) and
                    len(order) == n and
                    sorted(order) == list(range(n))):
                return {'order': order, 'reasoning': reasoning}
            else:
                return {'error': f'Ordine non valido da Claude (len={len(order)})'}

        except json.JSONDecodeError as e:
            return {'error': f'JSON parse error: {e}'}
        except Exception as e:
            return {'error': f'Claude API error: {e}'}

    # ── Helpers ───────────────────────────────────────────────

    def _find_bridge_first(self,
                           wps: list[Waypoint],
                           bridge_wp: Optional[Waypoint]) -> int:
        """Find the best first WP index respecting bridge constraint."""
        if bridge_wp is None:
            return 0
        best_idx = 0
        best_score = float('inf')
        for i, wp in enumerate(wps):
            d = haversine_km(wp.lat, wp.lon, bridge_wp.lat, bridge_wp.lon)
            # Score: penalize if outside [BRIDGE_MIN, BRIDGE_MAX] range
            if BRIDGE_MIN_KM <= d <= BRIDGE_MAX_KM:
                score = 0  # perfect
            elif d < BRIDGE_MIN_KM:
                score = (BRIDGE_MIN_KM - d) * 2
            else:
                score = d - BRIDGE_MAX_KM
            if score < best_score:
                best_score = score
                best_idx = i
        return best_idx

    def _enforce_bridge(self,
                        wps: list[Waypoint],
                        order: list[int],
                        bridge_wp: Optional[Waypoint]) -> list[int]:
        """
        Ensure the first WP in order respects bridge constraint.
        If not, swap first element with the best bridge candidate.
        """
        if bridge_wp is None or not order:
            return order

        # Check current first
        first = wps[order[0]]
        d_first = haversine_km(first.lat, first.lon, bridge_wp.lat, bridge_wp.lon)

        if BRIDGE_MIN_KM <= d_first <= BRIDGE_MAX_KM:
            return order  # already valid

        # Find best replacement
        best_swap_pos = 0
        best_dist_score = abs(d_first - (BRIDGE_MIN_KM + BRIDGE_MAX_KM) / 2)

        for pos, idx in enumerate(order):
            wp = wps[idx]
            d = haversine_km(wp.lat, wp.lon, bridge_wp.lat, bridge_wp.lon)
            if BRIDGE_MIN_KM <= d <= BRIDGE_MAX_KM:
                score = abs(d - (BRIDGE_MIN_KM + BRIDGE_MAX_KM) / 2)
                if score < best_dist_score:
                    best_dist_score = score
                    best_swap_pos = pos

        if best_swap_pos > 0:
            # Swap first element with best bridge candidate
            new_order = list(order)
            new_order[0], new_order[best_swap_pos] = (
                new_order[best_swap_pos], new_order[0]
            )
            return new_order

        return order  # no valid bridge WP found, keep as-is

    @staticmethod
    def _day_stats(day: DaySegment) -> dict:
        """Return day stats for before/after comparison."""
        km = day.total_km if day.total_km else sum(
            haversine_km(day.waypoints[i].lat, day.waypoints[i].lon,
                         day.waypoints[i+1].lat, day.waypoints[i+1].lon)
            for i in range(len(day.waypoints)-1)
        ) * 1.35  # rough road estimate
        return {
            'km':       round(km, 1),
            'hours':    round(day.total_hours, 2),
            'wp_count': len(day.waypoints),
            'elev_gain': round(day.total_elevation_gain, 0),
        }

    @staticmethod
    def _recalculate_day_estimates(day: DaySegment):
        """Recalculate segments using haversine estimates (fast, no API calls)."""
        from models import RouteSegment
        day.segments = []
        day.total_km = 0.0
        day.total_hours = 0.0
        day.total_elevation_gain = 0.0
        day.total_elevation_loss = 0.0

        for i in range(len(day.waypoints) - 1):
            wp1 = day.waypoints[i]
            wp2 = day.waypoints[i + 1]
            air  = haversine_km(wp1.lat, wp1.lon, wp2.lat, wp2.lon)
            dist = round(air * 1.35, 2)
            dur  = round(dist / AVERAGE_SPEED_KMH, 3)
            seg  = RouteSegment(
                from_wp=wp1, to_wp=wp2,
                distance_km=dist, duration_hours=dur,
                is_unpaved=wp2.is_unpaved,
                geometry=[[wp1.lat, wp1.lon], [wp2.lat, wp2.lon]],
                air_distance_km=air,
                road_distance_km=dist,
                routing_source='estimate',
            )
            day.segments.append(seg)
            day.total_km    += dist
            day.total_hours += dur
            elev = wp2.elevation - wp1.elevation
            if elev > 0:
                day.total_elevation_gain += elev
            else:
                day.total_elevation_loss += abs(elev)
