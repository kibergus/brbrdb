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
 * Plotly chart management and synchronization logic.
 */
import { state } from './state.js';
import { getSpeedAtDistance, getBrakingPointsInRange, getPointAtDistance, getPointsAtDistancesMonotonic, getTurnTime, fetchTelemetryChannel } from './telemetry.js';
import { parseLapTime, getSteeringTicks } from '../utils.js';
export { getSteeringTicks };
import { debouncedUpdateURL } from './url_sync.js';
import { updateAllPolylineColors } from './map.js';

let lastPlotUpdate = 0;

export const loadedChannels = new Set();
export const loadingChannels = new Map();

const COLUMN_MAPPINGS = {
    steering: ['Steering Wheel Angle (deg)', 'Steering Angle (deg)', 'Steering Angle', 'Steering Wheel Angle', 'Steering'],
    rps_fl: ['RPS FL', 'RPS_FL', 'WheelSpd FL', 'Wheel Speed FL'],
    rps_fr: ['RPS FR', 'RPS_FR', 'WheelSpd FR', 'Wheel Speed FR'],
    rps_rl: ['RPS RL', 'RPS_RL', 'WheelSpd RL', 'Wheel Speed RL'],
    rps_rr: ['RPS RR', 'RPS_RR', 'WheelSpd RR', 'Wheel Speed RR'],
    gx: ['GForceLon'],
    gy: ['GForceLat'],
    gyro_yaw: ['Yaw Rate'],
    gyro_pitch: ['Pitch Rate'],
    gyro_roll: ['Roll Rate'],
    sp_fl: ['Slide Pct FL', 'Slide Pct_FL', 'Slide Pct FL (%)'],
    sp_fr: ['Slide Pct FR', 'Slide Pct_FR', 'Slide Pct FR (%)'],
    sp_rl: ['Slide Pct RL', 'Slide Pct_RL', 'Slide Pct RL (%)'],
    sp_rr: ['Slide Pct RR', 'Slide Pct_RR', 'Slide Pct RR (%)'],
    lpv_lat_fl: ['Lat Patch Vel FL'],
    lpv_lat_fr: ['Lat Patch Vel FR'],
    lpv_lat_rl: ['Lat Patch Vel RL'],
    lpv_lat_rr: ['Lat Patch Vel RR'],
    lpv_lon_fl: ['Long Patch Vel FL'],
    lpv_lon_fr: ['Long Patch Vel FR'],
    lpv_lon_rl: ['Long Patch Vel RL'],
    lpv_lon_rr: ['Long Patch Vel RR'],
    lf_lat_fl: ['Lat Force FL'],
    lf_lat_fr: ['Lat Force FR'],
    lf_lat_rl: ['Lat Force RL'],
    lf_lat_rr: ['Lat Force RR'],
    lf_lon_fl: ['Long Force FL'],
    lf_lon_fr: ['Long Force FR'],
    lf_lon_rl: ['Long Force RL'],
    lf_lon_rr: ['Long Force RR'],
    tl_fl: ['Tyre Load FL'],
    tl_fr: ['Tyre Load FR'],
    tl_rl: ['Tyre Load RL'],
    tl_rr: ['Tyre Load RR'],
    sa_front: ['Slip Angle Front (deg)', 'Slip Angle Front'],
    sa_rear: ['Slip Angle Rear (deg)', 'Slip Angle Rear']
};

/**
 * Map of channel group names (tab names) to the COLUMN_MAPPINGS keys required.
 * A tab is considered available if at least one session has at least one column
 * matching any of the keys for that channel group.
 * For 'pedals', we check the base columns directly (Throttle, Brake).
 */
const CHANNEL_AVAILABILITY = {
    pedals: { baseColumns: ['Throttle', 'Brake'] },
    steering: { keys: ['steering'] },
    rps: { keys: ['rps_fl', 'rps_fr', 'rps_rl', 'rps_rr'] },
    gforce: { keys: ['gx', 'gy'] },
    gyro: { keys: ['gyro_yaw', 'gyro_pitch', 'gyro_roll'] },
    slide: { keys: ['sp_fl', 'sp_fr', 'sp_rl', 'sp_rr'] },
    patch_vel: { keys: ['lpv_lat_fl', 'lpv_lat_fr', 'lpv_lat_rl', 'lpv_lat_rr', 'lpv_lon_fl', 'lpv_lon_fr', 'lpv_lon_rl', 'lpv_lon_rr'] },
    force: { keys: ['lf_lat_fl', 'lf_lat_fr', 'lf_lat_rl', 'lf_lat_rr', 'lf_lon_fl', 'lf_lon_fr', 'lf_lon_rl', 'lf_lon_rr'] },
    tyre_load: { keys: ['tl_fl', 'tl_fr', 'tl_rl', 'tl_rr'] },
    slip_angle: { keys: ['sa_front', 'sa_rear'] }
};

export function ensureChannelLoaded(channel) {
    if (loadedChannels.has(channel)) {
        return Promise.resolve();
    }
    if (loadingChannels.has(channel)) {
        return loadingChannels.get(channel);
    }

    // If session data isn't loaded yet, return a no-op promise without
    // registering in loadingChannels. This avoids a race condition where the
    // async IIFE resolves synchronously (no awaits before the early return),
    // causing the finally-block's loadingChannels.delete() to run before the
    // loadingChannels.set() below, re-inserting a stale resolved promise that
    // permanently blocks future fetches once data is available.
    if (!state.allSessionsData || state.allSessionsData.length === 0) {
        return Promise.resolve();
    }

    const keys = {
        steering: ['steering'],
        rps: ['rps_fl', 'rps_fr', 'rps_rl', 'rps_rr'],
        gforce: ['gx', 'gy'],
        gyro: ['gyro_yaw', 'gyro_pitch', 'gyro_roll'],
        slide: ['sp_fl', 'sp_fr', 'sp_rl', 'sp_rr'],
        patch_vel: ['lpv_lat_fl', 'lpv_lat_fr', 'lpv_lat_rl', 'lpv_lat_rr', 'lpv_lon_fl', 'lpv_lon_fr', 'lpv_lon_rl', 'lpv_lon_rr'],
        force: ['lf_lat_fl', 'lf_lat_fr', 'lf_lat_rl', 'lf_lat_rr', 'lf_lon_fl', 'lf_lon_fr', 'lf_lon_rl', 'lf_lon_rr'],
        tyre_load: ['tl_fl', 'tl_fr', 'tl_rl', 'tl_rr'],
        slip_angle: ['sa_front', 'sa_rear']
    }[channel] || [];

    const promise = (async () => {
        try {
            const fetchPromises = [];
            state.allSessionsData.forEach(session => {
                keys.forEach(key => {
                    const choices = COLUMN_MAPPINGS[key] || [key];
                    const exactCol = session.columns ? session.columns.find(col =>
                        choices.some(choice => col.toLowerCase() === choice.toLowerCase())
                    ) : null;

                    if (exactCol) {
                        fetchPromises.push((async () => {
                            try {
                                const values = await fetchTelemetryChannel(session.session_id, exactCol);
                                return { session_id: session.session_id, key, values };
                            } catch (e) {
                                console.error(`Failed to fetch column ${exactCol} for session ${session.session_id}:`, e);
                                return { session_id: session.session_id, key, values: [] };
                            }
                        })());
                    } else {
                        console.warn(`Column choice not found for key ${key} in session ${session.session_id}`);
                    }
                });
            });

            const results = await Promise.all(fetchPromises);

            const sessionData = {};
            results.forEach(res => {
                sessionData[res.session_id] = sessionData[res.session_id] || {};
                sessionData[res.session_id][res.key] = res.values;
            });

            state.allSessionsData.forEach(session => {
                const sData = sessionData[session.session_id] || {};
                if (!session.laps) return;
                session.laps.forEach(lap => {
                    const lapId = `${session.session_id}-${lap.lap_num}`;
                    const targetLap = state.lapDataLookup[lapId];
                    if (targetLap && targetLap.points) {
                        for (let i = 0; i < targetLap.points.length; i++) {
                            const globalIdx = lap.start_idx + i;
                            keys.forEach(key => {
                                const vals = sData[key];
                                if (vals) {
                                    targetLap.points[i][key] = (globalIdx < vals.length) ? vals[globalIdx] : null;
                                }
                            });
                        }
                    }
                });
            });

            loadedChannels.add(channel);
        } catch (error) {
            console.error(`Failed to load channel ${channel}:`, error);
            throw error;
        } finally {
            loadingChannels.delete(channel);
        }
    })();

    loadingChannels.set(channel, promise);
    return promise;
}

/**
 * Calculates a dynamic alpha (transparency) for a lap trace based on its index
 * and the total number of laps being displayed, to ensure good visual separation.
 */
function getLapAlpha(index, total, baseAlpha = 1.0) {
    if (total <= 1) return baseAlpha;
    const minAlpha = 0.3;
    // Linearly interpolate between baseAlpha and minAlpha
    return Math.max(minAlpha, baseAlpha - (index * (baseAlpha - minAlpha) / (total - 1)));
}

/**
 * Sorts an array of lap objects by time based on the current sort mode (lap time or turn time).
 * Fastest laps come first.
 */
function sortLapsByTime(laps) {
    return laps.slice().sort((a, b) => {
        if (state.sortMode === 'turn') {
            const tA = getTurnTime(a, state.currentTurnIdx) || 999999;
            const tB = getTurnTime(b, state.currentTurnIdx) || 999999;
            return tA - tB;
        }
        return parseLapTime(a.lap_time) - parseLapTime(b.lap_time);
    });
}

export function updateAccelerationPlot(targetDist) {
    if (!state.trackData || !state.trackData.lap_length) return;

    const lapsA = state.groupAVisibleMap ? Array.from(state.groupASelection).map(id => {
        const lap = state.lapDataLookup[id];
        return lap ? { id, ...lap } : null;
    }).filter(l => l && l.points) : [];

    const lapsB = state.groupBVisibleMap ? Array.from(state.groupBSelection).map(id => {
        const lap = state.lapDataLookup[id];
        return lap ? { id, ...lap } : null;
    }).filter(l => l && l.points) : [];

    const hasAnyData = lapsA.length > 0 || lapsB.length > 0;
    const placeholder = document.getElementById('acceleration-chart-placeholder');
    const chartDiv = document.getElementById('acceleration-chart');

    if (!hasAnyData) {
        if (placeholder) {
            placeholder.style.display = 'flex';
            placeholder.innerHTML = 'Select laps and move distance slider<br>to see acceleration vs speed';
        }
        if (chartDiv) chartDiv.style.display = 'none';
        return;
    }

    if (placeholder) placeholder.style.display = 'none';
    if (chartDiv) chartDiv.style.display = 'block';

    const data = [];
    const lapLength = state.trackData.lap_length;

    // Group A traces
    lapsA.forEach((lap, idx) => {
        const filteredPoints = [];
        for (let j = 0; j < lap.points.length; j++) {
            const p = lap.points[j];
            let distDiff = p.dist - targetDist;
            if (distDiff > lapLength / 2) distDiff -= lapLength;
            else if (distDiff < -lapLength / 2) distDiff += lapLength;

            if (Math.abs(distDiff) <= 20) {
                filteredPoints.push({ p, distDiff });
            }
        }

        filteredPoints.sort((a, b) => a.distDiff - b.distDiff);

        if (filteredPoints.length > 0) {
            const x = filteredPoints.map(item => item.p.speed);
            const y = filteredPoints.map(item => item.p.acceleration || 0);
            const alpha = getLapAlpha(idx, lapsA.length);

            data.push({
                x: x,
                y: y,
                mode: 'lines+markers',
                name: `Lap ${lap.lap_num}`,
                hoverinfo: 'skip',
                line: { color: `rgba(251, 146, 60, ${alpha})`, width: 0.8 },
                marker: { size: 3, color: `rgba(251, 146, 60, ${alpha})` }
            });

            // Highlight current cursor position (point closest to distDiff = 0)
            let minDiffIndex = -1;
            let minDiffVal = Infinity;
            filteredPoints.forEach((item, fIdx) => {
                const absDiff = Math.abs(item.distDiff);
                if (absDiff < minDiffVal) {
                    minDiffVal = absDiff;
                    minDiffIndex = fIdx;
                }
            });

            if (minDiffIndex !== -1 && filteredPoints[minDiffIndex]) {
                const curP = filteredPoints[minDiffIndex].p;
                data.push({
                    x: [curP.speed],
                    y: [curP.acceleration || 0],
                    mode: 'markers',
                    name: `Lap ${lap.lap_num} Current`,
                    hoverinfo: 'skip',
                    showlegend: false,
                    marker: {
                        size: 10,
                        color: `rgba(251, 146, 60, 1.0)`,
                        line: { color: '#ffffff', width: 2 }
                    }
                });
            }
        }
    });

    // Group B traces
    lapsB.forEach((lap, idx) => {
        const filteredPoints = [];
        for (let j = 0; j < lap.points.length; j++) {
            const p = lap.points[j];
            let distDiff = p.dist - targetDist;
            if (distDiff > lapLength / 2) distDiff -= lapLength;
            else if (distDiff < -lapLength / 2) distDiff += lapLength;

            if (Math.abs(distDiff) <= 20) {
                filteredPoints.push({ p, distDiff });
            }
        }

        filteredPoints.sort((a, b) => a.distDiff - b.distDiff);

        if (filteredPoints.length > 0) {
            const x = filteredPoints.map(item => item.p.speed);
            const y = filteredPoints.map(item => item.p.acceleration || 0);
            const alpha = getLapAlpha(idx, lapsB.length, 0.8);

            data.push({
                x: x,
                y: y,
                mode: 'lines+markers',
                name: `Lap ${lap.lap_num} (B)`,
                hoverinfo: 'skip',
                line: { color: `rgba(56, 189, 248, ${alpha})`, width: 0.6, dash: 'dash' },
                marker: { size: 3, color: `rgba(56, 189, 248, ${alpha})` }
            });

            // Highlight current cursor position (point closest to distDiff = 0)
            let minDiffIndex = -1;
            let minDiffVal = Infinity;
            filteredPoints.forEach((item, fIdx) => {
                const absDiff = Math.abs(item.distDiff);
                if (absDiff < minDiffVal) {
                    minDiffVal = absDiff;
                    minDiffIndex = fIdx;
                }
            });

            if (minDiffIndex !== -1 && filteredPoints[minDiffIndex]) {
                const curP = filteredPoints[minDiffIndex].p;
                data.push({
                    x: [curP.speed],
                    y: [curP.acceleration || 0],
                    mode: 'markers',
                    name: `Lap ${lap.lap_num} (B) Current`,
                    hoverinfo: 'skip',
                    showlegend: false,
                    marker: {
                        size: 10,
                        color: `rgba(56, 189, 248, 1.0)`,
                        line: { color: '#ffffff', width: 2 }
                    }
                });
            }
        }
    });

    // Average line trace
    const allShownLaps = [...lapsA, ...lapsB];
    if (allShownLaps.length > 0) {
        const avgGridPoints = [];
        for (let o = -20; o <= 20; o += 0.5) {
            let absoluteDist = targetDist + o;
            if (absoluteDist < 0) {
                absoluteDist += lapLength;
            } else if (absoluteDist >= lapLength) {
                absoluteDist -= lapLength;
            }

            const speeds = [];
            const accelerations = [];
            const throttles = [];
            const brakes = [];

            allShownLaps.forEach(lap => {
                const p = getPointAtDistance(lap, absoluteDist, ['speed', 'acceleration', 'throttle', 'brake']);
                if (p && typeof p.speed === 'number') {
                    speeds.push(p.speed);
                    accelerations.push(p.acceleration || 0);
                    throttles.push(p.throttle || 0);
                    brakes.push(p.brake || 0);
                }
            });

            if (speeds.length > 0) {
                const avgSpeed = speeds.reduce((sum, v) => sum + v, 0) / speeds.length;
                const avgAcc = accelerations.reduce((sum, v) => sum + v, 0) / accelerations.length;
                const avgThrottle = throttles.reduce((sum, v) => sum + v, 0) / throttles.length;
                const avgBrake = brakes.reduce((sum, v) => sum + v, 0) / brakes.length;
                avgGridPoints.push({ speed: avgSpeed, acceleration: avgAcc, throttle: avgThrottle, brake: avgBrake });
            }
        }

        if (avgGridPoints.length > 0) {
            // Apply 11-point moving average smoothing (-5 to +5)
            const smoothedPoints = [];
            for (let i = 0; i < avgGridPoints.length; i++) {
                let sumSpeed = 0;
                let sumAcc = 0;
                let sumThrottle = 0;
                let sumBrake = 0;
                let count = 0;
                for (let k = -5; k <= 5; k++) {
                    const idx = i + k;
                    if (idx >= 0 && idx < avgGridPoints.length) {
                        sumSpeed += avgGridPoints[idx].speed;
                        sumAcc += avgGridPoints[idx].acceleration;
                        sumThrottle += avgGridPoints[idx].throttle;
                        sumBrake += avgGridPoints[idx].brake;
                        count++;
                    }
                }
                smoothedPoints.push({
                    speed: sumSpeed / count,
                    acceleration: sumAcc / count,
                    throttle: sumThrottle / count,
                    brake: sumBrake / count
                });
            }

            // Draw color-encoded segments
            for (let i = 0; i < smoothedPoints.length - 1; i++) {
                const pt1 = smoothedPoints[i];
                const pt2 = smoothedPoints[i + 1];

                let color = '#ffffff';
                if (pt1.brake > 1.0) {
                    const intensity = Math.pow(pt1.brake / 100, 0.2);
                    const factor = 1 - intensity;
                    const gb = Math.round(255 * factor);
                    color = `rgb(255, ${gb}, ${gb})`;
                } else if (pt1.throttle > 1.0) {
                    const factor = (100 - pt1.throttle) / 100;
                    const rb = Math.round(255 * factor);
                    color = `rgb(${rb}, 255, ${rb})`;
                }

                data.push({
                    x: [pt1.speed, pt2.speed],
                    y: [pt1.acceleration, pt2.acceleration],
                    mode: 'lines',
                    hoverinfo: 'text',
                    text: [
                        `Speed: ${pt1.speed.toFixed(1)} km/h<br>Avg Acc: ${pt1.acceleration.toFixed(2)} m/s²<br>Throttle: ${pt1.throttle.toFixed(0)}%<br>Brake: ${pt1.brake.toFixed(0)}%`,
                        `Speed: ${pt2.speed.toFixed(1)} km/h<br>Avg Acc: ${pt2.acceleration.toFixed(2)} m/s²<br>Throttle: ${pt2.throttle.toFixed(0)}%<br>Brake: ${pt2.brake.toFixed(0)}%`
                    ],
                    line: {
                        color: color,
                        width: 4
                    },
                    showlegend: false
                });
            }
        }
    }

    const config = { responsive: true, displayModeBar: false };
    const layout = {
        autosize: true,
        uirevision: true,
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        showlegend: false,
        margin: { t: 30, b: 35, l: 45, r: 15 },
        hovermode: 'closest',
        xaxis: {
            title: 'Speed (km/h)',
            color: '#94a3b8',
            titlefont: { size: 10 },
            gridcolor: 'rgba(255,255,255,0.05)',
            zeroline: false
        },
        yaxis: {
            title: 'Acceleration (m/s²)',
            color: '#94a3b8',
            titlefont: { size: 10 },
            gridcolor: 'rgba(255,255,255,0.05)',
            zeroline: true,
            zerolinecolor: 'rgba(255,255,255,0.1)'
        },
        annotations: [
            {
                text: `Acceleration vs Speed (±20m at ${targetDist.toFixed(0)}m)`,
                xref: 'paper', yref: 'paper', x: 0, y: 1.05,
                showarrow: false,
                font: { size: 11, color: '#94a3b8' },
                xanchor: 'left'
            }
        ]
    };

    Plotly.react('acceleration-chart', data, layout, config);
}

