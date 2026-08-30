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

import { parseLapTime, attachSmoothWheelZoom } from './utils.js';

const DEFAULT_COLORS = ['#10b981', '#f43f5e', '#38bdf8', '#f59e0b', '#a855f7', '#ec4899', '#14b8a6'];

export const SolidBackgroundMapType = function () {
    if (typeof google !== 'undefined' && google.maps && google.maps.Size) {
        this.tileSize = new google.maps.Size(256, 256);
    } else {
        this.tileSize = { width: 256, height: 256 };
    }
    this.maxZoom = 21;
    this.name = 'Track Limits';
    this.alt = 'Show track limits on solid background';
};

SolidBackgroundMapType.prototype.getTile = function (coord, zoom, ownerDocument) {
    const doc = ownerDocument || (typeof document !== 'undefined' ? document : null);
    const div = doc ? doc.createElement('div') : { style: {} };
    const w = (this.tileSize && this.tileSize.width) ? this.tileSize.width : 256;
    const h = (this.tileSize && this.tileSize.height) ? this.tileSize.height : 256;
    div.style.width = w + 'px';
    div.style.height = h + 'px';
    div.style.backgroundColor = '#0f172a';
    return div;
};

export class TrajectoryPlot {
    /**
     * Render an interactive Google Maps trajectory plot inside container.
     * @param {string|HTMLElement} container Selector string or DOM element
     * @param {Object} options Configuration options
     */
    static async render(container, options = {}) {
        const plot = new TrajectoryPlot(container, options);
        await plot.init();
        return plot;
    }

    constructor(container, options = {}) {
        this.container = typeof container === 'string' ? document.querySelector(container) : container;
        if (!this.container) {
            throw new Error(`TrajectoryPlot container not found: ${container}`);
        }
        this.options = {
            session_id: options.session_id || '',
            track: options.track || '',
            date: options.date || '',
            league: options.league || '',
            class_name: options.class_name || '',
            laps: Array.isArray(options.laps) ? options.laps : [],
            start_m: (options.start_m !== undefined && options.start_m !== null) ? Number(options.start_m) : (options.start !== undefined && options.start !== null ? Number(options.start) : null),
            end_m: (options.end_m !== undefined && options.end_m !== null) ? Number(options.end_m) : (options.end !== undefined && options.end !== null ? Number(options.end) : null),
            markers: Array.isArray(options.markers) ? options.markers : [],
            lap_styles: options.lap_styles || {},
            data: options.data || null,
            width: options.width || '100%',
            height: options.height || '600px',
            map_type: options.map_type || 'satellite',
            theme: options.theme || 'dark',
            color_mode: options.color_mode || 'delta_t',
            reference_lap: options.reference_lap !== undefined ? options.reference_lap : null
        };

        this.map = null;
        this.polylines = [];
        this.lapPolylines = {};
        this.googleMarkers = [];
        this.lapsData = [];
        this.currentMapType = this.options.map_type;
        this.currentColorMode = this.options.color_mode;
        this.legendElement = null;
    }

    async init() {
        this.lapsData = await this._loadData();
        this._renderPlot();
    }

    _parseLapSpec(item) {
        const spec = {};
        if (Array.isArray(item) && item.length >= 4) {
            spec.date = String(item[0]);
            spec.track = String(item[1]);
            spec.session_id = String(item[2]);
            spec.lap = Number(item[3]);
            if (item[4]) spec.label = String(item[4]);
            if (item[5]) spec.color = String(item[5]);
        } else if (typeof item === 'object' && item !== null) {
            spec.date = String(item.date || this.options.date || '');
            spec.track = String(item.track || this.options.track || '');
            spec.session_id = String(item.session_id || this.options.session_id || '');
            spec.lap = Number(item.lap !== undefined ? item.lap : (item.lap_num !== undefined ? item.lap_num : 0));
            if (item.label) spec.label = String(item.label);
            if (item.color) spec.color = String(item.color);
        } else {
            spec.date = String(this.options.date || '');
            spec.track = String(this.options.track || '');
            spec.session_id = String(this.options.session_id || '');
            spec.lap = Number(item);
        }
        spec.key = spec.key || `${spec.session_id}_L${spec.lap}`;
        return spec;
    }

    _resolveReferenceLap() {
        if (!this.lapsData || this.lapsData.length === 0) return null;

        if (this.options.reference_lap !== undefined && this.options.reference_lap !== null) {
            const spec = this._parseLapSpec(this.options.reference_lap);
            const match = this.lapsData.find(lap => {
                if (spec.session_id && lap.session_id && lap.session_id !== spec.session_id && !lap.session_id.startsWith(spec.session_id)) {
                    return false;
                }
                if (spec.date && lap.date && lap.date !== spec.date) {
                    return false;
                }
                if (spec.track && lap.track && lap.track !== spec.track) {
                    return false;
                }
                return Number(lap.lap_num) === Number(spec.lap);
            });
            if (match) return match;
        }

        // Default to the first lap in lapsData (which maps to options.laps[0] Benchmark Lap)
        return this.lapsData[0];
    }

