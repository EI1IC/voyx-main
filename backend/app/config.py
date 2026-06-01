# backend/app/config.py
import osmnx as ox
import os

# ==============================================================================
# НАСТРОЙКИ OSMNX
# ==============================================================================
ox.settings.log_console = False
ox.settings.use_cache = True
ox.settings.timeout = 180

# ==============================================================================
# ФАЙЛЫ
# ==============================================================================
_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GRAPH_FILENAME = os.path.join(_BASE_DIR, "kirov_road_network.graphml")
BARRIERS_FILENAME = os.path.join(_BASE_DIR, "kirov_barriers.geojson")

# ==============================================================================
# ГРАНИЦЫ И ПРОЕКЦИЯ
# ==============================================================================

# Единые границы для графа и скриншота (west, south, east, north)
BBOX = (49.540100, 58.556461, 49.714165, 58.647655)
FULL_CITY_BBOX = BBOX  # ✅ Используем одинаковые границы везде

# Размер вьюпорта при захвате (должен совпадать с реальным traffic.png)
TRAFFIC_VIEWPORT = {"width": 1920, "height": 1080}

# Отступы интерфейса Яндекса
MARGIN_X = 20
MARGIN_Y_TOP = 30
MARGIN_Y_BOTTOM = 30

# ==============================================================================
# БАРЬЕРЫ
# ==============================================================================
BARRIER_TOLERANCE = 5  # Метры для привязки барьера к дороге
BARRIER_TYPES = [
    'gate', 'lift_gate', 'swing_gate', 'sliding_gate',
    'barrier', 'bollard', 'chain'
]

# ==============================================================================
# ДОРОГИ (фильтр OSM)
# ==============================================================================
CUSTOM_FILTER = (
    '["highway"]["area"!~"yes"]'
    '["highway"!~"footway|path|cycleway|bridleway|steps|corridor|elevator|pedestrian|track"]'
)

# ==============================================================================
# ВЕСА МАРШРУТИЗАЦИИ
# ==============================================================================
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