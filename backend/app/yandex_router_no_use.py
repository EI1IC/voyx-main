import os
import time
import asyncio
from typing import Sequence, Tuple, Dict, Optional, List
import aiohttp
from dotenv import load_dotenv

load_dotenv()

# ✅ Правильный хост и эндпоинт
YANDEX_API_HOST = "https://api.routing.yandex.net/v2/route"
YANDEX_ROUTER_ENDPOINT = "/router-api/v2/route"
YANDEX_ROUTER_API_KEY = os.getenv("YANDEX_ROUTER_API_KEY")

DEFAULT_TIMEOUT = 10
DEFAULT_TRAFFIC = "enabled"
DEFAULT_RESULTS = 1
CACHE_TTL_SECONDS = 300
MAX_RETRIES = 3
RETRY_BACKOFF = 1.0
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


class YandexRouterError(RuntimeError):
    """Исключение для ошибок Яндекс.Маршрутизации"""
    pass


def _format_waypoints(waypoints: Sequence[Tuple[float, float]]) -> str:
    """
    Преобразует [(lon, lat), ...] в строку "lon,lat|lon,lat".
    Яндекс ожидает формат: долгота,широта.
    """
    return "|".join(f"{lon},{lat}" for lon, lat in waypoints)


def _extract_leg_summary(leg: dict) -> Tuple[float, float]:
    """Возвращает время (сек) и расстояние (м) для части маршрута."""
    summary = leg.get("summary", {})
    duration = summary.get("duration")
    distance = summary.get("length") or summary.get("distance")

    if duration is None:
        duration = sum(float(step.get("duration", 0) or 0) for step in leg.get("steps", []))
    if distance is None:
        distance = sum(float(step.get("length", 0) or 0) for step in leg.get("steps", []))

    return float(duration or 0), float(distance or 0)


def _extract_route_summary(route: dict) -> Tuple[float, float]:
    """Возвращает суммарное время и расстояние для маршрута."""
    if not route:
        return 0.0, 0.0

    legs = route.get("legs") or []
    total_duration = 0.0
    total_distance = 0.0
    for leg in legs:
        d, dist = _extract_leg_summary(leg)
        total_duration += d
        total_distance += dist
    return total_duration, total_distance


class YandexRouterClient:
    """Асинхронный клиент для Яндекс.Маршрутизации с кэшем и повторами"""
    
    def __init__(
        self,
        api_key: str = YANDEX_ROUTER_API_KEY,
        cache_ttl: int = CACHE_TTL_SECONDS,
        max_retries: int = MAX_RETRIES
    ):
        self.api_key = api_key
        self.cache_ttl = cache_ttl
        self.max_retries = max_retries
        self._cache: Dict[str, Tuple[dict, float]] = {}
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def __aenter__(self):
        self._session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, *args):
        if self._session:
            await self._session.close()
    
    def _make_cache_key(self, waypoints: Tuple[Tuple[float, float], ...], traffic: str) -> str:
        """Создаёт ключ кэша из координат и параметров"""
        wp_str = "|".join(f"{lon:.5f},{lat:.5f}" for lon, lat in waypoints)
        return f"{wp_str}|{traffic}"
    
    def _get_cached(self, key: str) -> Optional[dict]:
        """Получает данные из кэша, если они не устарели"""
        if key in self._cache:
            data, timestamp = self._cache[key]
            if time.time() - timestamp < self.cache_ttl:
                return data
            del self._cache[key]
        return None
    
    def _set_cached(self, key: str, data: dict):
        """Сохраняет данные в кэш с временной меткой"""
        self._cache[key] = (data, time.time())
    
    def _clear_expired(self):
        """Очищает устаревшие записи кэша"""
        now = time.time()
        expired = [k for k, (_, ts) in self._cache.items() if now - ts >= self.cache_ttl]
        for k in expired:
            del self._cache[k]
    
    async def _request_with_retry(
        self,
        url: str,
        params: dict,
        headers: dict
    ) -> dict:
        """Выполняет запрос с повторами при ошибках"""
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                async with self._session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=DEFAULT_TIMEOUT
                ) as resp:
                    if resp.status in RETRY_STATUS_CODES and attempt < self.max_retries - 1:
                        wait = RETRY_BACKOFF * (2 ** attempt)
                        await asyncio.sleep(wait)
                        continue
                    
                    resp.raise_for_status()
                    return await resp.json()
                    
            except aiohttp.ClientError as e:
                last_exception = e
                if attempt < self.max_retries - 1:
                    wait = RETRY_BACKOFF * (2 ** attempt)
                    await asyncio.sleep(wait)
                    continue
        
        raise YandexRouterError(f"Ошибка после {self.max_retries} попыток: {last_exception}")
    
    async def get_route_details(
        self,
        waypoints: Sequence[Tuple[float, float]],
        results: int = DEFAULT_RESULTS,
        traffic: str = DEFAULT_TRAFFIC,
    ) -> dict:
        """
        Запрашивает маршруты у Яндекс.Маршрутизации.
        
        Args:
            waypoints: [(lon, lat), ...] — точки маршрута
            results: количество альтернатив
            traffic: "enabled" или "disabled"
        
        Returns:
            dict с ключами: 'routes', 'best_route_index', 'best_route'
        """
        if not self.api_key:
            raise YandexRouterError("Не задан YANDEX_ROUTER_API_KEY")
        
        # Проверка кэша
        cache_key = self._make_cache_key(tuple(waypoints), traffic)
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        # Подготовка запроса
        url = f"{YANDEX_API_HOST}{YANDEX_ROUTER_ENDPOINT}"
        params = {
            "waypoints": _format_waypoints(waypoints),
            "lang": "ru_RU",
            "mode": "auto",
            "traffic": traffic,
            "results": results,
        }
        headers = {
            "X-Auth-Key": self.api_key,
            "Content-Type": "application/json"
        }
        
        # Выполнение запроса
        data = await self._request_with_retry(url, params, headers)
        
        # Парсинг ответа
        routes = data.get("routes")
        if routes is None:
            route = data.get("route")
            routes = [route] if route else []
        
        if not routes:
            raise YandexRouterError("Пустой ответ от API")
        
        # Обработка маршрутов
        processed = []
        for route in routes:
            duration, distance = _extract_route_summary(route)
            processed.append({
                "route": route,
                "duration_s": duration,
                "distance_m": distance,
            })
        
        # Выбор лучшего по времени
        best_idx = min(range(len(processed)), key=lambda i: processed[i]["duration_s"])
        result = {
            "routes": processed,
            "best_route_index": best_idx,
            "best_route": processed[best_idx],
            "waypoints": tuple(waypoints),
        }
        
        # Сохранение в кэш
        self._set_cached(cache_key, result)
        return result