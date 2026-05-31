# backend/visualize_graph.py
import os
import osmnx as ox
import networkx as nx
import matplotlib.pyplot as plt
from app.config import GRAPH_FILENAME 

# === НАСТРОЙКИ ===
GRAPH_FILE = "kirov_road_network.graphml"
OUTPUT_FILE = "graph_with_route.png"

# Точки маршрута (широта, долгота)
# Можешь заменить на любые адреса Кирова
START_LAT, START_LON = 58.5907, 49.6807  # Ленина 111
END_LAT,   END_LON   = 58.5835, 49.5864  # Ульяновская 30

def main():
    # 1. Загрузка графа
    if not os.path.exists(GRAPH_FILENAME):
        print(f"❌ Файл графа не найден: {GRAPH_FILE}")
        print("💡 Положи kirov_road_network.graphml в папку backend/")
        return

    print("📦 Загрузка графа...")
    G = ox.load_graphml(GRAPH_FILENAME)
    print(f"✅ Загружено: {len(G.nodes)} узлов, {len(G.edges)} рёбер")

    # 2. Поиск ближайших узлов к координатам
    # OSMnx ожидает X=долгота, Y=широта
    start_node = ox.nearest_nodes(G, X=START_LON, Y=START_LAT)
    end_node   = ox.nearest_nodes(G, X=END_LON,   Y=END_LAT)
    print(f"📍 Найденные узлы: {start_node} → {end_node}")

    # 3. Построение маршрута (кратчайший по длине)
    print("🛣️ Расчёт маршрута...")
    try:
        route = nx.shortest_path(G, source=start_node, target=end_node, weight="length")
        print(f"✅ Маршрут найден: {len(route)} узлов")
    except nx.NetworkXNoPath:
        print("❌ Путь не найден! Проверьте связность графа.")
        return

    # 4. Визуализация
    print("🎨 Генерация изображения...")
    fig, ax = ox.plot_graph(
        G,
        ax=None,
        node_size=0,
        edge_color="#d3d3d3",
        edge_linewidth=0.6,
        bgcolor="white",
        show=False,
        close=False
    )

    # Получаем координаты маршрута
    route_lons = [G.nodes[n]["x"] for n in route]
    route_lats = [G.nodes[n]["y"] for n in route]

    # Рисуем маршрут поверх графа
    ax.plot(route_lons, route_lats, color="blue", linewidth=2.5, alpha=0.9, zorder=10)
    
    # Точки старта (зелёная) и финиша (красная)
    ax.scatter([route_lons[0], route_lons[-1]], [route_lats[0], route_lats[-1]],
               c=["green", "red"], s=120, zorder=11, edgecolors="black", linewidth=1.5)

    ax.set_title("Дорожная сеть OSM + Построенный маршрут", fontsize=14, pad=10)
    plt.tight_layout()
    plt.savefig(OUTPUT_FILE, dpi=150, bbox_inches="tight")
    plt.close()

    print(f"💾 Картинка сохранена: {OUTPUT_FILE}")
    print("👉 Откройте файл, чтобы увидеть граф и маршрут.")

if __name__ == "__main__":
    main()