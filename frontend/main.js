import './style.css'
import maplibregl from 'maplibre-gl';
import Toastify from 'toastify-js';

// Инициализация карты
const map = new maplibregl.Map({
    container: 'map',
    style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json', // Стильный минималистичный стиль без лишних логотипов
    center: [49.6074, 58.5967], // [lng, lat]
    zoom: 13,
    attributionControl: false // Отключаем стандартную плашку атрибуции для чистого вида
});

// Добавляем навигацию (зум + компас)
map.addControl(new maplibregl.NavigationControl(), 'top-right');

map.on('error', (e) => {
    console.error('Ошибка загрузки карты:', e);
    // Можно добавить фолбэк или уведомление, если карта критически важна
});

// Глобальные переменные для хранения объектов карты
let routeSource = null;
let routeLayer = null;
const markers = [];

// Backend API URL
const currentHost = window.location.hostname;
const API_BASE_URL = "https://voyx-api.onrender.com"

function showToast(message, type = 'info') {
    const colors = {
        success: '#14752a', // Зеленый
        error: '#9e111f',   // Красный
        warning: '#d5b721', // Желтый
        info: '#062b6c'     // Синий (как ваша тема)
    };

    Toastify({
        text: message,
        duration: 4000, // Время показа в мс
        gravity: "top", // Положение: top или bottom
        position: "center", // left, center, right
        style: {
            background: colors[type] || colors.info,
            color: "#fff",
            borderRadius: "10px",
            boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
            fontWeight: "500",
            fontSize: "14px"
        },
        close: true, // Крестик закрытия
        stopOnFocus: true, // Пауза при наведении
    }).showToast();
}

// Функции управления промежуточными точками (без изменений логики UI)
function addWaypoint() {
    const container = document.getElementById('waypoints-container');
    const div = document.createElement('div');
    div.className = 'form-group waypoint-group';

    const waypointNumber = document.querySelectorAll('.waypoint-input').length + 1;

    const svgPoint = `
        <svg class="waypoint-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
        <path d="M256 480 
                C 180 350, 140 200, 140 200 
                A 116 116 0 1 1 372 200 
                C 372 200, 332 350, 256 480 Z" 
                fill="#1F3178"/>
        
        <!-- Белый круг в центре -->
        <circle cx="256" cy="200" r="48" fill="#FFFFFF"/>
        </svg>
    `;

    div.innerHTML = `
            <span class="waypoint-text">${waypointNumber}</span>
            <input type="text" class="waypoint-input" placeholder="Промежуточная точка">
            <button class="btn-remove remove-btn">✕</button>
    `;
    container.appendChild(div);
    updateWaypointNumbers();
}

function removeWaypoint(button) {
    // Удаляем родительский элемент .waypoint-group
    const group = button.closest('.waypoint-group');
    if (group) {
        group.remove();
        updateWaypointNumbers();
    }
}

function updateWaypointNumbers() {
    const waypoints = document.querySelectorAll('.waypoint-group');
    waypoints.forEach((group, index) => {
        const textSpan = group.querySelector('.waypoint-text');
        if (textSpan) {
            textSpan.textContent = `${index + 1}`;
        }
    });
}

function getWaypoints() {
    const inputs = document.querySelectorAll('.waypoint-input');
    return Array.from(inputs).map(input => input.value).filter(v => v.trim());
}

// Создание кастомного маркера с эмодзи
function createCustomMarker(lng, lat, emoji, color, popupText) {
    // Создаем DOM элемент для маркера
    const el = document.createElement('div');
    el.className = 'custom-marker';
    el.innerHTML = `<span style="font-size: 24px; filter: drop-shadow(0 2px 2px rgba(0,0,0,0.3));">${emoji}</span>`;

    // Настраиваем маркер MapLibre
    const marker = new maplibregl.Marker({ element: el, anchor: 'bottom' })
        .setLngLat([lng, lat])
        .setPopup(new maplibregl.Popup({ offset: 25 }).setHTML(`<b>${popupText}</b>`))
        .addTo(map);

    return marker;
}

document.addEventListener('DOMContentLoaded', () => {
    // Обработчик для кнопки расчета маршрута
    const btn = document.getElementById('calculate-btn');
    if (btn) {
        btn.addEventListener('click', calculateRoute);
    }

    // Обработчик для кнопки добавления точки
    const addBtn = document.getElementById('add-waypoint-btn');
    if (addBtn) {
        addBtn.addEventListener('click', addWaypoint);
    }

    // ДЕЛЕГИРОВАНИЕ: Обработчик удаления точек (работает даже для динамически созданных)
    const container = document.getElementById('waypoints-container');
    if (container) {
        container.addEventListener('click', (event) => {
            // Проверяем, был ли клик по кнопке удаления (или её иконке)
            if (event.target.closest('.remove-btn')) {
                const button = event.target.closest('.remove-btn');
                // Удаляем родительский элемент (.waypoint-group)
                const group = button.closest('.waypoint-group');
                if (group) {
                    group.remove();
                    updateWaypointNumbers();
                }
            }
        });
    }
});


