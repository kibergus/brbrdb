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
import { selectTurnAndSwitchToMap } from './lap_selection.js';
import {
  getRedThreshold,
  getTurnDiffColor,
  computeGroupBTurnDiffs,
  generateMinimapSvg
} from '../stats_plots.js';
import { getGroupColor } from './palette.js';

export { getRedThreshold, getTurnDiffColor, computeGroupBTurnDiffs, renderTurnGapsPlot };

let currentHighlightTurnName = null;

function attachTurnClickHandlers(gd) {
  if (!gd || gd._turnClickBound) return;
  gd._turnClickBound = true;

  if (typeof gd.addEventListener === 'function') {
    gd.addEventListener('click', e => {
      const turns = state.trackData && state.trackData.turns ? state.trackData.turns : [];
      if (turns.length === 0) return;

      const target = e.target;
      let foundIdx = -1;

      // 1. Direct SVG target check on ticks/labels
      if (target && typeof target.closest === 'function') {
        const tickEl = target.closest('.xtick, .xtick text, .xtick tspan, .xaxislayer-above text, .xaxislayer-above g');
        if (tickEl) {
          const text = tickEl.textContent || '';
          for (let i = 0; i < turns.length; i++) {
            const name = turns[i].name || `Turn ${i + 1}`;
            if (text.startsWith(name) || text.includes(name)) {
              foundIdx = i;
              break;
            }
          }

          if (foundIdx === -1) {
            const xtick = target.closest('.xtick');
            if (xtick && xtick.parentElement) {
              const allTicks = Array.from(xtick.parentElement.querySelectorAll('.xtick'));
              const tickIdx = allTicks.indexOf(xtick);
              if (tickIdx >= 0 && tickIdx < turns.length) {
                foundIdx = tickIdx;
              }
            }
          }
        }
      }

      // 2. Coordinate-based fallback for click anywhere in the x-axis label zone
      if (foundIdx === -1 && typeof gd.getBoundingClientRect === 'function') {
        const rect = gd.getBoundingClientRect();
        const clickX = e.clientX - rect.left;
        const clickY = e.clientY - rect.top;

        const fullLayout = gd._fullLayout;
        if (fullLayout && fullLayout.xaxis && fullLayout.yaxis) {
          const xOffset = fullLayout.xaxis._offset !== undefined ? fullLayout.xaxis._offset : 60;
          const xLength = fullLayout.xaxis._length !== undefined ? fullLayout.xaxis._length : (rect.width - 100);
          const yBottom = (fullLayout.yaxis._offset !== undefined ? fullLayout.yaxis._offset : 38) +
                          (fullLayout.yaxis._length !== undefined ? fullLayout.yaxis._length : (rect.height - 130));

          if (clickY >= yBottom - 5 && clickX >= xOffset && clickX <= xOffset + xLength) {
            const idx = Math.floor(((clickX - xOffset) / xLength) * turns.length);
            if (idx >= 0 && idx < turns.length) {
              foundIdx = idx;
            }
          }
        }
      }

      if (foundIdx !== -1) {
        selectTurnAndSwitchToMap(foundIdx);
      }
    });
  }
}

/**
 * Main entry point to refresh all stats plots based on current selection and visibility.
 */
