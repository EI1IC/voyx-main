import os
import osmnx as ox
import networkx as nx

from .config import GRAPH_FILENAME, BBOX, CUSTOM_FILTER
from .barriers import load_and_map_barriers

# Глобальное хранилище (кэш)
_graph_cache = {
    "G": None,
    "blocked_edges_set": None
}

def init_graph() -> None:
    """
    Инициализация графа при старте приложения.
    Загружает граф, барьеры и привязывает их к рёбрам.
    """
    print("📥 Инициализация графа дорожной сети...")
    
    # Загрузка или создание графа
    if not os.path.exists(GRAPH_FILENAME):
        print("   Загрузка из OSM...")
        G = ox.graph_from_bbox(
            bbox=BBOX,
            custom_filter=CUSTOM_FILTER,
            network_type="drive",
            simplify=True,
            retain_all=False,
            truncate_by_edge=True
        )
        G = ox.distance.add_edge_lengths(G)
        ox.save_graphml(G, filepath=GRAPH_FILENAME)
        print(f"   ✅ Граф сохранён: {len(G.nodes)} узлов, {len(G.edges)} рёбер")
    else:
        print(f"   Загрузка из {GRAPH_FILENAME}...")
        G = ox.load_graphml(filepath=GRAPH_FILENAME)
        print(f"   ✅ Граф загружён: {len(G.nodes)} узлов, {len(G.edges)} рёбер")
    
    # Загрузка и привязка барьеров
    G, blocked_edges = load_and_map_barriers(G)
    
    # Сохраняем в кэш
    _graph_cache["G"] = G
    _graph_cache["blocked_edges_set"] = set(blocked_edges)
    
    print(f"✅ Заблокировано рёбер: {len(_graph_cache['blocked_edges_set'])}")
    print("✅ Система готова к работе")

def get_graph() -> tuple:
    """
    Возвращает кэшированный граф и множество заблокированных рёбер.
    
    Returns:
        (G, blocked_edges_set) - граф и множество заблокированных рёбер
    """
    if _graph_cache["G"] is None:
        init_graph()
    return _graph_cache["G"], _graph_cache["blocked_edges_set"]