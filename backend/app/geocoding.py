from functools import lru_cache
import osmnx as ox

from .config import BBOX

@lru_cache(maxsize=1000)
def geocode_address(address_query: str, city: str = "Kirov, Russia") -> tuple[float, float]:
    """
    Преобразует адрес в координаты с кэшированием.
    
    Args:
        address_query: Адресная строка
        city: Город по умолчанию
        
    Returns:
        (lat, lon) - широта и долгота
        
    Raises:
        ValueError: Если адрес не найден или вне границ города
    """
    if "Киров" in address_query or "Kirov" in address_query:
        full_query = address_query
    else:
        full_query = f"{address_query}, {city}"
    
    try:
        location = ox.geocode(full_query)
        lat, lon = float(location[0]), float(location[1])
        
        # Проверка: попадает ли точка в границы города
        west, south, east, north = BBOX
        if not (south <= lat <= north and west <= lon <= east):
            raise ValueError(f"Координаты вне диапазона Кирова: {lat}, {lon}")
        
        return lon, lat
    except Exception as e:
        raise ValueError(f"Не удалось найти адрес '{address_query}': {str(e)}")