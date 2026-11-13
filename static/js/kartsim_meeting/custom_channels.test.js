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
import { state } from './state.js';
import {
    interpolateLinearSeries,
    applyCustomChannelPayloadToLaps,
    syncCustomChannelsManifest,
    ensureCustomPlotGroupLoaded,
    customChannelFieldKey
} from './custom_channels.js';
import { loadedChannels, renderSingleChannelPlot } from './plots_sync.js';

vi.mock('./url_sync.js', () => ({
    debouncedUpdateURL: vi.fn()
}));

function makeElement(tagName, idMap) {
    const el = {
        tagName: String(tagName || 'div').toUpperCase(),
        id: '',
        className: '',
        textContent: '',
        innerHTML: '',
        style: {},
        attributes: {},
        children: [],
        parentNode: null,
        offsetParent: {},
        offsetWidth: 600,
        clientWidth: 600,
        listeners: {},
        classList: {
            _set: new Set(),
            add(c) { this._set.add(c); },
            remove(c) { this._set.delete(c); },
            toggle(c, force) {
                const next = force !== undefined ? Boolean(force) : !this._set.has(c);
                if (next) this._set.add(c); else this._set.delete(c);
                return next;
            },
            contains(c) { return this._set.has(c); }
        },
        setAttribute(k, v) { this.attributes[k] = String(v); },
        getAttribute(k) { return this.attributes[k] ?? null; },
        addEventListener(ev, fn) {
            this.listeners[ev] = this.listeners[ev] || [];
            this.listeners[ev].push(fn);
        },
        appendChild(child) {
            child.parentNode = this;
            this.children.push(child);
            if (child.id && idMap) idMap.set(child.id, child);
            return child;
        },
        remove() {
            if (this.id && idMap && idMap.get(this.id) === this) {
                idMap.delete(this.id);
            }
            if (this.parentNode) {
                const idx = this.parentNode.children.indexOf(this);
                if (idx >= 0) this.parentNode.children.splice(idx, 1);
                this.parentNode = null;
            }
        },
        querySelector(sel) {
            if (sel === '.custom-plot-legend') {
                return this.children.find(c => c.className === 'custom-plot-legend') || null;
            }
            return null;
        },
        querySelectorAll(sel) {
            if (sel === '.dynamic-plot-tab') {
                return this.children.filter(c => String(c.className).includes('dynamic-plot-tab'));
            }
            if (sel === '.dynamic-plot-area') {
                return this.children.filter(c => String(c.className).includes('dynamic-plot-area'));
            }
            return [];
        },
        on: vi.fn()
    };
    return el;
}

