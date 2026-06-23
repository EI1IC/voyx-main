# backend/route_engine.py
import os
import math
import datetime
import pickle
import osmnx as ox
import networkx as nx
import geopandas as gpd
from shapely.geometry import LineString, Point
from functools import lru_cache
from typing import List

from app.traffic_screen import get_edge_factor, _edge_factor_cache

# ==============================================================================
# КОНФИГУРАЦИЯ
# ==============================================================================
ox.settings.log_console = False
ox.settings.use_cache = True
ox.settings.timeout = 180

GRAPH_FILENAME = "kirov_road_network.graphml"
BARRIERS_FILENAME = "kirov_barriers.geojson"
BARRIER_TOLERANCE = 7

from app.config import *

_G = None
_BLOCKED_EDGES = None
_BLOCKED_EDGES_SET = None

# Кэш коэффициентов для рёбер в рамках одного запроса
_edge_k_cache = {}

# Кэш для интерполированных значений
_interpolated_k_cache = {}

import json
from pathlib import Path

_KNOWN_ADDRESSES_FILE = Path(__file__).parent / "known_addresses.json"
_KNOWN_ADDRESSES_CACHE = {}
_KNOWN_ADDRESSES_MTIME = None

def _load_known_addresses():
    """
    Загружает словарь известных адресов из JSON-файла.
    Автоматически перезагружает при изменении файла (для разработки).
    """
    global _KNOWN_ADDRESSES_CACHE, _KNOWN_ADDRESSES_MTIME
    
    try:
        stat = _KNOWN_ADDRESSES_FILE.stat()
        mtime = stat.st_mtime
        
        # Если файл не менялся — используем кэш
        if _KNOWN_ADDRESSES_MTIME == mtime and _KNOWN_ADDRESSES_CACHE:
            return _KNOWN_ADDRESSES_CACHE
        
        # Загружаем файл
        with open(_KNOWN_ADDRESSES_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Фильтруем служебные ключи (начинаются с _)
        _KNOWN_ADDRESSES_CACHE = {
            k.lower(): tuple(v) 
            for k, v in data.items() 
            if not k.startswith('_')
        }
        _KNOWN_ADDRESSES_MTIME = mtime
        
        logger.info(f"📚 Загружено {len(_KNOWN_ADDRESSES_CACHE)} известных адресов из {_KNOWN_ADDRESSES_FILE.name}")
        return _KNOWN_ADDRESSES_CACHE
        
    except FileNotFoundError:
        logger.warning(f"⚠️ Файл известных адресов не найден: {_KNOWN_ADDRESSES_FILE}")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"❌ Ошибка в JSON-файле известных адресов: {e}")
        return {}
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки известных адресов: {e}")
        return {}

# ==============================================================================
# ГЕОКОДИРОВАНИЕ С ПРОВЕРКОЙ СЛОВАРЯ
# ==============================================================================
@lru_cache(maxsize=1000)
def geocode_address(address_query, city="Kirov, Russia"):
    """
    Геокодирует адрес.
    Сначала проверяет словарь известных адресов, потом osmnx.
    """
    import re
    
    # ✅ Проверка номера дома (отрицательный или нулевой)
    numbers = re.findall(r'-?\d+', address_query)
    if numbers:
        house_number = int(numbers[-1])
        if house_number <= 0:
            raise ValueError(
                f"Некорректный номер дома в адресе '{address_query}': "
                f"номер должен быть больше 0."
            )
    
    # ✅ ПРОВЕРКА СЛОВАРЯ известных адресов
    known_coords = _lookup_known_address(address_query)
    if known_coords:
        lat, lon = known_coords
        logger.info(f"📚 '{address_query}' → ({lat:.6f}, {lon:.6f}) из словаря")
        return lat, lon
    
    # ✅ Если в словаре нет — идём в osmnx
    if "Киров" in address_query or "Kirov" in address_query:
        full_query = address_query
    else:
        full_query = f"{address_query}, {city}"
    
    try:
        location = ox.geocode(full_query)
        lat, lon = float(location[0]), float(location[1])
    except Exception as e:
        raise ValueError(f"Не удалось найти адрес '{address_query}': {str(e)}")
    
    # ✅ Проверка границ Кирова
    KIROV_BBOX = {
        'min_lat': 58.556461,
        'max_lat': 58.647655,
        'min_lon': 49.540100,
        'max_lon': 49.714165,
    }
    
    if not (KIROV_BBOX['min_lat'] <= lat <= KIROV_BBOX['max_lat'] and
            KIROV_BBOX['min_lon'] <= lon <= KIROV_BBOX['max_lon']):
        raise ValueError(
            f"Адрес '{address_query}' найден за пределами Кирова "
            f"(координаты: {lat:.4f}, {lon:.4f}). "
            f"Убедитесь, что адрес указан правильно."
        )
    
    return lat, lon


