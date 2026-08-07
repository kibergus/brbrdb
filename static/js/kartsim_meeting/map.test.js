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
import { addTrackMarker, addTrackMarkerAtDistance, initTrackMarkers, clearLapPolylines } from './map.js';

function createMockElement(tagName = 'div') {
    const children = [];
    const attributes = {};
    const style = {};

    return {
        tagName: tagName.toUpperCase(),
        children,
        style,
        innerHTML: '',
        textContent: '',
        className: '',
        setAttribute(k, v) { attributes[k] = String(v); },
        getAttribute(k) { return attributes[k]; },
        appendChild(child) {
            children.push(child);
            return child;
        }
    };
}

// Setup global google maps mock
global.google = {
    maps: {
        Size: function(w, h) { this.width = w; this.height = h; },
        LatLng: function(lat, lng) { this.lat = lat; this.lng = lng; },
        LatLngBounds: function() {
            this.isEmpty = () => true;
            this.extend = () => {};
        },
        Map: function() {
            this.addListener = () => {};
            this.mapTypes = new Map();
            this.controls = { [1]: [] };
            this.data = {
                setMap: () => {},
                setStyle: () => {},
                forEach: () => {},
                remove: () => {},
                addGeoJson: () => {}
            };
        },
        SymbolPath: { CIRCLE: 1 },
        ControlPosition: { TOP_LEFT: 1 },
        Polyline: function(opts) {
            this.opts = opts;
            this.setMap = (m) => { this.map = m; };
            this.setPath = (p) => { this.path = p; };
        },
        marker: {
            AdvancedMarkerElement: function(opts) {
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
