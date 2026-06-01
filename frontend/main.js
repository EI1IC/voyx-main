import './style.css'
import maplibregl from 'maplibre-gl';
import Toastify from 'toastify-js';

// Инициализация карты
const map = new maplibregl.Map({
    container: 'map',
    style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
    center: [49.6074, 58.5967],
    zoom: 13,
    attributionControl: false
});
map.addControl(new maplibregl.NavigationControl(), 'top-right');
map.on('error', (e) => console.error('Ошибка загрузки карты:', e));

let routeSource = null;
let routeLayer = null;
const markers = [];

// ✅ API URL (Codespaces)
const API_BASE_URL = "https://super-parakeet-5w9wx75xjvv3rwp-8000.app.github.dev";

function showToast(message, type = 'info') {
    const colors = { success: '#14752a', error: '#9e111f', warning: '#d5b721', info: '#062b6c' };
    Toastify({
        text: message, duration: 4000, gravity: "top", position: "center",
        style: { background: colors[type], color: "#fff", borderRadius: "10px", boxShadow: "0 4px 12px rgba(0,0,0,0.15)", fontWeight: "500", fontSize: "14px" },
        close: true, stopOnFocus: true,
    }).showToast();
}

// Управление промежуточными точками
function addWaypoint() {
    const container = document.getElementById('waypoints-container');
    const div = document.createElement('div');
    div.className = 'form-group waypoint-group';
    const waypointNumber = document.querySelectorAll('.waypoint-input').length + 1;
    div.innerHTML = `<span class="waypoint-text">${waypointNumber}</span>
        <input type="text" class="waypoint-input" placeholder="Промежуточная точка">
        <button class="btn-remove remove-btn">✕</button>`;
    container.appendChild(div);
    updateWaypointNumbers();
}
function removeWaypoint(button) {
    const group = button.closest('.waypoint-group');
    if (group) { group.remove(); updateWaypointNumbers(); }
}
function updateWaypointNumbers() {
    document.querySelectorAll('.waypoint-group').forEach((group, index) => {
        const textSpan = group.querySelector('.waypoint-text');
        if (textSpan) textSpan.textContent = `${index + 1}`;
    });
}
function getWaypoints() {
    return Array.from(document.querySelectorAll('.waypoint-input')).map(i => i.value).filter(v => v.trim());
}

function createCustomMarker(lng, lat, emoji, color, popupText) {
    const el = document.createElement('div');
    el.className = 'custom-marker';
    el.innerHTML = `<span style="font-size: 24px; filter: drop-shadow(0 2px 2px rgba(0,0,0,0.3));">${emoji}</span>`;
    return new maplibregl.Marker({ element: el, anchor: 'bottom' })
        .setLngLat([lng, lat])
        .setPopup(new maplibregl.Popup({ offset: 25 }).setHTML(`<b>${popupText}</b>`))
        .addTo(map);
}

// 🔽 Логика выпадающей панели параметров + кнопки "сейчас"/"сброс"
document.addEventListener('DOMContentLoaded', () => {
    const paramsToggle = document.getElementById('params-toggle');
    const paramsPanel = document.getElementById('params-panel');
    
    paramsToggle.addEventListener('click', () => {
        paramsToggle.classList.toggle('open');
        paramsPanel.classList.toggle('open');
    });

    // ⏰ Кнопка "сейчас" — подставляет текущие дату и время в отправление
    document.getElementById('set-now')?.addEventListener('click', () => {
        const now = new Date();
        const timeStr = now.toTimeString().slice(0, 5); // "HH:MM"
        const dateStr = now.toISOString().slice(0, 10); // "YYYY-MM-DD"
        document.getElementById('departure-time').value = timeStr;
        document.getElementById('departure-date').value = dateStr;
        // Визуальный отклик
        const btn = document.getElementById('set-now');
        btn.style.color = '#2980b9';
        setTimeout(() => { btn.style.color = ''; }, 200);
    });

    // 🔄 Кнопка "сброс" — очищает поля прибытия
    document.getElementById('reset-arrival')?.addEventListener('click', () => {
        document.getElementById('arrival-time').value = '';
        document.getElementById('arrival-date').value = '';
        // Визуальный отклик
        const btn = document.getElementById('reset-arrival');
        btn.style.color = '#e74c3c';
        setTimeout(() => { btn.style.color = ''; }, 200);
    });

    // Обработчики кнопок
    document.getElementById('calculate-btn')?.addEventListener('click', calculateRoute);
    document.getElementById('add-waypoint-btn')?.addEventListener('click', addWaypoint);
    document.getElementById('waypoints-container')?.addEventListener('click', (e) => {
        if (e.target.closest('.remove-btn')) {
            const group = e.target.closest('.remove-btn').closest('.waypoint-group');
            if (group) { group.remove(); updateWaypointNumbers(); }
        }
    });
});

// 🕰️ Вспомогательная функция: объединяет time+date в ISO
function combineDateTime(timeVal, dateVal) {
    if (!timeVal || !dateVal) return null;
    return `${dateVal}T${timeVal}:00`;
}

// 🕰️ Вспомогательная функция: форматирует время для отображения
function formatTimeDisplay(isoString) {
    if (!isoString) return null;
    try {
        const dt = new Date(isoString);
        return dt.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    } catch {
        return isoString.slice(11, 16);
    }
}

