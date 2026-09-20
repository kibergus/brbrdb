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
 * Google Maps integration and marker/polyline management.
 */
import { state } from './state.js';
import { findSegmentIndex, precalculateLapData, fetchTelemetryChannel, getPointAtDistance, getTurnTime } from './telemetry.js';
import { parseLapTime, attachSmoothWheelZoom } from '../utils.js';
import { debouncedUpdateURL } from './url_sync.js';

export const LAP_PALETTE = [
    '#00f0ff', // Lap 1: Vivid Electric Cyan
    '#ff6600', // Lap 2: Bright Safety Orange
    '#00ff44', // Lap 3: Electric Lime Green
    '#ff0055', // Lap 4: Vivid Hot Pink / Magenta
    '#ffcc00', // Lap 5: Bright Yellow
    '#a855f7', // Lap 6: Deep Electric Purple
    '#0088ff', // Lap 7: Vivid Cobalt Blue
    '#ff3300', // Lap 8: Red-Orange
    '#00ffcc', // Lap 9: Bright Aqua Teal
    '#ff00cc', // Lap 10: Bright Fuchsia
    '#76e000', // Lap 11: Apple Lime
    '#e60000', // Lap 12: Pure Bright Red
    '#33ffff', // Lap 13: Bright Cyan
    '#ff9900', // Lap 14: Amber Orange
    '#cc33ff', // Lap 15: Violet
    '#00ff99'  // Lap 16: Mint Green
];

export function calculateBoundsZoom(bounds, mapDim) {
    if (!bounds) return 17.5;
    try {
        let ne = null;
        let sw = null;
        if (typeof bounds.getNorthEast === 'function') {
            ne = bounds.getNorthEast();
            sw = bounds.getSouthWest();
        } else if (bounds.ne && bounds.sw) {
            ne = bounds.ne;
            sw = bounds.sw;
        }
        if (!ne || !sw) return 17.5;

        let width = (mapDim && mapDim.width > 50) ? mapDim.width : 0;
        let height = (mapDim && mapDim.height > 50) ? mapDim.height : 0;

        if (!width || !height) {
            if (typeof window !== 'undefined') {
                const mapEl = document.getElementById('map');
                if (mapEl && mapEl.clientWidth > 50 && mapEl.clientHeight > 50) {
                    width = mapEl.clientWidth;
                    height = mapEl.clientHeight;
                } else {
                    width = Math.max(400, (window.innerWidth || 1200) - 450);
                    height = Math.max(300, (window.innerHeight || 800) - 250);
                }
            } else {
                width = 1000;
                height = 700;
            }
        }

        const paddedWidth = Math.max(100, width - 60);
        const paddedHeight = Math.max(100, height - 60);

        const latNe = typeof ne.lat === 'function' ? ne.lat() : ne.lat;
        const latSw = typeof sw.lat === 'function' ? sw.lat() : sw.lat;
        const lngNe = typeof ne.lng === 'function' ? ne.lng() : ne.lng;
        const lngSw = typeof sw.lng === 'function' ? sw.lng() : sw.lng;

        const latRad = (lat) => {
            const sin = Math.sin(lat * Math.PI / 180);
            return Math.log((1 + sin) / (1 - sin)) / 2;
        };

        const latFraction = Math.abs(latRad(latNe) - latRad(latSw)) / (2 * Math.PI);
        let lngDiff = lngNe - lngSw;
        if (lngDiff < 0) lngDiff += 360;
        const lngFraction = lngDiff / 360;

        const zoom = (mapPx, worldPx, fraction) => {
            if (fraction <= 0) return 17.5;
            return Math.log(mapPx / worldPx / fraction) / Math.LN2;
        };

        const latZoom = zoom(paddedHeight, 256, Math.max(1e-7, latFraction));
        const lngZoom = zoom(paddedWidth, 256, Math.max(1e-7, lngFraction));

        const result = Math.min(latZoom, lngZoom);
        return (isFinite(result) && result > 5) ? Math.round(result * 10) / 10 : 17.5;
    } catch (e) {
        return 17.5;
    }
}

export function interpolateMultiStopColor(u, stops) {
    const val = Math.max(0, Math.min(1, u));
    for (let i = 0; i < stops.length - 1; i++) {
        const [u1, c1] = stops[i];
        const [u2, c2] = stops[i + 1];
        if (val >= u1 && val <= u2) {
            const f = (val - u1) / (u2 - u1 || 1);
            const r = Math.round(c1[0] + f * (c2[0] - c1[0]));
            const g = Math.round(c1[1] + f * (c2[1] - c1[1]));
            const b = Math.round(c1[2] + f * (c2[2] - c1[2]));
            return `rgb(${r}, ${g}, ${b})`;
        }
    }
    const lastColor = stops[stops.length - 1][1];
    return `rgb(${lastColor[0]}, ${lastColor[1]}, ${lastColor[2]})`;
}

export function getReferenceLap() {
    if (state.fastestGroupALap) return state.fastestGroupALap;
    if (state.fastestSelectedLap) return state.fastestSelectedLap;
    if (state.lapDataLookup) {
        let best = null;
        let minTime = Infinity;
        const turnSelector = typeof document !== 'undefined' ? document.getElementById('turn-selector') : null;
        const turnIdx = (turnSelector && turnSelector.value !== undefined && turnSelector.value !== "" && !isNaN(parseInt(turnSelector.value)))
            ? parseInt(turnSelector.value)
            : (state.currentTurnIdx !== undefined ? state.currentTurnIdx : 0);

        for (const id in state.lapDataLookup) {
            const lap = state.lapDataLookup[id];
            if (!lap || !lap.points || lap.points.length === 0 || lap.is_valid === false) continue;
            let t;
            if (state.sortMode === 'turn') {
                t = getTurnTime(lap, turnIdx);
            } else {
                t = parseLapTime(lap.lap_time);
            }
            if (t !== null && !isNaN(t) && t < minTime) {
                minTime = t;
                best = lap;
            }
        }
        return best;
    }
    return null;
}