export function updateSlipAnglePlot(targetDist) {
    if (!state.trackData || !state.trackData.lap_length) return;

    const placeholder = document.getElementById('slip_angle-chart-placeholder');
    const chartDiv = document.getElementById('slip_angle-chart');

    const activeColorMode = state.slipAngleColorMode || 'brake_throttle';
    const channelsToLoad = ['slip_angle', 'force'];
    if (activeColorMode === 'longitudinal_accel') {
        channelsToLoad.push('gforce');
    } else if (activeColorMode === 'weight_transfer') {
        channelsToLoad.push('tyre_load');
    }

    const needsLoad = channelsToLoad.some(ch => !loadedChannels.has(ch));
    if (needsLoad) {
        if (placeholder) {
            placeholder.style.display = 'flex';
            placeholder.innerHTML = `
                <div style="display: flex; flex-direction: column; align-items: center; gap: 8px;">
                    <div class="loading-spinner" style="width: 24px; height: 24px; border: 2px solid rgba(255,255,255,0.1); border-top-color: #38bdf8; border-radius: 50%; animation: spin 1s linear infinite;"></div>
                    <div>Loading tyre telemetry...</div>
                </div>
            `;
        }
        if (chartDiv) chartDiv.style.display = 'none';

        Promise.all(channelsToLoad.map(ch => ensureChannelLoaded(ch)))
            .then(() => {
                if (state.activeRightTab === 'slip_angle') {
                    updateSlipAnglePlot(targetDist);
                }
            }).catch(err => {
                console.error("Error loading telemetry channels:", err);
                if (placeholder) {
                    placeholder.innerHTML = '<div style="color: var(--warning);">Error loading telemetry data</div>';
                }
            });
        return;
    }

    const lapsA = state.groupAVisibleMap ? Array.from(state.groupASelection).map(id => {
        const lap = state.lapDataLookup[id];
        return lap ? { id, ...lap } : null;
    }).filter(l => l && l.points) : [];

    const lapsB = state.groupBVisibleMap ? Array.from(state.groupBSelection).map(id => {
        const lap = state.lapDataLookup[id];
        return lap ? { id, ...lap } : null;
    }).filter(l => l && l.points) : [];

    const hasAnyData = lapsA.length > 0 || lapsB.length > 0;

    if (!hasAnyData) {
        if (placeholder) {
            placeholder.style.display = 'flex';
            placeholder.innerHTML = 'Select laps and move distance slider<br>to see slip angle vs lateral force';
        }
        if (chartDiv) chartDiv.style.display = 'none';
        return;
    }

    if (placeholder) placeholder.style.display = 'none';
    if (chartDiv) chartDiv.style.display = 'block';

    const data = [];
    const lapLength = state.trackData.lap_length;

    // Group A traces
    lapsA.forEach((lap, idx) => {
        const filteredPoints = [];
        for (let j = 0; j < lap.points.length; j++) {
            const p = lap.points[j];
            let distDiff = p.dist - targetDist;
            if (distDiff > lapLength / 2) distDiff -= lapLength;
            else if (distDiff < -lapLength / 2) distDiff += lapLength;

            if (Math.abs(distDiff) <= 20) {
                filteredPoints.push({ p, distDiff });
            }
        }

        filteredPoints.sort((a, b) => a.distDiff - b.distDiff);

        if (filteredPoints.length > 0) {
            const alpha = getLapAlpha(idx, lapsA.length);

            // Front subplot
            data.push({
                x: filteredPoints.map(item => item.p.sa_front || 0),
                y: filteredPoints.map(item => (item.p.lf_lat_fl || 0) + (item.p.lf_lat_fr || 0)),
                mode: 'lines+markers',
                name: `Lap ${lap.lap_num} Front`,
                hoverinfo: 'skip',
                xaxis: 'x',
                yaxis: 'y',
                line: { color: `rgba(251, 146, 60, ${alpha})`, width: 0.8 },
                marker: { size: 3, color: `rgba(251, 146, 60, ${alpha})` }
            });

            // Rear subplot
            data.push({
                x: filteredPoints.map(item => item.p.sa_rear || 0),
                y: filteredPoints.map(item => (item.p.lf_lat_rl || 0) + (item.p.lf_lat_rr || 0)),
                mode: 'lines+markers',
                name: `Lap ${lap.lap_num} Rear`,
                hoverinfo: 'skip',
                xaxis: 'x2',
                yaxis: 'y2',
                line: { color: `rgba(251, 146, 60, ${alpha})`, width: 0.8 },
                marker: { size: 3, color: `rgba(251, 146, 60, ${alpha})` }
            });

            // Highlight current cursor position (point closest to distDiff = 0)
            let minDiffIndex = -1;
            let minDiffVal = Infinity;
            filteredPoints.forEach((item, fIdx) => {
                const absDiff = Math.abs(item.distDiff);
                if (absDiff < minDiffVal) {
                    minDiffVal = absDiff;
                    minDiffIndex = fIdx;
                }
            });

            if (minDiffIndex !== -1 && filteredPoints[minDiffIndex]) {
                const curP = filteredPoints[minDiffIndex].p;
                // Front Highlight
                data.push({
                    x: [curP.sa_front || 0],
                    y: [(curP.lf_lat_fl || 0) + (curP.lf_lat_fr || 0)],
                    mode: 'markers',
                    name: `Lap ${lap.lap_num} Front Current`,
                    hoverinfo: 'skip',
                    showlegend: false,
                    xaxis: 'x',
                    yaxis: 'y',
                    marker: {
                        size: 10,
                        color: `rgba(251, 146, 60, 1.0)`,
                        line: { color: '#ffffff', width: 2 }
                    }
                });

                // Rear Highlight
                data.push({
                    x: [curP.sa_rear || 0],
                    y: [(curP.lf_lat_rl || 0) + (curP.lf_lat_rr || 0)],
                    mode: 'markers',
                    name: `Lap ${lap.lap_num} Rear Current`,
                    hoverinfo: 'skip',
                    showlegend: false,
                    xaxis: 'x2',
                    yaxis: 'y2',
                    marker: {
                        size: 10,
                        color: `rgba(251, 146, 60, 1.0)`,
                        line: { color: '#ffffff', width: 2 }
                    }
                });
            }
        }
    });

    // Group B traces
    lapsB.forEach((lap, idx) => {
        const filteredPoints = [];
        for (let j = 0; j < lap.points.length; j++) {
            const p = lap.points[j];
            let distDiff = p.dist - targetDist;
            if (distDiff > lapLength / 2) distDiff -= lapLength;
            else if (distDiff < -lapLength / 2) distDiff += lapLength;

            if (Math.abs(distDiff) <= 20) {
                filteredPoints.push({ p, distDiff });
            }
        }

        filteredPoints.sort((a, b) => a.distDiff - b.distDiff);

        if (filteredPoints.length > 0) {
            const alpha = getLapAlpha(idx, lapsB.length, 0.8);

            // Front subplot
            data.push({
                x: filteredPoints.map(item => item.p.sa_front || 0),
                y: filteredPoints.map(item => (item.p.lf_lat_fl || 0) + (item.p.lf_lat_fr || 0)),
                mode: 'lines+markers',
                name: `Lap ${lap.lap_num} Front (B)`,
                hoverinfo: 'skip',
                xaxis: 'x',
                yaxis: 'y',
                line: { color: `rgba(56, 189, 248, ${alpha})`, width: 0.6, dash: 'dash' },
                marker: { size: 3, color: `rgba(56, 189, 248, ${alpha})` }
            });

            // Rear subplot
            data.push({
                x: filteredPoints.map(item => item.p.sa_rear || 0),
                y: filteredPoints.map(item => (item.p.lf_lat_rl || 0) + (item.p.lf_lat_rr || 0)),
                mode: 'lines+markers',
                name: `Lap ${lap.lap_num} Rear (B)`,
                hoverinfo: 'skip',
                xaxis: 'x2',
                yaxis: 'y2',
                line: { color: `rgba(56, 189, 248, ${alpha})`, width: 0.6, dash: 'dash' },
                marker: { size: 3, color: `rgba(56, 189, 248, ${alpha})` }
            });

            // Highlight current cursor position (point closest to distDiff = 0)
            let minDiffIndex = -1;
            let minDiffVal = Infinity;
            filteredPoints.forEach((item, fIdx) => {
                const absDiff = Math.abs(item.distDiff);
                if (absDiff < minDiffVal) {
                    minDiffVal = absDiff;
                    minDiffIndex = fIdx;
                }
            });

            if (minDiffIndex !== -1 && filteredPoints[minDiffIndex]) {
                const curP = filteredPoints[minDiffIndex].p;
                // Front Highlight
                data.push({
                    x: [curP.sa_front || 0],
                    y: [(curP.lf_lat_fl || 0) + (curP.lf_lat_fr || 0)],
                    mode: 'markers',
                    name: `Lap ${lap.lap_num} Front (B) Current`,
                    hoverinfo: 'skip',
                    showlegend: false,
                    xaxis: 'x',
                    yaxis: 'y',
                    marker: {
                        size: 10,
                        color: `rgba(56, 189, 248, 1.0)`,
                        line: { color: '#ffffff', width: 2 }
                    }
                });

                // Rear Highlight
                data.push({
                    x: [curP.sa_rear || 0],
                    y: [(curP.lf_lat_rl || 0) + (curP.lf_lat_rr || 0)],
                    mode: 'markers',
                    name: `Lap ${lap.lap_num} Rear (B) Current`,
                    hoverinfo: 'skip',
                    showlegend: false,
                    xaxis: 'x2',
                    yaxis: 'y2',
                    marker: {
                        size: 10,
                        color: `rgba(56, 189, 248, 1.0)`,
                        line: { color: '#ffffff', width: 2 }
                    }
                });
            }
        }
    });

    // Average line trace
    const allShownLaps = [...lapsA, ...lapsB];
    if (allShownLaps.length > 0) {
        const avgGridPoints = [];
        for (let o = -20; o <= 20; o += 0.5) {
            let absoluteDist = targetDist + o;
            if (absoluteDist < 0) {
                absoluteDist += lapLength;
            } else if (absoluteDist >= lapLength) {
                absoluteDist -= lapLength;
            }

            const saFronts = [];
            const forceFronts = [];
            const saRears = [];
            const forceRears = [];
            const throttles = [];
            const brakes = [];
            const gForces = [];
            const tlFLs = [];
            const tlFRs = [];
            const tlRLs = [];
            const tlRRs = [];

            const keysToQuery = ['sa_front', 'sa_rear', 'lf_lat_fl', 'lf_lat_fr', 'lf_lat_rl', 'lf_lat_rr'];
            if (activeColorMode === 'brake_throttle') {
                keysToQuery.push('throttle', 'brake');
            } else if (activeColorMode === 'longitudinal_accel') {
                keysToQuery.push('gx');
            } else if (activeColorMode === 'weight_transfer') {
                keysToQuery.push('tl_fl', 'tl_fr', 'tl_rl', 'tl_rr');
            }

            allShownLaps.forEach(lap => {
                const p = getPointAtDistance(lap, absoluteDist, keysToQuery);
                if (p) {
                    saFronts.push(p.sa_front || 0);
                    forceFronts.push((p.lf_lat_fl || 0) + (p.lf_lat_fr || 0));
                    saRears.push(p.sa_rear || 0);
                    forceRears.push((p.lf_lat_rl || 0) + (p.lf_lat_rr || 0));

                    if (activeColorMode === 'brake_throttle') {
                        throttles.push(p.throttle || 0);
                        brakes.push(p.brake || 0);
                    } else if (activeColorMode === 'longitudinal_accel') {
                        gForces.push(p.gx || 0);
                    } else if (activeColorMode === 'weight_transfer') {
                        tlFLs.push(p.tl_fl || 0);
                        tlFRs.push(p.tl_fr || 0);
                        tlRLs.push(p.tl_rl || 0);
                        tlRRs.push(p.tl_rr || 0);
                    }
                }
            });

            if (saFronts.length > 0) {
                const avgSaFront = saFronts.reduce((sum, v) => sum + v, 0) / saFronts.length;
                const avgForceFront = forceFronts.reduce((sum, v) => sum + v, 0) / forceFronts.length;
                const avgSaRear = saRears.reduce((sum, v) => sum + v, 0) / saRears.length;
                const avgForceRear = forceRears.reduce((sum, v) => sum + v, 0) / forceRears.length;

                const gridPt = {
                    saFront: avgSaFront,
                    forceFront: avgForceFront,
                    saRear: avgSaRear,
                    forceRear: avgForceRear
                };

                if (activeColorMode === 'brake_throttle') {
                    gridPt.throttle = throttles.reduce((sum, v) => sum + v, 0) / throttles.length;
                    gridPt.brake = brakes.reduce((sum, v) => sum + v, 0) / brakes.length;
                } else if (activeColorMode === 'longitudinal_accel') {
                    gridPt.gx = gForces.reduce((sum, v) => sum + v, 0) / gForces.length;
                } else if (activeColorMode === 'weight_transfer') {
                    gridPt.tl_fl = tlFLs.reduce((sum, v) => sum + v, 0) / tlFLs.length;
                    gridPt.tl_fr = tlFRs.reduce((sum, v) => sum + v, 0) / tlFRs.length;
                    gridPt.tl_rl = tlRLs.reduce((sum, v) => sum + v, 0) / tlRLs.length;
                    gridPt.tl_rr = tlRRs.reduce((sum, v) => sum + v, 0) / tlRRs.length;
                }

                avgGridPoints.push(gridPt);
            }
        }

        if (avgGridPoints.length > 0) {
            // Apply 11-point moving average smoothing (-5 to +5)
            const smoothedPoints = [];
            for (let i = 0; i < avgGridPoints.length; i++) {
                let sumSaFront = 0;
                let sumForceFront = 0;
                let sumSaRear = 0;
                let sumForceRear = 0;

                let sumThrottle = 0;
                let sumBrake = 0;
                let sumGx = 0;
                let sumTlFL = 0;
                let sumTlFR = 0;
                let sumTlRL = 0;
                let sumTlRR = 0;

                let count = 0;
                for (let k = -5; k <= 5; k++) {
                    const idx = i + k;
                    if (idx >= 0 && idx < avgGridPoints.length) {
                        sumSaFront += avgGridPoints[idx].saFront;
                        sumForceFront += avgGridPoints[idx].forceFront;
                        sumSaRear += avgGridPoints[idx].saRear;
                        sumForceRear += avgGridPoints[idx].forceRear;

                        if (activeColorMode === 'brake_throttle') {
                            sumThrottle += avgGridPoints[idx].throttle;
                            sumBrake += avgGridPoints[idx].brake;
                        } else if (activeColorMode === 'longitudinal_accel') {
                            sumGx += avgGridPoints[idx].gx;
                        } else if (activeColorMode === 'weight_transfer') {
                            sumTlFL += avgGridPoints[idx].tl_fl;
                            sumTlFR += avgGridPoints[idx].tl_fr;
                            sumTlRL += avgGridPoints[idx].tl_rl;
                            sumTlRR += avgGridPoints[idx].tl_rr;
                        }
                        count++;
                    }
                }

                const smoothedPt = {
                    saFront: sumSaFront / count,
                    forceFront: sumForceFront / count,
                    saRear: sumSaRear / count,
                    forceRear: sumForceRear / count
                };

                if (activeColorMode === 'brake_throttle') {
                    smoothedPt.throttle = sumThrottle / count;
                    smoothedPt.brake = sumBrake / count;
                } else if (activeColorMode === 'longitudinal_accel') {
                    smoothedPt.gx = sumGx / count;
                } else if (activeColorMode === 'weight_transfer') {
                    smoothedPt.tl_fl = sumTlFL / count;
                    smoothedPt.tl_fr = sumTlFR / count;
                    smoothedPt.tl_rl = sumTlRL / count;
                    smoothedPt.tl_rr = sumTlRR / count;
                    smoothedPt.loadFront = smoothedPt.tl_fl + smoothedPt.tl_fr;
                    smoothedPt.loadRear = smoothedPt.tl_rl + smoothedPt.tl_rr;
                }

                smoothedPoints.push(smoothedPt);
            }

            // Calculate baseline and max dev for weight transfer mode
            let meanFrontLoad = 0;
            let meanRearLoad = 0;
            let maxDevFront = 1.0;
            let maxDevRear = 1.0;

            if (activeColorMode === 'weight_transfer') {
                const totalF = smoothedPoints.reduce((sum, pt) => sum + pt.loadFront, 0);
                meanFrontLoad = totalF / smoothedPoints.length;
                const totalR = smoothedPoints.reduce((sum, pt) => sum + pt.loadRear, 0);
                meanRearLoad = totalR / smoothedPoints.length;

                smoothedPoints.forEach(pt => {
                    const devF = Math.abs(pt.loadFront - meanFrontLoad);
                    if (devF > maxDevFront) maxDevFront = devF;
                    const devR = Math.abs(pt.loadRear - meanRearLoad);
                    if (devR > maxDevRear) maxDevRear = devR;
                });
            }

            // Draw color-encoded segments
            for (let i = 0; i < smoothedPoints.length - 1; i++) {
                const pt1 = smoothedPoints[i];
                const pt2 = smoothedPoints[i + 1];

                let colorFront = '#ffffff';
                let colorRear = '#ffffff';
                let textFront = '';
                let textRear = '';

                if (activeColorMode === 'brake_throttle') {
                    let color = '#ffffff';
                    if (pt1.brake > 1.0) {
                        const intensity = Math.pow(pt1.brake / 100, 0.2);
                        const factor = 1 - intensity;
                        const gb = Math.round(255 * factor);
                        color = `rgb(255, ${gb}, ${gb})`;
                    } else if (pt1.throttle > 1.0) {
                        const factor = (100 - pt1.throttle) / 100;
                        const rb = Math.round(255 * factor);
                        color = `rgb(${rb}, 255, ${rb})`;
                    }
                    colorFront = color;
                    colorRear = color;

                    textFront = [
                        `Front Slip Angle: ${pt1.saFront.toFixed(2)} deg<br>Avg Force: ${pt1.forceFront.toFixed(1)} N<br>Throttle: ${pt1.throttle.toFixed(0)}%<br>Brake: ${pt1.brake.toFixed(0)}%`,
                        `Front Slip Angle: ${pt2.saFront.toFixed(2)} deg<br>Avg Force: ${pt2.forceFront.toFixed(1)} N<br>Throttle: ${pt2.throttle.toFixed(0)}%<br>Brake: ${pt2.brake.toFixed(0)}%`
                    ];
                    textRear = [
                        `Rear Slip Angle: ${pt1.saRear.toFixed(2)} deg<br>Avg Force: ${pt1.forceRear.toFixed(1)} N<br>Throttle: ${pt1.throttle.toFixed(0)}%<br>Brake: ${pt1.brake.toFixed(0)}%`,
                        `Rear Slip Angle: ${pt2.saRear.toFixed(2)} deg<br>Avg Force: ${pt2.forceRear.toFixed(1)} N<br>Throttle: ${pt2.throttle.toFixed(0)}%<br>Brake: ${pt2.brake.toFixed(0)}%`
                    ];
                } else if (activeColorMode === 'longitudinal_accel') {
                    let color = '#ffffff';
                    if (pt1.gx < 0) {
                        const factor = Math.min(1.0, -pt1.gx / 1.5);
                        const gb = Math.round(255 * (1 - factor));
                        color = `rgb(255, ${gb}, ${gb})`;
                    } else if (pt1.gx > 0) {
                        const factor = Math.min(1.0, pt1.gx / 0.8);
                        const rb = Math.round(255 * (1 - factor));
                        color = `rgb(${rb}, 255, ${rb})`;
                    }
                    colorFront = color;
                    colorRear = color;

                    textFront = [
                        `Front Slip Angle: ${pt1.saFront.toFixed(2)} deg<br>Avg Force: ${pt1.forceFront.toFixed(1)} N<br>Long Accel: ${pt1.gx.toFixed(2)} G`,
                        `Front Slip Angle: ${pt2.saFront.toFixed(2)} deg<br>Avg Force: ${pt2.forceFront.toFixed(1)} N<br>Long Accel: ${pt2.gx.toFixed(2)} G`
                    ];
                    textRear = [
                        `Rear Slip Angle: ${pt1.saRear.toFixed(2)} deg<br>Avg Force: ${pt1.forceRear.toFixed(1)} N<br>Long Accel: ${pt1.gx.toFixed(2)} G`,
                        `Rear Slip Angle: ${pt2.saRear.toFixed(2)} deg<br>Avg Force: ${pt2.forceRear.toFixed(1)} N<br>Long Accel: ${pt2.gx.toFixed(2)} G`
                    ];
                } else if (activeColorMode === 'weight_transfer') {
                    const diffF = pt1.loadFront - meanFrontLoad;
                    const factorF = Math.min(1.0, Math.abs(diffF) / maxDevFront);
                    const valF = Math.round(255 * (1 - factorF));
                    if (diffF > 0) {
                        colorFront = `rgb(255, ${valF}, ${valF})`;
                    } else {
                        colorFront = `rgb(${valF}, 255, ${valF})`;
                    }

                    const diffR = pt1.loadRear - meanRearLoad;
                    const factorR = Math.min(1.0, Math.abs(diffR) / maxDevRear);
                    const valR = Math.round(255 * (1 - factorR));
                    if (diffR > 0) {
                        colorRear = `rgb(${valR}, 255, ${valR})`;
                    } else {
                        colorRear = `rgb(255, ${valR}, ${valR})`;
                    }

                    textFront = [
                        `Front Slip Angle: ${pt1.saFront.toFixed(2)} deg<br>Avg Force: ${pt1.forceFront.toFixed(1)} N<br>Front Load: ${pt1.loadFront.toFixed(0)} N`,
                        `Front Slip Angle: ${pt2.saFront.toFixed(2)} deg<br>Avg Force: ${pt2.forceFront.toFixed(1)} N<br>Front Load: ${pt2.loadFront.toFixed(0)} N`
                    ];
                    textRear = [
                        `Rear Slip Angle: ${pt1.saRear.toFixed(2)} deg<br>Avg Force: ${pt1.forceRear.toFixed(1)} N<br>Rear Load: ${pt1.loadRear.toFixed(0)} N`,
                        `Rear Slip Angle: ${pt2.saRear.toFixed(2)} deg<br>Avg Force: ${pt2.forceRear.toFixed(1)} N<br>Rear Load: ${pt2.loadRear.toFixed(0)} N`
                    ];
                }

                // Front Average Segment Trace
                data.push({
                    x: [pt1.saFront, pt2.saFront],
                    y: [pt1.forceFront, pt2.forceFront],
                    mode: 'lines',
                    hoverinfo: 'text',
                    text: textFront,
                    xaxis: 'x',
                    yaxis: 'y',
                    line: {
                        color: colorFront,
                        width: 4
                    },
                    showlegend: false
                });

                // Rear Average Segment Trace
                data.push({
                    x: [pt1.saRear, pt2.saRear],
                    y: [pt1.forceRear, pt2.forceRear],
                    mode: 'lines',
                    hoverinfo: 'text',
                    text: textRear,
                    xaxis: 'x2',
                    yaxis: 'y2',
                    line: {
                        color: colorRear,
                        width: 4
                    },
                    showlegend: false
                });
            }
        }
    }

    const config = { responsive: true, displayModeBar: false };
    const layout = {
        autosize: true,
        uirevision: true,
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        showlegend: false,
        margin: { t: 30, b: 35, l: 45, r: 15 },
        hovermode: 'closest',

        // Front subplot
        xaxis: {
            title: 'Front Slip Angle (deg)',
            color: '#94a3b8',
            titlefont: { size: 10 },
            gridcolor: 'rgba(255,255,255,0.05)',
            zeroline: true,
            zerolinecolor: 'rgba(255,255,255,0.1)'
        },
        yaxis: {
            title: 'Front Force (N)',
            color: '#94a3b8',
            titlefont: { size: 10 },
            gridcolor: 'rgba(255,255,255,0.05)',
            zeroline: true,
            zerolinecolor: 'rgba(255,255,255,0.1)',
            domain: [0.58, 1.0]
        },

        // Rear subplot
        xaxis2: {
            title: 'Rear Slip Angle (deg)',
            color: '#94a3b8',
            titlefont: { size: 10 },
            gridcolor: 'rgba(255,255,255,0.05)',
            zeroline: true,
            zerolinecolor: 'rgba(255,255,255,0.1)',
            anchor: 'y2'
        },
        yaxis2: {
            title: 'Rear Force (N)',
            color: '#94a3b8',
            titlefont: { size: 10 },
            gridcolor: 'rgba(255,255,255,0.05)',
            zeroline: true,
            zerolinecolor: 'rgba(255,255,255,0.1)',
            domain: [0.0, 0.42]
        },

        annotations: [
            {
                text: `Front Tyres (±20m at ${targetDist.toFixed(0)}m)`,
                xref: 'paper', yref: 'paper', x: 0, y: 1.05,
                showarrow: false,
                font: { size: 11, color: '#94a3b8' },
                xanchor: 'left'
            },
            {
                text: `Rear Tyres (±20m at ${targetDist.toFixed(0)}m)`,
                xref: 'paper', yref: 'paper', x: 0, y: 0.48,
                showarrow: false,
                font: { size: 11, color: '#94a3b8' },
                xanchor: 'left'
            }
        ]
    };

    Plotly.react('slip_angle-chart', data, layout, config);
}

