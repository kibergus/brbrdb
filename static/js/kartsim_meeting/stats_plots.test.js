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
import { getTurnDiffColor, getRedThreshold, computeGroupBTurnDiffs, renderStatsMinimap, renderTurnGapsPlot } from './stats_plots.js';
import { state } from './state.js';

// Mock Plotly to prevent canvas/layout errors in tests
vi.mock('plotly.js-dist-min', () => ({
    default: {
        react: vi.fn(),
        Plots: { resize: vi.fn() }
    }
}));

function createMockContainer() {
    return {
        id: 'stats-minimap-container',
        innerHTML: ''
    };
}

describe('stats_plots.js', () => {
    beforeEach(() => {
        global.document = {
            getElementById: vi.fn().mockReturnValue(null)
        };
        state.trackData = null;
        state.groupAVisible = true;
        state.groupBVisible = true;
    });

    describe('getRedThreshold', () => {
        it('returns minimum 0.5s for max gap <= 0.5s', () => {
            expect(getRedThreshold(0)).toBe(0.5);
            expect(getRedThreshold(0.2)).toBe(0.5);
            expect(getRedThreshold(0.5)).toBe(0.5);
        });

        it('returns 1.0s for max gap between 0.5s and 1.0s', () => {
            expect(getRedThreshold(0.6)).toBe(1.0);
            expect(getRedThreshold(1.0)).toBe(1.0);
        });

        it('returns 2.0s for max gap between 1.0s and 2.0s', () => {
            expect(getRedThreshold(1.2)).toBe(2.0);
            expect(getRedThreshold(2.0)).toBe(2.0);
        });

        it('returns 4.0s for max gap between 2.0s and 4.0s', () => {
            expect(getRedThreshold(2.5)).toBe(4.0);
        });
    });

    describe('getTurnDiffColor', () => {
        it('returns green for 0 second difference', () => {
            expect(getTurnDiffColor(0)).toBe('#22c55e');
        });

        it('returns yellow for 0.5 second difference', () => {
            expect(getTurnDiffColor(0.5)).toBe('#eab308');
        });

        it('returns red for 1.0 second difference', () => {
            expect(getTurnDiffColor(1.0)).toBe('#ef4444');
        });

        it('clamps difference above 1.0 to red', () => {
            expect(getTurnDiffColor(1.5)).toBe('#ef4444');
        });

        it('handles null, undefined, or negative values as 0s (green)', () => {
            expect(getTurnDiffColor(null)).toBe('#22c55e');
            expect(getTurnDiffColor(undefined)).toBe('#22c55e');
            expect(getTurnDiffColor(-0.5)).toBe('#22c55e');
        });
    });

    describe('computeGroupBTurnDiffs', () => {
        it('returns array of zeroes when laps or Group B laps are empty', () => {
            expect(computeGroupBTurnDiffs([], 3)).toEqual([0, 0, 0]);
            const groupALapsOnly = [{ group: 'A', turn_times: [10, 12, 14] }];
            expect(computeGroupBTurnDiffs(groupALapsOnly, 3)).toEqual([0, 0, 0]);
        });

        it('correctly calculates mean - min difference for Group B laps', () => {
            const laps = [
                { group: 'A', turn_times: [8, 8, 8] },
                { group: 'B', turn_times: [10, 15, 20] },
                { group: 'B', turn_times: [12, 17, 22] }
            ];
            // Turn 0: min=10, mean=11 -> diff=1
            // Turn 1: min=15, mean=16 -> diff=1
            // Turn 2: min=20, mean=21 -> diff=1
            const diffs = computeGroupBTurnDiffs(laps, 3);
            expect(diffs[0]).toBeCloseTo(1.0);
            expect(diffs[1]).toBeCloseTo(1.0);
            expect(diffs[2]).toBeCloseTo(1.0);
        });
    });

    describe('renderStatsMinimap', () => {
        it('renders placeholder when state.trackData is missing', () => {
            const container = createMockContainer();
            global.document = { getElementById: (id) => id === 'stats-minimap-container' ? container : null };

            renderStatsMinimap([]);
            expect(container.innerHTML).toContain('No track geometry available');
        });

        it('renders track SVG with turn paths, ticks, and labels when trackData is available', () => {
            const container = createMockContainer();
            global.document = { getElementById: (id) => id === 'stats-minimap-container' ? container : null };

            state.trackData = {
                lap_length: 100,
                turns: [
                    { name: 'Turn 1', start: 10, apex: [20], end: 30 },
                    { name: 'Turn 2', start: 60, apex: [70], end: 80 }
                ],
                center_line: [
                    { lat: 50.0, lon: -3.0, dist: 0 },
                    { lat: 50.001, lon: -3.0, dist: 25 },
                    { lat: 50.001, lon: -3.001, dist: 50 },
                    { lat: 50.0, lon: -3.001, dist: 75 },
                    { lat: 50.0, lon: -3.0, dist: 100 }
                ]
            };

            const laps = [
                { group: 'B', turn_times: [10, 15] },
                { group: 'B', turn_times: [11, 16] }
            ];

            renderStatsMinimap(laps);

            expect(container.innerHTML).toContain('<svg');
            expect(container.innerHTML).toContain('minimap-underlines');
            expect(container.innerHTML).toContain('minimap-centerlines');
            expect(container.innerHTML).toContain('minimap-ticks');
            expect(container.innerHTML).toContain('minimap-labels');
            expect(container.innerHTML).toContain('Turn 1');
            expect(container.innerHTML).toContain('Turn 2');
        });
    });

    describe('renderTurnGapsPlot dynamic y-axis scaling', () => {
        let mockContainer;
        let mockPlotlyReact;

        beforeEach(() => {
            mockContainer = { id: 'stats-plot-turn-gaps' };
            mockPlotlyReact = vi.fn();
            global.Plotly = { react: mockPlotlyReact, Plots: { resize: vi.fn() } };
            global.document = { getElementById: (id) => id === 'stats-plot-turn-gaps' ? mockContainer : null };

            state.trackData = {
                turns: [
                    { name: 'Turn 1', start: 10, end: 30 },
                    { name: 'Turn 2', start: 60, end: 80 }
                ]
            };
        });

        it('scales y-axis tightly when all gaps are small (< 0.4s)', () => {
            const laps = [
                { group: 'A', is_valid: true, turn_times: [10.0, 15.0], lapId: 'l1', lap_num: 1 },
                { group: 'A', is_valid: true, turn_times: [10.25, 15.15], lapId: 'l2', lap_num: 2 }
            ];

            renderTurnGapsPlot(laps);

            expect(mockPlotlyReact).toHaveBeenCalled();
            const layout = mockPlotlyReact.mock.calls[0][2];
            expect(layout.yaxis.range[1]).toBeLessThanOrEqual(0.5);
            expect(layout.yaxis.tickvals).toContain(0.3);
            expect(layout.yaxis.tickvals).not.toContain(2.0);
        });

        it('caps y-axis at 2.0s when data has larger spread (< 2.0s)', () => {
            const laps = [
                { group: 'A', is_valid: true, turn_times: [10.0, 15.0], lapId: 'l1', lap_num: 1 },
                { group: 'A', is_valid: true, turn_times: [11.8, 16.5], lapId: 'l2', lap_num: 2 }
            ];

            renderTurnGapsPlot(laps);

            expect(mockPlotlyReact).toHaveBeenCalled();
            const layout = mockPlotlyReact.mock.calls[0][2];
            expect(layout.yaxis.range[1]).toBeCloseTo(2.12, 1);
            expect(layout.yaxis.tickvals).toEqual([0, 0.5, 1.0, 1.5, 2.0]);
        });

        it('plots outliers (> 2.0s) and keeps 2.0s + outlier range', () => {
            const laps = [
                { group: 'A', is_valid: true, turn_times: [10.0, 15.0], lapId: 'l1', lap_num: 1 },
                { group: 'A', is_valid: true, turn_times: [14.5, 15.1], lapId: 'l2', lap_num: 2 } // Turn 1 gap is 4.5s (outlier)
            ];

            renderTurnGapsPlot(laps);

            expect(mockPlotlyReact).toHaveBeenCalled();
            const data = mockPlotlyReact.mock.calls[0][1];
            const layout = mockPlotlyReact.mock.calls[0][2];
            expect(data.some(d => d.type === 'scatter')).toBe(true);
            expect(layout.yaxis.range[1]).toBeCloseTo(2.12, 1);
            expect(layout.yaxis.tickvals).toEqual([0, 0.5, 1.0, 1.5, 2.0]);
        });
    });
});
