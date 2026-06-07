# backend/app/config.py
import osmnx as ox
import os
from pathlib import Path

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

# ✅ METADATA_FILENAME как Path объект
METADATA_FILENAME = Path(_BASE_DIR) / "screenshots" / "metadata.json"

# ==============================================================================
# ГРАНИЦЫ И ПРОЕКЦИЯ
# ==============================================================================
BBOX = (49.540100, 58.556461, 49.714165, 58.647655)
FULL_CITY_BBOX = BBOX

TRAFFIC_VIEWPORT = {"width": 1920, "height": 1080}

MARGIN_X = 20
MARGIN_Y_TOP = 30
MARGIN_Y_BOTTOM = 30

# ==============================================================================
# ЦЕНТР КАРТЫ
# ==============================================================================
MAP_CENTER_LON = 49.6271
MAP_CENTER_LAT = 58.6021
MAP_ZOOM = 14

# ==============================================================================
# ️ СЕЗОНЫ И ДНИ НЕДЕЛИ
# ==============================================================================
SEASON_MONTHS = {
    'spring': [3, 4, 5],
    'summer': [6, 7, 8],
    'autumn': [9, 10, 11],
    'winter': [12, 1, 2]
}

WEEKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']

SCREENSHOT_MAX_AGE_DAYS = 90

# ==============================================================================
# 🕐 ВРЕМЕННЫЕ ПЕРИОДЫ
# ==============================================================================
TIME_PERIODS = {
    'morning': (8, 10),
    'day': (13, 15),
    'evening': (17, 19),
    'night': (22, 24)
}

PERIOD_CAPTURE_TIMES = {
    'morning': 7,
    'day': 14,
    'evening': 17,  # 17:30
    'night': 23
}

PERIOD_NAMES_RU = {
    'morning': 'Утро (08:00-10:00)',
    'day': 'День (13:00-15:00)',
    'evening': 'Вечер (17:00-19:00)',
    'night': 'Ночь (22:00-00:00)'
}

# ==============================================================================
# БАРЬЕРЫ
# ==============================================================================
BARRIER_TOLERANCE = 5
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
    'motorway': 70,
    'trunk': 50,
    'primary': 35,
    'secondary': 30,
    'tertiary': 25,
    'residential': 20,
    'service': 15,
    'living_street': 15,
    'unclassified': 25,
    'road': 25
}

ROAD_PENALTIES = {
    'motorway': 1.0, 'trunk': 1.0, 'primary': 1.1,
    'secondary': 1.2, 'tertiary': 1.3,
    'residential': 3.0, 'living_street': 3.0, 'service': 3.0,
    'unclassified': 1.5
}