import os
import json
import hashlib
import osmnx as ox
import geopandas as gpd
from shapely.geometry import LineString, Point

from .config import BARRIERS_FILENAME, BBOX, BARRIER_TOLERANCE, BARRIER_TYPES

# ✅ Файл для кэша привязанных барьеров
BARRIERS_CACHE_FILE = "kirov_barriers_cache.json"

def load_barriers() -> gpd.GeoDataFrame:
    """Загружает барьеры из файла или скачивает из OSM."""
    if not os.path.exists(BARRIERS_FILENAME):
        print("   Загрузка барьеров из OSM...")
        barriers_gdf = ox.features_from_bbox(bbox=BBOX, tags={'barrier': True})
        
        if 'barrier' in barriers_gdf.columns:
            barriers_gdf = barriers_gdf[barriers_gdf['barrier'].isin(BARRIER_TYPES)]
        
        barriers_gdf.to_file(BARRIERS_FILENAME, driver='GeoJSON')
        print(f"   ✅ Барьеры сохранены: {len(barriers_gdf)}")
    else:
        print(f"   Загрузка барьеров из {BARRIERS_FILENAME}...")
        barriers_gdf = gpd.read_file(BARRIERS_FILENAME)
        print(f"   ✅ Барьеры загружены: {len(barriers_gdf)}")
    
    return barriers_gdf

def _get_file_hash(filepath: str) -> str:
    """Вычисляет MD5 хэш файла для проверки изменений."""
    if not os.path.exists(filepath):
        return ""
    with open(filepath, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()

def _load_blocked_edges_cache() -> set:
    """Загружает кэш заблокированных рёбер из файла."""
    if not os.path.exists(BARRIERS_CACHE_FILE):
        return None
    
    try:
        with open(BARRIERS_CACHE_FILE, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)
        
        # ✅ Проверяем актуальность кэша по хэшам файлов
        graph_hash = _get_file_hash("kirov_road_network.graphml")
        barriers_hash = _get_file_hash(BARRIERS_FILENAME)
        
        if cache_data.get("graph_hash") == graph_hash and \
           cache_data.get("barriers_hash") == barriers_hash:
            print("   ✅ Кэш барьеров актуален, загружаем...")
            # Восстанавливаем set из списка кортежей
            blocked_edges = set(tuple(e) for e in cache_data["blocked_edges"])
            return blocked_edges
        else:
            print("   ⚠️ Кэш устарел, пересчитываем...")
            return None
    except Exception as e:
        print(f"   ⚠️ Ошибка чтения кэша: {e}")
        return None

def _save_blocked_edges_cache(blocked_edges: set, graph_hash: str, barriers_hash: str) -> None:
    """Сохраняет кэш заблокированных рёбер в файл."""
    try:
        cache_data = {
            "graph_hash": graph_hash,
            "barriers_hash": barriers_hash,
            "blocked_edges": [list(e) for e in blocked_edges]  # set → list для JSON
        }
        with open(BARRIERS_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        print(f"   ✅ Кэш барьеров сохранён: {len(blocked_edges)} рёбер")
    except Exception as e:
        print(f"   ⚠️ Ошибка сохранения кэша: {e}")

def map_barriers_to_graph(G, barriers_gdf, tolerance=BARRIER_TOLERANCE) -> set:
    """
    Привязывает барьеры к рёбрам графа.
    
    Args:
        G: NetworkX граф
        barriers_gdf: GeoDataFrame с барьерами
        tolerance: Радиус поиска в метрах
        
    Returns:
        set - множество заблокированных рёбер (u, v, key)
    """
    blocked_edges = set()
    
    if barriers_gdf is None or len(barriers_gdf) == 0:
        return blocked_edges

    barriers_proj = barriers_gdf.to_crs("EPSG:3857").copy()
    _ = barriers_proj.sindex

    print(f"   Привязка {len(barriers_gdf)} барьеров к {len(G.edges)} рёбрам...")
    
    for u, v, key, data in G.edges(keys=True, data=True):
        # Получаем геометрию ребра
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
    
    return blocked_edges

def load_and_map_barriers(G) -> tuple:
    """
    Загружает барьеры и привязывает их к графу с кэшированием.
    
    Returns:
        (G, blocked_edges) - граф с помеченными рёбрами и множество заблокированных рёбер
    """
    # ✅ Пробуем загрузить из кэша
    cached_edges = _load_blocked_edges_cache()
    
    if cached_edges is not None:
        # ✅ Кэш найден и актуален — помечаем рёбра в графе
        print(f"   📂 Загружено {len(cached_edges)} заблокированных рёбер из кэша")
        for (u, v, key) in cached_edges:
            if G.has_edge(u, v, key):
                G.edges[u, v, key]['has_barrier'] = True
        return G, cached_edges
    
    # ✅ Кэша нет или устарел — считаем заново
    barriers_gdf = load_barriers()
    blocked_edges = map_barriers_to_graph(G, barriers_gdf)
    
    # ✅ Сохраняем в кэш
    graph_hash = _get_file_hash("kirov_road_network.graphml")
    barriers_hash = _get_file_hash(BARRIERS_FILENAME)
    _save_blocked_edges_cache(blocked_edges, graph_hash, barriers_hash)
    
    return G, blocked_edges