export function calculateSegmentColor(p1, lapId, mode, maxSpeed = 100, p2 = null) {
    if (!p1) return '#ffffff';

    const currentMode = mode || state.trajectoryColorMode || 'pedals';

    if (currentMode === 'delta_t') {
        const refLap = getReferenceLap();
        const refLapId = state.fastestGroupALapId || (refLap && refLap.lapId ? refLap.lapId : (refLap && refLap.session_id && refLap.lap_num ? `${refLap.session_id}-${refLap.lap_num}` : null)) || state.fastestSelectedLapId;

        // Fast track is white
        if (!refLap || !refLap.points || refLap.points.length === 0 || lapId === refLapId) {
            return '#ffffff';
        }

        const currentLap = state.lapDataLookup ? state.lapDataLookup[lapId] : null;
        if (!currentLap || !currentLap.points || currentLap.points.length === 0) {
            return '#ffffff';
        }

        const s1 = p1.dist !== undefined && p1.dist !== null ? p1.dist : 0;
        const s2 = (p2 && p2.dist !== undefined && p2.dist !== null && p2.dist > s1) ? p2.dist : (s1 + 1);
        const sMid = (s1 + s2) / 2;

        // Centered distance window (5.0m half-width = 10.0m total window, approx 0.5s at race speed)
        // Symmetric around sMid for zero phase lag and smooth, readable derivative.
        const halfWindow = 5.0;
        const wStart = Math.max(0, sMid - halfWindow);
        const wEnd = sMid + halfWindow;

        const pLap1 = getPointAtDistance(currentLap, wStart, 'time');
        const pLap2 = getPointAtDistance(currentLap, wEnd, 'time');
        const pRef1 = getPointAtDistance(refLap, wStart, 'time');
        const pRef2 = getPointAtDistance(refLap, wEnd, 'time');

        let rate = 0;
        if (pLap1 && pLap2 && pRef1 && pRef2) {
            const dtLap = pLap2.time - pLap1.time;
            const dtRef = pRef2.time - pRef1.time;
            if (dtLap > 0 && dtRef > 0) {
                // Exact derivative matching the slope of the delta T plot over the centered window:
                rate = (dtLap - dtRef) / dtRef;
            }
        } else {
            const refSpeed = getPointAtDistance(refLap, sMid, 'speed')?.speed;
            const lapSpeed = (p1.speed !== undefined && p1.speed !== null) ? p1.speed : getPointAtDistance(currentLap, sMid, 'speed')?.speed;
            if (lapSpeed && lapSpeed > 0 && refSpeed && refSpeed > 0) {
                rate = (refSpeed - lapSpeed) / lapSpeed;
            }
        }

        // Color mapping:
        // rate < 0 (gaining time / faster than fast lap): Green
        // rate == 0 (same pace): Yellow
        // rate > 0 (losing time / slower than fast lap): Red
        const DELTA_T_STOPS = [
            [0.0, [0, 255, 0]],     // -maxRate (gaining time): Pure Green
            [0.5, [255, 255, 0]],   //  0.0 (equal pace):        Pure Yellow
            [1.0, [255, 0, 0]]      // +maxRate (losing time):   Pure Red
        ];

        const maxRate = 0.25; // 0.25s gained/lost per second of fast lap
        const s0 = 0.025;      // Sensitivity threshold for asinh scale
        const asinhMax = Math.asinh(maxRate / s0);
        const normalizedRate = Math.max(-1, Math.min(1, Math.asinh(rate / s0) / asinhMax));
        const u = 0.5 + 0.5 * normalizedRate;

        return interpolateMultiStopColor(u, DELTA_T_STOPS);
    }

    if (currentMode === 'time') {
        const turnSelector = typeof document !== 'undefined' ? document.getElementById('turn-selector') : null;
        const turnIdx = (turnSelector && turnSelector.value !== undefined && turnSelector.value !== "" && !isNaN(parseInt(turnSelector.value)))
            ? parseInt(turnSelector.value)
            : (state.currentTurnIdx !== undefined ? state.currentTurnIdx : 0);
        const isTurnSort = (state.sortMode === 'turn');

        const getLapMetricTime = (lap) => {
            if (!lap) return null;
            if (isTurnSort) {
                const t = getTurnTime(lap, turnIdx);
                if (t !== null && t !== undefined && !isNaN(t) && t > 0 && t < 999999) {
                    return t;
                }
                return null;
            }
            const t = parseLapTime(lap.lap_time);
            if (t !== null && t !== undefined && !isNaN(t) && t > 0 && t < 999999) {
                return t;
            }
            return null;
        };

        const selectedIds = state.groupASelection ? Array.from(state.groupASelection) : [];

        const validTimes = [];
        if (state.lapDataLookup) {
            selectedIds.forEach(id => {
                const lap = state.lapDataLookup[id];
                const t = getLapMetricTime(lap);
                if (t !== null) {
                    validTimes.push(t);
                }
            });
        }

        const currentLap = state.lapDataLookup ? state.lapDataLookup[lapId] : null;
        const lapTime = getLapMetricTime(currentLap);
        if (lapTime === null) {
            return '#94a3b8';
        }

        const minTime = validTimes.length > 0 ? Math.min(...validTimes) : lapTime;
        const maxTime = validTimes.length > 0 ? Math.max(...validTimes) : lapTime;

        const TIME_STOPS = [
            [0.0, [0, 255, 0]],     // Fastest lap: Pure Green
            [0.5, [255, 255, 0]],   // Mid pace:    Pure Yellow
            [1.0, [255, 0, 0]]      // Slowest lap: Pure Red
        ];

        let u = 0.0;
        if (maxTime > minTime) {
            u = Math.max(0, Math.min(1, (lapTime - minTime) / (maxTime - minTime)));
        }

        return interpolateMultiStopColor(u, TIME_STOPS);
    }

    if (currentMode === 'speed') {
        const v = p1.speed || 0;
        const vMax = maxSpeed > 0 ? maxSpeed : (state.globalMaxSpeed || 100);
        const vMin = (state.globalMinSpeed !== undefined && state.globalMinSpeed < vMax) ? state.globalMinSpeed : 0;
        const range = vMax - vMin;
        const u = range > 0 ? Math.max(0, Math.min(1, (v - vMin) / range)) : Math.max(0, Math.min(1, v / vMax));

        const SPEED_STOPS = [
            [0.00, [40, 20, 180]],   // Apex / Minimum speed: Deep Navy/Violet
            [0.25, [0, 200, 255]],   // Low speed / Corner exit: Cyan
            [0.55, [255, 30, 60]],   // Mid speed / Acceleration: Crimson Red
            [0.80, [255, 200, 0]],   // High speed straight: Gold Yellow
            [1.00, [0, 255, 80]]     // Top speed: Neon Green
        ];

        return interpolateMultiStopColor(u, SPEED_STOPS);
    }

    if (currentMode === 'accel') {
        const acc = p1.acceleration || 0;
        if (acc < 0) {
            const factor = Math.min(1, Math.abs(acc) / 4.0);
            const intensity = Math.pow(factor, 0.5);
            const gb = Math.round(255 * (1 - intensity));
            return `rgb(255, ${gb}, ${gb})`;
        } else if (acc > 0) {
            const factor = Math.min(1, acc / 2.5);
            const intensity = Math.pow(factor, 0.5);
            const rb = Math.round(255 * (1 - intensity));
            return `rgb(${rb}, 255, ${rb})`;
        }
        return '#ffffff';
    }

    if (currentMode === 'gforce_lon') {
        const gx = (p1.gx !== null && p1.gx !== undefined) ? p1.gx : ((p1.acceleration || 0) / 9.81);
        if (gx < 0) {
            const factor = Math.min(1, Math.abs(gx) / 1.5);
            const intensity = Math.pow(factor, 0.5);
            const gb = Math.round(255 * (1 - intensity));
            return `rgb(255, ${gb}, ${gb})`;
        } else if (gx > 0) {
            const factor = Math.min(1, gx / 1.0);
            const intensity = Math.pow(factor, 0.5);
            const rb = Math.round(255 * (1 - intensity));
            return `rgb(${rb}, 255, ${rb})`;
        }
        return '#ffffff';
    }

    if (currentMode === 'gforce_lat') {
        const gy = (p1.gy !== null && p1.gy !== undefined) ? p1.gy : 0;
        if (gy < 0) {
            const factor = Math.min(1, Math.abs(gy) / 1.5);
            const intensity = Math.pow(factor, 0.5);
            const rg = Math.round(255 * (1 - intensity));
            return `rgb(${rg}, ${rg}, 255)`;
        } else if (gy > 0) {
            const factor = Math.min(1, gy / 1.5);
            const intensity = Math.pow(factor, 0.5);
            const gb = Math.round(255 * (1 - intensity));
            return `rgb(255, ${gb}, ${gb})`;
        }
        return '#ffffff';
    }

    if (currentMode === 'lap') {
        if (lapId && state.lapColorsForId[lapId]) {
            return state.lapColorsForId[lapId];
        }
        return LAP_PALETTE[0];
    }

    // Default 'pedals' (throttle/brake)
    if (p1.brake > 1.0) {
        const intensity = Math.pow(p1.brake / 100, 0.2);
        const factor = 1 - intensity;
        const gb = Math.round(255 * factor);
        return `rgb(255, ${gb}, ${gb})`;
    } else if (p1.throttle > 1.0) {
        const factor = (100 - p1.throttle) / 100;
        const rb = Math.round(255 * factor);
        return `rgb(${rb}, 255, ${rb})`;
    }
    return '#ffffff';
}