export function updateTelemetryPlots(targetDist) {
    if (state.activeRightTab === 'acceleration') {
        updateAccelerationPlot(targetDist);
        return;
    }
    if (state.activeRightTab === 'slip_angle') {
        updateSlipAnglePlot(targetDist);
        return;
    }

    const now = Date.now();
    if (now - lastPlotUpdate < 100) return;
    lastPlotUpdate = now;

    const speedValsA = [];
    const speedLapsA = [];
    const speedTimesA = [];
    const speedValsB = [];
    const speedLapsB = [];
    const speedTimesB = [];
    const brakingValsA = [];
    const brakingLapsA = [];
    const brakingTimesA = [];
    const brakingValsB = [];
    const brakingLapsB = [];
    const brakingTimesB = [];

    state.lapToPlotIndices.speed = {};
    state.lapToPlotIndices.braking = {};

    const selectedIds = new Set([...state.groupASelection, ...state.groupBSelection]);
    const lapTimeMap = {};
    let minTime = Infinity;
    let maxTime = -Infinity;

    selectedIds.forEach(id => {
        const lap = state.lapDataLookup[id];
        if (lap) {
            const t = parseLapTime(lap.lap_time);
            lapTimeMap[id] = t;
            if (t < minTime) minTime = t;
            if (t > maxTime) maxTime = t;
        }
    });

    // Process Group A Speed
    state.groupASelection.forEach(lapId => {
        const lap = state.lapDataLookup[lapId];
        if (lap) {
            const s = getSpeedAtDistance(lap, targetDist);
            if (s !== null) {
                speedValsA.push(s);
                speedLapsA.push(`Lap ${lap.lap_num}`);
                speedTimesA.push(lapTimeMap[lapId]);
            }
        }
    });

    // Process Group B Speed
    if (state.groupBSelection.size > 0) {
        state.groupBSelection.forEach(lapId => {
            const lap = state.lapDataLookup[lapId];
            if (lap) {
                const s = getSpeedAtDistance(lap, targetDist);
                if (s !== null) {
                    speedValsB.push(s);
                    speedLapsB.push(`Lap ${lap.lap_num}`);
                    speedTimesB.push(lapTimeMap[lapId]);
                }
            }
        });
    }

    // Process Group A Braking
    state.groupASelection.forEach(lapId => {
        const lap = state.lapDataLookup[lapId];
        if (lap) {
            const bps = getBrakingPointsInRange(lap, targetDist, 20);
            bps.forEach(bp => {
                brakingValsA.push(bp);
                brakingLapsA.push(`Lap ${lap.lap_num}`);
                brakingTimesA.push(lapTimeMap[lapId]);
            });
        }
    });

    // Process Group B Braking
    if (state.groupBSelection.size > 0) {
        state.groupBSelection.forEach(lapId => {
            const lap = state.lapDataLookup[lapId];
            if (lap) {
                const bps = getBrakingPointsInRange(lap, targetDist, 20);
                bps.forEach(bp => {
                    brakingValsB.push(bp);
                    brakingLapsB.push(`Lap ${lap.lap_num}`);
                    brakingTimesB.push(lapTimeMap[lapId]);
                });
            }
        });
    }

    const hasAnyData = speedValsA.length > 0 || speedValsB.length > 0 || brakingValsA.length > 0 || brakingValsB.length > 0;
    const placeholder = document.getElementById('telemetry-chart-placeholder');
    const chartDiv = document.getElementById('telemetry-chart');

    if (!hasAnyData) {
        if (placeholder) {
            placeholder.style.display = 'flex';
            placeholder.innerHTML = 'Select laps and move distance slider<br>to see speed and braking analysis';
        }
        chartDiv.style.display = 'none';
        return;
    }

    if (placeholder) placeholder.style.display = 'none';
    chartDiv.style.display = 'block';

    const config = { responsive: true, displayModeBar: false };
    const data = [];
    const colorscale = 'Viridis';

    const getJitter = (lapId) => {
        const lap = state.lapDataLookup[lapId];
        if (!lap) return 0;
        return ((lap.lap_num * 137) % 100) / 200 - 0.25;
    };

    const transformTime = (t) => Math.pow(Math.max(0, t - minTime), 0.25);
    const transformedMax = transformTime(maxTime);

    let firstWithScale = true;
    const commonMarker = (times, showScale) => {
        const transformed = times.map(t => transformTime(t));

        let colorbar = undefined;
        if (showScale) {
            const range = maxTime - minTime;
            const steps = [0, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0].filter(s => s < range);
            if (steps.length > 6) steps.splice(6);
            steps.push(range);

            colorbar = {
                orientation: 'h', y: 0, thickness: 12,
                title: { text: 'Lap Time', font: { size: 10, color: '#94a3b8' }, side: 'top' },
                tickvals: steps.map(s => Math.pow(s, 0.25)),
                ticktext: steps.map(s => (minTime + s).toFixed(2)),
                tickfont: { size: 9, color: '#94a3b8' },
                len: 0.9
            };
        }

        return {
            size: 8, opacity: 1.0, color: transformed, colorscale: colorscale,
            cmin: 0, cmax: transformedMax, showscale: showScale, reversescale: true,
            colorbar: colorbar,
            line: { width: 1, color: 'rgba(255,255,255,0.3)' }
        };
    };

    if (speedValsA.length > 0) {
        data.push({
            type: 'violin', y: speedValsA, x0: 1, name: 'A',
            hoverinfo: 'skip', points: false,
            line: { color: '#fb923c', width: 1.5 }, fillcolor: 'rgba(251, 146, 60, 0.2)',
            box: { visible: false, width: 0.15 }, meanline: { visible: true },
            width: 0.5
        });

        const scatterCurve = data.length;
        const xVals = [];
        state.groupASelection.forEach(id => {
            if (state.lapDataLookup[id] && getSpeedAtDistance(state.lapDataLookup[id], targetDist) !== null) {
                xVals.push(1.0 - 0.35 + getJitter(id));
            }
        });

        data.push({
            type: 'scatter', x: xVals, y: speedValsA, name: 'A', text: speedLapsA, customdata: speedTimesA,
            mode: 'markers',
            hovertemplate: '<b>%{text}</b><br>Speed: %{y:.1f} km/h<br>Time: %{customdata:.3f}s<extra></extra>',
            marker: commonMarker(speedTimesA, firstWithScale),
            selected: { marker: { size: 14, line: { width: 2.5, color: '#ffffff' } } }
        });

        let ptIdx = 0;
        state.groupASelection.forEach(id => {
            if (state.lapDataLookup[id] && getSpeedAtDistance(state.lapDataLookup[id], targetDist) !== null) {
                if (!state.lapToPlotIndices.speed[id]) state.lapToPlotIndices.speed[id] = [];
                state.lapToPlotIndices.speed[id].push({ curveNumber: scatterCurve, pointNumber: ptIdx++ });
            }
        });
        firstWithScale = false;
    }

    if (speedValsB.length > 0) {
        data.push({
            type: 'violin', y: speedValsB, x0: 2, name: 'B',
            hoverinfo: 'skip', points: false,
            line: { color: '#38bdf8', width: 1.5 }, fillcolor: 'rgba(56, 189, 248, 0.2)',
            box: { visible: false, width: 0.15 }, meanline: { visible: true },
            width: 0.5
        });

        const scatterCurve = data.length;
        const xVals = [];
        state.groupBSelection.forEach(id => {
            if (state.lapDataLookup[id] && getSpeedAtDistance(state.lapDataLookup[id], targetDist) !== null) {
                xVals.push(2.0 + 0.35 + getJitter(id));
            }
        });

        data.push({
            type: 'scatter', x: xVals, y: speedValsB, name: 'B', text: speedLapsB, customdata: speedTimesB,
            mode: 'markers',
            hovertemplate: '<b>%{text}</b><br>Speed: %{y:.1f} km/h<br>Time: %{customdata:.3f}s<extra></extra>',
            marker: commonMarker(speedTimesB, firstWithScale),
            selected: { marker: { size: 14, line: { width: 2.5, color: '#ffffff' } } }
        });

        let ptIdx = 0;
        state.groupBSelection.forEach(id => {
            if (state.lapDataLookup[id] && getSpeedAtDistance(state.lapDataLookup[id], targetDist) !== null) {
                if (!state.lapToPlotIndices.speed[id]) state.lapToPlotIndices.speed[id] = [];
                state.lapToPlotIndices.speed[id].push({ curveNumber: scatterCurve, pointNumber: ptIdx++ });
            }
        });
        firstWithScale = false;
    }

    if (brakingValsA.length > 0) {
        data.push({
            type: 'violin', y: brakingValsA, x0: 1, name: 'A', yaxis: 'y2', xaxis: 'x2',
            hoverinfo: 'skip', points: false,
            line: { color: '#fb923c', width: 1.5 }, fillcolor: 'rgba(251, 146, 60, 0.2)',
            box: { visible: false, width: 0.15 }, meanline: { visible: true },
            width: 0.5
        });

        const scatterCurve = data.length;
        const pointsData = [];
        state.groupASelection.forEach(id => {
            if (state.lapDataLookup[id]) {
                const bps = getBrakingPointsInRange(state.lapDataLookup[id], targetDist, 20);
                bps.forEach(bp => pointsData.push({ id, bp, t: lapTimeMap[id], lapNum: state.lapDataLookup[id].lap_num }));
            }
        });

        data.push({
            type: 'scatter', x: pointsData.map(p => 1.0 - 0.35 + getJitter(p.id)), y: pointsData.map(p => p.bp),
            name: 'A', text: pointsData.map(p => `Lap ${p.lapNum}`), customdata: pointsData.map(p => p.t),
            yaxis: 'y2', xaxis: 'x2', mode: 'markers',
            hovertemplate: '<b>%{text}</b><br>Dist: %{y:.1f} m<br>Time: %{customdata:.3f}s<extra></extra>',
            marker: commonMarker(pointsData.map(p => p.t), firstWithScale),
            selected: { marker: { size: 14, line: { width: 2.5, color: '#ffffff' } } }
        });

        pointsData.forEach((p, idx) => {
            if (!state.lapToPlotIndices.braking[p.id]) state.lapToPlotIndices.braking[p.id] = [];
            state.lapToPlotIndices.braking[p.id].push({ curveNumber: scatterCurve, pointNumber: idx });
        });
        firstWithScale = false;
    }

    if (brakingValsB.length > 0) {
        data.push({
            type: 'violin', y: brakingValsB, x0: 2, name: 'B', yaxis: 'y2', xaxis: 'x2',
            hoverinfo: 'skip', points: false,
            line: { color: '#38bdf8', width: 1.5 }, fillcolor: 'rgba(56, 189, 248, 0.2)',
            box: { visible: false, width: 0.15 }, meanline: { visible: true },
            width: 0.5
        });

        const scatterCurve = data.length;
        const pointsData = [];
        state.groupBSelection.forEach(id => {
            if (state.lapDataLookup[id]) {
                const bps = getBrakingPointsInRange(state.lapDataLookup[id], targetDist, 20);
                bps.forEach(bp => pointsData.push({ id, bp, t: lapTimeMap[id], lapNum: state.lapDataLookup[id].lap_num }));
            }
        });

        data.push({
            type: 'scatter', x: pointsData.map(p => 2.0 + 0.35 + getJitter(p.id)), y: pointsData.map(p => p.bp),
            name: 'B', text: pointsData.map(p => `Lap ${p.lapNum}`), customdata: pointsData.map(p => p.t),
            yaxis: 'y2', xaxis: 'x2', mode: 'markers',
            hovertemplate: '<b>%{text}</b><br>Dist: %{y:.1f} m<br>Time: %{customdata:.3f}s<extra></extra>',
            marker: commonMarker(pointsData.map(p => p.t), firstWithScale),
            selected: { marker: { size: 14, line: { width: 2.5, color: '#ffffff' } } }
        });

        pointsData.forEach((p, idx) => {
            if (!state.lapToPlotIndices.braking[p.id]) state.lapToPlotIndices.braking[p.id] = [];
            state.lapToPlotIndices.braking[p.id].push({ curveNumber: scatterCurve, pointNumber: idx });
        });
    }

    const gdView = document.getElementById('telemetry-chart');
    let xRange = [0.4, 2.6];
    if (gdView && gdView.layout && gdView.layout.xaxis && gdView.layout.xaxis.range) {
        xRange = gdView.layout.xaxis.range;
    }

    const layout = {
        autosize: true,
        uirevision: true,
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        showlegend: false,
        margin: { t: 5, b: 35, l: 45, r: 15 },
        hovermode: 'closest',
        yaxis: {
            title: 'Speed (km/h)', color: '#94a3b8', titlefont: { size: 10 },
            gridcolor: 'rgba(255,255,255,0.05)', zeroline: false,
            domain: [0.53, 0.98]
        },
        yaxis2: {
            title: 'Dist (m)', color: '#94a3b8', titlefont: { size: 10 },
            gridcolor: 'rgba(255,255,255,0.05)', zeroline: false,
            domain: [0.05, 0.50]
        },
        xaxis: {
            range: xRange, tickvals: [1, 2], ticktext: ['A', 'B'],
            color: '#94a3b8', gridcolor: 'rgba(255,255,255,0.05)'
        },
        xaxis2: {
            anchor: 'y2', range: xRange, tickvals: [1, 2], ticktext: ['A', 'B'],
            color: '#94a3b8', gridcolor: 'rgba(255,255,255,0.05)', showticklabels: false
        },
        shapes: [{
            type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y2', y0: targetDist, y1: targetDist,
            line: { color: 'rgba(255, 255, 255, 0.2)', width: 1, dash: 'dash' }
        }],
        annotations: [
            {
                text: `Speeds at ${targetDist.toFixed(0)}m`,
                xref: 'paper', yref: 'paper', x: 0, y: 1.0,
                showarrow: false, font: { size: 11, color: '#94a3b8' }, xanchor: 'left'
            },
            {
                text: `Braking Starts (±20m)`,
                xref: 'paper', yref: 'paper', x: 0, y: 0.52,
                showarrow: false, font: { size: 11, color: '#94a3b8' }, xanchor: 'left'
            }
        ]
    };

    Plotly.react('telemetry-chart', data, layout, config);
}

