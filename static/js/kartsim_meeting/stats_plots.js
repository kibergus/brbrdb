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

import { state } from './state.js';
import { formatLapTime, parseLapTime } from '../utils.js';
import { getSpeedAtDistance, getMinSpeedInRange } from './telemetry.js';

let currentHighlightTurnName = null;


/**
 * Main entry point to refresh all stats plots based on current selection and visibility.
 */
export function renderStatsPlots() {
    const selectedLaps = [];
    
    // Collect selected laps from Group A if visible
    if (state.groupAVisible) {
        state.groupASelection.forEach(lapId => {
            const lap = state.lapDataLookup[lapId];
            if (lap) {
                // Find session name for context
                const sessionId = lapId.split('-')[0];
                const session = state.allSessionsData.find(s => s.session_id == sessionId);
                selectedLaps.push({ 
                    ...lap, 
                    group: 'A', 
                    session_name: session ? session.session_name : '',
                    lapId: lapId
                });
            }
        });
    }
    
    // Collect selected laps from Group B if visible
    if (state.groupBVisible) {
        state.groupBSelection.forEach(lapId => {
            const lap = state.lapDataLookup[lapId];
            if (lap) {
                const sessionId = lapId.split('-')[0];
                const session = state.allSessionsData.find(s => s.session_id == sessionId);
                selectedLaps.push({ 
                    ...lap, 
                    group: 'B', 
                    session_name: session ? session.session_name : '',
                    lapId: lapId
                });
            }
        });
    }

    const container = document.getElementById('stats-plots-container');
    const noData = document.getElementById('stats-no-data');
    const idealDisplay = document.getElementById('ideal-time-display');

    if (selectedLaps.length === 0) {
        if (container) container.style.display = 'none';
        if (noData) noData.style.display = 'block';
        if (idealDisplay) idealDisplay.style.display = 'none';
        return;
    }

    if (container) container.style.display = 'flex';
    if (noData) noData.style.display = 'none';

    const groupALaps = selectedLaps.filter(l => l.group === 'A');
    currentHighlightTurnName = null;
    if (state.trackData && state.trackData.turns && groupALaps.length > 0) {
        let maxDiff = -1;
        state.trackData.turns.forEach((turn, turnIdx) => {
            const times = groupALaps
                .map(lap => (lap.turn_times && lap.turn_times[turnIdx]) || 0)
                .filter(t => t > 0);
            if (times.length > 0) {
                const bestTime = Math.min(...times);
                const avgTime = times.reduce((a, b) => a + b, 0) / times.length;
                const diff = avgTime - bestTime;
                if (diff > maxDiff) {
                    maxDiff = diff;
                    currentHighlightTurnName = turn.name || `Turn ${turnIdx + 1}`;
                }
            }
        });
    }

    renderSummaryStats(selectedLaps);
    renderLapTimesPlot(selectedLaps);
    renderStatsMinimap(selectedLaps);
    renderTurnGapsPlot(selectedLaps);
    renderApexSpeedsPlot(selectedLaps);

    // Ensure Plotly resizes after DOM reflow
    setTimeout(() => {
        const ids = ['stats-plot-lap-times', 'stats-plot-turn-gaps', 'stats-plot-apex-speeds'];
        ids.forEach(id => {
            const gd = document.getElementById(id);
            if (gd && gd.offsetParent !== null) Plotly.Plots.resize(gd);
        });
    }, 0);
}

/**
 * Calculates and renders summary statistics (Best Lap, Theoretical Best).
 */
