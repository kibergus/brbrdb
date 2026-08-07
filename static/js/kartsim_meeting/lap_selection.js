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
import { updateTelemetryPlots, updateAccelerationPlot, updateSlipAnglePlot, renderExpandablePlots, updateExpandablePlotsVisibility } from './plots_sync.js';
import { renderStatsPlots } from './stats_plots.js';
import { initMap, loadTrackPoints } from './map.js';
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

export function onSessionChange(sessionId) {
    state.selectedSessionId = sessionId;
    state.mapInitialized = false;
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

            // Update group visibility icons for the active tab
            updateVisibilityIcons();

            if (tabId === 'map') {
                // Update map polylines for the Map tab
                Object.keys(state.lapPolylines).forEach(lapId => updateLapVisibility(lapId));
                // Refresh bottom plots to respect Map tab's visibility settings
                renderExpandablePlots();
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
                            if (gd) Plotly.Plots.resize(gd);
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

export function setSort(mode) {
    state.sortMode = mode;
    document.querySelectorAll('.sort-btn').forEach(btn => btn.classList.remove('active'));
    const btn = document.getElementById('sort-' + mode);
    if (btn) btn.classList.add('active');
    
    const turnSelector = document.getElementById('turn-selector');
    if (turnSelector) {
        state.currentTurnIdx = parseInt(turnSelector.value || 0);
        turnSelector.style.display = (mode === 'turn') ? 'block' : 'none';
        
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

                if (turn.apex && turn.apex.length > 0) {
                    const avgApexDist = turn.apex.reduce((sum, val) => sum + val, 0) / turn.apex.length;
                    const points = state.trackData.center_line;
                    if (points && points.length > 0) {
                        const pt = getPointAtDistance({ points: points }, avgApexDist, ['lat', 'lng']);
                        if (pt && pt.lat !== undefined && pt.lng !== undefined && state.map) {
                            state.map.panTo({ lat: pt.lat, lng: pt.lng });
                        }
                    }
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

    allLaps.sort((a, b) => {
        try {
            if (state.sortMode === 'time') {
                return parseLapTime(a.lap_time) - parseLapTime(b.lap_time);
            } else if (state.sortMode === 'turn') {
                const turnSelector = document.getElementById('turn-selector');
                const turnIdx = parseInt(turnSelector && turnSelector.value !== "" ? turnSelector.value : state.currentTurnIdx || 0);
                return (getTurnTime(a, turnIdx) || 999999) - (getTurnTime(b, turnIdx) || 999999);
            }
            if (a.lap_num !== b.lap_num) return a.lap_num - b.lap_num;
            return (a.sessionName || "").localeCompare(b.sessionName || "");
        } catch (e) {
            console.error("Sort error:", e);
            return 0;
        }
    });

    if (allLaps.length === 0) {
        lapList.innerHTML = '<div style="padding: 1rem; text-align: center; opacity: 0.5;">No valid laps found</div>';
        return;
    }

    allLaps.forEach(lap => {
        try {
            const lapId = lap.lapId;
            const isInA = state.groupASelection.has(lapId);
            const isInB = state.groupBSelection.has(lapId);

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
                        <span>Lap ${lap.lap_num}</span>
                        <span class="lap-time">${timeLabel}</span>
                    </label>
                    <div class="lap-indicator" id="ind-${lapId}" style="width: 8px; height: 8px; border-radius: 50%; border: 1px solid var(--border-color); background: ${state.baseColor};"></div>
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

    updateLapVisibility(lapId);
    updateSelectAllCheckboxes();
    updateFastestSelectedLap();
    updateTelemetryPlots(state.currentTargetDist);
    renderExpandablePlots();
    renderStatsPlots();
    debouncedUpdateURL();
}

export function highlightLap(lapId, active) {
    const isLapVisible = (state.groupASelection && state.groupASelection.has(lapId) && state.groupAVisibleMap) ||
                         (state.groupBSelection && state.groupBSelection.has(lapId) && state.groupBVisibleMap);
    
    const reallyActive = active && isLapVisible;

    const indicator = document.getElementById('ind-' + lapId);
    if (indicator) {
        indicator.style.background = reallyActive ? state.highlightColor : state.baseColor;
        indicator.style.transform = reallyActive ? 'scale(1.5)' : 'scale(1)';
        indicator.style.transition = 'all 0.2s';
    }

    // Map polyline highlighting and dimming
    const lapIds = Object.keys(state.lapPolylines);
    lapIds.forEach(id => {
        const polylines = state.lapPolylines[id];
        if (!polylines) return;

        const isCurrentLap = (id === lapId);
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
                    // Reset to default style
                    p.setOptions({
                        strokeWeight: 4,
                        strokeOpacity: 1.0,
                        zIndex: 1
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

        const isActiveTrace = targetIndices.includes(i);
        const isGroupB = trace.line && trace.line.dash === 'dash';

        if (activeLapId === null) {
            opacities.push(1.0);
            widths.push(isGroupB ? 1.5 : 2.0);
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

    allLaps.sort((a, b) => {
        try {
            if (state.sortMode === 'time') {
                return parseLapTime(a.lap_time) - parseLapTime(b.lap_time);
            } else if (state.sortMode === 'turn') {
                const turnSelector = document.getElementById('turn-selector');
                const turnIdx = parseInt(turnSelector ? turnSelector.value : 0);
                return (getTurnTime(a, turnIdx) || 999999) - (getTurnTime(b, turnIdx) || 999999);
            }
            if (a.lap_num !== b.lap_num) return a.lap_num - b.lap_num;
            return (a.sessionName || "").localeCompare(b.sessionName || "");
        } catch (e) {
            console.error("Sort error:", e);
            return 0;
        }
    });
    return allLaps;
}

export function clearSelection(group) {
    const selection = group === 'A' ? state.groupASelection : state.groupBSelection;
    selection.clear();
    
    Object.keys(state.lapPolylines).forEach(lapId => {
        updateLapVisibility(lapId);
    });
    
    document.querySelectorAll(`#lap-list input`).forEach(chk => {
        if (chk.id.startsWith(`chk-${group.toLowerCase()}-`)) {
            chk.checked = false;
        }
    });
    
    updateFastestSelectedLap();
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

    Object.keys(state.lapPolylines).forEach(lapId => {
        updateLapVisibility(lapId);
    });

    document.querySelectorAll(`#lap-list input`).forEach(chk => {
        if (chk.id.startsWith(`chk-${group.toLowerCase()}-`)) {
            const lapId = chk.id.replace(`chk-${group.toLowerCase()}-`, '');
            chk.checked = selection.has(lapId);
        }
    });

    updateFastestSelectedLap();
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
        state.lapPolylines[lapId].forEach(p => p.setMap(isVisible ? state.map : null));
    }
}

export function toggleGroupVisibility(group) {
    if (group === 'A') state.groupAVisible = !state.groupAVisible;
    else state.groupBVisible = !state.groupBVisible;

    updateVisibilityIcons();

    if (state.activeTab === 'map') {
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
        const lap = state.lapDataLookup[id];
        if (lap) {
            let t;
            if (state.sortMode === 'turn') {
                t = getTurnTime(lap, turnIdx);
            } else {
                t = parseLapTime(lap.lap_time);
            }
            
            if (t !== null) {
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
    state.fastestSelectedLap = fastest;
    state.fastestSelectedLapId = fastestId;
    state.fastestGroupALap = fastestA;
    state.fastestGroupALapId = fastestIdA;
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


