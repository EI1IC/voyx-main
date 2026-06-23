# backend/app/traffic_screen.py
import asyncio, logging, functools, time, os, math, json, pickle
from pathlib import Path
from PIL import Image
from playwright.async_api import async_playwright
from playwright_stealth import Stealth
import datetime
import pytz

from .config import (
    FULL_CITY_BBOX, TRAFFIC_VIEWPORT as VIEWPORT,
    MARGIN_X, MARGIN_Y_TOP, MARGIN_Y_BOTTOM,
    MAP_CENTER_LON, MAP_CENTER_LAT, MAP_ZOOM,
    SEASON_MONTHS, WEEKDAYS, SCREENSHOT_MAX_AGE_DAYS,
    TIME_PERIODS, PERIOD_CAPTURE_TIMES, METADATA_FILENAME
)

logger = logging.getLogger(__name__)

DEBUG_TRAFFIC = os.getenv("DEBUG_TRAFFIC", "False").lower() == "true"

PALETTE = {"green": (0, 180, 0), "yellow": (255, 200, 0), "red": (255, 50, 50), "darkred": (150, 0, 0)}
FACTOR_MAP = {
    "green": 1.0,
    "yellow": 1.5,
    "red": 2.2,
    "darkred": 3.0,
    "gray": 1.0
}
TOLERANCE = 85

# Пути к файлам скриншотов
IMG_PATH_CURRENT = Path(__file__).parent.parent / "traffic_current.png"
SCREENSHOTS_DIR = Path(__file__).parent.parent / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)

# Глобальное состояние
_current_traffic_image_path = IMG_PATH_CURRENT
_cached_img = None
_img_size = (0, 0)
_last_capture_time = 0

_edge_factor_cache = {}
_pixel_cache = {}  # Кэш пикселей в памяти (загружается с диска)

# ==============================================================================
# 💾 КЭШИРОВАНИЕ ПИКСЕЛЕЙ НА ДИСКЕ
# ==============================================================================
def get_cache_path(image_path: Path) -> Path:
    """Возвращает путь к файлу кэша для изображения"""
    return image_path.with_suffix('.cache.pkl')

