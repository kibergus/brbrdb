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
import { showTab, toggleGroupVisibility, showStatsSubTab, resetProgressionLoaded } from './lap_selection.js';
import { state } from './state.js';

vi.mock('./map.js', () => ({
    initMap: vi.fn(),
    updateTrackLimitsVisibility: vi.fn()
}));

vi.mock('./plots_sync.js', () => ({
    updateTelemetryPlots: vi.fn(),
    updateAccelerationPlot: vi.fn(),
    updateSlipAnglePlot: vi.fn(),
    renderExpandablePlots: vi.fn(),
    startSyncLoop: vi.fn(),
    updateDeltaYLim: vi.fn()
}));

vi.mock('./stats_plots.js', () => ({
    renderStatsPlots: vi.fn()
}));

vi.mock('./url_sync.js', () => ({
    debouncedUpdateURL: vi.fn()
}));

describe('lap_selection.js showTab UI changes', () => {
    let mockSidePanel;

    beforeEach(() => {
        mockSidePanel = { style: {} };

        vi.stubGlobal('document', {
            querySelectorAll: vi.fn().mockReturnValue([]),
            getElementById: vi.fn().mockReturnValue(null),
            querySelector: vi.fn().mockImplementation((selector) => {
                if (selector === '.map-side-panel') {
                    return mockSidePanel;
                }
                return null;
            })
        });

        // Reset state selections
        state.groupASelection = new Set();
        state.groupBSelection = new Set();
        state.lapPolylines = {};
    });

    it('sets state.activeTab and hides sidebar panel when Stats tab is selected', () => {
        showTab('stats');
        expect(state.activeTab).toBe('stats');
        expect(mockSidePanel.style.display).toBe('none');
    });

    it('sets state.activeTab and shows sidebar panel when Map tab is selected', () => {
        showTab('map');
        expect(state.activeTab).toBe('map');
        expect(mockSidePanel.style.display).toBe('flex');
    });

    it('does not update map polylines visibility when toggling group visibility in stats tab', () => {
        const setMapSpy = vi.fn();
        state.lapPolylines = { 'lap-1': [{ setMap: setMapSpy }] };
        state.activeTab = 'stats';

        toggleGroupVisibility('B');

        expect(setMapSpy).not.toHaveBeenCalled();
    });

    it('updates map polylines visibility when toggling group visibility in map tab', () => {
        const setMapSpy = vi.fn();
        state.lapPolylines = { 'lap-1': [{ setMap: setMapSpy }] };
        state.activeTab = 'map';

        toggleGroupVisibility('B');

        expect(setMapSpy).toHaveBeenCalled();
    });
});

describe('showStatsSubTab', () => {
    let mockOverviewBtn;
    let mockProgressionBtn;
    let mockOverviewPane;
    let mockProgressionPane;
    let mockContainer;

    beforeEach(() => {
        resetProgressionLoaded();
        mockOverviewBtn = { classList: { toggle: vi.fn() } };
        mockProgressionBtn = { classList: { toggle: vi.fn() } };
        mockOverviewPane = { classList: { toggle: vi.fn() }, style: {} };
        mockProgressionPane = {
            classList: { toggle: vi.fn() },
            style: {},
            dataset: { league: 'kartsim', class: 'iame', track: 'Dunkeswell', date: '2026-07-29' }
        };
        mockContainer = { innerHTML: '', appendChild: vi.fn() };

        vi.stubGlobal('document', {
            getElementById: vi.fn().mockImplementation((id) => {
                if (id === 'stats-tab-btn-overview') return mockOverviewBtn;
                if (id === 'stats-tab-btn-progression') return mockProgressionBtn;
                if (id === 'stats-subtab-overview') return mockOverviewPane;
                if (id === 'stats-subtab-progression') return mockProgressionPane;
                if (id === 'stats-progression-container') return mockContainer;
                return null;
            }),
            createElement: vi.fn().mockImplementation(() => ({
                className: '',
                innerHTML: '',
                querySelector: vi.fn().mockReturnValue(null)
            }))
        });

        vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
            json: () => Promise.resolve({
                plots: [
                    { condition: 'Dry', title: 'Dry Conditions', plot_url: '/track_plot/kartsim/iame/Dunkeswell/Dry.png' }
                ]
            })
        }));
    });

    it('activates overview subtab and hides progression pane', () => {
        showStatsSubTab('overview');
        expect(mockOverviewBtn.classList.toggle).toHaveBeenCalledWith('active', true);
        expect(mockProgressionBtn.classList.toggle).toHaveBeenCalledWith('active', false);
        expect(mockOverviewPane.style.display).toBe('block');
        expect(mockProgressionPane.style.display).toBe('none');
    });

    it('activates progression subtab and fetches progression plots', () => {
        showStatsSubTab('progression');
        expect(mockProgressionBtn.classList.toggle).toHaveBeenCalledWith('active', true);
        expect(mockProgressionPane.style.display).toBe('block');
        expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/api/track_progression'));
    });
});

