import './style.css'
import maplibregl from 'maplibre-gl';
import Toastify from 'toastify-js';

const map = new maplibregl.Map({
    container: 'map',
    style: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
    center: [49.6074, 58.5967],
    zoom: 13,
    attributionControl: false
});
map.addControl(new maplibregl.NavigationControl(), 'top-right');
map.on('error', (e) => console.error('Ошибка загрузки карты:', e));

let markers = [];
const API_BASE_URL = "https://super-parakeet-5w9wx75xjvv3rwp-8000.app.github.dev";

function showToast(message, type = 'info') {
    const colors = { success: '#14752a', error: '#9e111f', warning: '#d5b721', info: '#062b6c' };
    Toastify({
        text: message, duration: 4000, gravity: "top", position: "center",
        style: { background: colors[type], color: "#fff", borderRadius: "10px", boxShadow: "0 4px 12px rgba(0,0,0,0.15)", fontWeight: "500", fontSize: "14px" },
        close: true, stopOnFocus: true,
    }).showToast();
}

function addWaypoint() {
    const container = document.getElementById('waypoints-container');
    const div = document.createElement('div');
    div.className = 'form-group waypoint-group';
    const num = document.querySelectorAll('.waypoint-input').length + 1;
    div.innerHTML = `<span class="waypoint-text">${num}</span><input type="text" class="waypoint-input" placeholder="Промежуточная точка"><button class="btn-remove remove-btn">✕</button>`;
    container.appendChild(div);
    updateWaypointNumbers();
}
function updateWaypointNumbers() {
    document.querySelectorAll('.waypoint-group').forEach((g, i) => {
        const span = g.querySelector('.waypoint-text');
        if (span) span.textContent = `${i + 1}`;
    });
}
function getWaypoints() {
    return Array.from(document.querySelectorAll('.waypoint-input')).map(i => i.value).filter(v => v.trim());
}

function createMarker(lng, lat, emoji, popupText) {
    const el = document.createElement('div');
    el.className = 'custom-marker';
    el.innerHTML = `<span style="font-size: 24px; filter: drop-shadow(0 2px 2px rgba(0,0,0,0.3));">${emoji}</span>`;
    return new maplibregl.Marker({ element: el, anchor: 'bottom' })
        .setLngLat([lng, lat])
        .setPopup(new maplibregl.Popup({ offset: 25 }).setHTML(`<b>${popupText}</b>`))
        .addTo(map);
}

// ==============================================================================
// 🕐 ИНИЦИАЛИЗАЦИЯ ВЫПАДАЮЩИХ СПИСКОВ ВРЕМЕНИ
// ==============================================================================
function initTimeSelects() {
    const pad = n => String(n).padStart(2, '0');
    
    // Заполняем часы (00-23)
    const hourSelects = [
        document.getElementById('departure-hour'),
        document.getElementById('arrival-hour')
    ];
    hourSelects.forEach(select => {
        if (!select) return;
        select.innerHTML = '<option value="">--</option>';
        for (let h = 0; h < 24; h++) {
            const opt = document.createElement('option');
            opt.value = pad(h);
            opt.textContent = pad(h);
            select.appendChild(opt);
        }
    });
    
    // Заполняем минуты (только 0, 15, 30, 45)
    const minuteSelects = [
        document.getElementById('departure-minute'),
        document.getElementById('arrival-minute')
    ];
    minuteSelects.forEach(select => {
        if (!select) return;
        select.innerHTML = '<option value="">--</option>';
        for (let m = 0; m < 60; m++) {
            const opt = document.createElement('option');
            opt.value = pad(m);
            opt.textContent = pad(m);
            select.appendChild(opt);
        }
    });
}

// Получить время из двух select как "HH:MM"
function getTimeFromSelects(prefix) {
    const h = document.getElementById(`${prefix}-hour`)?.value;
    const m = document.getElementById(`${prefix}-minute`)?.value;
    if (!h || !m) return null;
    return `${h}:${m}`;
}

