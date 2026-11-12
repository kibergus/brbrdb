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
 * Workspace GeoJSON vector & HTML5 <canvas> heatmap overlays for the Telemetry Viewer map.
 * Each vector overlay uses its own dedicated google.maps.Data instance so it never collides
 * with track-limits rendering on state.map.data.
 */
import { state } from './state.js';
import { debouncedUpdateURL } from './url_sync.js';

const COLORMAP_STOPS = {
    turbo: [
        [0.0, [48, 18, 59]],
        [0.2, [65, 115, 243]],
        [0.4, [27, 218, 178]],
        [0.6, [164, 252, 60]],
        [0.8, [251, 138, 36]],
        [1.0, [122, 4, 3]]
    ],
    inferno: [
        [0.0, [0, 0, 4]],
        [0.25, [87, 16, 110]],
        [0.5, [188, 55, 84]],
        [0.75, [249, 142, 9]],
        [1.0, [252, 255, 164]]
    ],
    viridis: [
        [0.0, [68, 1, 84]],
        [0.25, [59, 82, 139]],
        [0.5, [33, 145, 140]],
        [0.75, [94, 201, 98]],
        [1.0, [253, 231, 37]]
    ],
    rdylgn: [
        [0.0, [215, 48, 39]],
        [0.25, [252, 141, 89]],
        [0.5, [255, 255, 191]],
        [0.75, [145, 207, 96]],
        [1.0, [26, 152, 80]]
    ],
    coolwarm: [
        [0.0, [59, 76, 192]],
        [0.25, [124, 159, 249]],
        [0.5, [221, 221, 221]],
        [0.75, [244, 152, 122]],
        [1.0, [180, 4, 38]]
    ]
};

const _lutCache = new Map();
let _sharedInfoWindow = null;
let _HeatmapOverlayClass = null;

export function buildColormapLut(colormapName) {
    const key = String(colormapName || 'turbo').toLowerCase();
    if (_lutCache.has(key)) {
        return _lutCache.get(key);
    }
    const stops = COLORMAP_STOPS[key] || COLORMAP_STOPS.turbo;
    const lut = new Array(256);
    for (let i = 0; i < 256; i++) {
        const t = i / 255;
        let lo = stops[0];
        let hi = stops[stops.length - 1];
        for (let s = 0; s < stops.length - 1; s++) {
            if (t >= stops[s][0] && t <= stops[s + 1][0]) {
                lo = stops[s];
                hi = stops[s + 1];
                break;
            }
        }
        const span = hi[0] - lo[0];
        const f = span > 0 ? (t - lo[0]) / span : 0;
        lut[i] = [
            Math.round(lo[1][0] + f * (hi[1][0] - lo[1][0])),
            Math.round(lo[1][1] + f * (hi[1][1] - lo[1][1])),
            Math.round(lo[1][2] + f * (hi[1][2] - lo[1][2]))
        ];
    }
    _lutCache.set(key, lut);
    return lut;
}

export function sampleColormapRgb(colormapName, t) {
    const lut = buildColormapLut(colormapName);
    const clamped = Math.max(0, Math.min(1, Number.isFinite(t) ? t : 0));
    return lut[Math.round(clamped * 255)];
}

export function sampleColormapHex(colormapName, t) {
    const [r, g, b] = sampleColormapRgb(colormapName, t);
    const toHex = (n) => n.toString(16).padStart(2, '0');
    return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

function getCssGradientForColormap(colormapName) {
    const lut = buildColormapLut(colormapName);
    const parts = [];
    for (let i = 0; i <= 8; i++) {
        const idx = Math.min(255, Math.round((i / 8) * 255));
        const [r, g, b] = lut[idx];
        parts.push(`rgb(${r},${g},${b}) ${Math.round((i / 8) * 100)}%`);
    }
    return `linear-gradient(90deg, ${parts.join(', ')})`;
}

function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, (ch) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
    }[ch]));
}

function resolveOverlayEntry(overlayRef) {
    if (!state.agentOverlays || !overlayRef) return null;
    const raw = String(overlayRef).trim();
    if (!raw) return null;
    if (state.agentOverlays.has(raw)) {
        return state.agentOverlays.get(raw);
    }
    const stripped = raw.replace(/\.geojson$/i, '');
    if (state.agentOverlays.has(stripped)) {
        return state.agentOverlays.get(stripped);
    }
    for (const entry of state.agentOverlays.values()) {
        const d = entry.descriptor;
        if (!d) continue;
        if (d.meta_id === raw || d.meta_id === stripped || d.path === raw || d.path === `${stripped}.geojson`) {
            return entry;
        }
    }
    return null;
}

