import json
import time
from pathlib import Path
import networkx as nx
from shapely.geometry import LineString
import logging
from typing import Dict

logger = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).parent.parent / "traffic_cache.json"
COLOR_FACTOR = {"green": 1.0, "yellow": 1.3, "red": 1.8, "darkred": 2.5, "gray": 1.0}

def load_traffic_cache() -> list:
    if not CACHE_PATH.exists():
        return []
    try:
        data = json.loads(CACHE_PATH.read_text())
        # Кэш старше 1 часа — не используем
        if time.time() - data.get("timestamp", 0) > 3600:
            logger.warning("⚠️ Кэш просрочен")
            return []
        return data.get("segments", [])
    except Exception as e:
        logger.error(f"Ошибка чтения кэша: {e}")
        return []

def map_traffic_to_graph(G: nx.Graph, segments: list, max_distance_deg=0.0003) -> nx.Graph:
    """Сопоставляет сегменты Яндекса с рёбрами графа"""
    G_traffic = G.copy()
    updated_count = 0
    
    for seg in segments:
        if not seg.get("coords") or seg["color"] == "gray":
            continue
            
        factor = COLOR_FACTOR.get(seg["color"], 1.0)
        coords = [(c[0], c[1]) for c in seg["coords"]]
        seg_line = LineString(coords)
        
        for u, v, data in G_traffic.edges(data=True):
            u_lon, u_lat = G_traffic.nodes[u]["x"], G_traffic.nodes[u]["y"]
            v_lon, v_lat = G_traffic.nodes[v]["x"], G_traffic.nodes[v]["y"]
            edge_line = LineString([(u_lon, u_lat), (v_lon, v_lat)])
            
            if seg_line.distance(edge_line) < max_distance_deg:
                data["congestion_factor"] = factor
                data["traffic_color"] = seg["color"]
                updated_count += 1
                
    logger.info(f"🔗 Обновлено рёбер: {updated_count}")
    return G_traffic

def apply_traffic_to_graph(G: nx.Graph) -> nx.Graph:
    """Загружает кэш и применяет к графу"""
    segments = load_traffic_cache()
    if not segments:
        logger.warning("⚠️ Нет данных о пробках, используем базовые веса")
        return G
    return map_traffic_to_graph(G, segments)