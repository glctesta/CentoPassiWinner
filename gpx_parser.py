"""
Centopassi Route Planner - GPX Parser
Parses Garmin GPX files and classifies waypoints by road type.
"""
import xml.etree.ElementTree as ET
import re
from models import Waypoint
from config import UNPAVED_ROAD_KEYWORDS, FORBIDDEN_ROAD_KEYWORDS


# XML namespaces used in Garmin GPX files
NS = {
    'gpx': 'http://www.topografix.com/GPX/1/1',
    'gpxx': 'http://www.garmin.com/xmlschemas/GpxExtensions/v3',
    'wptx1': 'http://www.garmin.com/xmlschemas/WaypointExtension/v1',
    'ctx': 'http://www.garmin.com/xmlschemas/CreationTimeExtension/v1',
}


def classify_road_type(description: str) -> tuple[str, bool, bool]:
    """
    Classify road type from waypoint description.
    
    Returns:
        (road_type, is_unpaved, is_forbidden)
    """
    desc_lower = description.lower() if description else ""
    
    # Check for forbidden roads
    is_forbidden = False
    for keyword in FORBIDDEN_ROAD_KEYWORDS:
        if keyword.lower() in desc_lower:
            is_forbidden = True
            break
    
    # Check for unpaved roads
    is_unpaved = False
    for keyword in UNPAVED_ROAD_KEYWORDS:
        if keyword.lower() in desc_lower:
            is_unpaved = True
            break
    
    # Determine road type category
    if is_forbidden:
        road_type = "forbidden"
    elif is_unpaved:
        road_type = "unpaved"
    elif re.search(r'\bsp\d+', desc_lower) or 'strada provinciale' in desc_lower:
        road_type = "provincial"
    elif 'secondary road' in desc_lower:
        road_type = "secondary"
    elif 'unclassified' in desc_lower:
        road_type = "unclassified"
    elif 'country road' in desc_lower:
        road_type = "country"
    elif 'service road' in desc_lower:
        road_type = "service"
    elif 'strada statale' in desc_lower or re.search(r'\bss\d+', desc_lower):
        road_type = "statale"  # State road (may or may not be forbidden)
    elif re.search(r'\bvia\b', desc_lower) or re.search(r'\bpiazz', desc_lower):
        road_type = "urban"
    else:
        road_type = "unknown"
    
    return road_type, is_unpaved, is_forbidden


def _extract_address(wpt_elem) -> tuple[str, str, str]:
    """Extract city, province, country from Garmin extensions."""
    city = ""
    province = ""
    country = "ITA"
    
    # Try gpxx namespace first
    for ns_prefix in ['gpxx', 'wptx1']:
        ext = wpt_elem.find(f'.//gpx:extensions/{ns_prefix}:WaypointExtension/{ns_prefix}:Address', NS)
        if ext is not None:
            city_elem = ext.find(f'{ns_prefix}:City', NS)
            state_elem = ext.find(f'{ns_prefix}:State', NS)
            country_elem = ext.find(f'{ns_prefix}:Country', NS)
            if city_elem is not None and city_elem.text:
                city = city_elem.text.strip()
            if state_elem is not None and state_elem.text:
                province = state_elem.text.strip()
            if country_elem is not None and country_elem.text:
                country = country_elem.text.strip()
            if city or province:
                break
    
    # Also try to extract from description text
    # Format: "Name\n\nCity, Province, PostalCode, Country"
    return city, province, country


def _extract_from_description(desc: str) -> tuple[str, str]:
    """Try to extract city and province from description text."""
    if not desc:
        return "", ""
    
    lines = desc.strip().split('\n')
    for line in lines:
        line = line.strip()
        parts = [p.strip() for p in line.split(',')]
        if len(parts) >= 3:
            # Likely: City, Province, PostalCode, Country
            city = parts[0]
            province = parts[1]
            # Validate: province should be a known Italian province
            if len(province) > 1 and not province.isdigit():
                return city, province
    
    return "", ""