function renderSummaryStats(laps) {
    const summaryContainer = document.getElementById('stats-summary-values');
    if (!summaryContainer) return;

    // Best Lap
    const lapTimes = laps.map(l => parseLapTime(l.lap_time)).filter(t => t > 0);
    const bestLapTime = lapTimes.length > 0 ? Math.min(...lapTimes) : null;
    
    // Theoretical Best (sum of best turns)
    let theoreticalBest = null;
    if (state.trackData && state.trackData.turns) {
        const numTurns = state.trackData.turns.length;
        const bestTurns = new Array(numTurns).fill(Infinity);
        laps.forEach(lap => {
            if (!lap.turn_times) return;
            lap.turn_times.forEach((t, i) => {
                if (t > 0 && t < bestTurns[i]) bestTurns[i] = t;
            });
        });
        const sum = bestTurns.reduce((a, b) => a + b, 0);
        if (sum !== Infinity) theoreticalBest = sum;
    }

    summaryContainer.innerHTML = `
        <div class="stat-item">
            <div style="font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase; font-weight: 700; letter-spacing: 0.05em; margin-bottom: 0.4rem;">Best Lap</div>
            <div style="font-size: 1.8rem; font-weight: 800; font-variant-numeric: tabular-nums;">${bestLapTime ? formatLapTime(bestLapTime) : '—'}</div>
        </div>
        <div class="stat-item">
            <div style="font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase; font-weight: 700; letter-spacing: 0.05em; margin-bottom: 0.4rem;">Theoretical Best</div>
            <div style="font-size: 1.8rem; font-weight: 800; font-variant-numeric: tabular-nums; color: var(--accent);">${theoreticalBest ? formatLapTime(theoreticalBest) : '—'}</div>
        </div>
    `;
}

/**
 * Renders a horizontal violin plot of lap times, split by group.
 */
function renderLapTimesPlot(laps) {
    const gd = document.getElementById('stats-plot-lap-times');
    if (!gd) return;

    state.statsPlotIndices = state.statsPlotIndices || {};
    state.statsPlotIndices.lapTimes = {};

    const data = [];
    let curveIdx = 0;
    ['A', 'B'].forEach(group => {
        const groupLaps = laps.filter(l => l.group === group);
        if (groupLaps.length === 0) return;

        const times = groupLaps.map(l => parseLapTime(l.lap_time));
        const labels = groupLaps.map(l => `Lap ${l.lap_num} (${l.session_name})`);
        const color = group === 'A' ? state.baseColor : state.highlightColor;
        
        groupLaps.forEach((lap, pointIdx) => {
            const lapId = lap.lapId;
            if (!state.statsPlotIndices.lapTimes[lapId]) {
                state.statsPlotIndices.lapTimes[lapId] = [];
            }
            state.statsPlotIndices.lapTimes[lapId].push({ curveNumber: curveIdx, pointNumber: pointIdx });
        });

        data.push({
            type: 'violin',
            y: times,
            name: `Group ${group}`,
            box: { visible: false },
            line: { color: color },
            fillcolor: color + '44', // Add transparency
            meanline: { visible: true },
            points: 'all',
            jitter: 0.5,
            pointpos: 0,
            text: labels,
            hoverinfo: 'y+text',
            orientation: 'v',
            showlegend: false
        });
        curveIdx++;
    });

    const layout = {
        autosize: true,
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: '#e2e8f0', family: 'Inter, sans-serif' },
        margin: { l: 60, r: 20, t: 10, b: 40 },
        xaxis: {
            title: '',
            gridcolor: 'rgba(255,255,255,0.1)',
            showticklabels: true
        },
        yaxis: {
            title: 'Lap Time (s)',
            gridcolor: 'rgba(255,255,255,0.1)',
            zerolinecolor: 'rgba(255,255,255,0.1)',
            tickformat: '.3f'
        },
        showlegend: false,
        violingap: 0.3,
        violingroupgap: 0,
        violinmode: 'group',
        hovermode: 'closest'
    };

    Plotly.react(gd, data, layout, { responsive: true, displayModeBar: false });
}

/**
 * Renders a vertical violin plot for each turn, showing the gap from the fastest turn time.
 */
