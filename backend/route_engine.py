# backend/route_engine.py
import os
import osmnx as ox
import networkx as nx
import geopandas as gpd
from shapely.geometry import LineString, Point
from functools import lru_cache

# ✅ Импорт функции чтения цвета дороги со скриншота
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

SPEED_LIMITS = {
    'motorway': 90, 'trunk': 70, 'primary': 60,
    'secondary': 50, 'tertiary': 40, 'residential': 30,
    'service': 20, 'living_street': 20
}

ROAD_PENALTIES = {
    'motorway': 1.0, 'trunk': 1.0, 'primary': 1.1,
    'secondary': 1.2, 'tertiary': 1.3,
    'residential': 3.0, 'living_street': 3.0, 'service': 3.0,
    'unclassified': 1.5
}

_G = None
_BLOCKED_EDGES = None
_BLOCKED_EDGES_SET = None

# ==============================================================================
# ГЕОКОДИРОВАНИЕ
# ==============================================================================
@lru_cache(maxsize=1000)
def geocode_address(address_query, city="Kirov, Russia"):
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
    global _G, _BLOCKED_EDGES, _BLOCKED_EDGES_SET
    
    # ✅ Используем BBOX из config.py для консистентности
    from app.config import BBOX, CUSTOM_FILTER
    bbox = BBOX
    
    if not os.path.exists(GRAPH_FILENAME):
        print("📥 Загрузка графа дорожной сети...")
        _G = ox.graph_from_bbox(
            bbox=bbox, 
            custom_filter=CUSTOM_FILTER, 
            network_type="drive", 
            simplify=False,
            retain_all=False,
            truncate_by_edge=True
        )
        _G = ox.distance.add_edge_lengths(_G)
        ox.save_graphml(_G, filepath=GRAPH_FILENAME)
        print(f"✅ Граф сохранён: {len(_G.nodes)} узлов, {len(_G.edges)} рёбер")
    else:
        print("📂 Загрузка графа из файла...")
        _G = ox.load_graphml(filepath=GRAPH_FILENAME)
        print(f"✅ Граф загружён: {len(_G.nodes)} узлов, {len(_G.edges)} рёбер")

    # Барьеры
    if not os.path.exists(BARRIERS_FILENAME):
        print("🚧 Загрузка барьеров...")
        barriers_gdf = ox.features_from_bbox(bbox=bbox, tags={'barrier': True})
        barrier_types = ['gate', 'lift_gate', 'swing_gate', 'sliding_gate', 'barrier', 'bollard', 'chain']
        if 'barrier' in barriers_gdf.columns:
            barriers_gdf = barriers_gdf[barriers_gdf['barrier'].isin(barrier_types)]
        barriers_gdf.to_file(BARRIERS_FILENAME, driver='GeoJSON')
        print(f"✅ Барьеры сохранены: {len(barriers_gdf)}")
    else:
        barriers_gdf = gpd.read_file(BARRIERS_FILENAME)
        print(f"✅ Барьеры загружены: {len(barriers_gdf)}")

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
        if 'geometry' in data and data['geometry'] is not None:
            edge_geom = data['geometry']
        else:
            node_u, node_v = G.nodes[u], G.nodes[v]
            edge_geom = LineString([(node_u['x'], node_u['y']), (node_v['x'], node_v['y'])])

        edge_gdf = gpd.GeoDataFrame(geometry=[edge_geom], crs="EPSG:4326")
        edge_geom_proj = edge_gdf.to_crs("EPSG:3857").geometry.iloc[0]
        search_box = edge_geom_proj.buffer(tolerance).bounds
        candidates = list(barriers_proj.sindex.intersection(search_box))

        for idx in candidates:
            barrier = barriers_proj.iloc[idx]
            if isinstance(barrier.geometry, Point):
                dist = edge_geom_proj.distance(barrier.geometry)
                if dist <= tolerance:
                    blocked_edges.add((u, v, key))
                    G.edges[u, v, key]['has_barrier'] = True
                    G.edges[u, v, key]['barrier_type'] = barrier.get('barrier', 'unknown')
    return G, blocked_edges

