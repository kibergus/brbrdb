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
import { showTab, showRightPanelTab, showStatsSubTab, setSort, selectTurnAndSwitchToMap, toggleAllLaps, toggleGroupVisibility, toggleGroupManagementPopup, togglePlay, setPlaybackSpeed, toggleDeltaPlot, toggleSpeedPlot, initAllLapsHandlers, initReportInteractions, onSessionChange, toggleSidePanel, collapseSidePanel, expandSidePanel, initSidePanelResizer, updateSessionSelectorColors, toggleSessionDropdown, selectSessionFromDropdown, toggleMobileSidebar, toggleMobileRightPanel } from './lap_selection.js';
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
window.toggleSessionDropdown = toggleSessionDropdown;
window.selectSessionFromDropdown = selectSessionFromDropdown;

window.toggleAllLaps = toggleAllLaps;
window.toggleGroupVisibility = toggleGroupVisibility;
window.toggleGroupManagementPopup = toggleGroupManagementPopup;
window.togglePlay = togglePlay;
window.setPlaybackSpeed = setPlaybackSpeed;
window.setMapType = setMapType;
window.setTrajectoryColorMode = setTrajectoryColorMode;
window.toggleTrajDropdown = toggleTrajDropdown;
window.toggleDeltaPlot = toggleDeltaPlot;
window.toggleSpeedPlot = toggleSpeedPlot;
window.toggleSidePanel = toggleSidePanel;
window.collapseSidePanel = collapseSidePanel;
window.expandSidePanel = expandSidePanel;
window.toggleMobileSidebar = toggleMobileSidebar;
window.toggleMobileRightPanel = toggleMobileRightPanel;

document.addEventListener('DOMContentLoaded', () => {
    document.addEventListener('click', (e) => {
        const dropdown = document.getElementById('traj-color-dropdown');
        if (dropdown && !dropdown.contains(e.target)) {
            dropdown.classList.remove('open');
        }
        const sessionDropdown = document.getElementById('session-dropdown');
        if (sessionDropdown && !sessionDropdown.contains(e.target)) {
            sessionDropdown.classList.remove('open');
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
    const isReportMode = Boolean(window.KART_CONFIG && window.KART_CONFIG.isReportMode);
    const reportState = (window.KART_CONFIG && window.KART_CONFIG.reportState) || {};

    const params = new URLSearchParams(window.location.search);
    const initialTab = params.get('tab') || reportState.tab || (isReportMode ? 'map' : 'stats');
    const sidParam = params.get('session_id') || reportState.session_id || (window.KART_CONFIG && window.KART_CONFIG.sessionId);
    if (sidParam) {
        state.selectedSessionId = sidParam;
        const sessionSelector = document.getElementById('session-selector');
        if (sessionSelector) {
            sessionSelector.value = sidParam;
        }
    }
    showTab(initialTab);
    initExpandablePlots();
    initSidePanelResizer();
    initAllLapsHandlers();
    updateSessionSelectorColors();
    initReportInteractions();

    if (isReportMode) {
        if (reportState.sidePanelWidth) {
            state.sidePanelWidth = reportState.sidePanelWidth;
            const sidePanel = document.getElementById('map-side-panel');
            if (sidePanel) {
                sidePanel.style.width = `${state.sidePanelWidth}px`;
            }
        }
        expandSidePanel();
    }

    // Restore bottom plots state from URL or reportState
    const delta = params.has('delta') ? params.get('delta') : (reportState.delta ? '1' : null);
    const speed = params.has('speed') ? params.get('speed') : (reportState.speed ? '1' : null);
    let activePlot = params.get('plot') || (Array.isArray(reportState.plot) ? reportState.plot.join(',') : reportState.plot);

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

    // Restore Group A/B visibility state from URL or reportState
    const visA = params.has('visA') ? params.get('visA') : (reportState.visA !== undefined ? (reportState.visA ? '1' : '0') : null);
    const visB = params.has('visB') ? params.get('visB') : (reportState.visB !== undefined ? (reportState.visB ? '1' : '0') : null);
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

    // Restore slip angle color mode from URL or reportState
    const sacol = params.get('sacol') || reportState.sacol || 'brake_throttle';
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

    // Restore right panel tab from URL or reportState
    const rtab = params.get('rtab') || reportState.rtab || (isReportMode ? 'report' : 'cornering');
    showRightPanelTab(rtab);

    // Restore trajectory color mode from URL or reportState
    const tcol = params.get('tcol') || reportState.tcol;
    if (tcol && ['pedals', 'speed', 'accel', 'gforce_lon', 'gforce_lat', 'lap', 'delta_t', 'time'].includes(tcol)) {
        setTrajectoryColorMode(tcol);
    }

    // Restore distance if specified in URL or reportState
    const distParam = params.get('dist');
    if (distParam !== null) {
        const d = parseFloat(distParam);
        if (!isNaN(d)) {
            state.playbackDistance = d;
            const slider = document.getElementById('distance-slider');
            const display = document.getElementById('distance-display');
            if (slider) slider.value = d;
            if (display) display.textContent = Math.round(d) + 'm';
            updateDistanceMarker(d);
        }
    } else if (reportState.dist !== undefined) {
        const d = parseFloat(reportState.dist);
        if (!isNaN(d)) {
            state.playbackDistance = d;
            const slider = document.getElementById('distance-slider');
            const display = document.getElementById('distance-display');
            if (slider) slider.value = d;
            if (display) display.textContent = Math.round(d) + 'm';
            updateDistanceMarker(d);
        }
    }

    window.addEventListener('resize', () => {
        if (window.innerWidth > 768 && document.body.classList.contains('mobile-sidebar-open')) {
            toggleMobileSidebar(false);
        }
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

    if (e.key === 'Escape' && document.body.classList.contains('mobile-sidebar-open')) {
        toggleMobileSidebar(false);
        return;
    }

    if (e.key === ' ') {
        e.preventDefault();
        togglePlay();
    } else if (e.key === '.' || e.key === '>') {
        stepDistance(1);
    } else if (e.key === ',' || e.key === '<') {
        stepDistance(-1);
    }
});