def _lookup_known_address(address_query):
    """
    Ищет адрес в JSON-файле известных адресов.
    Возвращает (lat, lon) или None.
    """
    import re
    
    # Загружаем словарь (с кэшированием)
    known = _load_known_addresses()
    if not known:
        return None
    
    # Нормализуем адрес
    addr = address_query.lower().strip()
    
    # Убираем всё лишнее: город, тип улицы, слово "дом", запятые
    addr = re.sub(r'\b(киров|kirov)\b', '', addr, flags=re.IGNORECASE)
    addr = re.sub(
        r'\b(улица|ул\.|проспект|пр\.|пр-т|переулок|пер\.|бульвар|бул\.)\s*',
        '', addr
    )
    addr = re.sub(
        r'\b(дом|д\.|корпус|к\.|строение|стр\.|литер|лит\.)\s*',
        '', addr
    )
    addr = addr.replace(',', ' ').strip()
    addr = ' '.join(addr.split())
    
    # ✅ 1. ТОЧНОЕ совпадение
    if addr in known:
        return known[addr]
    
    # ✅ 2. Совпадение по улице + номеру дома (умная проверка)
    # Извлекаем название улицы и номер дома из нормализованного адреса
    match = re.match(r'^(.+?)\s+(\d+(?:[а-яА-Я]|\/\d+)?)\s*$', addr)
    if match:
        street = match.group(1).strip()
        house = match.group(2).strip()
        
        # Ищем в словаре адрес с такой же улицей и номером дома
        for key, coords in known.items():
            key_match = re.match(r'^(.+?)\s+(\d+(?:[а-яА-Я]|\/\d+)?)\s*$', key)
            if key_match:
                key_street = key_match.group(1).strip()
                key_house = key_match.group(2).strip()
                
                # ✅ Проверяем ТОЧНОЕ совпадение улицы И номера дома
                if key_street == street and key_house == house:
                    return coords
    
    return None

# ==============================================================================
# УПРАВЛЕНИЕ ГРАФОМ
# ==============================================================================
def init_graph():
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
        print(f"✅ Граф загружен: {len(_G.nodes)} узлов, {len(_G.edges)} рёбер")

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
    if _G is None: init_graph()
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
    lat_diff = abs(lat - point_lat) * 111000
    lon_diff = abs(lon - point_lon) * 111000 * math.cos(math.radians(point_lat))
    return math.sqrt(lat_diff**2 + lon_diff**2) <= radius_m

def _process_time_logic(departure_time_str, arrival_time_str, calc_time_minutes):
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
        if abs((arr_dt - expected_arr).total_seconds()) > 120:
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

def _get_edge_k_cached(u, v, G, use_traffic):
    """Получает коэффициент пробок для ребра из кэша"""
    if not use_traffic:
        return 1.0
    
    cache_key = (u, v)
    
    # Проверяем кэш рёбер (предварительно вычисленный)
    if cache_key in _edge_factor_cache:
        return _edge_factor_cache[cache_key]
    
    # Если кэша нет (fallback) — вычисляем
    traffic_k, _ = get_edge_factor(G.nodes[u]['x'], G.nodes[u]['y'], G.nodes[v]['x'], G.nodes[v]['y'])
    _edge_factor_cache[cache_key] = traffic_k
    return traffic_k

