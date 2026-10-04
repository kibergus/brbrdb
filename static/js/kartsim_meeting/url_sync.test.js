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

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { updateURL, formatLapForUrl, compareShortLapKeys } from './url_sync.js';
import { state } from './state.js';

describe('url_sync.js updateURL', () => {
    let mockReplaceState;

    beforeEach(() => {
        state.mapInitialized = true;
        state.activeTab = 'map';
        state.groups = ['A', 'B'];
        state.groupSelectionsMap = { A: new Set(), B: new Set() };
        state.groupSelectionsStats = { A: new Set(), B: new Set() };
        state.allSessionsData = [];
        state.lapDataLookup = {};
        mockReplaceState = vi.fn();

        vi.stubGlobal('window', {
            location: {
                search: '?session_id=s1',
                pathname: '/telemetry/test'
            },
            history: {
                replaceState: mockReplaceState
            }
        });

        vi.stubGlobal('document', {
            querySelector: vi.fn().mockReturnValue(null)
        });
    });

    it('sets lapsA=none, lapsB=none, slapsA=none, slapsB=none when selections are empty sets', () => {
        updateURL();

        expect(mockReplaceState).toHaveBeenCalled();
        const urlCall = mockReplaceState.mock.calls[0][2];
        expect(urlCall).toContain('lapsA=none');
        expect(urlCall).toContain('lapsB=none');
        expect(urlCall).toContain('slapsA=none');
        expect(urlCall).toContain('slapsB=none');
    });

    it('sets laps<G> and slaps<G> independently in URL', () => {
        state.groupSelectionsMap.A.add('lap-1');
        state.groupSelectionsMap.A.add('lap-2');
        state.groupSelectionsMap.B.add('lap-3');
        state.groupSelectionsStats.A.add('lap-stats-1');

        updateURL();

        expect(mockReplaceState).toHaveBeenCalled();
        const urlCall = mockReplaceState.mock.calls[0][2];
        expect(urlCall).toContain('lapsA=lap-1%2Clap-2');
        expect(urlCall).toContain('lapsB=lap-3');
        expect(urlCall).toContain('slapsA=lap-stats-1');
        expect(urlCall).toContain('slapsB=none');
    });

    it('encodes laps using session index and lap number when sessions data is available', () => {
        state.allSessionsData = [
            { session_id: 'club100/cadet/2026-09-12/Lydd/09_48_cadet_group_b' },
            { session_id: 'club100_south/cadet_lw/2026-09-12/Lydd/11_02_practice' }
        ];
        const lapA1 = 'club100/cadet/2026-09-12/Lydd/09_48_cadet_group_b-4';
        const lapA2 = 'club100_south/cadet_lw/2026-09-12/Lydd/11_02_practice-2';
        const lapB1 = 'club100/cadet/2026-09-12/Lydd/09_48_cadet_group_b-1';

        state.lapDataLookup = {
            [lapA1]: { session_id: 'club100/cadet/2026-09-12/Lydd/09_48_cadet_group_b', lap_num: 4 },
            [lapA2]: { session_id: 'club100_south/cadet_lw/2026-09-12/Lydd/11_02_practice', lap_num: 2 },
            [lapB1]: { session_id: 'club100/cadet/2026-09-12/Lydd/09_48_cadet_group_b', lap_num: 1 }
        };

        expect(formatLapForUrl(lapA1)).toBe('0_4');
        expect(formatLapForUrl(lapA2)).toBe('1_2');
        expect(formatLapForUrl(lapB1)).toBe('0_1');

        state.groupASelection.add(lapA1);
        state.groupASelection.add(lapA2);
        state.groupBSelection.add(lapB1);

        updateURL();

        expect(mockReplaceState).toHaveBeenCalled();
        const urlCall = mockReplaceState.mock.calls[0][2];
        expect(urlCall).toContain('lapsA=0_4%2C1_2');
        expect(urlCall).toContain('lapsB=0_1');
    });

    it('sorts short lap keys numerically', () => {
        const keys = ['1_10', '0_4', '0_1', '1_2'];
        keys.sort(compareShortLapKeys);
        expect(keys).toEqual(['0_1', '0_4', '1_2', '1_10']);
    });

    it('sets tcol parameter when trajectoryColorMode is not pedals', () => {
        state.trajectoryColorMode = 'time';
        updateURL();

        expect(mockReplaceState).toHaveBeenCalled();
        let urlCall = mockReplaceState.mock.calls[mockReplaceState.mock.calls.length - 1][2];
        expect(urlCall).toContain('tcol=time');

        state.trajectoryColorMode = 'delta_t';
        updateURL();
        urlCall = mockReplaceState.mock.calls[mockReplaceState.mock.calls.length - 1][2];
        expect(urlCall).toContain('tcol=delta_t');

        state.trajectoryColorMode = 'pedals';
        updateURL();
        urlCall = mockReplaceState.mock.calls[mockReplaceState.mock.calls.length - 1][2];
        expect(urlCall).not.toContain('tcol=');
    });

    it('serializes dynamic groups C and D and handles disabled groups in URL', () => {
        state.groups = ['A', 'B', 'C'];
        state.groupSelections['C'] = new Set(['lap-99']);
        state.groupEnabled['B'] = false;

        updateURL();

        expect(mockReplaceState).toHaveBeenCalled();
        const urlCall = mockReplaceState.mock.calls[mockReplaceState.mock.calls.length - 1][2];
        expect(urlCall).toContain('groups=A%2CB%2CC');
        expect(urlCall).toContain('lapsC=lap-99');
        expect(urlCall).toContain('dis=B');

        // Reset state for subsequent tests
        state.groups = ['A', 'B'];
        delete state.groupSelectionsMap['C'];
        delete state.groupSelectionsStats['C'];
        state.groupEnabled['B'] = true;
    });
});
