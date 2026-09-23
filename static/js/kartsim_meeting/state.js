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

import { getGroupColor } from './palette.js';

export const state = {
    map: null,
    mapInitialized: false,
    defaultMapZoom: null,
    trackBounds: null,
    lapPolylines: {}, // lapId -> polyline
    lapColorsForId: {}, // lapId -> color
    allSessionsData: [],
    lapDataLookup: {}, // lapId -> lap object
    sortMode: 'time',
    baseColor: '#fb923c', // Orange
    highlightColor: '#38bdf8', // Light Blue

    // Dynamic groups state
    groups: ['A', 'B'],
    groupSelections: {
        A: new Set(),
        B: new Set()
    },
    groupVisibilityMap: {
        A: true,
        B: false
    },
    groupVisibilityStats: {
        A: true,
        B: true
    },
    groupEnabled: {
        A: true,
        B: true
    },

    getActiveGroups() {
        return this.groups.filter(g => this.groupEnabled[g] !== false);
    },

    isGroupVisible(group, tab) {
        if (this.groupEnabled && this.groupEnabled[group] === false) return false;
        const targetTab = tab || this.activeTab;
        if (targetTab === 'stats') {
            return this.groupVisibilityStats && this.groupVisibilityStats[group] !== undefined
                ? Boolean(this.groupVisibilityStats[group])
                : true;
        } else {
            return this.groupVisibilityMap && this.groupVisibilityMap[group] !== undefined
                ? Boolean(this.groupVisibilityMap[group])
                : false;
        }
    },

    setGroupVisible(group, val, tab) {
        const targetTab = tab || this.activeTab;
        if (targetTab === 'stats') {
            this.groupVisibilityStats[group] = val;
        } else {
            this.groupVisibilityMap[group] = val;
        }
    },

    getGroupColor(group) {
        return getGroupColor(group);
    },

    // Backward compatibility getters/setters
    get groupASelection() {
        return this.groupSelections.A || (this.groupSelections.A = new Set());
    },
    set groupASelection(val) {
        this.groupSelections.A = val;
    },
    get groupBSelection() {
        return this.groupSelections.B || (this.groupSelections.B = new Set());
    },
    set groupBSelection(val) {
        this.groupSelections.B = val;
    },
    get groupAVisibleMap() {
        return this.groupVisibilityMap.A !== undefined ? this.groupVisibilityMap.A : true;
    },
    set groupAVisibleMap(val) {
        this.groupVisibilityMap.A = val;
    },
    get groupBVisibleMap() {
        return this.groupVisibilityMap.B !== undefined ? this.groupVisibilityMap.B : false;
    },
    set groupBVisibleMap(val) {
        this.groupVisibilityMap.B = val;
    },
    get groupAVisibleStats() {
        return this.groupVisibilityStats.A !== undefined ? this.groupVisibilityStats.A : true;
    },
    set groupAVisibleStats(val) {
        this.groupVisibilityStats.A = val;
    },
    get groupBVisibleStats() {
        return this.groupVisibilityStats.B !== undefined ? this.groupVisibilityStats.B : true;
    },
    set groupBVisibleStats(val) {
        this.groupVisibilityStats.B = val;
    },

    activeTab: 'stats',
    selectedSessionId: null,
    trackData: null,
    trackMarkers: [],
    distanceMarker: null,
    currentTargetDist: 0,
    lapToPlotIndices: { speed: {}, braking: {} },
    globalTelemetryXRange: [0, 100],
    currentMapType: 'hybrid',
    trajectoryColorMode: 'pedals',
    deltaPlotVisible: false,
    deltaPlotInitialized: false,
    speedPlotVisible: false,
    speedPlotInitialized: false,
    attachedSyncPlots: new Set(),
    slipAngleColorMode: 'brake_throttle',
    
    // Playback state
    isPlaying: false,
    animationFrameId: null,
    lastTimestamp: null,
    playbackSpeed: 1.0,
    playbackDistance: 0,
    fastestSelectedLap: null,
    fastestSelectedLapId: null,
    fastestGroupALap: null,
    fastestGroupALapId: null,
    
    // Turn selection for sorting
    currentTurnIdx: 0,
    
    // Expandable plots state
    activePlotChannels: new Set(),
    plotTraceVisibility: {},
    get currentActivePlotTab() {
        return this.activePlotChannels.size > 0 ? Array.from(this.activePlotChannels)[0] : null;
    },
    set currentActivePlotTab(val) {
        if (val) {
            this.activePlotChannels.clear();
            this.activePlotChannels.add(val);
        } else {
            this.activePlotChannels.clear();
        }
    },
    
    // Right panel active tab state
    activeRightTab: 'cornering',
    sidePanelWidth: 380,
    sidePanelCollapsed: false,
    
    // Sync state
    _syncLastRange: null,
    _syncApplying: false,
    _syncRafId: null,

    get groupAVisible() {
        return this.isGroupVisible('A');
    },
    set groupAVisible(val) {
        this.setGroupVisible('A', val);
    },
    get groupBVisible() {
        return this.isGroupVisible('B');
    },
    set groupBVisible(val) {
        this.setGroupVisible('B', val);
    }
};