function isOverlayEntryActive(entry) {
    if (!entry || !entry.descriptor || !state.activeOverlayIds) return false;
    const d = entry.descriptor;
    if (state.activeOverlayIds.has(d.id)) return true;
    if (d.meta_id && state.activeOverlayIds.has(d.meta_id)) return true;
    if (d.path && state.activeOverlayIds.has(d.path)) return true;
    for (const reqId of state.activeOverlayIds) {
        const cleaned = String(reqId).trim().replace(/\.geojson$/i, '');
        if (cleaned === d.id || (d.path && d.path.replace(/\.geojson$/i, '') === cleaned)) {
            return true;
        }
    }
    return false;
}

function detachOverlayLayers(entry) {
    if (!entry) return;
    if (entry.dataLayer) {
        try {
            if (typeof entry.dataLayer.setMap === 'function') {
                entry.dataLayer.setMap(null);
            }
        } catch {
            // Ignore teardown errors
        }
        entry.dataLayer = null;
    }
    if (entry.heatmapOverlay) {
        try {
            if (typeof entry.heatmapOverlay.setMap === 'function') {
                entry.heatmapOverlay.setMap(null);
            }
        } catch {
            // Ignore teardown errors
        }
        entry.heatmapOverlay = null;
    }
}

function computeFeatureValueDomain(geojson, valueProp, explicitDomain) {
    if (Array.isArray(explicitDomain) && explicitDomain.length === 2) {
        const d0 = Number(explicitDomain[0]);
        const d1 = Number(explicitDomain[1]);
        if (Number.isFinite(d0) && Number.isFinite(d1)) {
            return [d0, d1];
        }
    }
    let minVal = Infinity;
    let maxVal = -Infinity;
    const features = (geojson && Array.isArray(geojson.features)) ? geojson.features : [];
    for (const feat of features) {
        const props = (feat && feat.properties) || {};
        const v = Number(props[valueProp]);
        if (Number.isFinite(v)) {
            if (v < minVal) minVal = v;
            if (v > maxVal) maxVal = v;
        }
    }
    if (!Number.isFinite(minVal) || !Number.isFinite(maxVal)) {
        return [0, 1];
    }
    if (minVal === maxVal) {
        return [minVal > 0 ? 0 : minVal - 1, maxVal || 1];
    }
    return [minVal, maxVal];
}

function extractHeatmapSamples(geojson, valueProp) {
    const samples = [];
    const features = (geojson && Array.isArray(geojson.features)) ? geojson.features : [];

    const addCoord = (coord, weight) => {
        if (!Array.isArray(coord) || coord.length < 2) return;
        const lng = Number(coord[0]);
        const lat = Number(coord[1]);
        if (Number.isFinite(lat) && Number.isFinite(lng)) {
            samples.push({ lat, lng, weight });
        }
    };

    const addLineCoords = (coords, weight) => {
        if (!Array.isArray(coords) || coords.length === 0) return;
        if (coords.length === 1) {
            addCoord(coords[0], weight);
            return;
        }
        for (let i = 0; i < coords.length - 1; i++) {
            const c0 = coords[i];
            const c1 = coords[i + 1];
            if (!Array.isArray(c0) || !Array.isArray(c1) || c0.length < 2 || c1.length < 2) continue;
            const lng0 = Number(c0[0]);
            const lat0 = Number(c0[1]);
            const lng1 = Number(c1[0]);
            const lat1 = Number(c1[1]);
            if (!Number.isFinite(lat0) || !Number.isFinite(lng0) || !Number.isFinite(lat1) || !Number.isFinite(lng1)) {
                continue;
            }
            const midLatRad = ((lat0 + lat1) * 0.5 * Math.PI) / 180;
            const dyM = (lat1 - lat0) * 111320;
            const dxM = (lng1 - lng0) * 111320 * Math.cos(midLatRad);
            const segLenM = Math.hypot(dxM, dyM);
            const steps = Math.max(1, Math.min(64, Math.ceil(segLenM / 2.0)));
            const endStep = (i === coords.length - 2) ? steps : steps - 1;
            for (let s = 0; s <= endStep; s++) {
                const f = s / steps;
                samples.push({
                    lat: lat0 + f * (lat1 - lat0),
                    lng: lng0 + f * (lng1 - lng0),
                    weight
                });
            }
        }
    };

    for (const feat of features) {
        if (!feat || !feat.geometry) continue;
        const props = feat.properties || {};
        let w = Number(props[valueProp]);
        if (!Number.isFinite(w)) {
            w = Number(props.weight ?? props.value ?? 1.0);
        }
        if (!Number.isFinite(w)) w = 1.0;

        const geom = feat.geometry;
        if (geom.type === 'Point') {
            addCoord(geom.coordinates, w);
        } else if (geom.type === 'MultiPoint' && Array.isArray(geom.coordinates)) {
            geom.coordinates.forEach((c) => addCoord(c, w));
        } else if (geom.type === 'LineString') {
            addLineCoords(geom.coordinates, w);
        } else if (geom.type === 'MultiLineString' && Array.isArray(geom.coordinates)) {
            geom.coordinates.forEach((line) => addLineCoords(line, w));
        }
    }
    return samples;
}

