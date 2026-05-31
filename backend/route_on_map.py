# backend/route_on_map.py
import importlib.util
import os
import osmnx as ox
import networkx as nx
from PIL import Image, ImageDraw, ImageFont
import route_engine

_BASE_DIR = os.path.abspath(os.path.dirname(__file__))
config_path = os.path.join(_BASE_DIR, "app", "config.py")
spec = importlib.util.spec_from_file_location("app_config", config_path)
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)

# === НАСТРОЙКИ (должны совпадать с traffic_screen.py) ===
GRAPH_FILE = config.GRAPH_FILENAME
SCREENSHOT_FILE = os.path.join(_BASE_DIR, "traffic.png")
OUTPUT_FILE = "route_on_yandex.png"

# Границы карты должны совпадать с захваченным скриншотом
BBOX = config.FULL_CITY_BBOX
# Поправка для смещения маршрута на скриншоте
OFFSET_X = 75
OFFSET_Y = 250
# Точки маршрута
START = (58.5907, 49.6807)  # lat, lon — Ленина 111
END   = (58.5835, 49.5864)  # lat, lon — Ульяновская 30

def _project(lon, lat, img_w, img_h):
    """Проецирует координаты прямо на снимок в пределах BBOX"""
    if BBOX[2] == BBOX[0] or BBOX[3] == BBOX[1]:
        return img_w // 2, img_h // 2
    x = int((lon - BBOX[0]) / (BBOX[2] - BBOX[0]) * img_w) + OFFSET_X
    y = int((1 - (lat - BBOX[1]) / (BBOX[3] - BBOX[1])) * img_h) + OFFSET_Y
    return max(0, min(img_w-1, x)), max(0, min(img_h-1, y))

def _draw_grid(draw, img_w, img_h, font=None):
    """Рисует сетку с подписями координат для калибровки"""
    # Вертикальные линии (по долготе)
    for i, lon in enumerate([BBOX[0], (BBOX[0]+BBOX[2])/2, BBOX[2]]):
        x, _ = _project(lon, (BBOX[1]+BBOX[3])/2, img_w, img_h)
        draw.line([(x, 0), (x, img_h)], fill=(255, 0, 0, 128), width=1)
        if font:
            draw.text((x+5, 10), f"{lon:.4f}°E", fill="red", font=font)
    
    # Горизонтальные линии (по широте)
    for i, lat in enumerate([BBOX[1], (BBOX[1]+BBOX[3])/2, BBOX[3]]):
        _, y = _project((BBOX[0]+BBOX[2])/2, lat, img_w, img_h)
        draw.line([(0, y), (img_w, y)], fill=(0, 0, 255, 128), width=1)
        if font:
            draw.text((10, y+5), f"{lat:.4f}°N", fill="blue", font=font)
    
    # Углы BBOX (должны быть у краёв картинки)
    corners = [
        (BBOX[0], BBOX[1], "SW"), (BBOX[2], BBOX[1], "SE"),
        (BBOX[0], BBOX[3], "NW"), (BBOX[2], BBOX[3], "NE")
    ]
    for lon, lat, label in corners:
        x, y = _project(lon, lat, img_w, img_h)
        draw.rectangle([x-3, y-3, x+3, y+3], fill="yellow", outline="black")
        if font:
            draw.text((x+5, y-15), label, fill="yellow", font=font)