export function renderDeltaPlot() {
    const targetId = 'plot-area-delta';
    const targetEl = document.getElementById(targetId);
    if (!targetEl) return;

    if (typeof Plotly === 'undefined') return;

    try {
        const data = [];
        state.bottomPlotIndices = state.bottomPlotIndices || {};
        state.bottomPlotIndices['plot-area-delta'] = {};

        // Use the fastest selected lap in group A as the reference lap (or fastest selected lap overall)
        const refLap = state.fastestGroupALap || state.fastestSelectedLap;
        const refLapId = state.fastestGroupALapId || state.fastestSelectedLapId || (refLap && (refLap.lapId || `${refLap.session_id}-${refLap.lap_num}`));

        const lapsA = state.groupAVisibleMap ? Array.from(state.groupASelection).map(id => {
            return { lapId: id, ...state.lapDataLookup[id] };
        }).filter(l => l && l.points && l.points.length > 0) : [];

        const lapsB = state.groupBVisibleMap ? Array.from(state.groupBSelection).map(id => {
            return { lapId: id, ...state.lapDataLookup[id] };
        }).filter(l => l && l.points && l.points.length > 0) : [];

        if (!refLap || (lapsA.length < 1 && lapsB.length < 1)) {
            Plotly.purge(targetEl);
            state.deltaPlotInitialized = false;
            return;
        }

        const refPoints = refLap.points;
        let startDist = 0;
        if (state.sortMode === 'turn' && state.trackData && state.trackData.turns) {
            const turn = state.trackData.turns[state.currentTurnIdx];
            if (turn) startDist = turn.start;
        }

        const refAtStart = getPointAtDistance(refLap, startDist, 'time');
        const refStartTime = refAtStart ? refAtStart.time : refPoints[0].time;
        const refOfficialTime = parseLapTime(refLap.lap_time);
        const maxDist = (state.trackData && state.trackData.lap_length) ? state.trackData.lap_length : 999999;

        if (refStartTime === undefined) {
            Plotly.purge(targetEl);
            state.deltaPlotInitialized = false;
            return;
        }

        const computeDeltaTrace = (lap) => {
            const x = [];
            const y = [];
            const lapAtStart = getPointAtDistance(lap, startDist, 'time');
            const lapStartTime = lapAtStart ? lapAtStart.time : lap.points[0].time;
            if (lapStartTime === undefined) return null;

            let lastD = -1;
            const targetDists = [];
            const refTimes = [];
            for (let j = 0; j < refPoints.length; j += 4) {
                const rp = refPoints[j];
                const d = rp.dist;

                // Robustness checks: only plot within track limits and official lap time
                if (d < 0) continue;
                if (d > maxDist + 10) break; // Allow a small buffer beyond track length
                if (d <= lastD) continue; // Avoid vertical lines if reference is stationary
                lastD = d;

                const tRef = rp.time - refStartTime;
                if (tRef > refOfficialTime + 0.5) break; // Stop if reference lap goes way beyond official time

                targetDists.push(d);
                refTimes.push(tRef);
            }

            const interpolatedPoints = getPointsAtDistancesMonotonic(lap, targetDists, 'time');
            for (let k = 0; k < targetDists.length; k++) {
                const pLap = interpolatedPoints[k];
                if (pLap && typeof pLap.time === 'number') {
                    const tLap = pLap.time - lapStartTime;
                    const delta = tLap - refTimes[k];
                    if (!isNaN(delta)) {
                        x.push(targetDists[k]);
                        y.push(delta);
                    }
                }
            }

            if (x.length > 0) {
                return { x, y };
            }
            return null;
        };

        const allLapsInDelta = [...lapsA, ...lapsB];
        const uniqueDeltaSids = new Set(allLapsInDelta.map(l => l.sessionId || l.session_id).filter(Boolean));
        const isMultiSessionDelta = uniqueDeltaSids.size > 1;

        const formatDeltaLabel = (lap, suffix = '') => {
            const sName = lap.sessionName || lap.session_name || '';
            if (isMultiSessionDelta && sName) {
                return `${sName} L${lap.lap_num}${suffix}`;
            }
            return `Lap ${lap.lap_num}${suffix}`;
        };

        const lapsToDeltaA = sortLapsByTime(lapsA.filter(l => l.lapId !== refLapId));
        lapsToDeltaA.forEach((lap, i) => {
            const traceData = computeDeltaTrace(lap);
            if (traceData) {
                const alpha = getLapAlpha(i, lapsToDeltaA.length);
                state.bottomPlotIndices['plot-area-delta'][lap.lapId] = state.bottomPlotIndices['plot-area-delta'][lap.lapId] || [];
                state.bottomPlotIndices['plot-area-delta'][lap.lapId].push(data.length);
                data.push({
                    x: traceData.x, y: traceData.y, mode: 'lines',
                    name: formatDeltaLabel(lap),
                    line: { color: `rgba(251, 146, 60, ${alpha})`, width: 2 },
                    hoverinfo: 'none'
                });
            }
        });

        const lapsToDeltaB = sortLapsByTime(lapsB.filter(l => l.lapId !== refLapId));
        lapsToDeltaB.forEach((lap, i) => {
            const traceData = computeDeltaTrace(lap);
            if (traceData) {
                const alpha = getLapAlpha(i, lapsToDeltaB.length, 0.8);
                state.bottomPlotIndices['plot-area-delta'][lap.lapId] = state.bottomPlotIndices['plot-area-delta'][lap.lapId] || [];
                state.bottomPlotIndices['plot-area-delta'][lap.lapId].push(data.length);
                data.push({
                    x: traceData.x, y: traceData.y, mode: 'lines',
                    name: formatDeltaLabel(lap, ' (B)'),
                    line: { color: `rgba(56, 189, 248, ${alpha})`, width: 2, dash: 'solid' },
                    hoverinfo: 'none'
                });
            }
        });

        const currentRange = (state.globalTelemetryXRange && state.globalTelemetryXRange.length === 2)
            ? state.globalTelemetryXRange
            : (state.trackData && state.trackData.lap_length ? [0, state.trackData.lap_length] : [0, 1000]);

        const layout = {
            uirevision: `${currentRange[0]}_${currentRange[1]}`,
            dragmode: 'pan',
            margin: { t: 30, b: 25, l: 80, r: 20 },
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            font: { color: '#94a3b8', size: 10 },
            showlegend: false,
            xaxis: {
                gridcolor: 'rgba(255,255,255,0.05)',
                zeroline: false,
                range: currentRange,
                fixedrange: false
            },
            yaxis: {
                title: 'Time Delta (s)',
                gridcolor: 'rgba(255,255,255,0.05)',
                zeroline: true,
                zerolinecolor: 'rgba(255,255,255,0.4)',
                color: '#94a3b8',
                fixedrange: true,
                autorange: false
            },
            annotations: [
                {
                    text: `Time Delta to ${formatDeltaLabel(refLap)}`,
                    xref: 'paper', yref: 'paper', x: 0, y: 1.0,
                    showarrow: false, font: { size: 11, color: '#94a3b8' }, xanchor: 'left'
                }
            ]
        };

        addCommonPlotElements(layout);

        const config = { responsive: true, displayModeBar: false, scrollZoom: true };
        if (!state.deltaPlotInitialized) {
            Plotly.newPlot(targetEl, data, layout, config).then(gd => {
                // Ensure sync is attached if needed
            });
            state.deltaPlotInitialized = true;
        } else {
            Plotly.react(targetEl, data, layout, config);
            updateDeltaYLim();
        }
    } catch (e) {
        console.error("Delta Plot Error:", e);
    }
}

export function renderSpeedPlot() {
    const targetId = 'plot-area-speed';
    const targetEl = document.getElementById(targetId);
    if (!targetEl) return;

    if (typeof Plotly === 'undefined') return;

    try {
        const data = [];
        state.bottomPlotIndices = state.bottomPlotIndices || {};
        state.bottomPlotIndices['plot-area-speed'] = {};

        const lapsA = state.groupAVisibleMap ? Array.from(state.groupASelection).map(id => {
            return { lapId: id, ...state.lapDataLookup[id] };
        }).filter(l => l && l.points && l.points.length > 0) : [];

        const lapsB = state.groupBVisibleMap ? Array.from(state.groupBSelection).map(id => {
            return { lapId: id, ...state.lapDataLookup[id] };
        }).filter(l => l && l.points && l.points.length > 0) : [];

        if (lapsA.length < 1 && lapsB.length < 1) {
            Plotly.purge(targetEl);
            state.speedPlotInitialized = false;
            return;
        }

        const allLapsInSpeed = [...lapsA, ...lapsB];
        const uniqueSpeedSids = new Set(allLapsInSpeed.map(l => l.sessionId || l.session_id).filter(Boolean));
        const isMultiSessionSpeed = uniqueSpeedSids.size > 1;

        const formatSpeedLabel = (lap, suffix = '') => {
            const sName = lap.sessionName || lap.session_name || '';
            if (isMultiSessionSpeed && sName) {
                return `${sName} L${lap.lap_num}${suffix}`;
            }
            return `Lap ${lap.lap_num}${suffix}`;
        };

        const maxDist = (state.trackData && state.trackData.lap_length) ? state.trackData.lap_length : 999999;

        const lapsToSpeedA = sortLapsByTime(lapsA);
        lapsToSpeedA.forEach((lap, i) => {
            const x = [];
            const y = [];
            const points = lap.points;

            for (let j = 0; j < points.length; j += 4) {
                const p = points[j];
                const d = p.dist;
                if (d < 0) continue;
                if (d > maxDist + 10) break;

                x.push(d);
                y.push(p.speed);
            }

            if (x.length > 0) {
                const alpha = getLapAlpha(i, lapsToSpeedA.length);
                state.bottomPlotIndices['plot-area-speed'][lap.lapId] = state.bottomPlotIndices['plot-area-speed'][lap.lapId] || [];
                state.bottomPlotIndices['plot-area-speed'][lap.lapId].push(data.length);
                data.push({
                    x: x, y: y, mode: 'lines',
                    name: formatSpeedLabel(lap),
                    line: { color: `rgba(251, 146, 60, ${alpha})`, width: 2 },
                    hoverinfo: 'none'
                });
            }
        });

        const lapsToSpeedB = sortLapsByTime(lapsB);
        lapsToSpeedB.forEach((lap, i) => {
            const x = [];
            const y = [];
            const points = lap.points;

            for (let j = 0; j < points.length; j += 4) {
                const p = points[j];
                const d = p.dist;
                if (d < 0) continue;
                if (d > maxDist + 10) break;

                x.push(d);
                y.push(p.speed);
            }

            if (x.length > 0) {
                const alpha = getLapAlpha(i, lapsToSpeedB.length, 0.8);
                state.bottomPlotIndices['plot-area-speed'][lap.lapId] = state.bottomPlotIndices['plot-area-speed'][lap.lapId] || [];
                state.bottomPlotIndices['plot-area-speed'][lap.lapId].push(data.length);
                data.push({
                    x: x, y: y, mode: 'lines',
                    name: formatSpeedLabel(lap, ' (B)'),
                    line: { color: `rgba(56, 189, 248, ${alpha})`, width: 2, dash: 'solid' },
                    hoverinfo: 'none'
                });
            }
        });

        const currentRange = (state.globalTelemetryXRange && state.globalTelemetryXRange.length === 2)
            ? state.globalTelemetryXRange
            : (state.trackData && state.trackData.lap_length ? [0, state.trackData.lap_length] : [0, 1000]);

        const layout = {
            uirevision: `${currentRange[0]}_${currentRange[1]}`,
            dragmode: 'pan',
            margin: { t: 30, b: 25, l: 80, r: 20 },
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            font: { color: '#94a3b8', size: 10 },
            showlegend: false,
            xaxis: {
                gridcolor: 'rgba(255,255,255,0.05)',
                zeroline: false,
                range: currentRange,
                fixedrange: false
            },
            yaxis: {
                title: 'Speed (km/h)',
                gridcolor: 'rgba(255,255,255,0.05)',
                zeroline: false,
                color: '#94a3b8',
                fixedrange: true,
                autorange: false
            },
            annotations: [
                {
                    text: `Speed Analysis`,
                    xref: 'paper', yref: 'paper', x: 0, y: 1.0,
                    showarrow: false, font: { size: 11, color: '#94a3b8' }, xanchor: 'left'
                }
            ]
        };

        addCommonPlotElements(layout);

        const config = { responsive: true, displayModeBar: false, scrollZoom: true };
        if (!state.speedPlotInitialized) {
            Plotly.newPlot(targetEl, data, layout, config);
            state.speedPlotInitialized = true;
        } else {
            Plotly.react(targetEl, data, layout, config);
            updateSpeedYLim();
        }
    } catch (e) {
        console.error("Speed Plot Error:", e);
    }
}