# ==============================================================================
# 🆕 ЛИНЕЙНАЯ ИНТЕРПОЛЯЦИЯ КОЭФФИЦИЕНТОВ ПРОБОК
# ==============================================================================
def get_edge_k_for_time(u, v, G, use_traffic, start_time: datetime.datetime = None):
    """
    Получает коэффициент пробок для ребра с учетом временной интерполяции.
    Если start_time указан, интерполирует между двумя ближайшими временными срезами.
    """
    if not use_traffic:
        return 1.0
    
    cache_key = (u, v, start_time.isoformat() if start_time else None)
    
    # Проверяем кэш интерполированных значений
    if cache_key in _interpolated_k_cache:
        return _interpolated_k_cache[cache_key]
    
    # Если время не указано или это "сейчас" - используем текущий кэш
    if start_time is None:
        traffic_k = _get_edge_k_cached(u, v, G, use_traffic)
        _interpolated_k_cache[cache_key] = traffic_k
        return traffic_k
    
    # Находим два ближайших временных среза
    k1, k2, t1, t2, alpha = _find_bracketing_caches(u, v, start_time)
    
    if k1 is None or k2 is None:
        # Fallback: если не нашли оба кэша, используем ближайший
        traffic_k = _get_edge_k_cached(u, v, G, use_traffic)
        _interpolated_k_cache[cache_key] = traffic_k
        return traffic_k
    
    # Линейная интерполяция: k(t) = k1 + (k2 - k1) * alpha
    if alpha == 0:
        traffic_k = k1
    elif alpha == 1:
        traffic_k = k2
    else:
        traffic_k = k1 + (k2 - k1) * alpha
    
    _interpolated_k_cache[cache_key] = traffic_k
    return traffic_k

def _find_bracketing_caches(u, v, start_time: datetime.datetime):
    """
    Находит два ближайших временных среза (до и после start_time)
    и возвращает их коэффициенты для ребра (u, v).
    
    Использует ЧАСОВУЮ интерполяцию: если время 7:32, берутся кэши за 7:00 и 8:00.
    
    Returns:
        (k1, k2, t1, t2, alpha) где:
        - k1, k2 - коэффициенты для t1 и t2
        - t1, t2 - временные метки (datetime)
        - alpha - вес для интерполяции (0 = только k1, 1 = только k2)
    """
    from app.traffic_screen import SCREENSHOTS_DIR, get_screenshot_for_datetime
    
    # ✅ Округляем время вниз до целого часа (например, 7:32 → 7:00)
    t1 = _floor_to_hour(start_time)
    # ✅ Следующий час (например, 7:00 → 8:00)
    t2 = t1 + datetime.timedelta(hours=1)
    
    # Загружаем кэши для обоих временных точек
    k1 = _load_edge_k_from_cache(u, v, t1)
    k2 = _load_edge_k_from_cache(u, v, t2)
    
    # Если один из кэшей отсутствует, используем fallback
    if k1 is None or k2 is None:
        return None, None, None, None, 0
    
    # ✅ Вычисляем alpha (вес для интерполяции) - делим на 1 час (3600 секунд)
    time_diff = (start_time - t1).total_seconds()
    interval_seconds = 60 * 60  # ✅ 1 час в секундах (было 15 * 60)
    alpha = time_diff / interval_seconds
    
    return k1, k2, t1, t2, alpha

def _floor_to_hour(dt: datetime.datetime) -> datetime.datetime:
    """Округляет время вниз до ближайшего целого часа."""
    return dt.replace(minute=0, second=0, microsecond=0)


