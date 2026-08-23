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
 * Global state for the kartsim meeting page.
 */

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
    groupAVisibleMap: true,
    groupBVisibleMap: false,
    groupAVisibleStats: true,
    groupBVisibleStats: true,
    activeTab: 'stats',
    selectedSessionId: null,
    groupASelection: new Set(),
    groupBSelection: new Set(),
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
    
    // Sync state
    _syncLastRange: null,
    _syncApplying: false,
    _syncRafId: null,

    get groupAVisible() {
        return this.activeTab === 'stats' ? this.groupAVisibleStats : this.groupAVisibleMap;
    },
    set groupAVisible(val) {
        if (this.activeTab === 'stats') {
            this.groupAVisibleStats = val;
        } else {
            this.groupAVisibleMap = val;
        }
    },
    get groupBVisible() {
        return this.activeTab === 'stats' ? this.groupBVisibleStats : this.groupBVisibleMap;
    },
    set groupBVisible(val) {
        if (this.activeTab === 'stats') {
            this.groupBVisibleStats = val;
        } else {
            this.groupBVisibleMap = val;
        }
    }
};
