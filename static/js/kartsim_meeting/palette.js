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
 * Reusable color palette for groups and sessions.
 * Preserves Group A as Orange (#fb923c) and Group B as Sky Blue (#38bdf8).
 */
export const GROUP_PALETTE = [
    '#fb923c', // Orange (Group A)
    '#38bdf8', // Sky Blue (Group B)
    '#4ade80', // Emerald Green (Group C)
    '#c084fc', // Purple (Group D)
    '#f472b6', // Pink (Group E)
    '#facc15', // Amber / Gold (Group F)
    '#2dd4bf', // Teal (Group G)
    '#a3e635', // Lime (Group H)
    '#818cf8', // Indigo (Group I)
    '#fb7185', // Rose / Coral (Group J)
];

/**
 * Returns the color assigned to a group identifier (e.g. 'A', 'B', 'C'...) or index.
 */
export function getGroupColor(groupOrIndex) {
    if (typeof groupOrIndex === 'number') {
        return GROUP_PALETTE[groupOrIndex % GROUP_PALETTE.length];
    }
    if (typeof groupOrIndex === 'string') {
        const upper = groupOrIndex.trim().toUpperCase();
        if (upper.length === 1 && upper >= 'A' && upper <= 'Z') {
            const idx = upper.charCodeAt(0) - 65; // 'A' -> 0, 'B' -> 1, ...
            return GROUP_PALETTE[idx % GROUP_PALETTE.length];
        }
    }
    return GROUP_PALETTE[0];
}

/**
 * Converts a hex color (e.g. #fb923c) and alpha value into rgba string.
 */
export function hexToRgba(hex, alpha = 1.0) {
    if (!hex) return `rgba(255, 255, 255, ${alpha})`;
    let clean = hex.replace('#', '');
    if (clean.length === 3) {
        clean = clean.split('').map(c => c + c).join('');
    }
    const r = parseInt(clean.substring(0, 2), 16) || 0;
    const g = parseInt(clean.substring(2, 4), 16) || 0;
    const b = parseInt(clean.substring(4, 6), 16) || 0;
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
