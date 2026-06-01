# backend/route_engine.py
import os
import math
import datetime
import osmnx as ox
import networkx as nx
import geopandas as gpd
from shapely.geometry import LineString, Point
from functools import lru_cache
from typing import List

from app.traffic_screen import get_edge_factor

# ==============================================================================
# КОНФИГУРАЦИЯ
# ==============================================================================
ox.settings.log_console = False
ox.settings.use_cache = True
ox.settings.timeout = 180

GRAPH_FILENAME = "kirov_road_network.graphml"
BARRIERS_FILENAME = "kirov_barriers.geojson"
BARRIER_TOLERANCE = 7

# ✅ Реалистичные средние скорости (откалиброваны под городские условия)
SPEED_LIMITS = {
    'motorway': 75, 'trunk': 60, 'primary': 42,
    'secondary': 34, 'tertiary': 27, 'residential': 16,
    'service': 13, 'living_street': 11, 'unclassified': 27, 'road': 32
}

# ✅ Штрафы для маршрутизации (дворы избегаются, но не блокируются)
ROAD_PENALTIES = {
    'motorway': 1.0, 'trunk': 1.0, 'primary': 1.1,
    'secondary': 1.2, 'tertiary': 1.3,
    'residential': 4.0, 'living_street': 6.0, 'service': 8.0,
    'unclassified': 1.5, 'road': 1.5
}

_G = None
_BLOCKED_EDGES = None
_BLOCKED_EDGES_SET = None

# ==============================================================================
# ГЕОКОДИРОВАНИЕ
# ==============================================================================
@lru_cache(maxsize=1000)
def geocode_address(address_query, city="Kirov, Russia"):
    """Преобразует адрес в координаты (lat, lon)"""
    if "Киров" in address_query or "Kirov" in address_query:
        full_query = address_query
    else:
        full_query = f"{address_query}, {city}"
    try:
        location = ox.geocode(full_query)
        return float(location[0]), float(location[1])
    except Exception as e:
        raise ValueError(f"Не удалось найти адрес '{address_query}': {str(e)}")

# ==============================================================================
# УПРАВЛЕНИЕ ГРАФОМ
# ==============================================================================
def init_graph():
    """Инициализация графа ПРИ СТАРТЕ приложения"""
    global _G, _BLOCKED_EDGES, _BLOCKED_EDGES_SET
    from app.config import BBOX, CUSTOM_FILTER
    bbox = BBOX
    
    if not os.path.exists(GRAPH_FILENAME):
        print("📥 Загрузка графа дорожной сети...")
        _G = ox.graph_from_bbox(bbox=bbox, custom_filter=CUSTOM_FILTER, network_type="drive",
                                simplify=True, retain_all=False, truncate_by_edge=True)
        _G = ox.distance.add_edge_lengths(_G)
        ox.save_graphml(_G, filepath=GRAPH_FILENAME)
        print(f"✅ Граф сохранён: {len(_G.nodes)} узлов, {len(_G.edges)} рёбер")
    else:
        print("📂 Загрузка графа из файла...")
        _G = ox.load_graphml(filepath=GRAPH_FILENAME)
        print(f"✅ Граф загружён: {len(_G.nodes)} узлов, {len(_G.edges)} рёбер")

    if not os.path.exists(BARRIERS_FILENAME):
        print("🚧 Загрузка барьеров...")
        barriers_gdf = ox.features_from_bbox(bbox=bbox, tags={'barrier': True})
        barrier_types = ['gate', 'lift_gate', 'swing_gate', 'sliding_gate', 'barrier', 'bollard', 'chain']
        if 'barrier' in barriers_gdf.columns:
            barriers_gdf = barriers_gdf[barriers_gdf['barrier'].isin(barrier_types)]
        barriers_gdf.to_file(BARRIERS_FILENAME, driver='GeoJSON')
    else:
        barriers_gdf = gpd.read_file(BARRIERS_FILENAME)

    print("🔗 Привязка барьеров к графу...")
    _G, _BLOCKED_EDGES = map_barriers_to_graph(_G, barriers_gdf)
    _BLOCKED_EDGES_SET = set(_BLOCKED_EDGES)
    print(f"✅ Заблокировано рёбер: {len(_BLOCKED_EDGES_SET)}")
    print("✅ Система готова к работе")