# ==============================================================================
# РАСЧЁТ МАРШРУТА (с учётом пробок)
# ==============================================================================
def calculate_route(start_addr, end_addr, use_traffic=True):
    """Основная функция: адреса → маршрут с учётом пробок"""
    G, blocked_edges_set = get_graph()
    
    start_coords = geocode_address(start_addr)
    end_coords = geocode_address(end_addr)
    
    orig_node = ox.nearest_nodes(G, X=start_coords[1], Y=start_coords[0])
    dest_node = ox.nearest_nodes(G, X=end_coords[1], Y=end_coords[0])
    
    # ✅ Весовая функция с динамическим множителем из скриншота
    def weight(u, v, data):
        for key in G.get_edge_data(u, v).keys():
            if (u, v, key) in blocked_edges_set:
                return float('inf')
        
        length = data.get('length', 0)
        highway = data.get('highway', 'residential')
        if isinstance(highway, list):
            highway = highway[0]
        penalty = ROAD_PENALTIES.get(highway, 2.0)
        
        # ✅ Читаем цвет дороги со скриншота и умножаем вес
        if use_traffic:
            lon1, lat1 = G.nodes[u]['x'], G.nodes[u]['y']
            lon2, lat2 = G.nodes[v]['x'], G.nodes[v]['y']
            k, _ = get_edge_factor(lon1, lat1, lon2, lat2)  # ← Использует OFFSET_X/Y из config
            penalty *= k  # 1.0 * 1.8 = 1.8 (красная пробка)
        
        return length * penalty

    try:
        path = nx.astar_path(
            G, source=orig_node, target=dest_node, weight=weight,
            heuristic=lambda u, v: ((G.nodes[u]['x'] - G.nodes[v]['x'])**2 + 
                                    (G.nodes[u]['y'] - G.nodes[v]['y'])**2)**0.5
        )
        used_fallback = False
    except nx.NetworkXNoPath:
        def fallback_weight(u, v, data):
            length = data.get('length', 0)
            for key in G.get_edge_data(u, v).keys():
                if (u, v, key) in blocked_edges_set:
                    return length + 10000
            return length
        path = nx.astar_path(
            G, source=orig_node, target=dest_node, weight=fallback_weight,
            heuristic=lambda u, v: ((G.nodes[u]['x'] - G.nodes[v]['x'])**2 + 
                                    (G.nodes[u]['y'] - G.nodes[v]['y'])**2)**0.5
        )
        used_fallback = True

    # Сбор метрик
    route_coords = []
    distance_meters = 0
    estimated_time_minutes = 0
    
    for u, v in zip(path[:-1], path[1:]):
        route_coords.append([G.nodes[u]['x'], G.nodes[u]['y']])
        edge_data = G.get_edge_data(u, v, key=0)
        length = edge_data.get('length', 0)
        distance_meters += length
        highway_type = edge_data.get('highway', 'residential')
        if isinstance(highway_type, list):
            highway_type = highway_type[0]
        speed_kmh = SPEED_LIMITS.get(highway_type, 30)
        estimated_time_minutes += (length / (speed_kmh / 3.6)) / 60
    
    route_coords.append([G.nodes[path[-1]]['x'], G.nodes[path[-1]]['y']])
    
    return {
        "route": route_coords,
        "distance_km": round(distance_meters / 1000, 2),
        "time_min": round(estimated_time_minutes, 1),
        "start": {"lat": start_coords[0], "lon": start_coords[1]},
        "end": {"lat": end_coords[0], "lon": end_coords[1]},
        "has_barriers": used_fallback,
        "path": path
    }

def calculate_route_by_coords(start_coords, end_coords, use_traffic=True):
    """Вариант с координатами напрямую: (lat, lon)"""
    G, blocked_edges_set = get_graph()

    orig_node = ox.nearest_nodes(G, X=start_coords[1], Y=start_coords[0])
    dest_node = ox.nearest_nodes(G, X=end_coords[1], Y=end_coords[0])

    def weight(u, v, data):
        for key in G.get_edge_data(u, v).keys():
            if (u, v, key) in blocked_edges_set:
                return float('inf')
        length = data.get('length', 0)
        highway = data.get('highway', 'residential')
        if isinstance(highway, list):
            highway = highway[0]
        penalty = ROAD_PENALTIES.get(highway, 2.0)
        
        if use_traffic:
            lon1, lat1 = G.nodes[u]['x'], G.nodes[u]['y']
            lon2, lat2 = G.nodes[v]['x'], G.nodes[v]['y']
            k, _ = get_edge_factor(lon1, lat1, lon2, lat2)
            penalty *= k
        return length * penalty

    try:
        path = nx.astar_path(
            G, source=orig_node, target=dest_node, weight=weight,
            heuristic=lambda u, v: ((G.nodes[u]['x'] - G.nodes[v]['x'])**2 + 
                                    (G.nodes[u]['y'] - G.nodes[v]['y'])**2)**0.5
        )
        used_fallback = False
    except nx.NetworkXNoPath:
        def fallback_weight(u, v, data):
            length = data.get('length', 0)
            for key in G.get_edge_data(u, v).keys():
                if (u, v, key) in blocked_edges_set:
                    return length + 10000
            return length
        path = nx.astar_path(
            G, source=orig_node, target=dest_node, weight=fallback_weight,
            heuristic=lambda u, v: ((G.nodes[u]['x'] - G.nodes[v]['x'])**2 + 
                                    (G.nodes[u]['y'] - G.nodes[v]['y'])**2)**0.5
        )
        used_fallback = True

    route_coords = []
    distance_meters = 0
    estimated_time_minutes = 0
    for u, v in zip(path[:-1], path[1:]):
        route_coords.append([G.nodes[u]['x'], G.nodes[u]['y']])
        edge_data = G.get_edge_data(u, v, key=0)
        length = edge_data.get('length', 0)
        distance_meters += length
        highway_type = edge_data.get('highway', 'residential')
        if isinstance(highway_type, list):
            highway_type = highway_type[0]
        speed_kmh = SPEED_LIMITS.get(highway_type, 30)
        estimated_time_minutes += (length / (speed_kmh / 3.6)) / 60
    route_coords.append([G.nodes[path[-1]]['x'], G.nodes[path[-1]]['y']])

    return {
        "route": route_coords,
        "distance_km": round(distance_meters / 1000, 2),
        "time_min": round(estimated_time_minutes, 1),
        "start": {"lat": start_coords[0], "lon": start_coords[1]},
        "end": {"lat": end_coords[0], "lon": end_coords[1]},
        "has_barriers": used_fallback,
        "path": path
    }