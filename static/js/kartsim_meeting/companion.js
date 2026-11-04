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
 * Direct Localhost WebSocket listener for Antigravity Companion Mode.
 * Enables zero-reload navigation (distance seek, plot selection, lap switching, map centering)
 * directly from Antigravity IDE or local test harnesses.
 */
import { handleReportTriggerAction } from './lap_selection.js';

export function handleCompanionCommand(data) {
    if (!data || typeof data !== 'object') return;

    // Direct delegation to report action handler, which natively supports:
    // - dist: seek distance along track
    // - xlim / range / plotRange: visible range for bottom plots [min, max]
    // - mapRange / mapFocus / centerMap / focusMap: map segment bounds [min, max]
    // - focusRange / zoomRange: sets both plot range & map bounds
    // - lat / lng / zoom: direct GPS coordinate centering
    // - turn: turn index focus
    // - laps / lapsA / lapsB: active comparison laps
    // - togglePlot: toggle visibility of an individual channel
    // - plots / plot: set open bottom channels ('speed', 'pedals', 'steering', etc.)
    // - tab: switch main view ('map' | 'stats')
    // - rtab: switch right panel tab ('report' | 'cornering')
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

    const wsPort = params.get('ws_port') || '38472';
    const wsUrl = params.get('ws_url') || `ws://127.0.0.1:${wsPort}/ws`;
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
                } catch (_) {}
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
                } catch (_) {}
            }
        }
    };
}