def get_graph():
    if _G is None:
        init_graph()
    return _G, _BLOCKED_EDGES_SET

# ==============================================================================
# ПРИВЯЗКА БАРЬЕРОВ
# ==============================================================================
def map_barriers_to_graph(G, barriers_gdf, tolerance=BARRIER_TOLERANCE):
    blocked_edges = set()
    if barriers_gdf is None or len(barriers_gdf) == 0:
        return G, blocked_edges

    barriers_proj = barriers_gdf.to_crs("EPSG:3857").copy()
    _ = barriers_proj.sindex

    for u, v, key, data in G.edges(keys=True, data=True):
        edge_geom = data.get('geometry') or LineString([(G.nodes[u]['x'], G.nodes[u]['y']), (G.nodes[v]['x'], G.nodes[v]['y'])])
        edge_gdf = gpd.GeoDataFrame(geometry=[edge_geom], crs="EPSG:4326")
        edge_geom_proj = edge_gdf.to_crs("EPSG:3857").geometry.iloc[0]
        search_box = edge_geom_proj.buffer(tolerance).bounds
        candidates = list(barriers_proj.sindex.intersection(search_box))

        for idx in candidates:
            barrier = barriers_proj.iloc[idx]
            if isinstance(barrier.geometry, Point):
                if edge_geom_proj.distance(barrier.geometry) <= tolerance:
                    blocked_edges.add((u, v, key))
                    G.edges[u, v, key]['has_barrier'] = True
    return G, blocked_edges

# ==============================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==============================================================================
def _is_near_point(lat, lon, point_lat, point_lon, radius_m=150):
    """Проверяет, находится ли точка в радиусе от целевой"""
    lat_diff = abs(lat - point_lat) * 111000
    lon_diff = abs(lon - point_lon) * 111000 * math.cos(math.radians(point_lat))
    return math.sqrt(lat_diff**2 + lon_diff**2) <= radius_m

def _process_time_logic(departure_time_str, arrival_time_str, calc_time_minutes):
    """Унифицированная логика расчёта времени отправления/прибытия"""
    dep_dt = arr_dt = None
    time_mismatch = False

    if departure_time_str:
        try: dep_dt = datetime.datetime.fromisoformat(departure_time_str.replace("Z", "+00:00"))
        except: pass
    if arrival_time_str:
        try: arr_dt = datetime.datetime.fromisoformat(arrival_time_str.replace("Z", "+00:00"))
        except: pass

    if dep_dt and arr_dt:
        expected_arr = dep_dt + datetime.timedelta(minutes=calc_time_minutes)
        if abs((arr_dt - expected_arr).total_seconds()) > 120:  # >2 мин расхождение
            time_mismatch = True
    elif dep_dt:
        arr_dt = dep_dt + datetime.timedelta(minutes=calc_time_minutes)
    elif arr_dt:
        dep_dt = arr_dt - datetime.timedelta(minutes=calc_time_minutes)

    return {
        "departure_time_display": dep_dt.strftime("%H:%M") if dep_dt else None,
        "arrival_time_display": arr_dt.strftime("%H:%M") if arr_dt else None,
        "time_mismatch": time_mismatch,
        "departure_iso": dep_dt.isoformat() if dep_dt else None,
        "arrival_iso": arr_dt.isoformat() if arr_dt else None
    }

