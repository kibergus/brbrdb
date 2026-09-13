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
 * UI interaction logic: tab switching, lap list rendering, and selection.
 */
import { state } from './state.js';
import { parseLapTime } from '../utils.js';
import { getTurnTime, animateSlider, getPointAtDistance } from './telemetry.js';
import { updateTelemetryPlots, updateAccelerationPlot, updateSlipAnglePlot, renderExpandablePlots, updateExpandablePlotsVisibility, togglePlot, setVisiblePlots } from './plots_sync.js';
import { renderStatsPlots } from './stats_plots.js';
import { initMap, loadTrackPoints, calculateBoundsZoom, updateAllPolylineColors, getReferenceLap, updateDistanceMarker, setTrajectoryColorMode, matchesRequestedLap } from './map.js';
import { debouncedUpdateURL } from './url_sync.js';

export function showRightPanelTab(tabId) {
    state.activeRightTab = tabId;
    
    // Toggle active classes on right panel tab buttons
    document.querySelectorAll('.right-panel-tab').forEach(btn => btn.classList.remove('active'));
    const activeBtn = document.getElementById('right-tab-btn-' + tabId);
    if (activeBtn) activeBtn.classList.add('active');

    // Toggle active panes
    document.querySelectorAll('.right-panel-tab-pane').forEach(pane => {
        pane.classList.remove('active');
        pane.style.display = 'none';
    });
    const activePane = document.getElementById(tabId + '-chart-container');
    if (activePane) {
        activePane.classList.add('active');
        activePane.style.display = 'flex';
    }

    // Force update of the visible chart
    if (tabId === 'cornering') {
        updateTelemetryPlots(state.currentTargetDist);
        setTimeout(() => {
            const gd = document.getElementById('telemetry-chart');
            if (gd && gd.offsetParent !== null) Plotly.Plots.resize(gd);
        }, 100);
    } else if (tabId === 'acceleration') {
        updateAccelerationPlot(state.currentTargetDist);
        setTimeout(() => {
            const gd = document.getElementById('acceleration-chart');
            if (gd && gd.offsetParent !== null) Plotly.Plots.resize(gd);
        }, 100);
    } else if (tabId === 'slip_angle') {
        updateSlipAnglePlot(state.currentTargetDist);
        setTimeout(() => {
            const gd = document.getElementById('slip_angle-chart');
            if (gd && gd.offsetParent !== null) Plotly.Plots.resize(gd);
        }, 100);
    }
    debouncedUpdateURL();
}

export function triggerPlotsResize() {
    const ids = [
        'stats-plot-lap-times', 'stats-plot-turn-gaps', 'stats-plot-apex-speeds',
        'telemetry-chart', 'acceleration-chart', 'slip_angle-chart',
        'plot-area-speed', 'plot-area-delta'
    ];
    if (state.activePlotChannels) {
        state.activePlotChannels.forEach(tab => {
            ids.push('plot-area-' + tab);
        });
    }
    ids.forEach(id => {
        const gd = document.getElementById(id);
        if (gd && typeof Plotly !== 'undefined' && Plotly.Plots && gd.offsetParent !== null) {
            Plotly.Plots.resize(gd);
        }
    });
    if (state.map && window.google && window.google.maps) {
        google.maps.event.trigger(state.map, 'resize');
    }
}

export function toggleSidePanel() {
    const sidePanel = document.querySelector('.map-side-panel');
    if (!sidePanel) return;
    if (sidePanel.classList.contains('collapsed')) {
        expandSidePanel();
    } else {
        collapseSidePanel();
    }
}

export function collapseSidePanel(triggerResize = true) {
    const sidePanel = document.querySelector('.map-side-panel');
    const tabMap = document.getElementById('tab-map');
    const toggleBtn = document.getElementById('side-panel-toggle-btn');
    if (!sidePanel) return;

    sidePanel.classList.add('collapsed');
    if (tabMap) tabMap.classList.add('side-panel-collapsed');
    state.sidePanelCollapsed = true;
    try {
        localStorage.setItem('kartsim_side_panel_collapsed', '1');
    } catch (e) {}

    if (toggleBtn) {
        toggleBtn.setAttribute('title', 'Expand panel');
    }

    if (triggerResize) {
        setTimeout(() => {
            triggerPlotsResize();
        }, 220);
    }
}

export function expandSidePanel(triggerResize = true) {
    const sidePanel = document.querySelector('.map-side-panel');
    const tabMap = document.getElementById('tab-map');
    const toggleBtn = document.getElementById('side-panel-toggle-btn');
    if (!sidePanel) return;

    sidePanel.classList.remove('collapsed');
    if (tabMap) tabMap.classList.remove('side-panel-collapsed');
    state.sidePanelCollapsed = false;
    try {
        localStorage.setItem('kartsim_side_panel_collapsed', '0');
    } catch (e) {}

    if (toggleBtn) {
        toggleBtn.setAttribute('title', 'Collapse panel');
    }

    try {
        const savedWidth = localStorage.getItem('kartsim_side_panel_width');
        if (savedWidth) {
            sidePanel.style.width = savedWidth + 'px';
            state.sidePanelWidth = parseInt(savedWidth, 10);
        } else if (!sidePanel.style.width) {
            sidePanel.style.width = '380px';
            state.sidePanelWidth = 380;
        }
    } catch (e) {
        if (!sidePanel.style.width) {
            sidePanel.style.width = '380px';
        }
    }

    if (triggerResize) {
        setTimeout(() => {
            triggerPlotsResize();
            showRightPanelTab(state.activeRightTab || 'cornering');
        }, 220);
    }
}

export function initSidePanelResizer() {
    const sidePanel = document.querySelector('.map-side-panel');
    const resizer = document.getElementById('side-panel-resizer');
    const toggleBtn = document.getElementById('side-panel-toggle-btn');
    if (!sidePanel) return;

    let savedCollapsed = null;
    let savedWidth = null;
    try {
        savedCollapsed = localStorage.getItem('kartsim_side_panel_collapsed');
        savedWidth = localStorage.getItem('kartsim_side_panel_width');
    } catch (e) {}

    if (savedWidth) {
        const parsed = parseInt(savedWidth, 10);
        const maxAllowed = (typeof window !== 'undefined' && window.innerWidth) ? window.innerWidth * 0.8 : 1600;
        if (parsed >= 200 && parsed <= maxAllowed) {
            sidePanel.style.width = parsed + 'px';
            state.sidePanelWidth = parsed;
        }
    }

    if (savedCollapsed === '1') {
        collapseSidePanel(false);
    }

    if (!resizer) return;

    let isResizing = false;
    let startX = 0;
    let startWidth = 0;

    resizer.addEventListener('mousedown', (e) => {
        if (sidePanel.classList.contains('collapsed')) return;
        isResizing = true;
        startX = e.clientX;
        startWidth = sidePanel.getBoundingClientRect().width;
        sidePanel.classList.add('resizing');
        document.body.style.cursor = 'ew-resize';
        document.body.style.userSelect = 'none';
        resizer.classList.add('active');
        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        const dx = startX - e.clientX;
        const minWidth = 240;
        const winWidth = (typeof window !== 'undefined' && window.innerWidth) ? window.innerWidth : 1200;
        const maxWidth = Math.max(minWidth, Math.min(winWidth - 300, Math.floor(winWidth * 0.75)));
        const newWidth = Math.max(minWidth, Math.min(maxWidth, startWidth + dx));
        sidePanel.style.width = newWidth + 'px';
        state.sidePanelWidth = newWidth;

        // Resize visible right panel charts during drag
        const chartIds = ['telemetry-chart', 'acceleration-chart', 'slip_angle-chart'];
        chartIds.forEach(id => {
            const gd = document.getElementById(id);
            if (gd && typeof Plotly !== 'undefined' && Plotly.Plots && gd.offsetParent !== null) {
                Plotly.Plots.resize(gd);
            }
        });
    });

    document.addEventListener('mouseup', () => {
        if (!isResizing) return;
        isResizing = false;
        sidePanel.classList.remove('resizing');
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
        resizer.classList.remove('active');
        try {
            localStorage.setItem('kartsim_side_panel_width', sidePanel.offsetWidth);
        } catch (e) {}

        triggerPlotsResize();
    });
}

export const SESSION_PALETTE = [
    '#3b82f6', // Blue
    '#10b981', // Emerald
    '#a855f7', // Purple
    '#f59e0b', // Amber
    '#ec4899', // Pink
    '#06b6d4', // Cyan
    '#84cc16', // Lime
    '#f97316', // Orange
    '#6366f1', // Indigo
    '#14b8a6', // Teal
];