_loaded_cache_files = {}  # Кэш загруженных файлов
import logging
logger = logging.getLogger(__name__)
def _load_edge_k_from_cache(u, v, dt: datetime.datetime):
    """
    Загружает коэффициент пробок для ребра (u, v) из кэша для времени dt.
    """
    try:
        from app.traffic_screen import get_screenshot_for_datetime
        
        screenshot_path = get_screenshot_for_datetime(dt)
        cache_path = screenshot_path.with_suffix('.edge_cache.pkl')
        
        if not cache_path.exists():
            return None
        
        # ✅ Кэшируем загруженный файл в памяти
        cache_key = str(cache_path)
        if cache_key not in _loaded_cache_files:
            logger.info(f"📂 Loading cache file from disk: {cache_path.name}")
            with open(cache_path, 'rb') as f:
                _loaded_cache_files[cache_key] = pickle.load(f)
        else:
            logger.debug(f"📦 Using cached file from memory: {cache_path.name}")
        
        edge_cache = _loaded_cache_files[cache_key]
        return edge_cache.get((u, v), 1.0)
        
    except Exception as e:
        logger.error(f"❌ Error loading cache for edge ({u},{v}) at {dt}: {e}", exc_info=True)
        return None


def clear_interpolation_cache():
    """Очищает кэш интерполированных значений и загруженных файлов."""
    global _interpolated_k_cache, _loaded_cache_files
    _interpolated_k_cache.clear()
    _loaded_cache_files.clear()  # ✅ Очищаем кэш файлов при смене времени
    logger.info("🧹 Interpolation cache cleared")

# ==============================================================================
# РАСЧЁТ МЕТРИК СЕГМЕНТА
# ==============================================================================
def _calc_segment_metrics(G, path, use_traffic, current_time=None):
    distance_meters = 0
    estimated_time_minutes = 0
    
    for i, (u, v) in enumerate(zip(path[:-1], path[1:])):
        edge_data = list(G[u][v].values())[0]
        length = edge_data.get('length', 0)
        distance_meters += length
        
        highway_type = edge_data.get('highway', 'residential')
        if isinstance(highway_type, list): highway_type = highway_type[0]
        base_speed = SPEED_LIMITS.get(highway_type, 30)
        
        # ✅ ИСПОЛЬЗУЕМ ИНТЕРПОЛЯЦИЮ ЕСЛИ ЕСТЬ ВРЕМЯ
        if current_time:
            traffic_k = get_edge_k_for_time(u, v, G, use_traffic, current_time)
        else:
            traffic_k = _get_edge_k_cached(u, v, G, use_traffic)
        
        effective_speed = base_speed / traffic_k
        estimated_time_minutes += (length / (effective_speed / 3.6)) / 60
        
        if 0 < i < len(path) - 2:
            estimated_time_minutes += 0.08
            
    return distance_meters, estimated_time_minutes * 1.1

