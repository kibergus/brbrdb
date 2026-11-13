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
    BUILTIN_PLOT_GROUPS,
    normalizePlotGroupId,
    resolvePlotToolbar,
    computeResponsiveToolbarItems,
    initPlotToolbar,
    renderPlotToolbar,
    closePlotFolderPopover
} from './plot_toolbar.js';
import { updateTabAvailability, updateExpandablePlotsVisibility } from './plots_sync.js';
import { syncCustomChannelsManifest } from './custom_channels.js';
import { handleReportTriggerAction } from './lap_selection.js';

vi.mock('./url_sync.js', () => ({
    debouncedUpdateURL: vi.fn()
}));

function makeDomNode(tagName, idMap) {
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
        offsetWidth: 0,
        offsetHeight: 0,
        clientWidth: 800,
        listeners: {},
        _rect: null,
        classList: {
            _set: new Set(),
            add(...cls) { cls.forEach(c => c && this._set.add(c)); },
            remove(...cls) { cls.forEach(c => this._set.delete(c)); },
            toggle(c, force) {
                const next = force !== undefined ? Boolean(force) : !this._set.has(c);
                if (next) this._set.add(c); else this._set.delete(c);
                return next;
            },
            contains(c) { return this._set.has(c); }
        },
        setAttribute(k, v) { this.attributes[k] = String(v); },
        getAttribute(k) { return this.attributes[k] ?? null; },
        removeAttribute(k) { delete this.attributes[k]; },
        addEventListener(ev, fn) {
            this.listeners[ev] = this.listeners[ev] || [];
            this.listeners[ev].push(fn);
        },
        dispatchEvent(ev, payload = {}) {
            const fns = this.listeners[ev] || [];
            fns.forEach(fn => fn(payload));
        },
        click() {
            this.dispatchEvent('click', {
                target: this,
                stopPropagation: () => {},
                preventDefault: () => {}
            });
        },
        appendChild(child) {
            if (child.parentNode && child.parentNode !== this) {
                child.remove();
            }
            child.parentNode = this;
            if (!this.children.includes(child)) {
                this.children.push(child);
            }
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
        contains(target) {
            let cur = target;
            while (cur) {
                if (cur === this) return true;
                cur = cur.parentNode;
            }
            return false;
        },
        getBoundingClientRect() {
            if (this._rect) return this._rect;
            const w = Math.max(42, Math.ceil(String(this.textContent || '').length * 7.2) + 20);
            return { left: 20, right: 20 + w, top: 680, bottom: 712, width: w, height: 32 };
        },
        querySelector(sel) {
            return this.children.find(c => c.className === sel.replace(/^\./, '')) || null;
        },
        querySelectorAll(sel) {
            if (sel === '.dynamic-plot-area') {
                return this.children.filter(c => String(c.className).includes('dynamic-plot-area'));
            }
            return [];
        },
        on: vi.fn()
    };
    return el;
}