export function getSessionColor(sessionId) {
    if (!sessionId || sessionId === 'all') return '#94a3b8';

    if (state.sessionColors && state.sessionColors[sessionId]) {
        return state.sessionColors[sessionId];
    }

    const selector = typeof document !== 'undefined' ? document.getElementById('session-selector') : null;
    if (selector && selector.options) {
        let sessionIdx = 0;
        for (let i = 0; i < selector.options.length; i++) {
            const opt = selector.options[i];
            if (opt.value === 'all') continue;
            if (opt.value === sessionId) {
                const color = (opt.dataset && opt.dataset.color) || SESSION_PALETTE[sessionIdx % SESSION_PALETTE.length];
                state.sessionColors = state.sessionColors || {};
                state.sessionColors[sessionId] = color;
                return color;
            }
            sessionIdx++;
        }
    }

    if (state.allSessionsData && state.allSessionsData.length > 0) {
        const idx = state.allSessionsData.findIndex(s => s.session_id === sessionId);
        if (idx !== -1) {
            const color = SESSION_PALETTE[idx % SESSION_PALETTE.length];
            state.sessionColors = state.sessionColors || {};
            state.sessionColors[sessionId] = color;
            return color;
        }
    }

    return SESSION_PALETTE[0];
}

export function getSessionOrderIndex(sessionId) {
    if (!sessionId || sessionId === 'all') return 999999;
    const selector = typeof document !== 'undefined' ? document.getElementById('session-selector') : null;
    if (selector && selector.options) {
        let sessionIdx = 0;
        for (let i = 0; i < selector.options.length; i++) {
            const opt = selector.options[i];
            if (opt.value === 'all') continue;
            if (String(opt.value) === String(sessionId)) {
                return sessionIdx;
            }
            sessionIdx++;
        }
    }

    if (state.allSessionsData && state.allSessionsData.length > 0) {
        const idx = state.allSessionsData.findIndex(s => String(s.session_id) === String(sessionId));
        if (idx !== -1) return idx;
    }

    return 999999;
}

export function compareLaps(a, b) {
    try {
        if (state.sortMode === 'time') {
            return parseLapTime(a.lap_time) - parseLapTime(b.lap_time);
        } else if (state.sortMode === 'turn') {
            const turnSelector = typeof document !== 'undefined' ? document.getElementById('turn-selector') : null;
            const turnIdx = parseInt(turnSelector && turnSelector.value !== "" ? turnSelector.value : state.currentTurnIdx || 0);
            return (getTurnTime(a, turnIdx) || 999999) - (getTurnTime(b, turnIdx) || 999999);
        }
        // Sorting by lap number: split by session first, then lap number
        if (a.sessionId !== b.sessionId) {
            const orderA = getSessionOrderIndex(a.sessionId);
            const orderB = getSessionOrderIndex(b.sessionId);
            if (orderA !== orderB) return orderA - orderB;
            const nameCmp = (a.sessionName || "").localeCompare(b.sessionName || "");
            if (nameCmp !== 0) return nameCmp;
            const idCmp = String(a.sessionId || "").localeCompare(String(b.sessionId || ""));
            if (idCmp !== 0) return idCmp;
        }
        if (a.lap_num !== b.lap_num) return a.lap_num - b.lap_num;
        return String(a.lapId || "").localeCompare(String(b.lapId || ""));
    } catch (e) {
        console.error("Sort error:", e);
        return 0;
    }
}

export function toggleSessionDropdown(event) {
    if (event && event.stopPropagation) event.stopPropagation();
    const dropdown = document.getElementById('session-dropdown');
    if (dropdown && dropdown.classList) {
        dropdown.classList.toggle('open');
    }
}

export function selectSessionFromDropdown(sessionId) {
    const dropdown = document.getElementById('session-dropdown');
    if (dropdown && dropdown.classList) {
        dropdown.classList.remove('open');
    }
    const selector = document.getElementById('session-selector');
    if (selector) {
        selector.value = sessionId;
    }
    onSessionChange(sessionId);
}

export function updateSessionSelectorColors() {
    if (typeof document === 'undefined') return;
    const selector = document.getElementById('session-selector');
    if (!selector || !selector.options) return;

    let sessionIdx = 0;
    for (let i = 0; i < selector.options.length; i++) {
        const opt = selector.options[i];
        if (opt.value === 'all') {
            opt.style.backgroundColor = '#0f172a';
            opt.style.color = 'var(--text-primary, #f8fafc)';
            continue;
        }
        const color = (opt.dataset && opt.dataset.color) || SESSION_PALETTE[sessionIdx % SESSION_PALETTE.length];
        state.sessionColors = state.sessionColors || {};
        state.sessionColors[opt.value] = color;
        opt.style.backgroundColor = '#0f172a';
        opt.style.color = color;
        sessionIdx++;
    }

    const curVal = selector.value;
    const isAll = !curVal || curVal === 'all';
    const activeColor = isAll ? 'var(--text-primary, #f8fafc)' : getSessionColor(curVal);

    if (selector.style) {
        selector.style.backgroundColor = '#0f172a';
        selector.style.color = activeColor;
    }

    const dropdownBtn = document.getElementById('session-dropdown-btn');
    if (dropdownBtn && dropdownBtn.style) {
        dropdownBtn.style.backgroundColor = '#0f172a';
        dropdownBtn.style.color = activeColor;
    }

    const currentLabel = document.getElementById('session-dropdown-current-label');
    if (currentLabel) {
        if (isAll) {
            currentLabel.textContent = 'All Sessions';
        } else {
            const selectedOpt = Array.from(selector.options).find(o => o.value === curVal);
            if (selectedOpt) {
                currentLabel.textContent = (selectedOpt.textContent || '').trim();
            } else {
                const sessionObj = state.allSessionsData && state.allSessionsData.find(s => s.session_id === curVal);
                if (sessionObj) {
                    currentLabel.textContent = sessionObj.session_name;
                }
            }
        }
    }

    const dropdownMenu = document.getElementById('session-dropdown-menu');
    if (dropdownMenu && dropdownMenu.querySelectorAll) {
        const items = dropdownMenu.querySelectorAll('.dropdown-item');
        if (items) {
            items.forEach(item => {
                const sessId = item.getAttribute('data-session-id');
                const isActive = sessId === curVal || (isAll && sessId === 'all');
                if (item.classList) {
                    if (isActive) {
                        item.classList.add('active');
                    } else {
                        item.classList.remove('active');
                    }
                }
                if (item.style) {
                    item.style.backgroundColor = isActive ? '#1e293b' : '#0f172a';
                    if (sessId && sessId !== 'all') {
                        const itemColor = (item.dataset && item.dataset.color) || getSessionColor(sessId);
                        item.style.color = itemColor;
                    } else if (sessId === 'all') {
                        item.style.color = 'var(--text-primary, #f8fafc)';
                    }
                }
            });
        }
    }
}

export function onSessionChange(sessionId) {
    state.selectedSessionId = sessionId;
    state.mapInitialized = false;
    updateSessionSelectorColors();
    debouncedUpdateURL();
    loadTrackPoints(sessionId);
}

let progressionLoaded = false;

export function resetProgressionLoaded() {
    progressionLoaded = false;
}

export function showStatsSubTab(subTabId) {
    try {
        const overviewBtn = document.getElementById('stats-tab-btn-overview');
        const progressionBtn = document.getElementById('stats-tab-btn-progression');
        const overviewPane = document.getElementById('stats-subtab-overview');
        const progressionPane = document.getElementById('stats-subtab-progression');

        if (overviewBtn) overviewBtn.classList.toggle('active', subTabId === 'overview');
        if (progressionBtn) progressionBtn.classList.toggle('active', subTabId === 'progression');

        if (overviewPane) {
            overviewPane.classList.toggle('active', subTabId === 'overview');
            overviewPane.style.display = (subTabId === 'overview') ? 'block' : 'none';
        }
        if (progressionPane) {
            progressionPane.classList.toggle('active', subTabId === 'progression');
            progressionPane.style.display = (subTabId === 'progression') ? 'block' : 'none';
        }

        if (subTabId === 'overview') {
            setTimeout(() => {
                const ids = ['stats-plot-lap-times', 'stats-plot-turn-gaps', 'stats-plot-apex-speeds'];
                ids.forEach(id => {
                    const gd = document.getElementById(id);
                    if (gd && window.Plotly && window.Plotly.Plots) window.Plotly.Plots.resize(gd);
                });
            }, 100);
        } else if (subTabId === 'progression') {
            loadProgressionPlots();
        }
    } catch (e) {
        console.error("Error in showStatsSubTab:", e);
    }
}