def main():
    if not os.path.exists(GRAPH_FILE) or not os.path.exists(SCREENSHOT_FILE):
        print("❌ Файлы не найдены. Проверь наличие graphml и traffic.png")
        return

    # Загрузка графа (берём тот же экземпляр, что и в route_engine)
    G, _ = route_engine.get_graph()
    img = Image.open(SCREENSHOT_FILE).convert("RGB")
    img_w, img_h = img.size
    draw = ImageDraw.Draw(img)
    
    print(f"📐 Скриншот: {img_w}×{img_h} | BBOX: {BBOX}")
    print(f"⚙️ Параметры: projection uses full screenshot width/height")
    print(f"⚙️ Смещение маршрута: OFFSET_X={OFFSET_X}, OFFSET_Y={OFFSET_Y}")

    # 🔍 ДИАГНОСТИКА: куда проецируются ключевые точки
    print(f"\n🎯 Проекция контрольных точек:")
    test_points = [
        ("Центр BBOX", (BBOX[0]+BBOX[2])/2, (BBOX[1]+BBOX[3])/2),
        ("Старт (Ленина)", START[1], START[0]),
        ("Финиш (Ульяновская)", END[1], END[0]),
    ]
    for name, lon, lat in test_points:
        x, y = _project(lon, lat, img_w, img_h)
        rel_x = x / img_w * 100
        rel_y = y / img_h * 100
        print(f"   {name:20s} → ({x:4d}, {y:4d}) [{rel_x:5.1f}%, {rel_y:5.1f}%]")
        # Подсказка: если % далеко от 50% — точка у края
        if rel_x < 10 or rel_x > 90 or rel_y < 10 or rel_y > 90:
            print(f"      ⚠️ У края! Проверь margins или BBOX")

    # Рисуем сетку для визуальной калибровки
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except:
        font = None
    _draw_grid(draw, img_w, img_h, font)

    # Маршрут: используем маршрут из `route_engine`
    route_result = route_engine.calculate_route_by_coords(START, END)
    route_coords = route_result.get("route", [])
    # Убедимся, что coords в формате [lon, lat]
    route_px = [_project(lon, lat, img_w, img_h) for lon, lat in route_coords]
    if len(route_px) > 1:
        draw.line(route_px, fill=(255,255,255), width=5)  # обводка
        draw.line(route_px, fill=(0,100,255), width=3)     # линия

    # Старт/финиш
    sx, sy = _project(START[1], START[0], img_w, img_h)
    ex, ey = _project(END[1], END[0], img_w, img_h)
    draw.ellipse([sx-12,sy-12,sx+12,sy+12], fill=(0,200,0), outline="black", width=2)
    draw.ellipse([ex-12,ey-12,ex+12,ey+12], fill=(255,50,50), outline="black", width=2)

    img.save(OUTPUT_FILE)
    print(f"\n✅ Сохранено: {OUTPUT_FILE}")
    # Дополнительно: сохранить рендер маршрута поверх графа (чистый граф без скриншота)
    try:
        path = route_result.get("path") if 'route_result' in locals() else None
        if path:
            import matplotlib.pyplot as plt
            # Рисуем базовый граф и затем маршрут на том же axes
            fig, ax = ox.plot_graph(G, show=False, close=False, node_size=0, bgcolor='white')
            ox.plot_graph_route(G, path, route_color="red", route_linewidth=3, ax=ax, show=False, close=False)
            graph_output = os.path.join(_BASE_DIR, "..", "route_on_graph.png")
            try:
                plt.savefig(graph_output, dpi=150, bbox_inches='tight')
                print(f"✅ Сохранено: {graph_output}")
            finally:
                plt.close(fig)
    except Exception:
        import traceback
        print("⚠️ Не удалось сохранить графовый рендер:")
        print(traceback.format_exc())
    print("👉 ОТКРОЙ КАРТИНКУ И СМОТРИ:")
    print("   • Красные линии = долгота, синие = широта")
    print("   • Жёлтые квадраты = углы BBOX (должны быть у краёв)")
    print("   • Если углы НЕ у краёв → меняй MARGIN_Х/Y")
    print("   • Если сетка не совпадает с дорогами → меняй MARGIN_Х/Y")
    print("\n💡 Быстрая калибровка:")
    print("   • Маркеры правее дорог? → увеличь MARGIN_X")
    print("   • Маркеры ниже дорог? → уменьши MARGIN_Y (или увеличь, если сдвиг вверх)")

if __name__ == "__main__":
    main()