def load_pixel_cache(image_path: Path) -> dict:
    """Загружает кэш пикселей с диска"""
    cache_path = get_cache_path(image_path)
    if cache_path.exists():
        try:
            with open(cache_path, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            logger.warning(f"️ Ошибка загрузки кэша {cache_path.name}: {e}")
    return {}

def save_pixel_cache(image_path: Path, cache: dict):
    """Сохраняет кэш пикселей на диск"""
    cache_path = get_cache_path(image_path)
    try:
        with open(cache_path, 'wb') as f:
            pickle.dump(cache, f)
        logger.info(f"💾 Кэш сохранён на диск: {cache_path.name} ({len(cache)} пикселей)")
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения кэша: {e}")

def build_pixel_cache(image: Image.Image) -> dict:
    """Создаёт кэш пикселей для изображения (тяжелая операция)"""
    cache = {}
    width, height = image.size
    
    # Сканируем с шагом 5 для скорости
    for py in range(0, height, 5):
        for px in range(0, width, 5):
            r, g, b = image.getpixel((px, py))
            color = _classify_pixel(r, g, b)
            if color != "gray":
                # Сохраняем для области 5x5
                for dy in range(5):
                    for dx in range(5):
                        if py + dy < height and px + dx < width:
                            cache[(px + dx, py + dy)] = color
    return cache

# ==============================================================================
# 📋 METADATA
# ==============================================================================
def load_metadata() -> dict:
    if not METADATA_FILENAME.exists(): return {}
    try:
        with open(METADATA_FILENAME, 'r', encoding='utf-8') as f: return json.load(f)
    except: return {}

def save_metadata(metadata: dict):
    try:
        with open(METADATA_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"❌ Ошибка записи metadata.json: {e}")

def update_metadata(filename: str, url: str, season: str, weekday: str, period: str, year: int):
    metadata = load_metadata()
    metadata[filename] = {
        "created": datetime.datetime.now().isoformat(),
        "url": url, "season": season, "weekday": weekday, "period": period, "year": year,
        "file_size_mb": round(Path(SCREENSHOTS_DIR / filename).stat().st_size / (1024*1024), 2) if (SCREENSHOTS_DIR / filename).exists() else 0
    }
    save_metadata(metadata)

def get_metadata_summary() -> dict:
    metadata = load_metadata()
    seasons = {}
    for info in metadata.values(): seasons[info.get('season', 'unknown')] = seasons.get(info.get('season', 'unknown'), 0) + 1
    return {
        "total_files": len(metadata), "expected_files": 112, "by_season": seasons,
        "last_updated": max((v.get('created', '') for v in metadata.values()), default=None)
    }

# ==============================================================================
# ОСНОВНЫЕ ФУНКЦИИ
# ==============================================================================
def set_traffic_image_path(path: Path):
    global _current_traffic_image_path, _cached_img, _edge_factor_cache, _pixel_cache
    _current_traffic_image_path = path
    _cached_img = None
    _pixel_cache = {}
    _edge_factor_cache.clear()

def _invalidate_cache():
    global _cached_img, _img_size, _pixel_cache
    _cached_img = None
    _img_size = (0, 0)
    _pixel_cache = {}

def _get_traffic_image():
    global _cached_img, _img_size, _pixel_cache
    if _cached_img is None:
        if not _current_traffic_image_path.exists(): return None
        try:
            _cached_img = Image.open(_current_traffic_image_path).convert("RGB")
            _img_size = _cached_img.size
            
            # Пытаемся загрузить готовый кэш с диска
            _pixel_cache = load_pixel_cache(_current_traffic_image_path)
            
            # Если кэша нет — создаём его и сохраняем на диск
            if not _pixel_cache:
                logger.info(f"🔄 Предобработка скриншота: {_current_traffic_image_path.name}...")
                _pixel_cache = build_pixel_cache(_cached_img)
                save_pixel_cache(_current_traffic_image_path, _pixel_cache)
                logger.info(f"✅ Кэш создан: {len(_pixel_cache)} цветных пикселей")
        except Exception as e:
            logger.error(f"Ошибка загрузки изображения: {e}")
            return None
    return _cached_img

def _get_pixel_color_cached(px, py):
    """Мгновенное получение цвета из кэша"""
    return _pixel_cache.get((px, py), "gray")

def _geo_to_px(lon, lat):
    W, H = VIEWPORT["width"], VIEWPORT["height"]
    def lon_to_px(lng, z): return ((lng + 180) / 360) * (2**z * 256)
    def lat_to_px(lt, z):
        lat_rad = math.radians(lt)
        return (1 - math.log(math.tan(lat_rad) + 1/math.cos(lat_rad)) / math.pi) / 2 * (2**z * 256)
    
    cx, cy = lon_to_px(MAP_CENTER_LON, MAP_ZOOM), lat_to_px(MAP_CENTER_LAT, MAP_ZOOM)
    tx, ty = lon_to_px(lon, MAP_ZOOM), lat_to_px(lat, MAP_ZOOM)
    
    PAD_X = -4
    PAD_Y = 12
    
    x = (tx - cx) + (W / 2) + PAD_X
    y = (ty - cy) + (H / 2) + PAD_Y
    
    return int(max(0, min(W-1, x))), int(max(0, min(H-1, y)))

@functools.lru_cache(maxsize=4096)
def _classify_pixel(r, g, b):
    if r > 200 and g > 140 and b < 140: return "yellow"
    if r > 180 and g < 120 and b < 120: return "red"
    if g > 140 and g > r + 20 and g > b + 20: return "green"
    return "gray"

def get_edge_factor(u_lon, u_lat, v_lon, v_lat, samples=20):
    """
    Анализирует участок дороги. 
    ИСПОЛЬЗУЕТ ТОЛЬКО КЭШ (без обращения к PIL Image).
    samples=20, radius=4, шаг=2 (как в оригинале).
    """
    img = _get_traffic_image()
    if img is None: return 1.0, "gray"
    
    traffic_counts = {"yellow": 0, "red": 0, "darkred": 0, "green": 0}
    total_color_pixels = 0
    
    SEARCH_RADIUS = 4 

    for i in range(samples):
        t = i / (samples - 1) if samples > 1 else 0
        lon = u_lon + t * (v_lon - u_lon)
        lat = u_lat + t * (v_lat - u_lat)
        px, py = _geo_to_px(lon, lat)
        
        # Сканируем область вокруг точки (шаг 2 пикселя)
        for dx in range(-SEARCH_RADIUS, SEARCH_RADIUS + 1, 2):
            for dy in range(-SEARCH_RADIUS, SEARCH_RADIUS + 1, 2):
                # ✅ Берем цвет из кэша, а не из файла!
                color = _get_pixel_color_cached(px + dx, py + dy)
                
                if color != "gray":
                    traffic_counts[color] = traffic_counts.get(color, 0) + 1
                    total_color_pixels += 1

    if total_color_pixels < 2:
        return 1.0, "gray"

    dominant = max(traffic_counts, key=traffic_counts.get)
    
    if dominant == "green" and traffic_counts["green"] > total_color_pixels * 0.3:
        return 1.0, "green"
    
    if dominant in ["yellow", "red", "darkred"]:
        return FACTOR_MAP.get(dominant, 1.3), dominant
    
    return 1.0, "gray"

# ==============================================================================
# 🗓️ СЕЗОНЫ, ДНИ НЕДЕЛИ, ПЕРИОДЫ
# ==============================================================================
def get_season(month: int) -> str:
    for season, months in SEASON_MONTHS.items():
        if month in months: return season
    return 'spring'

def get_weekday_name(date: datetime.datetime) -> str:
    return WEEKDAYS[date.weekday()]

def get_time_period(hour: int) -> str:
    for period, (start, end) in TIME_PERIODS.items():
        if start <= end:
            if start <= hour < end: return period
        else:
            if hour >= start or hour < end: return period
    return 'day'

def get_screenshot_filename(weekday: str, hour: int, minute: int, season: str) -> Path:
    """
    Новый формат имени файла: день_недели_час_минута_сезон.png
    Пример: monday_08_00_spring.png
    """
    return SCREENSHOTS_DIR / f"{weekday}_{hour:02d}_{minute:02d}_{season}.png"

def get_screenshot_for_datetime(dt: datetime.datetime) -> Path:
    """
    Определяет путь к скриншоту на основе даты и времени.
    Для часовой интерполяции округляет до ЦЕЛОГО ЧАСА (minute=0).
    """
    weekday = get_weekday_name(dt)
    season = get_season(dt.month)
    # ✅ Округляем до целого часа (7:32 → 7:00)
    dt_rounded = dt.replace(minute=0, second=0, microsecond=0)
    return get_screenshot_filename(weekday, dt_rounded.hour, dt_rounded.minute, season)

def is_screenshot_fresh(path: Path) -> bool:
    """
    Проверяем не сам .png (его может не быть), 
    а наличие готового кэша рёбер .edge_cache.pkl
    """
    cache_path = path.with_suffix('.edge_cache.pkl')
    if not cache_path.exists(): 
        return False
    file_age_days = (time.time() - cache_path.stat().st_mtime) / 86400
    return file_age_days < SCREENSHOT_MAX_AGE_DAYS

def round_to_15_minutes(dt: datetime.datetime) -> datetime.datetime:
    minute = dt.minute
    if minute < 8: rounded = 0
    elif minute < 23: rounded = 15
    elif minute < 38: rounded = 30
    elif minute < 53: rounded = 45
    else:
        rounded = 0
        dt = dt + datetime.timedelta(hours=1)
    return dt.replace(minute=rounded, second=0, microsecond=0)

def convert_to_yandex_timestamp(dt: datetime.datetime) -> int:
    moscow_tz = pytz.timezone('Europe/Moscow')
    if dt.tzinfo is None: dt = moscow_tz.localize(dt)
    dt_utc = dt.astimezone(pytz.UTC)
    return int(dt_utc.timestamp() * 1000)

def build_historical_traffic_url(date_time: datetime.datetime, center_lon: float = MAP_CENTER_LON, center_lat: float = MAP_CENTER_LAT, zoom: int = MAP_ZOOM) -> str:
    dt_rounded = round_to_15_minutes(date_time)
    timestamp_ms = convert_to_yandex_timestamp(dt_rounded)
    hour = dt_rounded.hour
    minute = dt_rounded.minute
    
    base_url = "https://yandex.ru/maps/46/kirov/probki/?l=sat%2Ctrf&"
    params = {
        'll': f"{center_lon}%2C{center_lat}",
        'trfm': 'arc',
        'trfst': f"date%3A{timestamp_ms}~time%3A{hour}%2C{minute}",
        'z': "14"
    }
    url = base_url + '&'.join([f"{k}={v}" for k, v in params.items()])
    logger.info(f"🕰️ Исторические пробки: {dt_rounded} (ts={timestamp_ms}) → time={hour},{minute}")
    return url

async def capture_screenshot_for_datetime(date_time: datetime.datetime, output_path: Path = None, center_lon: float = MAP_CENTER_LON, center_lat: float = MAP_CENTER_LAT):
    global _last_capture_time
    if output_path is None: output_path = get_screenshot_for_datetime(date_time)
    if is_screenshot_fresh(output_path):
        logger.info(f"📦 Используем кэшированный скриншот: {output_path.name}")
        return output_path
    
    _invalidate_cache()
    url = build_historical_traffic_url(date_time, center_lon, center_lat)
    
    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(headless=True, args=["--single-process","--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--disable-gpu", "--disable-web-security", "--disable-features=IsolateOrigins,site-per-process"])
        context = await browser.new_context(viewport=VIEWPORT, user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", locale="ru-RU", timezone_id="Europe/Moscow")
        page = await context.new_page()
        
        try:
            logger.info(f"📸 Захват скриншота для {date_time} → {output_path.name}")
            await page.goto(url, wait_until="networkidle", timeout=25000)
            try: await page.wait_for_load_state("networkidle", timeout=8000)
            except: pass
            try: await page.click('span.sidebar-toggle-button__icon', timeout=2000)
            except: pass
            
            await page.evaluate("""() => { document.querySelectorAll('.header__top, .cookie-banner, .copyrights-pane, .logo, .sidebar-toggle-button').forEach(el => el.style.display = 'none'); }""")
            await page.wait_for_timeout(1500)
            
            if await page.query_selector('text="Подтвердите, что вы не робот"'):
                logger.warning("⚠️ CAPTCHA при захвате")
                return None
            
            await page.screenshot(path=str(output_path), full_page=False)
            _last_capture_time = time.time()
            
            season = get_season(date_time.month)
            weekday = get_weekday_name(date_time)
            period = get_time_period(date_time.hour)
            update_metadata(filename=output_path.name, url=url, season=season, weekday=weekday, period=period, year=date_time.year)
            
            logger.info(f"✅ Скриншот сохранён: {output_path.name}")
            return output_path
        except Exception as e:
            logger.error(f"❌ Ошибка захвата: {e}")
            return None
        finally:
            await browser.close()

import asyncio

async def capture_current_screenshot(output_path: Path = None):
    """Создаёт скриншот с оптимизациями для Render (в фоне, с таймаутом)"""
    global _last_capture_time
    
    if output_path is None:
        output_path = IMG_PATH_CURRENT
    
    # ✅ Проверка кэша
    if output_path.exists():
        file_age = time.time() - output_path.stat().st_mtime
        if file_age < 900:  # 15 минут
            logger.info(f"📦 Кэшированный скриншот (возраст {file_age/60:.1f} мин)")
            return output_path
    
    _invalidate_cache()
    
    async def _capture_impl():
        from playwright.async_api import async_playwright
        
        async with async_playwright() as p:
            # ✅ Оптимизированные флаги для Render
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--use-gl=swiftshader",
                    "--enable-webgl",
                    "--ignore-gpu-blacklist",
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-default-apps",
                    "--disable-sync",
                    "--disable-translate",
                    "--no-first-run",
                    "--mute-audio",
                ]
            )
            
            try:
                page = await browser.new_page(
                    viewport={"width": 1280, "height": 720},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    locale="ru-RU",
                    timezone_id="Europe/Moscow",
                )
                
                url = f"https://yandex.ru/maps/46/kirov/probki/?l=sat%2Ctrf&ll={MAP_CENTER_LON}%2C{MAP_CENTER_LAT}&z=14"
                logger.info(f"🌐 Открываю: {url[:80]}...")
                
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(5000)
                
                # Скрываем лишние элементы
                await page.evaluate("""() => {
                    document.querySelectorAll('.header__top, .cookie-banner, .copyrights-pane, .logo')
                        .forEach(el => el.style.display = 'none');
                }""")
                
                await page.screenshot(path=str(output_path), full_page=False)
                _last_capture_time = time.time()
                logger.info(f"📸 Скриншот сохранён: {output_path}")
                return output_path
                
            finally:
                await browser.close()
    
    try:
        # ✅ Таймаут 30 секунд
        return await asyncio.wait_for(_capture_impl(), timeout=30.0)
    except asyncio.TimeoutError:
        logger.warning("⚠️ Скриншот: таймаут 30 секунд")
        return None
    except Exception as e:
        logger.error(f"❌ Ошибка скриншота: {e}", exc_info=False)
        return None
        
