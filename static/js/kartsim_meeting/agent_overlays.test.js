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

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { state } from './state.js';
import {
    buildColormapLut,
    sampleColormapHex,
    syncAgentOverlaysManifest,
    setActiveAgentOverlays,
    toggleAgentOverlay,
    reattachAgentOverlaysOnMapInit
} from './agent_overlays.js';

vi.mock('./url_sync.js', () => ({
    debouncedUpdateURL: vi.fn()
}));

function createMockElement(tagName, registry) {
    const el = {
        tagName: String(tagName || 'div').toUpperCase(),
        id: '',
        className: '',
        style: {},
        innerHTML: '',
        textContent: '',
        children: [],
        parentNode: null,
        attributes: {},
        listeners: {},
        width: 0,
        height: 0,
        clientWidth: 200,
        clientHeight: 200,
        classList: {
            _set: new Set(),
            add(c) { this._set.add(c); },
            remove(c) { this._set.delete(c); },
            toggle(c, force) {
                if (force === undefined) {
                    if (this._set.has(c)) { this._set.delete(c); return false; }
                    this._set.add(c);
                    return true;
                }
                if (force) this._set.add(c); else this._set.delete(c);
                return Boolean(force);
            },
            contains(c) { return this._set.has(c); }
        },
        setAttribute(k, v) { this.attributes[k] = String(v); },
        getAttribute(k) { return this.attributes[k] ?? null; },
        addEventListener(ev, fn) {
            this.listeners[ev] = this.listeners[ev] || [];
            this.listeners[ev].push(fn);
        },
        appendChild(child) {
            child.parentNode = this;
            this.children.push(child);
            if (child.id && registry) registry.set(child.id, child);
            return child;
        },
        insertBefore(child, ref) {
            child.parentNode = this;
            const idx = this.children.indexOf(ref);
            if (idx >= 0) this.children.splice(idx, 0, child);
            else this.children.push(child);
            if (child.id && registry) registry.set(child.id, child);
            return child;
        },
        removeChild(child) {
            const idx = this.children.indexOf(child);
            if (idx >= 0) this.children.splice(idx, 1);
            child.parentNode = null;
            return child;
        },
        querySelector() { return null; },
        querySelectorAll() { return []; }
    };

    if (String(tagName).toLowerCase() === 'canvas') {
        el._putImageDataCalls = [];
        el.getContext = vi.fn(() => ({
            clearRect: vi.fn(),
            createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4), width: w, height: h }),
            putImageData: (imgData, x, y) => { el._putImageDataCalls.push({ imgData, x, y }); }
        }));
    }
    return el;
}