export function loadProgressionPlots() {
    if (progressionLoaded) return;
    const progressionPane = document.getElementById('stats-subtab-progression');
    const container = document.getElementById('stats-progression-container');
    if (!progressionPane || !container) return;

    const league = progressionPane.dataset.league;
    const className = progressionPane.dataset.class;
    const track = progressionPane.dataset.track;
    const date = progressionPane.dataset.date || '';

    if (!league || !className || !track) return;

    container.innerHTML = `
        <div class="plot-card" style="padding: 2rem; text-align: center;">
            <div class="spinner" style="width: 32px; height: 32px; margin: 0 auto 1rem auto;"></div>
            <p style="color: var(--text-secondary);">Loading track progression plots...</p>
        </div>
    `;

    const url = `/api/track_progression?league=${encodeURIComponent(league)}&class_name=${encodeURIComponent(className)}&track=${encodeURIComponent(track)}&date=${encodeURIComponent(date)}`;
    fetch(url)
        .then(res => res.json())
        .then(data => {
            if (!data.plots || data.plots.length === 0) {
                container.innerHTML = `
                    <div class="plot-card" style="padding: 2rem; text-align: center; color: var(--text-secondary);">
                        No progression data available for this track.
                    </div>
                `;
                return;
            }

            progressionLoaded = true;
            container.innerHTML = '';

            data.plots.forEach((plot, index) => {
                const card = document.createElement('div');
                card.className = 'plot-card';
                const loadingId = `loading-progression-${index}`;
                const imgId = `plot-img-progression-${index}`;

                card.innerHTML = `
                    <h3 style="margin-bottom: 1rem;">${plot.title}</h3>
                    <div class="plot-loading" id="${loadingId}">
                        <div class="spinner"></div> Generating progression plot...
                    </div>
                    <img id="${imgId}"
                        src="${plot.plot_url}"
                        alt="${track} - ${plot.title} Progression" style="display: none; width: 100%; height: auto;">
                `;

                container.appendChild(card);

                const img = card.querySelector(`#${imgId}`);
                const loading = card.querySelector(`#${loadingId}`);
                if (img && loading) {
                    img.onload = () => {
                        loading.style.display = 'none';
                        img.style.display = 'block';
                    };
                    img.onerror = () => {
                        loading.textContent = 'Failed to load plot.';
                    };
                }
            });
        })
        .catch(err => {
            console.error("Failed to load progression plots:", err);
            container.innerHTML = `
                <div class="plot-card" style="padding: 2rem; text-align: center; color: var(--warning);">
                    Failed to load progression data.
                </div>
            `;
        });
}


export function showTab(tabId) {
    try {
        const isStatsOrMap = (tabId === 'stats' || tabId === 'map');
        
        // Handle main tab panes
        document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
        const mainTabId = isStatsOrMap ? 'tab-map' : 'tab-' + tabId;
        const mainPane = document.getElementById(mainTabId);
        if (mainPane) mainPane.classList.add('active');

        // Handle subtabs if necessary
        if (isStatsOrMap) {
            state.activeTab = tabId;

            // Automatically hide/show the right sidebar (.map-side-panel)
            const sidePanel = document.querySelector('.map-side-panel');
            if (sidePanel) {
                sidePanel.style.display = (tabId === 'stats') ? 'none' : 'flex';
            }

            // Automatically hide/show bottom plots and map controls bar (slider)
            const expandablePlots = document.getElementById('expandable-plots-block');
            if (expandablePlots) {
                expandablePlots.style.display = (tabId === 'stats') ? 'none' : 'flex';
            }
            const mapControlsBar = document.querySelector('.map-controls-bar');
            if (mapControlsBar) {
                mapControlsBar.style.display = (tabId === 'stats') ? 'none' : 'flex';
            }

            // Update group visibility icons for the active tab
            updateVisibilityIcons();

            if (tabId === 'map') {
                if ((!state.globalTelemetryXRange || state.globalTelemetryXRange.length !== 2) && state.trackData && state.trackData.lap_length) {
                    state.globalTelemetryXRange = [0, state.trackData.lap_length];
                }
                // Update map polylines for the Map tab
                Object.keys(state.lapPolylines).forEach(lapId => updateLapVisibility(lapId));
                // Refresh bottom plots to respect Map tab's visibility settings
                renderExpandablePlots();
                // Ensure right panel chart is refreshed and resized
                showRightPanelTab(state.activeRightTab || 'cornering');
            }

            document.querySelectorAll('.subtab-pane').forEach(p => p.classList.remove('active'));
            const subPane = document.getElementById(tabId + '-view');
            if (subPane) {
                subPane.classList.add('active');
                if (tabId === 'stats') {
                    renderStatsPlots();
                    // Resize plotly charts after tab switch to ensure proper dimensions
                    setTimeout(() => {
                        const ids = ['stats-plot-lap-times', 'stats-plot-turn-gaps', 'stats-plot-apex-speeds'];
                        ids.forEach(id => {
                            const gd = document.getElementById(id);
                            if (gd && window.Plotly && window.Plotly.Plots) Plotly.Plots.resize(gd);
                        });
                    }, 100);
                } else if (tabId === 'map') {
                    if (state.map && typeof window !== 'undefined' && window.google && window.google.maps) {
                        google.maps.event.trigger(state.map, 'resize');
                    }
                    if (state.sortMode === 'turn') {
                        focusMapOnTurn(state.currentTurnIdx);
                    } else if (!state.hasCustomMapSet && state.trackBounds && !state.defaultMapZoom) {
                        if (state.map && typeof state.map.fitBounds === 'function') {
                            state.map.fitBounds(state.trackBounds);
                            if (window.google && window.google.maps && google.maps.event) {
                                google.maps.event.addListenerOnce(state.map, 'idle', () => {
                                    if (state.map && typeof state.map.getZoom === 'function') {
                                        const z = state.map.getZoom();
                                        if (z && z > 5) state.defaultMapZoom = z;
                                    }
                                });
                            }
                        }
                    }
                    setTimeout(() => {
                        const ids = [
                            'telemetry-chart', 'acceleration-chart', 'slip_angle-chart',
                            'plot-area-speed', 'plot-area-delta',
                            'plot-area-pedals', 'plot-area-steering', 'plot-area-rps',
                            'plot-area-gforce', 'plot-area-slide', 'plot-area-patch_vel',
                            'plot-area-force', 'plot-area-tyre_load', 'plot-area-slip_angle'
                        ];
                        ids.forEach(id => {
                            const gd = document.getElementById(id);
                            if (gd && gd._fullLayout && gd.offsetParent !== null && window.Plotly && window.Plotly.Plots) {
                                Plotly.Plots.resize(gd);
                            }
                        });
                    }, 100);
                }
            }
        }

        // Update buttons
        document.querySelectorAll('.sidebar-item, .sidebar-tab').forEach(b => b.classList.remove('active'));
        const btn = document.getElementById('btn-' + tabId);
        if (btn) btn.classList.add('active');

        const mapControls = document.getElementById('sidebar-map-controls');
        if (isStatsOrMap) {
            if (mapControls) mapControls.style.display = 'block';
            initMap();
        } else {
            if (mapControls) mapControls.style.display = 'none';
        }
        debouncedUpdateURL();
    } catch (e) {
        console.error("Error in showTab:", e);
    }
}