export function renderStatsPlots() {
  const selectedLaps = [];

  const activeGroups = state.getActiveGroups ? state.getActiveGroups() : ['A', 'B'];

  activeGroups.forEach(group => {
    const isVis = state.isGroupVisible ? state.isGroupVisible(group, 'stats') : true;
    if (isVis) {
      const sel = (state.groupSelections && state.groupSelections[group]) || [];
      sel.forEach(lapId => {
        const lap = state.lapDataLookup ? state.lapDataLookup[lapId] : null;
        if (lap) {
          const sessionId = lap.session_id || lapId.slice(0, lapId.lastIndexOf('-'));
          const session = (state.allSessionsData || []).find(s => s.session_id == sessionId);
          selectedLaps.push({
            ...lap,
            group: group,
            session_name: session ? session.session_name : (lap.session_name || ''),
            lapId: lapId
          });
        }
      });
    }
  });

  const container = document.getElementById('stats-plots-container');
  const noData = document.getElementById('stats-no-data');
  const loading = document.getElementById('stats-loading');
  const idealDisplay = document.getElementById('ideal-time-display');

  if (state.isLoadingTelemetry) {
    if (container) container.style.display = 'none';
    if (noData) noData.style.display = 'none';
    if (loading) loading.style.display = 'flex';
    if (idealDisplay) idealDisplay.style.display = 'none';
    return;
  }

  if (loading) loading.style.display = 'none';

  if (selectedLaps.length === 0) {
    if (container) container.style.display = 'none';
    if (noData) noData.style.display = 'block';
    if (idealDisplay) idealDisplay.style.display = 'none';
    return;
  }

  if (container) container.style.display = 'flex';
  if (noData) noData.style.display = 'none';

  const groupALaps = selectedLaps.filter(l => l.group === 'A' && l.is_valid !== false);
  currentHighlightTurnName = null;
  if (state.trackData && state.trackData.turns && groupALaps.length > 0) {
    let maxDiff = -1;
    state.trackData.turns.forEach((turn, turnIdx) => {
      const times = groupALaps
        .map(lap => (lap.turn_times && lap.turn_times[turnIdx]) || 0)
        .filter(t => t > 0.5);
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

  const validLaps = laps.filter(l => l.is_valid !== false);

  // Best Lap
  const lapTimes = validLaps.map(l => parseLapTime(l.lap_time)).filter(t => t > 0);
  const bestLapTime = lapTimes.length > 0 ? Math.min(...lapTimes) : null;

  // Theoretical Best (sum of best turns)
  let theoreticalBest = null;
  if (state.trackData && state.trackData.turns) {
    const numTurns = state.trackData.turns.length;
    const bestTurns = new Array(numTurns).fill(Infinity);
    validLaps.forEach(lap => {
      if (!lap.turn_times) return;
      lap.turn_times.forEach((t, i) => {
        if (t > 0.5 && t < bestTurns[i]) bestTurns[i] = t;
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
 * Renders a horizontal violin plot showing the distribution of lap times per session.
 */
function renderLapTimesPlot(laps) {
  const gd = document.getElementById('stats-plot-lap-times');
  if (!gd) return;

  state.statsPlotIndices = state.statsPlotIndices || {};
  state.statsPlotIndices.lapTimes = {};

  const data = [];
  let curveIdx = 0;
  const activeGroups = state.getActiveGroups ? state.getActiveGroups() : ['A', 'B'];
  const groupsInLaps = Array.from(new Set(laps.map(l => l.group))).filter(Boolean);
  const orderedGroups = activeGroups.filter(g => groupsInLaps.includes(g));
  const groupsToRender = orderedGroups.length > 0 ? orderedGroups : (groupsInLaps.length > 0 ? groupsInLaps.sort() : ['A', 'B']);

  groupsToRender.forEach(group => {
    const groupLaps = laps.filter(l => l.group === group && l.is_valid !== false);
    if (groupLaps.length === 0) return;

    const times = [];
    const labels = [];
    const validGroupLaps = [];
    groupLaps.forEach(l => {
      const raw = l.lap_time !== undefined ? l.lap_time : l.time;
      const t = parseLapTime(raw);
      if (t !== null && !isNaN(t) && t > 0) {
        times.push(t);
        labels.push(`Lap ${l.lap_num} (${l.session_name || 'Session'})<br>Time: ${formatLapTime(t)}`);
        validGroupLaps.push(l);
      }
    });

    if (times.length === 0) return;

    const color = getGroupColor(group);

    validGroupLaps.forEach((lap, pointIdx) => {
      const lapId = lap.lapId;
      if (!state.statsPlotIndices.lapTimes[lapId]) {
        state.statsPlotIndices.lapTimes[lapId] = [];
      }
      state.statsPlotIndices.lapTimes[lapId].push({ curveNumber: curveIdx, pointNumber: pointIdx });
    });

    data.push({
      type: 'violin',
      x: new Array(times.length).fill(group),
      y: times,
      name: `Group ${group}`,
      box: { visible: false },
      line: { color: color },
      fillcolor: color + '44',
      meanline: { visible: true },
      points: 'all',
      jitter: 0.4,
      pointpos: 0,
      text: labels,
      hoverinfo: 'y+text',
      orientation: 'v',
      spanmode: 'hard',
      showlegend: false
    });
    curveIdx++;
  });

  const layout = {
    autosize: true,
    dragmode: false,
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { color: '#e2e8f0', family: 'Inter, sans-serif' },
    margin: { l: 60, r: 20, t: 10, b: 40 },
    xaxis: {
      title: '',
      gridcolor: 'rgba(255,255,255,0.1)',
      showticklabels: true,
      categoryorder: 'array',
      categoryarray: activeGroups,
      fixedrange: true
    },
    yaxis: {
      title: 'Lap Time (s)',
      gridcolor: 'rgba(255,255,255,0.1)',
      zerolinecolor: 'rgba(255,255,255,0.1)',
      tickformat: '.3f',
      fixedrange: true
    },
    showlegend: false,
    violingap: 0.3,
    violingroupgap: 0,
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

  const turnFastestTimes = new Array(turns.length).fill(Infinity);
  laps.forEach(lap => {
    if (lap.is_valid === false || !lap.turn_times) return;
    lap.turn_times.forEach((t, i) => {
      if (t > 0.5 && t < turnFastestTimes[i]) {
        turnFastestTimes[i] = t;
      }
    });
  });

  const groupBDiffs = computeGroupBTurnDiffs(laps, turns.length);
  const redThreshold = getRedThreshold(Math.max(...groupBDiffs, 0));

  const MAX_GAP = 2.0;
  const OUTLIER_Y = 2.06;

  const activeGroups = state.getActiveGroups ? state.getActiveGroups() : ['A', 'B'];
  const groupsInLaps = Array.from(new Set(laps.map(l => l.group))).filter(Boolean);
  const orderedGroups = activeGroups.filter(g => groupsInLaps.includes(g));
  const groupsList = orderedGroups.length > 0 ? orderedGroups : (groupsInLaps.length > 0 ? groupsInLaps.sort() : ['A', 'B']);

  const turnLabels = new Array(turns.length);
  turns.forEach((_, turnIdx) => {
    const fastest = turnFastestTimes[turnIdx];
    const name = turnNames[turnIdx];
    const groupBColor = getTurnDiffColor(groupBDiffs[turnIdx], redThreshold);
    if (fastest === Infinity) {
      turnLabels[turnIdx] = `<span style="color: ${groupBColor}">${name}</span>`;
      return;
    }

    const groupStrings = groupsList.map(g => {
      const gLaps = laps.filter(l => l.group === g && l.is_valid !== false);
      const gaps = gLaps
        .map(l => (l.turn_times && l.turn_times[turnIdx] > 0.5) ? l.turn_times[turnIdx] - fastest : null)
        .filter(val => val !== null && val >= 0 && val <= MAX_GAP);
      const avgGap = gaps.length > 0 ? (gaps.reduce((a, b) => a + b, 0) / gaps.length) : null;
      return avgGap !== null ? `<span style="color: ${getGroupColor(g)}">${avgGap.toFixed(2)} s</span>` : '— s';
    });

    const isHighlighted = (currentHighlightTurnName && (name === currentHighlightTurnName || name.split(' & ')[0].trim() === currentHighlightTurnName.split(' & ')[0].trim()));
    const nameStr = isHighlighted
      ? `<b><span style="color: ${groupBColor}">${name}</span></b>`
      : `<span style="color: ${groupBColor}; font-weight: 600;">${name}</span>`;

    turnLabels[turnIdx] = [nameStr, ...groupStrings].join('<br>');
  });

  const data = [];
  let curveIdx = 0;
  let maxObservedGap = 0;
  let hasOutliers = false;

  groupsList.forEach(group => {
    const groupLaps = laps.filter(l => l.group === group && l.is_valid !== false);
    if (groupLaps.length === 0) return;

    const inRangeX = [];
    const inRangeY = [];
    const inRangeText = [];

    const outlierX = [];
    const outlierY = [];
    const outlierText = [];

    const color = getGroupColor(group);

    turns.forEach((_, turnIdx) => {
      const fastest = turnFastestTimes[turnIdx];
      if (fastest === Infinity) return;

      groupLaps.forEach(lap => {
        const turnTime = (lap.turn_times && lap.turn_times[turnIdx]);
        if (turnTime > 0.5) {
          const gap = turnTime - fastest;
          const hoverLabel = `Lap ${lap.lap_num} (${lap.session_name})<br>Time: ${turnTime.toFixed(3)}s (+${gap.toFixed(3)}s)`;
          const lapId = lap.lapId;
          if (!state.statsPlotIndices.turnGaps[lapId]) {
            state.statsPlotIndices.turnGaps[lapId] = [];
          }

          if (gap <= MAX_GAP) {
            const ptIdx = inRangeX.length;
            inRangeX.push(turnNames[turnIdx]);
            inRangeY.push(gap);
            inRangeText.push(hoverLabel);
            state.statsPlotIndices.turnGaps[lapId].push({ curveNumber: curveIdx, pointNumber: ptIdx });
            if (gap > maxObservedGap) {
              maxObservedGap = gap;
            }
          } else {
            hasOutliers = true;
            const ptIdx = outlierX.length;
            outlierX.push(turnNames[turnIdx]);
            outlierY.push(OUTLIER_Y);
            outlierText.push(hoverLabel + ' (Outlier)');
            state.statsPlotIndices.turnGaps[lapId].push({ curveNumber: curveIdx + 1, pointNumber: ptIdx });
          }
        }
      });
    });

    if (inRangeX.length === 0 && outlierX.length === 0) return;

    if (inRangeX.length > 0) {
      data.push({
        type: 'violin',
        x: inRangeX,
        y: inRangeY,
        name: `Group ${group}`,
        box: { visible: false },
        line: { color: color },
        fillcolor: color + '44',
        meanline: { visible: true },
        points: 'all',
        jitter: 0.3,
        text: inRangeText,
        hoverinfo: 'y+text',
        spanmode: 'hard',
        showlegend: false
      });
      curveIdx++;
    }

    if (outlierX.length > 0) {
      data.push({
        type: 'scatter',
        mode: 'markers',
        x: outlierX,
        y: outlierY,
        name: `Group ${group} Outliers`,
        marker: { color: color, size: 6, symbol: 'circle' },
        text: outlierText,
        hoverinfo: 'text',
        showlegend: false
      });
      curveIdx++;
    }
  });

  let yMax = 2.0;
  let yMin = -0.05;
  let yRangeTop = 2.12;
  let tickvals = [0, 0.5, 1.0, 1.5, 2.0];

  if (!hasOutliers && maxObservedGap > 0) {
    let step = 0.5;
    if (maxObservedGap <= 0.15) step = 0.05;
    else if (maxObservedGap <= 0.4) step = 0.1;
    else if (maxObservedGap <= 1.0) step = 0.2;
    else step = 0.5;

    yMax = Math.min(2.0, Math.ceil(maxObservedGap / step) * step);
    yMin = -0.03 * yMax;
    yRangeTop = yMax + (step * 0.25);

    tickvals = [];
    for (let v = 0; v <= yMax + 0.0001; v += step) {
      tickvals.push(parseFloat(v.toFixed(3)));
    }
  }

  const ticktext = tickvals.map((v, i) => (i === tickvals.length - 1 ? `${v.toFixed(3)}s` : v.toFixed(3)));

  const layout = {
    autosize: true,
    dragmode: false,
    title: {
      text: '<b>Turn Performance Gaps</b>',
      font: { color: '#cbd5e1', size: 19, family: 'Inter, sans-serif' },
      x: 0.02,
      xref: 'paper',
      y: 0.98,
      yref: 'paper',
      xanchor: 'left',
      yanchor: 'top'
    },
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { color: '#e2e8f0', family: 'Inter, sans-serif' },
    margin: { l: 60, r: 40, t: 38, b: Math.max(95, 45 + (groupsList.length * 18)) },
    yaxis: {
      title: 'Gap from Fastest (s)',
      range: [yMin, yRangeTop],
      autorange: false,
      gridcolor: 'rgba(255,255,255,0.1)',
      zerolinecolor: 'rgba(255,255,255,0.2)',
      tickvals: tickvals,
      ticktext: ticktext,
      tickformat: '.3f',
      fixedrange: true
    },
    xaxis: {
      title: '',
      gridcolor: 'rgba(255,255,255,0.1)',
      tickangle: 0,
      tickmode: 'array',
      tickvals: turnNames,
      ticktext: turnLabels,
      fixedrange: true
    },
    showlegend: false,
    legend: { orientation: 'h', y: -0.15, x: 0.5, xanchor: 'center', yanchor: 'top' },
    violinmode: 'group',
    hovermode: 'closest'
  };

  Plotly.react(gd, data, layout, { responsive: true, displayModeBar: false });
  attachTurnClickHandlers(gd);
}

/**
 * Renders a vertical violin plot showing minimum corner speeds for each selected lap.
 */
function renderApexSpeedsPlot(laps) {
  const gd = document.getElementById('stats-plot-apex-speeds');
  if (!gd || !state.trackData || !state.trackData.turns) return;

  state.statsPlotIndices = state.statsPlotIndices || {};
  state.statsPlotIndices.apexSpeeds = {};

  const turns = state.trackData.turns;
  const turnNames = turns.map((t, i) => t.name || `Turn ${i + 1}`);

  const activeGroups = state.getActiveGroups ? state.getActiveGroups() : ['A', 'B'];
  const groupsInLaps = Array.from(new Set(laps.map(l => l.group))).filter(Boolean);
  const orderedGroups = activeGroups.filter(g => groupsInLaps.includes(g));
  const groupsList = orderedGroups.length > 0 ? orderedGroups : (groupsInLaps.length > 0 ? groupsInLaps.sort() : ['A', 'B']);

  const data = [];
  let curveIdx = 0;
  groupsList.forEach(group => {
    const groupLaps = laps.filter(l => l.group === group && l.is_valid !== false);
    if (groupLaps.length === 0) return;

    const xData = [];
    const yData = [];
    const textData = [];
    const color = getGroupColor(group);

    let ptIdx = 0;
    turns.forEach((turn, turnIdx) => {
      const turnStart = turn.start !== undefined ? turn.start : 0;
      const turnEnd = turn.end !== undefined ? turn.end : turnStart;

      groupLaps.forEach(lap => {
        let minSpeed = null;
        if (lap.turn_min_speeds && lap.turn_min_speeds[turnIdx] !== undefined) {
          minSpeed = lap.turn_min_speeds[turnIdx];
        } else if (lap.points && lap.points.length > 0) {
          const fallbackApex = (turn.apex && turn.apex.length > 0) ? turn.apex[0] : null;
          minSpeed = getMinSpeedInRange(lap, turnStart, turnEnd, fallbackApex);
        }

        if (minSpeed !== null && minSpeed > 0) {
          xData.push(turnNames[turnIdx]);
          yData.push(minSpeed);
          textData.push(`Lap ${lap.lap_num} (${lap.session_name})<br>Min Speed: ${minSpeed.toFixed(1)} km/h`);

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
    dragmode: false,
    title: {
      text: '<b>Minimum Corner Speeds</b>',
      font: { color: '#cbd5e1', size: 19, family: 'Inter, sans-serif' },
      x: 0.02,
      xref: 'paper',
      y: 0.98,
      yref: 'paper',
      xanchor: 'left',
      yanchor: 'top'
    },
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { color: '#e2e8f0', family: 'Inter, sans-serif' },
    margin: { l: 60, r: 20, t: 38, b: 40 },
    yaxis: {
      title: 'Min Speed (km/h)',
      gridcolor: 'rgba(255,255,255,0.1)',
      zerolinecolor: 'rgba(255,255,255,0.1)',
      fixedrange: true
    },
    xaxis: { title: '', gridcolor: 'rgba(255,255,255,0.1)', fixedrange: true },
    showlegend: false,
    violinmode: 'group',
    hovermode: 'closest'
  };

  Plotly.react(gd, data, layout, { responsive: true, displayModeBar: false });
  attachTurnClickHandlers(gd);
}

/**
 * Renders the track minimap using the shared generateMinimapSvg generator.
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

  const numTurns = state.trackData.turns.length;
  const groupBDiffs = computeGroupBTurnDiffs(laps, numTurns);

  container.innerHTML = generateMinimapSvg(state.trackData, {
    turn_diffs: groupBDiffs
  });
}