describe('custom_channels.js & custom plot groups', () => {
    let idMap;
    let tabsContainer;
    let plotContent;
    let plotlyReactCalls;

    beforeEach(() => {
        idMap = new Map();
        plotlyReactCalls = [];
        loadedChannels.clear();

        tabsContainer = makeElement('div', idMap);
        tabsContainer.className = 'plot-tabs';

        plotContent = makeElement('div', idMap);
        plotContent.id = 'plot-content';
        idMap.set('plot-content', plotContent);

        const headEl = makeElement('head', idMap);

        vi.stubGlobal('document', {
            head: headEl,
            createElement: vi.fn((tag) => makeElement(tag, idMap)),
            getElementById: vi.fn((id) => idMap.get(id) || null),
            querySelector: vi.fn((sel) => (sel === '.plot-tabs' ? tabsContainer : null)),
            querySelectorAll: vi.fn((sel) => {
                if (sel === '.plot-tab[data-tab]') {
                    return tabsContainer.children;
                }
                return [];
            })
        });

        const plotlyMock = {
            react: vi.fn((el, data, layout) => {
                plotlyReactCalls.push({ el, data, layout });
            }),
            purge: vi.fn(),
            relayout: vi.fn(),
            restyle: vi.fn(),
            Plots: { resize: vi.fn() }
        };
        vi.stubGlobal('Plotly', plotlyMock);
        vi.stubGlobal('window', {
            Plotly: plotlyMock,
            location: { search: '' }
        });
        vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1));
        vi.stubGlobal('cancelAnimationFrame', vi.fn());

        state.customChannels = new Map();
        state.customPlotGroups = new Map();
        state.customChannelPayloads = new Map();
        state.customPlotActiveComponents = {};
        state.activePlotChannels = new Set();
        state.deltaPlotVisible = false;
        state.speedPlotVisible = false;
        state.globalTelemetryXRange = [0, 100];
        state.trackData = { lap_length: 100, turns: [] };

        const lap14Pts = [
            { dist: 0, time: 10.0, speed: 60 },
            { dist: 25, time: 11.5, speed: 65 },
            { dist: 50, time: 13.0, speed: 70 },
            { dist: 100, time: 16.0, speed: 80 }
        ];
        const lap18Pts = [
            { dist: 0, time: 50.0, speed: 58 },
            { dist: 50, time: 53.2, speed: 68 },
            { dist: 100, time: 56.5, speed: 79 }
        ];

        state.allSessionsData = [
            {
                session_id: 'sess_1',
                driver_name: 'kibergus',
                columns: ['Speed', 'Throttle', 'Brake'],
                laps: [
                    { lap_num: 14, lap_time: '0:46.000' },
                    { lap_num: 18, lap_time: '0:46.500' }
                ]
            }
        ];
        state.lapDataLookup = {
            'sess_1-14': {
                session_id: 'sess_1',
                lap_num: 14,
                lap_time: '0:46.000',
                points: lap14Pts
            },
            'sess_1-18': {
                session_id: 'sess_1',
                lap_num: 18,
                lap_time: '0:46.500',
                points: lap18Pts
            }
        };
        state.groups = ['A', 'B'];
        state.groupEnabled = { A: true, B: true };
        state.groupVisibilityMap = { A: true, B: true };
        state.groupSelectionsMap = {
            A: new Set(['sess_1-14']),
            B: new Set(['sess_1-18'])
        };
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    it('interpolates distance-aligned and session-time-aligned series accurately', () => {
        expect(interpolateLinearSeries([0, 50, 100], [10, 20, 40], 25)).toBeCloseTo(15.0);
        expect(interpolateLinearSeries([0, 50, 100], [10, 20, 40], 75)).toBeCloseTo(30.0);
        expect(interpolateLinearSeries([10, 20], [5, 15], 5, 0)).toBeNull();

        // Session-time-aligned series across entire session (omitting lap_num)
        applyCustomChannelPayloadToLaps({
            channel_id: 'heart_rate',
            series: [
                {
                    session_id: 'sess_1',
                    align: 'time',
                    x: [10.0, 16.0, 50.0, 56.5],
                    y: [150.0, 162.0, 165.0, 178.0]
                }
            ]
        });

        const field = customChannelFieldKey('heart_rate');
        expect(state.lapDataLookup['sess_1-14'].points[1][field]).toBeCloseTo(153.0);
        expect(state.lapDataLookup['sess_1-18'].points[2][field]).toBeCloseTo(178.0);
    });

    it('mounts & unmounts dynamic plot tabs across study switches, renders multi-channel EEG traces, and invalidates on mtime change', async () => {
        let alphaPeak = 20.0;
        const fetchMock = vi.fn(async () => ({
            ok: true,
            json: async () => ({
                channels: [
                    {
                        channel_id: 'eeg_alpha',
                        label: 'Alpha (8-13 Hz)',
                        unit: 'uV^2',
                        color: '#a855f7',
                        series: [
                            { session_id: 'sess_1', lap_num: 14, align: 'distance', x: [0, 50, 100], y: [10, alphaPeak, 12] },
                            { session_id: 'sess_1', lap_num: 18, align: 'distance', x: [0, 50, 100], y: [8, 14, 11] }
                        ]
                    },
                    {
                        channel_id: 'eeg_theta',
                        label: 'Theta (4-8 Hz)',
                        unit: 'uV^2',
                        color: '#06b6d4',
                        series: [
                            { session_id: 'sess_1', lap_num: 14, align: 'distance', x: [0, 50, 100], y: [6, 9, 7] },
                            { session_id: 'sess_1', lap_num: 18, align: 'distance', x: [0, 50, 100], y: [11, 18, 13] }
                        ]
                    }
                ]
            })
        }));
        vi.stubGlobal('fetch', fetchMock);

        const manifestChannels = (mtime) => [
            {
                channel_id: 'eeg_alpha',
                label: 'Alpha (8-13 Hz)',
                unit: 'uV^2',
                color: '#a855f7',
                group: 'eeg_bands',
                url: 'http://127.0.0.1:38472/files/study_eeg/channels/eeg.json',
                mtime
            },
            {
                channel_id: 'eeg_theta',
                label: 'Theta (4-8 Hz)',
                unit: 'uV^2',
                color: '#06b6d4',
                group: 'eeg_bands',
                url: 'http://127.0.0.1:38472/files/study_eeg/channels/eeg.json',
                mtime
            }
        ];
        const manifestGroups = [
            {
                id: 'eeg_bands',
                label: 'EEG Bands',
                y_label: 'Band Power (uV^2)',
                color_mode: 'channel',
                channels: [
                    { channel_id: 'eeg_alpha', label: 'Alpha (8-13 Hz)', unit: 'uV^2', color: '#a855f7' },
                    { channel_id: 'eeg_theta', label: 'Theta (4-8 Hz)', unit: 'uV^2', color: '#06b6d4' }
                ]
            }
        ];

        syncCustomChannelsManifest(manifestChannels(1.0), manifestGroups);

        // Dynamic button and plot area mounted
        expect(idMap.get('tab-btn-eeg_bands')).toBeDefined();
        expect(idMap.get('tab-btn-eeg_bands').textContent).toBe('EEG Bands');
        expect(idMap.get('plot-area-eeg_bands')).toBeDefined();

        // Load and render EEG Bands plot
        state.activePlotChannels.add('eeg_bands');
        await ensureCustomPlotGroupLoaded('eeg_bands');
        loadedChannels.add('eeg_bands');
        renderSingleChannelPlot('eeg_bands');

        // Multi-channel file fetched only once for both channels
        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(state.lapDataLookup['sess_1-14'].points[1][customChannelFieldKey('eeg_alpha')]).toBeCloseTo(15.0);

        expect(plotlyReactCalls.length).toBeGreaterThan(0);
        const lastCall = plotlyReactCalls[plotlyReactCalls.length - 1];
        // 2 laps (Group A solid, Group B dash) x 2 channels (Alpha, Theta) = 4 traces
        expect(lastCall.data).toHaveLength(4);
        expect(lastCall.layout.yaxis.title).toBe('Band Power (uV^2)');

        const groupATraces = lastCall.data.filter(t => t.line.dash === 'solid');
        const groupBTraces = lastCall.data.filter(t => t.line.dash === 'dash');
        expect(groupATraces).toHaveLength(2);
        expect(groupBTraces).toHaveLength(2);

        // Updating mtime invalidates cache and reloads new values
        alphaPeak = 40.0;
        syncCustomChannelsManifest(manifestChannels(2.0), manifestGroups);
        expect(loadedChannels.has('eeg_bands')).toBe(false);

        await ensureCustomPlotGroupLoaded('eeg_bands');
        expect(fetchMock).toHaveBeenCalledTimes(2);
        expect(state.lapDataLookup['sess_1-14'].points[1][customChannelFieldKey('eeg_alpha')]).toBeCloseTo(25.0);

        // Switching to an empty study unmounts the EEG Bands tab and plot area cleanly
        syncCustomChannelsManifest([], []);
        expect(idMap.get('tab-btn-eeg_bands')).toBeUndefined();
        expect(idMap.get('plot-area-eeg_bands')).toBeUndefined();
        expect(state.activePlotChannels.has('eeg_bands')).toBe(false);
    });

    it('supports native EEG/ECG/EMG columns, places EEG in •••, and cleanly restores static built-in #tab-btn-ecg after custom override unmount', async () => {
        const { isChannelAvailable } = await import('./plots_sync.js');
        const { collectAvailablePlotGroups } = await import('./plot_toolbar.js');

        // Without EEG/ECG columns, built-in eeg and ecg are unavailable
        state.allSessionsData = [{ session_id: 'sess_1', columns: ['Speed', 'Throttle'], laps: [] }];
        expect(isChannelAvailable('eeg')).toBe(false);
        expect(isChannelAvailable('ecg')).toBe(false);

        // With native EEG and ECG columns from Spiker:bit, both become available
        state.allSessionsData = [
            {
                session_id: 'sess_1',
                columns: ['Speed', 'Throttle', 'EEG', 'ECG'],
                laps: [{ lap_num: 14, start_idx: 0, is_valid: true }]
            }
        ];
        expect(isChannelAvailable('eeg')).toBe(true);
        expect(isChannelAvailable('ecg')).toBe(true);

        // Simulate static #tab-btn-ecg and #plot-area-ecg from kartsim_meeting.html
        const staticEcgBtn = document.createElement('button');
        staticEcgBtn.id = 'tab-btn-ecg';
        staticEcgBtn.className = 'plot-tab';
        staticEcgBtn.setAttribute('data-tab', 'ecg');
        staticEcgBtn.textContent = 'ECG';
        tabsContainer.appendChild(staticEcgBtn);

        const staticEcgArea = document.createElement('div');
        staticEcgArea.id = 'plot-area-ecg';
        staticEcgArea.className = 'plot-area';
        plotContent.appendChild(staticEcgArea);

        // Mount custom overrides for both ecg (top-level unwrapped Bio Sensors) and eeg (inside •••)
        syncCustomChannelsManifest(
            [
                {
                    channel_id: 'ecg_hr',
                    label: 'Heart Rate',
                    unit: 'bpm',
                    group: 'ecg',
                    url: 'http://127.0.0.1:38472/files/study/channels/ecg.json',
                    mtime: 1.0
                },
                {
                    channel_id: 'eeg_alpha',
                    label: 'Alpha',
                    unit: 'uV^2',
                    group: 'eeg',
                    url: 'http://127.0.0.1:38472/files/study/channels/eeg.json',
                    mtime: 1.0
                }
            ],
            [
                {
                    id: 'ecg',
                    label: 'Heart Rate (BPM)',
                    channels: [{ channel_id: 'ecg_hr', label: 'Heart Rate' }]
                },
                {
                    id: 'eeg',
                    label: 'EEG Bands',
                    channels: [{ channel_id: 'eeg_alpha', label: 'Alpha' }]
                }
            ]
        );
        expect(staticEcgBtn.textContent).toBe('Heart Rate (BPM)');
        expect(idMap.get('folder-btn-more')).toBeDefined();
        expect(collectAvailablePlotGroups(tabsContainer).find(g => g.id === 'eeg')?.label).toBe('EEG Bands');

        // Unmount custom study -> static #tab-btn-ecg stays in DOM, restores label "ECG", and native EEG stays in ••• with label "EEG"
        syncCustomChannelsManifest([], []);
        expect(idMap.get('tab-btn-ecg')).toBe(staticEcgBtn);
        expect(staticEcgBtn.textContent).toBe('ECG');
        expect(staticEcgBtn.style.display).toBe('');
        expect(idMap.get('folder-btn-more')).toBeDefined();
        expect(collectAvailablePlotGroups(tabsContainer).find(g => g.id === 'eeg')?.label).toBe('EEG');
    });
});