function getHeatmapOverlayClass() {
    if (_HeatmapOverlayClass) return _HeatmapOverlayClass;
    if (typeof google === 'undefined' || !google.maps || !google.maps.OverlayView) {
        return null;
    }

    class AgentCanvasHeatmapOverlay extends google.maps.OverlayView {
        constructor(samples, options) {
            super();
            this.samples = samples || [];
            this.options = options || {};
            this.canvas = null;
            this._mapListeners = [];
        }

        onAdd() {
            const doc = typeof document !== 'undefined' ? document : null;
            if (!doc) return;
            const canvas = doc.createElement('canvas');
            canvas.className = 'agent-heatmap-canvas';
            canvas.style.position = 'absolute';
            canvas.style.pointerEvents = 'none';
            canvas.style.left = '0px';
            canvas.style.top = '0px';
            this.canvas = canvas;

            const panes = typeof this.getPanes === 'function' ? this.getPanes() : null;
            const targetPane = panes && (panes.overlayLayer || panes.overlayMouseTarget || panes.mapPane);
            if (targetPane && typeof targetPane.appendChild === 'function') {
                targetPane.appendChild(canvas);
            }

            const map = typeof this.getMap === 'function' ? this.getMap() : null;
            if (map && typeof map.addListener === 'function') {
                this._mapListeners.push(map.addListener('bounds_changed', () => this.draw()));
                this._mapListeners.push(map.addListener('zoom_changed', () => this.draw()));
            }
        }

        draw() {
            if (!this.canvas) return;
            const projection = typeof this.getProjection === 'function' ? this.getProjection() : null;
            const map = typeof this.getMap === 'function' ? this.getMap() : null;
            if (!projection || !map) return;

            const mapDiv = (typeof map.getDiv === 'function' && map.getDiv()) ||
                           (typeof document !== 'undefined' ? document.getElementById('map') : null);
            const width = Math.max(1, (mapDiv && mapDiv.clientWidth) || 512);
            const height = Math.max(1, (mapDiv && mapDiv.clientHeight) || 512);

            if (this.canvas.width !== width) this.canvas.width = width;
            if (this.canvas.height !== height) this.canvas.height = height;
            this.canvas.style.width = `${width}px`;
            this.canvas.style.height = `${height}px`;

            const centerObj = typeof map.getCenter === 'function' ? map.getCenter() : null;
            const centerLat = centerObj ? (typeof centerObj.lat === 'function' ? centerObj.lat() : Number(centerObj.lat || 0)) : 0;
            const centerLng = centerObj ? (typeof centerObj.lng === 'function' ? centerObj.lng() : Number(centerObj.lng || 0)) : 0;
            const zoom = (typeof map.getZoom === 'function' && Number.isFinite(map.getZoom())) ? map.getZoom() : 17;

            let originX = 0;
            let originY = 0;
            if (typeof projection.fromLatLngToDivPixel === 'function') {
                const centerLatLng = (google.maps.LatLng && !(centerObj && typeof centerObj.lat === 'function'))
                    ? new google.maps.LatLng(centerLat, centerLng)
                    : (centerObj || { lat: () => centerLat, lng: () => centerLng });
                const centerPx = projection.fromLatLngToDivPixel(centerLatLng);
                if (centerPx && Number.isFinite(centerPx.x) && Number.isFinite(centerPx.y)) {
                    originX = Math.round(centerPx.x - width / 2);
                    originY = Math.round(centerPx.y - height / 2);
                }
            }
            this.canvas.style.left = `${originX}px`;
            this.canvas.style.top = `${originY}px`;

            const ctx = typeof this.canvas.getContext === 'function' ? this.canvas.getContext('2d') : null;
            if (!ctx) return;
            if (typeof ctx.clearRect === 'function') {
                ctx.clearRect(0, 0, width, height);
            }
            if (this.samples.length === 0) return;

            let rPx;
            if (Number.isFinite(this.options.radiusPx) && this.options.radiusPx > 0) {
                rPx = this.options.radiusPx;
            } else {
                const radiusM = Number.isFinite(this.options.radiusM) && this.options.radiusM > 0
                    ? this.options.radiusM
                    : 5.0;
                const metersPerPx = Math.max(
                    0.01,
                    (156543.03392 * Math.cos((centerLat * Math.PI) / 180)) / Math.pow(2, zoom)
                );
                rPx = Math.max(2, Math.min(120, radiusM / metersPerPx));
            }

            const sigma = Math.max(1.0, rPx / 2.0);
            const kRad = Math.min(150, Math.max(2, Math.ceil(sigma * 2.5)));
            const twoSigmaSq = 2 * sigma * sigma;

            const kernel1D = new Float32Array(2 * kRad + 1);
            for (let d = -kRad; d <= kRad; d++) {
                kernel1D[d + kRad] = Math.exp(-(d * d) / twoSigmaSq);
            }

            const gridLen = width * height;
            const wSum = new Float32Array(gridLen);
            const kSum = new Float32Array(gridLen);
            const kPeak = new Float32Array(gridLen);

            for (let i = 0; i < this.samples.length; i++) {
                const s = this.samples[i];
                const ll = google.maps.LatLng
                    ? new google.maps.LatLng(s.lat, s.lng)
                    : { lat: () => s.lat, lng: () => s.lng };
                const pt = projection.fromLatLngToDivPixel(ll);
                if (!pt || !Number.isFinite(pt.x) || !Number.isFinite(pt.y)) continue;

                const cx = Math.round(pt.x - originX);
                const cy = Math.round(pt.y - originY);
                if (cx + kRad < 0 || cx - kRad >= width || cy + kRad < 0 || cy - kRad >= height) {
                    continue;
                }

                const yStart = Math.max(0, cy - kRad);
                const yEnd = Math.min(height - 1, cy + kRad);
                const xStart = Math.max(0, cx - kRad);
                const xEnd = Math.min(width - 1, cx + kRad);

                for (let py = yStart; py <= yEnd; py++) {
                    const ky = kernel1D[py - cy + kRad];
                    const rowOffset = py * width;
                    for (let px = xStart; px <= xEnd; px++) {
                        const kVal = ky * kernel1D[px - cx + kRad];
                        if (kVal < 0.01) continue;
                        const idx = rowOffset + px;
                        wSum[idx] += kVal * s.weight;
                        kSum[idx] += kVal;
                        if (kVal > kPeak[idx]) {
                            kPeak[idx] = kVal;
                        }
                    }
                }
            }

            const lut = buildColormapLut(this.options.colormap || 'turbo');
            const [dMin, dMax] = this.options.domain || [0, 1];
            const span = (dMax - dMin) || 1;
            const opacity = Number.isFinite(this.options.opacity) ? this.options.opacity : 0.75;
            const isDensityMode = Boolean(this.options.densityMode);

            let maxKSum = 1;
            if (isDensityMode) {
                for (let i = 0; i < gridLen; i++) {
                    if (kSum[i] > maxKSum) maxKSum = kSum[i];
                }
            }

            const imgData = typeof ctx.createImageData === 'function'
                ? ctx.createImageData(width, height)
                : { data: new Uint8ClampedArray(gridLen * 4), width, height };
            const rgba = imgData.data;

            for (let i = 0; i < gridLen; i++) {
                const ks = kSum[i];
                if (ks < 0.02) continue;
                let t;
                let alphaFactor;
                if (isDensityMode) {
                    t = Math.max(0, Math.min(1, ks / maxKSum));
                    alphaFactor = opacity * Math.min(1, t * 1.15);
                } else {
                    const localVal = wSum[i] / ks;
                    t = Math.max(0, Math.min(1, (localVal - dMin) / span));
                    const env = Math.min(1, kPeak[i] * 1.35);
                    alphaFactor = opacity * env * Math.max(0.3, Math.min(1, 0.35 + 0.65 * t));
                }
                const [r, g, b] = lut[Math.round(t * 255)];
                const off = i * 4;
                rgba[off] = r;
                rgba[off + 1] = g;
                rgba[off + 2] = b;
                rgba[off + 3] = Math.round(Math.max(0, Math.min(1, alphaFactor)) * 255);
            }

            if (typeof ctx.putImageData === 'function') {
                ctx.putImageData(imgData, 0, 0);
            }
            this._lastImageData = imgData;
        }

        onRemove() {
            if (this._mapListeners && this._mapListeners.length > 0) {
                this._mapListeners.forEach((l) => {
                    if (l && typeof l.remove === 'function') l.remove();
                    else if (typeof google !== 'undefined' && google.maps && google.maps.event && google.maps.event.removeListener) {
                        google.maps.event.removeListener(l);
                    }
                });
                this._mapListeners = [];
            }
            if (this.canvas && this.canvas.parentNode) {
                this.canvas.parentNode.removeChild(this.canvas);
            }
            this.canvas = null;
        }
    }

    _HeatmapOverlayClass = AgentCanvasHeatmapOverlay;
    return _HeatmapOverlayClass;
}