// Установить время в два select из "HH:MM"
function setTimeToSelects(prefix, timeStr) {
    if (!timeStr) return;
    const [h, m] = timeStr.split(':');
    const hSelect = document.getElementById(`${prefix}-hour`);
    const mSelect = document.getElementById(`${prefix}-minute`);
    if (hSelect) hSelect.value = h;
    if (mSelect) mSelect.value = m;
}

// Округление минут до ближайших 15
function roundMinutesTo15(minutes) {
    return Math.round(minutes / 15) * 15;
}

document.addEventListener('DOMContentLoaded', () => {
    // Инициализируем выпадающие списки
    initTimeSelects();
    
    const toggle = document.getElementById('params-toggle');
    const panel = document.getElementById('params-panel');
    
    toggle?.addEventListener('click', () => {
        toggle.classList.toggle('open');
        panel.classList.toggle('open');
    });

    // ⏰ Кнопка "сейчас" — ставит текущее время
    document.getElementById('set-now')?.addEventListener('click', () => {
        const now = new Date();
        let h = now.getHours();
        let m = now.getMinutes();
        
        const pad = n => String(n).padStart(2, '0');
        setTimeToSelects('departure', `${pad(h)}:${pad(m)}`);
        document.getElementById('departure-date').value = now.toISOString().slice(0, 10);
    });

    // 🔄 Кнопка "сброс" — очищает поля прибытия
    document.getElementById('reset-arrival')?.addEventListener('click', () => {
        document.getElementById('arrival-hour').value = '';
        document.getElementById('arrival-minute').value = '';
        document.getElementById('arrival-date').value = '';
    });

    document.getElementById('calculate-btn')?.addEventListener('click', calculateRoute);
    document.getElementById('add-waypoint-btn')?.addEventListener('click', addWaypoint);
    document.getElementById('waypoints-container')?.addEventListener('click', (e) => {
        const btn = e.target.closest('.remove-btn');
        if (btn) { btn.closest('.waypoint-group').remove(); updateWaypointNumbers(); }
    });
});

// 🕰️ Преобразование в ISO с поясом Москвы (+03:00)
function combineDateTime(timeVal, dateVal) {
    if (!timeVal || !dateVal) return null;
    const [h, m] = timeVal.split(':').map(Number);
    const [y, mo, d] = dateVal.split('-').map(Number);
    
    const pad = n => String(n).padStart(2, '0');
    return `${y}-${pad(mo)}-${pad(d)}T${pad(h)}:${pad(m)}:00+03:00`;
}

