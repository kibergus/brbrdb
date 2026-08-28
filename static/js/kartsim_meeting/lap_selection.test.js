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
import { showTab, toggleGroupVisibility, showStatsSubTab, resetProgressionLoaded, toggleSidePanel, collapseSidePanel, expandSidePanel, initSidePanelResizer } from './lap_selection.js';
import { state } from './state.js';

vi.mock('./map.js', () => ({
    initMap: vi.fn(),
    updateTrackLimitsVisibility: vi.fn(),
    updateDistanceMarker: vi.fn(),
    calculateBoundsZoom: vi.fn(() => 15),
    updateAllPolylineColors: vi.fn(),
    getReferenceLap: vi.fn(() => null)
}));

vi.mock('./plots_sync.js', () => ({
    updateTelemetryPlots: vi.fn(),
    updateAccelerationPlot: vi.fn(),
    updateSlipAnglePlot: vi.fn(),
    renderExpandablePlots: vi.fn(),
    startSyncLoop: vi.fn(),
    updateDeltaYLim: vi.fn(),
    updateExpandablePlotsVisibility: vi.fn()
}));

vi.mock('./stats_plots.js', () => ({
    renderStatsPlots: vi.fn()
}));

vi.mock('./url_sync.js', () => ({
    debouncedUpdateURL: vi.fn()
}));

