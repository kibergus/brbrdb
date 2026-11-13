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
 * Dynamic workspace custom channels and plot-group tab/area management.
 * Supports distance-aligned and session-time-aligned series interpolated onto lap.points.
 */
import { state } from './state.js';
import {
    loadedChannels,
    loadingChannels,
    isBuiltinPlotChannel,
    isChannelAvailable,
    updateExpandablePlotsVisibility,
    updateTabAvailability,
    renderSingleChannelPlot
} from './plots_sync.js';
import { normalizePlotGroupId } from './plot_toolbar.js';

export function customChannelFieldKey(channelId) {
    return `_custom_${String(channelId)}`;
}

const _inflightPayloadFetches = new Map();

/**
 * Binary-search linear interpolation of `targetX` over sorted `xs` / `ys`.
 * @param {number[]} xs
 * @param {number[]} ys
 * @param {number} targetX
 * @param {number} boundaryClampTolerance
 * @returns {number|null}
 */
export function interpolateLinearSeries(xs, ys, targetX, boundaryClampTolerance = 0) {
    const n = xs ? xs.length : 0;
    if (n === 0 || !ys || ys.length !== n || !Number.isFinite(targetX)) {
        return null;
    }
    if (n === 1) {
        return Math.abs(targetX - xs[0]) <= Math.max(1e-6, boundaryClampTolerance) ? ys[0] : null;
    }
    if (targetX < xs[0]) {
        if (boundaryClampTolerance > 0 && (xs[0] - targetX) <= boundaryClampTolerance) {
            return ys[0];
        }
        return null;
    }
    if (targetX > xs[n - 1]) {
        if (boundaryClampTolerance > 0 && (targetX - xs[n - 1]) <= boundaryClampTolerance) {
            return ys[n - 1];
        }
        return null;
    }

    let lo = 0;
    let hi = n - 1;
    while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (xs[mid] <= targetX) {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    const x0 = xs[lo];
    const x1 = xs[hi];
    const y0 = ys[lo];
    const y1 = ys[hi];
    if (!Number.isFinite(y0) || !Number.isFinite(y1)) {
        return null;
    }
    if (x1 === x0) {
        return y0;
    }
    return y0 + ((y1 - y0) * (targetX - x0)) / (x1 - x0);
}

function normalizeNumericPairs(rawX, rawY) {
    if (!Array.isArray(rawX) || !Array.isArray(rawY)) {
        return { xs: [], ys: [] };
    }
    const len = Math.min(rawX.length, rawY.length);
    const pairs = [];
    let monotonic = true;
    let prevX = -Infinity;

    for (let i = 0; i < len; i++) {
        const x = Number(rawX[i]);
        const y = Number(rawY[i]);
        if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
        if (x < prevX) monotonic = false;
        prevX = x;
        pairs.push([x, y]);
    }

    if (!monotonic) {
        pairs.sort((a, b) => a[0] - b[0]);
    }

    const xs = new Array(pairs.length);
    const ys = new Array(pairs.length);
    for (let i = 0; i < pairs.length; i++) {
        xs[i] = pairs[i][0];
        ys[i] = pairs[i][1];
    }
    return { xs, ys };
}

function clearCustomFieldFromAllLaps(channelId) {
    const fieldKey = customChannelFieldKey(channelId);
    if (state.lapDataLookup && typeof state.lapDataLookup === 'object') {
        Object.values(state.lapDataLookup).forEach((lap) => {
            if (lap && Array.isArray(lap.points)) {
                for (let i = 0; i < lap.points.length; i++) {
                    if (lap.points[i] && fieldKey in lap.points[i]) {
                        delete lap.points[i][fieldKey];
                    }
                }
            }
        });
    }
}

function sessionMatchesTarget(session, targetSessionId, targetDriver) {
    if (!session) return false;
    if (targetSessionId) {
        const sId = String(session.session_id || '').toLowerCase();
        const want = String(targetSessionId).toLowerCase();
        if (sId === want || sId.includes(want)) {
            return true;
        }
        return false;
    }
    if (targetDriver) {
        const dName = String(session.driver_name || '').toLowerCase();
        return dName === String(targetDriver).toLowerCase();
    }
    return true;
}

/**
 * Interpolates a parsed custom channel definition onto matching laps in `state.allSessionsData`
 * and `state.lapDataLookup`.
 */
export function applyCustomChannelPayloadToLaps(channelPayload) {
    if (!channelPayload || !channelPayload.channel_id) return;
    const channelId = String(channelPayload.channel_id);
    const fieldKey = customChannelFieldKey(channelId);
    const seriesList = Array.isArray(channelPayload.series) ? channelPayload.series : [];
    const matchMeta = (channelPayload.match && typeof channelPayload.match === 'object') ? channelPayload.match : {};

    clearCustomFieldFromAllLaps(channelId);
    if (seriesList.length === 0) return;

    const sessions = Array.isArray(state.allSessionsData) ? state.allSessionsData : [];

    for (const sEntry of seriesList) {
        if (!sEntry) continue;
        const align = String(sEntry.align || 'distance').toLowerCase() === 'time' ? 'time' : 'distance';
        const rawX = Array.isArray(sEntry.x)
            ? sEntry.x
            : (align === 'time' ? (sEntry.time || sEntry.dist) : (sEntry.dist || sEntry.time));
        const rawY = Array.isArray(sEntry.y) ? sEntry.y : sEntry.values;
        const { xs, ys } = normalizeNumericPairs(rawX, rawY);
        if (xs.length === 0) continue;

        const targetSessionId = sEntry.session_id || matchMeta.session_id || null;
        const targetDriver = sEntry.driver || matchMeta.driver || null;
        const hasLapNum = sEntry.lap_num !== undefined && sEntry.lap_num !== null && sEntry.lap_num !== '';
        const targetLapNum = hasLapNum ? Number(sEntry.lap_num) : null;
        const clampTol = align === 'distance' ? 1.0 : 0.0;

        const targetLaps = [];
        if (sessions.length > 0) {
            for (const session of sessions) {
                if (!sessionMatchesTarget(session, targetSessionId, targetDriver)) continue;
                const laps = Array.isArray(session.laps) ? session.laps : [];
                for (const lapMeta of laps) {
                    if (targetLapNum !== null && Number(lapMeta.lap_num) !== targetLapNum) continue;
                    const lapId = `${session.session_id}-${lapMeta.lap_num}`;
                    const lapObj = (state.lapDataLookup && state.lapDataLookup[lapId]) || lapMeta;
                    if (lapObj && Array.isArray(lapObj.points)) {
                        targetLaps.push(lapObj);
                    }
                }
            }
        } else if (state.lapDataLookup) {
            for (const lapObj of Object.values(state.lapDataLookup)) {
                if (!lapObj || !Array.isArray(lapObj.points)) continue;
                if (targetSessionId && String(lapObj.session_id || '').toLowerCase() !== String(targetSessionId).toLowerCase()) {
                    continue;
                }
                if (targetLapNum !== null && Number(lapObj.lap_num) !== targetLapNum) continue;
                targetLaps.push(lapObj);
            }
        }

        for (const lapObj of targetLaps) {
            const pts = lapObj.points;
            for (let i = 0; i < pts.length; i++) {
                const pt = pts[i];
                const sampleX = align === 'time' ? Number(pt.time) : Number(pt.dist);
                const val = interpolateLinearSeries(xs, ys, sampleX, clampTol);
                if (val !== null) {
                    pt[fieldKey] = val;
                }
            }
        }
    }
}

function mountDynamicPlotGroupElements() {
    if (typeof document === 'undefined') return;
    const plotContent = document.getElementById('plot-content');
    if (!plotContent) return;

    const desiredGroupIds = new Set(state.customPlotGroups ? state.customPlotGroups.keys() : []);

    // Unmount stale dynamic plot areas no longer in the active study
    plotContent.querySelectorAll('.dynamic-plot-area').forEach((area) => {
        const gId = area.getAttribute('data-custom-group') || area.id.replace(/^plot-area-/, '');
        if (!gId || !desiredGroupIds.has(gId)) {
            if (typeof window !== 'undefined' && window.Plotly && typeof window.Plotly.purge === 'function') {
                window.Plotly.purge(area);
            }
            area.remove();
        }
    });

    // Mount plot areas for all custom groups (regardless of whether their toolbar item is top-level or in a folder)
    for (const [groupId] of state.customPlotGroups.entries()) {
        let area = document.getElementById(`plot-area-${groupId}`);
        if (!area) {
            area = document.createElement('div');
            area.id = `plot-area-${groupId}`;
            area.className = 'plot-area dynamic-plot-area';
            area.setAttribute('data-custom-group', groupId);
            area.style.width = '100%';
            area.style.height = '100%';
            area.style.display = (state.activePlotChannels && state.activePlotChannels.has(groupId)) ? 'block' : 'none';
            plotContent.appendChild(area);
        }
    }

    updateTabAvailability();
}

function normalizeManifestGroups(manifestChannels, manifestPlotGroups) {
    const channelMap = new Map();
    for (const rawCh of manifestChannels || []) {
        if (!rawCh) continue;
        const chId = String(rawCh.channel_id || rawCh.id || '').trim();
        if (!chId) continue;
        const normCh = {
            ...rawCh,
            channel_id: chId,
            label: rawCh.label ? String(rawCh.label) : chId,
            unit: rawCh.unit ? String(rawCh.unit) : '',
            color: rawCh.color ? String(rawCh.color) : '#38bdf8',
            group: rawCh.group ? normalizePlotGroupId(rawCh.group) : '',
            folder: rawCh.folder ? String(rawCh.folder).trim().toLowerCase() : '',
            folder_label: rawCh.folder_label ? String(rawCh.folder_label).trim() : ''
        };
        channelMap.set(chId, normCh);
    }

    const groupMap = new Map();
    const coveredChannelIds = new Set();

    for (const rawGroup of manifestPlotGroups || []) {
        if (!rawGroup || !rawGroup.id) continue;
        const gId = normalizePlotGroupId(rawGroup.id);
        if (!gId || gId === '*' || gId === 'speed' || gId === 'delta') continue;
        const normChannels = (rawGroup.channels || []).map((item) => {
            const obj = typeof item === 'string' ? { channel_id: item } : (item || {});
            const cId = String(obj.channel_id || obj.id || '').trim();
            coveredChannelIds.add(cId);
            const base = channelMap.get(cId);
            return {
                channel_id: cId,
                label: obj.label || (base ? base.label : cId),
                unit: obj.unit || (base ? base.unit : ''),
                color: obj.color || (base ? base.color : '#38bdf8'),
                yaxis: obj.yaxis === 'y2' ? 'y2' : 'y'
            };
        }).filter((c) => Boolean(c.channel_id));

        groupMap.set(gId, {
            id: gId,
            label: rawGroup.label ? String(rawGroup.label) : gId.toUpperCase(),
            folder: rawGroup.folder ? String(rawGroup.folder).trim().toLowerCase() : '',
            folder_label: rawGroup.folder_label ? String(rawGroup.folder_label).trim() : '',
            y_label: rawGroup.y_label ? String(rawGroup.y_label) : '',
            y2_label: rawGroup.y2_label ? String(rawGroup.y2_label) : '',
            y_range: Array.isArray(rawGroup.y_range) && rawGroup.y_range.length === 2 ? rawGroup.y_range : null,
            color_mode: rawGroup.color_mode === 'lap' ? 'lap' : 'channel',
            channels: normChannels
        });
    }

    // Synthesize fallback groups for any channels not yet covered by explicit plot_groups
    for (const ch of channelMap.values()) {
        if (coveredChannelIds.has(ch.channel_id)) continue;
        const gId = ch.group || normalizePlotGroupId(ch.channel_id);
        if (!gId || gId === '*' || gId === 'speed' || gId === 'delta') continue;
        if (!groupMap.has(gId)) {
            groupMap.set(gId, {
                id: gId,
                label: ch.group_label || (ch.group ? ch.group.toUpperCase() : ch.label),
                folder: ch.folder || '',
                folder_label: ch.folder_label || '',
                y_label: ch.unit ? `${ch.label} (${ch.unit})` : ch.label,
                y2_label: '',
                y_range: null,
                color_mode: ch.group ? 'channel' : 'lap',
                channels: []
            });
        } else {
            const existingGroup = groupMap.get(gId);
            if (!existingGroup.folder && ch.folder) existingGroup.folder = ch.folder;
            if (!existingGroup.folder_label && ch.folder_label) existingGroup.folder_label = ch.folder_label;
        }
        groupMap.get(gId).channels.push({
            channel_id: ch.channel_id,
            label: ch.label,
            unit: ch.unit,
            color: ch.color,
            yaxis: 'y'
        });
    }

    return { channelMap, groupMap };
}

/**
 * Synchronizes `state.customChannels`, `state.customPlotGroups`, and `state.customPlotLayout`
 * from the workspace manifest, invalidating cached payloads whose `mtime` or `url` changed
 * and updating the 2-level plot toolbar.
 */
export function syncCustomChannelsManifest(
    manifestChannels = [],
    manifestPlotGroups = [],
    manifestPlotLayout = undefined
) {
    state.customChannels = state.customChannels || new Map();
    state.customPlotGroups = state.customPlotGroups || new Map();
    state.customChannelPayloads = state.customChannelPayloads || new Map();
    if (manifestPlotLayout !== undefined) {
        state.customPlotLayout = (Array.isArray(manifestPlotLayout) && manifestPlotLayout.length > 0)
            ? manifestPlotLayout
            : null;
    }

    const { channelMap, groupMap } = normalizeManifestGroups(manifestChannels, manifestPlotGroups);
    const invalidatedChannelIds = new Set();

    // Detect removed channels
    for (const [oldChId] of state.customChannels.entries()) {
        if (!channelMap.has(oldChId)) {
            invalidatedChannelIds.add(oldChId);
            clearCustomFieldFromAllLaps(oldChId);
        }
    }

    // Detect changed mtime or URL on incoming channels
    for (const [chId, nextDesc] of channelMap.entries()) {
        const prevDesc = state.customChannels.get(chId);
        const cacheKey = nextDesc.url || nextDesc.path || chId;
        const cachedPayload = state.customChannelPayloads.get(cacheKey);

        const descChanged = Boolean(
            prevDesc && (prevDesc.mtime !== nextDesc.mtime || prevDesc.url !== nextDesc.url)
        );
        const cacheStale = Boolean(
            cachedPayload && (cachedPayload.mtime !== nextDesc.mtime || cachedPayload.url !== nextDesc.url)
        );

        if (descChanged || cacheStale) {
            state.customChannelPayloads.delete(cacheKey);
            _inflightPayloadFetches.delete(cacheKey);
            invalidatedChannelIds.add(chId);
            clearCustomFieldFromAllLaps(chId);
        }
    }

    // Detect removed or modified plot groups so loadedChannels is invalidated properly
    const invalidatedGroups = new Set();
    const removedGroupIds = [];
    for (const [oldGroupId] of state.customPlotGroups.entries()) {
        if (!groupMap.has(oldGroupId)) {
            loadedChannels.delete(oldGroupId);
            loadingChannels.delete(oldGroupId);
            removedGroupIds.push(oldGroupId);
        }
    }

    for (const [gId, nextGroup] of groupMap.entries()) {
        const prevGroup = state.customPlotGroups.get(gId);
        const prevJson = prevGroup ? JSON.stringify(prevGroup) : '';
        const nextJson = JSON.stringify(nextGroup);
        const hasInvalidatedChild = nextGroup.channels.some((c) => invalidatedChannelIds.has(c.channel_id));
        if (!prevGroup || hasInvalidatedChild || prevJson !== nextJson) {
            loadedChannels.delete(gId);
            loadingChannels.delete(gId);
            invalidatedGroups.add(gId);
        }
    }

    state.customChannels = channelMap;
    state.customPlotGroups = groupMap;

    // Deactivate removed custom groups (including those that lived inside folders) unless backed by a native built-in
    for (const oldGroupId of removedGroupIds) {
        if (state.activePlotChannels && state.activePlotChannels.has(oldGroupId)) {
            const backedByBuiltin = isBuiltinPlotChannel(oldGroupId) && isChannelAvailable(oldGroupId);
            if (!backedByBuiltin) {
                state.activePlotChannels.delete(oldGroupId);
            }
        }
    }

    mountDynamicPlotGroupElements();
    updateExpandablePlotsVisibility();

    for (const gId of invalidatedGroups) {
        if (state.activePlotChannels && state.activePlotChannels.has(gId)) {
            renderSingleChannelPlot(gId);
        }
    }
}

/**
 * Fetches and interpolates all custom channels belonging to `groupId`.
 */
export async function ensureCustomPlotGroupLoaded(groupId) {
    const groupDef = state.customPlotGroups ? state.customPlotGroups.get(groupId) : null;
    if (!groupDef) return;

    state.customChannelPayloads = state.customChannelPayloads || new Map();

    // Group required channel descriptors by URL so multi-channel JSON files are fetched only once
    const descriptorsByKey = new Map();
    for (const chRef of groupDef.channels || []) {
        const desc = state.customChannels ? state.customChannels.get(chRef.channel_id) : null;
        if (!desc) continue;
        const cacheKey = desc.url || desc.path || desc.channel_id;
        if (!descriptorsByKey.has(cacheKey)) {
            descriptorsByKey.set(cacheKey, desc);
        }
    }

    const fetchTasks = [];
    for (const [cacheKey, desc] of descriptorsByKey.entries()) {
        const cached = state.customChannelPayloads.get(cacheKey);
        if (cached && cached.mtime === desc.mtime && cached.channelsById) {
            continue;
        }
        if (desc.inlinePayload) {
            const list = Array.isArray(desc.inlinePayload.channels)
                ? desc.inlinePayload.channels
                : [desc.inlinePayload];
            const channelsById = new Map();
            list.forEach((item) => {
                if (item && item.channel_id) channelsById.set(String(item.channel_id), item);
            });
            state.customChannelPayloads.set(cacheKey, {
                mtime: desc.mtime,
                url: desc.url,
                channelsById
            });
            continue;
        }
        if (!desc.url || typeof fetch !== 'function') continue;

        const inflightKey = `${cacheKey}@${desc.mtime ?? ''}`;
        if (_inflightPayloadFetches.has(inflightKey)) {
            fetchTasks.push(_inflightPayloadFetches.get(inflightKey));
            continue;
        }

        const taskPromise = (async () => {
            try {
                const resp = await fetch(desc.url, { cache: 'no-store' });
                if (!resp.ok) {
                    throw new Error(`Failed to fetch custom channel ${desc.url}: HTTP ${resp.status}`);
                }
                const parsed = await resp.json();
                const list = Array.isArray(parsed.channels) ? parsed.channels : [parsed];
                const channelsById = new Map();
                list.forEach((item) => {
                    if (item && item.channel_id) {
                        channelsById.set(String(item.channel_id), item);
                    }
                });
                state.customChannelPayloads.set(cacheKey, {
                    mtime: desc.mtime,
                    url: desc.url,
                    channelsById
                });
            } finally {
                _inflightPayloadFetches.delete(inflightKey);
            }
        })();
        _inflightPayloadFetches.set(inflightKey, taskPromise);
        fetchTasks.push(taskPromise);
    }

    await Promise.all(fetchTasks);

    for (const chRef of groupDef.channels || []) {
        const desc = state.customChannels ? state.customChannels.get(chRef.channel_id) : null;
        if (!desc) continue;
        const cacheKey = desc.url || desc.path || desc.channel_id;
        const cached = state.customChannelPayloads.get(cacheKey);
        if (!cached || !cached.channelsById) continue;
        const payload = cached.channelsById.get(chRef.channel_id);
        if (payload) {
            applyCustomChannelPayloadToLaps(payload);
        }
    }
}
