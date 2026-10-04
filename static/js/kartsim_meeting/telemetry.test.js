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
import { getSteeringTicks, addTrace, renderSpeedPlot, renderDeltaPlot } from './plots_sync.js';
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

    describe('addTrace visibility preservation', () => {
        const lap = {
            id: 'lap1',
            lap_num: 1,
            points: [
                { dist: 0, sp_fl: 5, sp_rl: 2 },
                { dist: 10, sp_fl: 6, sp_rl: 3 }
            ]
        };

        beforeEach(() => {
            state.plotTraceVisibility = {};
            state.currentActivePlotTab = 'slide';
        });

        it('defaults trace visibility to true when not configured', () => {
            const data = [];
            addTrace(data, lap, 'sp_fl', 'blue', 2, 'solid', 'FL');
            expect(data.length).toBe(1);
            expect(data[0].visible).toBe(true);
        });

        it('applies legendonly visibility when configured in state.plotTraceVisibility', () => {
            state.plotTraceVisibility = {
                slide: {
                    FL: 'legendonly',
                    RL: true
                }
            };
            const data = [];
            addTrace(data, lap, 'sp_fl', 'blue', 2, 'solid', 'FL');
            addTrace(data, lap, 'sp_rl', 'red', 2, 'solid', 'RL');
            expect(data[0].visible).toBe('legendonly');
            expect(data[1].visible).toBe(true);
        });

        it('strips Group B suffix (B) to match base trace visibility', () => {
            state.plotTraceVisibility = {
                slide: {
                    FL: 'legendonly'
                }
            };
            const data = [];
            addTrace(data, lap, 'sp_fl', 'blue', 1.5, 'dash', 'FL (B)');
            expect(data[0].visible).toBe('legendonly');
        });
    });

    describe('renderSpeedPlot and renderDeltaPlot group visibility and styling', () => {
        let speedEl;
        let deltaEl;
        let mockNewPlot;
        let mockReact;
        let mockPurge;

        const lapA1 = {
            lapId: 'lapA1',
            lap_num: 1,
            lap_time: '1:00.000',
            points: [
                { dist: 0, speed: 60, time: 0 },
                { dist: 100, speed: 70, time: 5 },
                { dist: 200, speed: 80, time: 10 }
            ]
        };

        const lapA2 = {
            lapId: 'lapA2',
            lap_num: 2,
            lap_time: '1:01.000',
            points: [
                { dist: 0, speed: 58, time: 0 },
                { dist: 100, speed: 68, time: 5.2 },
                { dist: 200, speed: 78, time: 10.3 }
            ]
        };

        const lapB1 = {
            lapId: 'lapB1',
            lap_num: 3,
            lap_time: '1:02.000',
            points: [
                { dist: 0, speed: 55, time: 0 },
                { dist: 100, speed: 65, time: 5.5 },
                { dist: 200, speed: 75, time: 10.8 }
            ]
        };

        beforeEach(() => {
            speedEl = { id: 'plot-area-speed', remove: vi.fn() };
            deltaEl = { id: 'plot-area-delta', remove: vi.fn() };
            vi.stubGlobal('document', {
                getElementById: vi.fn(id => {
                    if (id === 'plot-area-speed') return speedEl;
                    if (id === 'plot-area-delta') return deltaEl;
                    return null;
                }),
                createElement: vi.fn(() => ({ remove: vi.fn() })),
                body: { appendChild: vi.fn() }
            });

            mockNewPlot = vi.fn().mockResolvedValue(speedEl);
            mockReact = vi.fn();
            mockPurge = vi.fn();
            global.Plotly = {
                newPlot: mockNewPlot,
                react: mockReact,
                purge: mockPurge
            };

            state.activeTab = 'map';
            state.groupSelectionsMap = {
                A: new Set(['lapA1', 'lapA2']),
                B: new Set(['lapB1'])
            };
            state.groupSelectionsStats = {
                A: new Set(),
                B: new Set()
            };
            state.groupASelection = state.groupSelectionsMap.A;
            state.groupBSelection = state.groupSelectionsMap.B;
            state.lapDataLookup = {
                lapA1,
                lapA2,
                lapB1
            };
            state.fastestGroupALap = lapA1;
            state.fastestGroupALapId = 'lapA1';
            state.fastestSelectedLap = lapA1;
            state.fastestSelectedLapId = 'lapA1';
            state.groupAVisibleMap = true;
            state.groupBVisibleMap = true;
            state.speedPlotInitialized = false;
            state.deltaPlotInitialized = false;
            state.trackData = { lap_length: 200 };
            state.sortMode = 'lap';
        });

        afterEach(() => {
            delete global.Plotly;
            vi.unstubAllGlobals();
        });

        it('renderSpeedPlot renders Group A in orange and Group B in blue with solid lines', () => {
            renderSpeedPlot();
            expect(mockNewPlot).toHaveBeenCalled();
            const data = mockNewPlot.mock.calls[0][1];
            expect(data.length).toBe(3);

            // Group A traces (orange, width 2)
            const traceA1 = data.find(t => t.name === 'Lap 1');
            const traceA2 = data.find(t => t.name === 'Lap 2');
            expect(traceA1).toBeDefined();
            expect(traceA1.line.color).toContain('251, 146, 60');
            expect(traceA1.line.width).toBe(2);

            expect(traceA2).toBeDefined();
            expect(traceA2.line.color).toContain('251, 146, 60');

            // Group B trace (blue, width 2, dash: 'solid')
            const traceB1 = data.find(t => t.name === 'Lap 3 (B)');
            expect(traceB1).toBeDefined();
            expect(traceB1.line.color).toContain('56, 189, 248');
            expect(traceB1.line.width).toBe(2);
            expect(traceB1.line.dash).toBe('solid');

            // Check indices recorded for both groups
            expect(state.bottomPlotIndices['plot-area-speed']['lapA1']).toBeDefined();
            expect(state.bottomPlotIndices['plot-area-speed']['lapB1']).toBeDefined();
        });

        it('renderSpeedPlot hides Group B when groupBVisibleMap is false', () => {
            state.groupBVisibleMap = false;
            renderSpeedPlot();
            expect(mockNewPlot).toHaveBeenCalled();
            const data = mockNewPlot.mock.calls[0][1];
            expect(data.length).toBe(2);
            expect(data.some(t => t.name.includes('(B)'))).toBe(false);
        });

        it('renderSpeedPlot hides Group A when groupAVisibleMap is false', () => {
            state.groupAVisibleMap = false;
            renderSpeedPlot();
            expect(mockNewPlot).toHaveBeenCalled();
            const data = mockNewPlot.mock.calls[0][1];
            expect(data.length).toBe(1);
            expect(data[0].name).toBe('Lap 3 (B)');
            expect(data[0].line.color).toContain('56, 189, 248');
        });

        it('renderSpeedPlot purges when both groups are hidden', () => {
            state.groupAVisibleMap = false;
            state.groupBVisibleMap = false;
            renderSpeedPlot();
            expect(mockPurge).toHaveBeenCalledWith(speedEl);
            expect(mockNewPlot).not.toHaveBeenCalled();
        });

        it('renderDeltaPlot renders Group A in orange and Group B in blue relative to reference lap', () => {
            renderDeltaPlot();
            expect(mockNewPlot).toHaveBeenCalled();
            const data = mockNewPlot.mock.calls[0][1];
            // lapA1 is refLap, so delta traces are lapA2 and lapB1
            expect(data.length).toBe(2);

            const traceA2 = data.find(t => t.name === 'Lap 2');
            expect(traceA2).toBeDefined();
            expect(traceA2.line.color).toContain('251, 146, 60');
            expect(traceA2.line.width).toBe(2);

            const traceB1 = data.find(t => t.name === 'Lap 3 (B)');
            expect(traceB1).toBeDefined();
            expect(traceB1.line.color).toContain('56, 189, 248');
            expect(traceB1.line.width).toBe(2);
            expect(traceB1.line.dash).toBe('solid');

            // Check indices recorded for both groups
            expect(state.bottomPlotIndices['plot-area-delta']['lapA2']).toBeDefined();
            expect(state.bottomPlotIndices['plot-area-delta']['lapB1']).toBeDefined();
        });

        it('renderDeltaPlot hides Group B when groupBVisibleMap is false', () => {
            state.groupBVisibleMap = false;
            renderDeltaPlot();
            expect(mockNewPlot).toHaveBeenCalled();
            const data = mockNewPlot.mock.calls[0][1];
            expect(data.length).toBe(1);
            expect(data[0].name).toBe('Lap 2');
        });

        it('renderDeltaPlot hides Group A when groupAVisibleMap is false and only plots Group B against ref lap', () => {
            state.groupAVisibleMap = false;
            renderDeltaPlot();
            expect(mockNewPlot).toHaveBeenCalled();
            const data = mockNewPlot.mock.calls[0][1];
            expect(data.length).toBe(1);
            expect(data[0].name).toBe('Lap 3 (B)');
            expect(data[0].line.color).toContain('56, 189, 248');
        });

        it('renderDeltaPlot purges when both groups are hidden', () => {
            state.groupAVisibleMap = false;
            state.groupBVisibleMap = false;
            renderDeltaPlot();
            expect(mockPurge).toHaveBeenCalledWith(deltaEl);
            expect(mockNewPlot).not.toHaveBeenCalled();
        });
    });
});

