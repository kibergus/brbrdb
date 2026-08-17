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

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { state } from './state.js';
import { addTrackMarker, addTrackMarkerAtDistance, initTrackMarkers, clearLapPolylines, calculateSegmentColor, setTrajectoryColorMode, loadBaseColumnsForSessions, hasBrakeChannel, loadTrackPoints, LAP_PALETTE } from './map.js';

function createMockElement(tagName = 'div') {
    const children = [];
    const attributes = {};
    const style = {};
    const classList = {
        add: vi.fn(),
        remove: vi.fn(),
        toggle: vi.fn(),
        contains: vi.fn().mockReturnValue(false)
    };

    return {
        tagName: tagName.toUpperCase(),
        children,
        style,
        classList,
        innerHTML: '',
        textContent: '',
        className: '',
        setAttribute(k, v) { attributes[k] = String(v); },
        getAttribute(k) { return attributes[k]; },
        appendChild(child) {
            children.push(child);
            return child;
        },
        querySelector: vi.fn(() => createMockElement('div')),
        querySelectorAll: vi.fn(() => [])
    };
}

global.Plotly = {
    react: vi.fn(),
    Plots: { resize: vi.fn() }
};

// Setup global google maps mock
global.google = {
    maps: {
        Size: function (w, h) { this.width = w; this.height = h; },
        LatLng: function (lat, lng) { this.lat = lat; this.lng = lng; },
        LatLngBounds: function () {
            this.isEmpty = () => true;
            this.extend = () => { };
        },
        Map: function () {
            this.addListener = () => { };
            this.mapTypes = new Map();
            this.controls = { [1]: [], [2]: [] };
            this.data = {
                setMap: () => { },
                setStyle: () => { },
                forEach: () => { },
                remove: () => { },
                addGeoJson: () => { }
            };
        },
        SymbolPath: { CIRCLE: 1 },
        ControlPosition: { TOP_LEFT: 1, TOP_RIGHT: 2 },
        Polyline: function (opts) {
            this.opts = opts;
            this.setMap = (m) => { this.map = m; };
            this.setPath = (p) => { this.path = p; };
            this.setOptions = (o) => { Object.assign(this.opts, o); };
        },
        marker: {
            AdvancedMarkerElement: function (opts) {
                this.opts = opts;
                this.setMap = (m) => { this.map = m; };
            }
        },
        geometry: {
            spherical: {
                computeHeading: () => 45,
                computeOffset: (p, dist, heading) => ({ lat: (p.lat || 0) + 0.001, lng: (p.lng || 0) + 0.001 }),
                interpolate: (from, to, fraction) => new google.maps.LatLng(from.lat, from.lng)
            }
        }
    }
};

describe('map.js track markers', () => {
    beforeEach(() => {
        state.map = new google.maps.Map();
        state.trackMarkers = [];

        vi.stubGlobal('document', {
            getElementById: vi.fn().mockImplementation((id) => createMockElement('div')),
            querySelectorAll: vi.fn().mockImplementation(() => []),
            createElement: vi.fn().mockImplementation((tagName) => createMockElement(tagName))
        });

        vi.stubGlobal('window', {
            location: { search: '' },
            KART_CONFIG: {
                trackName: 'Rissington 2026',
                getTrackDataUrl: '/api/track_data?track=Rissington%202026'
            }
        });
    });

    it('adds track marker line and label marker correctly', () => {
        const p1 = { lat: 51.86, lng: -1.68 };
        const p2 = { lat: 51.87, lng: -1.69 };
        addTrackMarker(p1, p2, 'T1', '#10b981');

        expect(state.trackMarkers.length).toBe(2);
        expect(state.trackMarkers[0].opts.strokeColor).toBe('#10b981');
        expect(state.trackMarkers[1].opts.title).toBe('T1');
        expect(state.trackMarkers[1].opts.content.textContent).toBe('T1');
    });

    it('adds marker at specified distance along points', () => {
        const points = [
            { dist: 0, lat: 51.86, lng: -1.68 },
            { dist: 100, lat: 51.87, lng: -1.69 },
            { dist: 200, lat: 51.88, lng: -1.70 }
        ];

        addTrackMarkerAtDistance(points, 50, 'T1', '#10b981');
        expect(state.trackMarkers.length).toBe(2);
        expect(state.trackMarkers[1].opts.title).toBe('T1');
    });

    it('places markers at start AND end of turns in initTrackMarkers', async () => {
        const trackData = {
            lap_length: 1000,
            center_line: [
                { dist: 0, lat: 51.86, lon: -1.68 },
                { dist: 50, lat: 51.861, lon: -1.681 },
                { dist: 100, lat: 51.862, lon: -1.682 },
                { dist: 400, lat: 51.865, lon: -1.685 },
                { dist: 600, lat: 51.867, lon: -1.687 }
            ],
            turns: [
                { name: 'Elbow', start: 50, end: 100 },
                { name: 'Esses', start: 400, end: 600 }
            ]
        };

        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            json: async () => trackData
        }));

        initTrackMarkers();

        // Wait for fetch promise chain to complete
        await new Promise(resolve => setTimeout(resolve, 50));

        const turnMarkerTitles = state.trackMarkers
            .filter(m => m.opts && m.opts.title && m.opts.title.startsWith('T'))
            .map(m => m.opts.title);

        expect(turnMarkerTitles).toEqual(['T1', 'T1', 'T2', 'T2']);
    });

    it('clears existing lap polylines by detaching them from google maps', () => {
        const mockPolyline1 = { setMap: vi.fn() };
        const mockPolyline2 = { setMap: vi.fn() };

        state.lapPolylines = {
            'session-1-1': [mockPolyline1],
            'session-1-2': [mockPolyline2]
        };

        clearLapPolylines();

        expect(mockPolyline1.setMap).toHaveBeenCalledWith(null);
        expect(mockPolyline2.setMap).toHaveBeenCalledWith(null);
        expect(state.lapPolylines).toEqual({});
    });
});

