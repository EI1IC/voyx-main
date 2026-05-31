# backend/debug_traffic.py
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from PIL import Image, ImageDraw
from app.routing import calculate_route
from app.traffic_screen import get_edge_factor, IMG_PATH
from app.config import FULL_CITY_BBOX

# ✅ Точные границы и размеры (должны совпадать с traffic_screen.py)
BBOX = FULL_CITY_BBOX  # west, south, east, north

print("🔍 Диагностика наложения маршрута на скриншот...")
start, end = "Ленина 111", "Ульяновская 30"
res = calculate_route(start, end, use_traffic=False)

if not IMG_PATH.exists():
    print("❌ traffic.png не найден!")
    exit()

img = Image.open(IMG_PATH).copy()
draw = ImageDraw.Draw(img)
w, h = img.size
route = res["route"]

def _project(lon, lat, img_w, img_h):
    x = int((lon - BBOX[0]) / (BBOX[2] - BBOX[0]) * img_w)
    y = int((1 - (lat - BBOX[1]) / (BBOX[3] - BBOX[1])) * img_h)
    return max(0, min(img_w-1, x)), max(0, min(img_h-1, y))

path_pixels = [_project(lon, lat, w, h) for lon, lat in route]
if len(path_pixels) > 1:
    draw.line(path_pixels, fill=(180, 180, 180), width=2)

step = max(1, len(route) // 10)
COLORS_RGB = {"green": (0,180,0), "yellow": (255,200,0), "red": (255,50,50), "darkred": (130,0,0), "gray": (100,100,100)}

print(f"\n{'#':<4} | {'Цвет':<8} | {'k':<4} | {'Пиксели':<15}")
for i in range(0, len(route)-1, step):
    u = route[i]
    v = route[i+1] if i+1 < len(route) else route[-1]
    k, color = get_edge_factor(u[0], u[1], v[0], v[1], samples=5)
    x, y = _project(u[0], u[1], w, h)
    draw.ellipse([(x-3, y-3), (x+3, y+3)], fill=COLORS_RGB.get(color, (255,255,255)), outline=(0,0,0), width=1)
    print(f"{i:<4} | {color:<8} | {k:.2f} | ({x}, {y})")

img.save("traffic_debug_full.png")
print("\n✅ Сохранено: traffic_debug_full.png")