export function getDistanceRangeBounds(startDist, endDist, margin = 15) {
    if (!state.trackData || !state.trackData.center_line || state.trackData.center_line.length === 0) {
        return null;
    }
    const points = state.trackData.center_line;
    const lapLength = state.trackData.lap_length || Infinity;

    const sDist = (startDist !== undefined && startDist !== null) ? parseFloat(startDist) : 0;
    const eDist = (endDist !== undefined && endDist !== null) ? parseFloat(endDist) : sDist;

    const minD = Math.min(sDist, eDist);
    const maxD = Math.max(sDist, eDist);

    const s = Math.max(0, minD - margin);
    const e = maxD + margin;

    const rangePoints = [];

    if (minD <= maxD) {
        points.forEach(p => {
            if (p.dist >= s && p.dist <= e) {
                rangePoints.push(p);
            }
        });
    } else {
        points.forEach(p => {
            if (p.dist >= s || p.dist <= (e % lapLength)) {
                rangePoints.push(p);
            }
        });
    }

    const ptStart = getPointAtDistance({ points }, sDist, ['lat', 'lng']);
    if (ptStart && ptStart.lat !== undefined && ptStart.lng !== undefined) {
        rangePoints.push(ptStart);
    }
    const ptEnd = getPointAtDistance({ points }, eDist, ['lat', 'lng']);
    if (ptEnd && ptEnd.lat !== undefined && ptEnd.lng !== undefined) {
        rangePoints.push(ptEnd);
    }

    if (rangePoints.length === 0) return null;

    if (typeof window !== 'undefined' && window.google && window.google.maps && google.maps.LatLngBounds) {
        const bounds = new google.maps.LatLngBounds();
        rangePoints.forEach(p => bounds.extend({ lat: p.lat, lng: p.lng }));
        return bounds;
    }

    let minLat = Infinity, maxLat = -Infinity, minLng = Infinity, maxLng = -Infinity;
    rangePoints.forEach(p => {
        if (p.lat < minLat) minLat = p.lat;
        if (p.lat > maxLat) maxLat = p.lat;
        if (p.lng < minLng) minLng = p.lng;
        if (p.lng > maxLng) maxLng = p.lng;
    });
    return {
        getNorthEast: () => ({ lat: () => maxLat, lng: () => maxLng }),
        getSouthWest: () => ({ lat: () => minLat, lng: () => minLng }),
        getCenter: () => ({ lat: () => (minLat + maxLat) / 2, lng: () => (minLng + maxLng) / 2 }),
        isEmpty: () => false
    };
}

export function getTurnBounds(turn) {
    if (!turn) return null;
    const startDist = (turn.start !== undefined && turn.start !== null) ? turn.start : 0;
    const endDist = (turn.end !== undefined && turn.end !== null) ? turn.end : startDist;
    return getDistanceRangeBounds(startDist, endDist, 15);
}

export function focusMapOnRange(startDist, endDist, padding = 50) {
    if (!state.map || !state.trackData) return;
    let s = startDist;
    let e = endDist;
    if (typeof s === 'string' && s.includes(',') && e === undefined) {
        const parts = s.split(',').map(v => parseFloat(v.trim()));
        s = parts[0];
        e = parts[1];
    } else if (Array.isArray(s) && s.length === 2 && e === undefined) {
        e = s[1];
        s = s[0];
    }
    const bounds = getDistanceRangeBounds(s, e, 15);

    let centerCoord = null;
    if (s !== undefined && e !== undefined && state.trackData.center_line) {
        const midDist = (parseFloat(s) + parseFloat(e)) / 2;
        const pt = getPointAtDistance({ points: state.trackData.center_line }, midDist, ['lat', 'lng']);
        if (pt && pt.lat !== undefined && pt.lng !== undefined) {
            centerCoord = { lat: pt.lat, lng: pt.lng };
        }
    }

    const apply = () => {
        if (!state.map) return;
        if (bounds && typeof state.map.fitBounds === 'function') {
            state.map.fitBounds(bounds, padding);
        } else if (centerCoord) {
            if (typeof state.map.setCenter === 'function') state.map.setCenter(centerCoord);
            if (typeof state.map.panTo === 'function') state.map.panTo(centerCoord);
        }
    };

    apply();
    if (typeof setTimeout === 'function') {
        setTimeout(apply, 50);
        setTimeout(apply, 150);
        setTimeout(apply, 300);
    }
}

export function setTelemetryRange(startDist, endDist) {
    let range = null;
    if (Array.isArray(startDist) && startDist.length === 2) {
        range = [parseFloat(startDist[0]), parseFloat(startDist[1])];
    } else if (startDist !== undefined && endDist !== undefined) {
        range = [parseFloat(startDist), parseFloat(endDist)];
    } else if (typeof startDist === 'string' && startDist.includes(',')) {
        const parts = startDist.split(',').map(s => parseFloat(s.trim()));
        if (parts.length === 2) range = parts;
    }

    if (range && !isNaN(range[0]) && !isNaN(range[1])) {
        state.globalTelemetryXRange = range;
        renderExpandablePlots();
        debouncedUpdateURL();
    }
}

export function focusMapOnTurn(turnIdx) {
    if (!state.map || !state.trackData || !state.trackData.turns) return;
    const turn = state.trackData.turns[turnIdx];
    if (!turn) return;

    const bounds = getTurnBounds(turn);

    let apexDist = null;
    if (turn.apex !== undefined && turn.apex !== null) {
        const apexes = Array.isArray(turn.apex) ? turn.apex : [turn.apex];
        if (apexes.length > 0 && !isNaN(apexes[0])) {
            apexDist = apexes.reduce((sum, val) => sum + val, 0) / apexes.length;
        }
    } else if (turn.apexes_m && Array.isArray(turn.apexes_m) && turn.apexes_m.length > 0) {
        apexDist = turn.apexes_m.reduce((sum, val) => sum + val, 0) / turn.apexes_m.length;
    } else if (turn.start !== undefined && turn.end !== undefined) {
        apexDist = (turn.start + turn.end) / 2;
    }

    let centerCoord = null;
    if (apexDist !== null && state.trackData.center_line) {
        const points = state.trackData.center_line;
        if (points && points.length > 0) {
            const pt = getPointAtDistance({ points: points }, apexDist, ['lat', 'lng']);
            if (pt && pt.lat !== undefined && pt.lng !== undefined) {
                centerCoord = { lat: pt.lat, lng: pt.lng };
            }
        }
    }

    const apply = () => {
        if (!state.map) return;
        if (bounds && typeof state.map.fitBounds === 'function') {
            state.map.fitBounds(bounds, 50);
        } else if (centerCoord) {
            if (typeof state.map.setCenter === 'function') state.map.setCenter(centerCoord);
            if (typeof state.map.panTo === 'function') state.map.panTo(centerCoord);
        }
    };

    apply();
    if (typeof setTimeout === 'function') {
        setTimeout(apply, 50);
        setTimeout(apply, 150);
        setTimeout(apply, 300);
    }
}

export function selectTurnAndSwitchToMap(turnIdx) {
    if (turnIdx === undefined || turnIdx === null || isNaN(turnIdx)) return;
    const turns = state.trackData && state.trackData.turns ? state.trackData.turns : [];
    if (turnIdx < 0 || turnIdx >= turns.length) return;

    state.currentTurnIdx = turnIdx;
    const turnSelector = document.getElementById('turn-selector');
    if (turnSelector) {
        turnSelector.value = turnIdx;
    }

    // Collect all valid laps in that turn across all sessions
    const turnLaps = [];
    if (state.allSessionsData) {
        state.allSessionsData.forEach(session => {
            if (!session.laps) return;
            session.laps.forEach(lap => {
                if (lap.is_valid === false) return;
                const t = getTurnTime(lap, turnIdx);
                if (t !== null && t !== undefined && t > 0.5 && !isNaN(t)) {
                    turnLaps.push({
                        lapId: `${session.session_id}-${lap.lap_num}`,
                        time: t
                    });
                }
            });
        });
    }

    turnLaps.sort((a, b) => a.time - b.time);

    const selectedIds = new Set();
    if (turnLaps.length > 0) {
        selectedIds.add(turnLaps[0].lapId); // Fastest lap in turn
        const medianIdx = Math.floor(turnLaps.length / 2);
        if (turnLaps[medianIdx]) {
            selectedIds.add(turnLaps[medianIdx].lapId); // Median lap in turn
        }
    }

    if (selectedIds.size > 0) {
        state.groupASelection = selectedIds;
        state.groupAVisibleMap = true;
        state.groupAVisibleStats = true;
    }

    state.deltaPlotVisible = true;
    updateExpandablePlotsVisibility();

    setTrajectoryColorMode('delta_t');

    state.sortMode = 'turn';
    showTab('map');
    setSort('turn', turnIdx);
}

