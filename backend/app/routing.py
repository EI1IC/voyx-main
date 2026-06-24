# app/routing.py
import osmnx as ox
import networkx as nx
import math
from typing import List, Tuple, Dict

from .config import SPEED_LIMITS, ROAD_PENALTIES, BBOX
from .graph import get_graph
from .geocoding import geocode_address
from .traffic_screen import get_edge_factor 

Coord = Tuple[float, float]


def _seconds_to_minutes(seconds: float) -> float:
    return round(seconds / 60.0, 1)


def _calc_base_time(edge_data: dict) -> float:
    length = edge_data.get("length", 0)
    highway = edge_data.get("highway", "residential")
    if isinstance(highway, list):
        highway = highway[0]
    
    speed_kmh = SPEED_LIMITS.get(highway, 30)
    penalty = ROAD_PENALTIES.get(highway, 1.0)
    speed_ms = speed_kmh / 3.6
    return (length / speed_ms) * penalty if speed_ms > 0 else float("inf")


def _haversine_heuristic(u, v, G) -> float:
    lat1, lon1 = G.nodes[u]["y"], G.nodes[u]["x"]
    lat2, lon2 = G.nodes[v]["y"], G.nodes[v]["x"]
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _build_route_result(G: nx.Graph, path: List, start_coords: Coord, end_coords: Coord, used_fallback: bool = False) -> dict:
    route_coords = []
    distance_m = 0
    time_sec = 0
    
    for u, v in zip(path[:-1], path[1:]):
        route_coords.append([G.nodes[u]["x"], G.nodes[u]["y"]])
        edge_data = G.get_edge_data(u, v, key=0)
        distance_m += edge_data.get("length", 0)
        time_sec += _calc_base_time(edge_data)
    
    if path:
        route_coords.append([G.nodes[path[-1]]["x"], G.nodes[path[-1]]["y"]])
    
    return {
        "route": route_coords,
        "distance_km": round(distance_m / 1000, 2),
        "time_min": round(time_sec / 60, 1),
        "start": {"lat": start_coords[1], "lon": start_coords[0], "address": ""},
        "end": {"lat": end_coords[1], "lon": end_coords[0], "address": ""},
        "has_barriers": used_fallback
    }


def _fallback_route(G: nx.Graph, orig_node, dest_node, blocked_edges_set: set) -> List:
    def fallback_weight(u, v, data):
        length = data.get("length", 0)
        for key in G.get_edge_data(u, v).keys():
            if (u, v, key) in blocked_edges_set:
                return length + 10000
        return length
    return nx.shortest_path(G, orig_node, dest_node, weight=fallback_weight, heuristic=lambda u, v: _haversine_heuristic(u, v, G))


def calculate_route(start_addr: str, end_addr: str, use_traffic: bool = True) -> dict:
    G, blocked_edges_set = get_graph()
    start_coords = geocode_address(start_addr)
    end_coords = geocode_address(end_addr)
    
    if not start_coords or len(start_coords) != 2:
        raise ValueError(f"Не удалось определить координаты для: {start_addr}")
    if not end_coords or len(end_coords) != 2:
        raise ValueError(f"Не удалось определить координаты для: {end_addr}")
    
    try:
        orig_node = ox.nearest_nodes(G, X=start_coords[0], Y=start_coords[1])
        dest_node = ox.nearest_nodes(G, X=end_coords[0], Y=end_coords[1])
    except ValueError as e:
        if "scikit-learn" in str(e):
            raise RuntimeError("Требуется scikit-learn. Установите: pip install -r requirements.txt") from e
        raise

    # Весовая функция с прямым анализом скриншота
    def route_weight(u, v, data):
        for key in G.get_edge_data(u, v).keys():
            if (u, v, key) in blocked_edges_set:
                return float('inf')
        base = _calc_base_time(data)
        if use_traffic:
            k, _ = get_edge_factor(G.nodes[u]["x"], G.nodes[u]["y"], G.nodes[v]["x"], G.nodes[v]["y"])
            return base * k
        return base
    
    try:
        path = nx.shortest_path(G, orig_node, dest_node, weight=route_weight)
        used_fallback = False
    except nx.NetworkXNoPath:
        path = _fallback_route(G, orig_node, dest_node, blocked_edges_set)
        used_fallback = True
    
    result = _build_route_result(G, path, start_coords, end_coords, used_fallback)
    result["waypoints"] = [
        {"lat": start_coords[1], "lon": start_coords[0], "address": start_addr},
        {"lat": end_coords[1], "lon": end_coords[0], "address": end_addr}
    ]
    result["use_traffic"] = use_traffic
    
    # ✅ Статистика пересчитывается по финальному пути
    if use_traffic:
        path_factors = []
        for u, v in zip(path[:-1], path[1:]):
            k, _ = get_edge_factor(G.nodes[u]["x"], G.nodes[u]["y"], G.nodes[v]["x"], G.nodes[v]["y"])
            path_factors.append(k)
        result["traffic_factors_applied"] = sum(1 for k in path_factors if k > 1.05)
        result["avg_congestion_factor"] = round(sum(path_factors) / len(path_factors), 2) if path_factors else 1.0
    else:
        result["traffic_factors_applied"] = 0
        result["avg_congestion_factor"] = 1.0
        
    return result