async function calculateRoute() {
    const startAddr = document.getElementById('start').value;
    const endAddr = document.getElementById('end').value;
    const waypoints = getWaypoints();
    const btn = document.getElementById('calculate-btn');
    const loading = document.getElementById('loading');
    const results = document.getElementById('results');
    const warning = document.getElementById('barrier-warning');

    // Валидация
    if (!startAddr.trim() || !endAddr.trim()) {
        showToast('Пожалуйста, заполните адреса старта и финиша', 'warning');
        return;
    }

    // Блокировка интерфейса
    btn.disabled = true;
    loading.style.display = 'block';
    results.classList.remove('active');
    warning.classList.remove('show');

    try {
        const response = await fetch(`${API_BASE_URL}/api/route`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                start_address: startAddr,
                end_address: endAddr,
                waypoints: waypoints
            })
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`Ошибка сервера: ${response.status} - ${errorText}`);
        }

        const res = await response.json();
        const data = res.data;

        console.log('✅ Данные получены:', data);

        // Валидация ответа
        if (!data || !data.route) {
            throw new Error('Сервер вернул невалидный ответ: отсутствуют данные маршрута');
        }
        if (!data.waypoints) {
            throw new Error('Сервер вернул невалидный ответ: отсутствуют waypoints');
        }

        // --- ОЧИСТКА КАРТЫ ---
        // Удаляем старые маркеры
        markers.forEach(m => m.remove());
        markers.length = 0;

        // Удаляем старый слой маршрута
        if (map.getLayer('route-line')) {
            map.removeLayer('route-line');
        }
        if (map.getSource('route-data')) {
            map.removeSource('route-data');
        }

        // --- ОТРИСОВКА МАРШРУТА ---
        // MapLibre использует [lng, lat]. Бэкенд возвращает [lon, lat], что совпадает.
        // Преобразуем массив координат в формат GeoJSON LineString
        const routeCoordinates = data.route.map(coord => [coord[0], coord[1]]);

        const routeColor = data.has_barriers ? '#e74c3c' : '#3498db';

        // Добавляем источник данных
        map.addSource('route-data', {
            'type': 'geojson',
            'data': {
                'type': 'Feature',
                'properties': {},
                'geometry': {
                    'type': 'LineString',
                    'coordinates': routeCoordinates
                }
            }
        });

        // Добавляем слой линии
        map.addLayer({
            'id': 'route-line',
            'type': 'line',
            'source': 'route-data',
            'layout': {
                'line-join': 'round',
                'line-cap': 'round'
            },
            'paint': {
                'line-color': routeColor,
                'line-width': 6,
                'line-opacity': 0.9
            }
        });

        // --- ОТРИСОВКА МАРКЕРОВ ---
        data.waypoints.forEach((wp, i) => {
            const isStart = i === 0;
            const isEnd = i === data.waypoints.length - 1;

            let emoji, label;
            if (isStart) {
                emoji = '📍';
                label = 'Старт';
            } else if (isEnd) {
                emoji = '🏁';
                label = 'Финиш';
            } else {
                emoji = '🔹';
                label = `Точка ${i}`;
            }

            const marker = createCustomMarker(wp.lon, wp.lat, emoji, isStart ? 'green' : (isEnd ? 'red' : 'orange'), `${label}<br>${wp.address}`);
            markers.push(marker);
        });

        // --- МАСШТАБИРОВАНИЕ ---
        // Вычисляем границы для отображения всего маршрута
        const bounds = new maplibregl.LngLatBounds();
        routeCoordinates.forEach(coord => bounds.extend(coord));

        map.fitBounds(bounds, {
            padding: { top: 50, bottom: 50, left: 50, right: 50 },
            duration: 1000 // Плавная анимация
        });

        // --- ОБНОВЛЕНИЕ ИНТЕРФЕЙСА ---
        document.getElementById('dist').textContent = data.distance_km + ' км';
        document.getElementById('time').textContent = data.time_min + ' мин';
        document.getElementById('points').textContent = data.waypoints.length;

        if (data.has_barriers) {
            warning.classList.add('show');
        } else {
            warning.classList.remove('show');
        }

        // Сегменты
        const segmentsContainer = document.getElementById('segments-container');
        if (data.segments && Array.isArray(data.segments) && data.segments.length > 1) {
            segmentsContainer.innerHTML = '<hr style="margin: 10px 0;"><strong>Сегменты:</strong>' +
                data.segments.map((s, i) => `
                    <div class="result-row" style="font-size: 13px; margin-top: 5px;">
                        <span>${i + 1}. ${s.from.split(',').slice(-2).join(',').trim()} → ${s.to.split(',').slice(-2).join(',').trim()}</span>
                        <span>${s.distance_km} км / ${s.time_min} мин</span>
                    </div>
                `).join('');
        } else {
            segmentsContainer.innerHTML = '';
        }

        results.classList.add('active');

    } catch (err) {
        console.error('💥 Ошибка:', err);
        console.error(err.message)
        showToast('Ошибка сервера', 'error');
    } finally {
        btn.disabled = false;
        loading.style.display = 'none';
    }
}