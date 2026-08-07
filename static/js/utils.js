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

/**
 * Shared utility functions for the karting analysis dashboard.
 * This file can be used as an ES module or included as a script.
 */

/**
 * Calculates the median of an array of numbers.
 * @param {number[]} arr - The array of numbers.
 * @returns {number|null} The median or null if the array is empty.
 */
export const getMedian = (arr) => {
    if (arr.length === 0) return null;
    const sorted = [...arr].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 !== 0 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
};

/**
 * Parses a lap time string (e.g., "1:04.532" or "64.532") into seconds.
 * @param {string|number} timeStr - The time string to parse.
 * @returns {number} The time in seconds.
 */
export function parseLapTime(timeStr) {
    if (!timeStr) return 999999;
    if (typeof timeStr !== 'string') timeStr = String(timeStr);
    const parts = timeStr.split(':');
    if (parts.length === 2) {
        return parseFloat(parts[0]) * 60 + parseFloat(parts[1]);
    }
    return parseFloat(parts[0]);
}

/**
 * Formats a time in seconds into a string (e.g., 64.532 -> "1:04.532").
 * @param {number} seconds - The time in seconds.
 * @returns {string} The formatted time string.
 */
export function formatLapTime(seconds) {
    if (seconds === Infinity || seconds === 0 || isNaN(seconds)) return '-';
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    if (mins > 0) {
        return `${mins}:${secs.toFixed(3).padStart(6, '0')}`;
    }
    return secs.toFixed(3);
}

/**
 * Calculates the percentile of an array of numbers.
 * @param {number[]} arr - The array of numbers.
 * @param {number} percentile - The percentile to calculate (0 to 100).
 * @returns {number|null} The percentile value or null if the array is empty.
 */
export const getPercentile = (arr, percentile) => {
    if (arr.length === 0) return null;
    const sorted = [...arr].sort((a, b) => a - b);
    const index = (sorted.length - 1) * (percentile / 100);
    const lower = Math.floor(index);
    const upper = Math.ceil(index);
    const weight = index - lower;
    if (upper >= sorted.length) return sorted[sorted.length - 1];
    return sorted[lower] * (1 - weight) + sorted[upper] * weight;
};

// Attach to window if running in a browser environment to make functions available globally
if (typeof window !== 'undefined') {
    window.getMedian = getMedian;
    window.getPercentile = getPercentile;
    window.parseLapTime = parseLapTime;
    window.formatLapTime = formatLapTime;
}