describe('plot_toolbar.js 2-level toolbar organization & responsive overflow', () => {
    let idMap;
    let bodyEl;
    let tabsContainer;
    let plotContent;
    let docListeners;
    let resizeObserverCallbacks;

    beforeEach(() => {
        closePlotFolderPopover();
        idMap = new Map();
        docListeners = {};
        resizeObserverCallbacks = [];

        bodyEl = makeDomNode('body', idMap);
        tabsContainer = makeDomNode('div', idMap);
        tabsContainer.className = 'plot-tabs';
        tabsContainer.clientWidth = 900;
        bodyEl.appendChild(tabsContainer);

        plotContent = makeDomNode('div', idMap);
        plotContent.id = 'plot-content';
        idMap.set('plot-content', plotContent);
        bodyEl.appendChild(plotContent);

        // Populate static built-in buttons & plot areas matching templates/kartsim_meeting.html
        for (const g of BUILTIN_PLOT_GROUPS) {
            const btn = makeDomNode('button', idMap);
            btn.id = `tab-btn-${g.id}`;
            btn.className = 'plot-tab';
            btn.setAttribute('data-tab', g.id);
            btn.textContent = g.label;
            tabsContainer.appendChild(btn);

            const area = makeDomNode('div', idMap);
            area.id = `plot-area-${g.id}`;
            area.className = 'plot-area';
            area.style.display = 'none';
            plotContent.appendChild(area);
        }

        const speedContainer = makeDomNode('div', idMap);
        speedContainer.id = 'speed-plot-container';
        idMap.set('speed-plot-container', speedContainer);
        const deltaContainer = makeDomNode('div', idMap);
        deltaContainer.id = 'delta-plot-container';
        idMap.set('delta-plot-container', deltaContainer);

        vi.stubGlobal('document', {
            body: bodyEl,
            head: makeDomNode('head', idMap),
            createElement: vi.fn((tag) => makeDomNode(tag, idMap)),
            getElementById: vi.fn((id) => idMap.get(id) || null),
            querySelector: vi.fn((sel) => (sel === '.plot-tabs' ? tabsContainer : null)),
            querySelectorAll: vi.fn(() => []),
            addEventListener: vi.fn((ev, fn) => {
                docListeners[ev] = docListeners[ev] || [];
                docListeners[ev].push(fn);
            })
        });

        const plotlyMock = {
            react: vi.fn(),
            purge: vi.fn(),
            relayout: vi.fn(),
            restyle: vi.fn(),
            Plots: { resize: vi.fn() }
        };
        vi.stubGlobal('Plotly', plotlyMock);
        vi.stubGlobal('window', {
            innerWidth: 1024,
            innerHeight: 768,
            Plotly: plotlyMock,
            KART_CONFIG: { getChannelUrl: 'http://localhost/api/channel' },
            location: { search: '', origin: 'http://localhost' },
            addEventListener: vi.fn()
        });
        vi.stubGlobal('ResizeObserver', class {
            constructor(cb) {
                this.cb = cb;
                resizeObserverCallbacks.push(cb);
            }
            observe() {}
            disconnect() {}
        });
        vi.stubGlobal('fetch', vi.fn(async () => ({
            ok: true,
            json: async () => ({ values: [] })
        })));
        vi.stubGlobal('requestAnimationFrame', vi.fn(() => 1));
        vi.stubGlobal('cancelAnimationFrame', vi.fn());

        state.customChannels = new Map();
        state.customPlotGroups = new Map();
        state.customPlotLayout = null;
        state.customChannelPayloads = new Map();
        state.activePlotChannels = new Set();
        state.deltaPlotVisible = false;
        state.speedPlotVisible = false;
        state.globalTelemetryXRange = [0, 100];
        state.trackData = { lap_length: 100, turns: [] };
        state.lapDataLookup = {};
        state.allSessionsData = [];
    });

    afterEach(() => {
        closePlotFolderPopover();
        vi.unstubAllGlobals();
    });

    it('places Slip Angle top-level, collapses remaining KartSim tyre channels into Tyres, unwraps single ECG, groups ECG+EMG into Bio Sensors, and demotes G-Force/RPS/EEG into •••', () => {
        // KartSim + Spiker:bit EEG (no ECG/EMG)
        state.allSessionsData = [
            {
                session_id: 'ks_1',
                columns: [
                    'Speed', 'Throttle', 'Brake', 'Steering Angle',
                    'RPS FL', 'GForceLon', 'GForceLat',
                    'Slide Pct FL', 'Lat Patch Vel FL', 'Lat Force FL',
                    'Tyre Load FL', 'Slip Angle Front', 'EEG'
                ],
                laps: []
            }
        ];

        initPlotToolbar();
        updateTabAvailability();

        const topSummary = tabsContainer.children.map(el => ({
            tab: el.getAttribute('data-tab'),
            folder: el.getAttribute('data-folder'),
            text: el.textContent
        }));

        expect(topSummary).toEqual([
            { tab: 'pedals', folder: null, text: 'Pedals' },
            { tab: 'steering', folder: null, text: 'Steering' },
            { tab: 'slip_angle', folder: null, text: 'Slip Angle' },
            { tab: null, folder: 'tyres', text: 'Tyres ▾' },
            { tab: null, folder: 'more', text: '•••' }
        ]);

        // Adding ECG alone unwraps Bio Sensors to a top-level ECG button
        state.allSessionsData[0].columns.push('ECG');
        updateTabAvailability();

        expect(tabsContainer.children.map(el => el.textContent)).toEqual([
            'Pedals',
            'Steering',
            'Slip Angle',
            'Tyres ▾',
            'ECG',
            '•••'
        ]);

        // Adding EMG causes ECG + EMG to group into Bio Sensors ▾
        state.allSessionsData[0].columns.push('EMG');
        updateTabAvailability();

        expect(tabsContainer.children.map(el => el.textContent)).toEqual([
            'Pedals',
            'Steering',
            'Slip Angle',
            'Tyres ▾',
            'Bio Sensors ▾',
            '•••'
        ]);
    });

    it('resolves Mode A shorthand folders, Mode B custom layouts, "*" inside more, unwrap_single:false, and safety-net fallback when "*" is omitted', () => {
        const available = [
            { id: 'pedals', label: 'Pedals' },
            { id: 'steering', label: 'Steering' },
            { id: 'slip_angle', label: 'Slip Angle' },
            { id: 'tyre_load', label: 'Tyre Load' },
            { id: 'slide', label: 'Slide' },
            { id: 'eeg', label: 'EEG' },
            { id: 'g_force', label: 'G-Force' },
            { id: 'eog_blinks', label: 'EOG & Blinks', isCustom: true },
            { id: 'eeg_bands', label: 'EEG Bands', folder: 'neuro', folder_label: 'EEG & Load', isCustom: true },
            { id: 'cognition', label: 'Cognition', folder: 'neuro', folder_label: 'EEG & Load', isCustom: true }
        ];

        // 1. Mode A: no custom layout -> shorthand "neuro" folder + standalone "eog_blinks" placed at "*"
        const modeA = resolvePlotToolbar(available, null);
        expect(modeA.map(x => (x.type === 'tab' ? `tab:${x.id}` : `folder:${x.folder}(${x.items.map(i => i.id).join(',')})`))).toEqual([
            'tab:pedals',
            'tab:steering',
            'tab:slip_angle',
            'folder:tyres(tyre_load,slide)',
            'tab:eog_blinks',
            'folder:neuro(eeg_bands,cognition)',
            'folder:more(gforce,eeg)'
        ]);

        // Single item inside overflow:true never unwraps!
        const singleOverflow = resolvePlotToolbar([{ id: 'g_force', label: 'G-Force' }], null);
        expect(singleOverflow).toHaveLength(1);
        expect(singleOverflow[0].type).toBe('folder');
        expect(singleOverflow[0].folder).toBe('more');

        // 2. unwrap_single: false keeps a 1-item non-overflow folder wrapped
        const forcedSingleFolder = resolvePlotToolbar(
            [{ id: 'eeg_bands', label: 'EEG Bands' }],
            [{ folder: 'neuro', label: 'EEG & Load', unwrap_single: false, items: ['eeg_bands'] }]
        );
        expect(forcedSingleFolder).toHaveLength(1);
        expect(forcedSingleFolder[0].type).toBe('folder');
        expect(forcedSingleFolder[0].folder).toBe('neuro');

        // 3. Mode B with "*" inside "more"
        const modeB = resolvePlotToolbar(available, [
            'pedals',
            'eog_blinks',
            { folder: 'neuro', label: 'EEG & Load', items: ['eeg_bands', 'cognition'] },
            { folder: 'more', label: '•••', overflow: true, items: ['gforce', 'eeg', '*'] }
        ]);
        expect(modeB.map(x => (x.type === 'tab' ? x.id : x.folder))).toEqual([
            'pedals',
            'eog_blinks',
            'neuro',
            'more'
        ]);
        const moreIds = modeB[3].items.map(g => g.id);
        expect(moreIds).toEqual(['gforce', 'eeg', 'steering', 'slip_angle', 'tyre_load', 'slide']);

        // 4. Safety net: custom layout omits "*" and omits "more" -> zero dropped groups!
        const noWildcard = resolvePlotToolbar(available, ['pedals', 'eog_blinks']);
        expect(noWildcard.map(x => (x.type === 'tab' ? x.id : x.folder))).toEqual([
            'pedals',
            'eog_blinks',
            'more'
        ]);
        const rescuedIds = noWildcard[2].items.map(g => g.id);
        expect(rescuedIds).toEqual([
            'steering', 'slip_angle', 'tyre_load', 'slide', 'eeg', 'gforce', 'eeg_bands', 'cognition'
        ]);

        // 5. Alias normalization check
        expect(normalizePlotGroupId('g_force')).toBe('gforce');
        expect(normalizePlotGroupId('lat_force')).toBe('force');
        expect(normalizePlotGroupId('long_patch_vel')).toBe('patch_vel');
    });

    it('spills trailing tabs and folders into ••• on narrow ResizeObserver width and restores them when widened', () => {
        state.allSessionsData = [
            {
                session_id: 'ks_1',
                columns: [
                    'Speed', 'Throttle', 'Brake', 'Steering Angle',
                    'RPS FL', 'GForceLon', 'Slide Pct FL', 'Tyre Load FL', 'Slip Angle Front', 'EEG'
                ],
                laps: []
            }
        ];

        initPlotToolbar();
        updateTabAvailability();
        expect(tabsContainer.children.map(c => c.textContent)).toEqual([
            'Pedals', 'Steering', 'Slip Angle', 'Tyres ▾', '•••'
        ]);

        // Shrink container via ResizeObserver so only Pedals + Steering + ••• fit
        tabsContainer.clientWidth = 240;
        expect(resizeObserverCallbacks.length).toBeGreaterThan(0);
        resizeObserverCallbacks[0]([{ contentRect: { width: 240 } }]);

        const narrowLabels = tabsContainer.children.map(c => c.textContent);
        expect(narrowLabels).toEqual(['Pedals', 'Steering', '•••']);

        // Open ••• popover -> spilled Slip Angle + Tyres folder sub-section + divider + G-Force/RPS/EEG
        const moreBtn = idMap.get('folder-btn-more');
        moreBtn.click();
        const popover = idMap.get('plot-folder-popover');
        expect(popover).toBeDefined();

        const headerLabels = popover.children
            .filter(c => c.className === 'plot-folder-section-header')
            .map(c => c.textContent);
        expect(headerLabels).toContain('Tyres');

        const popoverItemIds = popover.children
            .filter(c => c.getAttribute('data-tab'))
            .map(c => c.getAttribute('data-tab'));
        expect(popoverItemIds).toEqual(['slip_angle', 'tyre_load', 'slide', 'gforce', 'rps', 'eeg']);

        // Widen container back to 900px -> Slip Angle and Tyres ▾ restore to top-level!
        tabsContainer.clientWidth = 900;
        resizeObserverCallbacks[0]([{ contentRect: { width: 900 } }]);
        expect(tabsContainer.children.map(c => c.textContent)).toEqual([
            'Pedals', 'Steering', 'Slip Angle', 'Tyres ▾', '•••'
        ]);

        // Direct unit check on computeResponsiveToolbarItems
        const dummyResolved = resolvePlotToolbar([
            { id: 'pedals', label: 'Pedals' },
            { id: 'steering', label: 'Steering' }
        ], null);
        const ultraNarrow = computeResponsiveToolbarItems(dummyResolved, 140, () => 80, 48, 6);
        expect(ultraNarrow.map(x => (x.type === 'tab' ? x.id : x.folder))).toEqual(['pedals', 'more']);
    });

    it('propagates active count badges (Tyres · 2 ▾, ••• · 2), supports multi-toggle without closing, clamps to viewport, and closes on Esc / outside click', () => {
        state.allSessionsData = [
            {
                session_id: 'ks_1',
                columns: [
                    'Speed', 'Throttle', 'Brake', 'Steering Angle',
                    'RPS FL', 'GForceLon', 'Slide Pct FL', 'Tyre Load FL', 'Slip Angle Front', 'EEG'
                ],
                laps: []
            }
        ];

        initPlotToolbar();
        updateTabAvailability();

        const tyresBtn = idMap.get('folder-btn-tyres');
        const moreBtn = idMap.get('folder-btn-more');
        expect(tyresBtn.textContent).toBe('Tyres ▾');
        expect(tyresBtn.classList.contains('active')).toBe(false);

        // Open Tyres popover and toggle 2 child plots in one visit
        tyresBtn.click();
        let popover = idMap.get('plot-folder-popover');
        expect(popover).toBeDefined();

        idMap.get('folder-item-tyre_load').click();
        // Popover stays open after first toggle!
        expect(idMap.get('plot-folder-popover')).toBeDefined();
        expect(state.activePlotChannels.has('tyre_load')).toBe(true);
        expect(idMap.get('plot-area-tyre_load').style.display).toBe('block');
        expect(tyresBtn.textContent).toBe('Tyres · 1 ▾');
        expect(tyresBtn.classList.contains('active')).toBe(true);

        idMap.get('folder-item-slide').click();
        expect(idMap.get('plot-folder-popover')).toBeDefined();
        expect(tyresBtn.textContent).toBe('Tyres · 2 ▾');
        expect(idMap.get('folder-item-tyre_load').getAttribute('aria-checked')).toBe('true');
        expect(idMap.get('folder-item-slide').getAttribute('aria-checked')).toBe('true');

        // Pressing Escape closes the popover while keeping plots open
        (docListeners.keydown || []).forEach(fn => fn({ key: 'Escape' }));
        expect(document.getElementById('plot-folder-popover')).toBeNull();
        expect(tyresBtn.textContent).toBe('Tyres · 2 ▾');

        // Position clamping near right edge on ••• button
        moreBtn._rect = { left: 980, right: 1020, top: 700, bottom: 732, width: 40, height: 32 };
        moreBtn.click();
        popover = idMap.get('plot-folder-popover');
        expect(popover).toBeDefined();
        const leftPx = parseInt(popover.style.left, 10);
        expect(leftPx).toBeGreaterThanOrEqual(8);
        expect(leftPx).toBeLessThanOrEqual(1024 - 40);

        // Toggle G-Force and EEG inside ••• -> badge becomes ••• · 2 with active underline
        idMap.get('folder-item-gforce').click();
        expect(moreBtn.textContent).toBe('••• · 1');
        expect(moreBtn.classList.contains('active')).toBe(true);
        expect(idMap.get('plot-area-gforce').style.display).toBe('block');

        idMap.get('folder-item-eeg').click();
        expect(moreBtn.textContent).toBe('••• · 2');
        expect(idMap.get('plot-area-eeg').style.display).toBe('block');

        // Outside click closes the popover
        const outsideNode = makeDomNode('div', idMap);
        bodyEl.appendChild(outsideNode);
        (docListeners.click || []).forEach(fn => fn({ target: outsideNode }));
        expect(document.getElementById('plot-folder-popover')).toBeNull();
    });

    it('hot-reloads Companion plot_groups/plot_layout while preserving open plots and supports programmatic handleReportTriggerAction', () => {
        state.allSessionsData = [
            {
                session_id: 'ks_1',
                columns: [
                    'Speed', 'Throttle', 'Brake', 'Steering Angle',
                    'RPS FL', 'GForceLon', 'Slide Pct FL', 'Tyre Load FL', 'Slip Angle Front', 'EEG'
                ],
                laps: []
            }
        ];

        initPlotToolbar();
        updateTabAvailability();

        // Programmatic activation via handleReportTriggerAction works for top-level, folder, and ••• plots
        handleReportTriggerAction({ plots: ['speed', 'slip_angle', 'tyre_load', 'g_force'] });
        expect(state.speedPlotVisible).toBe(true);
        expect(state.activePlotChannels.has('slip_angle')).toBe(true);
        expect(state.activePlotChannels.has('tyre_load')).toBe(true);
        expect(state.activePlotChannels.has('gforce')).toBe(true);
        expect(idMap.get('plot-area-slip_angle').style.display).toBe('block');
        expect(idMap.get('plot-area-tyre_load').style.display).toBe('block');
        expect(idMap.get('plot-area-gforce').style.display).toBe('block');
        expect(idMap.get('folder-btn-tyres').textContent).toBe('Tyres · 1 ▾');
        expect(idMap.get('folder-btn-more').textContent).toBe('••• · 1');

        handleReportTriggerAction({ openPlot: 'slide' });
        expect(idMap.get('folder-btn-tyres').textContent).toBe('Tyres · 2 ▾');

        handleReportTriggerAction({ togglePlot: 'tyre_load' });
        expect(state.activePlotChannels.has('tyre_load')).toBe(false);
        expect(idMap.get('folder-btn-tyres').textContent).toBe('Tyres · 1 ▾');

        // Companion hot-reload with custom folder groups & custom layout preserves active plots!
        syncCustomChannelsManifest(
            [
                { channel_id: 'eeg_alpha', label: 'Alpha', group: 'eeg_bands', mtime: 1 },
                { channel_id: 'theta_beta', label: 'Cognitive Load', group: 'cognition', mtime: 1 },
                { channel_id: 'blink_rate', label: 'Blinks', group: 'eog_blinks', mtime: 1 }
            ],
            [
                {
                    id: 'eog_blinks',
                    label: 'EOG & Blinks',
                    channels: [{ channel_id: 'blink_rate' }]
                },
                {
                    id: 'eeg_bands',
                    label: 'EEG Bands',
                    folder: 'neuro',
                    folder_label: 'EEG & Load',
                    channels: [{ channel_id: 'eeg_alpha' }]
                },
                {
                    id: 'cognition',
                    label: 'Cognition',
                    folder: 'neuro',
                    folder_label: 'EEG & Load',
                    channels: [{ channel_id: 'theta_beta' }]
                }
            ],
            [
                'pedals',
                'steering',
                'slip_angle',
                'eog_blinks',
                { folder: 'neuro', label: 'EEG & Load', items: ['eeg_bands', 'cognition'] },
                { folder: 'tyres', label: 'Tyres', items: ['tyre_load', 'slide'] },
                { folder: 'more', label: '•••', overflow: true, items: ['gforce', 'rps', 'eeg', '*'] }
            ]
        );

        // Active plots ('slip_angle', 'slide', and 'gforce') remain open and reflected in badges after hot-reload
        expect(state.activePlotChannels.has('slip_angle')).toBe(true);
        expect(state.activePlotChannels.has('slide')).toBe(true);
        expect(state.activePlotChannels.has('gforce')).toBe(true);
        expect(tabsContainer.children.map(c => c.textContent)).toEqual([
            'Pedals',
            'Steering',
            'Slip Angle',
            'EOG & Blinks',
            'EEG & Load ▾',
            'Tyres · 1 ▾',
            '••• · 1'
        ]);

        // Programmatically open custom plot inside "EEG & Load ▾" folder
        handleReportTriggerAction({ openPlot: 'eeg_bands' });
        expect(state.activePlotChannels.has('eeg_bands')).toBe(true);
        expect(idMap.get('plot-area-eeg_bands').style.display).toBe('block');
        expect(idMap.get('folder-btn-neuro').textContent).toBe('EEG & Load · 1 ▾');

        // Clearing custom study unmounts custom folder & deactivates custom group while keeping built-ins
        syncCustomChannelsManifest([], [], null);
        renderPlotToolbar();
        updateExpandablePlotsVisibility();
        expect(state.activePlotChannels.has('eeg_bands')).toBe(false);
        expect(state.activePlotChannels.has('slide')).toBe(true);
        expect(idMap.get('folder-btn-neuro')).toBeUndefined();
    });
});
