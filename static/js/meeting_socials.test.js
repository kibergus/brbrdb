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
import { reloadSocialPlot } from './meeting_socials.js';

describe('meeting_socials.js reloadSocialPlot', () => {
    let mockImg;
    let mockLoading;
    let mockGapInput;
    let mockViolinInput;

    beforeEach(() => {
        mockImg = {
            id: 'social-img-1',
            src: 'https://example.com/plots/combined_plot.png?session=s1&aspect=4%3A5',
            style: { display: 'block' }
        };
        mockLoading = {
            id: 'social-loading-1',
            style: { display: 'none' }
        };
        mockGapInput = {
            value: ''
        };
        mockViolinInput = {
            value: ''
        };

        vi.stubGlobal('window', {
            location: {
                origin: 'https://example.com'
            }
        });

        vi.stubGlobal('document', {
            addEventListener: vi.fn(),
            querySelectorAll: vi.fn().mockReturnValue([]),
            getElementById: vi.fn((id) => {
                if (id === 'social-img-1') return mockImg;
                if (id === 'social-loading-1') return mockLoading;
                return null;
            }),
            querySelector: vi.fn((selector) => {
                if (selector.includes('gap-override-input')) return mockGapInput;
                if (selector.includes('violin-override-input')) return mockViolinInput;
                return null;
            })
        });
    });

    it('reloads plot with gap and violin override parameters when values are present', () => {
        mockGapInput.value = '10.5';
        mockViolinInput.value = '4.0';

        reloadSocialPlot(1);

        expect(mockImg.style.display).toBe('none');
        expect(mockLoading.style.display).toBe('flex');
        expect(mockImg.src).toContain('maxy=10.5');
        expect(mockImg.src).toContain('maxy_violin=4.0');
        expect(mockImg.src).toContain('aspect=4%3A5');
    });
});
