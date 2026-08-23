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
        },
        querySelectorAll(selector) {
            const results = [];
            const walk = (el) => {
                if (!el || !el.children) return;
                for (const c of el.children) {
                    if (selector.startsWith('.') && c.className === selector.slice(1)) results.push(c);
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
            createElement: vi.fn().mockImplementation((tagName) => createMockElement(tagName))
        });

        function MockMap() {
            this.fitBounds = vi.fn();
            this.data = {
                setStyle: vi.fn(),
                addGeoJson: vi.fn()
            };
        }
        function MockPolyline() {}
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
                LatLngBounds: vi.fn().mockImplementation(function() { return new MockBounds(); })
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
            laps: [1, 2]
        });

        expect(plot.map).not.toBeNull();
        expect(global.google.maps.Map).toHaveBeenCalled();
        expect(global.google.maps.Polyline).toHaveBeenCalledTimes(2);

        const legend = mockContainer.querySelector('.trajectory-legend');
        expect(legend).not.toBeNull();
    });

    it('supports distance cropping using start_m and end_m', async () => {
        const plot = await TrajectoryPlot.render(mockContainer, {
            data: mockTelemetryData,
            laps: [1],
            start_m: 50,
            end_m: 150
        });

        const croppedPoints = plot._cropPoints(mockTelemetryData.laps[0].points);
        expect(croppedPoints.length).toBe(3);
        expect(croppedPoints[0].dist).toBe(50);
        expect(croppedPoints[2].dist).toBe(150);
    });

    it('renders extra information markers on Google Maps', async () => {
        const plot = await TrajectoryPlot.render(mockContainer, {
            data: mockTelemetryData,
            laps: [1],
            markers: [
                { time: 10.0, label: 'Apex T1', color: '#ff5252', description: 'Maximum G-force' },
                { dist: 150, label: 'Brake Point', color: '#38bdf8' }
            ]
        });

        expect(global.google.maps.Marker).toHaveBeenCalledTimes(2);
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
            // In segment mode with two 5-point laps, it should create 8 segment polylines (4 per lap)
            expect(global.google.maps.Polyline).toHaveBeenCalledTimes(8);
        }
    });
});