function renderTurnGapsPlot(laps) {
    const gd = document.getElementById('stats-plot-turn-gaps');
    if (!gd || !state.trackData || !state.trackData.turns) return;

    state.statsPlotIndices = state.statsPlotIndices || {};
    state.statsPlotIndices.turnGaps = {};

    const turns = state.trackData.turns;
    const turnNames = turns.map((t, i) => t.name || `Turn ${i + 1}`);

    // Find fastest time for each turn among all selected/visible laps
    const turnFastestTimes = new Array(turns.length).fill(Infinity);
    laps.forEach(lap => {
        if (!lap.turn_times) return;
        lap.turn_times.forEach((t, i) => {
            if (t > 0 && t < turnFastestTimes[i]) {
                turnFastestTimes[i] = t;
            }
        });
    });

    const groupBDiffs = computeGroupBTurnDiffs(laps, turns.length);
    const maxGroupBDiff = groupBDiffs.length > 0 ? Math.max(...groupBDiffs) : 0;
    const redThreshold = getRedThreshold(maxGroupBDiff);

    // Pre-calculate average gaps for Group A and B per turn to format x-axis labels
    const turnLabels = new Array(turns.length);
    turns.forEach((_, turnIdx) => {
        const fastest = turnFastestTimes[turnIdx];
        const name = turnNames[turnIdx];
        const groupBColor = getTurnDiffColor(groupBDiffs[turnIdx], redThreshold);
        if (fastest === Infinity) {
            turnLabels[turnIdx] = `<span style="color: ${groupBColor}">${name}</span>`;
            return;
        }

        const lapsA = laps.filter(l => l.group === 'A');
        const gapsA = lapsA
            .map(l => (l.turn_times && l.turn_times[turnIdx]) ? l.turn_times[turnIdx] - fastest : null)
            .filter(g => g !== null && g >= 0);
        const avgGapA = gapsA.length > 0 ? (gapsA.reduce((a, b) => a + b, 0) / gapsA.length) : null;

        const lapsB = laps.filter(l => l.group === 'B');
        const gapsB = lapsB
            .map(l => (l.turn_times && l.turn_times[turnIdx]) ? l.turn_times[turnIdx] - fastest : null)
            .filter(g => g !== null && g >= 0);
        const avgGapB = gapsB.length > 0 ? (gapsB.reduce((a, b) => a + b, 0) / gapsB.length) : null;

        const strA = avgGapA !== null ? `<span style="color: #fb923c">${avgGapA.toFixed(2)} s</span>` : '— s';
        const strB = avgGapB !== null ? `<span style="color: #38bdf8">${avgGapB.toFixed(2)} s</span>` : '— s';
        
        const isHighlighted = (currentHighlightTurnName && (name === currentHighlightTurnName || name.split(' & ')[0].trim() === currentHighlightTurnName.split(' & ')[0].trim()));
        const nameStr = isHighlighted
            ? `<b><span style="color: ${groupBColor}">${name}</span></b>`
            : `<span style="color: ${groupBColor}; font-weight: 600;">${name}</span>`;
        
        turnLabels[turnIdx] = `${nameStr}<br>${strA}<br>${strB}`;
    });

    const data = [];
    let curveIdx = 0;
    ['A', 'B'].forEach(group => {
        const groupLaps = laps.filter(l => l.group === group);
        if (groupLaps.length === 0) return;

        const xData = []; // Turn names
        const yData = []; // Gaps
        const textData = []; // Hover text
        const color = group === 'A' ? state.baseColor : state.highlightColor;

        let ptIdx = 0;
        turns.forEach((_, turnIdx) => {
            const fastest = turnFastestTimes[turnIdx];
            if (fastest === Infinity) return;

            groupLaps.forEach(lap => {
                const turnTime = (lap.turn_times && lap.turn_times[turnIdx]);
                if (turnTime > 0) {
                    xData.push(turnNames[turnIdx]);
                    yData.push(turnTime - fastest);
                    textData.push(`Lap ${lap.lap_num} (${lap.session_name})<br>Time: ${turnTime.toFixed(3)}s`);
                    
                    const lapId = lap.lapId;
                    if (!state.statsPlotIndices.turnGaps[lapId]) {
                        state.statsPlotIndices.turnGaps[lapId] = [];
                    }
                    state.statsPlotIndices.turnGaps[lapId].push({ curveNumber: curveIdx, pointNumber: ptIdx });
                    ptIdx++;
                }
            });
        });

        if (xData.length === 0) return;

        data.push({
            type: 'violin',
            x: xData,
            y: yData,
            name: `Group ${group}`,
            box: { visible: false },
            line: { color: color },
            fillcolor: color + '44',
            meanline: { visible: true },
            points: 'all',
            jitter: 0.3,
            text: textData,
            hoverinfo: 'y+text',
            spanmode: 'hard',
            showlegend: false
        });
        curveIdx++;
    });

    const layout = {
        autosize: true,
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: '#e2e8f0', family: 'Inter, sans-serif' },
        margin: { l: 60, r: 40, t: 10, b: 95 },
        yaxis: {
            title: 'Gap from Fastest (s)',
            gridcolor: 'rgba(255,255,255,0.1)',
            zerolinecolor: 'rgba(255,255,255,0.2)',
            tickformat: '.3f'
        },
        xaxis: {
            title: '',
            gridcolor: 'rgba(255,255,255,0.1)',
            tickangle: 0,
            tickmode: 'array',
            tickvals: turnNames,
            ticktext: turnLabels
        },
        showlegend: false,
        legend: { 
            orientation: 'h', 
            y: -0.15, 
            x: 0.5, 
            xanchor: 'center',
            yanchor: 'top'
        },
        violinmode: 'group',
        hovermode: 'closest'
    };

    Plotly.react(gd, data, layout, { responsive: true, displayModeBar: false });
}

