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

import { describe, it, expect } from 'vitest';
import { getMedian, parseLapTime, getPercentile, getSteeringTicks, attachSmoothWheelZoom } from './utils';
import { vi } from 'vitest';

describe('utils.js', () => {
    describe('getMedian', () => {
        it('calculates median for odd number of elements', () => {
            expect(getMedian([1, 5, 2])).toBe(2);
        });

        it('calculates median for even number of elements', () => {
            expect(getMedian([1, 2, 3, 4])).toBe(2.5);
        });

        it('returns null for empty array', () => {
            expect(getMedian([])).toBeNull();
        });
    });

    describe('getPercentile', () => {
        it('calculates percentile correctly', () => {
            const arr = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
            expect(getPercentile(arr, 90)).toBeCloseTo(9.1);
            expect(getPercentile(arr, 0)).toBe(1);
            expect(getPercentile(arr, 100)).toBe(10);
            expect(getPercentile([5], 50)).toBe(5);
        });

        it('returns null for empty array', () => {
            expect(getPercentile([], 90)).toBeNull();
        });
    });

    describe('parseLapTime', () => {
        it('parses MM:SS.SSS format', () => {
            expect(parseLapTime('1:04.500')).toBe(64.5);
        });

        it('parses SS.SSS format', () => {
            expect(parseLapTime('45.123')).toBe(45.123);
        });

        it('handles non-string input', () => {
            expect(parseLapTime(45.5)).toBe(45.5);
        });

        it('returns large number for empty input', () => {
            expect(parseLapTime('')).toBe(999999);
        });
    });

    describe('getSteeringTicks', () => {
        it('formats negative ticks with R and positive ticks with L using absolute values', () => {
            const { tickvals, ticktext } = getSteeringTicks(-30, 30);
            expect(tickvals).toEqual([-30, -20, -10, 0, 10, 20, 30]);
            expect(ticktext).toEqual(['30 R', '20 R', '10 R', '0', '10 L', '20 L', '30 L']);
        });

        it('handles asymmetric bounds and step selection correctly', () => {
            const { tickvals, ticktext } = getSteeringTicks(-15, 45);
            expect(tickvals.includes(0)).toBe(true);
            expect(tickvals.includes(-20)).toBe(true);
            expect(tickvals.includes(50)).toBe(true);
            const idxNeg = tickvals.indexOf(-20);
            const idxZero = tickvals.indexOf(0);
            const idxPos = tickvals.indexOf(50);
            expect(ticktext[idxNeg]).toBe('20 R');
            expect(ticktext[idxZero]).toBe('0');
            expect(ticktext[idxPos]).toBe('50 L');
        });

        it('handles non-finite inputs with default range', () => {
            const { tickvals, ticktext } = getSteeringTicks(Infinity, -Infinity);
            expect(tickvals.length).toBeGreaterThan(0);
            expect(ticktext).toContain('0');
            expect(ticktext).toContain('45 R');
            expect(ticktext).toContain('45 L');
        });
    });

    describe('attachSmoothWheelZoom', () => {
        function createMockMap(initialZoom = 16) {
            let currentZoom = initialZoom;
            let currentCenter = { lat: 50.8, lng: -2.3 };
            const projection = {
                fromLatLngToPoint: vi.fn((latLng) => ({ x: 128, y: 128 })),
                fromPointToLatLng: vi.fn((pt) => ({ lat: 50.8 + (pt.y - 128) * 0.001, lng: -2.3 + (pt.x - 128) * 0.001 }))
            };

            return {
                getZoom: vi.fn(() => currentZoom),
                setZoom: vi.fn((z) => { currentZoom = z; }),
                getCenter: vi.fn(() => currentCenter),
                setCenter: vi.fn((c) => { currentCenter = c; }),
                getProjection: vi.fn(() => projection),
                projection
            };
        }

        function createMockContainer() {
            const listeners = {};
            return {
                listeners,
                addEventListener: vi.fn((evt, handler, opts) => {
                    listeners[evt] = listeners[evt] || [];
                    listeners[evt].push(handler);
                }),
                removeEventListener: vi.fn((evt, handler) => {
                    if (listeners[evt]) {
                        listeners[evt] = listeners[evt].filter(h => h !== handler);
                    }
                }),
                getBoundingClientRect: vi.fn(() => ({
                    left: 0,
                    top: 0,
                    width: 800,
                    height: 600
                }))
            };
        }

        it('attaches wheel listener and returns cleanup function', () => {
            const map = createMockMap();
            const container = createMockContainer();
            const cleanup = attachSmoothWheelZoom(map, container);

            expect(container.addEventListener).toHaveBeenCalledWith('wheel', expect.any(Function), { passive: false, capture: true });

            cleanup();
            expect(container.removeEventListener).toHaveBeenCalledWith('wheel', expect.any(Function), { capture: true });
        });

        it('zooms in with 2x smoother sensitivity on mouse wheel scroll up (deltaY = -120 -> +0.5 zoom)', () => {
            const map = createMockMap(16);
            const container = createMockContainer();
            attachSmoothWheelZoom(map, container);

            const wheelHandler = container.listeners['wheel'][0];
            const event = {
                preventDefault: vi.fn(),
                stopPropagation: vi.fn(),
                deltaY: -120,
                deltaMode: 0,
                clientX: 400,
                clientY: 300
            };

            wheelHandler(event);

            expect(event.preventDefault).toHaveBeenCalled();
            expect(event.stopPropagation).toHaveBeenCalled();
            expect(map.setZoom).toHaveBeenCalledWith(16.5);
        });

        it('zooms out with 2x smoother sensitivity on mouse wheel scroll down (deltaY = 120 -> -0.5 zoom)', () => {
            const map = createMockMap(16);
            const container = createMockContainer();
            attachSmoothWheelZoom(map, container);

            const wheelHandler = container.listeners['wheel'][0];
            const event = {
                preventDefault: vi.fn(),
                stopPropagation: vi.fn(),
                deltaY: 120,
                deltaMode: 0,
                clientX: 400,
                clientY: 300
            };

            wheelHandler(event);

            expect(map.setZoom).toHaveBeenCalledWith(15.5);
        });

        it('adjusts center when zooming into non-center cursor position', () => {
            const map = createMockMap(16);
            const container = createMockContainer();
            attachSmoothWheelZoom(map, container);

            const wheelHandler = container.listeners['wheel'][0];
            const event = {
                preventDefault: vi.fn(),
                stopPropagation: vi.fn(),
                deltaY: -120,
                deltaMode: 0,
                clientX: 600, // 200px to the right of center (width 800)
                clientY: 450  // 150px below center (height 600)
            };

            wheelHandler(event);

            expect(map.setCenter).toHaveBeenCalled();
            expect(map.projection.fromPointToLatLng).toHaveBeenCalled();
            expect(map.setZoom).toHaveBeenCalledWith(16.5);
        });

        it('clamps zoom to maximum 21 and minimum 3', () => {
            const mapMax = createMockMap(20.8);
            const container = createMockContainer();
            attachSmoothWheelZoom(mapMax, container);

            const wheelHandler = container.listeners['wheel'][0];
            wheelHandler({
                preventDefault: vi.fn(),
                stopPropagation: vi.fn(),
                deltaY: -240,
                deltaMode: 0
            });
            expect(mapMax.setZoom).toHaveBeenCalledWith(21);

            const mapMin = createMockMap(3.2);
            const containerMin = createMockContainer();
            attachSmoothWheelZoom(mapMin, containerMin);
            const wheelHandlerMin = containerMin.listeners['wheel'][0];
            wheelHandlerMin({
                preventDefault: vi.fn(),
                stopPropagation: vi.fn(),
                deltaY: 240,
                deltaMode: 0
            });
            expect(mapMin.setZoom).toHaveBeenCalledWith(3);
        });

        it('does not move map center when already at max or min zoom limits', () => {
            const mapAtMax = createMockMap(21);
            const containerAtMax = createMockContainer();
            attachSmoothWheelZoom(mapAtMax, containerAtMax);

            const wheelHandlerMax = containerAtMax.listeners['wheel'][0];
            wheelHandlerMax({
                preventDefault: vi.fn(),
                stopPropagation: vi.fn(),
                deltaY: -120, // scroll up to zoom in beyond max
                deltaMode: 0,
                clientX: 600,
                clientY: 450
            });

            expect(mapAtMax.setCenter).not.toHaveBeenCalled();

            const mapAtMin = createMockMap(3);
            const containerAtMin = createMockContainer();
            attachSmoothWheelZoom(mapAtMin, containerAtMin);

            const wheelHandlerMin = containerAtMin.listeners['wheel'][0];
            wheelHandlerMin({
                preventDefault: vi.fn(),
                stopPropagation: vi.fn(),
                deltaY: 120, // scroll down to zoom out beyond min
                deltaMode: 0,
                clientX: 600,
                clientY: 450
            });

            expect(mapAtMin.setCenter).not.toHaveBeenCalled();
        });

        it('detects when Google Maps clamps maxZoom dynamically and halts center movement', () => {
            let currentZoom = 19.8;
            const projection = {
                fromLatLngToPoint: vi.fn(() => ({ x: 128, y: 128 })),
                fromPointToLatLng: vi.fn(() => ({ lat: 50.8, lng: -2.3 }))
            };
            const map = {
                // Map clamps zoom at 20.0 (e.g. satellite tile max zoom)
                getZoom: vi.fn(() => currentZoom),
                setZoom: vi.fn((z) => { currentZoom = Math.min(20.0, z); }),
                getCenter: vi.fn(() => ({ lat: 50.8, lng: -2.3 })),
                setCenter: vi.fn(),
                getProjection: vi.fn(() => projection)
            };
            const container = createMockContainer();
            attachSmoothWheelZoom(map, container);

            const wheelHandler = container.listeners['wheel'][0];
            // First scroll zooms up to the 20.0 cap
            wheelHandler({
                preventDefault: vi.fn(),
                stopPropagation: vi.fn(),
                deltaY: -240,
                deltaMode: 0,
                clientX: 600,
                clientY: 450
            });

            // Map reached 20.0
            expect(map.getZoom()).toBe(20.0);
            map.setCenter.mockClear();

            // Subsequent scroll up at 20.0 must NOT move center
            wheelHandler({
                preventDefault: vi.fn(),
                stopPropagation: vi.fn(),
                deltaY: -120,
                deltaMode: 0,
                clientX: 600,
                clientY: 450
            });

            expect(map.setCenter).not.toHaveBeenCalled();

            // Zooming back OUT must work properly and decrease zoom
            wheelHandler({
                preventDefault: vi.fn(),
                stopPropagation: vi.fn(),
                deltaY: 120, // scroll down to zoom out
                deltaMode: 0,
                clientX: 600,
                clientY: 450
            });

            expect(map.getZoom()).toBe(19.5);
        });

        it('gracefully handles missing projection or container bounds', () => {
            const map = {
                getZoom: vi.fn(() => 16),
                setZoom: vi.fn(),
                getProjection: vi.fn(() => null),
                getCenter: vi.fn(() => null)
            };
            const container = createMockContainer();
            container.getBoundingClientRect = vi.fn(() => null);

            attachSmoothWheelZoom(map, container);
            const wheelHandler = container.listeners['wheel'][0];

            wheelHandler({
                preventDefault: vi.fn(),
                stopPropagation: vi.fn(),
                deltaY: -120,
                deltaMode: 0
            });

            expect(map.setZoom).toHaveBeenCalledWith(16.5);
        });

        it('returns early when map or container is null', () => {
            expect(attachSmoothWheelZoom(null, null)).toBeDefined();
        });
    });
});

