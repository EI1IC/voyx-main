# backend/main.py
import os, sys, logging, time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from dotenv import load_dotenv
from route_engine import get_graph

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
sys.path.insert(0, os.path.dirname(__file__))

from route_engine import get_graph, calculate_route, calculate_multi_point_route, clear_interpolation_cache
from app.graph import init_graph
from app.traffic_screen import (
    capture_current_screenshot, capture_screenshot_for_datetime,
    update_all_seasonal_screenshots, prebuild_all_caches,
    set_traffic_image_path, get_screenshot_for_datetime, is_screenshot_fresh,
    check_seasonal_integrity,
    IMG_PATH_CURRENT, SCREENSHOTS_DIR,
    load_metadata, get_metadata_summary
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Инициализация...")
    
    try:
        G, blocked_edges_set = get_graph()
        logger.info("✅ Граф загружен")
    except Exception as e:
        logger.error(f"❌ Не удалось загрузить граф: {e}", exc_info=True)
        raise
    
    # 1. Предзагрузка текущего скриншота (для работы с реальным временем)
    try:
        if not IMG_PATH_CURRENT.exists():
            logger.info("📸 Предзагрузка текущего скриншота...")
            await capture_current_screenshot()
        else:
            logger.info("📦 Текущий скриншот уже существует")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось предзагрузить текущий скриншот: {e}")
    
    # 2. Проверка наличия кэшей рёбер (создаются скриптом generate_seasonal_hourly_cache.py)
    try:
        cache_files = list(SCREENSHOTS_DIR.glob("*.edge_cache.pkl"))
        expected_count = 4 * 7 * 24  # 4 сезона × 7 дней × 24 часа = 672
        
        logger.info(f"📊 Найдено {len(cache_files)} кэшей рёбер в папке screenshots/")
        
        if len(cache_files) < expected_count:
            logger.warning(f"⚠️ Ожидается {expected_count} кэшей, найдено {len(cache_files)}.")
            logger.warning("💡 Запустите скрипт: python generate_seasonal_hourly_cache.py")
        else:
            logger.info(f"✅ Все {expected_count} кэшей рёбер готовы")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка проверки кэшей: {e}")
    
    logger.info("✅ Граф загружен. Система готова.")
    yield
    logger.info("🛑 Завершение...")

app = FastAPI(title="Маршрутизация Киров", version="2.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware,
                   allow_origins=[
                       "https://ei1ic.github.io",
                       "https://ei1ic.github.io/voyx-main",
                       "http://localhost:3000",
                       "http://localhost:5173",
                   ],
                   allow_credentials=True,
                   allow_methods=["GET","POST","OPTIONS","HEAD"],
                   allow_headers=["*"],
                  )

@app.options("/{path:path}")
async def options_handler(path: str):
    """Обработка preflight запросов"""
    from fastapi.responses import Response
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "https://ei1ic.github.io",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS, HEAD",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Max-Age": "3600",
        }
    )

@app.get("/")
@app.head("/")
async def root():
    """Корневой endpoint для проверки работоспособности"""
    return {
        "service": "Voyx Route API",
        "version": "2.0.0",
        "status": "running",
        "endpoints": {
            "health": "/health",
            "route": "/api/route",
            "docs": "/docs"
        }
    }
class RouteRequest(BaseModel):
    start_address: str
    end_address: str
    waypoints: Optional[List[str]] = []
    use_traffic: bool = True
    departure_time: Optional[str] = None
    arrival_time: Optional[str] = None
    optimize_order: bool = False

@app.post("/api/route")
async def route_api(req: RouteRequest):
    try:
        if req.use_traffic:
            has_time = bool(req.departure_time or req.arrival_time)
            
            if has_time:
                import datetime
                target_str = req.departure_time or req.arrival_time
                target_dt = datetime.datetime.fromisoformat(target_str.replace("Z", "+00:00"))
                
                screenshot_path = get_screenshot_for_datetime(target_dt)
                
                if not is_screenshot_fresh(screenshot_path):
                    logger.info(f"📸 Создаю сезонный скриншот: {screenshot_path.name}")
                    await capture_screenshot_for_datetime(target_dt, screenshot_path)
                else:
                    logger.info(f"📦 Используем кэшированный: {screenshot_path.name}")
                
                set_traffic_image_path(screenshot_path)
            else:
                set_traffic_image_path(IMG_PATH_CURRENT)
                if not IMG_PATH_CURRENT.exists():
                    await capture_current_screenshot()
                else:
                    file_age = time.time() - IMG_PATH_CURRENT.stat().st_mtime
                    if file_age > 900:
                        await capture_current_screenshot()
        
        # ✅ Очищаем кэш интерполяции при смене временного контекста
        clear_interpolation_cache()
        
        if req.waypoints:
            all_points = [req.start_address] + req.waypoints + [req.end_address]
            result = calculate_multi_point_route(all_points, req.use_traffic, req.departure_time, req.arrival_time, req.optimize_order)
        else:
            result = calculate_route(req.start_address, req.end_address, req.use_traffic, req.departure_time, req.arrival_time)
        
        if os.getenv("DEBUG_TRAFFIC", "False").lower() == "true":
            try:
                from app.traffic_screen import debug_route_traffic
                debug_route_traffic(result["route"], req.use_traffic)
            except: pass
        
        return {"status": "success", "data": result}
        
    except ValueError as e:
        logger.warning(f"⚠️ Ошибка валидации: {e}")
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"❌ Ошибка в /api/route: {e}", exc_info=True)
        raise HTTPException(500, "Внутренняя ошибка сервера")

@app.post("/api/traffic/capture-current")
async def capture_current():
    try:
        path = await capture_current_screenshot()
        if path: return {"status": "success", "message": "Текущий скриншот обновлён", "path": str(path)}
        else: return {"status": "error", "message": "Не удалось захватить скриншот"}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/traffic/update-seasonal")
async def update_seasonal():
    try:
        await update_all_seasonal_screenshots()
        return {"status": "success", "message": "Все сезонные скриншоты обновлены"}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/traffic/prebuild-caches")
async def prebuild_caches():
    try:
        await prebuild_all_caches()
        return {"status": "success", "message": "Все кэши созданы"}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/traffic/metadata")
async def get_traffic_metadata():
    try:
        metadata = load_metadata()
        summary = get_metadata_summary()
        return {"status": "success", "summary": summary, "files": metadata}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/traffic/integrity")
async def check_integrity():
    try:
        integrity = check_seasonal_integrity()
        return {"status": "success", "data": integrity}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/health")
async def health_check():
    current_exists = IMG_PATH_CURRENT.exists()
    seasonal_count = len(list(SCREENSHOTS_DIR.glob("traffic_*.png")))
    cache_count = len(list(SCREENSHOTS_DIR.glob("*.cache.pkl")))
    metadata = get_metadata_summary()
    return {
        "status": "ok",
        "graph_loaded": True,
        "traffic_current": "exists" if current_exists else "missing",
        "traffic_seasonal_count": seasonal_count,
        "cache_count": cache_count,
        "traffic_metadata": metadata,
        "api_version": "2.0.0"
    }

if __name__ == "__main__":
    import uvicorn
    import os

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