export function toggleTrajDropdown(event) {
    if (event) event.stopPropagation();
    const dropdown = document.getElementById('traj-color-dropdown');
    if (dropdown && dropdown.classList) {
        dropdown.classList.toggle('open');
    }
}

export function updateAllPolylineColors(mode) {
    const currentMode = mode || state.trajectoryColorMode || 'pedals';
    const maxSpeed = state.globalMaxSpeed || 100;

    if (!state.lapPolylines) return;

    const refLap = getReferenceLap();
    const refLapId = state.fastestGroupALapId || (refLap && refLap.lapId ? refLap.lapId : (refLap && refLap.session_id && refLap.lap_num ? `${refLap.session_id}-${refLap.lap_num}` : null)) || state.fastestSelectedLapId;

    Object.keys(state.lapPolylines).forEach(lapId => {
        const polylines = state.lapPolylines[lapId];
        const lap = state.lapDataLookup ? state.lapDataLookup[lapId] : null;
        if (!polylines || !lap || !lap.points) return;

        const isRefLap = (lapId === refLapId);
        const zIndex = (currentMode === 'delta_t') ? (isRefLap ? 1 : 5) : 1;
        const opacity = (currentMode === 'time') ? 0.45 : 1.0;
        const weight = (currentMode === 'time') ? 2 : 4;

        const numSegments = lap.points.length - 1;
        for (let i = 0; i < numSegments; i++) {
            const segment = polylines[i];
            if (segment && typeof segment.setOptions === 'function') {
                const color = calculateSegmentColor(lap.points[i], lapId, currentMode, maxSpeed, lap.points[i + 1]);
                segment.setOptions({ strokeColor: color, strokeOpacity: opacity, strokeWeight: weight, zIndex: zIndex });
                segment.originalColor = color;
            }
        }
    });
}

export function setTrajectoryColorMode(mode) {
    state.trajectoryColorMode = mode;

    const labelMap = {
        pedals: 'Pedals',
        delta_t: 'Δ t',
        time: 'Time',
        speed: 'Speed',
        accel: 'Accel',
        gforce_lon: 'G Lon',
        gforce_lat: 'G Lat',
        lap: 'Per Lap'
    };

    const currentLabel = document.getElementById('traj-color-current-label');
    if (currentLabel) {
        currentLabel.textContent = labelMap[mode] || 'Pedals';
    }

    document.querySelectorAll('#traj-color-menu .dropdown-item').forEach(item => {
        if (item.classList) {
            if (item.getAttribute('data-mode') === mode) {
                item.classList.add('active');
            } else {
                item.classList.remove('active');
            }
        }
    });

    const dropdown = document.getElementById('traj-color-dropdown');
    if (dropdown && dropdown.classList) {
        dropdown.classList.remove('open');
    }

    updateAllPolylineColors(mode);

    debouncedUpdateURL();
}