/**
 * Renders a vertical violin plot showing minimum corner speeds for each selected lap.
 */
function renderApexSpeedsPlot(laps) {
    const gd = document.getElementById('stats-plot-apex-speeds');
    if (!gd || !state.trackData || !state.trackData.turns) return;

    state.statsPlotIndices = state.statsPlotIndices || {};
    state.statsPlotIndices.apexSpeeds = {};

    // Collect all corners (turns) in order
    const corners = [];
    state.trackData.turns.forEach((turn, turnIdx) => {
        const rawName = turn.name || `Turn ${turnIdx + 1}`;
        const name = rawName.split(' & ')[0];
        // Use turn start/end for range if available, fallback to first apex or null
        const start = turn.start;
        const end = turn.end;
        const fallbackApex = turn.apex && turn.apex.length > 0 ? turn.apex[0] : null;
        
        const isHighlighted = (currentHighlightTurnName && (name === currentHighlightTurnName || name.split(' & ')[0].trim() === currentHighlightTurnName.split(' & ')[0].trim()));
        const label = isHighlighted ? `<b><span style="color: #fb923c">${name}</span></b>` : name;

        corners.push({
            turnIdx,
            start,
            end,
            fallbackApex,
            label,
            name
        });
    });

    if (corners.length === 0) {
        gd.innerHTML = '<div style="padding: 4rem; text-align: center; opacity: 0.5;">No turn data defined for this track</div>';
        return;
    }

    const data = [];
    let curveIdx = 0;
    ['A', 'B'].forEach(group => {
        const groupLaps = laps.filter(l => l.group === group);
        if (groupLaps.length === 0) return;

        const xData = []; // Corner labels
        const yData = []; // Speeds
        const textData = []; // Hover text
        const color = group === 'A' ? state.baseColor : state.highlightColor;

        let ptIdx = 0;
        corners.forEach((corner) => {
            groupLaps.forEach(lap => {
                const speed = getMinSpeedInRange(lap, corner.start, corner.end, corner.fallbackApex);
                if (speed !== null && speed !== undefined) {
                    xData.push(corner.name);
                    yData.push(speed);
                    textData.push(`Lap ${lap.lap_num} (${lap.session_name})<br>Min Speed: ${speed.toFixed(1)} km/h`);
                    
                    const lapId = lap.lapId;
                    if (!state.statsPlotIndices.apexSpeeds[lapId]) {
                        state.statsPlotIndices.apexSpeeds[lapId] = [];
                    }
                    state.statsPlotIndices.apexSpeeds[lapId].push({ curveNumber: curveIdx, pointNumber: ptIdx });
                    ptIdx++;
                }
            });
        });

        if (xData.length === 0) return;

        data.push({
            type: 'violin',
            x: xData,
            y: yData,
            name: `Group ${group}`,
            box: { visible: false },
            line: { color: color },
            fillcolor: color + '44',
            meanline: { visible: true },
            points: 'all',
            jitter: 0.3,
            text: textData,
            hoverinfo: 'y+text',
            spanmode: 'hard',
            showlegend: false
        });
        curveIdx++;
    });

    const layout = {
        autosize: true,
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: '#e2e8f0', family: 'Inter, sans-serif' },
        margin: { l: 60, r: 40, t: 10, b: 60 },
        yaxis: {
            title: 'Minimum Corner Speed (km/h)',
            gridcolor: 'rgba(255,255,255,0.1)',
            zerolinecolor: 'rgba(255,255,255,0.2)',
            tickformat: '.1f'
        },
        xaxis: {
            title: '',
            gridcolor: 'rgba(255,255,255,0.1)',
            tickangle: 0,
            tickmode: 'array',
            tickvals: corners.map(c => c.name),
            ticktext: corners.map(c => c.label)
        },
        showlegend: false,
        legend: { 
            orientation: 'h', 
            y: -0.2, 
            x: 0.5, 
            xanchor: 'center',
            yanchor: 'top'
        },
        violinmode: 'group',
        hovermode: 'closest'
    };

    Plotly.react(gd, data, layout, { responsive: true, displayModeBar: false });
}

