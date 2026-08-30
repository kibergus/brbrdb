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
import { TrajectoryPlot } from './trajectory_plot.js';

function createMockElement(tagName = 'div') {
    const children = [];
    const listeners = {};
    const attributes = {};
    const style = {};
    let className = '';
    const classList = {
        add: vi.fn(c => { if (!className.includes(c)) className = (className + ' ' + c).trim(); }),
        remove: vi.fn(c => { className = className.replace(new RegExp(`\\b${c}\\b`, 'g'), '').trim(); }),
        toggle: vi.fn(c => { if (className.includes(c)) classList.remove(c); else classList.add(c); }),
        contains: vi.fn(c => className.includes(c))
    };

    return {
        tagName: tagName.toUpperCase(),
        children,
        style,
        innerHTML: '',
        textContent: '',
        get className() { return className; },
        set className(v) { className = String(v); },
        classList,
        setAttribute(k, v) { attributes[k] = String(v); },
        getAttribute(k) { return attributes[k]; },
        appendChild(child) {
            children.push(child);
            return child;
        },
        querySelectorAll(selector) {
            const results = [];
            const walk = (el) => {
                if (!el || !el.children) return;
                for (const c of el.children) {
                    if (selector.startsWith('.') && c.className.split(/\s+/).includes(selector.slice(1))) results.push(c);
                    walk(c);
                }
            };
            walk(this);
            return results;
        },
        querySelector(selector) {
            const res = this.querySelectorAll(selector);
            return res.length > 0 ? res[0] : null;
        },
        addEventListener(evt, fn) {
            listeners[evt] = listeners[evt] || [];
            listeners[evt].push(fn);
        },
        getBoundingClientRect() {
            return { left: 0, top: 0, width: 400, height: 400 };
        },
        outerHTML: `<${tagName}></${tagName}>`
    };
}

