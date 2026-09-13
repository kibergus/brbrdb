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
import { addTrackMarker, addTrackMarkerAtDistance, initTrackMarkers, clearLapPolylines, calculateSegmentColor, setTrajectoryColorMode, loadBaseColumnsForSessions, hasBrakeChannel, loadTrackPoints, LAP_PALETTE, matchesRequestedLap } from './map.js';

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

    it('calculates colors for delta_t mode: fastest is white, negative rate is green, positive rate is red, zero rate is yellow', () => {
        const refLap = {
            lapId: 'lap-ref',
            lap_time: '1:00.000',
            points: [
                { dist: 0, time: 0, speed: 100 },
                { dist: 100, time: 5, speed: 100 },
                { dist: 200, time: 10, speed: 100 }
            ]
        };
        const otherLap = {
            lapId: 'lap-other',
            lap_time: '1:01.000',
            points: [
                { dist: 0, time: 100, speed: 100 },
                { dist: 100, time: 105, speed: 100 },
                { dist: 200, time: 111, speed: 80 }
            ]
        };
        state.fastestSelectedLap = refLap;
        state.fastestSelectedLapId = 'lap-ref';
        state.fastestGroupALap = refLap;
        state.fastestGroupALapId = 'lap-ref';
        state.lapDataLookup = {
            'lap-ref': refLap,
            'lap-other': otherLap
        };

        // Reference lap is white
        expect(calculateSegmentColor({ dist: 50, time: 2.5, speed: 100 }, 'lap-ref', 'delta_t')).toBe('#ffffff');

        // Other lap at dist 50 has equal pace -> rate = 0 (yellow)
        expect(calculateSegmentColor({ dist: 50, time: 102.5, speed: 100 }, 'lap-other', 'delta_t')).toBe('rgb(255, 255, 0)');

        // Other lap at dist 150 has slower pace (6s/100m vs 5s/100m -> rate = +0.20 -> reddish)
        const posColor = calculateSegmentColor({ dist: 150, time: 108, speed: 80 }, 'lap-other', 'delta_t');
        expect(posColor).toBe('rgb(255, 19, 0)');

        // Lap faster than ref (4s/100m vs 5s/100m -> rate = -0.20 -> greenish)
        const fastLap = {
            lapId: 'lap-fast',
            lap_time: '59.000',
            points: [
                { dist: 0, time: 0, speed: 125 },
                { dist: 100, time: 4, speed: 125 }
            ]
        };
        state.lapDataLookup['lap-fast'] = fastLap;
        expect(calculateSegmentColor({ dist: 50, time: 2, speed: 125 }, 'lap-fast', 'delta_t')).toBe('rgb(19, 255, 0)');
    });

    it('calculates colors for time mode based on lap time when sortMode is time', () => {
        const lapFast = { lapId: 'lap-1', lap_time: '58.000', points: [{ dist: 0 }] };
        const lapMid = { lapId: 'lap-2', lap_time: '59.000', points: [{ dist: 0 }] };
        const lapSlow = { lapId: 'lap-3', lap_time: '1:00.000', points: [{ dist: 0 }] };

        state.sortMode = 'time';
        state.groupASelection = new Set(['lap-1', 'lap-2', 'lap-3']);
        state.groupBSelection = new Set();
        state.lapDataLookup = {
            'lap-1': lapFast,
            'lap-2': lapMid,
            'lap-3': lapSlow
        };

        // Fastest lap (58.0s) -> green
        expect(calculateSegmentColor({}, 'lap-1', 'time')).toBe('rgb(0, 255, 0)');
        // Mid lap (59.0s) -> yellow
        expect(calculateSegmentColor({}, 'lap-2', 'time')).toBe('rgb(255, 255, 0)');
        // Slowest lap (60.0s) -> red
        expect(calculateSegmentColor({}, 'lap-3', 'time')).toBe('rgb(255, 0, 0)');
    });

    it('calculates colors for time mode based on turn time when sortMode is turn', () => {
        const lapFastTurn = { lapId: 'lap-1', turn_times: [5.0, 3.0], points: [{ dist: 0 }] };
        const lapMidTurn = { lapId: 'lap-2', turn_times: [5.0, 3.5], points: [{ dist: 0 }] };
        const lapSlowTurn = { lapId: 'lap-3', turn_times: [5.0, 4.0], points: [{ dist: 0 }] };

        state.sortMode = 'turn';
        state.currentTurnIdx = 1;
        state.groupASelection = new Set(['lap-1', 'lap-2', 'lap-3']);
        state.groupBSelection = new Set();
        state.lapDataLookup = {
            'lap-1': lapFastTurn,
            'lap-2': lapMidTurn,
            'lap-3': lapSlowTurn
        };

        // Fastest in turn 2 (3.0s) -> green
        expect(calculateSegmentColor({}, 'lap-1', 'time')).toBe('rgb(0, 255, 0)');
        // Mid in turn 2 (3.5s) -> yellow
        expect(calculateSegmentColor({}, 'lap-2', 'time')).toBe('rgb(255, 255, 0)');
        // Slowest in turn 2 (4.0s) -> red
        expect(calculateSegmentColor({}, 'lap-3', 'time')).toBe('rgb(255, 0, 0)');
    });

    it('uses first lap in group A as reference even if group B has a faster lap', () => {
        const groupALap = {
            lapId: 'lap-group-a',
            lap_time: '1:02.000',
            points: [
                { dist: 0, time: 0, speed: 90 },
                { dist: 100, time: 5, speed: 90 }
            ]
        };
        const fasterGroupBLap = {
            lapId: 'lap-group-b',
            lap_time: '1:00.000',
            points: [
                { dist: 0, time: 0, speed: 100 },
                { dist: 100, time: 4.5, speed: 100 }
            ]
        };
        state.fastestGroupALap = groupALap;
        state.fastestGroupALapId = 'lap-group-a';
        state.fastestSelectedLap = fasterGroupBLap;
        state.fastestSelectedLapId = 'lap-group-b';
        state.lapDataLookup = {
            'lap-group-a': groupALap,
            'lap-group-b': fasterGroupBLap
        };

        // Group A lap is the reference -> rendered in white
        expect(calculateSegmentColor({ dist: 100, time: 5, speed: 90 }, 'lap-group-a', 'delta_t')).toBe('#ffffff');
        // Group B lap is faster than reference -> rate < 0 (green / lime green)
        expect(calculateSegmentColor({ dist: 100, time: 4.5, speed: 100 }, 'lap-group-b', 'delta_t')).toBe('rgb(77, 255, 0)');
    });
});

