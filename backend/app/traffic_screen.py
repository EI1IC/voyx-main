# backend/app/traffic_screen.py
import asyncio, logging, functools, time, os
from pathlib import Path
from PIL import Image
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from .config import (
    FULL_CITY_BBOX, TRAFFIC_VIEWPORT as VIEWPORT,
    MARGIN_X, MARGIN_Y_TOP, MARGIN_Y_BOTTOM,
)

logger = logging.getLogger(__name__)
IMG_PATH = Path(__file__).parent.parent / "traffic.png"

# 🔍 ФЛАГ ОТЛАДКИ: поставь True, чтобы видеть проекцию и цвета в логах
DEBUG_TRAFFIC = os.getenv("DEBUG_TRAFFIC", "False").lower() == "true"

PALETTE = {"green": (0, 180, 0), "yellow": (255, 200, 0), "red": (255, 50, 50), "darkred": (150, 0, 0)}
FACTOR_MAP = {"green": 1.0, "yellow": 1.3, "red": 1.8, "darkred": 2.5, "gray": 1.0}
TOLERANCE = 85

# ✅ Кэш времени последнего захвата (в памяти, не зависит от файла)
_last_capture_time = 0
_cached_img = None
_img_size = (0, 0)

def _invalidate_cache():
    global _cached_img, _img_size, _last_capture_time
    _cached_img = None
    _img_size = (0, 0)
    # НЕ сбрасываем _last_capture_time, чтобы проверка возраста работала

def _get_traffic_image():
    global _cached_img, _img_size
    if _cached_img is None:
        if not IMG_PATH.exists():
            logger.warning(f"⚠️ Скриншот не найден: {IMG_PATH}")
            return None
        try:
            _cached_img = Image.open(IMG_PATH).convert("RGB")
            _img_size = _cached_img.size
            logger.info(f"🖼️ Скриншот загружен: {_img_size[0]}x{_img_size[1]} px")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки скриншота: {e}")
            return None
    return _cached_img

def _geo_to_px(lon, lat):
    """Точная проекция GPS → пиксели с учётом искажения Меркатора (z=14)"""
    import math
    
    # Параметры из URL скриншота: ?ll=49.6271,58.6021&z=14
    CENTER_LON = 49.6271
    CENTER_LAT = 58.6021
    ZOOM = 14
    W, H = VIEWPORT["width"], VIEWPORT["height"]
    
    # Стандартная формула тайлов OSM/Яндекс
    def lon_to_px(lng, z): return ((lng + 180) / 360) * (2**z * 256)
    def lat_to_px(lt, z):
        lat_rad = math.radians(lt)
        return (1 - math.log(math.tan(lat_rad) + 1/math.cos(lat_rad)) / math.pi) / 2 * (2**z * 256)

    # Глобальные пиксельные координаты
    cx, cy = lon_to_px(CENTER_LON, ZOOM), lat_to_px(CENTER_LAT, ZOOM)
    tx, ty = lon_to_px(lon, ZOOM), lat_to_px(lat, ZOOM)
    
    # Смещение относительно центра экрана + компенсация UI-рамок Яндекса
    # 🔧 Эти 2 числа подбираются один раз под твой конкретный скриншот:
    PAD_X = -2   # Отступ левого меню/логотипа (было ~90)
    PAD_Y = 7   # Отступ верхней панели (было ~40)
    
    x = (tx - cx) + (W / 2) + PAD_X
    y = (ty - cy) + (H / 2) + PAD_Y
    
    return int(max(0, min(W-1, x))), int(max(0, min(H-1, y)))

@functools.lru_cache(maxsize=2048)
def _classify_pixel(r, g, b):
    """Устойчивое распознавание цветов Яндекс.Пробок (учитывает сглаживание)"""
    # 🟡 Жёлтый / Оранжевый
    if r > 180 and 100 < g < 220 and b < 100:
        return "yellow"
    # 🔴 Красный / Тёмно-красный
    if r > 180 and g < 120 and b < 120:
        return "red" if r > 220 else "darkred"
    # 🟢 Зелёный (яркий, контрастный к серому фону карты)
    if g > 100 and g > r + 30 and g > b + 30:
        return "green"
    # ⚪ Всё остальное = фон карты
    return "gray"

