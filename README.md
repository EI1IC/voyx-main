# Voyx - маршрутизация Киров

Проект разделён на backend (API) и frontend (веб-интерфейс).

## 🏗️ Структура проекта

- **backend/** - FastAPI сервер с API для расчёта маршрутов
- **frontend/** - Статический веб-интерфейс (HTML/CSS/JS) с картой Leaflet
- **docs/** - Документация (создаётся при сборке)

## � Быстрый старт (Одна команда!)

### Предварительная подготовка (только первый раз)

```bash
# 1. Убедитесь что установлены зависимости
cd backend
python -m venv .venv
source .venv/bin/activate  # На Windows: .venv\Scripts\activate
pip install -r requirements.txt
cd ..
```

### Запуск всего проекта

```bash
# Запустить backend и frontend одновременно
npm start
```

Откройте браузер:
- **Frontend**: `http://localhost:3000`
- **Backend API**: `http://localhost:8000`
- **API Docs**: `http://localhost:8000/docs`

---

## 📋 Требования

- Python 3.10+
- Node.js (для npm)

---

## 🎯 Доступные npm команды

### Для запуска

| Команда | Описание |
|---------|---------|
| `npm start` | Запустить backend + frontend (production) |
| `npm run dev` | Запустить backend + frontend (development с перезагрузкой) |
| `npm run backend` | Только backend |
| `npm run frontend` | Только frontend |
| `npm run backend:only` | Только backend (альтернатива) |
| `npm run frontend:only` | Только frontend (альтернатива) |

### Для настройки

| Команда | Описание |
|---------|---------|
| `npm run backend:install` | Установить Python зависимости |
| `npm run build` | Создать документацию |
| `npm stop` | Остановить все процессы |
| `npm test` | Запустить тесты |

---

## 🛣️ API Endpoints

### POST /api/route
Рассчитывает маршрут между двумя точками

**Request:**
```json
{
  "start_address": "Киров, улица Ульяновская, 30",
  "end_address": "Киров, улица Ленина, 50",
  "waypoints": []
}
```

**Response:**
```json
{
  "status": "success",
  "data": {
    "route": [[lon, lat], ...],
    "distance_km": 5.2,
    "time_min": 12,
    "waypoints": [...],
    "has_barriers": false,
    "segments": [...]
  }
}
```

### POST /api/route/multi
Рассчитывает маршрут через несколько точек

**Request:**
```json
{
  "waypoints": ["Адрес 1", "Адрес 2", "Адрес 3"]
}
```

---

## 📦 Технологии

### Backend
- FastAPI - веб-фреймворк
- OSMnx - работа с картами OpenStreetMap
- NetworkX - работа с графами
- GeoPandas - географические данные

### Frontend
- Leaflet - интерактивные карты
- Vanilla JavaScript - логика приложения

---

## 🔧 Configuration

### Frontend

Настройка разных backend адресов:

В `frontend/src/index.html` добавьте перед загрузкой `main.js`:
```html
<script>
  window.API_BASE_URL = 'http://api.example.com:8000';
</script>
```

---

## 📚 Подробная документация

- [Backend документация](backend/README.md)
- [Frontend документация](frontend/README.md)

---

## 📝 Лицензия

MIT

---

## 👤 Автор

EI1IC