describe('calculateSegmentColor', () => {
    it('calculates colors for pedals mode', () => {
        expect(calculateSegmentColor({ brake: 80, throttle: 0 }, 'lap-1', 'pedals')).toContain('rgb(255,');
        expect(calculateSegmentColor({ brake: 0, throttle: 100 }, 'lap-1', 'pedals')).toContain('255,');
        expect(calculateSegmentColor({ brake: 0, throttle: 0 }, 'lap-1', 'pedals')).toBe('#ffffff');
    });

    it('calculates colors for speed mode using multi-stop spectrum', () => {
        expect(calculateSegmentColor({ speed: 0 }, 'lap-1', 'speed', 100)).toBe('rgb(40, 20, 180)');
        expect(calculateSegmentColor({ speed: 100 }, 'lap-1', 'speed', 100)).toBe('rgb(0, 255, 80)');
    });

    it('calculates colors for accel mode', () => {
        expect(calculateSegmentColor({ acceleration: -4 }, 'lap-1', 'accel')).toBe('rgb(255, 0, 0)');
        expect(calculateSegmentColor({ acceleration: 2.5 }, 'lap-1', 'accel')).toBe('rgb(0, 255, 0)');
        expect(calculateSegmentColor({ acceleration: 0 }, 'lap-1', 'accel')).toBe('#ffffff');
    });

    it('calculates colors for gforce_lon mode', () => {
        expect(calculateSegmentColor({ gx: -1.5 }, 'lap-1', 'gforce_lon')).toBe('rgb(255, 0, 0)');
        expect(calculateSegmentColor({ gx: 1.0 }, 'lap-1', 'gforce_lon')).toBe('rgb(0, 255, 0)');
        expect(calculateSegmentColor({ gx: 0 }, 'lap-1', 'gforce_lon')).toBe('#ffffff');
    });

    it('calculates colors for gforce_lat mode', () => {
        expect(calculateSegmentColor({ gy: -1.5 }, 'lap-1', 'gforce_lat')).toBe('rgb(0, 0, 255)');
        expect(calculateSegmentColor({ gy: 1.5 }, 'lap-1', 'gforce_lat')).toBe('rgb(255, 0, 0)');
        expect(calculateSegmentColor({ gy: 0 }, 'lap-1', 'gforce_lat')).toBe('#ffffff');
    });

    it('calculates colors for lap mode', () => {
        state.lapColorsForId['lap-1'] = LAP_PALETTE[2];
        expect(calculateSegmentColor({}, 'lap-1', 'lap')).toBe(LAP_PALETTE[2]);
    });
});

describe('setTrajectoryColorMode', () => {
    it('updates state.trajectoryColorMode and polyline colors', () => {
        const poly1 = new google.maps.Polyline({ strokeColor: '#ffffff' });
        state.lapPolylines = {
            's1-1': [poly1]
        };
        state.lapDataLookup = {
            's1-1': { points: [{ speed: 0 }, { speed: 10 }] }
        };
        state.globalMaxSpeed = 100;

        setTrajectoryColorMode('speed');
        expect(state.trajectoryColorMode).toBe('speed');
        expect(poly1.opts.strokeColor).toBe('rgb(40, 20, 180)');
    });
});