export function renderExpandablePlots() {
    if (!state.allSessionsData || state.allSessionsData.length === 0) {
        return;
    }
    if (state.speedPlotVisible) {
        renderSpeedPlot();
    }
    if (state.deltaPlotVisible) {
        renderDeltaPlot();
    }

    if (state.activePlotChannels) {
        state.activePlotChannels.forEach(activeTab => {
            renderSingleChannelPlot(activeTab);
        });
    }
}

export function renderSingleChannelPlot(activeTab) {
    if (!state.allSessionsData || state.allSessionsData.length === 0) {
        return;
    }
    const targetId = 'plot-area-' + activeTab;
    const targetEl = document.getElementById(targetId);

    const lazyTabs = ['steering', 'rps', 'gforce', 'gyro', 'slide', 'patch_vel', 'force', 'tyre_load', 'slip_angle'];

    if (lazyTabs.includes(activeTab)) {
        if (!loadedChannels.has(activeTab)) {
            if (loadingChannels.has(activeTab)) {
                return;
            }

            if (targetEl) {
                Plotly.purge(targetEl);
                targetEl.innerHTML = `
                    <div style="display: flex; align-items: center; justify-content: center; height: 100%; color: #94a3b8; font-size: 14px;">
                        <div style="display: flex; flex-direction: column; align-items: center; gap: 8px;">
                            <div class="loading-spinner" style="width: 24px; height: 24px; border: 2px solid rgba(255,255,255,0.1); border-top-color: #38bdf8; border-radius: 50%; animation: spin 1s linear infinite;"></div>
                            <div>Loading channel data...</div>
                        </div>
                    </div>
                `;
                if (!document.getElementById('spin-keyframes')) {
                    const style = document.createElement('style');
                    style.id = 'spin-keyframes';
                    style.innerHTML = '@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }';
                    document.head.appendChild(style);
                }
            }

            ensureChannelLoaded(activeTab)
                .then(() => {
                    if (state.activePlotChannels.has(activeTab)) {
                        if (targetEl) {
                            Plotly.purge(targetEl);
                            targetEl.innerHTML = '';
                        }
                        renderSingleChannelPlot(activeTab);
                    }
                })
                .catch(err => {
                    if (targetEl) {
                        targetEl.innerHTML = `
                            <div style="display: flex; align-items: center; justify-content: center; height: 100%; color: var(--warning); font-size: 14px;">
                                Error loading telemetry channel
                            </div>
                        `;
                    }
                });
            return;
        }
    }

    const originalDescriptor = Object.getOwnPropertyDescriptor(state, 'currentActivePlotTab');
    Object.defineProperty(state, 'currentActivePlotTab', {
        get: () => activeTab,
        configurable: true
    });
    try {
        state.bottomPlotIndices = state.bottomPlotIndices || {};
        state.bottomPlotIndices[targetId] = {};

        const data = [];

        const lapsA = state.groupAVisibleMap ? sortLapsByTime(Array.from(state.groupASelection).map(id => {
            const lap = state.lapDataLookup[id];
            return lap ? { id, ...lap } : null;
        }).filter(l => l && l.points)) : [];

        const lapsB = state.groupBVisibleMap ? sortLapsByTime(Array.from(state.groupBSelection).map(id => {
            const lap = state.lapDataLookup[id];
            return lap ? { id, ...lap } : null;
        }).filter(l => l && l.points)) : [];

        const currentRange = (state.globalTelemetryXRange && state.globalTelemetryXRange.length === 2)
            ? state.globalTelemetryXRange
            : (state.trackData && state.trackData.lap_length ? [0, state.trackData.lap_length] : [0, 1000]);

        const layout = {
            uirevision: `${currentRange[0]}_${currentRange[1]}`,
            dragmode: 'pan',
            margin: { t: 30, b: 25, l: 80, r: 20 },
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)',
            font: { color: '#94a3b8', size: 10 },
            showlegend: false,
            xaxis: {
                gridcolor: 'rgba(255,255,255,0.05)',
                zeroline: false,
                range: currentRange,
                fixedrange: false
            },
            yaxis: {
                gridcolor: 'rgba(255,255,255,0.05)',
                zeroline: false,
                color: '#94a3b8',
                fixedrange: true
            },
            annotations: [
                {
                    text: 'Dist (m)', xref: 'paper', yref: 'paper', x: 0, y: 0,
                    xanchor: 'right', yanchor: 'middle', xshift: -10, yshift: -9,
                    showarrow: false, font: { size: 10, color: '#94a3b8' }
                }
            ]
        };

        if (state.currentActivePlotTab === 'pedals') {
            layout.yaxis.title = 'Inputs (%)';
            layout.yaxis.range = [0, 105];
            layout.yaxis.autorange = false;
            lapsA.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsA.length);
                addTrace(data, lap, 'throttle', `rgba(34, 197, 94, ${alpha})`, 2);
                addTrace(data, lap, 'brake', `rgba(239, 68, 68, ${alpha})`, 2);
            });
            lapsB.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsB.length, 0.8);
                addTrace(data, lap, 'throttle', `rgba(34, 197, 94, ${alpha})`, 1.5, 'dash');
                addTrace(data, lap, 'brake', `rgba(239, 68, 68, ${alpha})`, 1.5, 'dash');
            });
        } else if (state.currentActivePlotTab === 'steering') {
            layout.yaxis.title = 'Steering Wheel Angle (deg)';
            lapsA.forEach((lap, i) => addTrace(data, lap, 'steering', `rgba(251, 146, 60, ${getLapAlpha(i, lapsA.length)})`, 2));
            lapsB.forEach((lap, i) => addTrace(data, lap, 'steering', `rgba(56, 189, 248, ${getLapAlpha(i, lapsB.length, 0.8)})`, 1.5, 'dash'));

            let minSteer = Infinity;
            let maxSteer = -Infinity;
            data.forEach(trace => {
                if (trace.y) {
                    for (let i = 0; i < trace.y.length; i++) {
                        const val = trace.y[i];
                        if (typeof val === 'number' && !isNaN(val)) {
                            if (val < minSteer) minSteer = val;
                            if (val > maxSteer) maxSteer = val;
                        }
                    }
                }
            });
            if (minSteer === Infinity || maxSteer === -Infinity) {
                minSteer = -45;
                maxSteer = 45;
            }
            const span = maxSteer - minSteer;
            const pad = Math.max(1, span * 0.1);
            const steerMin = minSteer - pad;
            const steerMax = maxSteer + pad;
            const { tickvals, ticktext } = getSteeringTicks(steerMin, steerMax);
            layout.yaxis.range = [steerMax, steerMin];
            layout.yaxis.autorange = false;
            layout.yaxis.tickmode = 'array';
            layout.yaxis.tickvals = tickvals;
            layout.yaxis.ticktext = ticktext;
        } else if (state.currentActivePlotTab === 'rps') {
            layout.yaxis.title = 'Rotation Speed (RPS)';
            layout.showlegend = true;
            layout.legend = {
                x: 0.98, y: 0.98, xanchor: 'right', yanchor: 'top',
                bgcolor: 'rgba(15, 23, 42, 0.8)', bordercolor: 'rgba(255, 255, 255, 0.1)', borderwidth: 1,
                font: { size: 10, color: '#94a3b8' }
            };
            lapsA.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsA.length);
                addTrace(data, lap, 'rps_fl', `rgba(56, 189, 248, ${alpha})`, 2, 'solid', 'FL', 'y', i === 0);
                addTrace(data, lap, 'rps_fr', `rgba(34, 197, 94, ${alpha})`, 2, 'solid', 'FR', 'y', i === 0);
                addTrace(data, lap, 'rps_rl', `rgba(239, 68, 68, ${alpha})`, 2, 'solid', 'RL', 'y', i === 0);
                addTrace(data, lap, 'rps_rr', `rgba(251, 146, 60, ${alpha})`, 2, 'solid', 'RR', 'y', i === 0);
            });
        } else if (state.currentActivePlotTab === 'gforce') {
            layout.yaxis.title = 'G-Force (g)';
            layout.showlegend = false;

            if (!state.gforcePlotActiveComponents) {
                state.gforcePlotActiveComponents = { lat: true, lon: true, tot: false };
            }

            const calcGForceTotals = (lap) => {
                lap.points.forEach(p => {
                    const gx = parseFloat(p['gx']) || 0;
                    const gy = parseFloat(p['gy']) || 0;
                    p['gforce_tot'] = Math.sqrt(gx * gx + gy * gy);
                });
            };

            lapsA.forEach(lap => calcGForceTotals(lap));
            lapsB.forEach(lap => calcGForceTotals(lap));

            lapsA.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsA.length);
                if (state.gforcePlotActiveComponents.lat) addTrace(data, lap, 'gy', `rgba(236, 72, 153, ${alpha})`, 2, 'solid', 'Lat', 'y', false);
                if (state.gforcePlotActiveComponents.lon) addTrace(data, lap, 'gx', `rgba(168, 85, 247, ${alpha})`, 2, 'solid', 'Lon', 'y', false);
                if (state.gforcePlotActiveComponents.tot) addTrace(data, lap, 'gforce_tot', `rgba(251, 146, 60, ${alpha})`, 2, 'solid', 'Total', 'y', false);
            });

            lapsB.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsB.length, 0.8);
                if (state.gforcePlotActiveComponents.lat) addTrace(data, lap, 'gy', `rgba(236, 72, 153, ${alpha})`, 1.5, 'solid', 'Lat (B)', 'y', false);
                if (state.gforcePlotActiveComponents.lon) addTrace(data, lap, 'gx', `rgba(168, 85, 247, ${alpha})`, 1.5, 'solid', 'Lon (B)', 'y', false);
                if (state.gforcePlotActiveComponents.tot) addTrace(data, lap, 'gforce_tot', `rgba(251, 146, 60, ${alpha})`, 1.5, 'solid', 'Total (B)', 'y', false);
            });
        } else if (state.currentActivePlotTab === 'gyro') {
            layout.yaxis.title = 'Angular Rate (deg/s)';
            layout.showlegend = false;

            if (!state.gyroPlotActiveComponents) {
                state.gyroPlotActiveComponents = { yaw: true, pitch: false, roll: false };
            }

            lapsA.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsA.length);
                if (state.gyroPlotActiveComponents.yaw) addTrace(data, lap, 'gyro_yaw', `rgba(56, 189, 248, ${alpha})`, 2, 'solid', 'Yaw', 'y', false);
                if (state.gyroPlotActiveComponents.pitch) addTrace(data, lap, 'gyro_pitch', `rgba(34, 197, 94, ${alpha})`, 2, 'solid', 'Pitch', 'y', false);
                if (state.gyroPlotActiveComponents.roll) addTrace(data, lap, 'gyro_roll', `rgba(251, 146, 60, ${alpha})`, 2, 'solid', 'Roll', 'y', false);
            });

            lapsB.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsB.length, 0.8);
                if (state.gyroPlotActiveComponents.yaw) addTrace(data, lap, 'gyro_yaw', `rgba(56, 189, 248, ${alpha})`, 1.5, 'solid', 'Yaw (B)', 'y', false);
                if (state.gyroPlotActiveComponents.pitch) addTrace(data, lap, 'gyro_pitch', `rgba(34, 197, 94, ${alpha})`, 1.5, 'solid', 'Pitch (B)', 'y', false);
                if (state.gyroPlotActiveComponents.roll) addTrace(data, lap, 'gyro_roll', `rgba(251, 146, 60, ${alpha})`, 1.5, 'solid', 'Roll (B)', 'y', false);
            });
        } else if (state.currentActivePlotTab === 'slide') {
            layout.yaxis.title = 'Slide Pct (%)';
            layout.showlegend = true;
            layout.legend = {
                x: 0.98, y: 0.98, xanchor: 'right', yanchor: 'top',
                bgcolor: 'rgba(15, 23, 42, 0.8)', bordercolor: 'rgba(255, 255, 255, 0.1)', borderwidth: 1,
                font: { size: 10, color: '#94a3b8' }
            };
            lapsA.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsA.length);
                addTrace(data, lap, 'sp_fl', `rgba(56, 189, 248, ${alpha})`, 2, 'solid', 'FL', 'y', i === 0);
                addTrace(data, lap, 'sp_fr', `rgba(34, 197, 94, ${alpha})`, 2, 'solid', 'FR', 'y', i === 0);
                addTrace(data, lap, 'sp_rl', `rgba(239, 68, 68, ${alpha})`, 2, 'solid', 'RL', 'y', i === 0);
                addTrace(data, lap, 'sp_rr', `rgba(251, 146, 60, ${alpha})`, 2, 'solid', 'RR', 'y', i === 0);
            });
            lapsB.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsB.length, 0.8);
                addTrace(data, lap, 'sp_fl', `rgba(56, 189, 248, ${alpha})`, 1.5, 'dash', 'FL (B)', 'y', false);
                addTrace(data, lap, 'sp_fr', `rgba(34, 197, 94, ${alpha})`, 1.5, 'dash', 'FR (B)', 'y', false);
                addTrace(data, lap, 'sp_rl', `rgba(239, 68, 68, ${alpha})`, 1.5, 'dash', 'RL (B)', 'y', false);
                addTrace(data, lap, 'sp_rr', `rgba(251, 146, 60, ${alpha})`, 1.5, 'dash', 'RR (B)', 'y', false);
            });
        } else if (state.currentActivePlotTab === 'patch_vel') {
            layout.showlegend = true;
            layout.legend = {
                x: 0.98, y: 0.98, xanchor: 'right', yanchor: 'top',
                bgcolor: 'rgba(15, 23, 42, 0.8)', bordercolor: 'rgba(255, 255, 255, 0.1)', borderwidth: 1,
                font: { size: 10, color: '#94a3b8' }
            };
            // Top plot: Lat Patch Vel (domain: [0.53, 1])
            layout.yaxis.title = 'Lat Patch Vel (m/s)';
            layout.yaxis.domain = [0.53, 1];

            // Bottom plot: Lon Patch Vel (domain: [0, 0.47])
            layout.yaxis2 = {
                domain: [0, 0.47],
                title: 'Lon Patch Vel (m/s)',
                gridcolor: 'rgba(255,255,255,0.05)',
                zeroline: false,
                color: '#94a3b8',
                fixedrange: true
            };

            lapsA.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsA.length);
                // Top plot (yaxis = 'y')
                addTrace(data, lap, 'lpv_lat_fl', `rgba(56, 189, 248, ${alpha})`, 2, 'solid', 'FL', 'y', i === 0);
                addTrace(data, lap, 'lpv_lat_fr', `rgba(34, 197, 94, ${alpha})`, 2, 'solid', 'FR', 'y', i === 0);
                addTrace(data, lap, 'lpv_lat_rl', `rgba(239, 68, 68, ${alpha})`, 2, 'solid', 'RL', 'y', i === 0);
                addTrace(data, lap, 'lpv_lat_rr', `rgba(251, 146, 60, ${alpha})`, 2, 'solid', 'RR', 'y', i === 0);

                // Bottom plot (yaxis = 'y2')
                addTrace(data, lap, 'lpv_lon_fl', `rgba(56, 189, 248, ${alpha})`, 2, 'solid', 'FL', 'y2', false);
                addTrace(data, lap, 'lpv_lon_fr', `rgba(34, 197, 94, ${alpha})`, 2, 'solid', 'FR', 'y2', false);
                addTrace(data, lap, 'lpv_lon_rl', `rgba(239, 68, 68, ${alpha})`, 2, 'solid', 'RL', 'y2', false);
                addTrace(data, lap, 'lpv_lon_rr', `rgba(251, 146, 60, ${alpha})`, 2, 'solid', 'RR', 'y2', false);
            });
            lapsB.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsB.length, 0.8);
                // Top plot (yaxis = 'y')
                addTrace(data, lap, 'lpv_lat_fl', `rgba(56, 189, 248, ${alpha})`, 1.5, 'dash', 'FL (B)', 'y', false);
                addTrace(data, lap, 'lpv_lat_fr', `rgba(34, 197, 94, ${alpha})`, 1.5, 'dash', 'FR (B)', 'y', false);
                addTrace(data, lap, 'lpv_lat_rl', `rgba(239, 68, 68, ${alpha})`, 1.5, 'dash', 'RL (B)', 'y', false);
                addTrace(data, lap, 'lpv_lat_rr', `rgba(251, 146, 60, ${alpha})`, 1.5, 'dash', 'RR (B)', 'y', false);

                // Bottom plot (yaxis = 'y2')
                addTrace(data, lap, 'lpv_lon_fl', `rgba(56, 189, 248, ${alpha})`, 1.5, 'dash', 'FL (B)', 'y2', false);
                addTrace(data, lap, 'lpv_lon_fr', `rgba(34, 197, 94, ${alpha})`, 1.5, 'dash', 'FR (B)', 'y2', false);
                addTrace(data, lap, 'lpv_lon_rl', `rgba(239, 68, 68, ${alpha})`, 1.5, 'dash', 'RL (B)', 'y2', false);
                addTrace(data, lap, 'lpv_lon_rr', `rgba(251, 146, 60, ${alpha})`, 1.5, 'dash', 'RR (B)', 'y2', false);
            });
        } else if (state.currentActivePlotTab === 'force') {
            layout.yaxis.title = 'Force (N)';
            layout.showlegend = false;

            if (!state.forcePlotActiveWheels) {
                state.forcePlotActiveWheels = { fl: true, fr: true, rl: true, rr: true };
            }
            if (!state.forcePlotActiveComponents) {
                state.forcePlotActiveComponents = { lat: true, lon: true, tot: false };
            }

            const calcTotals = (lap) => {
                lap.points.forEach(p => {
                    const lat_fl = parseFloat(p['lf_lat_fl']) || 0;
                    const lon_fl = parseFloat(p['lf_lon_fl']) || 0;
                    p['lf_tot_fl'] = Math.sqrt(lat_fl * lat_fl + lon_fl * lon_fl);

                    const lat_fr = parseFloat(p['lf_lat_fr']) || 0;
                    const lon_fr = parseFloat(p['lf_lon_fr']) || 0;
                    p['lf_tot_fr'] = Math.sqrt(lat_fr * lat_fr + lon_fr * lon_fr);

                    const lat_rl = parseFloat(p['lf_lat_rl']) || 0;
                    const lon_rl = parseFloat(p['lf_lon_rl']) || 0;
                    p['lf_tot_rl'] = Math.sqrt(lat_rl * lat_rl + lon_rl * lon_rl);

                    const lat_rr = parseFloat(p['lf_lat_rr']) || 0;
                    const lon_rr = parseFloat(p['lf_lon_rr']) || 0;
                    p['lf_tot_rr'] = Math.sqrt(lat_rr * lat_rr + lon_rr * lon_rr);
                });
            };

            lapsA.forEach(lap => calcTotals(lap));
            lapsB.forEach(lap => calcTotals(lap));

            lapsA.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsA.length);
                if (state.forcePlotActiveWheels.fl) {
                    if (state.forcePlotActiveComponents.lat) addTrace(data, lap, 'lf_lat_fl', `rgba(56, 189, 248, ${alpha})`, 2, 'solid', 'FL Lat', 'y', false);
                    if (state.forcePlotActiveComponents.lon) addTrace(data, lap, 'lf_lon_fl', `rgba(56, 189, 248, ${alpha})`, 2, 'dot', 'FL Lon', 'y', false);
                    if (state.forcePlotActiveComponents.tot) addTrace(data, lap, 'lf_tot_fl', `rgba(56, 189, 248, ${alpha})`, 2, 'solid', 'FL Tot', 'y', false);
                }
                if (state.forcePlotActiveWheels.fr) {
                    if (state.forcePlotActiveComponents.lat) addTrace(data, lap, 'lf_lat_fr', `rgba(34, 197, 94, ${alpha})`, 2, 'solid', 'FR Lat', 'y', false);
                    if (state.forcePlotActiveComponents.lon) addTrace(data, lap, 'lf_lon_fr', `rgba(34, 197, 94, ${alpha})`, 2, 'dot', 'FR Lon', 'y', false);
                    if (state.forcePlotActiveComponents.tot) addTrace(data, lap, 'lf_tot_fr', `rgba(34, 197, 94, ${alpha})`, 2, 'solid', 'FR Tot', 'y', false);
                }
                if (state.forcePlotActiveWheels.rl) {
                    if (state.forcePlotActiveComponents.lat) addTrace(data, lap, 'lf_lat_rl', `rgba(239, 68, 68, ${alpha})`, 2, 'solid', 'RL Lat', 'y', false);
                    if (state.forcePlotActiveComponents.lon) addTrace(data, lap, 'lf_lon_rl', `rgba(239, 68, 68, ${alpha})`, 2, 'dot', 'RL Lon', 'y', false);
                    if (state.forcePlotActiveComponents.tot) addTrace(data, lap, 'lf_tot_rl', `rgba(239, 68, 68, ${alpha})`, 2, 'solid', 'RL Tot', 'y', false);
                }
                if (state.forcePlotActiveWheels.rr) {
                    if (state.forcePlotActiveComponents.lat) addTrace(data, lap, 'lf_lat_rr', `rgba(251, 146, 60, ${alpha})`, 2, 'solid', 'RR Lat', 'y', false);
                    if (state.forcePlotActiveComponents.lon) addTrace(data, lap, 'lf_lon_rr', `rgba(251, 146, 60, ${alpha})`, 2, 'dot', 'RR Lon', 'y', false);
                    if (state.forcePlotActiveComponents.tot) addTrace(data, lap, 'lf_tot_rr', `rgba(251, 146, 60, ${alpha})`, 2, 'solid', 'RR Tot', 'y', false);
                }
            });

            lapsB.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsB.length, 0.8);
                if (state.forcePlotActiveWheels.fl) {
                    if (state.forcePlotActiveComponents.lat) addTrace(data, lap, 'lf_lat_fl', `rgba(56, 189, 248, ${alpha})`, 1.5, 'solid', 'FL Lat (B)', 'y', false);
                    if (state.forcePlotActiveComponents.lon) addTrace(data, lap, 'lf_lon_fl', `rgba(56, 189, 248, ${alpha})`, 1.5, 'dot', 'FL Lon (B)', 'y', false);
                    if (state.forcePlotActiveComponents.tot) addTrace(data, lap, 'lf_tot_fl', `rgba(56, 189, 248, ${alpha})`, 1.5, 'solid', 'FL Tot (B)', 'y', false);
                }
                if (state.forcePlotActiveWheels.fr) {
                    if (state.forcePlotActiveComponents.lat) addTrace(data, lap, 'lf_lat_fr', `rgba(34, 197, 94, ${alpha})`, 1.5, 'solid', 'FR Lat (B)', 'y', false);
                    if (state.forcePlotActiveComponents.lon) addTrace(data, lap, 'lf_lon_fr', `rgba(34, 197, 94, ${alpha})`, 1.5, 'dot', 'FR Lon (B)', 'y', false);
                    if (state.forcePlotActiveComponents.tot) addTrace(data, lap, 'lf_tot_fr', `rgba(34, 197, 94, ${alpha})`, 1.5, 'solid', 'FR Tot (B)', 'y', false);
                }
                if (state.forcePlotActiveWheels.rl) {
                    if (state.forcePlotActiveComponents.lat) addTrace(data, lap, 'lf_lat_rl', `rgba(239, 68, 68, ${alpha})`, 1.5, 'solid', 'RL Lat (B)', 'y', false);
                    if (state.forcePlotActiveComponents.lon) addTrace(data, lap, 'lf_lon_rl', `rgba(239, 68, 68, ${alpha})`, 1.5, 'dot', 'RL Lon (B)', 'y', false);
                    if (state.forcePlotActiveComponents.tot) addTrace(data, lap, 'lf_tot_rl', `rgba(239, 68, 68, ${alpha})`, 1.5, 'solid', 'RL Tot (B)', 'y', false);
                }
                if (state.forcePlotActiveWheels.rr) {
                    if (state.forcePlotActiveComponents.lat) addTrace(data, lap, 'lf_lat_rr', `rgba(251, 146, 60, ${alpha})`, 1.5, 'solid', 'RR Lat (B)', 'y', false);
                    if (state.forcePlotActiveComponents.lon) addTrace(data, lap, 'lf_lon_rr', `rgba(251, 146, 60, ${alpha})`, 1.5, 'dot', 'RR Lon (B)', 'y', false);
                    if (state.forcePlotActiveComponents.tot) addTrace(data, lap, 'lf_tot_rr', `rgba(251, 146, 60, ${alpha})`, 1.5, 'solid', 'RR Tot (B)', 'y', false);
                }
            });
        } else if (state.currentActivePlotTab === 'tyre_load') {
            layout.yaxis.title = 'Tyre Load (N)';
            layout.showlegend = true;
            layout.legend = {
                x: 0.98, y: 0.98, xanchor: 'right', yanchor: 'top',
                bgcolor: 'rgba(15, 23, 42, 0.8)', bordercolor: 'rgba(255, 255, 255, 0.1)', borderwidth: 1,
                font: { size: 10, color: '#94a3b8' }
            };
            lapsA.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsA.length);
                addTrace(data, lap, 'tl_fl', `rgba(56, 189, 248, ${alpha})`, 2, 'solid', 'FL', 'y', i === 0);
                addTrace(data, lap, 'tl_fr', `rgba(34, 197, 94, ${alpha})`, 2, 'solid', 'FR', 'y', i === 0);
                addTrace(data, lap, 'tl_rl', `rgba(239, 68, 68, ${alpha})`, 2, 'solid', 'RL', 'y', i === 0);
                addTrace(data, lap, 'tl_rr', `rgba(251, 146, 60, ${alpha})`, 2, 'solid', 'RR', 'y', i === 0);
            });
            lapsB.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsB.length, 0.8);
                addTrace(data, lap, 'tl_fl', `rgba(56, 189, 248, ${alpha})`, 1.5, 'dash', 'FL (B)', 'y', false);
                addTrace(data, lap, 'tl_fr', `rgba(34, 197, 94, ${alpha})`, 1.5, 'dash', 'FR (B)', 'y', false);
                addTrace(data, lap, 'tl_rl', `rgba(239, 68, 68, ${alpha})`, 1.5, 'dash', 'RL (B)', 'y', false);
                addTrace(data, lap, 'tl_rr', `rgba(251, 146, 60, ${alpha})`, 1.5, 'dash', 'RR (B)', 'y', false);
            });
        } else if (state.currentActivePlotTab === 'slip_angle') {
            layout.yaxis.title = 'Slip Angle (deg)';
            layout.showlegend = true;
            layout.legend = {
                x: 0.98, y: 0.98, xanchor: 'right', yanchor: 'top',
                bgcolor: 'rgba(15, 23, 42, 0.8)', bordercolor: 'rgba(255, 255, 255, 0.1)', borderwidth: 1,
                font: { size: 10, color: '#94a3b8' }
            };
            lapsA.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsA.length);
                addTrace(data, lap, 'sa_front', `rgba(56, 189, 248, ${alpha})`, 2, 'solid', 'Front', 'y', i === 0);
                addTrace(data, lap, 'sa_rear', `rgba(239, 68, 68, ${alpha})`, 2, 'solid', 'Rear', 'y', i === 0);
            });
            lapsB.forEach((lap, i) => {
                const alpha = getLapAlpha(i, lapsB.length, 0.8);
                addTrace(data, lap, 'sa_front', `rgba(56, 189, 248, ${alpha})`, 1.5, 'dash', 'Front (B)', 'y', false);
                addTrace(data, lap, 'sa_rear', `rgba(239, 68, 68, ${alpha})`, 1.5, 'dash', 'Rear (B)', 'y', false);
            });
        }

        addCommonPlotElements(layout);
        if (targetEl) {
            Plotly.react(targetEl, data, layout, { responsive: true, displayModeBar: false, scrollZoom: true });
            if (state.currentActivePlotTab === 'force' || state.currentActivePlotTab === 'gforce' || state.currentActivePlotTab === 'gyro') {
                renderCustomLegend(state.currentActivePlotTab, targetEl);
            } else {
                const existing = targetEl.querySelector('.custom-plot-legend');
                if (existing) {
                    existing.remove();
                }
            }
            updateChannelYLim(activeTab, false);
            if (!targetEl._legendClickBound) {
                targetEl.on('plotly_legendclick', function (eventData) {
                    const gd = targetEl;
                    const clickedTraceIndex = eventData.curveNumber;
                    const plotData = gd.data || data;
                    if (!plotData || !plotData[clickedTraceIndex]) return true;

                    const clickedTrace = plotData[clickedTraceIndex];
                    const clickedName = clickedTrace.name;
                    const baseName = clickedName ? clickedName.replace(/ \(B\)$/, '') : clickedName;

                    const currentVisible = clickedTrace.visible;
                    const nextVisible = (currentVisible === 'legendonly') ? true : 'legendonly';

                    state.plotTraceVisibility = state.plotTraceVisibility || {};
                    state.plotTraceVisibility[activeTab] = state.plotTraceVisibility[activeTab] || {};
                    if (baseName) {
                        state.plotTraceVisibility[activeTab][baseName] = nextVisible;
                    }

                    const updateIndices = [];
                    const updateVisible = [];

                    plotData.forEach((trace, idx) => {
                        const traceBase = trace.name ? trace.name.replace(/ \(B\)$/, '') : trace.name;
                        if (traceBase === baseName) {
                            updateIndices.push(idx);
                            updateVisible.push(nextVisible);
                        }
                    });

                    if (updateIndices.length > 0) {
                        Plotly.restyle(gd, { visible: updateVisible }, updateIndices);
                    }
                    updateChannelYLim(activeTab, false);

                    return false; // Prevent default toggle of single trace
                });
                targetEl._legendClickBound = true;
            }
        }
    } finally {
        Object.defineProperty(state, 'currentActivePlotTab', originalDescriptor);
    }
}