function attachVectorLayer(entry) {
    if (!state.map || typeof google === 'undefined' || !google.maps || !google.maps.Data) {
        return;
    }
    const geojson = entry.geojson;
    if (!geojson) return;

    const meta = (geojson.metadata && typeof geojson.metadata === 'object')
        ? geojson.metadata
        : (entry.descriptor.metadata || {});
    const defaultStyle = (meta.style && typeof meta.style === 'object') ? meta.style : {};
    const valueProp = meta.value_property ? String(meta.value_property) : 'value';
    const hasColormap = Boolean(meta.colormap || meta.value_property || meta.domain);
    const colormap = meta.colormap ? String(meta.colormap) : 'turbo';
    const domain = computeFeatureValueDomain(geojson, valueProp, meta.domain);

    entry.computedDomain = domain;
    entry.usesColormap = hasColormap;
    entry.colormap = colormap;
    entry.unit = meta.unit ? String(meta.unit) : '';

    const dataLayer = new google.maps.Data({ map: state.map });
    dataLayer.addGeoJson(geojson);

    dataLayer.setStyle((feature) => {
        const getProp = (k) => (feature && typeof feature.getProperty === 'function' ? feature.getProperty(k) : undefined);

        let mappedHex = null;
        if (hasColormap) {
            const rawV = Number(getProp(valueProp));
            if (Number.isFinite(rawV)) {
                const span = (domain[1] - domain[0]) || 1;
                mappedHex = sampleColormapHex(colormap, (rawV - domain[0]) / span);
            }
        }

        const explicitStroke = getProp('strokeColor') || getProp('stroke') || getProp('color') || defaultStyle.strokeColor || defaultStyle.stroke;
        const explicitFill = getProp('fillColor') || getProp('fill') || defaultStyle.fillColor || defaultStyle.fill;
        const strokeColor = String(explicitStroke || mappedHex || '#38bdf8');
        const fillColor = String(explicitFill || mappedHex || strokeColor);

        const baseOpacity = Number.isFinite(Number(meta.opacity)) ? Number(meta.opacity) : undefined;
        const strokeOpacity = Number(getProp('strokeOpacity') ?? defaultStyle.strokeOpacity ?? baseOpacity ?? 0.9);
        const strokeWeight = Number(getProp('strokeWeight') ?? defaultStyle.strokeWeight ?? 3);
        const fillOpacity = Number(
            getProp('fillOpacity') ?? defaultStyle.fillOpacity ?? (baseOpacity !== undefined ? baseOpacity * 0.45 : 0.35)
        );
        const radius = Number(getProp('radius') ?? defaultStyle.radius ?? 5);
        const zIndex = Number(getProp('zIndex') ?? defaultStyle.zIndex ?? 20);
        const featLabel = getProp('label');

        const circlePath = (google.maps.SymbolPath && google.maps.SymbolPath.CIRCLE !== undefined)
            ? google.maps.SymbolPath.CIRCLE
            : 0;

        const styleObj = {
            strokeColor,
            strokeOpacity,
            strokeWeight,
            fillColor,
            fillOpacity,
            zIndex,
            icon: {
                path: circlePath,
                scale: radius,
                fillColor,
                fillOpacity: Number(getProp('fillOpacity') ?? baseOpacity ?? 0.85),
                strokeColor,
                strokeWeight: Math.max(1, Math.min(2.5, strokeWeight * 0.5))
            }
        };
        if (featLabel !== undefined && featLabel !== null && String(featLabel).trim() !== '') {
            styleObj.title = String(featLabel);
        }
        return styleObj;
    });

    if (typeof dataLayer.addListener === 'function') {
        dataLayer.addListener('mouseover', (event) => {
            if (!event || !event.feature || typeof event.feature.getProperty !== 'function') return;
            const tipText = event.feature.getProperty('tooltip') || event.feature.getProperty('label');
            if (!tipText) return;
            if (google.maps.InfoWindow && event.latLng) {
                if (!_sharedInfoWindow) {
                    _sharedInfoWindow = new google.maps.InfoWindow({ disableAutoPan: true });
                }
                _sharedInfoWindow.setContent(
                    `<div class="agent-overlay-infowindow">${escapeHtml(tipText)}</div>`
                );
                _sharedInfoWindow.setPosition(event.latLng);
                _sharedInfoWindow.open(state.map);
            }
        });
        dataLayer.addListener('mouseout', () => {
            if (_sharedInfoWindow && typeof _sharedInfoWindow.close === 'function') {
                _sharedInfoWindow.close();
            }
        });
    }

    entry.dataLayer = dataLayer;
}