    async _loadData() {
        if (this.options.data) {
            return this._normalizeInputData(this.options.data);
        }

        const rawLaps = this.options.laps || [];
        if (rawLaps.length === 0) {
            return [];
        }

        const lapSpecs = rawLaps.map(item => this._parseLapSpec(item));
        const sessionMap = {};

        for (const spec of lapSpecs) {
            const sKey = `${spec.date}::${spec.track}::${spec.session_id}`;
            if (!sessionMap[sKey]) {
                sessionMap[sKey] = {
                    date: spec.date,
                    track: spec.track,
                    session_id: spec.session_id,
                    specs: []
                };
            }
            sessionMap[sKey].specs.push(spec);
        }

        const loadedLaps = [];
        const league = this.options.league || 'kartsim';
        const className = this.options.class_name || 'iame_waterswift_restricted_cadet_uk';

        for (const sKey of Object.keys(sessionMap)) {
            const sInfo = sessionMap[sKey];
            const params = new URLSearchParams();
            if (league) params.set('league', league);
            if (className) params.set('class_name', className);
            if (sInfo.date) params.set('date', sInfo.date);
            if (sInfo.track) params.set('track', sInfo.track);

            try {
                const res = await fetch(`/api/telemetry?${params.toString()}`, { credentials: 'include' });
                if (!res.ok) continue;
                const rawSessions = await res.json();
                if (!Array.isArray(rawSessions) || rawSessions.length === 0) continue;

                let targetSession = rawSessions.find(s => s.session_id === sInfo.session_id || s.session_id.includes(sInfo.session_id)) || rawSessions[0];
                const laps = targetSession.laps || [];
                const cols = targetSession.columns || [];
                const csvFilename = targetSession.session_id;

                const xCol = this._findCol(cols, ['Longitude', 'lon', 'x', 'lat', 'Latitude']);
                const yCol = this._findCol(cols, ['Latitude', 'lat', 'z', 'y', 'Longitude']);
                const distCol = this._findCol(cols, ['Lap Distance (m)', 'Lap Distance', 'LapDistance']);
                const timeCol = this._findCol(cols, ['Time']);
                const speedCol = this._findCol(cols, ['Speed', 'Speed (km/h)']);
                const throttleCol = this._findCol(cols, ['Throttle (%)', 'Throttle', 'throttle']);
                const brakeCol = this._findCol(cols, ['Brake (%)', 'Brake', 'brake']);
                const glonCol = this._findCol(cols, ['GForceLon', 'gforcelon']);

                const [xArr, yArr, distArr, timeArr, speedArr, throttleArr, brakeArr, glonArr] = await Promise.all([
                    this._fetchChannel(csvFilename, xCol),
                    this._fetchChannel(csvFilename, yCol),
                    this._fetchChannel(csvFilename, distCol),
                    this._fetchChannel(csvFilename, timeCol),
                    this._fetchChannel(csvFilename, speedCol),
                    this._fetchChannel(csvFilename, throttleCol),
                    this._fetchChannel(csvFilename, brakeCol),
                    this._fetchChannel(csvFilename, glonCol)
                ]);

                if (!xArr || !yArr) continue;

                for (const spec of sInfo.specs) {
                    const l = laps.find(lap => Number(lap.lap_num || lap.lap) === spec.lap);
                    if (!l) continue;

                    const startIdx = l.start_idx !== undefined ? l.start_idx : 0;
                    const endIdx = l.end_idx !== undefined ? l.end_idx : (xArr.length - 1);
                    const pts = [];

                    for (let i = startIdx; i <= endIdx && i < xArr.length; i++) {
                        if (xArr[i] !== null && yArr[i] !== null && !isNaN(xArr[i]) && !isNaN(yArr[i])) {
                            const speedVal = speedArr && speedArr[i] !== null ? speedArr[i] : 0;
                            const timeVal = timeArr && timeArr[i] !== null ? timeArr[i] : 0;
                            const glonVal = glonArr && glonArr[i] !== null ? glonArr[i] : null;

                            let accVal = 0;
                            if (glonVal !== null) {
                                accVal = glonVal * 9.81;
                            } else if (i > startIdx && timeArr && speedArr) {
                                const dt = (timeVal - timeArr[i - 1]) / 1000.0;
                                const dv = (speedVal - speedArr[i - 1]) / 3.6;
                                accVal = dt > 0.001 ? dv / dt : 0;
                            }

                            pts.push({
                                x: xArr[i],
                                y: yArr[i],
                                dist: distArr && distArr[i] !== null ? distArr[i] : i,
                                time: timeVal,
                                speed: speedVal,
                                throttle: throttleArr && throttleArr[i] !== null ? throttleArr[i] : 0,
                                brake: brakeArr && brakeArr[i] !== null ? brakeArr[i] : 0,
                                g_lon: glonVal,
                                acc: accVal
                            });
                        }
                    }

                    if (pts.length > 0) {
                        loadedLaps.push({
                            lap_num: spec.lap,
                            session_id: spec.session_id,
                            date: spec.date,
                            track: spec.track,
                            label: spec.label,
                            color: spec.color,
                            lap_time: l.lap_time || '',
                            points: pts
                        });
                    }
                }
            } catch (err) {
                console.warn('TrajectoryPlot session load error:', err);
            }
        }
        return loadedLaps;
    }