export function loadBaseColumnsForSessions(sessionsData) {
    const fetchSessionBase = async (session) => {
        const session_id = session.session_id;

        const findCol = (choices) => {
            return session.columns.find(col => choices.some(choice => col.toLowerCase() === choice.toLowerCase()));
        };

        const latCol = findCol(['Latitude']);
        const lngCol = findCol(['Longitude']);
        const timeCol = 'Time';
        const distCol = findCol(['Lap Distance (m)', 'Lap Distance']);
        const speedCol = findCol(['Speed (m/s)', 'Speed']);
        const throttleCol = findCol(['Throttle']);
        const brakeCol = findCol(['Brake']);
        const gxCol = findCol(['GForceLon']);
        const gyCol = findCol(['GForceLat']);

        const colsToFetch = {
            lat: latCol,
            lng: lngCol,
            time: timeCol,
            dist: distCol,
            speed: speedCol,
            throttle: throttleCol,
            brake: brakeCol,
            gx: gxCol,
            gy: gyCol
        };

        const fetchPromises = Object.entries(colsToFetch).map(async ([key, colName]) => {
            if (!colName) return { key, values: [] };
            try {
                const values = await fetchTelemetryChannel(session_id, colName);
                return { key, values };
            } catch (e) {
                console.error(`Failed to fetch column ${colName} for session ${session_id}:`, e);
                return { key, values: [] };
            }
        });

        const results = await Promise.all(fetchPromises);
        const colData = {};
        results.forEach(res => {
            colData[res.key] = res.values;
        });

        if (session.laps) {
            session.laps.forEach(lap => {
                lap.points = [];
                const start = lap.start_idx;
                const end = lap.end_idx;
                for (let i = start; i <= end; i++) {
                    const pt = {};
                    Object.entries(colData).forEach(([key, values]) => {
                        pt[key] = (i < values.length) ? values[i] : null;
                    });
                    lap.points.push(pt);
                }
            });
        }
    };

    return Promise.all(sessionsData.map(fetchSessionBase)).then(() => sessionsData);
}

export function hasBrakeChannel(sessionsData) {
    if (!Array.isArray(sessionsData)) return false;
    const brakeNames = ['brake'];
    return sessionsData.some(session => {
        if (!session || !Array.isArray(session.columns)) return false;
        return session.columns.some(col => brakeNames.includes(col.toLowerCase()));
    });
}

export const SolidBackgroundMapType = function () {
    this.tileSize = new google.maps.Size(256, 256);
    this.maxZoom = 21;
    this.name = 'Track Limits';
    this.alt = 'Show track limits on solid background';
};

SolidBackgroundMapType.prototype.getTile = function (coord, zoom, ownerDocument) {
    const div = ownerDocument.createElement('div');
    div.style.width = this.tileSize.width + 'px';
    div.style.height = this.tileSize.height + 'px';
    div.style.backgroundColor = '#0f172a';
    return div;
};

export function clearLapPolylines() {
    if (state.lapPolylines) {
        Object.keys(state.lapPolylines).forEach(lapId => {
            const polylines = state.lapPolylines[lapId];
            if (Array.isArray(polylines)) {
                polylines.forEach(p => {
                    if (p && typeof p.setMap === 'function') {
                        p.setMap(null);
                    }
                });
            }
        });
    }
    state.lapPolylines = {};
}

export function matchesRequestedLap(requestedSet, lapId, session, lap, sIdx = null) {
    if (!requestedSet || requestedSet.size === 0) return false;
    if (requestedSet.has(lapId)) return true;

    let sessionIndex = sIdx;
    if ((sessionIndex === null || sessionIndex === undefined) && state.allSessionsData && session) {
        sessionIndex = state.allSessionsData.findIndex(s => s === session || s.session_id === session.session_id);
    }

    for (const req of requestedSet) {
        if (!req) continue;
        const cleaned = String(req).replace(/\.csv(?=-|\b)/i, '').replace(/\.csv$/i, '').trim();
        if (cleaned === lapId) return true;

        // Check index notation: e.g. "0_4" or "0-4" (session index _ lap number)
        if (sessionIndex !== -1 && sessionIndex !== null && sessionIndex !== undefined && lap && lap.lap_num !== undefined) {
            if (cleaned === `${sessionIndex}_${lap.lap_num}` || cleaned === `${sessionIndex}-${lap.lap_num}`) {
                return true;
            }
        }

        const reqParts = cleaned.split('-');
        const reqLapNum = parseInt(reqParts[reqParts.length - 1], 10);
        if (!isNaN(reqLapNum) && lap && lap.lap_num === reqLapNum) {
            const reqSid = reqParts.slice(0, -1).join('-');
            if (!reqSid || reqSid === 'lap') return true;
            if (session) {
                const sid = session.session_id || '';
                const sname = session.session_name || '';
                if (sid === reqSid || sid.startsWith(reqSid) || reqSid.startsWith(sid)) return true;
                if (sname && (sname.toLowerCase().includes(reqSid.toLowerCase()) || reqSid.toLowerCase().includes(sname.toLowerCase()))) {
                    return true;
                }
            }
        }
    }
    return false;
}

let isFetchingTrackPoints = false;