function attachHeatmapLayer(entry) {
    if (!state.map) return;
    const HeatmapCls = getHeatmapOverlayClass();
    if (!HeatmapCls || !entry.geojson) return;

    const geojson = entry.geojson;
    const meta = (geojson.metadata && typeof geojson.metadata === 'object')
        ? geojson.metadata
        : (entry.descriptor.metadata || {});
    const valueProp = meta.value_property ? String(meta.value_property) : 'weight';
    const samples = extractHeatmapSamples(geojson, valueProp);

    let minW = Infinity;
    let maxW = -Infinity;
    for (const s of samples) {
        if (s.weight < minW) minW = s.weight;
        if (s.weight > maxW) maxW = s.weight;
    }
    const densityMode = Boolean(
        meta.mode === 'density' ||
        (!meta.domain && !meta.value_property && (minW === maxW || samples.length === 0))
    );
    const domain = computeFeatureValueDomain(
        geojson,
        valueProp,
        meta.domain || (Number.isFinite(minW) && Number.isFinite(maxW) && minW !== maxW ? [minW, maxW] : [0, 1])
    );
    const colormap = meta.colormap ? String(meta.colormap) : 'turbo';

    entry.computedDomain = domain;
    entry.usesColormap = true;
    entry.colormap = colormap;
    entry.unit = meta.unit ? String(meta.unit) : '';

    const overlay = new HeatmapCls(samples, {
        colormap,
        domain,
        opacity: Number.isFinite(Number(meta.opacity)) ? Number(meta.opacity) : 0.75,
        radiusM: Number.isFinite(Number(meta.radius_m)) ? Number(meta.radius_m) : 5.0,
        radiusPx: Number.isFinite(Number(meta.radius_px)) ? Number(meta.radius_px) : null,
        densityMode
    });
    overlay.setMap(state.map);
    entry.heatmapOverlay = overlay;
}