export function setSort(mode, turnIdx = undefined) {
    state.sortMode = mode;
    document.querySelectorAll('.sort-btn').forEach(btn => btn.classList.remove('active'));
    const btn = document.getElementById('sort-' + mode);
    if (btn) btn.classList.add('active');
    
    const turnSelector = document.getElementById('turn-selector');
    if (turnSelector) {
        turnSelector.style.display = (mode === 'turn') ? 'block' : 'none';

        if (turnIdx !== undefined && turnIdx !== null && !isNaN(parseInt(turnIdx))) {
            state.currentTurnIdx = parseInt(turnIdx);
            turnSelector.value = state.currentTurnIdx;
        } else if (turnSelector.value !== "" && !isNaN(parseInt(turnSelector.value))) {
            state.currentTurnIdx = parseInt(turnSelector.value);
        } else if (state.currentTurnIdx !== undefined) {
            turnSelector.value = state.currentTurnIdx;
        } else {
            state.currentTurnIdx = 0;
            turnSelector.value = 0;
        }
        
        if (mode === 'turn' && state.trackData && state.trackData.turns) {
            const turns = state.trackData.turns;
            const turn = turns[state.currentTurnIdx];
            if (turn) {
                let rangeEnd = turn.end;
                // Include the straight after the turn until the next turn starts
                const nextTurn = turns[state.currentTurnIdx + 1];
                if (nextTurn) {
                    rangeEnd = nextTurn.start;
                } else if (state.trackData.lap_length) {
                    rangeEnd = state.trackData.lap_length;
                }
                const rangeStart = Math.max(0, turn.start - 20);
                state.globalTelemetryXRange = [rangeStart, rangeEnd];

                // Move cursor on bottom slider to the start of that turn
                const turnStart = (turn.start !== undefined && turn.start !== null) ? turn.start : 0;
                state.playbackDistance = turnStart;
                state.currentTargetDist = turnStart;
                const slider = document.getElementById('distance-slider');
                const display = document.getElementById('distance-display');
                if (slider) slider.value = turnStart;
                if (display) display.textContent = Math.round(turnStart) + 'm';
                import('./map.js').then(m => {
                    if (m && typeof m.updateDistanceMarker === 'function') {
                        m.updateDistanceMarker(turnStart);
                    }
                });

                if (state.map) {
                    focusMapOnTurn(state.currentTurnIdx);
                }
            }
        } else if (mode === 'time' && state.trackData) {
            state.globalTelemetryXRange = [0, state.trackData.lap_length];
        }
    }
    
    updateFastestSelectedLap();
    renderLapList();
    updateTelemetryPlots(state.currentTargetDist);
    renderExpandablePlots();
    renderStatsPlots();
    debouncedUpdateURL();
}

export function renderLapList() {
    const lapList = document.getElementById('lap-list');
    if (!lapList) return;
    updateSessionSelectorColors();
    lapList.innerHTML = '';

    let allLaps = [];
    state.allSessionsData.forEach(session => {
        if (!session.laps) return;
        session.laps.forEach(lap => {
            if (lap.is_valid === false) return;
            allLaps.push({
                ...lap,
                sessionId: session.session_id,
                sessionName: session.session_name,
                lapId: `${session.session_id}-${lap.lap_num}`
            });
        });
    });

    allLaps.sort(compareLaps);

    if (allLaps.length === 0) {
        lapList.innerHTML = '<div style="padding: 1rem; text-align: center; opacity: 0.5;">No valid laps found</div>';
        return;
    }

    allLaps.forEach(lap => {
        try {
            const lapId = lap.lapId;
            const isInA = state.groupASelection.has(lapId);
            const isInB = state.groupBSelection.has(lapId);
            const sessionColor = getSessionColor(lap.sessionId);

            const lapItem = document.createElement('div');
            lapItem.className = 'lap-item';
            lapItem.title = lap.sessionName || "";
            lapItem.onmouseenter = () => highlightLap(lapId, true);
            lapItem.onmouseleave = () => highlightLap(lapId, false);

            let timeLabel = lap.lap_time || "N/A";
            if (state.sortMode === 'turn') {
                const turnSelector = document.getElementById('turn-selector');
                const turnIdx = parseInt(turnSelector && turnSelector.value !== "" ? turnSelector.value : state.currentTurnIdx || 0);
                const t = getTurnTime(lap, turnIdx);
                timeLabel = t ? t.toFixed(3) + 's' : 'N/A';
            }

            lapItem.innerHTML = `
                    <div style="display: flex; gap: 4px; align-items: center;">
                        <input type="checkbox" id="chk-a-${lapId}" ${isInA ? 'checked' : ''}>
                        <input type="checkbox" id="chk-b-${lapId}" ${isInB ? 'checked' : ''}>
                    </div>
                    <label for="chk-a-${lapId}" class="${lap.is_valid ? 'lap-valid' : 'lap-invalid'}" style="flex: 1; margin-left: 0.25rem;">
                        <span style="color: ${sessionColor}; font-weight: 600;">${lap.is_outlap ? 'Outlap' : `Lap ${lap.lap_num}`}</span>
                        <span class="lap-time">${timeLabel}</span>
                    </label>
                `;
                
            const checkboxes = lapItem.querySelectorAll('input[type="checkbox"]');
            if (checkboxes.length >= 2) {
                checkboxes[0].onchange = () => toggleLap(lapId, 'A');
                checkboxes[1].onchange = () => toggleLap(lapId, 'B');
            }
            
            lapList.appendChild(lapItem);
        } catch (e) {
            console.error("Error rendering lap item:", e, lap);
        }
    });
    updateSelectAllCheckboxes();
}

export function updateSelectAllCheckboxes() {
    const lapIds = Object.keys(state.lapPolylines);
    if (lapIds.length === 0) return;

    ['A', 'B'].forEach(group => {
        const selection = group === 'A' ? state.groupASelection : state.groupBSelection;
        const master = document.getElementById(`chk-all-${group.toLowerCase()}`);
        if (master) {
            const checkedCount = selection.size;
            master.checked = checkedCount === lapIds.length && lapIds.length > 0;
            master.indeterminate = checkedCount > 0 && checkedCount < lapIds.length;
        }
    });
}

export function toggleLap(lapId, group) {
    const chk = document.getElementById(`chk-${group.toLowerCase()}-${lapId}`);
    const selection = group === 'A' ? state.groupASelection : state.groupBSelection;

    if (chk.checked) {
        selection.add(lapId);
    } else {
        selection.delete(lapId);
    }

    updateFastestSelectedLap();
    updateLapVisibility(lapId);
    updateSelectAllCheckboxes();
    updateTelemetryPlots(state.currentTargetDist);
    renderExpandablePlots();
    renderStatsPlots();
    debouncedUpdateURL();
}

export function highlightLap(lapId, active) {
    const isLapVisible = (state.groupASelection && state.groupASelection.has(lapId) && state.groupAVisibleMap) ||
                         (state.groupBSelection && state.groupBSelection.has(lapId) && state.groupBVisibleMap);
    
    const reallyActive = active && isLapVisible;

    // Map polyline highlighting and dimming
    const lapIds = Object.keys(state.lapPolylines);
    const refLap = getReferenceLap();
    const refLapId = state.fastestGroupALapId || (refLap && refLap.lapId ? refLap.lapId : (refLap && refLap.session_id && refLap.lap_num ? `${refLap.session_id}-${refLap.lap_num}` : null)) || state.fastestSelectedLapId;

    lapIds.forEach(id => {
        const polylines = state.lapPolylines[id];
        if (!polylines) return;

        const isCurrentLap = (id === lapId);
        const isRefLap = (id === refLapId);
        const defaultZIndex = (state.trajectoryColorMode === 'delta_t') ? (isRefLap ? 1 : 10) : 1;

        polylines.forEach(p => {
            const isHitArea = p.strokeOpacity === 0;
            if (!isHitArea) {
                if (reallyActive) {
                    if (isCurrentLap) {
                        p.setOptions({
                            strokeWeight: 8,
                            strokeOpacity: 1.0,
                            zIndex: 1000
                        });
                    } else {
                        p.setOptions({
                            strokeWeight: 2.5,
                            strokeOpacity: 0.25,
                            zIndex: 1
                        });
                    }
                } else {
                    // Reset to default style preserving zIndex hierarchy
                    const defaultOpacity = (state.trajectoryColorMode === 'time') ? 0.45 : 1.0;
                    const defaultWeight = (state.trajectoryColorMode === 'time') ? 2 : 4;
                    p.setOptions({
                        strokeWeight: defaultWeight,
                        strokeOpacity: defaultOpacity,
                        zIndex: defaultZIndex
                    });
                }
            }
        });
    });

    // Telemetry chart highlighting (violin plots in the right sidebar)
    const gd = document.getElementById('telemetry-chart');
    if (gd && gd._fullLayout && typeof Plotly !== 'undefined') {
        // Reset selection on all curves first
        Plotly.restyle(gd, { selectedpoints: [null] });

        if (reallyActive) {
            const speedPoints = state.lapToPlotIndices.speed[lapId] || [];
            const brakingPoints = state.lapToPlotIndices.braking[lapId] || [];
            const hoverPoints = [...speedPoints, ...brakingPoints];

            if (hoverPoints.length > 0) {
                const curvePoints = {};
                hoverPoints.forEach(p => {
                    if (!curvePoints[p.curveNumber]) {
                        curvePoints[p.curveNumber] = [];
                    }
                    curvePoints[p.curveNumber].push(p.pointNumber);
                });

                const curvesToUpdate = Object.keys(curvePoints).map(Number);
                const selectedpointsArrays = curvesToUpdate.map(c => curvePoints[c]);

                Plotly.restyle(gd, { selectedpoints: selectedpointsArrays }, curvesToUpdate);
            }
        }
    }

    // Bottom bar line plots highlighting
    const activeLapId = reallyActive ? lapId : null;
    if (typeof Plotly !== 'undefined' && state.bottomPlotIndices) {
        const bottomPlots = [];
        if (state.deltaPlotVisible) bottomPlots.push('plot-area-delta');
        if (state.speedPlotVisible) bottomPlots.push('plot-area-speed');
        if (state.activePlotChannels) {
            state.activePlotChannels.forEach(tab => {
                bottomPlots.push('plot-area-' + tab);
            });
        }

        bottomPlots.forEach(plotId => {
            highlightBottomPlotTraces(plotId, activeLapId);
        });
    }
}

