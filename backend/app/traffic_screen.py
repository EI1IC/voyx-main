# backend/app/traffic_screen.py
import asyncio, logging, functools
from pathlib import Path
from PIL import Image
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from .config import (
    FULL_CITY_BBOX, TRAFFIC_VIEWPORT as VIEWPORT,
    MARGIN_X, MARGIN_Y_TOP, MARGIN_Y_BOTTOM,
    OFFSET_X, OFFSET_Y
)

logger = logging.getLogger(__name__)
IMG_PATH = Path(__file__).parent.parent / "traffic.png"

PALETTE = {"green": (0, 180, 0), "yellow": (255, 200, 0), "red": (255, 50, 50), "darkred": (150, 0, 0)}
FACTOR_MAP = {"green": 1.0, "yellow": 1.3, "red": 1.8, "darkred": 2.5, "gray": 1.0}
TOLERANCE = 85

_cached_img = None
_img_size = (0, 0)

def _invalidate_cache():
    global _cached_img, _img_size
    _cached_img = None
    _img_size = (0, 0)

def _get_traffic_image():
    global _cached_img, _img_size
    if _cached_img is None:
        if not IMG_PATH.exists():
            return None
        try:
            _cached_img = Image.open(IMG_PATH).convert("RGB")
            _img_size = _cached_img.size
        except:
            return None
    return _cached_img

def _geo_to_px(lon, lat):
    """Проекция координат → пиксели с учётом калибровочных оффсетов"""
    w, h = VIEWPORT["width"], VIEWPORT["height"]
    west, south, east, north = FULL_CITY_BBOX
    
    map_w = w - 2 * MARGIN_X
    map_h = h - MARGIN_Y_TOP - MARGIN_Y_BOTTOM
    
    # Линейная проекция
    x = int((lon - west) / (east - west) * map_w) + MARGIN_X
    y = int((1 - (lat - south) / (north - south)) * map_h) + MARGIN_Y_TOP
    
    # ✅ Применяем калибровочные оффсеты
    x += OFFSET_X
    y += OFFSET_Y
    
    return max(0, min(w-1, x)), max(0, min(h-1, y))

@functools.lru_cache(maxsize=2048)
def _classify_pixel(r, g, b):
    if r < 70 and g < 70 and b < 70 and (r+g+b) > 30:
        return "green"
    for name, (tr, tg, tb) in PALETTE.items():
        if abs(r-tr)<TOLERANCE and abs(g-tg)<TOLERANCE and abs(b-tb)<TOLERANCE:
            return name
    return "gray"

def get_edge_factor(u_lon, u_lat, v_lon, v_lat, samples=12):
    """Читает цвет дороги со скриншота и возвращает коэффициент задержки"""
    img = _get_traffic_image()
    if img is None:
        return 1.0, "gray"
    
    colors = []
    for i in range(samples):
        t = i / (samples-1) if samples > 1 else 0
        lon = u_lon + t*(v_lon-u_lon)
        lat = u_lat + t*(v_lat-u_lat)
        px, py = _geo_to_px(lon, lat)
        r, g, b = img.getpixel((px, py))
        colors.append(_classify_pixel(r, g, b))
        
    dominant = max(set(colors), key=colors.count)
    return FACTOR_MAP[dominant], dominant

async def capture_screenshot():
    """Делает скриншот Яндекс.Карт (схема + пробки)"""
    _invalidate_cache()
    async with Stealth().use_async(async_playwright()) as p:
        browser = await p.chromium.launch(
            headless=True, 
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            viewport=VIEWPORT,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="ru-RU", 
            timezone_id="Europe/Moscow"
        )
        page = await context.new_page()
        try:
            # ✅ z=12.5 покрывает весь BBOX без эмуляции клавиш
            url = "https://yandex.ru/maps/46/kirov/probki/?ll=49.6271%2C58.6021&z=12.5&l=map%2Ctraffic"
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(4000)

            # Закрываем сайдбар
            try:
                await page.click('span.sidebar-toggle-button__icon', timeout=2000)
                await page.wait_for_timeout(500)
            except:
                pass

            # Скрываем интерфейс
            await page.evaluate("""() => {
                document.querySelectorAll('.header__top, .cookie-banner, .copyrights-pane, .logo').forEach(el => el.style.display = 'none');
            }""")
            await page.wait_for_timeout(500)

            # CAPTCHA check
            if await page.query_selector('text="Подтвердите, что вы не робот"'):
                logger.warning("⚠️ CAPTCHA. Использую старый скриншот.")
                await browser.close()
                return

            await page.screenshot(path=str(IMG_PATH), full_page=False)
            logger.info(f"📸 Скриншот сохранён: {IMG_PATH}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
        finally:
            await browser.close()