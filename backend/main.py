# backend/main.py
import os
import sys
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

# ✅ ИМПОРТЫ: только то, что реально используется
from route_engine import calculate_route, calculate_route_by_coords
try:
    from app.routing import calculate_multi_point_route
except ImportError:
    calculate_multi_point_route = None
from app.graph import init_graph
# ✅ Импорт для ручного обновления скриншота
from app.traffic_screen import capture_screenshot

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация: только загрузка графа"""
    logger.info("🚀 Инициализация приложения...")
    init_graph()  # Загружаем OSM граф и барьеры
    yield
    logger.info("🛑 Завершение работы...")
    # Больше никаких фоновых задач, только чистый выход

app = FastAPI(
    title="Маршрутизация Киров",
    description="API для оптимизации курьерских маршрутов с учётом пробок (скриншоты + OSM)",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class RouteRequest(BaseModel):
    start_address: str
    end_address: str
    waypoints: Optional[List[str]] = []
    use_traffic: bool = True  # ✅ Флаг: учитывать ли цвет со скриншота

class MultiPointRequest(BaseModel):
    waypoints: List[str]
    use_traffic: bool = True

# ✅ Вспомогательная функция для многоточечных маршрутов
def _simple_multi_point_route(waypoints: List[str], use_traffic: bool = True):
    """Последовательно соединяет точки через calculate_route"""
    if len(waypoints) < 2:
        raise ValueError("Нужно минимум 2 точки")
    
    all_coords = []
    total_distance = 0
    total_time = 0
    last_result = None
    
    for i in range(len(waypoints) - 1):
        last_result = calculate_route(waypoints[i], waypoints[i+1], use_traffic=use_traffic)
        if i == 0:
            all_coords.extend(last_result["route"])
        else:
            all_coords.extend(last_result["route"][1:])  # Избегаем дублей
        total_distance += last_result["distance_km"]
        total_time += last_result["time_min"]
    
    if not last_result:
        raise ValueError("Не удалось построить маршрут")
        
    return {
        "route": all_coords,
        "distance_km": round(total_distance, 2),
        "time_min": round(total_time, 1),
        "start": last_result["start"],
        "end": last_result["end"],
        "has_barriers": last_result.get("has_barriers", False)
    }

@app.post("/api/route")
async def calculate_route_api(req: RouteRequest):
    """Рассчитывает маршрут с учётом пробок (опционально)"""
    try:
        if req.waypoints:
            all_points = [req.start_address] + req.waypoints + [req.end_address]
            if calculate_multi_point_route:
                result = calculate_multi_point_route(all_points, use_traffic=req.use_traffic)
            else:
                result = _simple_multi_point_route(all_points, use_traffic=req.use_traffic)
        else:
            # ✅ Вызов из route_engine с поддержкой use_traffic
            result = calculate_route(
                req.start_address,
                req.end_address,
                use_traffic=req.use_traffic
            )
        return {"status": "success", "data": result}
    except ValueError as e:
        logger.warning(f"⚠️ Ошибка валидации: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"❌ Ошибка в /api/route: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")

@app.post("/api/route/multi")
async def calculate_multi_route_api(req: MultiPointRequest):
    """Многоточечный маршрут"""
    try:
        if calculate_multi_point_route:
            result = calculate_multi_point_route(req.waypoints, use_traffic=req.use_traffic)
        else:
            result = _simple_multi_point_route(req.waypoints, use_traffic=req.use_traffic)
        return {"status": "success", "data": result}
    except ValueError as e:
        logger.warning(f"⚠️ Ошибка валидации: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"❌ Ошибка в /api/route/multi: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")

# ✅ Новый эндпоинт: ручной захват скриншота (вместо старого API-коллектора)
@app.post("/api/traffic/capture")
async def capture_traffic_screen():
    """
    Делает свежий скриншот Яндекс.Карт с пробками.
    Это заменяет старый /api/traffic/refresh, который работал с сегментами.
    """
    try:
        logger.info("📸 Запуск захвата скриншота пробок...")
        await capture_screenshot()  # Асинхронный вызов из traffic_screen.py
        return {"status": "success", "message": "Скриншот обновлён", "path": "traffic.png"}
    except Exception as e:
        logger.error(f"❌ Ошибка при захвате скриншота: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка захвата: {str(e)}")

@app.get("/health")
async def health_check():
    """Проверка работоспособности"""
    from pathlib import Path
    traffic_path = Path(__file__).parent.parent / "traffic.png"
    return {
        "status": "ok",
        "graph_loaded": True,
        "traffic_screenshot": "exists" if traffic_path.exists() else "missing",
        "api_version": "1.0.0"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info"
    )