function highlightBottomPlotTraces(plotId, activeLapId) {
    const gd = document.getElementById(plotId);
    if (!gd || !gd._fullLayout || !gd.data) return;

    const indicesMap = state.bottomPlotIndices[plotId] || {};
    const targetIndices = indicesMap[activeLapId] || [];

    const numTraces = gd.data.length;
    const opacities = [];
    const widths = [];
    const colors = [];

    for (let i = 0; i < numTraces; i++) {
        const trace = gd.data[i];
        if (trace.originalColor === undefined && trace.line && trace.line.color) {
            trace.originalColor = trace.line.color;
        }
        if (trace.originalWidth === undefined && trace.line && trace.line.width) {
            trace.originalWidth = trace.line.width;
        }

        const isActiveTrace = targetIndices.includes(i);
        const isGroupB = (trace.line && trace.line.dash === 'dash') || (trace.name && trace.name.includes('(B)'));

        if (activeLapId === null) {
            opacities.push(1.0);
            widths.push(trace.originalWidth !== undefined ? trace.originalWidth : (isGroupB ? 1.5 : 2.0));
            colors.push(trace.originalColor || (trace.line && trace.line.color));
        } else if (isActiveTrace) {
            opacities.push(1.0);
            widths.push(isGroupB ? 3.0 : 3.25);
            colors.push(getBrightColor(trace.originalColor || (trace.line && trace.line.color)));
        } else {
            opacities.push(0.45);
            widths.push(isGroupB ? 1.2 : 1.5);
            colors.push(trace.originalColor || (trace.line && trace.line.color));
        }
    }

    Plotly.restyle(gd, {
        'opacity': opacities,
        'line.width': widths,
        'line.color': colors
    });
}

function getBrightColor(color) {
    if (typeof color !== 'string') return color;
    if (color.startsWith('rgba')) {
        return color.replace(/rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*[0-9.]+\s*\)/, 'rgba($1,$2,$3,1.0)');
    }
    return color;
}

export function getSortedLaps() {
    let allLaps = [];
    state.allSessionsData.forEach(session => {
        if (!session.laps) return;
        session.laps.forEach(lap => {
            if (lap.is_valid === false) return;
            allLaps.push({
                ...lap,
                sessionId: session.session_id,
                sessionName: session.session_name,
                lapId: `${session.session_id}-${lap.lap_num}`
            });
        });
    });

    allLaps.sort(compareLaps);
    return allLaps;
}

export function clearSelection(group) {
    const selection = group === 'A' ? state.groupASelection : state.groupBSelection;
    selection.clear();
    
    updateFastestSelectedLap();
    Object.keys(state.lapPolylines).forEach(lapId => {
        updateLapVisibility(lapId);
    });
    
    document.querySelectorAll(`#lap-list input`).forEach(chk => {
        if (chk.id.startsWith(`chk-${group.toLowerCase()}-`)) {
            chk.checked = false;
        }
    });
    
    updateTelemetryPlots(state.currentTargetDist);
    renderExpandablePlots();
    renderStatsPlots();
    updateSelectAllCheckboxes();
    debouncedUpdateURL();
}

export function selectLapsByCriteria(group, type, value) {
    const selection = group === 'A' ? state.groupASelection : state.groupBSelection;
    const sortedLaps = getSortedLaps();
    const totalCount = sortedLaps.length;

    selection.clear();

    let countToSelect = 0;
    if (type === 'count') {
        countToSelect = Math.min(value, totalCount);
    } else if (type === 'percent') {
        countToSelect = Math.min(Math.ceil(totalCount * value), totalCount);
    } else if (type === 'all') {
        countToSelect = totalCount;
    } else if (type === 'clear') {
        countToSelect = 0;
    }

    for (let i = 0; i < countToSelect; i++) {
        selection.add(sortedLaps[i].lapId);
    }

    updateFastestSelectedLap();
    Object.keys(state.lapPolylines).forEach(lapId => {
        updateLapVisibility(lapId);
    });

    document.querySelectorAll(`#lap-list input`).forEach(chk => {
        if (chk.id.startsWith(`chk-${group.toLowerCase()}-`)) {
            const lapId = chk.id.replace(`chk-${group.toLowerCase()}-`, '');
            chk.checked = selection.has(lapId);
        }
    });

    updateTelemetryPlots(state.currentTargetDist);
    renderExpandablePlots();
    renderStatsPlots();
    updateSelectAllCheckboxes();
    debouncedUpdateURL();
}

export function showSelectionMenu(e, group) {
    const existing = document.getElementById('lap-selection-menu');
    if (existing) existing.remove();

    const menu = document.createElement('div');
    menu.id = 'lap-selection-menu';
    menu.className = `lap-selection-menu group-${group.toLowerCase()}`;
    
    const options = [
        { label: 'Top 1', value: 1, type: 'count' },
        { label: 'Top 3', value: 3, type: 'count' },
        { label: 'Top 5', value: 5, type: 'count' },
        { label: 'Top 10', value: 10, type: 'count' },
        { label: 'Top 50%', value: 0.50, type: 'percent' },
        { label: 'Top 75%', value: 0.75, type: 'percent' },
        { label: 'Top 90%', value: 0.90, type: 'percent' },
        { label: 'All Laps', value: 1, type: 'all' },
        { label: 'Clear Selection', value: 0, type: 'clear' }
    ];

    options.forEach(opt => {
        const item = document.createElement('div');
        item.className = 'menu-item';
        item.textContent = opt.label;
        
        item.onclick = () => {
            selectLapsByCriteria(group, opt.type, opt.value);
            menu.remove();
        };
        
        menu.appendChild(item);
    });

    document.body.appendChild(menu);

    const rect = e.target.getBoundingClientRect();
    menu.style.left = `${window.scrollX + rect.left}px`;
    menu.style.top = `${window.scrollY + rect.bottom + 6}px`;

    const outsideClickListener = (event) => {
        if (!menu.contains(event.target) && event.target !== e.target) {
            menu.remove();
            document.removeEventListener('click', outsideClickListener);
        }
    };
    
    setTimeout(() => {
        document.addEventListener('click', outsideClickListener);
    }, 10);
}

export function initAllLapsHandlers() {
    ['A', 'B'].forEach(group => {
        const chk = document.getElementById(`chk-all-${group.toLowerCase()}`);
        if (!chk) return;
        
        chk.addEventListener('click', (e) => {
            e.preventDefault();
            
            const selection = group === 'A' ? state.groupASelection : state.groupBSelection;
            const lapIds = Object.keys(state.lapPolylines);
            
            if (lapIds.length === 0) return;
            
            // If all laps are currently selected, clicking clears selection
            if (selection.size === lapIds.length) {
                clearSelection(group);
            } else {
                // Otherwise, show the selection menu
                showSelectionMenu(e, group);
            }
        });
    });
}

export function toggleAllLaps(group) {
    const selection = group === 'A' ? state.groupASelection : state.groupBSelection;
    const lapIds = Object.keys(state.lapPolylines);
    if (selection.size === lapIds.length) {
        clearSelection(group);
    } else {
        selectLapsByCriteria(group, 'all', 1);
    }
}


export function updateLapVisibility(lapId) {
    const isVisible = (state.groupASelection.has(lapId) && state.groupAVisibleMap) || (state.groupBSelection.has(lapId) && state.groupBVisibleMap);
    if (state.lapPolylines[lapId]) {
        const refLap = getReferenceLap();
        const refLapId = state.fastestGroupALapId || (refLap && refLap.lapId ? refLap.lapId : (refLap && refLap.session_id && refLap.lap_num ? `${refLap.session_id}-${refLap.lap_num}` : null)) || state.fastestSelectedLapId;
        const isRefLap = (lapId === refLapId);
        const zIndex = (state.trajectoryColorMode === 'delta_t') ? (isRefLap ? 1 : 10) : 1;

        state.lapPolylines[lapId].forEach(p => {
            const isHitArea = p.strokeOpacity === 0;
            if (!isHitArea && typeof p.setOptions === 'function') {
                p.setOptions({ zIndex: zIndex });
            }
            p.setMap(isVisible ? state.map : null);
        });
    }
}

