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
import { findSegmentIndex, precalculateLapData, fetchTelemetryChannel } from './telemetry.js';
import { parseLapTime } from '../utils.js';
import { debouncedUpdateURL } from './url_sync.js';

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
        const throttleCol = findCol(['Throttle (%)', 'Throttle']);
        const brakeCol = findCol(['Brake (%)', 'Brake']);

        const colsToFetch = {
            lat: latCol,
            lng: lngCol,
            time: timeCol,
            dist: distCol,
            speed: speedCol,
            throttle: throttleCol,
            brake: brakeCol
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
            if (lapList) lapList.innerHTML = '';
            const bounds = new google.maps.LatLngBounds();

            state.groupASelection.clear();
            state.groupBSelection.clear();

            const params = new URLSearchParams(window.location.search);
            const urlLapsA = params.get('lapsA');
            const urlLapsB = params.get('lapsB');
            let hasCustomLapsA = params.has('lapsA');
            let customLapsA = new Set();
            if (urlLapsA && urlLapsA !== 'none') {
                urlLapsA.split(',').forEach(id => {
                    const trimmed = id ? id.trim() : '';
                    if (trimmed && trimmed !== 'none') {
                        customLapsA.add(trimmed);
                    }
                });
            }
            let hasCustomLapsB = params.has('lapsB');
            let customLapsB = new Set();
            if (urlLapsB && urlLapsB !== 'none') {
                urlLapsB.split(',').forEach(id => {
                    const trimmed = id ? id.trim() : '';
                    if (trimmed && trimmed !== 'none') {
                        customLapsB.add(trimmed);
                    }
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

            sessionsData.forEach((session, sIdx) => {
                if (!session.laps) return;
                session.laps.forEach(lap => {
                    if (lap.is_valid === false) return;
                    const lapId = `${session.session_id}-${lap.lap_num}`;
                    state.lapDataLookup[lapId] = lap;

                    precalculateLapData(lap);

                    if (hasCustomLapsA) {
                        if (customLapsA.has(lapId)) {
                            state.groupASelection.add(lapId);
                        }
                    } else {
                        if (fastest5Ids.has(lapId)) {
                            state.groupASelection.add(lapId);
                        }
                    }
                    if (hasCustomLapsB) {
                        if (customLapsB.has(lapId)) {
                            state.groupBSelection.add(lapId);
                        }
                    } else {
                        if (fastest80Ids.has(lapId)) {
                            state.groupBSelection.add(lapId);
                        }
                    }
                    state.lapPolylines[lapId] = [];

                    const isVisible = (state.groupASelection.has(lapId) && state.groupAVisibleMap) || (state.groupBSelection.has(lapId) && state.groupBVisibleMap);

                    for (let i = 0; i < lap.points.length - 1; i++) {
                        const p1 = lap.points[i];
                        const p2 = lap.points[i + 1];

                        let color = '#ffffff';
                        if (p1.brake > 1.0) {
                            const intensity = Math.pow(p1.brake / 100, 0.2);
                            const factor = 1 - intensity;
                            const gb = Math.round(255 * factor);
                            color = `rgb(255, ${gb}, ${gb})`;
                        } else if (p1.throttle > 1.0) {
                            const factor = (100 - p1.throttle) / 100;
                            const rb = Math.round(255 * factor);
                            color = `rgb(${rb}, 255, ${rb})`;
                        }

                        const segment = new google.maps.Polyline({
                            path: [p1, p2],
                            geodesic: false,
                            strokeColor: color,
                            strokeOpacity: 1.0,
                            strokeWeight: 4,
                            zIndex: 1,
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

            if (!bounds.isEmpty() && state.map && !state.hasCustomMapSet) {
                state.map.fitBounds(bounds);
            }
            state.mapInitialized = true;
            isFetchingTrackPoints = false;

            import('./lap_selection.js').then(ui => {
                ui.updateVisibilityIcons();
                
                const params = new URLSearchParams(window.location.search);
                const urlSort = params.get('sort');
                if (urlSort) {
                    ui.setSort(urlSort);
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
                        if (!isNaN(min) && !isNaN(max)) {
                            state.globalTelemetryXRange = [min, max];
                        }
                    }
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
            state.globalTelemetryXRange = [0, 100];

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
            }
            state.hasCustomMapSet = hasCustomMap;

            state.map = new google.maps.Map(mapContainer, {
                center: customCenter,
                zoom: customZoom,
                mapTypeId: 'hybrid',
                mapId: 'KART_ANALYSIS_MAP',
                tilt: 0,
                gestureHandling: 'greedy',
                streetViewControl: false,
                mapTypeControl: false,
                fullscreenControl: false
            });

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
        state.map.setMapTypeId(type);
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
            const xlimParam = params.get('xlim');
            let hasCustomXlim = false;
            if (xlimParam) {
                const parts = xlimParam.split(',');
                if (parts.length === 2) {
                    const min = parseFloat(parts[0]);
                    const max = parseFloat(parts[1]);
                    if (!isNaN(min) && !isNaN(max)) {
                        state.globalTelemetryXRange = [min, max];
                        hasCustomXlim = true;
                    }
                }
            }
            if (slider && data.lap_length) {
                slider.max = data.lap_length;
                slider.step = 0.01;
                if (!hasCustomXlim) {
                    state.globalTelemetryXRange = [0, data.lap_length];
                }
            }

            let startDist = 0;
            const distParam = params.get('dist');
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
                    
                    const params = new URLSearchParams(window.location.search);
                    const urlTurn = params.get('turn');
                    if (urlTurn !== null) {
                        const turnIdx = parseInt(urlTurn);
                        if (!isNaN(turnIdx) && turnIdx >= 0 && turnIdx < data.turns.length) {
                            turnSelector.value = turnIdx;
                            state.currentTurnIdx = turnIdx;
                        }
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


            state.map.data.forEach(feature => state.map.data.remove(feature));
            if (data.geojson) {
                state.map.data.addGeoJson(data.geojson);
            }

            updateTrackLimitsVisibility();
            import('./stats_plots.js').then(stats => stats.renderStatsPlots());
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