def _calc_segment_metrics(G, path, use_traffic):
    """Считает дистанцию и время для пути"""
    distance_meters = 0
    estimated_time_minutes = 0
    
    for i, (u, v) in enumerate(zip(path[:-1], path[1:])):
        edge_data = list(G[u][v].values())[0]
        length = edge_data.get('length', 0)
        distance_meters += length
        
        highway_type = edge_data.get('highway', 'residential')
        if isinstance(highway_type, list): highway_type = highway_type[0]
        base_speed = SPEED_LIMITS.get(highway_type, 30)
        
        traffic_k = 1.0
        if use_traffic:
            traffic_k, _ = get_edge_factor(G.nodes[u]['x'], G.nodes[u]['y'], G.nodes[v]['x'], G.nodes[v]['y'])
            
        effective_speed = base_speed / traffic_k
        estimated_time_minutes += (length / (effective_speed / 3.6)) / 60
        
        # Задержка на узлы (светофоры/повороты)
        if 0 < i < len(path) - 2:
            estimated_time_minutes += 0.042  # 2.5 секунды
            
    return distance_meters, estimated_time_minutes * 1.05  # +5% на непредсказуемое

# ==============================================================================
# РАСЧЁТ МАРШРУТА (ОДНА ТОЧКА -> ДРУГАЯ)
# ==============================================================================
def calculate_route(start_addr, end_addr, use_traffic=True, departure_time=None, arrival_time=None):
    G, blocked_edges_set = get_graph()
    start_coords = geocode_address(start_addr)
    end_coords = geocode_address(end_addr)
    
    orig_node = ox.nearest_nodes(G, X=start_coords[1], Y=start_coords[0])
    dest_node = ox.nearest_nodes(G, X=end_coords[1], Y=end_coords[0])
    
    def weight(u, v, data):
        min_cost = float('inf')
        valid_found = False
        for key, d in data.items():
            if (u, v, key) in blocked_edges_set: continue
            length = d.get('length', 0)
            if length < 2.0: continue
            highway = d.get('highway', 'residential')
            if isinstance(highway, list): highway = highway[0]
            penalty = ROAD_PENALTIES.get(highway, 2.0)
            
            # Умный штраф дворов
            if highway in ['residential', 'living_street', 'service']:
                mid_lat = (G.nodes[u]['y'] + G.nodes[v]['y']) / 2
                mid_lon = (G.nodes[u]['x'] + G.nodes[v]['x']) / 2
                if (not _is_near_point(mid_lat, mid_lon, start_coords[0], start_coords[1]) and
                    not _is_near_point(mid_lat, mid_lon, end_coords[0], end_coords[1])):
                    penalty *= 3.0
            
            if use_traffic:
                k, _ = get_edge_factor(G.nodes[u]['x'], G.nodes[u]['y'], G.nodes[v]['x'], G.nodes[v]['y'])
                penalty *= min(k, 2.0)
                
            cost = length * penalty
            if cost < min_cost: min_cost = cost; valid_found = True
        return min_cost if valid_found else float('inf')

    def fallback_weight(u, v, data):
        length = data.get('length', 0)
        for key in G.get_edge_data(u, v).keys():
            if (u, v, key) in blocked_edges_set: return length * 50
        highway = data.get('highway', 'residential')
        if isinstance(highway, list): highway = highway[0]
        return length * ROAD_PENALTIES.get(highway, 2.0)

    try:
        path = nx.shortest_path(G, source=orig_node, target=dest_node, weight=weight)
    except nx.NetworkXNoPath:
        path = nx.shortest_path(G, source=orig_node, target=dest_node, weight=fallback_weight)

    dist, t_min = _calc_segment_metrics(G, path, use_traffic)
    route_coords = [[G.nodes[n]['x'], G.nodes[n]['y']] for n in path]

    time_data = _process_time_logic(departure_time, arrival_time, t_min)

    return {
        "route": route_coords,
        "distance_km": round(dist / 1000, 2),
        "time_min": round(t_min, 1),
        "start": {"lat": start_coords[0], "lon": start_coords[1], "address": start_addr},
        "end": {"lat": end_coords[0], "lon": end_coords[1], "address": end_addr},
        "waypoints": [
            {"lat": start_coords[0], "lon": start_coords[1], "address": start_addr},
            {"lat": end_coords[0], "lon": end_coords[1], "address": end_addr}
        ],
        "has_barriers": False,
        "path": path,
        **time_data
    }

