"""
Centopassi Route Planner - Road Intelligence via Claude API
Checks for road closures, landslides, and hazards along the route.
"""
import os
import json
from typing import Optional
from config import (
    ANTHROPIC_API_KEY_ENV, ROAD_INTELLIGENCE_MODEL,
    ROAD_INTELLIGENCE_MAX_TOKENS, ROAD_INTELLIGENCE_ENABLED,
)


class RoadIntelligence:
    """Query Claude API for road closure and hazard information."""

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get(ANTHROPIC_API_KEY_ENV)
        self._client = None
        self.warnings = []
        self._cache = {}

    @property
    def enabled(self) -> bool:
        return ROAD_INTELLIGENCE_ENABLED and bool(self.api_key)

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                print("[RoadIntelligence] anthropic package not installed")
                return None
            except Exception as e:
                print(f"[RoadIntelligence] Failed to init client: {e}")
                return None
        return self._client

    def check_road_closures(self, regions: list[str],
                            waypoints: list = None,
                            extra_context: list[str] = None) -> list[dict]:
        """
        Query Claude for known road closures/hazards in specified regions.
        Makes ONE API call per optimization run.

        Returns list of warning dicts:
        {
            'area': str,
            'type': str,  # 'frana', 'chiusura', 'dissesto', etc.
            'severity': str,  # 'alta', 'media', 'bassa'
            'description': str,
            'affected_roads': [str],
        }
        """
        if not self.enabled:
            return []

        # Cache key based on sorted regions
        cache_key = ",".join(sorted(regions))
        if cache_key in self._cache:
            self.warnings = self._cache[cache_key]
            return self.warnings

        client = self._get_client()
        if not client:
            return []

        # Build location context
        region_list = ", ".join(regions[:30])
        road_context = ""
        if extra_context:
            road_names = ", ".join([r for r in extra_context[:20] if r])
            if road_names:
                road_context = (
                    f"\nStrade specifiche del percorso: {road_names}."
                )

        prompt = (
            f"Sei un esperto di viabilità stradale italiana con accesso a dati "
            f"aggiornati sulle condizioni stradali montane.\n"
            f"Sto pianificando un percorso rally (Centopassi 2026, 29 maggio - 1 giugno 2026) "
            f"che attraversa: {region_list}.{road_context}\n\n"
            f"Considera:\n"
            f"- Frane, smottamenti e cedimenti recenti\n"
            f"- Ponti chiusi o con limitazioni di peso\n"
            f"- Strade chiuse per lavori o eventi\n"
            f"- Passi montani con chiusura stagionale o danni invernali\n"
            f"- Strade vietate dal regolamento: autostrade, tangenziali, SSV, SGC\n\n"
            f"Rispondi SOLO in formato JSON:\n"
            f'[{{"area":"nome zona","type":"frana|chiusura|dissesto|ponte_chiuso|vietata",'
            f'"severity":"alta|media|bassa","description":"descrizione concisa",'
            f'"affected_roads":["SS123","SP45"]}}]\n\n'
            f"Se non ci sono problemi noti, rispondi: []\n"
            f"Non aggiungere testo fuori dal JSON."
        )

        try:
            response = client.messages.create(
                model=ROAD_INTELLIGENCE_MODEL,
                max_tokens=ROAD_INTELLIGENCE_MAX_TOKENS,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )

            text = response.content[0].text.strip()

            # Parse JSON from response (handle markdown code blocks)
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                text = text.rsplit("```", 1)[0]
            text = text.strip()

            warnings = json.loads(text)
            if not isinstance(warnings, list):
                warnings = []

            # Validate structure
            valid_warnings = []
            for w in warnings:
                if isinstance(w, dict) and 'area' in w:
                    valid_warnings.append({
                        'area': str(w.get('area', '')),
                        'type': str(w.get('type', 'sconosciuto')),
                        'severity': str(w.get('severity', 'media')),
                        'description': str(w.get('description', '')),
                        'affected_roads': list(w.get('affected_roads', [])),
                    })

            self.warnings = valid_warnings
            self._cache[cache_key] = valid_warnings
            print(f"[RoadIntelligence] Found {len(valid_warnings)} warnings for {len(regions)} regions")
            return valid_warnings

        except json.JSONDecodeError as e:
            print(f"[RoadIntelligence] Failed to parse response: {e}")
            return []
        except Exception as e:
            print(f"[RoadIntelligence] API error: {e}")
            return []

    def get_affected_waypoints(self, waypoints: list) -> dict[int, list[str]]:
        """
        Match warnings against waypoints by city/province/description.
        Returns {wp_id: [warning_message, ...]}
        """
        if not self.warnings:
            return {}

        affected = {}
        for wp in waypoints:
            wp_fields = [
                (wp.city or '').lower(),
                (wp.province or '').lower(),
                (wp.description or '').lower(),
            ]

            for warning in self.warnings:
                area_lower = warning['area'].lower()
                roads_lower = [r.lower() for r in warning.get('affected_roads', [])]

                matched = False
                for field in wp_fields:
                    if not field:
                        continue
                    if area_lower in field or field in area_lower:
                        matched = True
                        break
                    for road in roads_lower:
                        if road in field:
                            matched = True
                            break
                    if matched:
                        break

                if matched:
                    msg = f"[{warning['severity'].upper()}] {warning['area']}: {warning['description']}"
                    if wp.id not in affected:
                        affected[wp.id] = []
                    if msg not in affected[wp.id]:
                        affected[wp.id].append(msg)

        return affected