describe('agent_overlays.js', () => {
    let createdDataLayers;
    let trackLimitsDataLayer;
    let elementRegistry;
    let mapViewEl;
    let rightControlsEl;

    beforeEach(() => {
        createdDataLayers = [];
        elementRegistry = new Map();

        mapViewEl = createMockElement('div', elementRegistry);
        mapViewEl.id = 'map-view';
        elementRegistry.set('map-view', mapViewEl);

        rightControlsEl = createMockElement('div', elementRegistry);
        rightControlsEl.id = 'map-overlay-right-controls';
        elementRegistry.set('map-overlay-right-controls', rightControlsEl);

        vi.stubGlobal('document', {
            createElement: vi.fn((tag) => createMockElement(tag, elementRegistry)),
            getElementById: vi.fn((id) => elementRegistry.get(id) || null)
        });

        trackLimitsDataLayer = {
            addGeoJson: vi.fn(),
            setMap: vi.fn(),
            forEach: vi.fn(),
            remove: vi.fn()
        };

        class MockDataLayer {
            constructor(opts = {}) {
                this.map = opts.map || null;
                this.geojsons = [];
                this.styleFn = null;
                this.listeners = {};
                createdDataLayers.push(this);
            }
            addGeoJson(gj) {
                this.geojsons.push(gj);
            }
            setStyle(fn) {
                this.styleFn = fn;
            }
            setMap(m) {
                this.map = m;
            }
            addListener(ev, fn) {
                this.listeners[ev] = fn;
                return { remove: vi.fn() };
            }
        }

        class MockOverlayView {
            constructor() {
                this._map = null;
                this._pane = createMockElement('div', elementRegistry);
            }
            setMap(m) {
                const prev = this._map;
                this._map = m;
                if (m && !prev && typeof this.onAdd === 'function') {
                    this.onAdd();
                    if (typeof this.draw === 'function') this.draw();
                } else if (!m && prev && typeof this.onRemove === 'function') {
                    this.onRemove();
                }
            }
            getMap() {
                return this._map;
            }
            getPanes() {
                return { overlayLayer: this._pane };
            }
            getProjection() {
                return {
                    fromLatLngToDivPixel: (ll) => {
                        const lat = typeof ll.lat === 'function' ? ll.lat() : ll.lat;
                        const lng = typeof ll.lng === 'function' ? ll.lng() : ll.lng;
                        return {
                            x: 100 + (lng - 0.5) * 10000,
                            y: 100 - (lat - 51.3) * 10000
                        };
                    }
                };
            }
        }

        vi.stubGlobal('google', {
            maps: {
                Data: MockDataLayer,
                OverlayView: MockOverlayView,
                LatLng: class {
                    constructor(lat, lng) { this._lat = lat; this._lng = lng; }
                    lat() { return this._lat; }
                    lng() { return this._lng; }
                },
                SymbolPath: { CIRCLE: 0 },
                InfoWindow: class {
                    setContent() {}
                    setPosition() {}
                    open() {}
                    close() {}
                }
            }
        });

        state.map = {
            data: trackLimitsDataLayer,
            getDiv: () => ({ clientWidth: 160, clientHeight: 160 }),
            getCenter: () => ({ lat: () => 51.3, lng: () => 0.5 }),
            getZoom: () => 18,
            addListener: vi.fn(() => ({ remove: vi.fn() }))
        };
        state.agentOverlays = new Map();
        state.activeOverlayIds = new Set();
        state.availableStudies = [];
        state.activeStudy = '';
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it('builds 256-entry colormap LUTs and samples distinct hex endpoints', () => {
        for (const name of ['turbo', 'inferno', 'viridis', 'rdylgn', 'coolwarm']) {
            const lut = buildColormapLut(name);
            expect(lut).toHaveLength(256);
            const lowHex = sampleColormapHex(name, 0.0);
            const highHex = sampleColormapHex(name, 1.0);
            expect(lowHex).toMatch(/^#[0-9a-f]{6}$/);
            expect(highHex).toMatch(/^#[0-9a-f]{6}$/);
            expect(lowHex).not.toBe(highHex);
        }
    });

    it('isolates vector overlays on dedicated google.maps.Data layers without touching state.map.data', async () => {
        const vectorGeoJson = {
            type: 'FeatureCollection',
            metadata: {
                id: 'braking_zones',
                label: 'Turn 3 Braking Delta',
                render: 'vector',
                value_property: 'delta_kmh',
                colormap: 'rdylgn',
                domain: [-10, 10],
                unit: 'km/h'
            },
            features: [
                {
                    type: 'Feature',
                    properties: { delta_kmh: -8, label: 'Late brake' },
                    geometry: { type: 'Point', coordinates: [0.5, 51.3] }
                },
                {
                    type: 'Feature',
                    properties: { delta_kmh: 9, strokeColor: '#ffffff' },
                    geometry: { type: 'LineString', coordinates: [[0.5, 51.3], [0.5005, 51.3005]] }
                }
            ]
        };

        vi.stubGlobal('fetch', vi.fn(async () => ({
            ok: true,
            json: async () => vectorGeoJson
        })));

        await syncAgentOverlaysManifest([
            {
                id: 'braking_zones',
                label: 'Turn 3 Braking Delta',
                render: 'vector',
                url: 'http://127.0.0.1:38472/files/study1/overlays/braking_zones.geojson',
                mtime: 100.0
            }
        ], [{ id: 'study1', label: 'Study 1' }], 'study1');

        // Not active until explicitly requested
        expect(createdDataLayers).toHaveLength(0);
        expect(trackLimitsDataLayer.addGeoJson).not.toHaveBeenCalled();

        await setActiveAgentOverlays(['braking_zones']);
        expect(createdDataLayers).toHaveLength(1);
        expect(createdDataLayers[0].map).toBe(state.map);
        expect(trackLimitsDataLayer.addGeoJson).not.toHaveBeenCalled();
        expect(trackLimitsDataLayer.setMap).not.toHaveBeenCalled();

        // Check colormap styling vs explicit stroke override
        const lowStyle = createdDataLayers[0].styleFn({
            getProperty: (k) => (k === 'delta_kmh' ? -10 : (k === 'label' ? 'Apex' : undefined))
        });
        expect(lowStyle.strokeColor).toBe(sampleColormapHex('rdylgn', 0));
        expect(lowStyle.title).toBe('Apex');

        const overrideStyle = createdDataLayers[0].styleFn({
            getProperty: (k) => (k === 'strokeColor' ? '#123456' : 5)
        });
        expect(overrideStyle.strokeColor).toBe('#123456');

        // Legend rendered inside #map-view
        const legend = elementRegistry.get('agent-overlay-legend');
        expect(legend).toBeDefined();
        expect(legend.style.display).toBe('flex');

        // Clearing active overlays detaches map layer while keeping descriptor in state.agentOverlays
        await setActiveAgentOverlays([]);
        expect(createdDataLayers[0].map).toBeNull();
        expect(state.agentOverlays.has('braking_zones')).toBe(true);
        expect(legend.style.display).toBe('none');
    });

    it('renders smooth Gaussian <canvas> heatmaps via custom OverlayView and reloads when mtime updates', async () => {
        let currentWeight = 12.0;
        const fetchMock = vi.fn(async () => ({
            ok: true,
            json: async () => ({
                type: 'FeatureCollection',
                metadata: {
                    id: 'theta_heat',
                    label: 'EEG Theta Power',
                    render: 'heatmap',
                    value_property: 'theta',
                    colormap: 'inferno',
                    domain: [5, 20],
                    radius_px: 12,
                    unit: 'uV^2'
                },
                features: [
                    {
                        type: 'Feature',
                        properties: { theta: currentWeight },
                        geometry: { type: 'Point', coordinates: [0.5, 51.3] }
                    }
                ]
            })
        }));
        vi.stubGlobal('fetch', fetchMock);

        state.activeOverlayIds.add('theta_heat');
        await syncAgentOverlaysManifest([
            {
                id: 'theta_heat',
                label: 'EEG Theta Power',
                render: 'heatmap',
                url: 'http://127.0.0.1:38472/files/eeg/overlays/theta_heat.geojson',
                mtime: 10.0
            }
        ]);

        expect(fetchMock).toHaveBeenCalledTimes(1);
        const entry = state.agentOverlays.get('theta_heat');
        expect(entry.heatmapOverlay).not.toBeNull();
        expect(entry.heatmapOverlay._lastImageData).toBeDefined();

        // Center pixel (80, 80) or nearby (100, 100) should have non-zero alpha
        const rgba = entry.heatmapOverlay._lastImageData.data;
        let nonZeroAlphaCount = 0;
        for (let i = 3; i < rgba.length; i += 4) {
            if (rgba[i] > 0) nonZeroAlphaCount++;
        }
        expect(nonZeroAlphaCount).toBeGreaterThan(20);

        // Updating mtime triggers automatic re-fetch and re-render
        currentWeight = 19.5;
        await syncAgentOverlaysManifest([
            {
                id: 'theta_heat',
                label: 'EEG Theta Power',
                render: 'heatmap',
                url: 'http://127.0.0.1:38472/files/eeg/overlays/theta_heat.geojson',
                mtime: 11.0
            }
        ]);
        expect(fetchMock).toHaveBeenCalledTimes(2);

        // Toggle off removes canvas cleanly
        await toggleAgentOverlay('theta_heat');
        expect(state.activeOverlayIds.has('theta_heat')).toBe(false);
        expect(entry.heatmapOverlay).toBeNull();

        // Toggle back on and verify reattachAgentOverlaysOnMapInit
        await toggleAgentOverlay('theta_heat');
        expect(state.activeOverlayIds.has('theta_heat')).toBe(true);
        await reattachAgentOverlaysOnMapInit();
        expect(state.agentOverlays.get('theta_heat').heatmapOverlay).not.toBeNull();
    });
});