/**
 * Calculates the red threshold for the turn difference color scale.
 * Red threshold is the lowest power of 2 (minimum 0.5s) that is >= max gap.
 * @param {number} maxGap - Maximum time difference in seconds.
 * @returns {number} Dynamic red threshold in seconds (0.5, 1, 2, 4...).
 */
export function getRedThreshold(maxGap) {
    if (!maxGap || maxGap <= 0.5) return 0.5;
    let val = 0.5;
    while (val < maxGap) {
        val *= 2;
    }
    return val;
}

/**
 * Maps a time difference in seconds to a green-yellow-red color scale based on redThreshold.
 * @param {number} diff - Time difference in seconds.
 * @param {number} [redThreshold=1.0] - Red threshold in seconds.
 * @returns {string} Hex color string (e.g., "#22c55e", "#eab308", "#ef4444").
 */
export function getTurnDiffColor(diff, redThreshold = 1.0) {
    if (diff === null || diff === undefined || isNaN(diff) || diff < 0) {
        diff = 0;
    }
    const maxVal = redThreshold > 0 ? redThreshold : 1.0;
    const t = Math.min(Math.max(diff / maxVal, 0), 1.0);
    let r, g, b;
    if (t <= 0.5) {
        const ratio = t / 0.5;
        r = Math.round(34 + (234 - 34) * ratio);
        g = Math.round(197 + (179 - 197) * ratio);
        b = Math.round(94 + (8 - 94) * ratio);
    } else {
        const ratio = (t - 0.5) / 0.5;
        r = Math.round(234 + (239 - 234) * ratio);
        g = Math.round(179 + (68 - 179) * ratio);
        b = Math.round(8 + (68 - 8) * ratio);
    }
    const toHex = (n) => n.toString(16).padStart(2, '0');
    return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}

/**
 * Computes mean - min time difference for each turn based on Group B laps.
 * @param {Array} laps - Array of lap objects with group and turn_times.
 * @param {number} numTurns - Number of turns in track.
 * @returns {Array<number>} Array of time differences per turn in seconds.
 */
export function computeGroupBTurnDiffs(laps, numTurns) {
    const diffs = new Array(numTurns).fill(0);
    if (!laps || laps.length === 0 || !numTurns) return diffs;

    const groupBLaps = laps.filter(l => l.group === 'B');
    if (groupBLaps.length === 0) return diffs;

    for (let turnIdx = 0; turnIdx < numTurns; turnIdx++) {
        const turnTimes = groupBLaps
            .map(l => (l.turn_times && l.turn_times[turnIdx] > 0) ? l.turn_times[turnIdx] : null)
            .filter(t => t !== null);

        if (turnTimes.length > 0) {
            const minTime = Math.min(...turnTimes);
            const meanTime = turnTimes.reduce((a, b) => a + b, 0) / turnTimes.length;
            diffs[turnIdx] = Math.max(0, meanTime - minTime);
        }
    }
    return diffs;
}

/**
 * Renders the track minimap with white turn underlays, Group B gap color-coded centerlines,
 * turn boundaries, and turn labels.
 * @param {Array} laps - Array of selected lap objects.
 */