async function ensureAndAttachOverlayEntry(entry) {
    if (!entry || !entry.descriptor) return;
    const desc = entry.descriptor;

    if (!entry.geojson || entry.loadedMtime !== desc.mtime) {
        if (!desc.url || typeof fetch !== 'function') return;
        try {
            const resp = await fetch(desc.url, { cache: 'no-store' });
            if (!resp.ok) {
                throw new Error(`HTTP ${resp.status}`);
            }
            const parsed = await resp.json();
            entry.geojson = parsed;
            entry.loadedMtime = desc.mtime;
            entry.error = null;
        } catch (err) {
            entry.error = err ? err.message : 'Failed to load overlay';
            console.warn(`[Overlays] Failed to fetch GeoJSON overlay ${desc.id}:`, err);
            return;
        }
    }

    if (!isOverlayEntryActive(entry)) {
        detachOverlayLayers(entry);
        return;
    }

    detachOverlayLayers(entry);
    if (!state.map) return;

    const metaRender = (entry.geojson && entry.geojson.metadata && entry.geojson.metadata.render) || desc.render;
    if (metaRender === 'heatmap') {
        attachHeatmapLayer(entry);
    } else {
        attachVectorLayer(entry);
    }
}

export async function refreshActiveOverlayLayers() {
    const promises = [];
    for (const entry of state.agentOverlays.values()) {
        if (isOverlayEntryActive(entry)) {
            promises.push(ensureAndAttachOverlayEntry(entry));
        } else {
            detachOverlayLayers(entry);
        }
    }
    await Promise.all(promises);
    renderOverlayLegend();
    renderOverlaysDropdownUI();
}

export function renderOverlayLegend() {
    if (typeof document === 'undefined') return;
    const mapView = document.getElementById('map-view') || document.getElementById('map');
    if (!mapView) return;

    let legendEl = document.getElementById('agent-overlay-legend');
    const activeColored = [];
    for (const entry of state.agentOverlays.values()) {
        if (isOverlayEntryActive(entry) && entry.usesColormap && entry.geojson) {
            activeColored.push(entry);
        }
    }

    if (activeColored.length === 0) {
        if (legendEl) legendEl.style.display = 'none';
        return;
    }

    if (!legendEl) {
        legendEl = document.createElement('div');
        legendEl.id = 'agent-overlay-legend';
        legendEl.className = 'agent-overlay-legend';
        mapView.appendChild(legendEl);
    }

    const fmtBound = (v) => {
        if (!Number.isFinite(v)) return '0';
        return Math.abs(v) >= 100 ? v.toFixed(0) : (Math.abs(v) >= 10 ? v.toFixed(1) : v.toFixed(2));
    };

    legendEl.innerHTML = activeColored.map((entry) => {
        const label = (entry.geojson.metadata && entry.geojson.metadata.label) || entry.descriptor.label || entry.descriptor.id;
        const [d0, d1] = entry.computedDomain || [0, 1];
        const unitSuffix = entry.unit ? ` ${entry.unit}` : '';
        const grad = getCssGradientForColormap(entry.colormap || 'turbo');
        return `
            <div class="agent-legend-row">
                <div class="agent-legend-title">${escapeHtml(label)}</div>
                <div class="agent-legend-bar" style="background: ${grad};"></div>
                <div class="agent-legend-scale">
                    <span>${escapeHtml(fmtBound(d0) + unitSuffix)}</span>
                    <span>${escapeHtml(fmtBound(d1) + unitSuffix)}</span>
                </div>
            </div>
        `;
    }).join('');
    legendEl.style.display = 'flex';
}

