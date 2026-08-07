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
 * Telemetry computation and animation logic.
 */
import { state } from './state.js';
import { parseLapTime } from '../utils.js';

export function findSegmentIndex(points, targetDist) {
    if (!points || points.length < 2) return -1;
    let low = 0;
    let high = points.length - 2;
    while (low <= high) {
        let mid = Math.floor((low + high) / 2);
        if (points[mid].dist <= targetDist && points[mid + 1].dist >= targetDist) {
            return mid;
        } else if (points[mid].dist < targetDist) {
            low = mid + 1;
        } else {
            high = mid - 1;
        }
    }
    return -1;
}

export function getPointAtDistance(lap, distance, targetKeys = null) {
    if (!lap.points || lap.points.length === 0) return null;

    let idx = findSegmentIndex(lap.points, distance);
    let p1, p2;

    if (idx !== -1) {
        p1 = lap.points[idx];
        p2 = lap.points[idx + 1];
    } else {
        if (distance <= lap.points[0].dist) return lap.points[0];
        if (distance >= lap.points[lap.points.length - 1].dist) return lap.points[lap.points.length - 1];
        return null;
    }

    const fraction = (distance - p1.dist) / (p2.dist - p1.dist || 0.0001);
    const result = { dist: distance };
    if (targetKeys) {
        if (Array.isArray(targetKeys)) {
            for (let i = 0; i < targetKeys.length; i++) {
                const key = targetKeys[i];
                if (typeof p1[key] === 'number') {
                    result[key] = p1[key] + fraction * (p2[key] - p1[key]);
                }
            }
        } else {
            const key = targetKeys;
            if (typeof p1[key] === 'number') {
                result[key] = p1[key] + fraction * (p2[key] - p1[key]);
            }
        }
    } else {
        for (let key in p1) {
            if (typeof p1[key] === 'number' && key !== 'dist') {
                result[key] = p1[key] + fraction * (p2[key] - p1[key]);
            }
        }
    }
    return result;
}

export function getPointsAtDistancesMonotonic(lap, distances, targetKeys = null) {
    if (!lap.points || lap.points.length === 0) return [];

    const results = new Array(distances.length);
    let pointsIdx = 0;
    const pointsLen = lap.points.length;

    for (let i = 0; i < distances.length; i++) {
        const distance = distances[i];

        let idx = -1;
        while (pointsIdx < pointsLen - 1) {
            if (lap.points[pointsIdx].dist <= distance && lap.points[pointsIdx + 1].dist >= distance) {
                idx = pointsIdx;
                break;
            } else if (lap.points[pointsIdx].dist < distance) {
                pointsIdx++;
            } else {
                break;
            }
        }

        if (idx === -1) {
            if (distance <= lap.points[0].dist) {
                results[i] = lap.points[0];
                continue;
            }
            if (distance >= lap.points[pointsLen - 1].dist) {
                results[i] = lap.points[pointsLen - 1];
                continue;
            }
            idx = findSegmentIndex(lap.points, distance);
            if (idx !== -1) {
                pointsIdx = idx;
            }
        }

        if (idx !== -1) {
            const p1 = lap.points[idx];
            const p2 = lap.points[idx + 1];
            const fraction = (distance - p1.dist) / (p2.dist - p1.dist || 0.0001);
            const result = { dist: distance };
            if (targetKeys) {
                if (Array.isArray(targetKeys)) {
                    for (let k = 0; k < targetKeys.length; k++) {
                        const key = targetKeys[k];
                        if (typeof p1[key] === 'number') {
                            result[key] = p1[key] + fraction * (p2[key] - p1[key]);
                        }
                    }
                } else {
                    const key = targetKeys;
                    if (typeof p1[key] === 'number') {
                        result[key] = p1[key] + fraction * (p2[key] - p1[key]);
                    }
                }
            } else {
                for (let key in p1) {
                    if (typeof p1[key] === 'number' && key !== 'dist') {
                        result[key] = p1[key] + fraction * (p2[key] - p1[key]);
                    }
                }
            }
            results[i] = result;
        } else {
            results[i] = null;
        }
    }
    return results;
}

export function getSpeedAtDistance(lap, distance) {
    const p = getPointAtDistance(lap, distance, 'speed');
    return p ? p.speed : null;
}

function computePointHeading(p1, p2) {
    if (!p1 || !p2 || p1.lat === undefined || p1.lat === null || p1.lng === undefined || p1.lng === null || p2.lat === undefined || p2.lat === null || p2.lng === undefined || p2.lng === null) return 0;
    const lat1 = p1.lat * Math.PI / 180;
    const lat2 = p2.lat * Math.PI / 180;
    const dLng = (p2.lng - p1.lng) * Math.PI / 180;
    const y = Math.sin(dLng) * Math.cos(lat2);
    const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLng);
    return Math.atan2(y, x) * 180 / Math.PI;
}

