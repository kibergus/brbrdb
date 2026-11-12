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
 * Direct Localhost WebSocket & Workspace Manifest client for Antigravity Companion Mode.
 * Enables zero-reload navigation, multi-study discovery, GeoJSON map overlays,
 * and custom telemetry channels directly from local workspaces.
 */
import { state } from './state.js';
import { handleReportTriggerAction, collapseSidePanel } from './lap_selection.js';
import { syncAgentOverlaysManifest, setActiveAgentOverlays } from './agent_overlays.js';
import { syncCustomChannelsManifest } from './custom_channels.js';
import { debouncedUpdateURL } from './url_sync.js';

function deriveHttpBaseFromWsUrl(wsUrl, fallbackPort = '38472') {
    if (typeof wsUrl === 'string' && wsUrl.trim()) {
        try {
            const parsed = new URL(wsUrl);
            const proto = parsed.protocol === 'wss:' ? 'https:' : 'http:';
            return `${proto}//${parsed.host}`;
        } catch {
            // Fall back to port-based URL
        }
    }
    return `http://127.0.0.1:${fallbackPort || '38472'}`;
}

function collectCurrentSessionQuery() {
    const meta = { date: '', track: '', session_id: '' };
    if (typeof window !== 'undefined' && window.KART_CONFIG) {
        if (window.KART_CONFIG.date) meta.date = String(window.KART_CONFIG.date);
        if (window.KART_CONFIG.track) meta.track = String(window.KART_CONFIG.track);
    }
    if (typeof document !== 'undefined' && typeof document.getElementById === 'function') {
        const prog = document.getElementById('stats-subtab-progression');
        if (prog && prog.dataset) {
            if (!meta.date && prog.dataset.date) meta.date = String(prog.dataset.date);
            if (!meta.track && prog.dataset.track) meta.track = String(prog.dataset.track);
        }
    }
    if (state.selectedSessionId && state.selectedSessionId !== 'all') {
        meta.session_id = String(state.selectedSessionId);
    }
    return meta;
}

/**
 * Fetches `/api/workspace/manifest` from the local companion sidecar and synchronizes
 * map overlays, studies dropdown, and custom channel plot groups.
 */
export async function refreshWorkspaceManifest(studyOverride = undefined) {
    if (typeof fetch !== 'function') return null;

    const base = (state.companionHttpBase || 'http://127.0.0.1:38472').replace(/\/+$/, '');
    const targetStudy = studyOverride !== undefined ? String(studyOverride || '') : String(state.activeStudy || '');
    const qs = new URLSearchParams();
    if (targetStudy || studyOverride !== undefined) {
        qs.set('study', targetStudy);
    }
    if (state.activeOverlayIds && state.activeOverlayIds.size > 0) {
        qs.set('overlays', Array.from(state.activeOverlayIds).join(','));
    }

    const sessionMeta = collectCurrentSessionQuery();
    if (sessionMeta.date) qs.set('date', sessionMeta.date);
    if (sessionMeta.track) qs.set('track', sessionMeta.track);
    if (sessionMeta.session_id) qs.set('session_id', sessionMeta.session_id);

    const url = `${base}/api/workspace/manifest${qs.toString() ? `?${qs.toString()}` : ''}`;
    try {
        const resp = await fetch(url, { cache: 'no-store' });
        if (!resp.ok) {
            return null;
        }
        const manifest = await resp.json();
        if (!manifest || typeof manifest !== 'object') {
            return null;
        }
        await syncAgentOverlaysManifest(
            Array.isArray(manifest.overlays) ? manifest.overlays : [],
            Array.isArray(manifest.studies) ? manifest.studies : [],
            manifest.study !== undefined ? manifest.study : targetStudy
        );
        syncCustomChannelsManifest(
            Array.isArray(manifest.channels) ? manifest.channels : [],
            Array.isArray(manifest.plot_groups) ? manifest.plot_groups : []
        );
        debouncedUpdateURL();
        return manifest;
    } catch {
        // Silent failure when local companion HTTP server is unreachable
        return null;
    }
}

/**
 * Updates the active workspace study both locally and on the companion sidecar,
 * then refreshes the workspace manifest.
 */
export async function switchWorkspaceStudy(studyId) {
    const normalized = String(studyId || '').trim();
    state.activeStudy = normalized;
    const base = (state.companionHttpBase || 'http://127.0.0.1:38472').replace(/\/+$/, '');
    if (typeof fetch === 'function') {
        try {
            await fetch(`${base}/api/workspace`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    study: normalized,
                    activeOverlays: state.activeOverlayIds ? Array.from(state.activeOverlayIds) : []
                })
            });
        } catch {
            // Ignore if sidecar is read-only or offline
        }
    }
    return refreshWorkspaceManifest(normalized);
}