# ==============================================================================
# РАСЧЁТ МАРШРУТА (ОДНА ТОЧКА -> ДРУГАЯ)
# ==============================================================================
def calculate_route(start_addr, end_addr, use_traffic=True, departure_time=None, arrival_time=None):
    G, blocked_edges_set = get_graph()
    start_coords = geocode_address(start_addr)
    end_coords = geocode_address(end_addr)
    
    orig_node = ox.nearest_nodes(G, X=start_coords[1], Y=start_coords[0])
    dest_node = ox.nearest_nodes(G, X=end_coords[1], Y=end_coords[0])
    
    # Парсим время выезда для интерполяции
    dep_dt = None
    if departure_time:
        try:
            dep_dt = datetime.datetime.fromisoformat(departure_time.replace("Z", "+00:00"))
        except:
            pass
    
    # ОДНОЭТАПНЫЙ АЛГОРИТМ С ПРОБКАМИ И ИНТЕРПОЛЯЦИЕЙ
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
            
            # ✅ ИСПОЛЬЗУЕМ ИНТЕРПОЛЯЦИЮ
            if use_traffic and dep_dt:
                traffic_k = get_edge_k_for_time(u, v, G, use_traffic, dep_dt)
            else:
                traffic_k = _get_edge_k_cached(u, v, G, use_traffic)
            
            penalty *= min(traffic_k, 2.0)
            
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

    # ✅ Передаём время в расчёт метрик
    dist, t_min = _calc_segment_metrics(G, path, use_traffic, dep_dt)
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

    # Парсим время выезда для интерполяции
    dep_dt = None
    if departure_time:
        try:
            dep_dt = datetime.datetime.fromisoformat(departure_time.replace("Z", "+00:00"))
        except:
            pass

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
            
            # ✅ ИСПОЛЬЗУЕМ ИНТЕРПОЛЯЦИЮ
            if use_traffic and dep_dt:
                traffic_k = get_edge_k_for_time(u, v, G, use_traffic, dep_dt)
            else:
                traffic_k = _get_edge_k_cached(u, v, G, use_traffic)
            
            penalty *= min(traffic_k, 2.0)
            cost = length * penalty
            if cost < min_cost: min_cost = cost; valid_found = True
        return min_cost if valid_found else float('inf')

    try:
        path = nx.shortest_path(G, source=orig_node, target=dest_node, weight=weight)
    except nx.NetworkXNoPath:
        def fallback(u, v, data): return data.get('length', 0) * 50
        path = nx.shortest_path(G, source=orig_node, target=dest_node, weight=fallback)

    # ✅ Передаём время в расчёт метрик
    dist, t_min = _calc_segment_metrics(G, path, use_traffic, dep_dt)
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
# МНОГОТОЧЕЧНЫЙ МАРШРУТ
# ==============================================================================
# ==============================================================================
# 🆕 ОПТИМИЗАЦИЯ ПОРЯДКА ТОЧЕК (TSP / 2-OPT)
# ==============================================================================
def _build_time_matrix(G, coords, use_traffic, base_time: datetime.datetime = None):
    """Строит матрицу времени проезда между всеми парами точек."""
    n = len(coords)
    matrix = [[0.0] * n for _ in range(n)]
    
    for i in range(n):
        for j in range(n):
            if i != j:
                # Используем существующую функцию для точного расчета времени между двумя точками
                # Она уже учитывает барьеры, пробки и интерполяцию
                result = calculate_route_by_coords(
                    start_coords=coords[i], 
                    end_coords=coords[j], 
                    use_traffic=use_traffic, 
                    departure_time=base_time.isoformat() if base_time else None
                )
                matrix[i][j] = result["time_min"]
    return matrix

def _optimize_order_2opt(indices, time_matrix):
    """
    Эвристика 2-opt для оптимизации порядка точек.
    ВАЖНО: Индексы 0 (старт) и N-1 (финиш) остаются на своих местах!
    """
    n = len(indices)
    if n <= 3:
        return indices  # Оптимизация не нужна для 2-3 точек

    best_order = indices[:]
    improved = True
    
    while improved:
        improved = False
        # Перебираем только внутренние точки (от 1 до n-2)
        for i in range(1, n - 2):
            for j in range(i + 1, n - 1):
                # Разворачиваем участок маршрута от i до j
                new_order = best_order[:i] + best_order[i:j+1][::-1] + best_order[j+1:]
                
                # Считаем время нового и старого маршрута по матрице
                new_time = sum(time_matrix[new_order[k]][new_order[k+1]] for k in range(n-1))
                old_time = sum(time_matrix[best_order[k]][best_order[k+1]] for k in range(n-1))
                
                # Если стало быстрее, сохраняем и продолжаем поиск
                if new_time < old_time:
                    best_order = new_order
                    improved = True
                    
    return best_order

