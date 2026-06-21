/**
 * API client для подключения к backend серверу
 * 
 * Стратегия:
 * - В Codespaces/Vite dev: используем относительный URL → работает Vite proxy
 * - В production (GitHub Pages): используем абсолютный URL на Render
 */
function getApiUrl() {
    const hostname = window.location.hostname;
    
    // ✅ Codespaces — используем относительный URL для Vite proxy
    if (hostname.endsWith('.app.github.dev')) {
        return '';  // ← ПУСТАЯ СТРОКА = относительный URL
    }
    
    // ✅ Локальная разработка — тоже относительный URL (для Vite proxy)
    if (hostname === 'localhost' || hostname === '127.0.0.1') {
        return '';  // ← Vite proxy сам перенаправит на localhost:8000
    }
    
    // ✅ Production (GitHub Pages) — абсолютный URL на Render
    return 'https://voyx-main.onrender.com';  // ← ИСПРАВЛЕНО
}

export const API_BASE_URL = getApiUrl();

/**
 * Рассчитывает маршрут между двумя адресами
 * @param {string} startAddress - Адрес старта
 * @param {string} endAddress - Адрес финиша
 * @param {string[]} waypoints - Промежуточные точки (опционально)
 * @param {object} options - Дополнительные опции
 * @returns {Promise<object>} Данные маршрута
 */
export async function calculateRoute(startAddress, endAddress, waypoints = [], options = {}) {
    const response = await fetch(`${API_BASE_URL}/api/route`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            start_address: startAddress,
            end_address: endAddress,
            waypoints: waypoints,
            use_traffic: options.use_traffic ?? true,
            departure_time: options.departure_time || null,
            arrival_time: options.arrival_time || null,
            optimize_order: options.optimize_order ?? false
        })
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Ошибка сервера: ${response.status} - ${errorText}`);
    }

    const res = await response.json();
    return res.data;
}

/**
 * Рассчитывает маршрут через несколько точек
 * @param {string[]} waypoints - Список адресов [старт, точка1, ..., финиш]
 * @param {object} options - Дополнительные опции
 * @returns {Promise<object>} Данные маршрута
 */
export async function calculateMultiPointRoute(waypoints, options = {}) {
    if (!waypoints || waypoints.length < 2) {
        throw new Error('Минимум 2 точки для маршрута');
    }

    // ✅ ИСПРАВЛЕНО: используем /api/route (не /api/route/multi)
    // Передаём все точки через waypoints: [старт, ...промежуточные, финиш]
    const startAddress = waypoints[0];
    const endAddress = waypoints[waypoints.length - 1];
    const middleWaypoints = waypoints.slice(1, -1);

    const response = await fetch(`${API_BASE_URL}/api/route`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            start_address: startAddress,
            end_address: endAddress,
            waypoints: middleWaypoints,
            use_traffic: options.use_traffic ?? true,
            departure_time: options.departure_time || null,
            arrival_time: options.arrival_time || null,
            optimize_order: options.optimize_order ?? false
        })
    });

    if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`Ошибка сервера: ${response.status} - ${errorText}`);
    }

    const res = await response.json();
    return res.data;
}

/**
 * Получает статус сервера
 * @returns {Promise<boolean>} true если сервер доступен
 */
export async function checkServerStatus() {
    try {
        const response = await fetch(`${API_BASE_URL}/health`, { method: 'GET' });
        return response.ok;
    } catch (err) {
        return false;
    }
}