export function addTrace(data, lap, field, color, width, dash = 'solid', customName = null, yaxis = 'y', showlegend = false) {
    const x = [];
    const y = [];
    lap.points.forEach(p => {
        if (p[field] !== undefined && p[field] !== null) {
            x.push(p.dist);
            y.push(p[field]);
        }
    });
    if (x.length === 0) return;
    const currentTab = state.currentActivePlotTab;
    const plotId = 'plot-area-' + currentTab;
    state.bottomPlotIndices = state.bottomPlotIndices || {};
    state.bottomPlotIndices[plotId] = state.bottomPlotIndices[plotId] || {};

    const lapId = lap.lapId || lap.id;
    if (lapId) {
        state.bottomPlotIndices[plotId][lapId] = state.bottomPlotIndices[plotId][lapId] || [];
        state.bottomPlotIndices[plotId][lapId].push(data.length);
    }

    let visible = true;
    if (customName && currentTab && state.plotTraceVisibility && state.plotTraceVisibility[currentTab]) {
        const baseName = customName.replace(/ \(B\)$/, '');
        if (state.plotTraceVisibility[currentTab][baseName] !== undefined) {
            visible = state.plotTraceVisibility[currentTab][baseName];
        }
    }

    data.push({
        x: x, y: y, mode: 'lines',
        name: customName || `Lap ${lap.lap_num} ${field}`,
        line: { color: color, width: width, dash: dash },
        hoverinfo: 'none', yaxis: yaxis === 'y' ? 'y' : yaxis, showlegend: showlegend,
        visible: visible
    });
}

export function addCommonPlotElements(layout) {
    const xPos = state.currentTargetDist;
    layout.shapes = layout.shapes || [];
    layout.annotations = layout.annotations || [];

    layout.shapes.push({
        type: 'line', x0: xPos, x1: xPos, yref: 'paper', y0: 0, y1: 1,
        line: { color: '#facc15', width: 2, dash: 'solid' }
    });

    if (state.trackData && state.trackData.turns) {
        state.trackData.turns.forEach(turn => {
            layout.shapes.push({
                type: 'rect', xref: 'x', yref: 'paper',
                x0: turn.start, x1: turn.end, y0: 0, y1: 1,
                fillcolor: 'rgba(59, 130, 246, 0.1)',
                line: { color: 'rgba(59, 130, 246, 0.2)', width: 1 },
                layer: 'below'
            });
            layout.annotations.push({
                x: (turn.start + turn.end) / 2, y: 1,
                xref: 'x', yref: 'paper', text: turn.name,
                showarrow: false, font: { size: 9, color: '#3b82f6' },
                yanchor: 'bottom'
            });
        });
    }
}

export function updateExpandablePlotsIndicator() {
    const xPos = state.currentTargetDist;
    const update = {
        'shapes[0].x0': xPos,
        'shapes[0].x1': xPos
    };

    if (state.activePlotChannels) {
        state.activePlotChannels.forEach(tab => {
            const id = 'plot-area-' + tab;
            const el = document.getElementById(id);
            if (el && el._fullLayout && typeof el.emit === 'function') Plotly.relayout(el, update);
        });
    }
    if (state.deltaPlotVisible) {
        const id = 'plot-area-delta';
        const el = document.getElementById(id);
        if (el && el._fullLayout && typeof el.emit === 'function') Plotly.relayout(el, update);
    }
    if (state.speedPlotVisible) {
        const id = 'plot-area-speed';
        const el = document.getElementById(id);
        if (el && el._fullLayout && typeof el.emit === 'function') Plotly.relayout(el, update);
    }
}

