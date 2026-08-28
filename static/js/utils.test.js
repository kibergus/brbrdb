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
import { getMedian, parseLapTime, getPercentile, getSteeringTicks } from './utils';

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
});