function maybeNavigateToSessionUrl(data) {
    const rawTarget = data.sessionUrl || data.url;
    if (!rawTarget || typeof rawTarget !== 'string') return false;
    if (typeof window === 'undefined' || !window.location) return false;

    const currentOrigin = window.location.origin || 'http://localhost';
    let parsedTarget;
    try {
        parsedTarget = new URL(rawTarget, currentOrigin);
    } catch {
        return false;
    }

    const currentPathname = window.location.pathname || '';
    const currentParams = new URLSearchParams(window.location.search || '');
    const targetSessionId = parsedTarget.searchParams.get('session_id');
    const currentSessionId = currentParams.get('session_id');

    const pathDiffers = Boolean(parsedTarget.pathname && parsedTarget.pathname !== currentPathname);
    const sessionDiffers = Boolean(targetSessionId && targetSessionId !== currentSessionId);

    if (!pathDiffers && !sessionDiffers) {
        return false;
    }

    // Preserve companion connection parameters across session navigation
    ['companion', 'ws', 'ws_port', 'ws_url'].forEach((paramKey) => {
        if (currentParams.has(paramKey) && !parsedTarget.searchParams.has(paramKey)) {
            parsedTarget.searchParams.set(paramKey, currentParams.get(paramKey));
        }
    });
    if (data.study !== undefined && !parsedTarget.searchParams.has('study')) {
        if (data.study) parsedTarget.searchParams.set('study', String(data.study));
    } else if (state.activeStudy && !parsedTarget.searchParams.has('study')) {
        parsedTarget.searchParams.set('study', state.activeStudy);
    }
    if (data.overlays !== undefined && !parsedTarget.searchParams.has('overlays')) {
        const ovStr = Array.isArray(data.overlays) ? data.overlays.join(',') : String(data.overlays || '');
        if (ovStr) parsedTarget.searchParams.set('overlays', ovStr);
    }

    const finalUrl = parsedTarget.origin === currentOrigin
        ? `${parsedTarget.pathname}${parsedTarget.search}${parsedTarget.hash}`
        : parsedTarget.toString();

    if (typeof window.location.assign === 'function') {
        window.location.assign(finalUrl);
    } else {
        window.location.href = finalUrl;
    }
    return true;
}

export function handleCompanionCommand(data) {
    if (!data || typeof data !== 'object') return;

    if (data.httpBase && typeof data.httpBase === 'string') {
        state.companionHttpBase = data.httpBase.replace(/\/+$/, '');
    }

    const msgType = typeof data.type === 'string' ? data.type : '';
    if (msgType === 'COMPANION_READY' || msgType === 'workspace_sync') {
        const urlParams = (typeof window !== 'undefined' && window.location && window.location.search)
            ? new URLSearchParams(window.location.search)
            : new URLSearchParams();

        if (data.study !== undefined && data.study !== null) {
            if (msgType === 'workspace_sync' || !urlParams.has('study')) {
                state.activeStudy = String(data.study);
            }
        }

        if (Array.isArray(data.activeOverlays)) {
            if (msgType === 'workspace_sync' || !urlParams.has('overlays')) {
                state.activeOverlayIds = state.activeOverlayIds || new Set();
                state.activeOverlayIds.clear();
                data.activeOverlays.forEach((ov) => {
                    const cleaned = String(ov || '').trim();
                    if (cleaned) state.activeOverlayIds.add(cleaned);
                });
            }
        } else if (data.overlays !== undefined) {
            setActiveAgentOverlays(data.overlays);
        }

        refreshWorkspaceManifest();
        return;
    }

    // Navigate when targeting a different session page
    if (maybeNavigateToSessionUrl(data)) {
        return;
    }

    handleReportTriggerAction(data);
}

export function initCompanionControl() {
    if (typeof window === 'undefined' || !window.location) return null;

    const params = new URLSearchParams(window.location.search);
    const isCompanionMode = params.get('companion') === '1' ||
                            params.get('ws') === '1' ||
                            params.has('ws_url') ||
                            params.has('ws_port');

    if (!isCompanionMode) return null;

    // Automatically collapse the right sidebar in companion side panel mode
    if (typeof collapseSidePanel === 'function') {
        collapseSidePanel(false);
    }

    const wsPort = params.get('ws_port') || '38472';
    const wsUrl = params.get('ws_url') || `ws://127.0.0.1:${wsPort}/ws`;
    state.companionHttpBase = deriveHttpBaseFromWsUrl(params.get('ws_url'), wsPort);

    let ws = null;
    let reconnectTimeout = null;
    let isDestroyed = false;

    function connect() {
        if (isDestroyed) return;
        try {
            ws = new WebSocket(wsUrl);

            ws.onopen = () => {
                console.log('[Companion] Connected to local Antigravity event bus at ' + wsUrl);
            };

            ws.onmessage = (event) => {
                try {
                    const data = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
                    handleCompanionCommand(data);
                } catch (err) {
                    console.error('[Companion] Error parsing event payload:', err);
                }
            };

            ws.onclose = () => {
                if (!isDestroyed) {
                    reconnectTimeout = setTimeout(connect, 3000);
                }
            };

            ws.onerror = () => {
                // Silent catch: if not running inside Antigravity, avoid console spam
                try {
                    if (ws && ws.readyState === WebSocket.OPEN) {
                        ws.close();
                    }
                } catch {
                    // Ignore close error
                }
            };
        } catch (e) {
            console.warn('[Companion] WebSocket init failed:', e);
            if (!isDestroyed) {
                reconnectTimeout = setTimeout(connect, 3000);
            }
        }
    }

    connect();

    return {
        getSocket: () => ws,
        close: () => {
            isDestroyed = true;
            if (reconnectTimeout) {
                clearTimeout(reconnectTimeout);
                reconnectTimeout = null;
            }
            if (ws) {
                try {
                    ws.close();
                } catch {
                    // Ignore close error
                }
            }
        }
    };
}