// Polling sync
function _getPlotRange(id) {
    const el = document.getElementById(id);
    if (!el || !el._fullLayout || !el._fullLayout.xaxis || !el.offsetParent || el.offsetWidth === 0) return null;
    const r = el._fullLayout.xaxis.range;
    return (r && r.length >= 2) ? r : null;
}

function _rangesEqual(a, b, tol = 0.0001) {
    if (!a || !b) return false;
    return Math.abs(a[0] - b[0]) < tol && Math.abs(a[1] - b[1]) < tol;
}

export function _syncPlotsFrame() {
    state._syncRafId = requestAnimationFrame(_syncPlotsFrame);
    if (state._syncApplying || state.activeTab !== 'map') return;

    const plotIds = [];
    if (state.activePlotChannels) {
        state.activePlotChannels.forEach(tab => plotIds.push('plot-area-' + tab));
    }
    if (state.deltaPlotVisible) plotIds.push('plot-area-delta');
    if (state.speedPlotVisible) plotIds.push('plot-area-speed');
    if (plotIds.length < 2) return;

    let sourceRange = null;
    let sourceId = null;
    for (const id of plotIds) {
        const r = _getPlotRange(id);
        if (r && !_rangesEqual(r, state._syncLastRange)) {
            sourceRange = r;
            sourceId = id;
            break;
        }
    }
    if (!sourceRange || _rangesEqual(sourceRange, state._syncLastRange)) return;

    state._syncLastRange = [sourceRange[0], sourceRange[1]];
    state.globalTelemetryXRange = state._syncLastRange;
    debouncedUpdateURL();

    state._syncApplying = true;
    const update = { 'xaxis.range': state._syncLastRange, 'xaxis.autorange': false };
    const promises = plotIds.map(id => {
        if (id === sourceId) return Promise.resolve(); // Skip the source of the change to avoid double-application/jitter

        const el = document.getElementById(id);
        if (!el || !el._fullLayout || typeof el.emit !== 'function') return Promise.resolve();

        const cur = el._fullLayout.xaxis.range;
        if (_rangesEqual(cur, state._syncLastRange)) return Promise.resolve();

        return Plotly.relayout(el, update);
    });

    Promise.all(promises).then(() => {
        state._syncApplying = false;
        // When syncing, also update the Y limits of the extra plots if they're visible.
        if (state.deltaPlotVisible) updateDeltaYLim(sourceId === 'plot-area-delta');
        if (state.speedPlotVisible) updateSpeedYLim(sourceId === 'plot-area-speed');
        if (state.activePlotChannels) {
            state.activePlotChannels.forEach(tab => {
                updateChannelYLim(tab, sourceId === 'plot-area-' + tab);
            });
        }

        // Finalize Y limits for all plots 150ms after the last range change (e.g. scroll/zoom end)
        if (state._syncEndTimeout) {
            clearTimeout(state._syncEndTimeout);
        }
        state._syncEndTimeout = setTimeout(() => {
            if (state.deltaPlotVisible) updateDeltaYLim(false);
            if (state.speedPlotVisible) updateSpeedYLim(false);
            if (state.activePlotChannels) {
                state.activePlotChannels.forEach(tab => {
                    updateChannelYLim(tab, false);
                });
            }
        }, 150);
    }).catch(() => {
        state._syncApplying = false;
    });
}

export function startSyncLoop() {
    if (state._syncRafId) cancelAnimationFrame(state._syncRafId);
    state._syncLastRange = null;
    state._syncRafId = requestAnimationFrame(_syncPlotsFrame);
}

export function updateDeltaYLim(skipRelayoutIfDragging = false) {
    if (!state.deltaPlotVisible) return;
    const gd = document.getElementById('plot-area-delta');
    if (!gd || !gd.data || gd.data.length === 0 || !gd._fullLayout || typeof gd.emit !== 'function') return;

    const range = state.globalTelemetryXRange;
    if (!range || range.length < 2) return;

    let min = Infinity;
    let max = -Infinity;
    gd.data.forEach(trace => {
        if (trace.name === 'DEBUG_DIAGONAL') return;
        for (let i = 0; i < trace.x.length; i++) {
            const x = trace.x[i];
            if (x >= range[0] && x <= range[1]) {
                const y = trace.y[i];
                if (typeof y === 'number' && !isNaN(y)) {
                    if (y < min) min = y;
                    if (y > max) max = y;
                }
            }
        }
    });

    if (min !== Infinity && max !== -Infinity) {
        const span = max - min;
        const padding = Math.max(0.01, span * 0.15);
        const newRange = [min - padding, max + padding];

        // Check if change is significant to avoid jitter
        if (gd._fullLayout && gd._fullLayout.yaxis && gd._fullLayout.yaxis.range) {
            const cur = gd._fullLayout.yaxis.range;
            const diff = Math.abs(cur[0] - newRange[0]) + Math.abs(cur[1] - newRange[1]);
            if (diff < 0.001) return;
        }

        // During an active drag/scroll on the delta plot itself, we skip the relayout call
        // because calling relayout (even for Y) during a drag causes Plotly to double-apply offsets.
        if (skipRelayoutIfDragging) return;

        Plotly.relayout(gd, { 'yaxis.range': newRange });
    }
}

export function initExpandablePlots() {
    const tabs = document.querySelectorAll('.plot-tab[data-tab]');
    const content = document.getElementById('plot-content');
    const resizer = document.getElementById('plot-resizer');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const target = tab.getAttribute('data-tab');
            if (!target) return;
            if (!isChannelAvailable(target)) return;

            if (state.activePlotChannels.has(target)) {
                state.activePlotChannels.delete(target);
                const targetArea = document.getElementById('plot-area-' + target);
                if (targetArea) {
                    Plotly.purge(targetArea);
                }
            } else {
                state.activePlotChannels.add(target);
            }
            updateExpandablePlotsVisibility();
            debouncedUpdateURL();
        });
    });

    const btnDelta = document.getElementById('btn-delta');
    if (btnDelta) {
        let preHoverColorMode = null;
        btnDelta.addEventListener('mouseenter', () => {
            if (state.trajectoryColorMode !== 'delta_t') {
                preHoverColorMode = state.trajectoryColorMode || 'pedals';
                updateAllPolylineColors('delta_t');
            }
        });
        btnDelta.addEventListener('mouseleave', () => {
            if (preHoverColorMode !== null) {
                const targetMode = state.trajectoryColorMode || preHoverColorMode;
                preHoverColorMode = null;
                updateAllPolylineColors(targetMode);
            }
        });
    }

    // Add mouseup listener to ensure Y-limits are finalized after a drag
    window.addEventListener('mouseup', () => {
        if (state.deltaPlotVisible) updateDeltaYLim(false);
        if (state.speedPlotVisible) updateSpeedYLim(false);
        if (state.activePlotChannels) {
            state.activePlotChannels.forEach(tab => {
                updateChannelYLim(tab, false);
            });
        }
    });
    let isResizingPlots = false;
    resizer.addEventListener('mousedown', (e) => {
        isResizingPlots = true;
        document.body.style.cursor = 'ns-resize';
        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!isResizingPlots) return;
        const block = document.getElementById('expandable-plots-block');
        const blockRect = block.getBoundingClientRect();
        const dy = blockRect.top + 48 - e.clientY;
        const newHeight = Math.max(100, Math.min(600, content.offsetHeight + dy));
        content.style.height = newHeight + 'px';

        if (state.activePlotChannels) {
            state.activePlotChannels.forEach(tab => {
                const gd = document.getElementById('plot-area-' + tab);
                if (gd && gd._fullLayout && gd.offsetParent !== null) Plotly.Plots.resize(gd);
            });
        }
        if (state.deltaPlotVisible) {
            const gd = document.getElementById('plot-area-delta');
            if (gd && gd._fullLayout && gd.offsetParent !== null) Plotly.Plots.resize(gd);
        }
        if (state.speedPlotVisible) {
            const gd = document.getElementById('plot-area-speed');
            if (gd && gd._fullLayout && gd.offsetParent !== null) Plotly.Plots.resize(gd);
        }
    });

    document.addEventListener('mouseup', () => {
        if (isResizingPlots) {
            isResizingPlots = false;
            document.body.style.cursor = '';
            if (state.activePlotChannels) {
                state.activePlotChannels.forEach(tab => {
                    const gd = document.getElementById('plot-area-' + tab);
                    if (gd && gd._fullLayout && gd.offsetParent !== null) Plotly.Plots.resize(gd);
                });
            }
            if (state.deltaPlotVisible) {
                const gd = document.getElementById('plot-area-delta');
                if (gd && gd._fullLayout && gd.offsetParent !== null) Plotly.Plots.resize(gd);
            }
            if (state.speedPlotVisible) {
                const gd = document.getElementById('plot-area-speed');
                if (gd && gd._fullLayout && gd.offsetParent !== null) Plotly.Plots.resize(gd);
            }
        }
    });
}

export function updateSpeedYLim(skipRelayoutIfDragging = false) {
    if (!state.speedPlotVisible) return;
    const gd = document.getElementById('plot-area-speed');
    if (!gd || !gd.data || gd.data.length === 0 || !gd._fullLayout || typeof gd.emit !== 'function') return;

    const range = state.globalTelemetryXRange;
    if (!range || range.length < 2) return;

    let min = Infinity;
    let max = -Infinity;
    gd.data.forEach(trace => {
        for (let i = 0; i < trace.x.length; i++) {
            const x = trace.x[i];
            if (x >= range[0] && x <= range[1]) {
                const y = trace.y[i];
                if (typeof y === 'number' && !isNaN(y)) {
                    if (y < min) min = y;
                    if (y > max) max = y;
                }
            }
        }
    });

    if (min !== Infinity && max !== -Infinity) {
        const span = max - min;
        const padding = Math.max(1, span * 0.1);
        const newRange = [Math.max(0, min - padding), max + padding];

        if (gd._fullLayout && gd._fullLayout.yaxis && gd._fullLayout.yaxis.range) {
            const cur = gd._fullLayout.yaxis.range;
            const diff = Math.abs(cur[0] - newRange[0]) + Math.abs(cur[1] - newRange[1]);
            if (diff < 0.01) return;
        }

        if (skipRelayoutIfDragging) return;

        Plotly.relayout(gd, {
            'yaxis.range': newRange,
            'yaxis.autorange': false
        });
    }
}

export function updateChannelYLim(tab, skipRelayoutIfDragging = false) {
    if (tab === 'pedals') return;
    const gd = document.getElementById('plot-area-' + tab);
    if (!gd || !gd.data || gd.data.length === 0 || !gd._fullLayout || typeof gd.emit !== 'function') return;

    const range = state.globalTelemetryXRange;
    if (!range || range.length < 2) return;

    const getMinMax = (traces) => {
        let min = Infinity;
        let max = -Infinity;
        traces.forEach(trace => {
            if (trace.visible === 'legendonly' || trace.visible === false) return;
            for (let i = 0; i < trace.x.length; i++) {
                const x = trace.x[i];
                if (x >= range[0] && x <= range[1]) {
                    const y = trace.y[i];
                    if (typeof y === 'number' && !isNaN(y)) {
                        if (y < min) min = y;
                        if (y > max) max = y;
                    }
                }
            }
        });
        return { min, max };
    };

    if (tab === 'steering') {
        const bounds = getMinMax(gd.data);
        if (bounds.min !== Infinity && bounds.max !== -Infinity) {
            const span = bounds.max - bounds.min;
            const padding = Math.max(1, span * 0.1);
            const min = bounds.min - padding;
            const max = bounds.max + padding;
            const { tickvals, ticktext } = getSteeringTicks(min, max);
            const newRange = [max, min];

            let cur = null;
            if (gd._fullLayout && gd._fullLayout.yaxis && gd._fullLayout.yaxis.range) {
                cur = gd._fullLayout.yaxis.range;
            }
            const isDiff = !cur || Math.abs(cur[0] - newRange[0]) + Math.abs(cur[1] - newRange[1]) >= 0.01;
            if (isDiff && !skipRelayoutIfDragging) {
                Plotly.relayout(gd, {
                    'yaxis.range': newRange,
                    'yaxis.autorange': false,
                    'yaxis.tickmode': 'array',
                    'yaxis.tickvals': tickvals,
                    'yaxis.ticktext': ticktext
                });
            }
        }
        return;
    }

    if (tab === 'patch_vel') {
        const latTraces = gd.data.filter(t => !t.yaxis || t.yaxis === 'y');
        const lonTraces = gd.data.filter(t => t.yaxis === 'y2');

        const latBounds = getMinMax(latTraces);
        const lonBounds = getMinMax(lonTraces);

        const update = {};
        let needsUpdate = false;

        if (latBounds.min !== Infinity && latBounds.max !== -Infinity) {
            const span = latBounds.max - latBounds.min;
            const padding = Math.max(0.01, span * 0.1);
            const newRange = [latBounds.min - padding, latBounds.max + padding];

            let cur = null;
            if (gd._fullLayout && gd._fullLayout.yaxis && gd._fullLayout.yaxis.range) {
                cur = gd._fullLayout.yaxis.range;
            }
            if (!cur || Math.abs(cur[0] - newRange[0]) + Math.abs(cur[1] - newRange[1]) >= 0.01) {
                update['yaxis.range'] = newRange;
                update['yaxis.autorange'] = false;
                needsUpdate = true;
            }
        }

        if (lonBounds.min !== Infinity && lonBounds.max !== -Infinity) {
            const span = lonBounds.max - lonBounds.min;
            const padding = Math.max(0.01, span * 0.1);
            const newRange = [lonBounds.min - padding, lonBounds.max + padding];

            let cur = null;
            if (gd._fullLayout && gd._fullLayout.yaxis2 && gd._fullLayout.yaxis2.range) {
                cur = gd._fullLayout.yaxis2.range;
            }
            if (!cur || Math.abs(cur[0] - newRange[0]) + Math.abs(cur[1] - newRange[1]) >= 0.01) {
                update['yaxis2.range'] = newRange;
                update['yaxis2.autorange'] = false;
                needsUpdate = true;
            }
        }

        if (needsUpdate && !skipRelayoutIfDragging) {
            Plotly.relayout(gd, update);
        }
    } else {
        const bounds = getMinMax(gd.data);
        if (bounds.min !== Infinity && bounds.max !== -Infinity) {
            const span = bounds.max - bounds.min;
            const padding = Math.max(0.01, span * 0.1);
            const newRange = [bounds.min - padding, bounds.max + padding];

            let cur = null;
            if (gd._fullLayout && gd._fullLayout.yaxis && gd._fullLayout.yaxis.range) {
                cur = gd._fullLayout.yaxis.range;
            }
            const isDiff = !cur || Math.abs(cur[0] - newRange[0]) + Math.abs(cur[1] - newRange[1]) >= 0.01;
            if (isDiff && !skipRelayoutIfDragging) {
                Plotly.relayout(gd, {
                    'yaxis.range': newRange,
                    'yaxis.autorange': false
                });
            }
        }
    }
}

export function updateExpandablePlotsVisibility() {
    const btnDelta = document.getElementById('btn-delta');
    const containerDelta = document.getElementById('delta-plot-container');
    const btnSpeed = document.getElementById('btn-speed');
    const containerSpeed = document.getElementById('speed-plot-container');
    const content = document.getElementById('plot-content');

    if (btnDelta) btnDelta.classList.toggle('active', state.deltaPlotVisible);
    if (containerDelta) containerDelta.style.display = state.deltaPlotVisible ? 'block' : 'none';

    if (btnSpeed) btnSpeed.classList.toggle('active', state.speedPlotVisible);
    if (containerSpeed) containerSpeed.style.display = state.speedPlotVisible ? 'block' : 'none';

    // Toggle the active class on plot tab elements and adjust visibility of their plot areas
    const tabs = document.querySelectorAll('.plot-tab[data-tab]');
    tabs.forEach(tab => {
        const target = tab.getAttribute('data-tab');
        const isActive = state.activePlotChannels.has(target);
        tab.classList.toggle('active', isActive);
        const targetArea = document.getElementById('plot-area-' + target);
        if (targetArea) {
            targetArea.style.display = isActive ? 'block' : 'none';
        }
    });

    const anyExtraVisible = state.deltaPlotVisible || state.speedPlotVisible || (state.activePlotChannels && state.activePlotChannels.size > 0);
    const numPlotsVisible = (state.deltaPlotVisible ? 1 : 0) + (state.speedPlotVisible ? 1 : 0) + (state.activePlotChannels ? state.activePlotChannels.size : 0);

    if (anyExtraVisible) {
        content.classList.add('expanded');
        content.style.height = (250 + (numPlotsVisible - 1) * 180) + 'px';
    } else {
        content.classList.remove('expanded');
        content.style.height = '0';
    }

    if ((!state.globalTelemetryXRange || state.globalTelemetryXRange.length !== 2 || (state.globalTelemetryXRange[0] === 0 && state.globalTelemetryXRange[1] === 100)) && state.trackData && state.trackData.lap_length) {
        state.globalTelemetryXRange = [0, state.trackData.lap_length];
    }

    renderExpandablePlots();
    if (anyExtraVisible) {
        startSyncLoop();
        setTimeout(() => {
            const range = state.globalTelemetryXRange || (state.trackData && state.trackData.lap_length ? [0, state.trackData.lap_length] : null);
            const plotIds = [];
            if (state.deltaPlotVisible) plotIds.push('plot-area-delta');
            if (state.speedPlotVisible) plotIds.push('plot-area-speed');
            if (state.activePlotChannels) {
                state.activePlotChannels.forEach(tab => plotIds.push('plot-area-' + tab));
            }

            plotIds.forEach(id => {
                const gd = document.getElementById(id);
                if (gd && gd._fullLayout && typeof gd.emit === 'function' && gd.offsetParent !== null && window.Plotly && window.Plotly.Plots) {
                    Plotly.Plots.resize(gd);
                    if (range && range.length === 2) {
                        Plotly.relayout(gd, { 'xaxis.range': range, 'xaxis.autorange': false });
                    }
                }
            });
        }, 150);
    }
}