export function precalculateLapData(lap) {
    lap.brakingPoints = [];
    if (!lap.points) return;

    const n = lap.points.length;
    for (let i = 0; i < n; i++) {
        const p = lap.points[i];
        let acc = 0;
        if (n > 1) {
            if (i > 0 && i < n - 1) {
                const pPrev = lap.points[i - 1];
                const pNext = lap.points[i + 1];
                const dt = (pNext.time || 0) - (pPrev.time || 0);
                if (dt > 0) {
                    const vPrev = (pPrev.speed || 0) / 3.6;
                    const vNext = (pNext.speed || 0) / 3.6;
                    acc = (vNext - vPrev) / dt;
                }
            } else if (i === 0) {
                const pNext = lap.points[1];
                const dt = (pNext.time || 0) - (p.time || 0);
                if (dt > 0) {
                    const vCurr = (p.speed || 0) / 3.6;
                    const vNext = (pNext.speed || 0) / 3.6;
                    acc = (vNext - vCurr) / dt;
                }
            } else if (i === n - 1) {
                const pPrev = lap.points[n - 2];
                const dt = (p.time || 0) - (pPrev.time || 0);
                if (dt > 0) {
                    const vPrev = (pPrev.speed || 0) / 3.6;
                    const vCurr = (p.speed || 0) / 3.6;
                    acc = (vCurr - vPrev) / dt;
                }
            }
        }
        p.acceleration = acc;

        if (p.gx === undefined || p.gx === null) {
            p.gx = acc / 9.81;
        }
        if (p.gy === undefined || p.gy === null) {
            let gy = 0;
            if (n > 2 && i > 0 && i < n - 1) {
                const pPrev = lap.points[i - 1];
                const pNext = lap.points[i + 1];
                const dt = (pNext.time || 0) - (pPrev.time || 0);
                if (dt > 0 && pPrev.lat !== undefined && pNext.lat !== undefined) {
                    const h1 = computePointHeading(pPrev, p);
                    const h2 = computePointHeading(p, pNext);
                    let dh = h2 - h1;
                    while (dh > 180) dh -= 360;
                    while (dh < -180) dh += 360;
                    const omega = (dh * Math.PI / 180) / dt;
                    const v = (p.speed || 0) / 3.6;
                    const aLat = v * omega;
                    gy = aLat / 9.81;
                }
            }
            p.gy = gy;
        }
    }

    for (let i = 1; i < lap.points.length; i++) {
        const pPrev = lap.points[i - 1];
        const pCurr = lap.points[i];
        if (pCurr.brake > 5 && pPrev.brake <= 5) {
            lap.brakingPoints.push(pCurr.dist);
        }
    }
}

export function getBrakingPointsInRange(lap, centerDist, range) {
    if (!lap.points || !state.trackData) return [];
    if (!lap.brakingPoints) precalculateLapData(lap);
    
    const lapLength = state.trackData.lap_length;
    return lap.brakingPoints.filter(bp => {
        let distDiff = Math.abs(bp - centerDist);
        if (distDiff > lapLength / 2) {
            distDiff = lapLength - distDiff;
        }
        return distDiff <= range;
    });
}

export function getSectorTime(lap, sectorIdx) {
    return (lap.sector_times && lap.sector_times[sectorIdx] !== undefined) ? lap.sector_times[sectorIdx] : null;
}

export function getTurnTime(lap, turnIdx) {
    return (lap.turn_times && lap.turn_times[turnIdx] !== undefined) ? lap.turn_times[turnIdx] : null;
}

// Animation loop
export function animateSlider(timestamp) {
    if (!state.isPlaying) {
        state.animationFrameId = null;
        return;
    }
    
    if (state.lastTimestamp === null) {
        state.lastTimestamp = timestamp;
        state.animationFrameId = requestAnimationFrame(animateSlider);
        return;
    }
    
    const dt = (timestamp - state.lastTimestamp) / 1000;
    state.lastTimestamp = timestamp;

    const fastestLap = state.fastestSelectedLap;
    if (fastestLap && state.trackData && state.trackData.lap_length) {
        const slider = document.getElementById('distance-slider');
        const display = document.getElementById('distance-display');

        const speedKmh = getSpeedAtDistance(fastestLap, state.playbackDistance);
        const speedMs = (speedKmh || 0) / 3.6 * state.playbackSpeed;

        state.playbackDistance += speedMs * dt;
        if (state.playbackDistance >= state.trackData.lap_length) {
            state.playbackDistance = state.playbackDistance % state.trackData.lap_length;
        }
        
        if (state.playbackDistance < 0) state.playbackDistance = 0;

        if (slider) slider.value = state.playbackDistance;
        if (display) display.textContent = Math.round(state.playbackDistance) + 'm';
        
        // Import updateDistanceMarker dynamically to avoid circular dependency
        import('./map.js').then(m => m.updateDistanceMarker(state.playbackDistance));
    }

    state.animationFrameId = requestAnimationFrame(animateSlider);
}

