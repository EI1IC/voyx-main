/**
 * API client для подключения к backend серверу
 * Backend API: http://localhost:8000/api/route
 */

const API_BASE_URL = process.env.API_URL || 'http://localhost:8000';

/**
 * Рассчитывает маршрут между двумя адресами
 * @param {string} startAddress - Адрес старта
 * @param {string} endAddress - Адрес финиша
 * @param {string[]} waypoints - Промежуточные точки (опционально)
 * @returns {Promise<object>} Данные маршрута
 */
export async function calculateRoute(startAddress, endAddress, waypoints = []) {
    const response = await fetch(`${API_BASE_URL}/api/route`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            start_address: startAddress,
            end_address: endAddress,
            waypoints: waypoints
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
 * @returns {Promise<object>} Данные маршрута
 */
export async function calculateMultiPointRoute(waypoints) {
    if (!waypoints || waypoints.length < 2) {
        throw new Error('Минимум 2 точки для маршрута');
    }

    const response = await fetch(`${API_BASE_URL}/api/route/multi`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ waypoints })
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
        const response = await fetch(`${API_BASE_URL}/docs`, { method: 'HEAD' });
        return response.ok;
    } catch (err) {
        return false;
    }
}