export function togglePlot(plotName) {
    if (!plotName) return;
    const p = String(plotName).trim().toLowerCase();
    if (p === 'delta') {
        state.deltaPlotVisible = !state.deltaPlotVisible;
    } else if (p === 'speed') {
        state.speedPlotVisible = !state.speedPlotVisible;
    } else {
        let channel = p;
        if (channel === 'lat_force') channel = 'force';
        else if (channel === 'lat_patch_vel' || channel === 'long_patch_vel') channel = 'patch_vel';

        if (state.activePlotChannels.has(channel)) {
            state.activePlotChannels.delete(channel);
            const targetArea = document.getElementById('plot-area-' + channel);
            if (targetArea && typeof window !== 'undefined' && window.Plotly && window.Plotly.purge) {
                window.Plotly.purge(targetArea);
            }
        } else {
            state.activePlotChannels.add(channel);
        }
    }
    updateExpandablePlotsVisibility();
    debouncedUpdateURL();
}

export function setVisiblePlots(plotNames) {
    state.activePlotChannels.forEach(channel => {
        const targetArea = document.getElementById('plot-area-' + channel);
        if (targetArea && typeof window !== 'undefined' && window.Plotly && window.Plotly.purge) {
            window.Plotly.purge(targetArea);
        }
    });
    state.activePlotChannels.clear();

    if (plotNames === undefined || plotNames === null) {
        state.deltaPlotVisible = false;
        state.speedPlotVisible = false;
        updateExpandablePlotsVisibility();
        debouncedUpdateURL();
        return;
    }

    const raw = String(plotNames).trim();
    if (!raw || raw === 'none' || raw === '0' || raw === 'false') {
        state.deltaPlotVisible = false;
        state.speedPlotVisible = false;
    } else {
        const list = (Array.isArray(plotNames) ? plotNames : raw.split(',')).map(s => String(s).trim().toLowerCase());
        state.deltaPlotVisible = list.includes('delta');
        state.speedPlotVisible = list.includes('speed');
        list.forEach(item => {
            if (!item || item === 'delta' || item === 'speed') return;
            let channel = item;
            if (channel === 'lat_force') channel = 'force';
            else if (channel === 'lat_patch_vel' || channel === 'long_patch_vel') channel = 'patch_vel';
            state.activePlotChannels.add(channel);
        });
    }

    updateExpandablePlotsVisibility();
    debouncedUpdateURL();
}

function renderCustomLegend(tab, targetEl) {
    if (!targetEl) return;
    targetEl.style.position = 'relative';

    // Remove existing legend if any
    const existing = targetEl.querySelector('.custom-plot-legend');
    if (existing) {
        existing.remove();
    }

    const legendDiv = document.createElement('div');
    legendDiv.className = 'custom-plot-legend';
    legendDiv.style.position = 'absolute';
    legendDiv.style.top = '10px';
    legendDiv.style.right = '20px';
    legendDiv.style.zIndex = '10';
    legendDiv.style.background = 'rgba(15, 23, 42, 0.85)';
    legendDiv.style.backdropFilter = 'blur(8px)';
    legendDiv.style.border = '1px solid rgba(255, 255, 255, 0.1)';
    legendDiv.style.borderRadius = '8px';
    legendDiv.style.padding = '8px 12px';
    legendDiv.style.display = 'flex';
    legendDiv.style.flexDirection = 'column';
    legendDiv.style.gap = '8px';
    legendDiv.style.boxShadow = '0 4px 12px rgba(0, 0, 0, 0.5)';
    legendDiv.style.pointerEvents = 'auto';

    if (tab === 'force') {
        // Initialize state variables if not set
        if (!state.forcePlotActiveWheels) {
            state.forcePlotActiveWheels = { fl: true, fr: true, rl: true, rr: true };
        }
        if (!state.forcePlotActiveComponents) {
            state.forcePlotActiveComponents = { lat: true, lon: true, tot: false };
        }

        legendDiv.innerHTML = `
            <!-- Wheel Grid: 2 columns, 2 rows -->
            <div style="display: flex; flex-direction: column; gap: 4px; min-width: 110px;">
                <div style="display: flex; gap: 10px; justify-content: space-between;">
                    <div class="legend-item" data-wheel="fl" style="cursor: pointer; display: flex; align-items: center; gap: 6px; font-size: 10px; font-weight: 700; color: ${state.forcePlotActiveWheels.fl ? '#fff' : '#64748b'}; opacity: ${state.forcePlotActiveWheels.fl ? 1 : 0.5}; user-select: none;">
                        <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #38bdf8;"></span> FL
                    </div>
                    <div class="legend-item" data-wheel="fr" style="cursor: pointer; display: flex; align-items: center; gap: 6px; font-size: 10px; font-weight: 700; color: ${state.forcePlotActiveWheels.fr ? '#fff' : '#64748b'}; opacity: ${state.forcePlotActiveWheels.fr ? 1 : 0.5}; user-select: none;">
                        <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #22c55e;"></span> FR
                    </div>
                </div>
                <div style="display: flex; gap: 10px; justify-content: space-between;">
                    <div class="legend-item" data-wheel="rl" style="cursor: pointer; display: flex; align-items: center; gap: 6px; font-size: 10px; font-weight: 700; color: ${state.forcePlotActiveWheels.rl ? '#fff' : '#64748b'}; opacity: ${state.forcePlotActiveWheels.rl ? 1 : 0.5}; user-select: none;">
                        <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #ef4444;"></span> RL
                    </div>
                    <div class="legend-item" data-wheel="rr" style="cursor: pointer; display: flex; align-items: center; gap: 6px; font-size: 10px; font-weight: 700; color: ${state.forcePlotActiveWheels.rr ? '#fff' : '#64748b'}; opacity: ${state.forcePlotActiveWheels.rr ? 1 : 0.5}; user-select: none;">
                        <span style="display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #fb923c;"></span> RR
                    </div>
                </div>
            </div>
            
            <div style="height: 1px; background: rgba(255, 255, 255, 0.15); margin: 2px 0;"></div>

            <!-- Component Selectors -->
            <div style="display: flex; gap: 4px; justify-content: center;">
                <button class="legend-toggle-btn" data-comp="lat" style="background: ${state.forcePlotActiveComponents.lat ? 'rgba(56, 189, 248, 0.25)' : 'transparent'}; border: 1px solid ${state.forcePlotActiveComponents.lat ? '#38bdf8' : 'rgba(255,255,255,0.1)'}; color: ${state.forcePlotActiveComponents.lat ? '#fff' : '#94a3b8'}; padding: 3px 6px; border-radius: 4px; font-size: 9px; font-weight: 700; cursor: pointer; transition: all 0.2s; outline: none; border-style: solid;">Lat</button>
                <button class="legend-toggle-btn" data-comp="lon" style="background: ${state.forcePlotActiveComponents.lon ? 'rgba(168, 85, 247, 0.25)' : 'transparent'}; border: 1px solid ${state.forcePlotActiveComponents.lon ? '#a855f7' : 'rgba(255,255,255,0.1)'}; color: ${state.forcePlotActiveComponents.lon ? '#fff' : '#94a3b8'}; padding: 3px 6px; border-radius: 4px; font-size: 9px; font-weight: 700; cursor: pointer; transition: all 0.2s; outline: none; border-style: dotted;">Lon</button>
                <button class="legend-toggle-btn" data-comp="tot" style="background: ${state.forcePlotActiveComponents.tot ? 'rgba(251, 146, 60, 0.25)' : 'transparent'}; border: 1px solid ${state.forcePlotActiveComponents.tot ? '#fb923c' : 'rgba(255,255,255,0.1)'}; color: ${state.forcePlotActiveComponents.tot ? '#fff' : '#94a3b8'}; padding: 3px 6px; border-radius: 4px; font-size: 9px; font-weight: 700; cursor: pointer; transition: all 0.2s; outline: none; border-style: solid;">Total</button>
            </div>
        `;

        // Event listeners for Wheel toggles
        legendDiv.querySelectorAll('.legend-item').forEach(el => {
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                const wheel = el.getAttribute('data-wheel');
                state.forcePlotActiveWheels[wheel] = !state.forcePlotActiveWheels[wheel];
                renderSingleChannelPlot('force');
            });
        });

        // Event listeners for Component toggles with exclusivity
        legendDiv.querySelectorAll('.legend-toggle-btn').forEach(el => {
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                const comp = el.getAttribute('data-comp');
                if (comp === 'tot') {
                    state.forcePlotActiveComponents.tot = !state.forcePlotActiveComponents.tot;
                    if (state.forcePlotActiveComponents.tot) {
                        state.forcePlotActiveComponents.lat = false;
                        state.forcePlotActiveComponents.lon = false;
                    } else {
                        state.forcePlotActiveComponents.lat = true;
                        state.forcePlotActiveComponents.lon = true;
                    }
                } else {
                    state.forcePlotActiveComponents[comp] = !state.forcePlotActiveComponents[comp];
                    if (state.forcePlotActiveComponents.lat || state.forcePlotActiveComponents.lon) {
                        state.forcePlotActiveComponents.tot = false;
                    } else {
                        state.forcePlotActiveComponents.tot = true;
                    }
                }
                renderSingleChannelPlot('force');
            });
        });

    } else if (tab === 'gforce') {
        if (!state.gforcePlotActiveComponents) {
            state.gforcePlotActiveComponents = { lat: true, lon: true, tot: false };
        }

        const hasLat = isComponentAvailable('gy');
        const hasLon = isComponentAvailable('gx');
        const buttons = [];
        if (hasLat) {
            buttons.push(`<button class="legend-toggle-btn" data-comp="lat" style="background: ${state.gforcePlotActiveComponents.lat ? 'rgba(236, 72, 153, 0.25)' : 'transparent'}; border: 1px solid ${state.gforcePlotActiveComponents.lat ? '#ec4899' : 'rgba(255,255,255,0.1)'}; color: ${state.gforcePlotActiveComponents.lat ? '#fff' : '#94a3b8'}; padding: 4px 8px; border-radius: 4px; font-size: 9px; font-weight: 700; cursor: pointer; transition: all 0.2s; outline: none; border-style: solid;">Lat</button>`);
        }
        if (hasLon) {
            buttons.push(`<button class="legend-toggle-btn" data-comp="lon" style="background: ${state.gforcePlotActiveComponents.lon ? 'rgba(168, 85, 247, 0.25)' : 'transparent'}; border: 1px solid ${state.gforcePlotActiveComponents.lon ? '#a855f7' : 'rgba(255,255,255,0.1)'}; color: ${state.gforcePlotActiveComponents.lon ? '#fff' : '#94a3b8'}; padding: 4px 8px; border-radius: 4px; font-size: 9px; font-weight: 700; cursor: pointer; transition: all 0.2s; outline: none; border-style: solid;">Lon</button>`);
        }
        if (hasLat && hasLon) {
            buttons.push(`<button class="legend-toggle-btn" data-comp="tot" style="background: ${state.gforcePlotActiveComponents.tot ? 'rgba(251, 146, 60, 0.25)' : 'transparent'}; border: 1px solid ${state.gforcePlotActiveComponents.tot ? '#fb923c' : 'rgba(255,255,255,0.1)'}; color: ${state.gforcePlotActiveComponents.tot ? '#fff' : '#94a3b8'}; padding: 4px 8px; border-radius: 4px; font-size: 9px; font-weight: 700; cursor: pointer; transition: all 0.2s; outline: none; border-style: solid;">Total</button>`);
        }

        legendDiv.innerHTML = `
            <!-- Component Selectors -->
            <div style="display: flex; gap: 6px; justify-content: center; min-width: 140px;">
                ${buttons.join('')}
            </div>
        `;

        // Event listeners for Component toggles
        legendDiv.querySelectorAll('.legend-toggle-btn').forEach(el => {
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                const comp = el.getAttribute('data-comp');
                state.gforcePlotActiveComponents[comp] = !state.gforcePlotActiveComponents[comp];
                renderSingleChannelPlot('gforce');
            });
        });
    } else if (tab === 'gyro') {
        if (!state.gyroPlotActiveComponents) {
            state.gyroPlotActiveComponents = { yaw: true, pitch: false, roll: false };
        }

        const hasYaw = isComponentAvailable('gyro_yaw');
        const hasPitch = isComponentAvailable('gyro_pitch');
        const hasRoll = isComponentAvailable('gyro_roll');

        const buttons = [];
        if (hasYaw) {
            buttons.push(`<button class="legend-toggle-btn" data-comp="yaw" style="background: ${state.gyroPlotActiveComponents.yaw ? 'rgba(56, 189, 248, 0.25)' : 'transparent'}; border: 1px solid ${state.gyroPlotActiveComponents.yaw ? '#38bdf8' : 'rgba(255,255,255,0.1)'}; color: ${state.gyroPlotActiveComponents.yaw ? '#fff' : '#94a3b8'}; padding: 4px 8px; border-radius: 4px; font-size: 9px; font-weight: 700; cursor: pointer; transition: all 0.2s; outline: none; border-style: solid;">Yaw</button>`);
        }
        if (hasPitch) {
            buttons.push(`<button class="legend-toggle-btn" data-comp="pitch" style="background: ${state.gyroPlotActiveComponents.pitch ? 'rgba(34, 197, 94, 0.25)' : 'transparent'}; border: 1px solid ${state.gyroPlotActiveComponents.pitch ? '#22c55e' : 'rgba(255,255,255,0.1)'}; color: ${state.gyroPlotActiveComponents.pitch ? '#fff' : '#94a3b8'}; padding: 4px 8px; border-radius: 4px; font-size: 9px; font-weight: 700; cursor: pointer; transition: all 0.2s; outline: none; border-style: solid;">Pitch</button>`);
        }
        if (hasRoll) {
            buttons.push(`<button class="legend-toggle-btn" data-comp="roll" style="background: ${state.gyroPlotActiveComponents.roll ? 'rgba(251, 146, 60, 0.25)' : 'transparent'}; border: 1px solid ${state.gyroPlotActiveComponents.roll ? '#fb923c' : 'rgba(255,255,255,0.1)'}; color: ${state.gyroPlotActiveComponents.roll ? '#fff' : '#94a3b8'}; padding: 4px 8px; border-radius: 4px; font-size: 9px; font-weight: 700; cursor: pointer; transition: all 0.2s; outline: none; border-style: solid;">Roll</button>`);
        }

        legendDiv.innerHTML = `
            <!-- Component Selectors -->
            <div style="display: flex; gap: 6px; justify-content: center; min-width: 160px;">
                ${buttons.join('')}
            </div>
        `;

        // Event listeners for Component toggles
        legendDiv.querySelectorAll('.legend-toggle-btn').forEach(el => {
            el.addEventListener('click', (e) => {
                e.stopPropagation();
                const comp = el.getAttribute('data-comp');
                state.gyroPlotActiveComponents[comp] = !state.gyroPlotActiveComponents[comp];
                renderSingleChannelPlot('gyro');
            });
        });
    }

    targetEl.appendChild(legendDiv);
}

export function isComponentAvailable(key) {
    if (!state.allSessionsData || state.allSessionsData.length === 0) return true;
    const choices = COLUMN_MAPPINGS[key] || [key];
    return state.allSessionsData.some(session => {
        const cols = session.columns;
        if (!Array.isArray(cols)) return false;
        return choices.some(choice =>
            cols.some(col => col.toLowerCase() === choice.toLowerCase())
        );
    });
}

/**
 * Returns true if the given channel tab has at least one matching column
 * in any of the loaded sessions. Uses session.columns which is available
 * without loading the full channel data.
 */
export function isChannelAvailable(channel) {
    if (!state.allSessionsData || state.allSessionsData.length === 0) return false;

    const spec = CHANNEL_AVAILABILITY[channel];
    if (!spec) return true; // Unknown channel — show by default

    return state.allSessionsData.some(session => {
        const cols = session.columns;
        if (!Array.isArray(cols)) return false;

        if (spec.baseColumns) {
            // Direct column name check (case-insensitive)
            return spec.baseColumns.some(base =>
                cols.some(col => col.toLowerCase() === base.toLowerCase())
            );
        }

        if (spec.keys) {
            // Check via COLUMN_MAPPINGS
            return spec.keys.some(key => {
                const choices = COLUMN_MAPPINGS[key] || [];
                return choices.some(choice =>
                    cols.some(col => col.toLowerCase() === choice.toLowerCase())
                );
            });
        }

        return false;
    });
}

/**
 * Show or hide plot tab buttons based on channel availability in the loaded
 * session data. Call this after allSessionsData has been populated.
 * Tabs for unavailable channels are hidden; if a hidden tab is currently
 * active it is also deactivated.
 */
export function updateTabAvailability() {
    const tabs = document.querySelectorAll('.plot-tab[data-tab]');
    let changed = false;
    tabs.forEach(tab => {
        const target = tab.getAttribute('data-tab');
        const available = isChannelAvailable(target);
        tab.style.display = available ? '' : 'none';

        // Deactivate a hidden tab that was previously enabled
        if (!available && state.activePlotChannels && state.activePlotChannels.has(target)) {
            state.activePlotChannels.delete(target);
            const targetArea = document.getElementById('plot-area-' + target);
            if (targetArea && typeof window !== 'undefined' && window.Plotly && window.Plotly.purge) {
                window.Plotly.purge(targetArea);
            }
            changed = true;
        }
    });
    if (changed) {
        updateExpandablePlotsVisibility();
    }
}