async def update_all_seasonal_screenshots():
    logger.info("🔄 Начинаю обновление всех сезонных скриншотов...")
    now = datetime.datetime.now()
    current_year = now.year
    
    for season in SEASON_MONTHS.keys():
        first_month = SEASON_MONTHS[season][0]
        for weekday_idx, weekday_name in enumerate(WEEKDAYS):
            for day in range(1, 8):
                try:
                    dt_base = datetime.datetime(current_year, first_month, day)
                    if dt_base.weekday() == weekday_idx:
                        for period, capture_hour in PERIOD_CAPTURE_TIMES.items():
                            dt = dt_base.replace(hour=capture_hour, minute=30)
                            output_path = get_screenshot_filename(current_year, season, weekday_name, period)
                            
                            if not is_screenshot_fresh(output_path):
                                logger.info(f"📸 Обновляю: {output_path.name}")
                                await capture_screenshot_for_datetime(dt, output_path)
                                await asyncio.sleep(2)
                            else:
                                logger.info(f"📦 Актуален: {output_path.name}")
                        break
                except ValueError:
                    continue
    logger.info("✅ Все сезонные скриншоты обновлены (112 файлов)")

async def prebuild_all_caches():
    """Создаёт кэши пикселей для всех существующих скриншотов"""
    logger.info("💾 Начинаю предобработку кэшей для всех скриншотов...")
    screenshot_files = list(SCREENSHOTS_DIR.glob("traffic_*.png"))
    total = len(screenshot_files)
    logger.info(f"📊 Найдено {total} скриншотов для кэширования")
    
    for i, img_path in enumerate(screenshot_files, 1):
        cache_path = get_cache_path(img_path)
        if cache_path.exists():
            logger.info(f"[{i}/{total}] 📦 Кэш уже существует: {img_path.name}")
            continue
        
        try:
            logger.info(f"[{i}/{total}] 🔄 Создаю кэш: {img_path.name}")
            img = Image.open(img_path).convert("RGB")
            cache = build_pixel_cache(img)
            save_pixel_cache(img_path, cache)
            logger.info(f"[{i}/{total}] ✅ Кэш создан: {len(cache)} пикселей")
        except Exception as e:
            logger.error(f"[{i}/{total}] ❌ Ошибка: {e}")
    logger.info(f"✅ Кэширование завершено: {total} файлов")