describe('setTrajectoryColorMode', () => {
    it('updates state.trajectoryColorMode and polyline colors/zIndex', () => {
        const poly1 = new google.maps.Polyline({ strokeColor: '#ffffff', zIndex: 1 });
        const poly2 = new google.maps.Polyline({ strokeColor: '#ffffff', zIndex: 1 });
        poly1.setOptions = function(opts) { Object.assign(this.opts, opts); };
        poly2.setOptions = function(opts) { Object.assign(this.opts, opts); };

        state.fastestGroupALapId = 's1-1';
        state.fastestSelectedLapId = 's1-1';
        state.lapPolylines = {
            's1-1': [poly1],
            's1-2': [poly2]
        };
        state.lapDataLookup = {
            's1-1': { points: [{ speed: 0 }, { speed: 10 }] },
            's1-2': { points: [{ speed: 0 }, { speed: 10 }] }
        };
        state.fastestGroupALap = state.lapDataLookup['s1-1'];
        state.globalMaxSpeed = 100;

        setTrajectoryColorMode('speed');
        expect(state.trajectoryColorMode).toBe('speed');
        expect(poly1.opts.strokeColor).toBe('rgb(40, 20, 180)');
        expect(poly1.opts.zIndex).toBe(1);

        setTrajectoryColorMode('delta_t');
        expect(state.trajectoryColorMode).toBe('delta_t');
        // Fastest lap has zIndex 1, other lap has higher zIndex 5
        expect(poly1.opts.zIndex).toBe(1);
        expect(poly2.opts.zIndex).toBe(5);

        setTrajectoryColorMode('time');
        expect(state.trajectoryColorMode).toBe('time');
        expect(poly1.opts.strokeOpacity).toBe(0.45);
        expect(poly1.opts.strokeWeight).toBe(2);
        expect(poly1.opts.zIndex).toBe(1);
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

    describe('matchesRequestedLap with index notation', () => {
        const session0 = { session_id: 'club100/cadet/2026-09-12/Lydd/09_48_cadet_group_b' };
        const session1 = { session_id: 'club100_south/cadet_lw/2026-09-12/Lydd/11_02_practice' };
        const lap0_4 = { lap_num: 4 };
        const lap1_4 = { lap_num: 4 };
        const lap1_2 = { lap_num: 2 };

        beforeEach(() => {
            state.allSessionsData = [session0, session1];
        });

        it('matches by session index and lap number: "0_4"', () => {
            const requested = new Set(['0_4']);
            expect(matchesRequestedLap(requested, 'id0-4', session0, lap0_4, 0)).toBe(true);
            expect(matchesRequestedLap(requested, 'id1-4', session1, lap1_4, 1)).toBe(false);
            expect(matchesRequestedLap(requested, 'id1-2', session1, lap1_2, 1)).toBe(false);
        });

        it('matches by session index and lap number without explicit sIdx passed', () => {
            const requested = new Set(['1_2']);
            expect(matchesRequestedLap(requested, 'id0-4', session0, lap0_4)).toBe(false);
            expect(matchesRequestedLap(requested, 'id1-2', session1, lap1_2)).toBe(true);
        });

        it('matches by hyphen index notation: "1-2"', () => {
            const requested = new Set(['1-2']);
            expect(matchesRequestedLap(requested, 'id1-2', session1, lap1_2)).toBe(true);
            expect(matchesRequestedLap(requested, 'id0-4', session0, lap0_4)).toBe(false);
        });

        it('maintains backward compatibility with full lapId', () => {
            const requested = new Set(['id0-4']);
            expect(matchesRequestedLap(requested, 'id0-4', session0, lap0_4)).toBe(true);
        });
    });
});