export function loadTrackPoints(overrideSessionId) {
    if (isFetchingTrackPoints) return Promise.resolve();
    isFetchingTrackPoints = true;

    clearLapPolylines();

    const lapList = document.getElementById('lap-list');
    if (lapList) {
        lapList.innerHTML = '<div style="padding: 1rem; text-align: center; opacity: 0.5;">Loading laps...</div>';
    }

    const urlParams = new URLSearchParams(window.location.search);
    const sid = overrideSessionId || state.selectedSessionId || urlParams.get('session_id') || (window.KART_CONFIG && window.KART_CONFIG.sessionId);
    let fetchUrl = window.KART_CONFIG ? window.KART_CONFIG.getTrackPointsUrl : '';
    if (sid && sid !== 'all') {
        fetchUrl += `&session_id=${encodeURIComponent(sid)}`;
    }
    console.log("Fetching track points from:", fetchUrl);
    return fetch(`${fetchUrl}&_t=${Date.now()}`)
        .then(response => {
            console.log("Track points response status:", response.status);
            if (!response.ok) throw new Error("HTTP error " + response.status);
            return response.json();
        })
        .then(sessionsData => loadBaseColumnsForSessions(sessionsData))
        .then(sessionsData => {
            state.allSessionsData = sessionsData;
            import('./plots_sync.js').then(m => m.updateTabAvailability());
            if (lapList) lapList.innerHTML = '';
            const bounds = new google.maps.LatLngBounds();

            const reportState = (window.KART_CONFIG && window.KART_CONFIG.reportState) || {};
            const params = new URLSearchParams(window.location.search);
            const hasExplicitTcol = params.has('tcol') || Boolean(reportState.tcol);
            if (!hasExplicitTcol) {
                if (!hasBrakeChannel(sessionsData)) {
                    setTrajectoryColorMode('accel');
                } else {
                    setTrajectoryColorMode('pedals');
                }
            }

            state.groupASelection.clear();
            state.groupBSelection.clear();

            const urlLapsA = params.get('lapsA');
            const urlLapsB = params.get('lapsB');
            let hasCustomLapsA = params.has('lapsA') || Boolean(reportState.lapsA);
            let customLapsA = new Set();
            if (params.has('lapsA')) {
                if (urlLapsA && urlLapsA !== 'none') {
                    urlLapsA.split(',').forEach(id => {
                        const trimmed = id ? id.trim() : '';
                        if (trimmed && trimmed !== 'none') {
                            customLapsA.add(trimmed);
                        }
                    });
                }
            } else if (reportState.lapsA) {
                const arrA = Array.isArray(reportState.lapsA) ? reportState.lapsA : (reportState.lapsA === 'none' ? [] : String(reportState.lapsA).split(','));
                arrA.forEach(id => {
                    const trimmed = id ? String(id).trim() : '';
                    if (trimmed && trimmed !== 'none') customLapsA.add(trimmed);
                });
            }

            let hasCustomLapsB = params.has('lapsB') || Boolean(reportState.lapsB);
            let customLapsB = new Set();
            if (params.has('lapsB')) {
                if (urlLapsB && urlLapsB !== 'none') {
                    urlLapsB.split(',').forEach(id => {
                        const trimmed = id ? id.trim() : '';
                        if (trimmed && trimmed !== 'none') {
                            customLapsB.add(trimmed);
                        }
                    });
                }
            } else if (reportState.lapsB) {
                const arrB = Array.isArray(reportState.lapsB) ? reportState.lapsB : (reportState.lapsB === 'none' ? [] : String(reportState.lapsB).split(','));
                arrB.forEach(id => {
                    const trimmed = id ? String(id).trim() : '';
                    if (trimmed && trimmed !== 'none') customLapsB.add(trimmed);
                });
            }

            let allLaps = [];
            sessionsData.forEach(session => {
                if (!session.laps) return;
                session.laps.forEach(lap => {
                    if (lap.is_valid === false) return;
                    const t = parseLapTime(lap.lap_time);
                    if (isNaN(t)) return;
                    allLaps.push({
                        lapId: `${session.session_id}-${lap.lap_num}`,
                        time: t
                    });
                });
            });
            allLaps.sort((a, b) => a.time - b.time);
            const fastest5Ids = new Set(allLaps.slice(0, 5).map(l => l.lapId));
            const fastest80Count = Math.ceil(allLaps.length * 0.8);
            const fastest80Ids = new Set(allLaps.slice(0, fastest80Count).map(l => l.lapId));

            let globalMaxSpeed = 0;
            let globalMinSpeed = Infinity;
            let globalLapCounter = 0;
            sessionsData.forEach(session => {
                if (!session.laps) return;
                session.laps.forEach(lap => {
                    if (lap.is_valid === false) return;
                    const lapId = `${session.session_id}-${lap.lap_num}`;
                    if (!state.lapColorsForId[lapId]) {
                        state.lapColorsForId[lapId] = LAP_PALETTE[globalLapCounter % LAP_PALETTE.length];
                    }
                    globalLapCounter++;
                    if (lap.points) {
                        lap.points.forEach(p => {
                            if (p.speed > globalMaxSpeed) globalMaxSpeed = p.speed;
                            if (p.speed !== null && p.speed !== undefined && p.speed < globalMinSpeed) {
                                globalMinSpeed = p.speed;
                            }
                        });
                    }
                });
            });
            state.globalMaxSpeed = globalMaxSpeed > 0 ? globalMaxSpeed : 100;
            state.globalMinSpeed = (globalMinSpeed !== Infinity && globalMinSpeed < state.globalMaxSpeed) ? globalMinSpeed : 0;

            sessionsData.forEach((session, sIdx) => {
                if (!session.laps) return;
                session.laps.forEach(lap => {
                    if (lap.is_valid === false) return;
                    const lapId = `${session.session_id}-${lap.lap_num}`;
                    lap.lapId = lapId;
                    lap.session_id = session.session_id;
                    lap.session_name = session.session_name;
                    state.lapDataLookup[lapId] = lap;

                    precalculateLapData(lap);

                    if (hasCustomLapsA) {
                        if (matchesRequestedLap(customLapsA, lapId, session, lap, sIdx)) {
                            state.groupASelection.add(lapId);
                        }
                    } else {
                        if (fastest5Ids.has(lapId)) {
                            state.groupASelection.add(lapId);
                        }
                    }
                    if (hasCustomLapsB) {
                        if (matchesRequestedLap(customLapsB, lapId, session, lap, sIdx)) {
                            state.groupBSelection.add(lapId);
                        }
                    } else {
                        if (fastest80Ids.has(lapId)) {
                            state.groupBSelection.add(lapId);
                        }
                    }
                    state.lapPolylines[lapId] = [];

                    const refLap = getReferenceLap();
                    const refLapId = state.fastestGroupALapId || (refLap && refLap.lapId ? refLap.lapId : (refLap && refLap.session_id && refLap.lap_num ? `${refLap.session_id}-${refLap.lap_num}` : null)) || state.fastestSelectedLapId;
                    const isRefLap = (lapId === refLapId);
                    const segZIndex = (state.trajectoryColorMode === 'delta_t') ? (isRefLap ? 1 : 5) : 1;
                    const isVisible = (state.groupASelection.has(lapId) && state.groupAVisibleMap) || (state.groupBSelection.has(lapId) && state.groupBVisibleMap);

                    for (let i = 0; i < lap.points.length - 1; i++) {
                        const p1 = lap.points[i];
                        const p2 = lap.points[i + 1];

                        const color = calculateSegmentColor(p1, lapId, state.trajectoryColorMode, state.globalMaxSpeed, p2);
                        const opacity = (state.trajectoryColorMode === 'time') ? 0.45 : 1.0;
                        const weight = (state.trajectoryColorMode === 'time') ? 2 : 4;

                        const segment = new google.maps.Polyline({
                            path: [p1, p2],
                            geodesic: false,
                            strokeColor: color,
                            strokeOpacity: opacity,
                            strokeWeight: weight,
                            zIndex: segZIndex,
                            map: isVisible ? state.map : null
                        });
                        segment.originalColor = color;
                        state.lapPolylines[lapId].push(segment);
                    }

                    const hitArea = new google.maps.Polyline({
                        path: lap.points,
                        geodesic: false,
                        strokeColor: '#38bdf8',
                        strokeOpacity: 0.0,
                        strokeWeight: 10,
                        zIndex: 2,
                        map: isVisible ? state.map : null
                    });

                    state.lapPolylines[lapId].push(hitArea);

                    lap.points.forEach(p => bounds.extend(p));
                });
            });

            if (!bounds.isEmpty()) {
                state.trackBounds = bounds;
            }

            if (!bounds.isEmpty() && state.map && !state.hasCustomMapSet && state.activeTab === 'map' && state.sortMode !== 'turn') {
                state.map.fitBounds(bounds);
                if (window.google && window.google.maps && google.maps.event) {
                    google.maps.event.addListenerOnce(state.map, 'idle', () => {
                        if (state.map && typeof state.map.getZoom === 'function') {
                            const z = state.map.getZoom();
                            if (z && z > 5) {
                                state.defaultMapZoom = z;
                            }
                        }
                    });
                }
            }
            state.mapInitialized = true;
            isFetchingTrackPoints = false;

            import('./lap_selection.js').then(ui => {
                ui.updateVisibilityIcons();

                const reportState = (window.KART_CONFIG && window.KART_CONFIG.reportState) || {};
                const params = new URLSearchParams(window.location.search);
                const urlSort = params.get('sort') || reportState.sort;
                const urlTurn = params.has('turn') ? params.get('turn') : (reportState.turn !== undefined ? String(reportState.turn) : null);
                if (urlSort) {
                    if (urlSort === 'turn' && urlTurn !== null && !isNaN(parseInt(urlTurn))) {
                        state.currentTurnIdx = parseInt(urlTurn);
                    }
                    ui.setSort(urlSort, state.currentTurnIdx);
                } else {
                    ui.renderLapList();
                }

                // Re-apply custom xlim if present, so it overrides any sort-based default range resets
                const xlimParam = params.get('xlim');
                if (xlimParam) {
                    const parts = xlimParam.split(',');
                    if (parts.length === 2) {
                        const min = parseFloat(parts[0]);
                        const max = parseFloat(parts[1]);
                        if (!isNaN(min) && !isNaN(max) && !(min === 0 && max === 100)) {
                            state.globalTelemetryXRange = [min, max];
                        }
                    }
                } else if (reportState.xlim && Array.isArray(reportState.xlim) && reportState.xlim.length === 2) {
                    state.globalTelemetryXRange = [parseFloat(reportState.xlim[0]), parseFloat(reportState.xlim[1])];
                }

                ui.updateFastestSelectedLap();
                import('./stats_plots.js').then(stats => stats.renderStatsPlots());
                import('./plots_sync.js').then(plots => plots.renderExpandablePlots());
            });
            initTrackMarkers();
        })
        .catch(err => {
            isFetchingTrackPoints = false;
            console.error('Error loading track points:', err);
            if (lapList) lapList.innerHTML = '<div style="padding: 1rem; color: var(--warning);">Error loading telemetry</div>';
        });
}