# ==============================================================================
# МНОГОТОЧЕЧНЫЙ МАРШРУТ (ОБНОВЛЕННАЯ ВЕРСИЯ)
# ==============================================================================
def calculate_multi_point_route(waypoints_addrs: List[str], use_traffic: bool = True, departure_time: str = None, arrival_time: str = None, optimize_order: bool = False):
    if len(waypoints_addrs) < 2:
        raise ValueError("Нужно минимум 2 точки")
        
    G, blocked_edges_set = get_graph()
    
    # 1. Геокодируем все адреса
    coords = [geocode_address(addr) for addr in waypoints_addrs]
    n = len(coords)
    
    # 2. Если включена оптимизация, переставляем точки
    if optimize_order and n > 2:
        logger.info(f"🔄 Запуск оптимизации порядка {n} точек (2-opt)...")
        
        # Парсим базовое время для построения матрицы
        base_time = None
        if departure_time:
            try: base_time = datetime.datetime.fromisoformat(departure_time.replace("Z", "+00:00"))
            except: pass
            
        # Строим матрицу времени N x N
        time_matrix = _build_time_matrix(G, coords, use_traffic, base_time)
        
        # Оптимизируем порядок (индексы от 0 до n-1)
        initial_indices = list(range(n))
        optimized_indices = _optimize_order_2opt(initial_indices, time_matrix)
        
        # Пересобираем координаты и адреса в новом порядке
        coords = [coords[i] for i in optimized_indices]
        waypoints_addrs = [waypoints_addrs[i] for i in optimized_indices]
        
        logger.info(f"✅ Порядок оптимизирован. Экономия времени по матрице достигнута.")

    # 3. Дальше идет стандартный расчет маршрута по уже (возможно) оптимизированному списку
    nodes = [ox.nearest_nodes(G, X=lon, Y=lat) for lat, lon in coords]
    
    full_path = []
    route_coords = []
    total_distance = 0
    total_time = 0
    
    current_time = None
    if departure_time:
        try: current_time = datetime.datetime.fromisoformat(departure_time.replace("Z", "+00:00"))
        except: pass
    
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
                
                if use_traffic and current_time:
                    traffic_k = get_edge_k_for_time(x, y, G, use_traffic, current_time)
                else:
                    traffic_k = _get_edge_k_cached(x, y, G, use_traffic)
                
                penalty *= min(traffic_k, 2.0)
                cost = length * penalty
                if cost < min_cost: min_cost = cost; valid = True
            return min_cost if valid else float('inf')

        try:
            seg_path = nx.shortest_path(G, source=u, target=v, weight=weight_seg)
        except nx.NetworkXNoPath:
            def fallback(x, y, data): return data.get('length', 0) * 50
            seg_path = nx.shortest_path(G, source=u, target=v, weight=fallback)

        if i == 0: full_path.extend(seg_path)
        else: full_path.extend(seg_path[1:])
            
        seg_dist, seg_time = 0, 0
        for a, b in zip(seg_path[:-1], seg_path[1:]):
            route_coords.append([G.nodes[a]['x'], G.nodes[a]['y']])
            edge_data = list(G[a][b].values())[0]
            length = edge_data.get('length', 0)
            seg_dist += length
            
            highway_type = edge_data.get('highway', 'residential')
            if isinstance(highway_type, list): highway_type = highway_type[0]
            base_speed = SPEED_LIMITS.get(highway_type, 30)
            
            if use_traffic and current_time:
                traffic_k = get_edge_k_for_time(a, b, G, use_traffic, current_time)
            else:
                traffic_k = _get_edge_k_cached(a, b, G, use_traffic)
            
            seg_time += (length / (base_speed / traffic_k / 3.6)) / 60
            if len(seg_path) > 2: seg_time += 0.08

        route_coords.append([G.nodes[seg_path[-1]]['x'], G.nodes[seg_path[-1]]['y']])
        total_distance += seg_dist
        total_time += seg_time
        
        if current_time:
            current_time += datetime.timedelta(minutes=seg_time)
        
    total_time *= 1.1
    
    time_data = _process_time_logic(departure_time, arrival_time, total_time)
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
        "order_optimized": optimize_order, # ✅ Флаг для фронтенда
        **time_data
    }