def calculate_multi_point_route(waypoints: List[str], use_traffic: bool = True) -> dict:
    if len(waypoints) < 2:
        raise ValueError("Минимум 2 точки для маршрута")
    
    G, blocked_edges_set = get_graph()
    coords: List[Coord] = []
    for addr in waypoints:
        c = geocode_address(addr)
        if not c or len(c) != 2:
            raise ValueError(f"Не удалось определить координаты для: {addr}")
        coords.append(c)
    
    all_route_coords, total_distance, total_time, has_barriers, segments_data = [], 0, 0, False, []
    
    for i in range(len(coords) - 1):
        start_c, end_c = coords[i], coords[i + 1]
        try:
            orig_node = ox.nearest_nodes(G, X=start_c[0], Y=start_c[1])
            dest_node = ox.nearest_nodes(G, X=end_c[0], Y=end_c[1])
        except ValueError: continue
        
        def seg_weight(u, v, data):
            for key in G.get_edge_data(u, v).keys():
                if (u, v, key) in blocked_edges_set: return float('inf')
            base = _calc_base_time(data)
            if use_traffic:
                k, _ = get_edge_factor(G.nodes[u]["x"], G.nodes[u]["y"], G.nodes[v]["x"], G.nodes[v]["y"])
                return base * k
            return base
            
        try:
            path = nx.shortest_path(G, orig_node, dest_node, weight=seg_weight)
        except nx.NetworkXNoPath:
            path = _fallback_route(G, orig_node, dest_node, blocked_edges_set)
            has_barriers = True
            
        segment_coords, seg_dist, seg_time = [], 0, 0
        for u, v in zip(path[:-1], path[1:]):
            segment_coords.append([G.nodes[u]["x"], G.nodes[u]["y"]])
            d = G.get_edge_data(u, v, key=0)
            seg_dist += d.get("length", 0)
            seg_time += _calc_base_time(d)
        segment_coords.append([G.nodes[path[-1]]["x"], G.nodes[path[-1]]["y"]])
        
        all_route_coords.extend(segment_coords if i == 0 else segment_coords[1:])
        total_distance += seg_dist
        total_time += seg_time
        segments_data.append({"from": waypoints[i], "to": waypoints[i+1], "distance_km": round(seg_dist/1000, 2), "time_min": round(seg_time/60, 1)})
        
    result = {"route": all_route_coords, "distance_km": round(total_distance/1000, 2), "time_min": round(total_time/60, 1),
              "waypoints": [{"lat": c[1], "lon": c[0], "address": addr} for c, addr in zip(coords, waypoints)],
              "has_barriers": has_barriers, "segments": segments_data, "use_traffic": use_traffic}
              
    if use_traffic:
        # Упрощённая статистика для многоточки
        result["traffic_factors_applied"] = 1 if result["time_min"] > 0 else 0
        result["avg_congestion_factor"] = 1.0
    else:
        result["traffic_factors_applied"] = 0
        result["avg_congestion_factor"] = 1.0
    return result