    _findCol(availableCols, candidates) {
        for (let c = 0; c < candidates.length; c++) {
            for (let a = 0; a < availableCols.length; a++) {
                if (availableCols[a].toLowerCase() === candidates[c].toLowerCase()) {
                    return availableCols[a];
                }
            }
        }
        return candidates[0];
    }

    _parseBinaryFloat64(arrayBuffer) {
        if (!arrayBuffer || arrayBuffer.byteLength < 16) {
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

    async _fetchChannel(csvFilename, channelName) {
        if (!channelName) return null;
        const league = this.options.league || 'kartsim';
        const className = this.options.class_name || 'iame_waterswift_restricted_cadet_uk';
        const track = this.options.track;
        const date = this.options.date;
        const apiBase = this.options.api_base || '';

        const url = `${apiBase}/api/telemetry/channel?league=${encodeURIComponent(league)}&class_name=${encodeURIComponent(className)}&date=${encodeURIComponent(date)}&track=${encodeURIComponent(track)}&session_id=${encodeURIComponent(csvFilename)}&channel=${encodeURIComponent(channelName)}`;

        try {
            const res = await fetch(url, { credentials: 'include' });
            if (!res.ok) return null;
            const buffer = await res.arrayBuffer();
            return this._parseBinaryFloat64(buffer);
        } catch (err) {
            console.warn(`Failed to fetch channel ${channelName}`, err);
            return null;
        }
    }

    _normalizeInputData(rawData) {
        let sessions = Array.isArray(rawData) ? rawData : [rawData];
        if (rawData && rawData.laps && Array.isArray(rawData.laps)) {
            sessions = [rawData];
        }

        const normalizedLaps = [];
        const lapSpecs = (this.options.laps || []).map(l => this._parseLapSpec(l));

        for (const s of sessions) {
            const laps = s.laps || (s.points ? [s] : []);
            for (const l of laps) {
                const lapNum = Number(l.lap_num !== undefined ? l.lap_num : (l.lap !== undefined ? l.lap : 0));
                const lapSessionId = l.session_id || s.session_id || this.options.session_id || '';
                const lapDate = l.date || s.date || this.options.date || '';
                const lapTrack = l.track || s.track || this.options.track || '';

                let matchedSpec = null;
                if (lapSpecs.length === 0) {
                    matchedSpec = true;
                } else {
                    matchedSpec = lapSpecs.find(spec => {
                        if (spec.session_id && lapSessionId && lapSessionId !== spec.session_id && !lapSessionId.startsWith(spec.session_id)) {
                            return false;
                        }
                        if (spec.date && lapDate && lapDate !== spec.date) {
                            return false;
                        }
                        if (spec.track && lapTrack && lapTrack !== spec.track) {
                            return false;
                        }
                        return Number(spec.lap) === lapNum;
                    });
                }

                if (matchedSpec) {
                    const points = this._extractPoints(l.points || l);
                    if (points.length > 0) {
                        normalizedLaps.push({
                            lap_num: lapNum,
                            session_id: lapSessionId,
                            date: lapDate,
                            track: lapTrack,
                            label: (matchedSpec && typeof matchedSpec === 'object' && matchedSpec.label) || l.label || s.label || '',
                            color: (matchedSpec && typeof matchedSpec === 'object' && matchedSpec.color) || l.color || s.color || '',
                            lap_time: l.lap_time || s.lap_time || '',
                            points: points
                        });
                    }
                }
            }
        }
        return normalizedLaps;
    }

    _extractPoints(pointsData) {
        if (!Array.isArray(pointsData)) return [];
        const pts = [];
        for (let i = 0; i < pointsData.length; i++) {
            const p = pointsData[i];
            let x = p.Longitude !== undefined ? p.Longitude : (p.lon !== undefined ? p.lon : (p.lng !== undefined ? p.lng : (p.x !== undefined ? p.x : null)));
            let y = p.Latitude !== undefined ? p.Latitude : (p.lat !== undefined ? p.lat : (p.y !== undefined ? p.y : (p.z !== undefined ? p.z : null)));
            let dist = p.dist !== undefined ? p.dist : (p.distance_m !== undefined ? p.distance_m : (p.dist_m !== undefined ? p.dist_m : i));
            let time = p.time !== undefined ? p.time : (p.t !== undefined ? p.t : 0);
            let speed = p.speed !== undefined ? p.speed : 0;
            let throttle = p.throttle !== undefined ? p.throttle : 0;
            let brake = p.brake !== undefined ? p.brake : 0;
            let acc = p.acc !== undefined ? p.acc : (p.g_lon !== undefined ? p.g_lon * 9.81 : 0);

            if (x !== null && y !== null && !isNaN(x) && !isNaN(y)) {
                pts.push({
                    x: Number(x),
                    y: Number(y),
                    dist: Number(dist),
                    time: Number(time),
                    speed: Number(speed),
                    throttle: Number(throttle),
                    brake: Number(brake),
                    acc: Number(acc)
                });
            }
        }
        return pts;
    }

    _cropPoints(points) {
        if (points.length === 0) return points;
        const startM = this.options.start_m;
        const endM = this.options.end_m;

        if (startM === null && endM === null) return points;

        const maxDist = points[points.length - 1].dist;
        const minM = startM !== null ? startM : 0;
        const maxM = endM !== null ? endM : maxDist;

        if (minM <= maxM) {
            return points.filter(p => p.dist >= minM && p.dist <= maxM);
        } else {
            return points.filter(p => p.dist >= minM || p.dist <= maxM);
        }
    }

    _renderPlot() {
        this.container.innerHTML = '';
        this.container.style.position = 'relative';

        const wrapper = document.createElement('div');
        wrapper.className = 'trajectory-plot-wrapper';
        wrapper.style.width = '100%';
        wrapper.style.height = this.options.height;
        wrapper.style.background = this.options.theme === 'light' ? '#f8fafc' : '#090d16';
        wrapper.style.borderRadius = '12px';
        wrapper.style.overflow = 'hidden';
        wrapper.style.position = 'relative';

        let allPoints = [];
        for (const lap of this.lapsData) {
            allPoints = allPoints.concat(lap.points || []);
        }

        if (allPoints.length === 0) {
            const emptyMsg = document.createElement('div');
            emptyMsg.textContent = 'No trajectory points available for selected lap(s)';
            emptyMsg.style.color = '#94a3b8';
            emptyMsg.style.textAlign = 'center';
            emptyMsg.style.paddingTop = '40px';
            wrapper.appendChild(emptyMsg);
            this.container.appendChild(wrapper);
            return;
        }

        if (typeof google === 'undefined' || !google.maps || !google.maps.Map) {
            const warnMsg = document.createElement('div');
            warnMsg.textContent = 'Google Maps API is required to render trajectory map';
            warnMsg.style.color = '#f87171';
            warnMsg.style.textAlign = 'center';
            warnMsg.style.paddingTop = '40px';
            wrapper.appendChild(warnMsg);
            this.container.appendChild(wrapper);
            return;
        }

        const mapDiv = document.createElement('div');
        mapDiv.style.width = '100%';
        mapDiv.style.height = '100%';
        wrapper.appendChild(mapDiv);
        this.container.appendChild(wrapper);

        this.map = new google.maps.Map(mapDiv, {
            mapTypeId: (this.currentMapType === 'kartsim') ? 'solid_dark' : 'satellite',
            gestureHandling: 'greedy',
            scrollwheel: false,
            isFractionalZoomEnabled: true,
            disableDefaultUI: false,
            zoomControl: true,
            mapTypeControl: false,
            scaleControl: true,
            streetViewControl: false,
            fullscreenControl: true
        });

        attachSmoothWheelZoom(this.map, mapDiv);

        if (this.map.mapTypes) {
            this.map.mapTypes.set('solid_dark', new SolidBackgroundMapType());
        }

        if (this.map.data) {
            this.map.data.setStyle({
                strokeColor: '#38bdf8',
                strokeOpacity: 0.8,
                strokeWeight: 2,
                fillColor: '#38bdf8',
                fillOpacity: 0.15
            });

            if (typeof this.map.data.setMap === 'function') {
                if (this.currentMapType === 'satellite') {
                    this.map.data.setMap(null);
                } else {
                    this.map.data.setMap(this.map);
                }
            }
        }

        if (this.options.track) {
            const apiBase = this.options.api_base || '';
            const trackUrl = `${apiBase}/api/track_data?track=${encodeURIComponent(this.options.track)}`;
            fetch(trackUrl, { credentials: 'include' })
                .then(res => res.ok ? res.json() : null)
                .then(data => {
                    if (data && data.geojson && this.map && this.map.data) {
                        this.map.data.addGeoJson(data.geojson);
                    }
                })
                .catch(err => console.warn('TrajectoryPlot: Failed to load track limits GeoJSON', err));
        }

        // Fit initial bounds: if start_m/end_m specified, zoom into corner; otherwise full track
        const bounds = new google.maps.LatLngBounds();
        let hasBoundsPoints = false;

        this.lapsData.forEach(lap => {
            const pts = (this.options.start_m !== null || this.options.end_m !== null)
                ? this._cropPoints(lap.points || [])
                : (lap.points || []);
            pts.forEach(p => {
                bounds.extend({ lat: p.y, lng: p.x });
                hasBoundsPoints = true;
            });
        });

        if (hasBoundsPoints && !bounds.isEmpty()) {
            this.map.fitBounds(bounds);
        }

        // Draw full-lap polylines
        this._drawPolylines();

        // Markers
        this._renderMarkers();

        // UI Controls & Legend
        this._renderMapTypeSelector(wrapper);
        this._renderColorModeSelector(wrapper);
        this._renderLegend(wrapper);
    }

    _drawPolylines() {
        if (this.polylines) {
            this.polylines.forEach(p => {
                if (p && typeof p.setMap === 'function') {
                    p.setMap(null);
                }
            });
        }
        this.polylines = [];
        this.lapPolylines = {};

        if (!this.map || !this.lapsData || this.lapsData.length === 0) return;

        const colorMode = (this.currentColorMode || 'delta_t').toLowerCase();

        let minSpeed = Infinity;
        let maxSpeed = -Infinity;
        this.lapsData.forEach(lap => {
            (lap.points || []).forEach(p => {
                const s = p.speed || 0;
                if (s < minSpeed) minSpeed = s;
                if (s > maxSpeed) maxSpeed = s;
            });
        });

        const fastestLap = this._resolveReferenceLap();

        this.lapsData.forEach((lap, idx) => {
            const lapNum = lap.lap_num;
            const style = this.options.lap_styles[lapNum] || {};
            const color = style.color || DEFAULT_COLORS[idx % DEFAULT_COLORS.length];
            const strokeWidth = style.width || 4;
            const points = lap.points || [];

            if (points.length === 0) return;

            this.lapPolylines[lapNum] = [];

            if (colorMode === 'lap' || points.length < 2) {
                const path = points.map(p => ({ lat: p.y, lng: p.x }));
                const polyline = new google.maps.Polyline({
                    path: path,
                    geodesic: true,
                    strokeColor: color,
                    strokeOpacity: 0.95,
                    strokeWeight: strokeWidth,
                    zIndex: 10,
                    map: this.map
                });
                this.polylines.push(polyline);
                this.lapPolylines[lapNum].push({
                    polyline,
                    defaultWeight: strokeWidth,
                    defaultOpacity: 0.95,
                    defaultZIndex: 10
                });
            } else {
                for (let i = 0; i < points.length - 1; i++) {
                    const p1 = points[i];
                    const p2 = points[i + 1];
                    const segColor = this._calculateSegmentColor(p1, color, colorMode, maxSpeed, minSpeed, lap, fastestLap, p2);
                    const isRef = (colorMode === 'delta_t' && (lap === fastestLap || (fastestLap && lap.lap_num === fastestLap.lap_num && lap.session_id === fastestLap.session_id)));
                    const defaultZIndex = isRef ? 5 : 10;
                    const defaultOpacity = (colorMode === 'time') ? 0.45 : 0.95;
                    const defaultWeight = (colorMode === 'time') ? Math.max(1, Math.round(strokeWidth / 2)) : strokeWidth;

                    const segmentPolyline = new google.maps.Polyline({
                        path: [{ lat: p1.y, lng: p1.x }, { lat: p2.y, lng: p2.x }],
                        geodesic: true,
                        strokeColor: segColor,
                        strokeOpacity: defaultOpacity,
                        strokeWeight: defaultWeight,
                        zIndex: defaultZIndex,
                        map: this.map
                    });
                    this.polylines.push(segmentPolyline);
                    this.lapPolylines[lapNum].push({
                        polyline: segmentPolyline,
                        defaultWeight: defaultWeight,
                        defaultOpacity: defaultOpacity,
                        defaultZIndex
                    });
                }
            }
        });
    }

    _renderMapTypeSelector(wrapper) {
        const controls = document.createElement('div');
        controls.className = 'map-overlay-controls';
        controls.style.position = 'absolute';
        controls.style.top = '12px';
        controls.style.left = '12px';
        controls.style.zIndex = '10';

        const selector = document.createElement('div');
        selector.className = 'custom-selector';
        selector.id = 'map-type-selector';

        const types = [
            { id: 'satellite', label: 'Satellite' },
            { id: 'kartsim', label: 'Track Limits' },
            { id: 'hybrid', label: 'Hybrid' }
        ];

        types.forEach(t => {
            const item = document.createElement('div');
            item.className = 'selector-item' + (this.currentMapType === t.id ? ' active' : '');
            item.setAttribute('data-type', t.id);
            item.textContent = t.label;
            item.addEventListener('click', () => {
                this.setMapType(t.id);
            });
            selector.appendChild(item);
        });

        controls.appendChild(selector);
        wrapper.appendChild(controls);
    }

    _renderColorModeSelector(wrapper) {
        const controls = document.createElement('div');
        controls.className = 'map-overlay-controls map-overlay-right';
        controls.style.position = 'absolute';
        controls.style.top = '12px';
        controls.style.right = '12px';
        controls.style.zIndex = '10';

        const dropdown = document.createElement('div');
        dropdown.className = 'custom-dropdown';

        const modes = [
            { id: 'delta_t', label: 'Δ t', dotClass: 'mode-delta-t' },
            { id: 'time', label: 'Time', dotClass: 'mode-time' },
            { id: 'pedals', label: 'Brake/Throttle', dotClass: 'mode-pedals' },
            { id: 'speed', label: 'Speed', dotClass: 'mode-speed' },
            { id: 'accel', label: 'Acceleration', dotClass: 'mode-accel' },
            { id: 'lap', label: 'Lap Colors', dotClass: 'mode-lap' }
        ];

        const activeMode = modes.find(m => m.id === this.currentColorMode) || modes[0];

        const triggerBtn = document.createElement('button');
        triggerBtn.className = 'dropdown-trigger-btn';
        triggerBtn.title = 'Trajectory Color Mode';
        triggerBtn.innerHTML = `
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="13.5" cy="6.5" r=".5" fill="currentColor"/>
                <circle cx="17.5" cy="10.5" r=".5" fill="currentColor"/>
                <circle cx="8.5" cy="7.5" r=".5" fill="currentColor"/>
                <circle cx="6.5" cy="12.5" r=".5" fill="currentColor"/>
                <path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.92 0 1.7-.72 1.7-1.61 0-.43-.17-.83-.44-1.13-.24-.26-.4-.63-.4-1.04 0-.84.67-1.5 1.5-1.5H16c3.31 0 6-2.69 6-6 0-4.96-4.49-9-10-9z"/>
            </svg>
            <span class="traj-color-current-label">${activeMode.label}</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <path d="M6 9l6 6 6-6"/>
            </svg>
        `;

        triggerBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            dropdown.classList.toggle('open');
        });

        if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
            document.addEventListener('click', () => {
                dropdown.classList.remove('open');
            });
        }

