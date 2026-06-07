# backend/scripts/generate_seasonal_hourly_cache.py
import asyncio
import logging
import random
import sys
from pathlib import Path
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Добавляем путь к backend, чтобы импорты работали
sys.path.append(str(Path(__file__).parent.parent))

from app.traffic_screen import (
    capture_screenshot_for_datetime, precompute_edge_weights,
    set_traffic_image_path, _get_traffic_image, SCREENSHOTS_DIR,
    get_weekday_name, get_season, SEASON_MONTHS
)
from route_engine import get_graph

# ==============================================================================
# НАСТРОЙКИ
# ==============================================================================
INTERVAL_MINUTES = 60  # ⏳ Сейчас каждый час. Позже поменяем на 30!

# Берём конкретные даты для каждого сезона, чтобы Яндекс отдал исторические пробки
# Формат: (год, месяц, день) - берём первый день первого месяца сезона
SEASON_DATES = {
    'spring': datetime(2026, 3, 2, 0, 0, 0),   # Понедельник, 2 марта 2026
    'summer': datetime(2026, 6, 1, 0, 0, 0),   # Понедельник, 1 июня 2026
    'autumn': datetime(2026, 10, 5, 0, 0, 0),   # Понедельник, 7 сентября 2026
    'winter': datetime(2026, 12, 7, 0, 0, 0)   # Понедельник, 7 декабря 2026
}

async def main():
    logger.info("🧹 Очистка старых файлов в screenshots/...")
    count = 0
    for ext in ["*.png", "*.cache.pkl", "*.edge_cache.pkl"]:
        for file_path in SCREENSHOTS_DIR.glob(ext):
            try:
                file_path.unlink()
                count += 1
            except Exception as e:
                logger.warning(f"Не удалось удалить {file_path}: {e}")
    logger.info(f"✅ Удалено {count} старых файлов.")

    logger.info("🌐 Инициализация графа...")
    G, blocked_edges_set = get_graph()

    # Подсчёт общего количества задач
    hours_per_day = 24 * 60 // INTERVAL_MINUTES
    total_tasks = 4 * 7 * hours_per_day  # 4 сезона × 7 дней × часов в дне
    current_task = 0
    
    logger.info(f"🚀 Начало генерации {total_tasks} скриншотов...")
    logger.info(f"📊 Формат: 4 сезона × 7 дней × {hours_per_day} часов = {total_tasks} файлов")

    # Для каждого сезона
    for season_name, base_date in SEASON_DATES.items():
        logger.info(f"🌸 Обрабатываю сезон: {season_name}")
        
        # Для каждого дня недели (0-6)
        for day_offset in range(7):
            current_date = base_date + timedelta(days=day_offset)
            weekday_name = get_weekday_name(current_date)
            logger.info(f"  📅 День: {weekday_name}")

            # Для каждого часа
            for hour in range(0, 24, INTERVAL_MINUTES // 60):
                current_task += 1
                dt = current_date.replace(hour=hour, minute=0, second=0, microsecond=0)
                
                # Формируем имя файла: день_недели_час_минута_сезон.png
                filename = f"{weekday_name}_{hour:02d}_00_{season_name}.png"
                output_path = SCREENSHOTS_DIR / filename

                logger.info(f"    [{current_task}/{total_tasks}] ⏳ {dt.strftime('%A %H:%M')} ({season_name})")

                try:
                    # 1. Делаем скриншот
                    result_path = await capture_screenshot_for_datetime(dt, output_path)
                    if not result_path or not result_path.exists():
                        logger.warning(f"      ⚠️ Скриншот не создан (CAPTCHA?). Пропускаю.")
                        continue

                    # 2. Создаём кэш рёбер (.edge_cache.pkl)
                    set_traffic_image_path(result_path)
                    await precompute_edge_weights(G, blocked_edges_set, use_traffic=True)

                    # 3. Создаём пиксельный кэш (.cache.pkl)
                    _get_traffic_image()

                    # 4. Удаляем тяжёлый .png, кэши уже на диске!
                    result_path.unlink()
                    logger.info(f"      ✅ Кэши созданы, .png удалён для экономии места.")

                except Exception as e:
                    logger.error(f"      ❌ Ошибка при обработке {dt.strftime('%H:%M')}: {e}", exc_info=True)

                # 5. АНТИ-БЛОК: Пауза 5-9 сек
                sleep_time = random.uniform(5.0, 9.0)
                logger.info(f"      😴 Пауза {sleep_time:.1f} сек...")
                await asyncio.sleep(sleep_time)

    logger.info("🎉 Все скриншоты успешно обработаны и закэшированы!")
    logger.info(f"📊 Итого создано: {total_tasks} кэшей рёбер")

if __name__ == "__main__":
    asyncio.run(main())