async def precompute_edge_weights(G, blocked_edges_set, use_traffic=True):
    """
    Предварительно вычисляет коэффициенты пробок для ВСЕХ рёбер графа.
    Сохраняет результат в файл .edge_cache.pkl рядом со скриншотом.
    """
    global _edge_factor_cache
    
    cache_path = _current_traffic_image_path.with_suffix('.edge_cache.pkl')
    
    # Если кэш уже есть — загружаем
    if cache_path.exists():
        try:
            with open(cache_path, 'rb') as f:
                edge_cache = pickle.load(f)
            logger.info(f" Загружен кэш рёбер: {len(edge_cache)} рёбер")
            _edge_factor_cache.update(edge_cache)
            return edge_cache
        except Exception as e:
            logger.warning(f"⚠️ Ошибка загрузки кэша рёбер: {e}")
    
    # Вычисляем веса для всех рёбер
    logger.info(f"🔄 Предобработка весов рёбер для {_current_traffic_image_path.name}...")
    edge_cache = {}
    total_edges = len(G.edges)
    
    for i, (u, v, key, data) in enumerate(G.edges(keys=True, data=True), 1):
        if i % 5000 == 0:
            logger.info(f"   [{i}/{total_edges}] Обработано рёбер...")
        
        # Пропускаем заблокированные рёбра
        if (u, v, key) in blocked_edges_set:
            continue
        
        if use_traffic:
            traffic_k, _ = get_edge_factor(
                G.nodes[u]['x'], G.nodes[u]['y'],
                G.nodes[v]['x'], G.nodes[v]['y']
            )
        else:
            traffic_k = 1.0
        
        edge_cache[(u, v)] = traffic_k
    
    # Сохраняем кэш на диск
    try:
        with open(cache_path, 'wb') as f:
            pickle.dump(edge_cache, f)
        logger.info(f"✅ Кэш рёбер сохранён: {cache_path.name} ({len(edge_cache)} рёбер)")
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения кэша рёбер: {e}")
    
    _edge_factor_cache.update(edge_cache)
    return edge_cache

