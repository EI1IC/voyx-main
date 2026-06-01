# backend/main.py
import os
import sys
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv

# ==============================================================================
# НАСТРОЙКИ
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Загрузка .env и добавление текущей директории в PATH
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
sys.path.insert(0, os.path.dirname(__file__))

# Импорт модулей проекта
from route_engine import calculate_route, calculate_multi_point_route
from app.graph import init_graph
from app.traffic_screen import capture_screenshot

# ==============================================================================
# LIFECYCLE
# ==============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Инициализация при старте и очистка при остановке"""
    logger.info("🚀 Инициализация приложения...")
    init_graph()  # Загрузка/построение графа OSM + барьеры
    logger.info("✅ Граф загружен. Система готова.")
    yield
    logger.info("🛑 Завершение работы...")

# ==============================================================================
# FASTAPI APP
# ==============================================================================
app = FastAPI(
    title="Маршрутизация Киров",
    description="API для оптимизации курьерских маршрутов с учётом пробок (скриншоты + OSM)",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Для разработки/Codespaces. В продакшене замени на ["https://yourdomain.com"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================================================
# MODELS
# ==============================================================================
class RouteRequest(BaseModel):
    start_address: str
    end_address: str
    waypoints: Optional[List[str]] = []
    use_traffic: bool = True
    departure_time: Optional[str] = None  # ISO: YYYY-MM-DDTHH:MM:SS
    arrival_time: Optional[str] = None    # ISO: YYYY-MM-DDTHH:MM:SS

# ==============================================================================
# ENDPOINTS
# ==============================================================================
@app.post("/api/route")
async def route_api(req: RouteRequest):
    """Основной эндпоинт расчёта маршрута"""
    import time
    from pathlib import Path
    
    try:
        # ✅ Простая проверка: обновлять ли скриншот (через mtime файла)
        if req.use_traffic:
            traffic_path = Path(__file__).parent / "traffic.png"
            should_refresh = False
            
            if not traffic_path.exists():
                should_refresh = True
                logger.info("🔄 Скриншот не найден, создаю...")
            else:
                # Проверяем возраст файла (15 минут = 900 сек)
                file_age = time.time() - traffic_path.stat().st_mtime
                if file_age > 900:
                    should_refresh = True
                    logger.info(f"🔄 Скриншот устарел ({file_age/60:.1f} мин), обновляю...")
            
            # Захват скриншота (если нужно)
            if should_refresh:
                try:
                    await capture_screenshot()
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось обновить скриншот: {e}")
                    # Продолжаем со старым скриншотом

        # Расчёт маршрута
        if req.waypoints:
            all_points = [req.start_address] + req.waypoints + [req.end_address]
            result = calculate_multi_point_route(
                all_points, use_traffic=req.use_traffic,
                departure_time=req.departure_time, arrival_time=req.arrival_time
            )
        else:
            result = calculate_route(
                req.start_address, req.end_address, use_traffic=req.use_traffic,
                departure_time=req.departure_time, arrival_time=req.arrival_time
            )

                    # 🔍 DEBUG: вывод влияния пробок на весь маршрут (включается через .env)
        import os
        if os.getenv("DEBUG_TRAFFIC", "False").lower() == "true":
            from app.traffic_screen import debug_route_traffic
            debug_route_traffic(result["route"], req.use_traffic)
            
        return {"status": "success", "data": result}
        
    except ValueError as e:
        logger.warning(f"⚠️ Ошибка валидации: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"❌ Ошибка в /api/route: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")

@app.post("/api/traffic/capture")
async def capture_traffic():
    """Ручное обновление скриншота пробок Яндекс.Карт"""
    try:
        logger.info("📸 Запуск захвата скриншота пробок...")
        await capture_screenshot()
        return {"status": "success", "message": "Скриншот обновлён"}
    except Exception as e:
        logger.error(f"❌ Ошибка захвата скриншота: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Ошибка захвата: {str(e)}")

@app.get("/health")
async def health_check():
    """Проверка работоспособности API"""
    from pathlib import Path
    traffic_path = Path(__file__).parent.parent / "traffic.png"
    return {
        "status": "ok",
        "graph_loaded": True,
        "traffic_screenshot": "exists" if traffic_path.exists() else "missing",
        "api_version": "1.0.0"
    }

# ==============================================================================
# ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )