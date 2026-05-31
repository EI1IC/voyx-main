const map = L.map('map').setView([58.5967, 49.6074], 13);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    attribution: '&copy; <a href="https://osm.org/copyright">OSM</a>'
}).addTo(map);

let routeLayer, markers = [];

// Backend API URL (можно переопределить через переменные окружения)
const API_BASE_URL = window.API_BASE_URL || 'http://localhost:8000';

function addWaypoint() {
    const container = document.getElementById('waypoints-container');
    const div = document.createElement('div');
    div.className = 'form-group waypoint-group';
    
    const waypointNumber = document.querySelectorAll('.waypoint-input').length + 1;
    
    div.innerHTML = `
        <label>🔹 Точка ${waypointNumber}</label>
        <div style="display: flex; gap: 5px;">
            <input type="text" class="waypoint-input" placeholder="Адрес промежуточной точки" style="flex: 1;">
            <button onclick="removeWaypoint(this)" class="btn-remove" style="width: auto; padding: 8px 12px; background: #e74c3c;">✕</button>
        </div>
    `;
    container.appendChild(div);
    updateWaypointNumbers();
}

function removeWaypoint(button) {
    button.parentElement.parentElement.remove();
    updateWaypointNumbers();
}

function updateWaypointNumbers() {
    const waypoints = document.querySelectorAll('.waypoint-group');
    waypoints.forEach((group, index) => {
        const label = group.querySelector('label');
        if (label) {
            label.textContent = `🔹 Точка ${index + 1}`;
        }
    });
}

function getWaypoints() {
    const inputs = document.querySelectorAll('.waypoint-input');
    return Array.from(inputs).map(input => input.value).filter(v => v.trim());
}

async function calculateRoute() {
    const startAddr = document.getElementById('start').value;
    const endAddr = document.getElementById('end').value;
    const waypoints = getWaypoints();
    const btn = document.getElementById('btn');
    const loading = document.getElementById('loading');
    const results = document.getElementById('results');
    const warning = document.getElementById('barrier-warning');

    // Валидация
    if (!startAddr.trim() || !endAddr.trim()) {
        alert('Пожалуйста, заполните адреса старта и финиша');
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
            headers: {'Content-Type': 'application/json'},
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

        // Очистка старых слоев
        if (routeLayer) map.removeLayer(routeLayer);
        if (markers && Array.isArray(markers)) {
            markers.forEach(m => map.removeLayer(m));
        }
        markers = [];

        // ✅ Отрисовка маршрута
        // Leaflet принимает [lat, lon], а у нас в data.route [lon, lat]
        const routeCoords = data.route.map(coord => [coord[1], coord[0]]);
        
        const routeColor = data.has_barriers ? '#e74c3c' : '#3498db';
        routeLayer = L.polyline(routeCoords, {
            color: routeColor, 
            weight: 5,
            opacity: 0.8
        }).addTo(map);

        // ✅ Отрисовка маркеров точек
        data.waypoints.forEach((wp, i) => {
            const isStart = i === 0;
            const isEnd = i === data.waypoints.length - 1;
            const color = isStart ? 'green' : (isEnd ? 'red' : 'orange');
            const icon = isStart ? '📍' : (isEnd ? '🏁' : '🔹');
            
            const marker = L.marker([wp.lat, wp.lon])
                .addTo(map)
                .bindPopup(`<b>${icon} ${isStart ? 'Старт' : (isEnd ? 'Финиш' : 'Точка ' + i)}</b><br>${wp.address}`);
            markers.push(marker);
        });

        // ✅ Масштабирование карты под маршрут
        map.fitBounds(routeLayer.getBounds(), {padding: [50, 50]});

        // ✅ Обновление интерфейса с метриками
        document.getElementById('dist').textContent = data.distance_km + ' км';
        document.getElementById('time').textContent = data.time_min + ' мин';
        document.getElementById('points').textContent = data.waypoints.length;
        
        // Предупреждение о шлагбаумах
        if (data.has_barriers) {
            warning.classList.add('show');
        } else {
            warning.classList.remove('show');
        }

        // ✅ Отображение сегментов (если их больше 1)
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
        alert('Ошибка: ' + err.message);
    } finally {
        // Разблокировка интерфейса
        btn.disabled = false;
        loading.style.display = 'none';
    }
}
