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

import { showTab, toggleGroupVisibility, showStatsSubTab, resetProgressionLoaded, toggleSidePanel, collapseSidePanel, expandSidePanel, initSidePanelResizer, showRightPanelTab, initReportInteractions, resetReportView, selectLaps, handleReportTriggerAction, setTelemetryRange, focusMapOnRange, getDistanceRangeBounds, setSort, selectTurnAndSwitchToMap, getSessionColor, updateSessionSelectorColors, renderLapList, toggleSessionDropdown, selectSessionFromDropdown, SESSION_PALETTE, getSessionOrderIndex, compareLaps, getSortedLaps } from './lap_selection.js';
import { setTrajectoryColorMode } from './map.js';
import { state } from './state.js';
import * as plotsSync from './plots_sync.js';

vi.mock('./map.js', () => ({
    initMap: vi.fn(),
    loadTrackPoints: vi.fn(),
    updateTrackLimitsVisibility: vi.fn(),
    updateDistanceMarker: vi.fn(),
    calculateBoundsZoom: vi.fn(() => 15),
    updateAllPolylineColors: vi.fn(),
    getReferenceLap: vi.fn(() => null),
    setTrajectoryColorMode: vi.fn(),
    matchesRequestedLap: vi.fn((set, id) => set.has(id))
}));

