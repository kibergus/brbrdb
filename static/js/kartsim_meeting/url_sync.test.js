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
import { updateURL } from './url_sync.js';
import { state } from './state.js';

describe('url_sync.js updateURL', () => {
    let mockReplaceState;

    beforeEach(() => {
        state.mapInitialized = true;
        state.groupASelection = new Set();
        state.groupBSelection = new Set();
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

    it('sets lapsA=none and lapsB=none when selections are empty sets', () => {
        updateURL();

        expect(mockReplaceState).toHaveBeenCalled();
        const urlCall = mockReplaceState.mock.calls[0][2];
        expect(urlCall).toContain('lapsA=none');
        expect(urlCall).toContain('lapsB=none');
    });

    it('sets lapsA and lapsB comma-separated lists when laps are selected', () => {
        state.groupASelection.add('lap-1');
        state.groupASelection.add('lap-2');
        state.groupBSelection.add('lap-3');

        updateURL();

        expect(mockReplaceState).toHaveBeenCalled();
        const urlCall = mockReplaceState.mock.calls[0][2];
        expect(urlCall).toContain('lapsA=lap-1%2Clap-2');
        expect(urlCall).toContain('lapsB=lap-3');
    });
});
