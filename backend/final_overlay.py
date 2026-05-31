# backend/simple_overlay.py
import osmnx as ox
import networkx as nx
import matplotlib.pyplot as plt
from PIL import Image
import os
from app.config import GRAPH_FILENAME,_BASE_DIR

GRAPH = GRAPH_FILENAME
IMG = os.path.join(_BASE_DIR, "traffic.png")
OUT = "simple_overlay.png"

if not os.path.exists(GRAPH) or not os.path.exists(IMG):
    print("❌ Положи kirov_road_network.graphml и traffic.png в папку backend/")
    exit()

print("📦 Загрузка...")
G = ox.load_graphml(GRAPH)
img = Image.open(IMG)

# 1️⃣ Границы графа (долгота/широта)
xs = [d['x'] for _, d in G.nodes(data=True)]
ys = [d['y'] for _, d in G.nodes(data=True)]
W, E, S, N = min(xs), max(xs), min(ys), max(ys)

# 2️⃣ Маршрут
start = ox.nearest_nodes(G, X=49.6807, Y=58.5907)  # Ленина 111
end = ox.nearest_nodes(G, X=49.5864, Y=58.5835)    # Ульяновская 30
route = nx.shortest_path(G, start, end, weight="length")
rx = [G.nodes[n]['x'] for n in route]
ry = [G.nodes[n]['y'] for n in route]

# 3️⃣ Рисуем
fig, ax = plt.subplots(figsize=(12, 10))
fig.patch.set_facecolor('white')

# Скриншот: привязываем ровно к границам графа
ax.imshow(img, extent=[W, E, S, N], origin='upper', aspect='equal', alpha=0.9)

# Рёбра графа (серый фон)
for u, v in G.edges():
    x1, y1 = G.nodes[u]['x'], G.nodes[u]['y']
    x2, y2 = G.nodes[v]['x'], G.nodes[v]['y']
    ax.plot([x1, x2], [y1, y2], color='#333', linewidth=0.4, alpha=0.3)

# Маршрут (синяя линия)
ax.plot(rx, ry, color='#0066ff', linewidth=3.5, zorder=10)
ax.scatter([rx[0], rx[-1]], [ry[0], ry[-1]], c=['#00cc44', '#ff3333'], s=100, zorder=11)

# Фиксируем область и пропорции
ax.set_xlim(W, E)
ax.set_ylim(S, N)
ax.set_aspect('equal')  # ✅ Гарантирует: НИЧЕГО НЕ РАСТЯГИВАЕТСЯ
ax.axis('off')
plt.tight_layout(pad=0)
plt.savefig(OUT, dpi=200, bbox_inches='tight')
print(f"✅ Сохранено: {OUT}")
print("👉 Если появились белые поля → пропорции скриншота и графа не совпадают. Это нормально.")