# ==============================================================================
# РАСЧЁТ МАРШРУТА ПО КООРДИНАТАМ
# ==============================================================================
def calculate_route_by_coords(start_coords, end_coords, use_traffic=True, departure_time=None, arrival_time=None):
    G, blocked_edges_set = get_graph()
    orig_node = ox.nearest_nodes(G, X=start_coords[1], Y=start_coords[0])
    dest_node = ox.nearest_nodes(G, X=end_coords[1], Y=end_coords[0])

    def weight(u, v, data):
        min_cost = float('inf')
        valid_found = False
        for key, d in data.items():
            if (u, v, key) in blocked_edges_set: continue
            length = d.get('length', 0)
            if length < 2.0: continue
            highway = d.get('highway', 'residential')
            if isinstance(highway, list): highway = highway[0]
            penalty = ROAD_PENALTIES.get(highway, 2.0)
            if highway in ['residential', 'living_street', 'service']:
                mid_lat = (G.nodes[u]['y'] + G.nodes[v]['y']) / 2
                mid_lon = (G.nodes[u]['x'] + G.nodes[v]['x']) / 2
                if (not _is_near_point(mid_lat, mid_lon, start_coords[0], start_coords[1]) and
                    not _is_near_point(mid_lat, mid_lon, end_coords[0], end_coords[1])):
                    penalty *= 3.0
            if use_traffic:
                k, _ = get_edge_factor(G.nodes[u]['x'], G.nodes[u]['y'], G.nodes[v]['x'], G.nodes[v]['y'])
                penalty *= min(k, 2.0)
            cost = length * penalty
            if cost < min_cost: min_cost = cost; valid_found = True
        return min_cost if valid_found else float('inf')

    try:
        path = nx.shortest_path(G, source=orig_node, target=dest_node, weight=weight)
    except nx.NetworkXNoPath:
        def fallback(u, v, data): return data.get('length', 0) * 50
        path = nx.shortest_path(G, source=orig_node, target=dest_node, weight=fallback)

    dist, t_min = _calc_segment_metrics(G, path, use_traffic)
    route_coords = [[G.nodes[n]['x'], G.nodes[n]['y']] for n in path]
    time_data = _process_time_logic(departure_time, arrival_time, t_min)

    return {
        "route": route_coords, "distance_km": round(dist / 1000, 2), "time_min": round(t_min, 1),
        "start": {"lat": start_coords[0], "lon": start_coords[1]},
        "end": {"lat": end_coords[0], "lon": end_coords[1]},
        "waypoints": [{"lat": start_coords[0], "lon": start_coords[1], "address": "Старт"}, {"lat": end_coords[0], "lon": end_coords[1], "address": "Финиш"}],
        "has_barriers": False, "path": path, **time_data
    }

