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
 * Main entry point for the kartsim meeting page.
 */
import { state } from './state.js';
import { showTab, showRightPanelTab, showStatsSubTab, setSort, selectTurnAndSwitchToMap, toggleAllLaps, toggleGroupVisibility, togglePlay, setPlaybackSpeed, toggleDeltaPlot, toggleSpeedPlot, initAllLapsHandlers, onSessionChange } from './lap_selection.js';
import { setMapType, setTrajectoryColorMode, toggleTrajDropdown, updateDistanceMarker } from './map.js';
import { stepDistance } from './telemetry.js';
import { initExpandablePlots } from './plots_sync.js';
import { debouncedUpdateURL } from './url_sync.js';

// Attach functions to window for HTML onclick handlers
window.showTab = showTab;
window.showRightPanelTab = showRightPanelTab;
window.showStatsSubTab = showStatsSubTab;
window.setSort = setSort;
window.selectTurnAndSwitchToMap = selectTurnAndSwitchToMap;
window.onSessionChange = onSessionChange;

window.toggleAllLaps = toggleAllLaps;
window.toggleGroupVisibility = toggleGroupVisibility;
window.togglePlay = togglePlay;
window.setPlaybackSpeed = setPlaybackSpeed;
window.setMapType = setMapType;
window.setTrajectoryColorMode = setTrajectoryColorMode;
window.toggleTrajDropdown = toggleTrajDropdown;
window.toggleDeltaPlot = toggleDeltaPlot;
window.toggleSpeedPlot = toggleSpeedPlot;

document.addEventListener('DOMContentLoaded', () => {
    document.addEventListener('click', (e) => {
        const dropdown = document.getElementById('traj-color-dropdown');
        if (dropdown && !dropdown.contains(e.target)) {
            dropdown.classList.remove('open');
        }
    });
    const slider = document.getElementById('distance-slider');
    const display = document.getElementById('distance-display');

    if (slider && display) {
        slider.addEventListener('input', (e) => {
            if (state.isPlaying) togglePlay();
            state.playbackDistance = parseFloat(e.target.value);
            display.textContent = Math.round(state.playbackDistance) + 'm';
            updateDistanceMarker(state.playbackDistance);
        });

        const handleWheel = (e) => {
            if (Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
                e.preventDefault();
                stepDistance(e.deltaX * 0.05);
            }
        };

        slider.addEventListener('wheel', handleWheel, { passive: false });

        ['expandable-plots-block', 'telemetry-chart-container'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.addEventListener('wheel', handleWheel, { passive: false });
        });
    }

    const playBtn = document.getElementById('play-pause-btn');
    const speedPopup = document.getElementById('speed-popup');

    if (playBtn && speedPopup) {
        playBtn.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            speedPopup.style.display = speedPopup.style.display === 'flex' ? 'none' : 'flex';
        });

        document.addEventListener('click', (e) => {
            if (!playBtn.contains(e.target) && !speedPopup.contains(e.target)) {
                speedPopup.style.display = 'none';
            }
        });
    }

    // Initialize
    const params = new URLSearchParams(window.location.search);
    const initialTab = params.get('tab') || 'stats';
    const sidParam = params.get('session_id') || (window.KART_CONFIG && window.KART_CONFIG.sessionId);
    if (sidParam) {
        state.selectedSessionId = sidParam;
        const sessionSelector = document.getElementById('session-selector');
        if (sessionSelector) {
            sessionSelector.value = sidParam;
        }
    }
    showTab(initialTab);
    initExpandablePlots();
    initAllLapsHandlers();

    // Restore bottom plots state from URL
    const delta = params.get('delta');
    const speed = params.get('speed');
    let activePlot = params.get('plot');

    if (delta === '1') {
        toggleDeltaPlot();
    }
    if (speed === '1') {
        toggleSpeedPlot();
    }
    if (activePlot) {
        activePlot.split(',').forEach(plotPart => {
            let p = plotPart.trim();
            if (p === 'lat_force') {
                p = 'force';
            } else if (p === 'lat_patch_vel' || p === 'long_patch_vel') {
                p = 'patch_vel';
            }
            const tabEl = document.querySelector(`.plot-tab[data-tab="${p}"]`);
            if (tabEl) {
                tabEl.click();
            }
        });
    }

    // Restore Group A/B visibility state from URL
    const visA = params.get('visA');
    const visB = params.get('visB');
    if (visA !== null) {
        const targetVisA = visA === '1';
        if (state.groupAVisible !== targetVisA) {
            toggleGroupVisibility('A');
        }
    }
    if (visB !== null) {
        const targetVisB = visB === '1';
        if (state.groupBVisible !== targetVisB) {
            toggleGroupVisibility('B');
        }
    }

    // Restore slip angle color mode from URL
    const sacol = params.get('sacol') || 'brake_throttle';
    const colorModeSelector = document.getElementById('slip_angle-color-mode');
    if (colorModeSelector) {
        colorModeSelector.value = sacol;
        state.slipAngleColorMode = sacol;
        colorModeSelector.addEventListener('change', (e) => {
            state.slipAngleColorMode = e.target.value;
            import('./plots_sync.js').then(plots => {
                plots.updateSlipAnglePlot(state.currentTargetDist);
            });
            debouncedUpdateURL();
        });
    }

    // Restore right panel tab from URL
    const rtab = params.get('rtab') || 'cornering';
    showRightPanelTab(rtab);

    // Restore trajectory color mode from URL
    const tcol = params.get('tcol');
    if (tcol && ['pedals', 'speed', 'accel', 'gforce_lon', 'gforce_lat', 'lap', 'delta_t'].includes(tcol)) {
        setTrajectoryColorMode(tcol);
    }

    window.addEventListener('resize', () => {
        const ids = ['stats-plot-lap-times', 'stats-plot-turn-gaps', 'stats-plot-apex-speeds', 'telemetry-chart', 'acceleration-chart', 'slip_angle-chart'];
        ids.forEach(id => {
            const gd = document.getElementById(id);
            if (gd && gd.offsetParent !== null) Plotly.Plots.resize(gd);
        });
    });
});

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;

    if (e.key === ' ') {
        e.preventDefault();
        togglePlay();
    } else if (e.key === '.' || e.key === '>') {
        stepDistance(1);
    } else if (e.key === ',' || e.key === '<') {
        stepDistance(-1);
    }
});