export function initMap() {
    try {
        const mapContainer = document.getElementById('map');
        if (!mapContainer) return;

        if (!state.map) {
            if (!state.globalTelemetryXRange && state.trackData && state.trackData.lap_length) {
                state.globalTelemetryXRange = [0, state.trackData.lap_length];
            }

            const reportState = (window.KART_CONFIG && window.KART_CONFIG.reportState) || {};
            const params = new URLSearchParams(window.location.search);
            const mapParam = params.get('map');
            let hasCustomMap = false;
            let customZoom = 16;
            let customCenter = { lat: 0, lng: 0 };
            if (mapParam) {
                const parts = mapParam.split(',');
                if (parts.length === 3) {
                    const zoom = parseInt(parts[0]);
                    const lat = parseFloat(parts[1]);
                    const lng = parseFloat(parts[2]);
                    if (!isNaN(zoom) && !isNaN(lat) && !isNaN(lng)) {
                        customZoom = zoom;
                        customCenter = { lat, lng };
                        hasCustomMap = true;
                    }
                }
            } else if (reportState.map) {
                if (Array.isArray(reportState.map) && reportState.map.length === 3) {
                    customZoom = parseInt(reportState.map[0]);
                    customCenter = { lat: parseFloat(reportState.map[1]), lng: parseFloat(reportState.map[2]) };
                    hasCustomMap = true;
                } else if (typeof reportState.map === 'object' && reportState.map.lat !== undefined && reportState.map.lng !== undefined) {
                    customZoom = parseInt(reportState.map.zoom) || 16;
                    customCenter = { lat: parseFloat(reportState.map.lat), lng: parseFloat(reportState.map.lng) };
                    hasCustomMap = true;
                }
            }
            state.hasCustomMapSet = hasCustomMap;

            state.map = new google.maps.Map(mapContainer, {
                center: customCenter,
                zoom: customZoom,
                mapTypeId: 'satellite',
                mapId: 'KART_ANALYSIS_MAP',
                tilt: 0,
                gestureHandling: 'greedy',
                scrollwheel: false,
                isFractionalZoomEnabled: true,
                streetViewControl: false,
                mapTypeControl: false,
                fullscreenControl: false
            });

            attachSmoothWheelZoom(state.map, mapContainer);

            state.map.addListener('idle', () => {
                debouncedUpdateURL();
            });

            state.map.mapTypes.set('solid_dark', new SolidBackgroundMapType());

            state.map.data.setStyle({
                strokeColor: '#38bdf8',
                strokeOpacity: 0.8,
                strokeWeight: 3,
                fillColor: '#38bdf8',
                fillOpacity: 0.2,
                icon: {
                    path: google.maps.SymbolPath.CIRCLE,
                    scale: 4,
                    fillColor: '#38bdf8',
                    fillOpacity: 0.8,
                    strokeColor: '#38bdf8',
                    strokeWeight: 1
                }
            });

            const controls = document.getElementById('map-overlay-controls');
            if (controls) {
                state.map.controls[google.maps.ControlPosition.TOP_LEFT].push(controls);
                controls.style.display = 'block';
            }
            const rightControls = document.getElementById('map-overlay-right-controls');
            if (rightControls) {
                state.map.controls[google.maps.ControlPosition.TOP_RIGHT].push(rightControls);
                rightControls.style.display = 'block';
            }
        }

        if (!state.mapInitialized && !isFetchingTrackPoints) {
            loadTrackPoints();
        }
    } catch (e) {
        console.error("Error in initMap:", e);
    }
}