vi.mock('./plots_sync.js', () => ({
    updateTelemetryPlots: vi.fn(),
    updateAccelerationPlot: vi.fn(),
    updateSlipAnglePlot: vi.fn(),
    renderExpandablePlots: vi.fn(),
    startSyncLoop: vi.fn(),
    updateDeltaYLim: vi.fn(),
    updateExpandablePlotsVisibility: vi.fn(),
    togglePlot: vi.fn(),
    setVisiblePlots: vi.fn()
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
        expect(setTrajectoryColorMode).toHaveBeenCalledWith('delta_t');
        expect(state.playbackDistance).toBe(50);
        expect(state.currentTargetDist).toBe(50);
        expect(mockSlider.value).toBe(50);
        expect(mockDisplay.textContent).toBe('50m');
        expect(mockFitBounds).toHaveBeenCalled();
    });

    it('updates currentTurnIdx when user changes turn selector dropdown', () => {
        const mockTurnSelector = { value: '1', style: {} };
        const mockSlider = { value: 0 };
        const mockDisplay = { textContent: '' };

        vi.stubGlobal('document', {
            querySelectorAll: vi.fn().mockReturnValue([]),
            getElementById: vi.fn().mockImplementation((id) => {
                if (id === 'turn-selector') return mockTurnSelector;
                if (id === 'distance-slider') return mockSlider;
                if (id === 'distance-display') return mockDisplay;
                return null;
            }),
            querySelector: vi.fn().mockReturnValue(null)
        });

        // Simulate page having ?turn=0 in URL search
        vi.stubGlobal('window', {
            location: { search: '?turn=0', pathname: '/telemetry/test' },
            history: { replaceState: vi.fn() }
        });

        state.trackData = {
            lap_length: 500,
            turns: [
                { start: 50, end: 150 },
                { start: 200, end: 300 }
            ]
        };

        // User changed select dropdown to Turn 2 (index 1)
        mockTurnSelector.value = '1';
        setSort('turn');

        expect(state.sortMode).toBe('turn');
        expect(state.currentTurnIdx).toBe(1);
        expect(mockTurnSelector.value).toBe('1');
        expect(state.globalTelemetryXRange).toEqual([180, 500]);
        expect(state.playbackDistance).toBe(200);
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

describe('lap_selection.js showRightPanelTab and Report Mode', () => {
    let mockBtnReport, mockBtnCornering;
    let mockPaneReport, mockPaneCornering;

    beforeEach(() => {
        mockBtnReport = { classList: { add: vi.fn(), remove: vi.fn() } };
        mockBtnCornering = { classList: { add: vi.fn(), remove: vi.fn() } };
        mockPaneReport = { classList: { add: vi.fn(), remove: vi.fn() }, style: {} };
        mockPaneCornering = { classList: { add: vi.fn(), remove: vi.fn() }, style: {} };

        vi.stubGlobal('document', {
            querySelectorAll: vi.fn().mockImplementation((selector) => {
                if (selector === '.right-panel-tab') return [mockBtnReport, mockBtnCornering];
                if (selector === '.right-panel-tab-pane') return [mockPaneReport, mockPaneCornering];
                return [];
            }),
            getElementById: vi.fn().mockImplementation((id) => {
                if (id === 'right-tab-btn-report') return mockBtnReport;
                if (id === 'right-tab-btn-cornering') return mockBtnCornering;
                if (id === 'report-chart-container') return mockPaneReport;
                if (id === 'cornering-chart-container') return mockPaneCornering;
                return null;
            })
        });
    });

    it('activates report tab and shows report pane when showRightPanelTab("report") is called', () => {
        showRightPanelTab('report');
        expect(state.activeRightTab).toBe('report');
        expect(mockBtnReport.classList.add).toHaveBeenCalledWith('active');
        expect(mockPaneReport.classList.add).toHaveBeenCalledWith('active');
        expect(mockPaneReport.style.display).toBe('flex');
    });

    it('handles interactive report triggers in initReportInteractions', () => {
        let clickListener = null;
        const mockReportContainer = {
            addEventListener: vi.fn().mockImplementation((event, listener) => {
                if (event === 'click') clickListener = listener;
            })
        };

        const mockSlider = { value: 0 };
        const mockDisplay = { textContent: '' };

        vi.stubGlobal('document', {
            getElementById: vi.fn().mockImplementation((id) => {
                if (id === 'report-chart-container') return mockReportContainer;
                if (id === 'distance-slider') return mockSlider;
                if (id === 'distance-display') return mockDisplay;
                return null;
            })
        });

        initReportInteractions();
        expect(mockReportContainer.addEventListener).toHaveBeenCalledWith('click', expect.any(Function));

        // Simulate click on a jump button with data-dist and data-xlim
        const mockTarget = {
            closest: vi.fn().mockReturnValue({
                dataset: {
                    dist: '725.5',
                    xlim: '700,800'
                }
            })
        };

        clickListener({ target: mockTarget });
        expect(state.playbackDistance).toBe(725.5);
        expect(mockSlider.value).toBe(725.5);
        expect(mockDisplay.textContent).toBe('726m');
        expect(state.globalTelemetryXRange).toEqual([700, 800]);
    });

    it('handles postMessage events from the report iframe', () => {
        let messageListener = null;
        vi.stubGlobal('window', {
            addEventListener: vi.fn().mockImplementation((event, listener) => {
                if (event === 'message') messageListener = listener;
            })
        });

        const mockSlider = { value: 0 };
        const mockDisplay = { textContent: '' };

        vi.stubGlobal('document', {
            getElementById: vi.fn().mockImplementation((id) => {
                if (id === 'distance-slider') return mockSlider;
                if (id === 'distance-display') return mockDisplay;
                return null;
            })
        });

        initReportInteractions();
        expect(window.addEventListener).toHaveBeenCalledWith('message', expect.any(Function));

        messageListener({
            data: {
                type: 'telemetry_jump',
                dist: '316.5',
                xlim: '290,380'
            }
        });

        expect(state.playbackDistance).toBe(316.5);
        expect(mockSlider.value).toBe(316.5);
        expect(mockDisplay.textContent).toBe('317m');
        expect(state.globalTelemetryXRange).toEqual([290, 380]);
    });

    it('resets report state when resetReportView is called', () => {
        const replaceState = vi.fn();
        vi.stubGlobal('window', {
            location: { pathname: '/telemetry/report/sample' },
            history: { replaceState },
            KART_CONFIG: {
                reportState: {
                    tab: 'map',
                    rtab: 'report',
                    sort: 'turn',
                    turn: 2,
                    lapsA: ['lap-6'],
                    lapsB: ['lap-5'],
                    xlim: [280, 420],
                    dist: 340,
                    delta: true,
                    speed: true,
                    sidePanelWidth: 420
                }
            }
        });

        const mockSidePanel = { style: {} };
        const mockSlider = { value: 0 };
        const mockDisplay = { textContent: '' };

        vi.stubGlobal('document', {
            getElementById: vi.fn().mockImplementation((id) => {
                if (id === 'map-side-panel') return mockSidePanel;
                if (id === 'distance-slider') return mockSlider;
                if (id === 'distance-display') return mockDisplay;
                return null;
            }),
            querySelectorAll: vi.fn().mockReturnValue([]),
            querySelector: vi.fn().mockReturnValue(null)
        });

        state.allSessionsData = [
            {
                session_id: 'sess',
                laps: [
                    { lap_num: 6, is_valid: true, lap_time: '1:00.000' },
                    { lap_num: 5, is_valid: true, lap_time: '1:00.500' }
                ]
            }
        ];

        resetReportView();
        expect(replaceState).toHaveBeenCalledWith(null, '', '/telemetry/report/sample');
        expect(state.activeRightTab).toBe('report');
        expect(state.sortMode).toBe('turn');
        expect(state.currentTurnIdx).toBe(2);
        expect(state.globalTelemetryXRange).toEqual([280, 420]);
        expect(state.playbackDistance).toBe(340);
        expect(mockSlider.value).toBe(340);
        expect(mockDisplay.textContent).toBe('340m');
    });

    it('handles togglePlot trigger action', () => {
        handleReportTriggerAction({ togglePlot: 'steering' });
        expect(plotsSync.togglePlot).toHaveBeenCalledWith('steering');
    });

    it('handles plots / setVisiblePlots trigger action', () => {
        handleReportTriggerAction({ plots: 'speed,steering,gforce' });
        expect(plotsSync.setVisiblePlots).toHaveBeenCalledWith('speed,steering,gforce');

        handleReportTriggerAction({ plot: 'delta,pedals' });
        expect(plotsSync.setVisiblePlots).toHaveBeenCalledWith('delta,pedals');
    });

    it('handles selectLaps trigger action with group A and group B', () => {
        state.allSessionsData = [
            {
                session_id: '18_09_practice',
                laps: [
                    { lap_num: 6, is_valid: true },
                    { lap_num: 5, is_valid: true }
                ]
            }
        ];

        selectLaps(['18_09_practice-6'], ['18_09_practice-5']);
        expect(state.groupASelection.has('18_09_practice-6')).toBe(true);
        expect(state.groupBSelection.has('18_09_practice-5')).toBe(true);

        handleReportTriggerAction({ laps: '18_09_practice-6' });
        expect(state.groupASelection.has('18_09_practice-6')).toBe(true);
        expect(state.groupBSelection.size).toBe(0);
    });

    it('sets visible range in the bottom plot with setTelemetryRange and triggers', () => {
        setTelemetryRange(250, 400);
        expect(state.globalTelemetryXRange).toEqual([250, 400]);

        setTelemetryRange([300, 450]);
        expect(state.globalTelemetryXRange).toEqual([300, 450]);

        handleReportTriggerAction({ range: '280,390' });
        expect(state.globalTelemetryXRange).toEqual([280, 390]);
    });

    it('computes distance range bounds and focuses map over track range in meters', () => {
        state.trackData = {
            lap_length: 1000,
            center_line: [
                { dist: 0, lat: 51.0, lng: -0.1 },
                { dist: 100, lat: 51.001, lng: -0.099 },
                { dist: 200, lat: 51.002, lng: -0.098 },
                { dist: 300, lat: 51.003, lng: -0.097 }
            ]
        };

        const bounds = getDistanceRangeBounds(100, 200);
        expect(bounds).not.toBeNull();
        expect(bounds.getCenter().lat()).toBeCloseTo(51.0015, 3);

        const fitBounds = vi.fn();
        state.map = { fitBounds };

        focusMapOnRange(100, 200);
        expect(fitBounds).toHaveBeenCalled();

        handleReportTriggerAction({ mapRange: '100,200' });
        expect(fitBounds).toHaveBeenCalledTimes(2);

        handleReportTriggerAction({ focusRange: '150,250' });
        expect(state.globalTelemetryXRange).toEqual([150, 250]);
        expect(fitBounds).toHaveBeenCalledTimes(3);
    });

    describe('Session colors and Lap N styling', () => {
        it('assigns colors from SESSION_PALETTE to sessions and handles "all"', () => {
            expect(getSessionColor('all')).toBe('#94a3b8');
            state.allSessionsData = [
                { session_id: 'sess_1', session_name: 'Practice' },
                { session_id: 'sess_2', session_name: 'Qualifying' }
            ];
            state.sessionColors = {};
            expect(getSessionColor('sess_1')).toBe(SESSION_PALETTE[0]);
            expect(getSessionColor('sess_2')).toBe(SESSION_PALETTE[1]);
        });

        it('updates session selector options and select background color', () => {
            const mockOptions = [
                { value: 'all', style: {} },
                { value: 'sess_1', style: {}, dataset: {} },
                { value: 'sess_2', style: {}, dataset: {} }
            ];
            const mockSelector = {
                id: 'session-selector',
                value: 'sess_1',
                options: mockOptions,
                style: {}
            };
            vi.stubGlobal('document', {
                getElementById: vi.fn(id => id === 'session-selector' ? mockSelector : null)
            });

            updateSessionSelectorColors();

            expect(mockOptions[0].style.backgroundColor).toBe('#0f172a');
            expect(mockOptions[1].style.backgroundColor).toBe('#0f172a');
            expect(mockOptions[1].style.color).toBe(SESSION_PALETTE[0]);
            expect(mockOptions[2].style.backgroundColor).toBe('#0f172a');
            expect(mockOptions[2].style.color).toBe(SESSION_PALETTE[1]);
            expect(mockSelector.style.backgroundColor).toBe('#0f172a');
            expect(mockSelector.style.color).toBe(SESSION_PALETTE[0]);
        });

        it('renders Lap N label with the session color in renderLapList', () => {
            const container = {
                id: 'lap-list',
                innerHTML: '',
                appendChild: vi.fn(el => {
                    container.innerHTML += el.innerHTML;
                })
            };
            const mockSelector = {
                id: 'session-selector',
                value: 'all',
                options: [
                    { value: 'all', style: {} },
                    { value: 'sess_1', style: {}, dataset: {} },
                    { value: 'sess_2', style: {}, dataset: {} }
                ],
                style: {}
            };
            vi.stubGlobal('document', {
                getElementById: vi.fn(id => {
                    if (id === 'lap-list') return container;
                    if (id === 'session-selector') return mockSelector;
                    return null;
                }),
                createElement: vi.fn(() => ({
                    className: '',
                    innerHTML: '',
                    querySelectorAll: vi.fn(() => [])
                }))
            });

            state.allSessionsData = [
                {
                    session_id: 'sess_1',
                    session_name: 'Practice',
                    laps: [{ lap_num: 1, lap_time: '50.123', is_valid: true }]
                },
                {
                    session_id: 'sess_2',
                    session_name: 'Qualifying',
                    laps: [{ lap_num: 2, lap_time: '49.876', is_valid: true }]
                }
            ];
            state.groupASelection = new Set();
            state.groupBSelection = new Set();
            state.sortMode = 'time';

            renderLapList();

            expect(container.innerHTML).toContain(`style="color: ${SESSION_PALETTE[0]}; font-weight: 600;">Lap 1</span>`);
            expect(container.innerHTML).toContain(`style="color: ${SESSION_PALETTE[1]}; font-weight: 600;">Lap 2</span>`);
        });

        it('toggles session-dropdown open class', () => {
            const classList = new Set();
            const mockDropdown = {
                id: 'session-dropdown',
                classList: {
                    toggle: vi.fn((cls) => {
                        if (classList.has(cls)) classList.delete(cls);
                        else classList.add(cls);
                    }),
                    contains: (cls) => classList.has(cls)
                }
            };
            vi.stubGlobal('document', {
                getElementById: vi.fn(id => id === 'session-dropdown' ? mockDropdown : null)
            });

            const stopPropagation = vi.fn();
            toggleSessionDropdown({ stopPropagation });

            expect(stopPropagation).toHaveBeenCalled();
            expect(mockDropdown.classList.toggle).toHaveBeenCalledWith('open');
        });

        it('selects session from custom dropdown and updates selector', () => {
            const mockDropdown = {
                id: 'session-dropdown',
                classList: {
                    remove: vi.fn()
                }
            };
            const mockSelector = {
                id: 'session-selector',
                value: 'all',
                options: [
                    { value: 'all', style: {} },
                    { value: 'sess_1', style: {}, dataset: {} }
                ],
                style: {}
            };
            vi.stubGlobal('document', {
                getElementById: vi.fn(id => {
                    if (id === 'session-dropdown') return mockDropdown;
                    if (id === 'session-selector') return mockSelector;
                    return null;
                })
            });

            selectSessionFromDropdown('sess_1');

            expect(mockDropdown.classList.remove).toHaveBeenCalledWith('open');
            expect(mockSelector.value).toBe('sess_1');
            expect(state.selectedSessionId).toBe('sess_1');
        });

        it('updates session-dropdown button, label, and items in updateSessionSelectorColors', () => {
            const mockBtn = { style: {} };
            const mockLabel = { textContent: '' };
            const mockItemAll = {
                getAttribute: vi.fn(() => 'all'),
                classList: { add: vi.fn(), remove: vi.fn() },
                style: {}
            };
            const mockItemSess1 = {
                getAttribute: vi.fn(() => 'sess_1'),
                classList: { add: vi.fn(), remove: vi.fn() },
                style: {},
                dataset: { color: '#3b82f6' }
            };
            const mockMenu = {
                querySelectorAll: vi.fn(() => [mockItemAll, mockItemSess1])
            };
            const mockSelector = {
                id: 'session-selector',
                value: 'sess_1',
                options: [
                    { value: 'all', textContent: 'All Sessions', style: {} },
                    { value: 'sess_1', textContent: 'Practice 1', style: {}, dataset: { color: '#3b82f6' } }
                ],
                style: {}
            };

            vi.stubGlobal('document', {
                getElementById: vi.fn(id => {
                    if (id === 'session-selector') return mockSelector;
                    if (id === 'session-dropdown-btn') return mockBtn;
                    if (id === 'session-dropdown-current-label') return mockLabel;
                    if (id === 'session-dropdown-menu') return mockMenu;
                    return null;
                })
            });

            updateSessionSelectorColors();

            expect(mockBtn.style.backgroundColor).toBe('#0f172a');
            expect(mockBtn.style.color).toBe('#3b82f6');
            expect(mockLabel.textContent).toBe('Practice 1');
            expect(mockItemSess1.classList.add).toHaveBeenCalledWith('active');
            expect(mockItemAll.classList.remove).toHaveBeenCalledWith('active');
            expect(mockItemSess1.style.backgroundColor).toBe('#1e293b');
            expect(mockItemSess1.style.color).toBe('#3b82f6');
            expect(mockItemAll.style.backgroundColor).toBe('#0f172a');
            expect(mockItemAll.style.color).toBe('var(--text-primary, #f8fafc)');
        });
    });

    describe('lap sorting by session and time', () => {
        beforeEach(() => {
            state.allSessionsData = [
                {
                    session_id: 'sess_practice',
                    session_name: 'Practice',
                    laps: [
                        { lap_num: 2, lap_time: '52.000', is_valid: true },
                        { lap_num: 1, lap_time: '55.000', is_valid: true }
                    ]
                },
                {
                    session_id: 'sess_race',
                    session_name: 'Race',
                    laps: [
                        { lap_num: 1, lap_time: '50.000', is_valid: true },
                        { lap_num: 2, lap_time: '51.000', is_valid: true }
                    ]
                }
            ];
            vi.stubGlobal('document', {
                getElementById: vi.fn(() => null),
                querySelectorAll: vi.fn(() => [])
            });
        });

        it('resolves session order index from selector options or state.allSessionsData', () => {
            expect(getSessionOrderIndex('sess_practice')).toBe(0);
            expect(getSessionOrderIndex('sess_race')).toBe(1);
            expect(getSessionOrderIndex('unknown')).toBe(999999);

            const mockSelector = {
                options: [
                    { value: 'all' },
                    { value: 'sess_race' },
                    { value: 'sess_practice' }
                ]
            };
            vi.stubGlobal('document', {
                getElementById: vi.fn(id => id === 'session-selector' ? mockSelector : null)
            });

            // With custom selector order, sess_race is 0, sess_practice is 1
            expect(getSessionOrderIndex('sess_race')).toBe(0);
            expect(getSessionOrderIndex('sess_practice')).toBe(1);
        });

        it('splits laps by session when sorting by lap number (num mode)', () => {
            state.sortMode = 'num';
            const sorted = getSortedLaps();

            expect(sorted.map(l => l.lapId)).toEqual([
                'sess_practice-1',
                'sess_practice-2',
                'sess_race-1',
                'sess_race-2'
            ]);
        });

        it('compares laps across all sessions when sorting by time', () => {
            state.sortMode = 'time';
            const sorted = getSortedLaps();

            expect(sorted.map(l => l.lapId)).toEqual([
                'sess_race-1',     // 50.000
                'sess_race-2',     // 51.000
                'sess_practice-2', // 52.000
                'sess_practice-1'  // 55.000
            ]);
        });
    });
});