export function toggleGroupVisibility(group) {
    if (group === 'A') state.groupAVisible = !state.groupAVisible;
    else state.groupBVisible = !state.groupBVisible;

    updateVisibilityIcons();

    if (state.activeTab === 'map') {
        updateFastestSelectedLap();
        Object.keys(state.lapPolylines).forEach(lapId => updateLapVisibility(lapId));
        renderExpandablePlots();
    } else if (state.activeTab === 'stats') {
        renderStatsPlots();
    }

    debouncedUpdateURL();
}

export function updateVisibilityIcons() {
    const btnA = document.getElementById('btn-vis-a');
    const btnB = document.getElementById('btn-vis-b');

    const eyeEnabled = '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>';
    const eyeDisabled = '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/>';

    if (btnA) {
        btnA.querySelector('svg').innerHTML = state.groupAVisible ? eyeEnabled : eyeDisabled;
        btnA.style.opacity = state.groupAVisible ? '1' : '0.4';
    }

    if (btnB) {
        btnB.querySelector('svg').innerHTML = state.groupBVisible ? eyeEnabled : eyeDisabled;
        btnB.style.opacity = state.groupBVisible ? '1' : '0.4';
    }
}

export function updateFastestSelectedLap() {
    let fastest = null;
    let fastestId = null;
    let minTime = Infinity;

    let fastestA = null;
    let fastestIdA = null;
    let minTimeA = Infinity;

    const selectedIds = new Set([...state.groupASelection, ...state.groupBSelection]);
    const turnSelector = document.getElementById('turn-selector');
    const turnIdx = parseInt(turnSelector && turnSelector.value !== "" ? turnSelector.value : state.currentTurnIdx || 0);

    selectedIds.forEach(id => {
        const lap = state.lapDataLookup ? state.lapDataLookup[id] : null;
        if (lap) {
            let t;
            if (state.sortMode === 'turn') {
                t = getTurnTime(lap, turnIdx);
            } else {
                t = parseLapTime(lap.lap_time);
            }
            
            if (t !== null && !isNaN(t)) {
                if (t < minTime) {
                    minTime = t;
                    fastest = lap;
                    fastestId = id;
                }
                if (state.groupASelection.has(id) && t < minTimeA) {
                    minTimeA = t;
                    fastestA = lap;
                    fastestIdA = id;
                }
            }
        }
    });

    // Pick the first lap in Group A based on the current sorting criteria
    let firstA = null;
    let firstIdA = null;
    if (typeof getSortedLaps === 'function' && state.allSessionsData && state.allSessionsData.length > 0) {
        const sorted = getSortedLaps();
        for (const l of sorted) {
            if (state.groupASelection && state.groupASelection.has(l.lapId)) {
                firstIdA = l.lapId;
                firstA = state.lapDataLookup ? (state.lapDataLookup[l.lapId] || l) : l;
                break;
            }
        }
    }

    state.fastestGroupALap = firstA || fastestA;
    state.fastestGroupALapId = firstIdA || fastestIdA;
    state.fastestSelectedLap = fastest;
    state.fastestSelectedLapId = fastestId;

    if (state.trajectoryColorMode === 'delta_t' || state.trajectoryColorMode === 'time') {
        updateAllPolylineColors();
    }
}

export function togglePlay() {
    state.isPlaying = !state.isPlaying;
    const playIcon = document.getElementById('play-icon');
    const pauseIcon = document.getElementById('pause-icon');
    const btn = document.getElementById('play-pause-btn');

    if (state.isPlaying) {
        playIcon.style.display = 'none';
        pauseIcon.style.display = 'block';
        btn.classList.add('playing');
        
        const slider = document.getElementById('distance-slider');
        if (slider) state.playbackDistance = parseFloat(slider.value);
        
        state.lastTimestamp = null;
        if (state.animationFrameId) cancelAnimationFrame(state.animationFrameId);
        state.animationFrameId = requestAnimationFrame(animateSlider);
    } else {
        playIcon.style.display = 'block';
        pauseIcon.style.display = 'none';
        btn.classList.remove('playing');
        if (state.animationFrameId) {
            cancelAnimationFrame(state.animationFrameId);
            state.animationFrameId = null;
        }
        state.lastTimestamp = null;
    }
}

export function setPlaybackSpeed(speed) {
    state.playbackSpeed = speed;
    document.querySelectorAll('.speed-option').forEach(opt => {
        opt.classList.remove('active');
        if (parseFloat(opt.textContent) === speed) {
            opt.classList.add('active');
        }
    });
    document.getElementById('speed-popup').style.display = 'none';

    const btn = document.getElementById('play-pause-btn');
    btn.title = `Play/Pause (Current speed: ${speed}x)`;
}

export function toggleDeltaPlot() {
    state.deltaPlotVisible = !state.deltaPlotVisible;
    updateExpandablePlotsVisibility();
    debouncedUpdateURL();
}

export function toggleSpeedPlot() {
    state.speedPlotVisible = !state.speedPlotVisible;
    updateExpandablePlotsVisibility();
    debouncedUpdateURL();
}

export function handleReportTriggerAction(data) {
    if (!data) return;

    // 1. Distance seek
    if (data.dist !== undefined && data.dist !== null && data.dist !== '') {
        const dist = parseFloat(data.dist);
        if (!isNaN(dist)) {
            state.playbackDistance = dist;
            const slider = document.getElementById('distance-slider');
            const display = document.getElementById('distance-display');
            if (slider) slider.value = dist;
            if (display) display.textContent = Math.round(dist) + 'm';
            updateDistanceMarker(dist);
        }
    }

    // 2. Telemetry X range (zoom / visible range in bottom plot)
    const rangeData = data.xlim || data.range || data.plotRange;
    if (rangeData) {
        setTelemetryRange(rangeData);
    }

    // Map centering / focus over track segment (meters)
    const mapRangeData = data.mapRange || data.mapFocus || data.centerMap || data.focusMap;
    if (mapRangeData) {
        focusMapOnRange(mapRangeData);
    }

    // Focus both plot range AND map centering
    const focusRangeData = data.focusRange || data.zoomRange;
    if (focusRangeData) {
        setTelemetryRange(focusRangeData);
        focusMapOnRange(focusRangeData);
    }

    // 3. Turn select
    if (data.turn !== undefined && data.turn !== null && data.turn !== '') {
        const turnIdx = parseInt(data.turn, 10);
        if (!isNaN(turnIdx)) {
            selectTurnAndSwitchToMap(turnIdx);
        }
    }

    // 4. Lap selection
    if (data.laps !== undefined && data.lapsA === undefined && data.lapsB === undefined) {
        selectLaps(data.laps, 'none');
    } else if (data.lapsA !== undefined || data.lapsB !== undefined) {
        selectLaps(data.lapsA, data.lapsB);
    }

    // 5. Toggle single plot
    if (data.togglePlot) {
        togglePlot(data.togglePlot);
    }

    // 6. Set visible plots
    if (data.plots !== undefined || data.plot !== undefined) {
        setVisiblePlots(data.plots !== undefined ? data.plots : data.plot);
    }

    // 7. Switch tab (map/stats)
    if (data.tab) {
        showTab(data.tab);
    }

    // 8. Switch right tab
    if (data.rtab) {
        showRightPanelTab(data.rtab);
    }

    debouncedUpdateURL();
}