export function setMapType(type) {
    state.currentMapType = type;

    document.querySelectorAll('#map-type-selector .selector-item').forEach(item => {
        if (item.getAttribute('data-type') === type) {
            item.classList.add('active');
        } else {
            item.classList.remove('active');
        }
    });

    if (type === 'satellite' || type === 'hybrid') {
        state.map.setMapTypeId('satellite');
        state.map.setOptions({ styles: [] });
    } else if (type === 'kartsim') {
        state.map.setMapTypeId('solid_dark');
    }

    updateTrackLimitsVisibility();
}

export function updateTrackLimitsVisibility() {
    const showLimits = (state.currentMapType === 'kartsim' || state.currentMapType === 'hybrid');

    state.map.data.setMap(showLimits ? state.map : null);

    state.trackMarkers.forEach(m => {
        if (m.setMap) m.setMap(state.map);
        else m.map = state.map;
    });

    if (state.distanceMarker) state.distanceMarker.setMap(state.map);
}

export function addTrackMarker(p1, p2, label, color) {
    const heading = google.maps.geometry.spherical.computeHeading(p1, p2);
    const linePath = [
        google.maps.geometry.spherical.computeOffset(p1, 7.5, heading - 90),
        google.maps.geometry.spherical.computeOffset(p1, 7.5, heading + 90)
    ];

    const line = new google.maps.Polyline({
        path: linePath,
        geodesic: false,
        strokeColor: color,
        strokeOpacity: 0.8,
        strokeWeight: 3,
        zIndex: 10,
        map: state.map
    });
    state.trackMarkers.push(line);

    const markerContent = document.createElement('div');
    markerContent.className = 'track-marker-label';
    markerContent.textContent = label;
    markerContent.style.color = color;
    markerContent.style.borderColor = color;

    const marker = new google.maps.marker.AdvancedMarkerElement({
        position: p1,
        map: state.map,
        content: markerContent,
        title: label
    });
    state.trackMarkers.push(marker);
}

export function addTrackMarkerAtDistance(points, targetDist, label, color) {
    const idx = findSegmentIndex(points, targetDist);
    if (idx !== -1 && idx < points.length - 1) {
        addTrackMarker(points[idx], points[idx + 1], label, color);
    }
}