def check_seasonal_integrity() -> dict:
    now = datetime.datetime.now()
    current_year = now.year
    missing, outdated, fresh = [], [], []
    
    for season in SEASON_MONTHS.keys():
        for weekday in WEEKDAYS:
            for period in TIME_PERIODS.keys():
                path = get_screenshot_filename(current_year, season, weekday, period)
                if not path.exists(): missing.append(path.name)
                elif not is_screenshot_fresh(path): outdated.append(path.name)
                else: fresh.append(path.name)
    
    return {
        "total_expected": 112, "total_found": len(fresh) + len(outdated), "fresh": len(fresh),
        "missing": missing, "outdated": outdated, "needs_update": len(missing) + len(outdated)
    }

def debug_route_traffic(route_coords, use_traffic=True):
    if not DEBUG_TRAFFIC: return
    try:
        img = _get_traffic_image()
        log_path = Path(__file__).parent.parent / "debug_route_traffic.log"
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(f"{'='*110}\n")
            f.write(f"🔍 DEBUG ROUTE TRAFFIC (ПОЛНЫЙ МАРШРУТ) | Всего сегментов: {len(route_coords)-1}\n")
            f.write(f"📁 Файл скриншота: {_current_traffic_image_path}\n")
            f.write(f"{'№':<5} {'Lon':>10} {'Lat':>10} {'Px':>9} {'RGB':>15} {'Цвет':<10} {'k':>4} {'Влияние':>12}\n")
            f.write(f"{'-'*110}\n")
            if img is None:
                f.write("⚠️ ОШИБКА: Файл скриншота пробок не найден.\n")
                return
            k_list = []
            for i in range(len(route_coords) - 1):
                lon1, lat1 = route_coords[i]
                lon2, lat2 = route_coords[i+1]
                mid_lon, mid_lat = (lon1 + lon2) / 2, (lat1 + lat2) / 2
                px, py = _geo_to_px(mid_lon, mid_lat)
                try: r, g, b = img.getpixel((px, py))
                except: r, g, b = 128, 128, 128
                color = _classify_pixel(r, g, b)
                k = FACTOR_MAP[color] if use_traffic else 1.0
                k_list.append(k)
                impact = f"x{100*(k-1):.0f}% замедл." if k > 1.0 else "свободно"
                f.write(f"{i+1:<5} {mid_lon:>10.4f} {mid_lat:>10.4f}  ({px:>3},{py:>3})   ({r:>3},{g:>3},{b:>3})   {color:<10} {k:>4.1f} {impact:>12}\n")
            if k_list:
                avg_k = sum(k_list) / len(k_list)
                f.write(f"{'-'*110}\n")
                f.write(f"📊 Средний коэффициент k по ВСЕМ {len(k_list)} сегментам: {avg_k:.2f}\n")
                f.write(f"{'='*110}\n")
        print(f"💾 Отладочный отчёт: backend/debug_route_traffic.log | Средний k: {sum(k_list)/len(k_list):.2f}")
    except Exception as e:
        print(f"⚠️ Ошибка записи отладочного файла: {e}")