        const menu = document.createElement('div');
        menu.className = 'dropdown-menu';

        modes.forEach(m => {
            const item = document.createElement('div');
            item.className = 'dropdown-item' + (this.currentColorMode === m.id ? ' active' : '');
            item.setAttribute('data-mode', m.id);
            item.innerHTML = `
                <span class="item-color-dot ${m.dotClass}"></span>
                <span>${m.label}</span>
            `;
            item.addEventListener('click', (e) => {
                e.stopPropagation();
                dropdown.classList.remove('open');
                this.setColorMode(m.id);
            });
            menu.appendChild(item);
        });

        dropdown.appendChild(triggerBtn);
        dropdown.appendChild(menu);
        controls.appendChild(dropdown);
        wrapper.appendChild(controls);
    }

    _renderLegend(wrapper) {
        if (!this.lapsData || this.lapsData.length === 0) return;

        const legend = document.createElement('div');
        legend.className = 'trajectory-legend';
        legend.style.position = 'absolute';
        legend.style.top = '58px';
        legend.style.left = '12px';
        legend.style.background = 'rgba(15, 23, 42, 0.85)';
        legend.style.backdropFilter = 'blur(8px)';
        legend.style.padding = '8px 12px';
        legend.style.borderRadius = '8px';
        legend.style.border = '1px solid rgba(255, 255, 255, 0.1)';
        legend.style.color = '#f8fafc';
        legend.style.fontSize = '12px';
        legend.style.zIndex = '10';
        legend.style.boxShadow = '0 4px 16px rgba(0,0,0,0.4)';
        legend.style.maxWidth = '300px';

        this.lapsData.forEach((lap, idx) => {
            const lapNum = lap.lap_num;
            const style = this.options.lap_styles[lapNum] || {};
            const color = style.color || DEFAULT_COLORS[idx % DEFAULT_COLORS.length];
            const label = style.label || `Lap ${lapNum}` + (lap.lap_time ? ` (${lap.lap_time})` : '');

            const row = document.createElement('div');
            row.className = 'trajectory-legend-item';
            row.style.display = 'flex';
            row.style.alignItems = 'center';
            row.style.padding = '4px 6px';
            row.style.borderRadius = '4px';
            row.style.cursor = 'pointer';
            row.style.transition = 'all 0.15s ease';
            row.setAttribute('data-lap', lapNum);

            const box = document.createElement('span');
            box.style.width = '14px';
            box.style.height = '4px';
            box.style.background = color;
            box.style.marginRight = '8px';
            box.style.borderRadius = '2px';
            box.style.flexShrink = '0';

            const txt = document.createElement('span');
            txt.textContent = label;

            row.appendChild(box);
            row.appendChild(txt);

            row.addEventListener('mouseenter', () => {
                this.highlightLap(lapNum, true);
                row.style.background = 'rgba(56, 189, 248, 0.2)';
            });

            row.addEventListener('mouseleave', () => {
                this.highlightLap(lapNum, false);
                row.style.background = 'transparent';
            });

            legend.appendChild(row);
        });

        wrapper.appendChild(legend);
        this.legendElement = legend;
    }

    highlightLap(lapNum, active) {
        Object.keys(this.lapPolylines).forEach(key => {
            const isTarget = Number(key) === Number(lapNum);
            const polyList = this.lapPolylines[key] || [];

            polyList.forEach(entry => {
                const p = entry.polyline;
                if (!p || typeof p.setOptions !== 'function') return;

                if (active) {
                    if (isTarget) {
                        p.setOptions({
                            strokeWeight: 8,
                            strokeOpacity: 1.0,
                            zIndex: 1000
                        });
                    } else {
                        p.setOptions({
                            strokeWeight: 2.5,
                            strokeOpacity: 0.35,
                            zIndex: 1
                        });
                    }
                } else {
                    p.setOptions({
                        strokeWeight: entry.defaultWeight || 4,
                        strokeOpacity: entry.defaultOpacity || 0.95,
                        zIndex: entry.defaultZIndex || 10
                    });
                }
            });
        });
    }

    setMapType(type) {
        this.currentMapType = type;
        if (this.container) {
            this.container.querySelectorAll('.selector-item').forEach(item => {
                if (item.getAttribute('data-type') === type) {
                    item.classList.add('active');
                } else {
                    item.classList.remove('active');
                }
            });
        }
        if (this.map) {
            if (type === 'satellite' || type === 'hybrid') {
                this.map.setMapTypeId('satellite');
            } else if (type === 'kartsim') {
                this.map.setMapTypeId('solid_dark');
            }
            if (this.map.data && typeof this.map.data.setMap === 'function') {
                this.map.data.setMap(type === 'satellite' ? null : this.map);
            }
        }
    }

    setColorMode(mode) {
        this.currentColorMode = mode;
        this.options.color_mode = mode;

        if (this.container) {
            const labelEl = this.container.querySelector('.traj-color-current-label');
            const modes = [
                { id: 'delta_t', label: 'Δ t' },
                { id: 'pedals', label: 'Brake/Throttle' },
                { id: 'speed', label: 'Speed' },
                { id: 'accel', label: 'Acceleration' },
                { id: 'lap', label: 'Lap Colors' }
            ];
            const found = modes.find(m => m.id === mode);
            if (labelEl && found) labelEl.textContent = found.label;

            this.container.querySelectorAll('.dropdown-item').forEach(item => {
                if (item.getAttribute('data-mode') === mode) {
                    item.classList.add('active');
                } else {
                    item.classList.remove('active');
                }
            });
        }

        this._drawPolylines();
    }

    _interpolateMultiStopColor(val, stops) {
        val = Math.max(0, Math.min(1, val));
        if (val <= stops[0][0]) {
            const c = stops[0][1];
            return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
        }
        for (let i = 0; i < stops.length - 1; i++) {
            const u1 = stops[i][0];
            const c1 = stops[i][1];
            const u2 = stops[i + 1][0];
            const c2 = stops[i + 1][1];
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

    _calculateSegmentColor(p, defaultColor, mode, maxSpeed = 100, minSpeed = 0, lap = null, fastestLap = null, p2 = null) {
        if (!p) return defaultColor || '#ffffff';
        const currentMode = mode || this.currentColorMode || this.options.color_mode || 'lap';

        if (currentMode === 'delta_t') {
            if (!fastestLap || !fastestLap.points || fastestLap.points.length === 0 || lap === fastestLap || (lap && fastestLap && lap.lap_num === fastestLap.lap_num && lap.session_id === fastestLap.session_id)) {
                return '#ffffff';
            }
            const s1 = p.dist !== undefined && p.dist !== null ? p.dist : 0;
            const s2 = (p2 && p2.dist !== undefined && p2.dist !== null && p2.dist > s1) ? p2.dist : (s1 + 1);
            const sMid = (s1 + s2) / 2;

            const halfWindow = 5.0;
            const wStart = Math.max(0, sMid - halfWindow);
            const wEnd = sMid + halfWindow;

            const pLap1 = this._findPointByDistance(lap.points, wStart);
            const pLap2 = this._findPointByDistance(lap.points, wEnd);
            const pRef1 = this._findPointByDistance(fastestLap.points, wStart);
            const pRef2 = this._findPointByDistance(fastestLap.points, wEnd);

            let rate = 0;
            if (pLap1 && pLap2 && pRef1 && pRef2 && typeof pLap1.time === 'number' && typeof pLap2.time === 'number' && typeof pRef1.time === 'number' && typeof pRef2.time === 'number') {
                const dtLap = pLap2.time - pLap1.time;
                const dtRef = pRef2.time - pRef1.time;
                if (dtLap > 0 && dtRef > 0) {
                    rate = (dtLap - dtRef) / dtRef;
                }
            } else {
                const refPt = this._findPointByDistance(fastestLap.points, sMid);
                const refSpeed = refPt ? refPt.speed : null;
                const lapSpeed = p.speed;
                if (lapSpeed && lapSpeed > 0 && refSpeed && refSpeed > 0) {
                    rate = (refSpeed - lapSpeed) / lapSpeed;
                }
            }

            const DELTA_T_STOPS = [
                [0.0, [0, 255, 0]],     // -maxRate: Pure Green
                [0.5, [255, 255, 0]],   //  0.0:      Pure Yellow
                [1.0, [255, 0, 0]]      // +maxRate: Pure Red
            ];
            const maxRate = 0.25;
            const s0 = 0.025;
            const asinhMax = Math.asinh(maxRate / s0);
            const normalizedRate = Math.max(-1, Math.min(1, Math.asinh(rate / s0) / asinhMax));
            const u = 0.5 + 0.5 * normalizedRate;
            return this._interpolateMultiStopColor(u, DELTA_T_STOPS);
        }

        if (currentMode === 'time') {
            let validTimes = [];
            if (this.lapsData) {
                this.lapsData.forEach(l => {
                    const t = parseLapTime(l.lap_time);
                    if (t !== null && t !== undefined && !isNaN(t) && t > 0 && t < 999999) {
                        validTimes.push(t);
                    }
                });
            }
            const lapTime = lap ? parseLapTime(lap.lap_time) : null;
            if (lapTime === null || isNaN(lapTime) || lapTime <= 0 || lapTime >= 999999) {
                return '#94a3b8';
            }
            const minTime = validTimes.length > 0 ? Math.min(...validTimes) : lapTime;
            const maxTime = validTimes.length > 0 ? Math.max(...validTimes) : lapTime;
            const TIME_STOPS = [
                [0.0, [0, 255, 0]],
                [0.5, [255, 255, 0]],
                [1.0, [255, 0, 0]]
            ];
            let u = 0.0;
            if (maxTime > minTime) {
                u = Math.max(0, Math.min(1, (lapTime - minTime) / (maxTime - minTime)));
            }
            return this._interpolateMultiStopColor(u, TIME_STOPS);
        }

        const SPEED_STOPS = [
            [0.00, [40, 20, 180]],   // Apex / Minimum speed: Deep Navy/Violet
            [0.25, [0, 200, 255]],   // Low speed / Corner exit: Cyan
            [0.55, [255, 30, 60]],   // Mid speed / Acceleration: Crimson Red
            [0.80, [255, 200, 0]],   // High speed straight: Gold Yellow
            [1.00, [0, 255, 80]]     // Top speed: Neon Green
        ];

        if (currentMode === 'speed') {
            const v = p.speed || 0;
            const range = maxSpeed - minSpeed;
            const u = range > 0 ? Math.max(0, Math.min(1, (v - minSpeed) / range)) : 0.5;
            return this._interpolateMultiStopColor(u, SPEED_STOPS);
        }

        if (currentMode === 'accel') {
            const acc = p.acc !== undefined && p.acc !== null ? p.acc : (p.g_lon !== undefined && p.g_lon !== null ? p.g_lon * 9.81 : 0);
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

        if (currentMode === 'pedals') {
            const brake = p.brake || 0;
            const throttle = p.throttle || 0;
            if (brake > 1.0) {
                const factor = Math.min(1, brake / 100.0);
                const intensity = Math.pow(factor, 0.5);
                const gb = Math.round(255 * (1 - intensity));
                return `rgb(255, ${gb}, ${gb})`;
            } else if (throttle > 1.0) {
                const factor = Math.min(1, throttle / 100.0);
                const intensity = Math.pow(factor, 0.5);
                const rb = Math.round(255 * (1 - intensity));
                return `rgb(${rb}, 255, ${rb})`;
            }
            return '#ffffff';
        }

        return defaultColor || '#10b981';
    }

    _renderMarkers() {
        if (!this.options.markers || this.options.markers.length === 0 || !this.map) return;

        this.options.markers.forEach((m, idx) => {
            const targetLap = this.lapsData.find(l => l.lap_num === Number(m.lap)) || this.lapsData[0];
            if (!targetLap || !targetLap.points || targetLap.points.length === 0) return;

            let closestPoint = null;
            if (m.time !== undefined && m.time !== null) {
                closestPoint = this._findPointByTime(targetLap.points, Number(m.time));
            } else if (m.distance_m !== undefined && m.distance_m !== null) {
                closestPoint = this._findPointByDistance(targetLap.points, Number(m.distance_m));
            }

            if (!closestPoint) return;

            const color = m.color || '#f59e0b';
            const marker = new google.maps.Marker({
                position: { lat: closestPoint.y, lng: closestPoint.x },
                map: this.map,
                title: m.label || `Marker ${idx + 1}`
            });
            this.googleMarkers.push(marker);

            if (m.description || m.label) {
                const infoWindow = new google.maps.InfoWindow({
                    content: `
                        <div style="color: #0f172a; padding: 4px; font-family: sans-serif;">
                            <div style="font-weight: bold; color: ${color}; fontSize: 14px;">${m.label || 'Marker'}</div>
                            ${m.description ? `<div style="font-size: 12px; margin-top: 4px;">${m.description}</div>` : ''}
                            <div style="font-size: 11px; color: #64748b; margin-top: 4px;">Distance: ${Math.round(closestPoint.dist)}m | Time: ${closestPoint.time.toFixed(2)}s</div>
                        </div>
                    `
                });

                marker.addListener('click', () => {
                    infoWindow.open(this.map, marker);
                });
            }
        });
    }

    _findPointByTime(points, targetTime) {
        let best = points[0];
        let minDiff = Math.abs(points[0].time - targetTime);
        for (let i = 1; i < points.length; i++) {
            const diff = Math.abs(points[i].time - targetTime);
            if (diff < minDiff) {
                minDiff = diff;
                best = points[i];
            }
        }
        return best;
    }

    _findPointByDistance(points, targetDist) {
        let best = points[0];
        let minDiff = Math.abs(points[0].dist - targetDist);
        for (let i = 1; i < points.length; i++) {
            const diff = Math.abs(points[i].dist - targetDist);
            if (diff < minDiff) {
                minDiff = diff;
                best = points[i];
            }
        }
        return best;
    }

    updateMarkers(newMarkers) {
        this.options.markers = Array.isArray(newMarkers) ? newMarkers : [];
        this._renderPlot();
    }
}

if (typeof window !== 'undefined') {
    window.TrajectoryPlot = TrajectoryPlot;
    window.renderTrajectoryPlot = TrajectoryPlot.render;
}