export function initTrackMarkers() {
    console.log("Fetching track data for:", window.KART_CONFIG.trackName);
    fetch(window.KART_CONFIG.getTrackDataUrl)
        .then(res => res.json())
        .then(data => {
            if (data.error) {
                console.error("Track data error:", data.error);
                return;
            }
            data.center_line = data.center_line.map(p => ({
                ...p,
                lng: p.lon
            })).sort((a, b) => a.dist - b.dist);
            state.trackData = data;

            const slider = document.getElementById('distance-slider');
            const params = new URLSearchParams(window.location.search);
            const reportState = (window.KART_CONFIG && window.KART_CONFIG.reportState) || {};
            const xlimParam = params.get('xlim');
            let hasCustomXlim = false;
            if (xlimParam) {
                const parts = xlimParam.split(',');
                if (parts.length === 2) {
                    const min = parseFloat(parts[0]);
                    const max = parseFloat(parts[1]);
                    if (!isNaN(min) && !isNaN(max) && !(min === 0 && max === 100)) {
                        state.globalTelemetryXRange = [min, max];
                        hasCustomXlim = true;
                    }
                }
            } else if (reportState.xlim && Array.isArray(reportState.xlim) && reportState.xlim.length === 2) {
                state.globalTelemetryXRange = [parseFloat(reportState.xlim[0]), parseFloat(reportState.xlim[1])];
                hasCustomXlim = true;
            }
            if (slider && data.lap_length) {
                slider.max = data.lap_length;
                slider.step = 0.01;
                if (!hasCustomXlim && state.sortMode !== 'turn') {
                    state.globalTelemetryXRange = [0, data.lap_length];
                }
            }

            let startDist = 0;
            const distParam = params.has('dist') ? params.get('dist') : (reportState.dist !== undefined ? String(reportState.dist) : null);
            if (distParam) {
                const parsedDist = parseFloat(distParam);
                if (!isNaN(parsedDist) && parsedDist >= 0 && parsedDist <= (data.lap_length || Infinity)) {
                    startDist = parsedDist;
                }
            }
            state.playbackDistance = startDist;
            state.currentTargetDist = startDist;
            if (slider) {
                slider.value = startDist;
            }
            const display = document.getElementById('distance-display');
            if (display) {
                display.textContent = Math.round(startDist) + 'm';
            }

            state.distanceMarker = new google.maps.Polyline({
                path: [],
                geodesic: false,
                strokeColor: '#facc15',
                strokeOpacity: 1.0,
                strokeWeight: 5,
                zIndex: 2000,
                map: state.map
            });

            updateDistanceMarker(startDist);

            state.trackMarkers.forEach(m => {
                if (m.setMap) m.setMap(null);
                else m.map = null;
            });
            state.trackMarkers = [];

            const points = data.center_line;
            if (!points || points.length < 2) return;

            addTrackMarker(points[0], points[1], 'START', '#3b82f6');

            if (data.sector_end) {
                data.sector_end.forEach((endDist, idx) => {
                    if (idx === data.sector_end.length - 1 && Math.abs(endDist - data.lap_length) < 1.0) return;
                    addTrackMarkerAtDistance(points, endDist, `S${idx + 2}`, '#f59e0b');
                });
            }

            if (data.turns) {
                const turnSelector = document.getElementById('turn-selector');
                if (turnSelector) {
                    turnSelector.innerHTML = '';
                    data.turns.forEach((turn, idx) => {
                        const opt = document.createElement('option');
                        opt.value = idx;
                        opt.textContent = turn.name || `Turn ${idx + 1}`;
                        turnSelector.appendChild(opt);
                    });

                    const urlTurn = params.has('turn') ? params.get('turn') : (reportState.turn !== undefined ? String(reportState.turn) : null);
                    if (urlTurn !== null) {
                        const turnIdx = parseInt(urlTurn);
                        if (!isNaN(turnIdx) && turnIdx >= 0 && turnIdx < data.turns.length) {
                            turnSelector.value = turnIdx;
                            state.currentTurnIdx = turnIdx;
                        }
                    } else if (state.currentTurnIdx !== undefined) {
                        turnSelector.value = state.currentTurnIdx;
                    }
                }

                if (state.sortMode === 'turn') {
                    const turn = data.turns[state.currentTurnIdx || 0];
                    if (turn && !hasCustomXlim) {
                        let rangeEnd = turn.end;
                        const nextTurn = data.turns[(state.currentTurnIdx || 0) + 1];
                        if (nextTurn) {
                            rangeEnd = nextTurn.start;
                        } else if (data.lap_length) {
                            rangeEnd = data.lap_length;
                        }
                        const rangeStart = Math.max(0, turn.start - 20);
                        state.globalTelemetryXRange = [rangeStart, rangeEnd];
                    }
                    if (state.map) {
                        import('./lap_selection.js').then(ui => {
                            ui.focusMapOnTurn(state.currentTurnIdx || 0);
                        });
                    }
                }

                data.turns.forEach((turn, idx) => {
                    if (turn.start !== undefined && turn.start !== turn.end) {
                        addTrackMarkerAtDistance(points, turn.start, `T${idx + 1}`, '#10b981');
                    }
                    addTrackMarkerAtDistance(points, turn.end, `T${idx + 1}`, '#10b981');
                });
            } else if (data.turn_end) {
                data.turn_end.forEach((endDist, idx) => {
                    if (data.sector_end && data.sector_end.some(s => Math.abs(s - endDist) < 2.0)) return;
                    if (Math.abs(endDist - data.lap_length) < 2.0 || endDist < 2.0) return;
                    if (data.turn_start && data.turn_start[idx] !== undefined && data.turn_start[idx] !== endDist) {
                        addTrackMarkerAtDistance(points, data.turn_start[idx], `T${idx + 1}`, '#10b981');
                    }
                    addTrackMarkerAtDistance(points, endDist, `T${idx + 1}`, '#10b981');
                });
            }

            import('./plots_sync.js').then(plots => {
                plots.renderExpandablePlots();
                plots.updateTelemetryPlots(startDist);
            });
            if (state.sortMode === 'turn') {
                import('./lap_selection.js').then(ui => {
                    ui.renderLapList();
                    ui.updateFastestSelectedLap();
                });
            }


            state.map.data.forEach(feature => state.map.data.remove(feature));
            if (data.geojson) {
                state.map.data.addGeoJson(data.geojson);
            }

            updateTrackLimitsVisibility();
            import('./lap_selection.js').then(ui => {
                ui.updateFastestSelectedLap();
                ui.renderLapList();
            });
            import('./stats_plots.js').then(stats => stats.renderStatsPlots());
            import('./plots_sync.js').then(plots => plots.renderExpandablePlots());
        });
}

export function updateDistanceMarker(targetDist) {
    if (!state.trackData || !state.distanceMarker) return;
    const points = state.trackData.center_line;
    state.currentTargetDist = targetDist;
    debouncedUpdateURL();

    import('./plots_sync.js').then(plots => {
        plots.updateExpandablePlotsIndicator();
        plots.updateTelemetryPlots(targetDist);
    });

    let idx = findSegmentIndex(points, targetDist);
    let p1, p2;

    if (idx !== -1) {
        p1 = points[idx];
        p2 = points[idx + 1];
    } else {
        p1 = points[0];
        p2 = points[1];
    }

    if (!p1 || !p2 || p1.dist === undefined || p2.dist === undefined || p1.dist === null || p2.dist === null) {
        return;
    }

    const fraction = (targetDist - p1.dist) / (p2.dist - p1.dist || 0.0001);
    const pos = google.maps.geometry.spherical.interpolate(
        new google.maps.LatLng(p1.lat, p1.lng),
        new google.maps.LatLng(p2.lat, p2.lng),
        fraction
    );

    let h1 = p1.heading;
    let h2 = p2.heading;
    if (h1 === undefined || h2 === undefined) {
        h1 = h2 = google.maps.geometry.spherical.computeHeading(p1, p2);
    }

    let diff = h2 - h1;
    while (diff > 180) diff -= 360;
    while (diff < -180) diff += 360;
    const heading = h1 + diff * fraction;
    const halfWidth = 7.5;

    const left = google.maps.geometry.spherical.computeOffset(pos, halfWidth, heading - 90);
    const right = google.maps.geometry.spherical.computeOffset(pos, halfWidth, heading + 90);

    state.distanceMarker.setPath([left, right]);
}