export function selectLaps(lapsA, lapsB) {
    let changed = false;

    if (lapsA !== undefined) {
        state.groupASelection.clear();
        if (lapsA && lapsA !== 'none') {
            const arr = Array.isArray(lapsA) ? lapsA : String(lapsA).split(',');
            const requested = new Set(arr.map(s => String(s).trim()).filter(Boolean));
            if (state.allSessionsData && state.allSessionsData.length > 0) {
                state.allSessionsData.forEach((session, sIdx) => {
                    if (!session.laps) return;
                    session.laps.forEach(lap => {
                        const lapId = `${session.session_id}-${lap.lap_num}`;
                        if (matchesRequestedLap(requested, lapId, session, lap, sIdx)) {
                            state.groupASelection.add(lapId);
                        }
                    });
                });
            } else {
                requested.forEach(id => state.groupASelection.add(id));
            }
        }
        changed = true;
    }

    if (lapsB !== undefined) {
        state.groupBSelection.clear();
        if (lapsB && lapsB !== 'none') {
            const arr = Array.isArray(lapsB) ? lapsB : String(lapsB).split(',');
            const requested = new Set(arr.map(s => String(s).trim()).filter(Boolean));
            if (state.allSessionsData && state.allSessionsData.length > 0) {
                state.allSessionsData.forEach((session, sIdx) => {
                    if (!session.laps) return;
                    session.laps.forEach(lap => {
                        const lapId = `${session.session_id}-${lap.lap_num}`;
                        if (matchesRequestedLap(requested, lapId, session, lap, sIdx)) {
                            state.groupBSelection.add(lapId);
                        }
                    });
                });
            } else {
                requested.forEach(id => state.groupBSelection.add(id));
            }
        }
        changed = true;
    }

    if (changed) {
        updateFastestSelectedLap();
        if (state.lapPolylines) {
            Object.keys(state.lapPolylines).forEach(lapId => updateLapVisibility(lapId));
        }
        updateAllPolylineColors();
        renderLapList();
        renderStatsPlots();
        renderExpandablePlots();
        showRightPanelTab(state.activeRightTab || 'report');
        debouncedUpdateURL();
    }
}

export function initReportInteractions() {
    if (typeof window !== 'undefined' && window.addEventListener) {
        window.addEventListener('message', (event) => {
            if (!event.data || event.data.type !== 'telemetry_jump') return;
            handleReportTriggerAction(event.data);
        });
    }

    if (typeof document !== 'undefined' && document.getElementById) {
        const reportContainer = document.getElementById('report-chart-container');
        if (reportContainer) {
            reportContainer.addEventListener('click', (e) => {
                const trigger = e.target.closest(
                    '[data-dist], [data-xlim], [data-range], [data-plot-range], [data-map-range], ' +
                    '[data-map-focus], [data-center-map], [data-focus-map], [data-focus-range], [data-zoom-range], ' +
                    '[data-turn], [data-laps], [data-laps-a], [data-laps-b], [data-tab], [data-rtab], ' +
                    '[data-toggle-plot], [data-plots], [data-plot]'
                );
                if (!trigger) return;
                handleReportTriggerAction({
                    dist: trigger.dataset.dist,
                    xlim: trigger.dataset.xlim || trigger.dataset.range || trigger.dataset.plotRange,
                    mapRange: trigger.dataset.mapRange || trigger.dataset.mapFocus || trigger.dataset.centerMap || trigger.dataset.focusMap,
                    focusRange: trigger.dataset.focusRange || trigger.dataset.zoomRange,
                    turn: trigger.dataset.turn,
                    laps: trigger.dataset.laps,
                    lapsA: trigger.dataset.lapsA,
                    lapsB: trigger.dataset.lapsB,
                    tab: trigger.dataset.tab,
                    rtab: trigger.dataset.rtab,
                    togglePlot: trigger.dataset.togglePlot,
                    plots: trigger.dataset.plots || trigger.dataset.plot
                });
            });
        }
    }
}

export function resetReportView(e) {
    if (e && typeof e.stopPropagation === 'function') {
        e.stopPropagation();
    }
    const reportState = (typeof window !== 'undefined' && window.KART_CONFIG && window.KART_CONFIG.reportState) || {};

    // 1. Clean URL query parameters
    if (typeof window !== 'undefined' && window.history && window.history.replaceState) {
        window.history.replaceState(null, '', window.location.pathname);
    }

    // 2. Reset tab
    const targetTab = reportState.tab || 'map';
    showTab(targetTab);

    // 3. Reset right tab
    const targetRightTab = reportState.rtab || 'report';
    showRightPanelTab(targetRightTab);

    // 4. Reset side panel width and state
    if (reportState.sidePanelWidth) {
        state.sidePanelWidth = reportState.sidePanelWidth;
        const sidePanel = document.getElementById('map-side-panel');
        if (sidePanel) {
            sidePanel.style.width = `${state.sidePanelWidth}px`;
        }
    }
    expandSidePanel();

    // 5. Reset bottom plots (speed, delta, channels)
    state.deltaPlotVisible = Boolean(reportState.delta);
    state.speedPlotVisible = Boolean(reportState.speed);
    updateExpandablePlotsVisibility();

    // Reset open channel plots
    document.querySelectorAll('.plot-tab').forEach(tab => {
        const plotType = tab.getAttribute('data-tab');
        const container = document.getElementById(`plot-row-${plotType}`);
        let shouldBeOpen = false;
        if (reportState.plot) {
            const plotList = Array.isArray(reportState.plot) ? reportState.plot : String(reportState.plot).split(',');
            shouldBeOpen = plotList.map(p => p.trim()).includes(plotType);
        }
        if (container) {
            if (shouldBeOpen) {
                container.style.display = 'block';
                tab.classList.add('active');
            } else {
                container.style.display = 'none';
                tab.classList.remove('active');
            }
        }
    });

    // 6. Reset trajectory color mode & slip angle mode
    if (reportState.tcol) {
        setTrajectoryColorMode(reportState.tcol);
    }
    if (reportState.sacol) {
        state.slipAngleColorMode = reportState.sacol;
        const colorModeSelector = document.getElementById('slip_angle-color-mode');
        if (colorModeSelector) colorModeSelector.value = reportState.sacol;
    }

    // 7. Reset lap selections
    state.groupASelection.clear();
    state.groupBSelection.clear();
    const arrA = reportState.lapsA ? (Array.isArray(reportState.lapsA) ? reportState.lapsA : [reportState.lapsA]) : [];
    const arrB = reportState.lapsB ? (Array.isArray(reportState.lapsB) ? reportState.lapsB : [reportState.lapsB]) : [];
    const customLapsA = new Set(arrA.map(id => String(id).trim()));
    const customLapsB = new Set(arrB.map(id => String(id).trim()));

    if (state.allSessionsData) {
        state.allSessionsData.forEach((session, sIdx) => {
            if (!session.laps) return;
            session.laps.forEach(lap => {
                const lapId = `${session.session_id}-${lap.lap_num}`;
                if (matchesRequestedLap(customLapsA, lapId, session, lap, sIdx)) {
                    state.groupASelection.add(lapId);
                }
                if (matchesRequestedLap(customLapsB, lapId, session, lap, sIdx)) {
                    state.groupBSelection.add(lapId);
                }
            });
        });
    }

    // 8. Reset sort mode and turn
    const targetSort = reportState.sort || 'turn';
    if (reportState.turn !== undefined) {
        state.currentTurnIdx = parseInt(reportState.turn);
    }
    setSort(targetSort, state.currentTurnIdx);

    // 9. Reset xlim
    if (reportState.xlim && Array.isArray(reportState.xlim) && reportState.xlim.length === 2) {
        state.globalTelemetryXRange = [parseFloat(reportState.xlim[0]), parseFloat(reportState.xlim[1])];
    }

    // 10. Reset distance cursor
    const targetDist = reportState.dist !== undefined ? parseFloat(reportState.dist) : 0;
    state.playbackDistance = targetDist;
    state.currentTargetDist = targetDist;
    const slider = document.getElementById('distance-slider');
    const display = document.getElementById('distance-display');
    if (slider) slider.value = targetDist;
    if (display) display.textContent = Math.round(targetDist) + 'm';
    updateDistanceMarker(targetDist);

    // 11. Reset map zoom / center if in reportState or focus on turn
    if (reportState.map && state.map) {
        if (Array.isArray(reportState.map) && reportState.map.length === 3) {
            state.map.setZoom(parseInt(reportState.map[0]));
            state.map.setCenter({ lat: parseFloat(reportState.map[1]), lng: parseFloat(reportState.map[2]) });
        }
    } else if (state.sortMode === 'turn') {
        focusMapOnTurn(state.currentTurnIdx || 0);
    }

    // 12. Re-render UI components
    if (state.lapPolylines) {
        Object.keys(state.lapPolylines).forEach(lapId => updateLapVisibility(lapId));
    }
    updateAllPolylineColors();
    renderLapList();
    renderStatsPlots();
    renderExpandablePlots();
    showRightPanelTab(targetRightTab);
}

if (typeof window !== 'undefined') {
    window.resetReportView = resetReportView;
    window.selectLaps = selectLaps;
    window.togglePlot = togglePlot;
    window.setVisiblePlots = setVisiblePlots;
    window.setTelemetryRange = setTelemetryRange;
    window.focusMapOnRange = focusMapOnRange;
    window.getDistanceRangeBounds = getDistanceRangeBounds;
}