def get_edge_factor(u_lon, u_lat, v_lon, v_lat, samples=20):
    """Читает цвет дороги со скриншота. Оптимизировано для линий 2px."""
    img = _get_traffic_image()
    if img is None:
        return 1.0, "gray"

    traffic_colors = []
    
    for i in range(samples):
        t = i / (samples - 1) if samples > 1 else 0
        lon = u_lon + t * (v_lon - u_lon)
        lat = u_lat + t * (v_lat - u_lat)
        px, py = _geo_to_px(lon, lat)

        # 🔍 Проверка "крестом" ±2 пикселя: гарантированно зацепим линию шириной 2px
        found_traffic = False
        for dx, dy in [(0,0), (1,0), (-1,0), (0,1), (0,-1), (2,0), (-2,0)]:
            try:
                r, g, b = img.getpixel((px + dx, py + dy))
                c = _classify_pixel(r, g, b)
                if c != "gray":
                    traffic_colors.append(c)
                    found_traffic = True
                    break  # Достаточно одного пикселя пробки в этой точке
            except:
                continue

    # Если пробка не обнаружена ни в одной точке
    if not traffic_colors:
        return 1.0, "gray"

    # ✅ Если пробка занимает >25% точек отрезка, применяем коэффициент
    if len(traffic_colors) / samples > 0.25:
        dominant = max(set(traffic_colors), key=traffic_colors.count)
        return FACTOR_MAP[dominant], dominant
        
    return 1.0, "gray"

def _should_refresh_traffic(min_age_seconds=900):
    """Проверяет, нужно ли обновлять скриншот (по времени в памяти)"""
    global _last_capture_time
    now = time.time()
    if _last_capture_time == 0:
        return True  # Первый запуск
    return (now - _last_capture_time) > min_age_seconds

async def capture_screenshot():
    """Делает скриншот Яндекс.Карт (схема + пробки)"""
    global _last_capture_time
    _invalidate_cache()  # Сбрасываем кэш изображения, но НЕ время
    
    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(
            headless=True, 
            args=[
                "--no-sandbox", 
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",  # Важно для Docker/Codespaces
                "--disable-gpu",
            ]
        )
        context = await browser.new_context(
            viewport=VIEWPORT,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="ru-RU", 
            timezone_id="Europe/Moscow"
        )
        page = await context.new_page()
        try:
            url = "https://yandex.ru/maps/46/kirov/probki/?ll=49.6271%2C58.6021&z=14&l=map%2Ctraffic"
            logger.info(f"🌐 Открываю: {url[:80]}...")
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(4000)

            # Закрываем сайдбар
            try:
                await page.click('span.sidebar-toggle-button__icon', timeout=2000)
                await page.wait_for_timeout(500)
            except Exception as e:
                logger.debug(f"⚠️ Не удалось закрыть сайдбар: {e}")

            # Скрываем интерфейс
            await page.evaluate("""() => {
                document.querySelectorAll('.header__top, .cookie-banner, .copyrights-pane, .logo').forEach(el => el.style.display = 'none');
            }""")
            await page.wait_for_timeout(500)

            # CAPTCHA check
            if await page.query_selector('text="Подтвердите, что вы не робот"'):
                logger.warning("⚠️ CAPTCHA обнаружена. Пропускаю захват.")
                return

            await page.screenshot(path=str(IMG_PATH), full_page=False)
            _last_capture_time = time.time()  # ✅ Запоминаем время УСПЕШНОГО захвата
            logger.info(f"📸 Скриншот сохранён: {IMG_PATH} ({_img_size[0]}x{_img_size[1]})")
            
        except Exception as e:
            logger.error(f"❌ Ошибка захвата: {e}", exc_info=True)
        finally:
            await browser.close()

