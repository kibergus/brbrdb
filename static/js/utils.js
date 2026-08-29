/*
 * Copyright 2026 Alexey Guseynov (kibergus). All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 * ==============================================================================
 */

/**
 * Shared utility functions for the karting analysis dashboard.
 * This file can be used as an ES module or included as a script.
 */

/**
 * Calculates the median of an array of numbers.
 * @param {number[]} arr - The array of numbers.
 * @returns {number|null} The median or null if the array is empty.
 */
export const getMedian = (arr) => {
    if (arr.length === 0) return null;
    const sorted = [...arr].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 !== 0 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

/**
 * Parses a lap time string (e.g., "1:04.532" or "64.532") into seconds.
 * @param {string|number} timeStr - The time string to parse.
 * @returns {number} The time in seconds.
 */
export function parseLapTime(timeStr) {
    if (!timeStr) return 999999;
    if (typeof timeStr !== 'string') timeStr = String(timeStr);
    const parts = timeStr.split(':');
    if (parts.length === 2) {
        return parseFloat(parts[0]) * 60 + parseFloat(parts[1]);
    }
    return parseFloat(parts[0]);
}

/**
 * Formats a time in seconds into a string (e.g., 64.532 -> "1:04.532").
 * @param {number} seconds - The time in seconds.
 * @returns {string} The formatted time string.
 */
export function formatLapTime(seconds) {
    if (seconds === Infinity || seconds === 0 || isNaN(seconds)) return '-';
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    if (mins > 0) {
        return `${mins}:${secs.toFixed(3).padStart(6, '0')}`;
    }
    return secs.toFixed(3);
}

/**
 * Calculates the percentile of an array of numbers.
 * @param {number[]} arr - The array of numbers.
 * @param {number} percentile - The percentile to calculate (0 to 100).
 * @returns {number|null} The percentile value or null if the array is empty.
 */
export const getPercentile = (arr, percentile) => {
    if (arr.length === 0) return null;
    const sorted = [...arr].sort((a, b) => a - b);
    const index = (sorted.length - 1) * (percentile / 100);
    const lower = Math.floor(index);
    const upper = Math.ceil(index);
    const weight = index - lower;
    if (upper >= sorted.length) return sorted[sorted.length - 1];
    return sorted[lower] * (1 - weight) + sorted[upper] * weight;
};

/**
 * Computes tick values and formatted labels for steering angles.
 * Labels use absolute values with 'R' for negative (right) and 'L' for positive (left).
 * @param {number} min - Minimum steering angle.
 * @param {number} max - Maximum steering angle.
 * @returns {{ tickvals: number[], ticktext: string[] }}
 */
export function getSteeringTicks(min, max) {
    if (!isFinite(min) || !isFinite(max)) {
        min = -45;
        max = 45;
    }
    const span = Math.max(10, max - min);
    let step = 10;
    if (span <= 25) step = 5;
    else if (span <= 60) step = 10;
    else if (span <= 120) step = 15;
    else if (span <= 200) step = 20;
    else if (span <= 350) step = 30;
    else step = 45;

    const start = Math.floor(min / step) * step;
    const end = Math.ceil(max / step) * step;

    const tickvals = [];
    const ticktext = [];
    for (let v = start; v <= end; v += step) {
        tickvals.push(v);
        const absVal = Math.abs(Math.round(v * 10) / 10);
        if (absVal === 0 || Math.abs(v) < 1e-6) {
            ticktext.push('0');
        } else if (v < 0) {
            ticktext.push(`${absVal} R`);
        } else {
            ticktext.push(`${absVal} L`);
        }
    }
    return { tickvals, ticktext };
}

/**
 * Helper to get max zoom from Google Map instance and active map type.
 */
function getMapMaxZoom(map, fallback = 21) {
    if (!map) return fallback;
    const mapMax = (typeof map.get === 'function') ? map.get('maxZoom') : null;
    if (typeof mapMax === 'number' && isFinite(mapMax)) return mapMax;
    const typeId = (typeof map.getMapTypeId === 'function') ? map.getMapTypeId() : null;
    if (typeId && map.mapTypes && typeof map.mapTypes.get === 'function') {
        const mt = map.mapTypes.get(typeId);
        if (mt && typeof mt.maxZoom === 'number' && isFinite(mt.maxZoom)) return mt.maxZoom;
    }
    return fallback;
}

/**
 * Helper to get min zoom from Google Map instance and active map type.
 */
function getMapMinZoom(map, fallback = 3) {
    if (!map) return fallback;
    const mapMin = (typeof map.get === 'function') ? map.get('minZoom') : null;
    if (typeof mapMin === 'number' && isFinite(mapMin)) return mapMin;
    const typeId = (typeof map.getMapTypeId === 'function') ? map.getMapTypeId() : null;
    if (typeId && map.mapTypes && typeof map.mapTypes.get === 'function') {
        const mt = map.mapTypes.get(typeId);
        if (mt && typeof mt.minZoom === 'number' && isFinite(mt.minZoom)) return mt.minZoom;
    }
    return fallback;
}

/**
 * Attaches smooth animated mouse wheel zooming to a Google Map container.
 * Google Maps default wheel zoom jumps abruptly by 1.0 zoom per notch.
 * This handler fractional-zooms around the cursor position with requestAnimationFrame easing
 * and 2x smoother sensitivity (~0.5 zoom per notch).
 *
 * @param {google.maps.Map} map - Google Maps instance
 * @param {HTMLElement} container - DOM container holding the map
 * @param {Object} [options] - Configuration options (minZoom, maxZoom, sensitivity)
 * @returns {Function} Cleanup function to remove event listeners and cancel animations
 */
export function attachSmoothWheelZoom(map, container, options = {}) {
    if (!map || !container || typeof container.addEventListener !== 'function') return () => {};

    let effectiveMaxZoom = options.maxZoom !== undefined ? options.maxZoom : getMapMaxZoom(map, 21);
    let effectiveMinZoom = options.minZoom !== undefined ? options.minZoom : getMapMinZoom(map, 3);
    const sensitivity = options.sensitivity !== undefined ? options.sensitivity : 0.5;

    let targetZoom = null;
    let animZoom = null;
    let anchorWorldPoint = null;
    let anchorScreenX = 0;
    let anchorScreenY = 0;
    let rafId = null;

    const cancelAnimation = () => {
        if (rafId !== null) {
            if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(rafId);
            rafId = null;
        }
        targetZoom = null;
        animZoom = null;
        anchorWorldPoint = null;
    };

    const animate = () => {
        if (targetZoom === null || animZoom === null) {
            rafId = null;
            return;
        }

        const isAsyncRaf = typeof requestAnimationFrame === 'function';
        const diff = targetZoom - animZoom;
        if (!isAsyncRaf || Math.abs(diff) < 0.005) {
            animZoom = targetZoom;
        } else {
            animZoom += diff * 0.25;
        }

        // Apply zoom
        if (typeof map.setZoom === 'function') {
            map.setZoom(animZoom);
        }

        const projection = (typeof map.getProjection === 'function') ? map.getProjection() : null;
        const rect = (typeof container.getBoundingClientRect === 'function') ? container.getBoundingClientRect() : null;

        if (projection && rect && rect.width > 0 && rect.height > 0 && anchorWorldPoint) {
            const width = rect.width;
            const height = rect.height;
            const offsetX = anchorScreenX - width / 2;
            const offsetY = anchorScreenY - height / 2;

            const scale = Math.pow(2, -animZoom);
            const newCenterX = anchorWorldPoint.x - offsetX * scale;
            const newCenterY = anchorWorldPoint.y - offsetY * scale;

            const PointClass = (typeof google !== 'undefined' && google.maps && google.maps.Point)
                ? google.maps.Point
                : function (x, y) { this.x = x; this.y = y; };

            const newCenterLatLng = projection.fromPointToLatLng(new PointClass(newCenterX, newCenterY));
            if (newCenterLatLng && typeof map.setCenter === 'function') {
                map.setCenter(newCenterLatLng);
            }
        }

        if (Math.abs(targetZoom - animZoom) < 0.001) {
            const finalActualZoom = (typeof map.getZoom === 'function') ? map.getZoom() : null;
            if (typeof finalActualZoom === 'number' && isFinite(finalActualZoom)) {
                if (targetZoom > finalActualZoom + 0.05) {
                    effectiveMaxZoom = finalActualZoom;
                } else if (targetZoom < finalActualZoom - 0.05) {
                    effectiveMinZoom = finalActualZoom;
                }
            }
            cancelAnimation();
        } else if (isAsyncRaf) {
            rafId = requestAnimationFrame(animate);
        } else {
            cancelAnimation();
        }
    };

    const handleWheel = (event) => {
        if (!event) return;
        if (typeof event.preventDefault === 'function') event.preventDefault();
        if (typeof event.stopPropagation === 'function') event.stopPropagation();

        const currentMapZoom = (typeof map.getZoom === 'function') ? map.getZoom() : null;
        if (currentMapZoom === null || currentMapZoom === undefined || isNaN(currentMapZoom)) return;

        let dy = (event.deltaY !== undefined) ? event.deltaY : 0;
        if (event.deltaMode === 1) {
            dy *= 33.33; // DOM_DELTA_LINE to pixels
        } else if (event.deltaMode === 2) {
            dy *= 800;   // DOM_DELTA_PAGE to pixels
        }

        const deltaZoom = -dy * (sensitivity / 120);

        // If at max zoom and scrolling in, or at min zoom and scrolling out, do nothing
        if (deltaZoom > 0 && currentMapZoom >= effectiveMaxZoom - 1e-4) {
            return;
        }
        if (deltaZoom < 0 && currentMapZoom <= effectiveMinZoom + 1e-4) {
            return;
        }

        const currentBaseZoom = (targetZoom !== null) ? targetZoom : currentMapZoom;
        const nextTargetZoom = Math.min(effectiveMaxZoom, Math.max(effectiveMinZoom, currentBaseZoom + deltaZoom));

        if (Math.abs(nextTargetZoom - currentBaseZoom) < 1e-4) {
            return;
        }

        const rect = (typeof container.getBoundingClientRect === 'function') ? container.getBoundingClientRect() : null;
        const width = rect && rect.width ? rect.width : (container.clientWidth || 800);
        const height = rect && rect.height ? rect.height : (container.clientHeight || 600);

        anchorScreenX = (event.clientX !== undefined && rect) ? event.clientX - rect.left : width / 2;
        anchorScreenY = (event.clientY !== undefined && rect) ? event.clientY - rect.top : height / 2;

        const projection = (typeof map.getProjection === 'function') ? map.getProjection() : null;
        const currentCenter = (typeof map.getCenter === 'function') ? map.getCenter() : null;

        if (projection && currentCenter) {
            const centerPt = projection.fromLatLngToPoint(currentCenter);
            if (centerPt && typeof centerPt.x === 'number' && typeof centerPt.y === 'number') {
                const activeZoom = (animZoom !== null) ? animZoom : currentMapZoom;
                const activeScale = Math.pow(2, -activeZoom);
                const offsetX = anchorScreenX - width / 2;
                const offsetY = anchorScreenY - height / 2;
                anchorWorldPoint = {
                    x: centerPt.x + offsetX * activeScale,
                    y: centerPt.y + offsetY * activeScale
                };
            }
        }

        targetZoom = nextTargetZoom;
        if (animZoom === null) {
            animZoom = currentMapZoom;
        }

        if (rafId === null) {
            if (typeof requestAnimationFrame === 'function') {
                rafId = requestAnimationFrame(animate);
            } else {
                animate();
            }
        }
    };

    const handlePointerDown = () => {
        cancelAnimation();
    };

    let mapTypeListener = null;
    if (typeof map.addListener === 'function') {
        mapTypeListener = map.addListener('maptypeid_changed', () => {
            effectiveMaxZoom = options.maxZoom !== undefined ? options.maxZoom : getMapMaxZoom(map, 21);
            effectiveMinZoom = options.minZoom !== undefined ? options.minZoom : getMapMinZoom(map, 3);
        });
    }

    container.addEventListener('wheel', handleWheel, { passive: false, capture: true });
    container.addEventListener('mousedown', handlePointerDown, { passive: true });
    container.addEventListener('touchstart', handlePointerDown, { passive: true });

    return () => {
        cancelAnimation();
        container.removeEventListener('wheel', handleWheel, { capture: true });
        container.removeEventListener('mousedown', handlePointerDown);
        container.removeEventListener('touchstart', handlePointerDown);
        if (mapTypeListener && typeof mapTypeListener.remove === 'function') {
            mapTypeListener.remove();
        }
    };
}

// Attach to window if running in a browser environment to make functions available globally
if (typeof window !== 'undefined') {
    window.getMedian = getMedian;
    window.getPercentile = getPercentile;
    window.parseLapTime = parseLapTime;
    window.formatLapTime = formatLapTime;
    window.getSteeringTicks = getSteeringTicks;
    window.attachSmoothWheelZoom = attachSmoothWheelZoom;
}