def parse_gpx(filepath: str) -> list[Waypoint]:
    """
    Parse a Garmin GPX file and return list of Waypoint objects.
    
    Args:
        filepath: path to GPX file
    
    Returns:
        List of Waypoint objects, sorted by name number
    """
    tree = ET.parse(filepath)
    root = tree.getroot()
    
    waypoints = []
    idx = 0
    
    for wpt in root.findall('gpx:wpt', NS):
        lat = float(wpt.get('lat', 0))
        lon = float(wpt.get('lon', 0))
        
        # Basic fields
        ele_elem = wpt.find('gpx:ele', NS)
        elevation = float(ele_elem.text) if ele_elem is not None and ele_elem.text else 0.0
        
        name_elem = wpt.find('gpx:name', NS)
        name = name_elem.text.strip() if name_elem is not None and name_elem.text else ""
        
        cmt_elem = wpt.find('gpx:cmt', NS)
        description = cmt_elem.text.strip() if cmt_elem is not None and cmt_elem.text else ""
        
        # Fallback to desc if cmt is empty
        if not description:
            desc_elem = wpt.find('gpx:desc', NS)
            description = desc_elem.text.strip() if desc_elem is not None and desc_elem.text else ""
        
        sym_elem = wpt.find('gpx:sym', NS)
        symbol = sym_elem.text.strip() if sym_elem is not None and sym_elem.text else ""
        
        # Golden Point detection
        is_gp = 'GP' in name.upper().split() if name else False
        
        # Road classification
        road_type, is_unpaved, _ = classify_road_type(description)
        
        # Address from extensions
        city, province, country = _extract_address(wpt)
        
        # Try description for city/province if not found in extensions
        if not city and not province:
            city, province = _extract_from_description(description)
        
        wp = Waypoint(
            id=idx,
            name=name,
            lat=lat,
            lon=lon,
            elevation=elevation,
            description=description,
            city=city,
            province=province,
            country=country,
            is_golden_point=is_gp,
            is_unpaved=is_unpaved,
            road_type=road_type,
            symbol=symbol,
        )
        waypoints.append(wp)
        idx += 1
    
    # Sort by numeric part of name
    def sort_key(wp):
        try:
            return int(wp.number)
        except (ValueError, IndexError):
            return 9999
    
    waypoints.sort(key=sort_key)
    
    # Re-assign sequential IDs after sorting
    for i, wp in enumerate(waypoints):
        wp.id = i
    
    return waypoints


def get_waypoints_by_region(waypoints: list[Waypoint]) -> dict[str, list[Waypoint]]:
    """Group waypoints by province/region."""
    regions = {}
    for wp in waypoints:
        key = wp.province if wp.province else "Sconosciuta"
        if key not in regions:
            regions[key] = []
        regions[key].append(wp)
    return regions


def get_waypoint_stats(waypoints: list[Waypoint]) -> dict:
    """Get summary statistics about waypoints."""
    total = len(waypoints)
    gp_count = sum(1 for wp in waypoints if wp.is_golden_point)
    unpaved_count = sum(1 for wp in waypoints if wp.is_unpaved)
    
    road_types = {}
    for wp in waypoints:
        rt = wp.road_type or "unknown"
        road_types[rt] = road_types.get(rt, 0) + 1
    
    # Geographic bounds
    lats = [wp.lat for wp in waypoints]
    lons = [wp.lon for wp in waypoints]
    
    return {
        'total': total,
        'golden_points': gp_count,
        'unpaved': unpaved_count,
        'road_types': road_types,
        'bounds': {
            'min_lat': min(lats),
            'max_lat': max(lats),
            'min_lon': min(lons),
            'max_lon': max(lons),
        },
        'elevation': {
            'min': min(wp.elevation for wp in waypoints),
            'max': max(wp.elevation for wp in waypoints),
            'avg': sum(wp.elevation for wp in waypoints) / total,
        }
    }


if __name__ == "__main__":
    import json
    from config import GPX_FILE
    
    wps = parse_gpx(GPX_FILE)
    stats = get_waypoint_stats(wps)
    
    print(f"Parsed {stats['total']} waypoints")
    print(f"  Golden Points: {stats['golden_points']}")
    print(f"  Unpaved: {stats['unpaved']}")
    print(f"  Road types: {json.dumps(stats['road_types'], indent=2)}")
    print(f"  Bounds: {stats['bounds']}")
    print(f"  Elevation: {stats['elevation']}")
    
    regions = get_waypoints_by_region(wps)
    print(f"\n  Regions ({len(regions)}):")
    for region, rwps in sorted(regions.items(), key=lambda x: -len(x[1])):
        print(f"    {region}: {len(rwps)} waypoints")