describe('loadBaseColumnsForSessions', () => {
    it('correctly maps GForceLon to gx and GForceLat to gy', async () => {
        const oldWindow = global.window;
        global.window = {
            location: { origin: 'http://localhost' },
            KART_CONFIG: { getTrackPointsUrl: 'http://localhost/api/telemetry' }
        };

        const createChannelBuffer = (val) => {
            const meanVal = val;
            const scale = 1.0;
            const deltas = new Int16Array([0]);
            const buffer = new ArrayBuffer(16 + deltas.byteLength);
            const view = new DataView(buffer);
            view.setFloat64(0, meanVal, true);
            view.setFloat64(8, scale, true);
            new Int16Array(buffer, 16).set(deltas);
            return buffer;
        };

        global.fetch = vi.fn().mockImplementation((url) => {
            const urlStr = String(url);
            if (urlStr.includes('GForceLon')) {
                return Promise.resolve({ ok: true, arrayBuffer: () => Promise.resolve(createChannelBuffer(1.2)) });
            }
            if (urlStr.includes('GForceLat')) {
                return Promise.resolve({ ok: true, arrayBuffer: () => Promise.resolve(createChannelBuffer(0.8)) });
            }
            return Promise.resolve({ ok: true, arrayBuffer: () => Promise.resolve(createChannelBuffer(0.0)) });
        });

        const session = {
            session_id: 's1',
            columns: ['Time', 'Latitude', 'Longitude', 'Speed', 'GForceLat', 'GForceLon'],
            laps: [{ lap: 1, start_idx: 0, end_idx: 0 }]
        };

        state.laps = [{ session_id: 's1', lap: 1 }];
        state.lapDataLookup = {};

        await loadBaseColumnsForSessions([session]);

        const points = session.laps[0].points;
        expect(points[0].gx).toBe(1.2);  // Forward acceleration from GForceLon
        expect(points[0].gy).toBe(0.8);  // Rightward force from GForceLat

        global.window = oldWindow;
    });
});

describe('hasBrakeChannel', () => {
    it('returns true when brake channel is in session columns', () => {
        expect(hasBrakeChannel([{ columns: ['Time', 'Speed', 'Brake'] }])).toBe(true);
        expect(hasBrakeChannel([{ columns: ['Time', 'Speed', 'Brake (%)'] }])).toBe(true);
    });

    it('returns false when no brake channel is present in session columns', () => {
        expect(hasBrakeChannel([])).toBe(false);
        expect(hasBrakeChannel([{ columns: ['Time', 'Latitude', 'Longitude', 'Speed', 'GForceLat', 'GForceLon'] }])).toBe(false);
        expect(hasBrakeChannel([{ columns: [] }])).toBe(false);
        expect(hasBrakeChannel([{}])).toBe(false);
    });
});

describe('loadTrackPoints default color mode', () => {
    it('defaults to accel mode when no brake channel is present and tcol is not specified', async () => {
        const oldWindow = global.window;
        const oldDocument = global.document;

        const currentLabel = createMockElement();
        const dropItemPedals = createMockElement();
        dropItemPedals.setAttribute('data-mode', 'pedals');
        const dropItemAccel = createMockElement();
        dropItemAccel.setAttribute('data-mode', 'accel');

        global.document = {
            getElementById: vi.fn((id) => {
                if (id === 'traj-color-current-label') return currentLabel;
                return createMockElement();
            }),
            querySelectorAll: vi.fn((sel) => {
                if (sel.includes('traj-color-menu')) return [dropItemPedals, dropItemAccel];
                return [];
            })
        };

        global.window = {
            location: { origin: 'http://localhost', search: '' },
            KART_CONFIG: { getTrackPointsUrl: 'http://localhost/api/telemetry' }
        };

        global.fetch = vi.fn().mockImplementation((url) => {
            const urlStr = String(url);
            if (urlStr.includes('/api/telemetry/channel')) {
                return Promise.resolve({
                    ok: true,
                    status: 200,
                    arrayBuffer: () => Promise.resolve(new ArrayBuffer(16))
                });
            }
            if (urlStr.includes('/api/telemetry') || urlStr.includes('getTrackPointsUrl')) {
                return Promise.resolve({
                    ok: true,
                    status: 200,
                    json: () => Promise.resolve([
                        {
                            session_id: 's1',
                            columns: ['Time', 'Latitude', 'Longitude', 'Speed'],
                            laps: [{ lap_num: 1, lap_time: '45.000', start_idx: 0, end_idx: 1 }]
                        }
                    ])
                });
            }
            return Promise.resolve({
                ok: true,
                status: 200,
                json: () => Promise.resolve({ center_line: [], turns: [] })
            });
        });

        state.trajectoryColorMode = 'pedals';
        await loadTrackPoints();

        expect(state.trajectoryColorMode).toBe('accel');
        expect(currentLabel.textContent).toBe('Accel');

        global.window = oldWindow;
        global.document = oldDocument;
    });
});