export function toggleAgentOverlaysDropdown(event) {
    if (event && typeof event.stopPropagation === 'function') {
        event.stopPropagation();
    }
    if (typeof document === 'undefined') return;
    const dropdown = document.getElementById('agent-overlays-dropdown');
    if (dropdown) {
        dropdown.classList.toggle('open');
    }
}

export function renderOverlaysDropdownUI() {
    if (typeof document === 'undefined') return;
    const rightControls = document.getElementById('map-overlay-right-controls');
    if (!rightControls) return;

    const studies = Array.isArray(state.availableStudies) ? state.availableStudies : [];
    const hasAnything = studies.length > 0 || (state.agentOverlays && state.agentOverlays.size > 0);

    let dropdown = document.getElementById('agent-overlays-dropdown');
    if (!hasAnything) {
        if (dropdown) dropdown.style.display = 'none';
        return;
    }

    if (!dropdown) {
        dropdown = document.createElement('div');
        dropdown.className = 'custom-dropdown agent-overlays-dropdown';
        dropdown.id = 'agent-overlays-dropdown';
        dropdown.innerHTML = `
            <button class="dropdown-trigger-btn" id="agent-overlays-btn" type="button" title="Workspace Studies & Overlays">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <polygon points="12 2 2 7 12 12 22 7 12 2"></polygon>
                    <polyline points="2 17 12 22 22 17"></polyline>
                    <polyline points="2 12 12 17 22 12"></polyline>
                </svg>
                <span id="agent-overlays-btn-label">Overlays</span>
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M6 9l6 6 6-6"/>
                </svg>
            </button>
            <div class="dropdown-menu agent-overlays-menu" id="agent-overlays-menu"></div>
        `;
        const trajDropdown = document.getElementById('traj-color-dropdown');
        if (trajDropdown && trajDropdown.parentNode === rightControls) {
            rightControls.insertBefore(dropdown, trajDropdown);
        } else {
            rightControls.appendChild(dropdown);
        }
        const btn = dropdown.querySelector('#agent-overlays-btn');
        if (btn) {
            btn.addEventListener('click', toggleAgentOverlaysDropdown);
        }
        const menu = dropdown.querySelector('#agent-overlays-menu');
        if (menu) {
            menu.addEventListener('click', (ev) => ev.stopPropagation());
        }
    }

    dropdown.style.display = 'inline-block';
    const btnLabel = dropdown.querySelector('#agent-overlays-btn-label');
    const activeCount = state.activeOverlayIds ? state.activeOverlayIds.size : 0;
    if (btnLabel) {
        btnLabel.textContent = activeCount > 0 ? `Overlays (${activeCount})` : 'Overlays';
    }

    const menu = dropdown.querySelector('#agent-overlays-menu');
    if (!menu) return;

    let html = '';
    if (studies.length > 0) {
        const studyOptions = [
            `<option value="" ${!state.activeStudy ? 'selected' : ''}>(None)</option>`,
            ...studies.map((s) => {
                const sid = String(s.id || '');
                const lbl = String(s.label || sid || 'Root');
                const sel = sid === String(state.activeStudy || '') ? 'selected' : '';
                return `<option value="${escapeHtml(sid)}" ${sel}>${escapeHtml(lbl)}</option>`;
            })
        ].join('');
        html += `
            <div class="agent-overlays-section">
                <div class="agent-overlays-section-label">Study</div>
                <select id="agent-study-select" class="agent-study-select">${studyOptions}</select>
            </div>
        `;
    }

    const overlayEntries = Array.from(state.agentOverlays.values());
    if (overlayEntries.length > 0) {
        html += `<div class="agent-overlays-section">`;
        html += `<div class="agent-overlays-section-header">
                    <span class="agent-overlays-section-label">Map Overlays</span>
                    ${activeCount > 0 ? '<button type="button" class="agent-overlays-clear-btn" id="agent-overlays-clear-btn">Clear</button>' : ''}
                 </div>`;
        for (const entry of overlayEntries) {
            const d = entry.descriptor;
            const checked = isOverlayEntryActive(entry) ? 'checked' : '';
            const badge = d.render === 'heatmap' ? 'heatmap' : 'vector';
            html += `
                <label class="agent-overlay-item" data-overlay-id="${escapeHtml(d.id)}">
                    <input type="checkbox" data-overlay-id="${escapeHtml(d.id)}" ${checked}>
                    <span class="agent-overlay-item-label">${escapeHtml(d.label || d.id)}</span>
                    <span class="agent-overlay-badge">${badge}</span>
                </label>
            `;
        }
        html += `</div>`;
    } else {
        html += `<div class="agent-overlays-empty">No overlays in current study</div>`;
    }

    menu.innerHTML = html;

    const studySelect = menu.querySelector('#agent-study-select');
    if (studySelect) {
        studySelect.addEventListener('change', (e) => {
            const nextStudy = e.target.value || '';
            state.activeStudy = nextStudy;
            setActiveAgentOverlays([]);
            import('./companion.js').then((m) => {
                if (typeof m.switchWorkspaceStudy === 'function') {
                    m.switchWorkspaceStudy(nextStudy);
                } else if (typeof m.refreshWorkspaceManifest === 'function') {
                    m.refreshWorkspaceManifest(nextStudy);
                }
            }).catch(() => {});
        });
    }

    const clearBtn = menu.querySelector('#agent-overlays-clear-btn');
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            setActiveAgentOverlays([]);
        });
    }

    menu.querySelectorAll('input[type="checkbox"][data-overlay-id]').forEach((chk) => {
        chk.addEventListener('change', () => {
            const ovId = chk.getAttribute('data-overlay-id');
            if (ovId) toggleAgentOverlay(ovId);
        });
    });
}