# ==============================================================================
# DEBUG: Быстрая проверка проекции (запусти один раз)
# ==============================================================================
def debug_projection():
    """Тестирует проекцию на известных координатах Кирова"""
    if not IMG_PATH.exists():
        print(f"❌ Скриншот не найден: {IMG_PATH}")
        return
    
    test_points = [
        (49.6271, 58.6021, "Центр Кирова"),  # Примерные координаты центра
        (49.6807, 58.5907, "Ленина 111"),
        (49.5864, 58.5835, "Ульяновская 30"),
    ]
    
    img = Image.open(IMG_PATH).convert("RGB")
    print(f"\n🔍 DEBUG PROJECTION — скриншот: {img.size}")
    print(f"   BBOX: {FULL_CITY_BBOX}")
    print(f"   VIEWPORT: {VIEWPORT['width']}x{VIEWPORT['height']}")
    
    for lon, lat, label in test_points:
        px, py = _geo_to_px(lon, lat)
        r, g, b = img.getpixel((px, py))
        color = _classify_pixel(r, g, b)
        k = FACTOR_MAP[color]
        print(f"📍 {label}: ({lat:.4f}, {lon:.4f}) → px({px},{py}) → RGB({r},{g},{b}) → {color} (k={k})")

# ==============================================================================
# 🔍 ПРОСТАЯ ОТЛАДКА (запусти один раз)
# ==============================================================================
def debug_route_traffic(route_coords, use_traffic=True):
    """📊 Выводит влияние пробок на КАЖДЫЙ сегмент маршрута"""
    try:
        img = _get_traffic_image()
        if img is None:
            print("⚠️ DEBUG: traffic.png не найден, пропускаю анализ")
            return
        if not route_coords or len(route_coords) < 2:
            print("⚠️ DEBUG: Маршрут пустой")
            return

        print(f"\n{'='*90}")
        print(f"🔍 DEBUG ROUTE TRAFFIC | Сегментов: {len(route_coords)-1}")
        print(f"{'№':<4} {'lon':>10} {'lat':>10} {'px':>8} {'RGB':>12} {'Цвет':<10} {'k':>4} {'Влияние':>10}")
        print(f"{'-'*90}")

        k_list = []
        # Логируем первые 20 сегментов, чтобы не спамить консоль
        for i in range(min(len(route_coords) - 1, 20)):
            lon1, lat1 = route_coords[i]
            lon2, lat2 = route_coords[i+1]
            
            mid_lon = (lon1 + lon2) / 2
            mid_lat = (lat1 + lat2) / 2
            px, py = _geo_to_px(mid_lon, mid_lat)
            
            try:
                r, g, b = img.getpixel((px, py))
            except (IndexError, TypeError):
                r, g, b = 128, 128, 128
                
            color = _classify_pixel(r, g, b)
            k = FACTOR_MAP[color] if use_traffic else 1.0
            k_list.append(k)
            
            impact = f"x{100*(k-1):.0f}% замедл." if k > 1.0 else "свободно"
            print(f"{i+1:<4} {mid_lon:>10.4f} {mid_lat:>10.4f} ({px:>3},{py:>3}) ({r:>3},{g:>3},{b:>3}) {color:<10} {k:>4.1f} {impact:>10}")

        if k_list:
            avg_k = sum(k_list) / len(k_list)
            print(f"{'='*90}")
            print(f"📊 Средний k по первым {len(k_list)} сегментам: {avg_k:.2f}")
            if avg_k > 1.15:
                print(f"✅ Пробки на маршруте ЕСТЬ! Если время не меняется → ошибка в формуле расчёта")
            else:
                print(f"⚠️ Средний k ≈ 1.0: либо пробок нет, либо оффсеты проекции неверны")
            print(f"{'='*90}\n")
            
    except Exception as e:
        print(f"⚠️ DEBUG ROUTE TRAFFIC упал (не критично): {e}")