async function calculateRoute() {
    const startAddr = document.getElementById('start').value;
    const endAddr = document.getElementById('end').value;
    const waypoints = getWaypoints();
    
    if (!startAddr.trim() || !endAddr.trim()) {
        showToast('Пожалуйста, заполните адреса старта и финиша', 'warning');
        return;
    }

    // ✅ Панель параметров НЕ закрывается

    const btn = document.getElementById('calculate-btn');
    const loading = document.getElementById('loading');
    const results = document.getElementById('results');
    btn.disabled = true;
    loading.style.display = 'block';
    results.classList.remove('active');
    document.getElementById('barrier-warning').classList.remove('show');
    document.getElementById('time-mismatch-warning').classList.remove('show');

    // Читаем значения из select
    const depTime = getTimeFromSelects('departure');
    const depDate = document.getElementById('departure-date').value;
    const arrTime = getTimeFromSelects('arrival');
    const arrDate = document.getElementById('arrival-date').value;
    const useTraffic = document.getElementById('use-traffic').checked;
    const optimizeOrder = document.getElementById('optimize-order').checked;

    const payload = {
        start_address: startAddr,
        end_address: endAddr,
        waypoints: waypoints,
        use_traffic: useTraffic,
        optimize_order: optimizeOrder
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
            const errText = await response.text();
            throw new Error(`Ошибка сервера: ${response.status} - ${errText}`);
        }

        const res = await response.json();
        const data = res.data;
        if (!data?.route) throw new Error('Невалидный ответ');

        if (data.order_optimized) {
            showToast('Маршрут оптимизирован: порядок точек изменен', 'success');
            
            const container = document.getElementById('waypoints-container');
            container.innerHTML = ''; // Очищаем старые поля
            
            // data.waypoints содержит [Старт, Точка1, Точка2, ..., Финиш]
            // Берем только промежуточные точки (от 1 до length - 2)
            for (let i = 1; i < data.waypoints.length - 1; i++) {
                const wp = data.waypoints[i];
                const div = document.createElement('div');
                div.className = 'form-group waypoint-group';
                div.innerHTML = `
                    <span class="waypoint-text">${i}</span>
                    <input type="text" class="waypoint-input" value="${wp.address}" placeholder="Промежуточная точка">
                    <button class="btn-remove remove-btn">✕</button>
                `;
                container.appendChild(div);
            }
            updateWaypointNumbers(); // Обновляем нумерацию на всякий случай
        }
        // 🕰️ Автозаполнение ТОЛЬКО поля отправления (если бэкенд его рассчитал)
        if (data.departure_iso) {
            const dt = new Date(data.departure_iso);
            const pad = n => String(n).padStart(2, '0');
            document.getElementById('departure-date').value = `${dt.getFullYear()}-${pad(dt.getMonth()+1)}-${pad(dt.getDate())}`;
            setTimeToSelects('departure', `${pad(dt.getHours())}:${pad(dt.getMinutes())}`);
        }

        // 🗺️ Очистка и отрисовка
        markers.forEach(m => m.remove()); markers = [];
        if (map.getLayer('route-line')) map.removeLayer('route-line');
        if (map.getSource('route-data')) map.removeSource('route-data');

        const routeCoords = data.route.map(c => [c[0], c[1]]);
        map.addSource('route-data', {
            type: 'geojson',
            data: { type: 'Feature', geometry: { type: 'LineString', coordinates: routeCoords } }
        });
        map.addLayer({
            id: 'route-line', type: 'line', source: 'route-data',
            layout: { 'line-join': 'round', 'line-cap': 'round' },
            paint: { 'line-color': data.has_barriers ? '#e74c3c' : '#3498db', 'line-width': 6, 'line-opacity': 0.9 }
        });

        data.waypoints.forEach((wp, i) => {
            const isStart = i === 0, isEnd = i === data.waypoints.length - 1;
            const [emoji, label] = isStart ? ['📍', 'Старт'] : isEnd ? ['🏁', 'Финиш'] : ['🔹', `Точка ${i}`];
            markers.push(createMarker(wp.lon, wp.lat, emoji, `${label}<br>${wp.address}`));
        });

        const bounds = new maplibregl.LngLatBounds();
        routeCoords.forEach(c => bounds.extend(c));
        map.fitBounds(bounds, { padding: 50, duration: 1000 });

        // 📊 Обновление результатов
        document.getElementById('dist').textContent = data.distance_km + ' км';
        document.getElementById('time').textContent = data.time_min + ' мин';
        
        const depCard = document.getElementById('dep-card');
        const arrCard = document.getElementById('arr-card');
        if (data.departure_time_display) {
            depCard.style.display = 'block';
            document.getElementById('dep-display').textContent = data.departure_time_display;
        } else depCard.style.display = 'none';
        
        if (data.arrival_time_display) {
            arrCard.style.display = 'block';
            document.getElementById('arr-display').textContent = data.arrival_time_display;
        } else arrCard.style.display = 'none';

        if (data.time_mismatch) document.getElementById('time-mismatch-warning').classList.add('show');
        if (data.has_barriers) document.getElementById('barrier-warning').classList.add('show');
        
        results.classList.add('active');

    } catch (err) {
        console.error('💥 Ошибка:', err);
        showToast('Ошибка сервера: ' + err.message, 'error');
    } finally {
        btn.disabled = false;
        loading.style.display = 'none';
    }
}