export function renderStatsMinimap(laps) {
    const container = document.getElementById('stats-minimap-container');
    if (!container) return;

    if (!state.trackData || !state.trackData.center_line || state.trackData.center_line.length < 2 || !state.trackData.turns) {
        container.innerHTML = `
            <div style="padding: 2rem; text-align: center; opacity: 0.5; height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;">
                <p style="font-size: 0.85rem; color: var(--text-secondary);">No track geometry available</p>
            </div>`;
        return;
    }

    const rawPoints = state.trackData.center_line;
    const turns = state.trackData.turns;
    const numTurns = turns.length;
    if (numTurns === 0) return;

    const groupBDiffs = computeGroupBTurnDiffs(laps, numTurns);
    const maxGroupBDiff = groupBDiffs.length > 0 ? Math.max(...groupBDiffs) : 0;
    const redThreshold = getRedThreshold(maxGroupBDiff);

    const points = rawPoints.map(p => ({
        lat: p.lat,
        lon: p.lon !== undefined ? p.lon : (p.lng !== undefined ? p.lng : 0),
        dist: p.dist
    })).filter(p => p.lat !== undefined && p.lon !== undefined)
      .sort((a, b) => a.dist - b.dist);

    if (points.length < 2) return;

    const latAvg = points.reduce((sum, p) => sum + p.lat, 0) / points.length;
    const cosLat = Math.cos((latAvg * Math.PI) / 180);

    const projected = points.map(p => ({
        x: p.lon * 111320 * cosLat,
        y: p.lat * 111320,
        dist: p.dist
    }));

    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    projected.forEach(p => {
        if (p.x < minX) minX = p.x;
        if (p.x > maxX) maxX = p.x;
        if (p.y < minY) minY = p.y;
        if (p.y > maxY) maxY = p.y;
    });

    const widthM = maxX - minX;
    const heightM = maxY - minY;
    const centerX_m = (minX + maxX) / 2;
    const centerY_m = (minY + maxY) / 2;

    const viewBoxW = 320;
    const viewBoxH = 430;
    const padding = 38;

    const availableW = viewBoxW - 2 * padding;
    const availableH = viewBoxH - 2 * padding;

    const scale = Math.max(widthM / availableW, heightM / availableH, 0.00001);

    function toSvgCoords(x_m, y_m) {
        const svgX = (viewBoxW / 2) + (x_m - centerX_m) / scale;
        const svgY = (viewBoxH / 2) - (y_m - centerY_m) / scale;
        return { x: svgX, y: svgY };
    }

    const svgPoints = projected.map(p => {
        const coords = toSvgCoords(p.x, p.y);
        return {
            ...p,
            svgX: coords.x,
            svgY: coords.y
        };
    });

    const lapLength = state.trackData.lap_length || svgPoints[svgPoints.length - 1].dist;

    function normDist(d) {
        let nd = d % lapLength;
        if (nd < 0) nd += lapLength;
        return nd;
    }

    function getPointAtDist(targetDist) {
        const dist = normDist(targetDist);
        for (let i = 0; i < svgPoints.length - 1; i++) {
            const p1 = svgPoints[i];
            const p2 = svgPoints[i + 1];
            if (dist >= p1.dist && dist <= p2.dist) {
                const frac = (p2.dist > p1.dist) ? (dist - p1.dist) / (p2.dist - p1.dist) : 0;
                return {
                    svgX: p1.svgX + (p2.svgX - p1.svgX) * frac,
                    svgY: p1.svgY + (p2.svgY - p1.svgY) * frac,
                    x: p1.x + (p2.x - p1.x) * frac,
                    y: p1.y + (p2.y - p1.y) * frac,
                    dist
                };
            }
        }
        if (dist >= svgPoints[svgPoints.length - 1].dist) {
            return svgPoints[svgPoints.length - 1];
        }
        return svgPoints[0];
    }

    function getPointsForRange(startDist, endDist) {
        const startPt = getPointAtDist(startDist);
        const endPt = getPointAtDist(endDist);
        const result = [startPt];

        const sDist = normDist(startDist);
        const eDist = normDist(endDist);

        if (sDist <= eDist) {
            svgPoints.forEach(p => {
                if (p.dist > sDist && p.dist < eDist) {
                    result.push(p);
                }
            });
        } else {
            svgPoints.forEach(p => {
                if (p.dist > sDist) {
                    result.push(p);
                }
            });
            svgPoints.forEach(p => {
                if (p.dist < eDist) {
                    result.push(p);
                }
            });
        }

        result.push(endPt);
        return result;
    }

    let underlaysSvg = '';
    let centerlinesSvg = '';
    let ticksSvg = '';
    let labelsSvg = '';

    turns.forEach((turn, i) => {
        const nextTurn = turns[(i + 1) % numTurns];
        const turnStart = turn.start !== undefined ? turn.start : 0;
        const turnEnd = turn.end !== undefined ? turn.end : turnStart;
        const segmentEnd = nextTurn.start !== undefined ? nextTurn.start : turnEnd;

        const diff = groupBDiffs[i];
        const color = getTurnDiffColor(diff, redThreshold);

        // 1. Thicker light-grey line under turn portion
        if (turnStart !== turnEnd) {
            const turnPts = getPointsForRange(turnStart, turnEnd);
            const dStr = "M " + turnPts.map(p => `${p.svgX.toFixed(1)},${p.svgY.toFixed(1)}`).join(" L ");
            underlaysSvg += `<path d="${dStr}" stroke="#cbd5e1" stroke-width="9" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity="0.9" />`;
        }

        // 2. Centerline covering turn and straight after it (thicker main line)
        const segPts = getPointsForRange(turnStart, segmentEnd);
        const segDStr = "M " + segPts.map(p => `${p.svgX.toFixed(1)},${p.svgY.toFixed(1)}`).join(" L ");
        centerlinesSvg += `<path d="${segDStr}" stroke="${color}" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round" fill="none" />`;

        // 3. Turn boundary tick marks (at start and end)
        [turnStart, turnEnd].forEach(bDist => {
            const pt = getPointAtDist(bDist);
            const pA = getPointAtDist(bDist - 0.5);
            const pB = getPointAtDist(bDist + 0.5);
            const tx = pB.svgX - pA.svgX;
            const ty = pB.svgY - pA.svgY;
            const tLen = Math.sqrt(tx * tx + ty * ty);
            if (tLen > 0) {
                const nx = -ty / tLen;
                const ny = tx / tLen;
                const x1 = (pt.svgX - nx * 5.5).toFixed(1);
                const y1 = (pt.svgY - ny * 5.5).toFixed(1);
                const x2 = (pt.svgX + nx * 5.5).toFixed(1);
                const y2 = (pt.svgY + ny * 5.5).toFixed(1);
                ticksSvg += `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#cbd5e1" stroke-width="2" opacity="0.9" />`;
            }
        });

        // 4. Turn name label with black stroke outline and background glow
        const apexDist = (turn.apex && turn.apex.length > 0) ? turn.apex[0] : (turnStart + turnEnd) / 2;
        const apexPt = getPointAtDist(apexDist);
        const vx = apexPt.svgX - (viewBoxW / 2);
        const vy = apexPt.svgY - (viewBoxH / 2);
        const vLen = Math.sqrt(vx * vx + vy * vy);
        const ux = vLen > 0 ? (vx / vLen) : 0;
        const uy = vLen > 0 ? (vy / vLen) : -1;

        const lblX = (apexPt.svgX + ux * 18).toFixed(1);
        const lblY = (apexPt.svgY + uy * 18).toFixed(1);
        const turnName = turn.name || (`T${i + 1}`);

        labelsSvg += `
            <text x="${lblX}" y="${lblY}" fill="${color}" font-size="11" font-weight="800" text-anchor="middle" dominant-baseline="central" style="paint-order: stroke fill; stroke: #000000; stroke-width: 3.5px; stroke-linejoin: round; text-shadow: 0 0 6px #000000, 0 0 10px #000000;">
                ${turnName}
            </text>`;
    });

    container.innerHTML = `
        <div style="font-size: 0.85rem; font-weight: 700; color: var(--text-secondary); margin-bottom: 0.4rem; text-align: center;">
            Track Minimap
        </div>
        <div style="flex: 1; width: 100%; display: flex; align-items: center; justify-content: center;">
            <svg viewBox="0 0 ${viewBoxW} ${viewBoxH}" style="width: 100%; height: 100%; max-height: 420px;">
                <g id="minimap-underlines">${underlaysSvg}</g>
                <g id="minimap-centerlines">${centerlinesSvg}</g>
                <g id="minimap-ticks">${ticksSvg}</g>
                <g id="minimap-labels">${labelsSvg}</g>
            </svg>
        </div>
        <div style="display: flex; align-items: center; justify-content: center; gap: 0.6rem; font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.4rem;">
            <span>0s (min)</span>
            <div style="width: 80px; height: 6px; border-radius: 3px; background: linear-gradient(to right, #22c55e, #eab308, #ef4444);"></div>
            <span>${redThreshold}s+ (mean)</span>
        </div>
    `;
}
