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
import { findSegmentIndex, getPointAtDistance, getPointsAtDistancesMonotonic, getSpeedAtDistance, precalculateLapData, getMinSpeedInRange, fetchTelemetryChannel } from './telemetry.js';
import { getSteeringTicks } from './plots_sync.js';
import { state } from './state.js';

describe('telemetry.js', () => {
    describe('findSegmentIndex', () => {
        const points = [
            { dist: 0 },
            { dist: 10 },
            { dist: 20 },
            { dist: 30 }
        ];

        it('finds correct segment for middle value', () => {
            expect(findSegmentIndex(points, 15)).toBe(1);
        });

        it('finds segment at start boundary', () => {
            expect(findSegmentIndex(points, 0)).toBe(0);
        });

        it('finds segment at end boundary', () => {
            expect(findSegmentIndex(points, 30)).toBe(2);
        });

        it('returns -1 for out of range value', () => {
            expect(findSegmentIndex(points, 35)).toBe(-1);
            expect(findSegmentIndex(points, -5)).toBe(-1);
        });

        it('handles empty points array', () => {
            expect(findSegmentIndex([], 10)).toBe(-1);
        });
    });

    describe('getPointAtDistance', () => {
        const lap = {
            points: [
                { dist: 0, speed: 50, time: 0 },
                { dist: 10, speed: 70, time: 1 },
                { dist: 20, speed: 90, time: 2 }
            ]
        };

        it('interpolates values correctly', () => {
            const p = getPointAtDistance(lap, 5);
            expect(p.speed).toBe(60);
            expect(p.time).toBe(0.5);
        });

        it('returns exact point if distance matches', () => {
            const p = getPointAtDistance(lap, 10);
            expect(p.speed).toBe(70);
            expect(p.time).toBe(1);
        });

        it('handles distance below range', () => {
            const p = getPointAtDistance(lap, -5);
            expect(p.dist).toBe(0);
            expect(p.speed).toBe(50);
        });

        it('handles distance above range', () => {
            const p = getPointAtDistance(lap, 25);
            expect(p.dist).toBe(20);
            expect(p.speed).toBe(90);
        });
    });

    describe('getPointsAtDistancesMonotonic', () => {
        const lap = {
            points: [
                { dist: 0, speed: 50, time: 0 },
                { dist: 10, speed: 70, time: 1 },
                { dist: 20, speed: 90, time: 2 }
            ]
        };

        it('interpolates multiple monotonic points correctly', () => {
            const results = getPointsAtDistancesMonotonic(lap, [5, 10, 15], ['speed', 'time']);
            expect(results.length).toBe(3);
            expect(results[0].speed).toBe(60);
            expect(results[0].time).toBe(0.5);
            expect(results[1].speed).toBe(70);
            expect(results[1].time).toBe(1);
            expect(results[2].speed).toBe(80);
            expect(results[2].time).toBe(1.5);
        });

        it('handles boundary and out of range inputs', () => {
            const results = getPointsAtDistancesMonotonic(lap, [-5, 25], ['speed']);
            expect(results.length).toBe(2);
            expect(results[0].speed).toBe(50);
            expect(results[1].speed).toBe(90);
        });

        it('returns empty array for empty inputs', () => {
            expect(getPointsAtDistancesMonotonic({ points: [] }, [5])).toEqual([]);
        });
    });

    describe('precalculateLapData', () => {
        it('identifies braking points', () => {
            const lap = {
                points: [
                    { dist: 0, brake: 0 },
                    { dist: 10, brake: 10 }, // onset
                    { dist: 20, brake: 20 },
                    { dist: 30, brake: 0 },
                    { dist: 40, brake: 60 } // onset
                ]
            };
            precalculateLapData(lap);
            expect(lap.brakingPoints).toEqual([10, 40]);
        });

        it('ignores brake values <= 5', () => {
            const lap = {
                points: [
                    { dist: 0, brake: 0 },
                    { dist: 10, brake: 4 },
                    { dist: 20, brake: 3 }
                ]
            };
            precalculateLapData(lap);
            expect(lap.brakingPoints).toEqual([]);
        });

        it('calculates acceleration correctly for all points', () => {
            // Speeds: 36 km/h (10 m/s), 72 km/h (20 m/s), 108 km/h (30 m/s)
            // Times: 0s, 1s, 2s
            const lap = {
                points: [
                    { time: 0, speed: 36 },
                    { time: 1, speed: 72 },
                    { time: 2, speed: 108 }
                ]
            };
            precalculateLapData(lap);
            // Point 0 (forward diff): (20 - 10) / 1 = 10 m/s^2
            expect(lap.points[0].acceleration).toBeCloseTo(10);
            // Point 1 (central diff): (30 - 10) / 2 = 10 m/s^2
            expect(lap.points[1].acceleration).toBeCloseTo(10);
            // Point 2 (backward diff): (30 - 20) / 1 = 10 m/s^2
            expect(lap.points[2].acceleration).toBeCloseTo(10);
        });
    });

    describe('getMinSpeedInRange', () => {
        const lap = {
            points: [
                { dist: 0, speed: 80 },
                { dist: 10, speed: 75 },
                { dist: 20, speed: 65 },
                { dist: 30, speed: 60 },
                { dist: 40, speed: 70 },
                { dist: 50, speed: 85 }
            ]
        };

        it('finds minimum speed in a normal range', () => {
            expect(getMinSpeedInRange(lap, 15, 45)).toBe(60);
        });

        it('returns null if start/end parameters are missing and no fallback is given', () => {
            expect(getMinSpeedInRange(lap, null, null)).toBeNull();
        });

        it('uses fallback apex when start/end are missing', () => {
            // fallbackApex = 30. range is [5, 55], which covers points at 10, 20, 30, 40, 50
            expect(getMinSpeedInRange(lap, null, null, 30)).toBe(60);
        });

        it('handles wrap-around corners (start > end)', () => {
            // start = 40, end = 15. covers points at 40, 50, 0, 10.
            // speeds: 70, 85, 80, 75. min is 70.
            expect(getMinSpeedInRange(lap, 40, 15)).toBe(70);
        });

        it('handles empty points or invalid lap data', () => {
            expect(getMinSpeedInRange({ points: [] }, 0, 50)).toBeNull();
            expect(getMinSpeedInRange({}, 0, 50)).toBeNull();
        });
    });

    describe('fetchTelemetryChannel', () => {
        beforeEach(() => {
            global.window = {
                location: { origin: 'http://localhost' },
                KART_CONFIG: { getTrackPointsUrl: 'http://localhost/api/telemetry' }
            };
        });

        afterEach(() => {
            delete global.window;
        });

        it('fetches and decodes binary channel data correctly', async () => {
            const meanVal = 10.5;
            const scale = 0.02;
            const deltas = new Int16Array([100, -50, -32768, 20]);
            
            const buffer = new ArrayBuffer(16 + deltas.byteLength);
            const view = new DataView(buffer);
            view.setFloat64(0, meanVal, true);
            view.setFloat64(8, scale, true);
            const deltaDest = new Int16Array(buffer, 16);
            deltaDest.set(deltas);

            global.fetch = vi.fn().mockResolvedValue({
                ok: true,
                arrayBuffer: () => Promise.resolve(buffer)
            });

            const result = await fetchTelemetryChannel('session1', 'Speed');
            expect(result).toEqual([12.5, 11.5, null, 11.9]);
        });

        it('returns empty array if arrayBuffer is too short', async () => {
            const buffer = new ArrayBuffer(8);
            global.fetch = vi.fn().mockResolvedValue({
                ok: true,
                arrayBuffer: () => Promise.resolve(buffer)
            });

            const result = await fetchTelemetryChannel('session1', 'Speed');
            expect(result).toEqual([]);
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
});
