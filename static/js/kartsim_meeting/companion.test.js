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

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { initCompanionControl, handleCompanionCommand } from './companion.js';
import * as lapSelection from './lap_selection.js';

vi.mock('./lap_selection.js', () => ({
    handleReportTriggerAction: vi.fn(),
    seekToDistance: vi.fn(),
    setPlotXLim: vi.fn(),
    setGroupALaps: vi.fn(),
    centerMapOnCoords: vi.fn(),
    focusMapOnRange: vi.fn(),
    collapseSidePanel: vi.fn(),
    expandSidePanel: vi.fn()
}));

describe('companion.js', () => {
    let mockWebSocket;
    let createdSockets;

    beforeEach(() => {
        vi.clearAllMocks();
        vi.useFakeTimers();
        createdSockets = [];

        mockWebSocket = vi.fn().mockImplementation(function (url) {
            this.url = url;
            this.readyState = 1; // WebSocket.OPEN
            this.send = vi.fn();
            this.close = vi.fn().mockImplementation(() => {
                this.readyState = 3; // WebSocket.CLOSED
                if (this.onclose) this.onclose();
            });
            createdSockets.push(this);
        });
        mockWebSocket.OPEN = 1;
        mockWebSocket.CLOSED = 3;

        vi.stubGlobal('WebSocket', mockWebSocket);
    });

    afterEach(() => {
        vi.useRealTimers();
        vi.unstubAllGlobals();
    });

    it('does nothing when companion mode is not in URL parameters', () => {
        vi.stubGlobal('window', {
            location: { search: '' }
        });

        const controller = initCompanionControl();
        expect(controller).toBeNull();
        expect(mockWebSocket).not.toHaveBeenCalled();
    });

    it('connects to default ws://127.0.0.1:38472/ws when ?companion=1 is present', () => {
        vi.stubGlobal('window', {
            location: { search: '?companion=1' }
        });

        const controller = initCompanionControl();
        expect(controller).not.toBeNull();
        expect(lapSelection.collapseSidePanel).toHaveBeenCalledWith(false);
        expect(mockWebSocket).toHaveBeenCalledWith('ws://127.0.0.1:38472/ws');

        const socket = controller.getSocket();
        expect(socket).toBeDefined();

        // Simulate open
        socket.onopen();

        // Simulate onmessage
        const payload = { type: 'TELEMETRY_JUMP', dist: 316.5, plots: ['speed', 'pedals'] };
        socket.onmessage({ data: JSON.stringify(payload) });

        expect(lapSelection.handleReportTriggerAction).toHaveBeenCalledWith(payload);

        controller.close();
        expect(socket.close).toHaveBeenCalled();
    });

    it('connects when ?ws=1 is present', () => {
        vi.stubGlobal('window', {
            location: { search: '?ws=1' }
        });

        const controller = initCompanionControl();
        expect(controller).not.toBeNull();
        expect(lapSelection.collapseSidePanel).toHaveBeenCalledWith(false);
        expect(mockWebSocket).toHaveBeenCalledWith('ws://127.0.0.1:38472/ws');
        controller.close();
    });

    it('uses custom port when ?ws_port is specified', () => {
        vi.stubGlobal('window', {
            location: { search: '?companion=1&ws_port=49152' }
        });

        const controller = initCompanionControl();
        expect(mockWebSocket).toHaveBeenCalledWith('ws://127.0.0.1:49152/ws');
        controller.close();
    });

    it('uses custom ws_url when specified', () => {
        vi.stubGlobal('window', {
            location: { search: '?ws_url=ws://localhost:9000/custom' }
        });

        const controller = initCompanionControl();
        expect(mockWebSocket).toHaveBeenCalledWith('ws://localhost:9000/custom');
        controller.close();
    });

    it('handles socket disconnect and reconnects after 3s', () => {
        vi.stubGlobal('window', {
            location: { search: '?companion=1' }
        });

        const controller = initCompanionControl();
        expect(createdSockets.length).toBe(1);

        // Simulate socket onclose
        createdSockets[0].onclose();

        // Advance 3 seconds
        vi.advanceTimersByTime(3000);
        expect(createdSockets.length).toBe(2);

        controller.close();
    });

    it('handles error gracefully without crashing', () => {
        vi.stubGlobal('window', {
            location: { search: '?companion=1' }
        });

        const controller = initCompanionControl();
        const socket = controller.getSocket();

        expect(() => {
            socket.onerror(new Error('Connection refused'));
        }).not.toThrow();

        controller.close();
    });

    it('routes various companion commands to handleReportTriggerAction', () => {
        // TELEMETRY_JUMP
        handleCompanionCommand({ type: 'TELEMETRY_JUMP', dist: 316.5, xlim: [290, 380] });
        expect(lapSelection.handleReportTriggerAction).toHaveBeenCalledWith({
            type: 'TELEMETRY_JUMP',
            dist: 316.5,
            xlim: [290, 380]
        });

        // TELEMETRY_SELECT_LAPS
        handleCompanionCommand({ type: 'TELEMETRY_SELECT_LAPS', lapsA: ['lap-1', 'lap-2'] });
        expect(lapSelection.handleReportTriggerAction).toHaveBeenCalledWith({
            type: 'TELEMETRY_SELECT_LAPS',
            lapsA: ['lap-1', 'lap-2']
        });

        // TELEMETRY_CENTER_MAP
        handleCompanionCommand({ type: 'TELEMETRY_CENTER_MAP', lat: 51.189, lng: 0.941, zoom: 17 });
        expect(lapSelection.handleReportTriggerAction).toHaveBeenCalledWith({
            type: 'TELEMETRY_CENTER_MAP',
            lat: 51.189,
            lng: 0.941,
            zoom: 17
        });

        // Lowercase telemetry_jump (existing report format)
        handleCompanionCommand({ type: 'telemetry_jump', dist: 400, plots: 'speed,steering' });
        expect(lapSelection.handleReportTriggerAction).toHaveBeenCalledWith({
            type: 'telemetry_jump',
            dist: 400,
            plots: 'speed,steering'
        });

        // Null / undefined safety
        expect(() => handleCompanionCommand(null)).not.toThrow();
        expect(() => handleCompanionCommand(undefined)).not.toThrow();
        expect(() => handleCompanionCommand('invalid')).not.toThrow();
    });

    it('fetches workspace manifest on COMPANION_READY and workspace_sync without calling handleReportTriggerAction', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true,
            json: async () => ({
                study: '2026-04-12_buckmore_eeg',
                studies: [{ id: '2026-04-12_buckmore_eeg', label: 'EEG Study' }],
                overlays: [],
                channels: [],
                plot_groups: []
            })
        });
        vi.stubGlobal('fetch', fetchMock);
        vi.stubGlobal('window', {
            location: { search: '?companion=1' }
        });

        handleCompanionCommand({
            type: 'COMPANION_READY',
            httpBase: 'http://127.0.0.1:45678',
            study: '2026-04-12_buckmore_eeg',
            activeOverlays: ['apex_markers']
        });

        await Promise.resolve();
        await Promise.resolve();

        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(fetchMock.mock.calls[0][0]).toContain('http://127.0.0.1:45678/api/workspace/manifest');
        expect(fetchMock.mock.calls[0][0]).toContain('study=2026-04-12_buckmore_eeg');
        expect(fetchMock.mock.calls[0][0]).toContain('overlays=apex_markers');
        expect(lapSelection.handleReportTriggerAction).not.toHaveBeenCalled();

        handleCompanionCommand({
            type: 'workspace_sync',
            study: '2026-04-12_buckmore_eeg',
            activeOverlays: []
        });
        await Promise.resolve();
        expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it('navigates via window.location.assign when sessionUrl targets a different session, and avoids reload on same session', () => {
        const assignMock = vi.fn();
        vi.stubGlobal('window', {
            location: {
                origin: 'http://127.0.0.1:5000',
                pathname: '/leagues/kartsim/senior/2026-04-10/lydd',
                search: '?companion=1&ws_port=38472',
                assign: assignMock
            }
        });

        // Different session pathname -> navigates preserving companion=1 & ws_port
        handleCompanionCommand({
            sessionUrl: '/leagues/kartsim/senior/2026-04-12/buckmore_park?tab=map',
            dist: 200
        });
        expect(assignMock).toHaveBeenCalledTimes(1);
        expect(assignMock.mock.calls[0][0]).toContain('/leagues/kartsim/senior/2026-04-12/buckmore_park');
        expect(assignMock.mock.calls[0][0]).toContain('companion=1');
        expect(assignMock.mock.calls[0][0]).toContain('ws_port=38472');
        expect(lapSelection.handleReportTriggerAction).not.toHaveBeenCalled();

        // Same session pathname -> zero-reload delegation to handleReportTriggerAction
        assignMock.mockClear();
        handleCompanionCommand({
            sessionUrl: '/leagues/kartsim/senior/2026-04-10/lydd',
            dist: 315,
            turnNumber: 3
        });
        expect(assignMock).not.toHaveBeenCalled();
        expect(lapSelection.handleReportTriggerAction).toHaveBeenCalledTimes(1);
    });
});