async function calculateRoute() {
    const startAddr = document.getElementById('start').value;
    const endAddr = document.getElementById('end').value;
    const waypoints = getWaypoints();
    const btn = document.getElementById('calculate-btn');
    const loading = document.getElementById('loading');
    const results = document.getElementById('results');
    const warning = document.getElementById('barrier-warning');
    const timeMismatchWarning = document.getElementById('time-mismatch-warning');

    if (!startAddr.trim() || !endAddr.trim()) {
        showToast('Пожалуйста, заполните адреса старта и финиша', 'warning');
        return;
    }

    btn.disabled = true;
    loading.style.display = 'block';
    results.classList.remove('active');
    warning.classList.remove('show');
    timeMismatchWarning.classList.remove('show');

    // 🕰️ Чтение раздельных полей времени/даты
    const depTime = document.getElementById('departure-time').value;
    const depDate = document.getElementById('departure-date').value;
    const arrTime = document.getElementById('arrival-time').value;
    const arrDate = document.getElementById('arrival-date').value;
    const useTraffic = document.getElementById('use-traffic').checked;

    // Формируем запрос
    const payload = {
        start_address: startAddr,
        end_address: endAddr,
        waypoints: waypoints,
        use_traffic: useTraffic
    };
    
    const depISO = combineDateTime(depTime, depDate);
    const arrISO = combineDateTime(arrTime, arrDate);
    
    if (depISO) payload.departure_time = depISO;
    if (arrISO) payload.arrival_time = arrISO;

    try {
        const response = await fetch(`${API_BASE_URL}/api/route`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`Ошибка сервера: ${response.status} - ${errorText}`);
        }

        const res = await response.json();
        const data = res.data;

        if (!data || !data.route || !data.waypoints) {
            throw new Error('Сервер вернул невалидный ответ');
        }

        // 🕰️ АВТОЗАПОЛНЕНИЕ ПОЛЕЙ при получении ответа
        if (data.departure_iso) {
            const [depDate, depTime] = data.departure_iso.split('T');
            document.getElementById('departure-date').value = depDate;
            document.getElementById('departure-time').value = depTime.slice(0, 5);
        }
        if (data.arrival_iso) {
            const [arrDate, arrTime] = data.arrival_iso.split('T');
            document.getElementById('arrival-date').value = arrDate;
            document.getElementById('arrival-time').value = arrTime.slice(0, 5);
        }

        // Очистка карты
        markers.forEach(m => m.remove()); markers.length = 0;
        if (map.getLayer('route-line')) map.removeLayer('route-line');
        if (map.getSource('route-data')) map.removeSource('route-data');

        // Отрисовка маршрута
        const routeCoordinates = data.route.map(coord => [coord[0], coord[1]]);
        const routeColor = data.has_barriers ? '#e74c3c' : '#3498db';

        map.addSource('route-data', {
            'type': 'geojson',
            'data': { 'type': 'Feature', 'properties': {}, 'geometry': { 'type': 'LineString', 'coordinates': routeCoordinates } }
        });
        map.addLayer({
            'id': 'route-line', 'type': 'line', 'source': 'route-data',
            'layout': { 'line-join': 'round', 'line-cap': 'round' },
            'paint': { 'line-color': routeColor, 'line-width': 6, 'line-opacity': 0.9 }
        });

        // Маркеры
        data.waypoints.forEach((wp, i) => {
            const isStart = i === 0, isEnd = i === data.waypoints.length - 1;
            const [emoji, label, color] = isStart ? ['📍', 'Старт', 'green'] : isEnd ? ['🏁', 'Финиш', 'red'] : ['🔹', `Точка ${i}`, 'orange'];
            markers.push(createCustomMarker(wp.lon, wp.lat, emoji, color, `${label}<br>${wp.address}`));
        });

        // Масштабирование
        const bounds = new maplibregl.LngLatBounds();
        routeCoordinates.forEach(coord => bounds.extend(coord));
        map.fitBounds(bounds, { padding: { top: 50, bottom: 50, left: 50, right: 50 }, duration: 1000 });

        // 📊 ОБНОВЛЕНИЕ ИТОГОВЫХ ДАННЫХ
        document.getElementById('dist').textContent = data.distance_km + ' км';
        document.getElementById('time').textContent = data.time_min + ' мин';
        
        // Показываем/скрываем карточки времени
        const depCard = document.getElementById('dep-card');
        const arrCard = document.getElementById('arr-card');
        
        if (data.departure_time_display) {
            depCard.style.display = 'block';
            document.getElementById('dep-display').textContent = data.departure_time_display;
        } else { depCard.style.display = 'none'; }
        
        if (data.arrival_time_display) {
            arrCard.style.display = 'block';
            document.getElementById('arr-display').textContent = data.arrival_time_display;
        } else { arrCard.style.display = 'none'; }

        // Предупреждение о несовпадении времени
        if (data.time_mismatch) {
            timeMismatchWarning.classList.add('show');
        }

        if (data.has_barriers) warning.classList.add('show');
        else warning.classList.remove('show');

        // Сегменты
        const segmentsContainer = document.getElementById('segments-container');
        if (data.segments?.length > 1) {
            segmentsContainer.innerHTML = '<hr style="margin:10px 0;"><strong>Сегменты:</strong>' +
                data.segments.map((s,i) => `<div class="result-row" style="font-size:13px;margin-top:5px;">
                    <span>${i+1}. ${s.from.split(',').slice(-2).join(',').trim()} → ${s.to.split(',').slice(-2).join(',').trim()}</span>
                    <span>${s.distance_km} км / ${s.time_min} мин</span></div>`).join('');
        } else segmentsContainer.innerHTML = '';

        results.classList.add('active');

    } catch (err) {
        console.error('💥 Ошибка:', err);
        showToast('Ошибка сервера', 'error');
    } finally {
        btn.disabled = false;
        loading.style.display = 'none';
    }
}