describe('TrajectoryPlot.js', () => {
    let mockContainer;

    const mockTelemetryData = {
        session_id: 'test_session_01',
        track: 'Lydd',
        date: '2026-07-12',
        laps: [
            {
                lap_num: 1,
                lap_time: '0:44.100',
                points: [
                    { x: 0.745, y: 51.347, dist: 0, time: 0, speed: 50 },
                    { x: 0.746, y: 51.347, dist: 50, time: 10, speed: 60 },
                    { x: 0.746, y: 51.348, dist: 100, time: 20, speed: 70 },
                    { x: 0.745, y: 51.348, dist: 150, time: 30, speed: 55 },
                    { x: 0.745, y: 51.347, dist: 200, time: 40, speed: 50 }
                ]
            },
            {
                lap_num: 2,
                lap_time: '0:43.500',
                points: [
                    { x: 0.745, y: 51.347, dist: 0, time: 0, speed: 52 },
                    { x: 0.746, y: 51.347, dist: 50, time: 9.8, speed: 62 },
                    { x: 0.746, y: 51.348, dist: 100, time: 19.5, speed: 72 },
                    { x: 0.745, y: 51.348, dist: 150, time: 29.2, speed: 58 },
                    { x: 0.745, y: 51.347, dist: 200, time: 39.0, speed: 52 }
                ]
            }
        ]
    };

    beforeEach(() => {
        mockContainer = createMockElement('div');

        vi.stubGlobal('document', {
            querySelector: vi.fn().mockImplementation((selector) => {
                if (selector === '#plot-container') return mockContainer;
                return null;
            }),
            createElement: vi.fn().mockImplementation((tagName) => createMockElement(tagName)),
            addEventListener: vi.fn()
        });

        function MockMap() {
            this.fitBounds = vi.fn();
            this.setMapTypeId = vi.fn();
            this.mapTypes = { set: vi.fn() };
            this.data = {
                setStyle: vi.fn(),
                addGeoJson: vi.fn(),
                setMap: vi.fn()
            };
        }
        function MockPolyline() {
            this.setOptions = vi.fn();
            this.setMap = vi.fn();
        }
        function MockMarker() { this.addListener = vi.fn(); }
        function MockInfoWindow() {}
        function MockBounds() {
            this.extend = vi.fn();
            this.isEmpty = vi.fn().mockReturnValue(false);
        }

        global.google = {
            maps: {
                Map: vi.fn().mockImplementation(function() { return new MockMap(); }),
                Polyline: vi.fn().mockImplementation(function() { return new MockPolyline(); }),
                Marker: vi.fn().mockImplementation(function() { return new MockMarker(); }),
                InfoWindow: vi.fn().mockImplementation(function() { return new MockInfoWindow(); }),
                LatLngBounds: vi.fn().mockImplementation(function() { return new MockBounds(); }),
                Size: vi.fn().mockImplementation(function(w, h) { return { width: w, height: h }; })
            }
        };
    });

    it('throws error if container element is not found', async () => {
        await expect(TrajectoryPlot.render('#non-existent-id')).rejects.toThrow(
            'TrajectoryPlot container not found: #non-existent-id'
        );
    });

    it('renders Google Maps plot correctly with pre-loaded telemetry data', async () => {
        const plot = await TrajectoryPlot.render(mockContainer, {
            data: mockTelemetryData,
            laps: [1, 2],
            color_mode: 'lap'
        });

        expect(plot.map).not.toBeNull();
        expect(global.google.maps.Map).toHaveBeenCalled();
        expect(global.google.maps.Polyline).toHaveBeenCalledTimes(2);

        const legend = mockContainer.querySelector('.trajectory-legend');
        expect(legend).not.toBeNull();
    });

    it('defaults height to 600px (1.5x higher)', async () => {
        const plot = await TrajectoryPlot.render(mockContainer, {
            data: mockTelemetryData,
            laps: [1]
        });

        expect(plot.options.height).toBe('600px');
    });

    it('renders full lap trajectory and uses crop range for initial map bounds focus', async () => {
        const plot = await TrajectoryPlot.render(mockContainer, {
            data: mockTelemetryData,
            laps: [1],
            start_m: 50,
            end_m: 150,
            color_mode: 'lap'
        });

        const croppedPoints = plot._cropPoints(mockTelemetryData.laps[0].points);
        expect(croppedPoints.length).toBe(3);
        expect(croppedPoints[0].dist).toBe(50);
        expect(croppedPoints[2].dist).toBe(150);

        // Polylines are drawn for the full 5-point lap
        expect(plot.lapPolylines[1].length).toBe(1);
    });

    it('renders extra information markers on Google Maps with canonical distance_m and time', async () => {
        const plot = await TrajectoryPlot.render(mockContainer, {
            data: mockTelemetryData,
            laps: [1],
            markers: [
                { time: 10.0, label: 'Apex T1', color: '#ff5252', description: 'Maximum G-force' },
                { distance_m: 150, label: 'Brake Point', color: '#38bdf8' },
                { distance_m: 100, label: 'Apex T2' }
            ]
        });

        expect(global.google.maps.Marker).toHaveBeenCalledTimes(3);
    });

    it('supports start and end crop aliases without _m suffix', async () => {
        const plot = await TrajectoryPlot.render(mockContainer, {
            data: mockTelemetryData,
            laps: [1],
            start: 50,
            end: 150
        });

        const croppedPoints = plot._cropPoints(mockTelemetryData.laps[0].points);
        expect(croppedPoints.length).toBe(3);
        expect(croppedPoints[0].dist).toBe(50);
        expect(croppedPoints[2].dist).toBe(150);
    });

    it('updates markers dynamically using updateMarkers()', async () => {
        const plot = await TrajectoryPlot.render(mockContainer, {
            data: mockTelemetryData,
            laps: [1],
            markers: [{ time: 10.0, label: 'Initial Marker' }]
        });

        expect(plot.options.markers[0].label).toBe('Initial Marker');

        plot.updateMarkers([{ time: 20.0, label: 'Updated Marker', color: '#10b981' }]);

        expect(plot.options.markers[0].label).toBe('Updated Marker');
    });

    it('supports coloring tracks by pedals, accel, speed, and delta_t', async () => {
        for (const mode of ['pedals', 'accel', 'speed', 'delta_t']) {
            global.google.maps.Polyline.mockClear();
            const plot = await TrajectoryPlot.render(mockContainer, {
                data: mockTelemetryData,
                laps: [1, 2],
                color_mode: mode
            });

            expect(plot.options.color_mode).toBe(mode);
            // In segment mode with two 5-point full laps, it should create 8 segment polylines (4 per lap)
            expect(global.google.maps.Polyline).toHaveBeenCalledTimes(8);
        }
    });

    it('switches map types between satellite, track limits (kartsim), and hybrid', async () => {
        const plot = await TrajectoryPlot.render(mockContainer, {
            data: mockTelemetryData,
            laps: [1]
        });

        plot.setMapType('kartsim');
        expect(plot.currentMapType).toBe('kartsim');
        expect(plot.map.setMapTypeId).toHaveBeenCalledWith('solid_dark');
        expect(plot.map.data.setMap).toHaveBeenCalledWith(plot.map);

        plot.setMapType('satellite');
        expect(plot.currentMapType).toBe('satellite');
        expect(plot.map.setMapTypeId).toHaveBeenCalledWith('satellite');
        expect(plot.map.data.setMap).toHaveBeenCalledWith(null);

        plot.setMapType('hybrid');
        expect(plot.currentMapType).toBe('hybrid');
        expect(plot.map.setMapTypeId).toHaveBeenCalledWith('satellite');
        expect(plot.map.data.setMap).toHaveBeenCalledWith(plot.map);
    });

    it('supports switching trajectory color modes dynamically', async () => {
        const plot = await TrajectoryPlot.render(mockContainer, {
            data: mockTelemetryData,
            laps: [1, 2],
            color_mode: 'lap'
        });

        expect(plot.polylines.length).toBe(2);

        plot.setColorMode('delta_t');
        expect(plot.currentColorMode).toBe('delta_t');
        expect(plot.polylines.length).toBe(8);

        plot.setColorMode('time');
        expect(plot.currentColorMode).toBe('time');
    });

    it('highlights target lap on hover in legend and restores on mouseleave', async () => {
        const plot = await TrajectoryPlot.render(mockContainer, {
            data: mockTelemetryData,
            laps: [1, 2],
            color_mode: 'lap'
        });

        plot.highlightLap(1, true);
        expect(plot.lapPolylines[1][0].polyline.setOptions).toHaveBeenCalledWith(
            expect.objectContaining({
                strokeWeight: 8,
                strokeOpacity: 1.0,
                zIndex: 1000
            })
        );
        expect(plot.lapPolylines[2][0].polyline.setOptions).toHaveBeenCalledWith(
            expect.objectContaining({
                strokeWeight: 2.5,
                strokeOpacity: 0.35,
                zIndex: 1
            })
        );

        plot.highlightLap(1, false);
        expect(plot.lapPolylines[1][0].polyline.setOptions).toHaveBeenCalledWith(
            expect.objectContaining({
                strokeWeight: 4,
                strokeOpacity: 0.95
            })
        );
    });

    it('respects explicit reference_lap in delta_t mode', async () => {
        const plot = await TrajectoryPlot.render(mockContainer, {
            data: mockTelemetryData,
            laps: [1, 2],
            color_mode: 'delta_t',
            reference_lap: 2
        });

        expect(plot.lapPolylines[2]).toBeDefined();
        expect(plot.lapPolylines[2].length).toBeGreaterThan(0);
    });

    it('disambiguates reference_lap across multiple sessions using tuple or object', async () => {
        const multiSessionData = [
            {
                session_id: 'session_A',
                date: '2026-08-02',
                track: 'Llandow',
                lap: 5,
                points: [{ x: -3.496, y: 51.434, dist: 0, time: 0, speed: 20 }, { x: -3.497, y: 51.435, dist: 50, time: 2.5, speed: 20 }]
            },
            {
                session_id: 'session_B',
                date: '2026-08-02',
                track: 'Llandow',
                lap: 5,
                points: [{ x: -3.496, y: 51.434, dist: 0, time: 0, speed: 20 }, { x: -3.497, y: 51.435, dist: 50, time: 2.4, speed: 21 }]
            }
        ];

        // Disambiguate using tuple
        const plot1 = await TrajectoryPlot.render(mockContainer, {
            data: multiSessionData,
            color_mode: 'delta_t',
            reference_lap: ['2026-08-02', 'Llandow', 'session_B', 5]
        });
        const refLap1 = plot1._resolveReferenceLap();
        expect(refLap1.session_id).toBe('session_B');
        expect(refLap1.lap_num).toBe(5);

        // Disambiguate using object
        const plot2 = await TrajectoryPlot.render(mockContainer, {
            data: multiSessionData,
            color_mode: 'delta_t',
            reference_lap: { session_id: 'session_A', lap: 5 }
        });
        const refLap2 = plot2._resolveReferenceLap();
        expect(refLap2.session_id).toBe('session_A');
        expect(refLap2.lap_num).toBe(5);

        // Default when omitted: first lap
        const plot3 = await TrajectoryPlot.render(mockContainer, {
            data: multiSessionData,
            color_mode: 'delta_t'
        });
        const refLap3 = plot3._resolveReferenceLap();
        expect(refLap3.session_id).toBe('session_A');
    });
});

