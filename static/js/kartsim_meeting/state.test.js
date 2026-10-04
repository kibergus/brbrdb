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

import { describe, it, expect, beforeEach } from 'vitest';
import { state } from './state.js';

describe('state.js group visibility getters/setters', () => {
    beforeEach(() => {
        // Reset state
        state.activeTab = 'map';
        state.groupAVisibleMap = true;
        state.groupBVisibleMap = false;
        state.groupAVisibleStats = true;
        state.groupBVisibleStats = true;
    });

    it('returns map visibility when activeTab is map', () => {
        state.activeTab = 'map';
        expect(state.groupAVisible).toBe(true);
        expect(state.groupBVisible).toBe(false);
    });

    it('returns stats visibility when activeTab is stats', () => {
        state.activeTab = 'stats';
        expect(state.groupAVisible).toBe(true);
        expect(state.groupBVisible).toBe(true);
    });

    it('sets map visibility when activeTab is map', () => {
        state.activeTab = 'map';
        state.groupAVisible = false;
        state.groupBVisible = true;
        expect(state.groupAVisibleMap).toBe(false);
        expect(state.groupBVisibleMap).toBe(true);
        expect(state.groupAVisibleStats).toBe(true); // stats remains unchanged
        expect(state.groupBVisibleStats).toBe(true);
    });

    it('sets stats visibility when activeTab is stats', () => {
        state.activeTab = 'stats';
        state.groupAVisible = false;
        state.groupBVisible = false;
        expect(state.groupAVisibleStats).toBe(false);
        expect(state.groupBVisibleStats).toBe(false);
        expect(state.groupAVisibleMap).toBe(true); // map remains unchanged
        expect(state.groupBVisibleMap).toBe(false);
    });
});

describe('state.js group selections getters/setters', () => {
    beforeEach(() => {
        state.activeTab = 'map';
        state.groupSelectionsMap = { A: new Set(['lap-map-1']), B: new Set(['lap-map-2']) };
        state.groupSelectionsStats = { A: new Set(['lap-stats-1', 'lap-stats-2']), B: new Set(['lap-stats-3']) };
    });

    it('returns map selections when activeTab is map', () => {
        state.activeTab = 'map';
        expect(state.groupSelections.A.has('lap-map-1')).toBe(true);
        expect(state.groupSelections.A.has('lap-stats-1')).toBe(false);
    });

    it('returns stats selections when activeTab is stats', () => {
        state.activeTab = 'stats';
        expect(state.groupSelections.A.has('lap-stats-1')).toBe(true);
        expect(state.groupSelections.A.has('lap-map-1')).toBe(false);
    });

    it('getGroupSelections explicitly returns map or stats selections regardless of activeTab', () => {
        state.activeTab = 'map';
        expect(state.getGroupSelections('stats').A.has('lap-stats-1')).toBe(true);
        expect(state.getGroupSelections('map').A.has('lap-map-1')).toBe(true);
    });

    it('mutating groupSelections in stats tab does not affect map tab', () => {
        state.activeTab = 'stats';
        state.groupSelections.A.add('lap-stats-new');
        expect(state.groupSelectionsStats.A.has('lap-stats-new')).toBe(true);
        expect(state.groupSelectionsMap.A.has('lap-stats-new')).toBe(false);
    });
});