# ==============================================================================
# МНОГОТОЧЕЧНЫЙ МАРШРУТ (БЕЗ ПЕТЕЛЬ НА СТЫКАХ)
# ==============================================================================
def calculate_multi_point_route(waypoints_addrs: List[str], use_traffic: bool = True, departure_time: str = None, arrival_time: str = None):
    """
    Строит маршрут через N точек без петель на стыках.
    Геокодирует все точки один раз и ищет узлы один раз.
    """
    if len(waypoints_addrs) < 2:
        raise ValueError("Нужно минимум 2 точки")
        
    G, blocked_edges_set = get_graph()
    
    # 1. Геокодируем ВСЕ точки ОДИН РАЗ
    coords = [geocode_address(addr) for addr in waypoints_addrs]
    
    # 2. Находим ближайшие узлы графа ОДИН РАЗ
    nodes = [ox.nearest_nodes(G, X=lon, Y=lat) for lat, lon in coords]
    
    full_path = []
    route_coords = []
    total_distance = 0
    total_time = 0
    
    # 3. Строим путь по сегментам
    for i in range(len(nodes) - 1):
        u, v = nodes[i], nodes[i+1]
        
        def weight_seg(x, y, data):
            min_cost = float('inf')
            valid = False
            for key, d in data.items():
                if (x, y, key) in blocked_edges_set: continue
                length = d.get('length', 0)
                if length < 2.0: continue
                highway = d.get('highway', 'residential')
                if isinstance(highway, list): highway = highway[0]
                penalty = ROAD_PENALTIES.get(highway, 2.0)
                
                if highway in ['residential', 'living_street', 'service']:
                    mid_lat = (G.nodes[x]['y'] + G.nodes[y]['y']) / 2
                    mid_lon = (G.nodes[x]['x'] + G.nodes[y]['x']) / 2
                    start_c, end_c = coords[i], coords[i+1]
                    if (not _is_near_point(mid_lat, mid_lon, start_c[0], start_c[1]) and
                        not _is_near_point(mid_lat, mid_lon, end_c[0], end_c[1])):
                        penalty *= 3.0
                
                if use_traffic:
                    k, _ = get_edge_factor(G.nodes[x]['x'], G.nodes[x]['y'], G.nodes[y]['x'], G.nodes[y]['y'])
                    penalty *= min(k, 2.0)
                    
                cost = length * penalty
                if cost < min_cost: min_cost = cost; valid = True
            return min_cost if valid else float('inf')

        try:
            seg_path = nx.shortest_path(G, source=u, target=v, weight=weight_seg)
        except nx.NetworkXNoPath:
            def fallback(x, y, data): return data.get('length', 0) * 50
            seg_path = nx.shortest_path(G, source=u, target=v, weight=fallback)

        # Собираем путь (убираем дубликат узла на стыке)
        if i == 0:
            full_path.extend(seg_path)
        else:
            full_path.extend(seg_path[1:])
            
        # Считаем метрики сегмента
        seg_dist, seg_time = 0, 0
        for a, b in zip(seg_path[:-1], seg_path[1:]):
            route_coords.append([G.nodes[a]['x'], G.nodes[a]['y']])
            edge_data = list(G[a][b].values())[0]
            length = edge_data.get('length', 0)
            seg_dist += length
            
            highway_type = edge_data.get('highway', 'residential')
            if isinstance(highway_type, list): highway_type = highway_type[0]
            base_speed = SPEED_LIMITS.get(highway_type, 30)
            traffic_k = 1.0
            if use_traffic:
                traffic_k, _ = get_edge_factor(G.nodes[a]['x'], G.nodes[a]['y'], G.nodes[b]['x'], G.nodes[b]['y'])
            
            seg_time += (length / (base_speed / traffic_k / 3.6)) / 60
            if len(seg_path) > 2:
                seg_time += 0.042  # 2.5 сек на узл

        route_coords.append([G.nodes[seg_path[-1]]['x'], G.nodes[seg_path[-1]]['y']])
        total_distance += seg_dist
        total_time += seg_time
        
    # Финальная калибровка
    total_time *= 1.05
    
    # Логика времени
    time_data = _process_time_logic(departure_time, arrival_time, total_time)
    
    # Формируем waypoints для фронтенда
    wps = [{"lat": lat, "lon": lon, "address": addr} for (lat, lon), addr in zip(coords, waypoints_addrs)]

    return {
        "route": route_coords,
        "distance_km": round(total_distance / 1000, 2),
        "time_min": round(total_time, 1),
        "start": wps[0],
        "end": wps[-1],
        "waypoints": wps,
        "has_barriers": False,
        "path": full_path,
        **time_data
    }