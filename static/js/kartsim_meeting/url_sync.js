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
 * URL Synchronization utility for the kartsim meeting page.
 * Synchronizes tab, selected laps, bottom plots visibility, xlim, and map center/zoom.
 */
import { state } from './state.js';

let updateTimeout = null;

/**
 * Encodes a lapId into a short index-based notation (e.g. "0_4" for 4th lap of 1st session)
 * if session data is available, otherwise returns lapId.
 */
export function formatLapForUrl(lapId) {
    const lap = state.lapDataLookup ? state.lapDataLookup[lapId] : null;
    if (lap && state.allSessionsData && state.allSessionsData.length > 0) {
        const sIdx = state.allSessionsData.findIndex(s => s.session_id === lap.session_id);
        if (sIdx !== -1 && lap.lap_num !== undefined) {
            return `${sIdx}_${lap.lap_num}`;
        }
    }
    return lapId;
}

/**
 * Compares two short lap keys (e.g. "0_4", "1_10") in session-index and lap-number order.
 */
export function compareShortLapKeys(a, b) {
    const [sA, lA] = a.split('_').map(Number);
    const [sB, lB] = b.split('_').map(Number);
    if (!isNaN(sA) && !isNaN(sB)) {
        if (sA !== sB) return sA - sB;
        if (!isNaN(lA) && !isNaN(lB)) return lA - lB;
    }
    return a.localeCompare(b);
}

/**
 * Debounces URL updates to ensure smooth scrolling/dragging performance.
 */
export function debouncedUpdateURL() {
    if (updateTimeout) clearTimeout(updateTimeout);
    updateTimeout = setTimeout(updateURL, 200);
}

/**
 * Updates the URL query parameters to reflect the current page state.
 */
export function updateURL() {
    if (!state.mapInitialized) return;
    try {
        const params = new URLSearchParams(window.location.search);

        // 1. Stats vs Map subtab
        const activeTabBtn = document.querySelector('.sidebar-tab.active');
        if (activeTabBtn) {
            const tabId = activeTabBtn.id.replace('btn-', '');
            params.set('tab', tabId);
        } else {
            params.delete('tab');
        }

        // 1b. Session ID filter
        if (state.selectedSessionId && state.selectedSessionId !== 'all') {
            params.set('session_id', state.selectedSessionId);
        } else {
            params.delete('session_id');
        }

        // 2. Group Lap Selection & Dynamic Groups
        const groups = state.groups || ['A', 'B'];
        const isDefaultGroups = groups.length === 2 && groups[0] === 'A' && groups[1] === 'B';
        if (!isDefaultGroups) {
            params.set('groups', groups.join(','));
        } else {
            params.delete('groups');
        }

        const disabled = groups.filter(g => g !== 'A' && state.groupEnabled && state.groupEnabled[g] === false);
        if (disabled.length > 0) {
            params.set('dis', disabled.join(','));
        } else {
            params.delete('dis');
        }

        // 3. Per-group Lap Selection and Visibility
        for (let code = 65; code <= 90; code++) { // 'A' to 'Z'
            const g = String.fromCharCode(code);
            if (groups.includes(g)) {
                const selection = state.groupSelections ? state.groupSelections[g] : null;
                if (selection) {
                    if (selection.size > 0) {
                        const formatted = Array.from(selection).map(formatLapForUrl).sort(compareShortLapKeys);
                        params.set('laps' + g, formatted.join(','));
                    } else {
                        params.set('laps' + g, 'none');
                    }
                }
                const isVis = state.isGroupVisible ? state.isGroupVisible(g) : (g === 'A' ? state.groupAVisible : state.groupBVisible);
                params.set('vis' + g, isVis ? '1' : '0');
            } else {
                params.delete('laps' + g);
                params.delete('vis' + g);
            }
        }

        // 4. Bottom sidebar plots (delta, speed, active plot tab)
        params.set('delta', state.deltaPlotVisible ? '1' : '0');
        params.set('speed', state.speedPlotVisible ? '1' : '0');
        if (state.activePlotChannels && state.activePlotChannels.size > 0) {
            params.set('plot', Array.from(state.activePlotChannels).join(','));
        } else {
            params.delete('plot');
        }

        // 4b. Right panel active tab
        if (state.activeRightTab) {
            params.set('rtab', state.activeRightTab);
        } else {
            params.delete('rtab');
        }

        // 4c. Slip angle plot color mode
        if (state.slipAngleColorMode && state.slipAngleColorMode !== 'brake_throttle') {
            params.set('sacol', state.slipAngleColorMode);
        } else {
            params.delete('sacol');
        }

        // 4d. Trajectory color mode
        if (state.trajectoryColorMode && state.trajectoryColorMode !== 'pedals') {
            params.set('tcol', state.trajectoryColorMode);
        } else {
            params.delete('tcol');
        }

        // 5. Telemetry xlim (range)
        if (state.globalTelemetryXRange && state.globalTelemetryXRange.length === 2) {
            if (state.trackData && state.trackData.lap_length &&
                Math.abs(state.globalTelemetryXRange[0] - 0) < 1 &&
                Math.abs(state.globalTelemetryXRange[1] - state.trackData.lap_length) < 1) {
                params.delete('xlim');
            } else if (state.globalTelemetryXRange[0] === 0 && state.globalTelemetryXRange[1] === 100) {
                params.delete('xlim');
            } else {
                params.set('xlim', `${state.globalTelemetryXRange[0].toFixed(2)},${state.globalTelemetryXRange[1].toFixed(2)}`);
            }
        } else {
            params.delete('xlim');
        }

        // 6. Map zoom & position
        if (state.map && state.mapInitialized) {
            const center = state.map.getCenter();
            const zoom = state.map.getZoom();
            if (center && zoom !== undefined) {
                params.set('map', `${zoom},${center.lat().toFixed(6)},${center.lng().toFixed(6)}`);
            }
        }

        // 7. Sorting mode and criteria
        if (state.sortMode && state.sortMode !== 'std') {
            params.set('sort', state.sortMode);
        } else {
            params.delete('sort');
        }
        if (state.sortMode === 'turn' && state.currentTurnIdx !== undefined) {
            params.set('turn', state.currentTurnIdx);
        } else {
            params.delete('turn');
        }

        // 8. Distance/cursor position
        if (state.playbackDistance !== undefined && state.playbackDistance !== null && state.playbackDistance !== 0) {
            params.set('dist', state.playbackDistance.toFixed(2));
        } else {
            params.delete('dist');
        }

        const newSearch = params.toString();
        const newURL = window.location.pathname + (newSearch ? '?' + newSearch : '');
        window.history.replaceState(null, '', newURL);
    } catch (e) {
        console.error("Error updating URL query parameters:", e);
    }
}