describe('lap_selection.js showTab UI changes', () => {
    let mockSidePanel;
    let mockExpandablePlots;
    let mockMapControlsBar;

    beforeEach(() => {
        mockSidePanel = { style: {} };
        mockExpandablePlots = { style: {} };
        mockMapControlsBar = { style: {} };

        vi.stubGlobal('document', {
            querySelectorAll: vi.fn().mockReturnValue([]),
            getElementById: vi.fn().mockImplementation((id) => {
                if (id === 'expandable-plots-block') return mockExpandablePlots;
                return null;
            }),
            querySelector: vi.fn().mockImplementation((selector) => {
                if (selector === '.map-side-panel') {
                    return mockSidePanel;
                }
                if (selector === '.map-controls-bar') {
                    return mockMapControlsBar;
                }
                return null;
            })
        });

        // Reset state selections
        state.groupASelection = new Set();
        state.groupBSelection = new Set();
        state.lapPolylines = {};
    });

    it('sets state.activeTab and hides sidebar panel, bottom plots, and slider when Stats tab is selected', () => {
        showTab('stats');
        expect(state.activeTab).toBe('stats');
        expect(mockSidePanel.style.display).toBe('none');
        expect(mockExpandablePlots.style.display).toBe('none');
        expect(mockMapControlsBar.style.display).toBe('none');
    });

    it('sets state.activeTab and shows sidebar panel, bottom plots, and slider when Map tab is selected', () => {
        showTab('map');
        expect(state.activeTab).toBe('map');
        expect(mockSidePanel.style.display).toBe('flex');
        expect(mockExpandablePlots.style.display).toBe('flex');
        expect(mockMapControlsBar.style.display).toBe('flex');
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

describe('selectTurnAndSwitchToMap', () => {
    it('sets turn index, selects fastest lap in that turn for Group A, and switches to map tab', async () => {
        const { selectTurnAndSwitchToMap } = await import('./lap_selection.js');

        state.trackData = {
            lap_length: 1000,
            center_line: [
                { dist: 0, lat: 51.0, lng: -0.1 },
                { dist: 50, lat: 51.001, lng: -0.101 },
                { dist: 80, lat: 51.002, lng: -0.102 },
                { dist: 120, lat: 51.003, lng: -0.103 },
                { dist: 500, lat: 51.005, lng: -0.105 }
            ],
            turns: [
                { name: 'Turn 1', start: 50, end: 120, apex: [80] },
                { name: 'Turn 2', start: 200, end: 300, apex: [250] }
            ]
        };

        state.allSessionsData = [
            {
                session_id: 'sess1',
                laps: [
                    { lap_num: 1, is_valid: true, turn_times: [5.2, 8.4] },
                    { lap_num: 2, is_valid: true, turn_times: [4.8, 8.9] },
                    { lap_num: 3, is_valid: false, turn_times: [3.0, 7.0] } // invalid lap ignored
                ]
            }
        ];

        const mockTurnSelector = { value: '0', style: {} };
        const mockSidePanel = { style: {} };
        const mockExpandablePlots = { style: {} };
        const mockMapControlsBar = { style: {} };
        const mockSlider = { value: 0 };
        const mockDisplay = { textContent: '' };

        const mockFitBounds = vi.fn();
        const mockPanTo = vi.fn();
        state.map = {
            getZoom: () => 16,
            fitBounds: mockFitBounds,
            panTo: mockPanTo
        };
        state.defaultMapZoom = 15;

        vi.stubGlobal('document', {
            querySelectorAll: vi.fn().mockReturnValue([]),
            getElementById: vi.fn().mockImplementation((id) => {
                if (id === 'turn-selector') return mockTurnSelector;
                if (id === 'expandable-plots-block') return mockExpandablePlots;
                if (id === 'distance-slider') return mockSlider;
                if (id === 'distance-display') return mockDisplay;
                return null;
            }),
            querySelector: vi.fn().mockImplementation((selector) => {
                if (selector === '.map-side-panel') return mockSidePanel;
                if (selector === '.map-controls-bar') return mockMapControlsBar;
                return null;
            })
        });

        selectTurnAndSwitchToMap(0);

        expect(state.currentTurnIdx).toBe(0);
        expect(mockTurnSelector.value).toBe(0);
        expect(state.sortMode).toBe('turn');
        expect(state.activeTab).toBe('map');
        expect(state.groupASelection.has('sess1-2')).toBe(true); // Fastest lap
        expect(state.groupASelection.has('sess1-1')).toBe(true); // Median lap
        expect(state.groupASelection.size).toBe(2);
        expect(state.deltaPlotVisible).toBe(true);
        expect(state.playbackDistance).toBe(50);
        expect(state.currentTargetDist).toBe(50);
        expect(mockSlider.value).toBe(50);
        expect(mockDisplay.textContent).toBe('50m');
        expect(mockFitBounds).toHaveBeenCalled();
    });
});

describe('lap_selection.js side panel collapse and resize', () => {
    let mockSidePanel;
    let mockTabMap;
    let mockToggleBtn;
    let mockResizer;

    beforeEach(() => {
        const classListSet = new Set();
        mockSidePanel = {
            style: { width: '380px' },
            classList: {
                add: (c) => classListSet.add(c),
                remove: (c) => classListSet.delete(c),
                contains: (c) => classListSet.has(c)
            },
            offsetWidth: 380,
            getBoundingClientRect: () => ({ width: 380 })
        };
        const tabMapClasses = new Set();
        mockTabMap = {
            classList: {
                add: (c) => tabMapClasses.add(c),
                remove: (c) => tabMapClasses.delete(c),
                contains: (c) => tabMapClasses.has(c)
            }
        };
        mockToggleBtn = {
            setAttribute: vi.fn(),
            addEventListener: vi.fn()
        };
        mockResizer = {
            classList: {
                add: vi.fn(),
                remove: vi.fn()
            },
            addEventListener: vi.fn()
        };

        vi.stubGlobal('document', {
            querySelectorAll: vi.fn().mockReturnValue([]),
            getElementById: vi.fn().mockImplementation((id) => {
                if (id === 'tab-map') return mockTabMap;
                if (id === 'side-panel-toggle-btn') return mockToggleBtn;
                if (id === 'side-panel-resizer') return mockResizer;
                return null;
            }),
            querySelector: vi.fn().mockImplementation((selector) => {
                if (selector === '.map-side-panel') return mockSidePanel;
                return null;
            }),
            body: { style: {} },
            addEventListener: vi.fn()
        });

        vi.stubGlobal('localStorage', {
            getItem: vi.fn(),
            setItem: vi.fn()
        });

        state.sidePanelCollapsed = false;
        state.sidePanelWidth = 380;
    });

    it('collapses side panel and updates button title and classList', () => {
        collapseSidePanel(false);
        expect(mockSidePanel.classList.contains('collapsed')).toBe(true);
        expect(mockTabMap.classList.contains('side-panel-collapsed')).toBe(true);
        expect(state.sidePanelCollapsed).toBe(true);
        expect(mockToggleBtn.setAttribute).toHaveBeenCalledWith('title', 'Expand panel');
        expect(localStorage.setItem).toHaveBeenCalledWith('kartsim_side_panel_collapsed', '1');
    });

    it('expands side panel and restores state and button title', () => {
        collapseSidePanel(false);
        expandSidePanel(false);
        expect(mockSidePanel.classList.contains('collapsed')).toBe(false);
        expect(mockTabMap.classList.contains('side-panel-collapsed')).toBe(false);
        expect(state.sidePanelCollapsed).toBe(false);
        expect(mockToggleBtn.setAttribute).toHaveBeenCalledWith('title', 'Collapse panel');
        expect(localStorage.setItem).toHaveBeenCalledWith('kartsim_side_panel_collapsed', '0');
    });

    it('toggles side panel between expanded and collapsed', () => {
        toggleSidePanel();
        expect(mockSidePanel.classList.contains('collapsed')).toBe(true);
        toggleSidePanel();
        expect(mockSidePanel.classList.contains('collapsed')).toBe(false);
    });

    it('initSidePanelResizer restores saved width and collapsed state', () => {
        localStorage.getItem.mockImplementation((key) => {
            if (key === 'kartsim_side_panel_width') return '450';
            if (key === 'kartsim_side_panel_collapsed') return '1';
            return null;
        });

        initSidePanelResizer();
        expect(mockSidePanel.style.width).toBe('450px');
        expect(state.sidePanelWidth).toBe(450);
        expect(mockSidePanel.classList.contains('collapsed')).toBe(true);
    });
});