export async function syncAgentOverlaysManifest(manifestOverlays = [], availableStudies = null, activeStudy = undefined) {
    if (Array.isArray(availableStudies)) {
        state.availableStudies = availableStudies;
    }
    if (activeStudy !== undefined && activeStudy !== null) {
        state.activeStudy = String(activeStudy);
    }

    const incomingById = new Map();
    for (const desc of manifestOverlays || []) {
        if (!desc || !desc.id) continue;
        incomingById.set(String(desc.id), desc);
    }

    // Remove overlays no longer present in the manifest, detaching any active layers
    for (const [id, existing] of Array.from(state.agentOverlays.entries())) {
        if (!incomingById.has(id)) {
            detachOverlayLayers(existing);
            state.agentOverlays.delete(id);
        }
    }

    // Upsert descriptors and invalidate cached GeoJSON when mtime or url changes
    for (const [id, desc] of incomingById.entries()) {
        const prev = state.agentOverlays.get(id);
        if (prev) {
            const changed = prev.loadedMtime !== desc.mtime || (prev.descriptor && prev.descriptor.url !== desc.url);
            prev.descriptor = desc;
            if (changed) {
                prev.geojson = null;
                prev.loadedMtime = null;
            }
        } else {
            state.agentOverlays.set(id, {
                descriptor: desc,
                geojson: null,
                loadedMtime: null,
                dataLayer: null,
                heatmapOverlay: null,
                computedDomain: null,
                usesColormap: false,
                colormap: 'turbo',
                unit: '',
                error: null
            });
        }
    }

    await refreshActiveOverlayLayers();
}

export async function setActiveAgentOverlays(overlayIds) {
    state.activeOverlayIds = state.activeOverlayIds || new Set();
    state.activeOverlayIds.clear();

    let list = [];
    if (Array.isArray(overlayIds)) {
        list = overlayIds;
    } else if (typeof overlayIds === 'string') {
        const trimmed = overlayIds.trim();
        if (trimmed && trimmed !== 'none' && trimmed !== 'false') {
            list = trimmed.split(',');
        }
    }

    for (const rawId of list) {
        const cleaned = String(rawId || '').trim();
        if (!cleaned || cleaned === 'none') continue;
        const resolved = resolveOverlayEntry(cleaned);
        state.activeOverlayIds.add(resolved ? resolved.descriptor.id : cleaned);
    }

    await refreshActiveOverlayLayers();
    debouncedUpdateURL();
}

export async function toggleAgentOverlay(overlayId) {
    if (!overlayId) return;
    state.activeOverlayIds = state.activeOverlayIds || new Set();
    const cleaned = String(overlayId).trim();
    const resolved = resolveOverlayEntry(cleaned);
    const canonicalId = resolved ? resolved.descriptor.id : cleaned;

    if (state.activeOverlayIds.has(canonicalId)) {
        state.activeOverlayIds.delete(canonicalId);
    } else if (state.activeOverlayIds.has(cleaned)) {
        state.activeOverlayIds.delete(cleaned);
    } else {
        state.activeOverlayIds.add(canonicalId);
    }

    await refreshActiveOverlayLayers();
    debouncedUpdateURL();
}

export async function reattachAgentOverlaysOnMapInit() {
    renderOverlaysDropdownUI();
    if (!state.map) return;
    await refreshActiveOverlayLayers();
}