export function stepDistance(delta) {
    if (!state.trackData || !state.trackData.lap_length) return;
    const slider = document.getElementById('distance-slider');
    const display = document.getElementById('distance-display');
    if (!slider) return;

    // Import togglePlay dynamically
    import('./lap_selection.js').then(ui => {
        if (state.isPlaying) ui.togglePlay();

        let newVal = state.playbackDistance + delta;
        while (newVal < 0) newVal += state.trackData.lap_length;
        while (newVal >= state.trackData.lap_length) newVal -= state.trackData.lap_length;

        state.playbackDistance = newVal;
        slider.value = newVal;
        if (display) display.textContent = Math.round(newVal) + 'm';

        import('./map.js').then(m => m.updateDistanceMarker(newVal));
    });
}

/**
 * Finds the minimum speed achieved within a specific track distance range.
 * Handles lap wrap-around (e.g., if startDist > endDist).
 */
function findFirstIndexGe(points, targetDist) {
    let low = 0;
    let high = points.length - 1;
    let result = points.length;
    while (low <= high) {
        let mid = Math.floor((low + high) / 2);
        if (points[mid].dist >= targetDist) {
            result = mid;
            high = mid - 1;
        } else {
            low = mid + 1;
        }
    }
    return result;
}

export function getMinSpeedInRange(lap, startDist, endDist, fallbackApex) {
    if (!lap.points || lap.points.length === 0) return null;

    let start = startDist;
    let end = endDist;

    if (start === undefined || start === null || end === undefined || end === null) {
        if (fallbackApex !== undefined && fallbackApex !== null) {
            start = fallbackApex - 25;
            end = fallbackApex + 25;
        } else {
            return null;
        }
    }

    const points = lap.points;
    const lapLength = (state.trackData && state.trackData.lap_length) || (points.length > 0 ? points[points.length - 1].dist : 0);

    if (lapLength > 0) {
        // If range spans more than the whole lap, cover everything
        if (Math.abs(end - start) >= lapLength) {
            let minSpeed = Infinity;
            for (let i = 0; i < points.length; i++) {
                if (points[i].speed !== undefined && points[i].speed !== null) {
                    if (points[i].speed < minSpeed) minSpeed = points[i].speed;
                }
            }
            return minSpeed === Infinity ? null : minSpeed;
        }

        start = (start + lapLength) % lapLength;
        end = (end + lapLength) % lapLength;
    }

    let minSpeed = Infinity;

    if (start <= end) {
        const startIndex = findFirstIndexGe(points, start);
        for (let i = startIndex; i < points.length; i++) {
            if (points[i].dist > end) break;
            if (points[i].speed !== undefined && points[i].speed !== null) {
                if (points[i].speed < minSpeed) {
                    minSpeed = points[i].speed;
                }
            }
        }
    } else {
        for (let i = 0; i < points.length; i++) {
            if (points[i].dist > end) break;
            if (points[i].speed !== undefined && points[i].speed !== null) {
                if (points[i].speed < minSpeed) {
                    minSpeed = points[i].speed;
                }
            }
        }
        const startIndex = findFirstIndexGe(points, start);
        for (let i = startIndex; i < points.length; i++) {
            if (points[i].speed !== undefined && points[i].speed !== null) {
                if (points[i].speed < minSpeed) {
                    minSpeed = points[i].speed;
                }
            }
        }
    }

    return minSpeed === Infinity ? null : minSpeed;
}

export async function fetchTelemetryChannel(sessionId, columnName) {
    const urlObj = new URL('/api/telemetry/channel', window.location.origin);
    const mainUrl = new URL(window.KART_CONFIG.getTrackPointsUrl, window.location.origin);
    mainUrl.searchParams.forEach((val, k) => {
        urlObj.searchParams.set(k, val);
    });
    urlObj.searchParams.set('session_id', sessionId);
    urlObj.searchParams.set('channel', columnName);
    urlObj.searchParams.set('_t', Date.now());

    const res = await fetch(urlObj.toString());
    if (!res.ok) throw new Error(`Status ${res.status}`);
    
    const arrayBuffer = await res.arrayBuffer();
    if (arrayBuffer.byteLength < 16) {
        return [];
    }

    const view = new DataView(arrayBuffer);
    const meanVal = view.getFloat64(0, true);
    const scale = view.getFloat64(8, true);
    const deltas = new Int16Array(arrayBuffer.slice(16));

    const length = deltas.length;
    const values = new Array(length);
    let currentQuantized = 0;
    for (let i = 0; i < length; i++) {
        if (deltas[i] === -32768) {
            values[i] = null;
        } else {
            currentQuantized += deltas[i];
            values[i] = meanVal + currentQuantized * scale;
        }
    }
    return values;
}
