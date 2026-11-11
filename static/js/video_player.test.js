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

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
    isSphericalVideo,
    formatTime,
    PanoramaViewer,
    toggle360View,
    syncVideo360Mode,
    getOrCreatePanoramaViewer
} from './video_player.js';

function createMockElement(tagName = 'div') {
    const children = [];
    const listeners = {};
    const attributes = {};
    const style = {};
    let className = '';
    const classList = {
        add: vi.fn(c => {
            if (!className.includes(c)) className = (className + ' ' + c).trim();
        }),
        remove: vi.fn(c => {
            className = className.replace(new RegExp(`\\b${c}\\b`, 'g'), '').trim();
        }),
        toggle: vi.fn(c => {
            if (className.includes(c)) classList.remove(c);
            else classList.add(c);
        }),
        contains: vi.fn(c => className.includes(c))
    };

    let parent = null;

    const el = {
        tagName: tagName.toUpperCase(),
        children,
        style,
        innerHTML: '',
        textContent: '',
        dataset: {},
        id: '',
        src: '',
        videoWidth: 0,
        videoHeight: 0,
        readyState: 4,
        paused: true,
        ended: false,
        duration: 100,
        currentTime: 0,
        playbackRate: 1.0,
        get className() { return className; },
        set className(v) { className = String(v); },
        classList,
        setAttribute(k, v) { attributes[k] = String(v); },
        getAttribute(k) { return attributes[k]; },
        get parentElement() { return parent; },
        set parentElement(p) { parent = p; },
        appendChild(child) {
            child.parentElement = this;
            children.push(child);
            return child;
        },
        closest(selector) {
            let cur = this;
            while (cur) {
                if (selector.startsWith('.') && cur.classList.contains(selector.slice(1))) {
                    return cur;
                }
                cur = cur.parentElement;
            }
            return null;
        },
        querySelectorAll(selector) {
            const results = [];
            const walk = (node) => {
                if (!node || !node.children) return;
                for (const c of node.children) {
                    if (selector.startsWith('.') && c.classList.contains(selector.slice(1))) {
                        results.push(c);
                    }
                    walk(c);
                }
            };
            walk(this);
            return results;
        },
        querySelector(selector) {
            const res = this.querySelectorAll(selector);
            return res.length > 0 ? res[0] : null;
        },
        addEventListener(evt, fn) {
            listeners[evt] = listeners[evt] || [];
            listeners[evt].push(fn);
        },
        removeEventListener(evt, fn) {
            if (listeners[evt]) {
                listeners[evt] = listeners[evt].filter(f => f !== fn);
            }
        },
        getBoundingClientRect() {
            return { left: 0, top: 0, width: 800, height: 450 };
        },
        getContext(type) {
            return null; // By default no WebGL in mock
        },
        play: vi.fn(() => { el.paused = false; }),
        pause: vi.fn(() => { el.paused = true; }),
        load: vi.fn()
    };

    return el;
}

describe('video_player.js', () => {
    describe('formatTime', () => {
        it('formats mm:ss correctly', () => {
            expect(formatTime(0)).toBe('0:00');
            expect(formatTime(65)).toBe('1:05');
            expect(formatTime(599)).toBe('9:59');
        });

        it('formats hh:mm:ss correctly', () => {
            expect(formatTime(3600)).toBe('1:00:00');
            expect(formatTime(3665)).toBe('1:01:05');
        });
    });

    describe('isSphericalVideo', () => {
        it('detects 360 video from dataset.spherical === "true"', () => {
            const video = createMockElement('video');
            video.dataset.spherical = 'true';
            expect(isSphericalVideo(video)).toBe(true);
        });

        it('respects dataset.spherical === "false"', () => {
            const video = createMockElement('video');
            video.dataset.spherical = 'false';
            expect(isSphericalVideo(video, 'CAM_20261107114937_0001_D.mov')).toBe(false);
        });

        it('detects 360 video from filename convention', () => {
            const video = createMockElement('video');
            expect(isSphericalVideo(video, 'CAM_20261107114937_0001_D.mov')).toBe(true);
            expect(isSphericalVideo(video, 'my_race_360_track.mp4')).toBe(true);
            expect(isSphericalVideo(video, 'normal_gopro_video.mp4')).toBe(false);
        });

        it('detects 360 video from 2:1 aspect ratio', () => {
            const video = createMockElement('video');
            video.videoWidth = 2160;
            video.videoHeight = 1080;
            expect(isSphericalVideo(video)).toBe(true);

            // Normal 16:9 video
            video.videoWidth = 1920;
            video.videoHeight = 1080;
            expect(isSphericalVideo(video, 'race.mp4')).toBe(false);
        });

        it('returns false for null or undefined input', () => {
            expect(isSphericalVideo(null)).toBe(false);
        });
    });

    describe('PanoramaViewer', () => {
        let card, container, video;

        beforeEach(() => {
            card = createMockElement('div');
            card.className = 'video-card';

            container = createMockElement('div');
            container.className = 'video-container';
            card.appendChild(container);

            video = createMockElement('video');
            video.id = 'video-test';
            video.src = '/test.mp4';
            container.appendChild(video);

            const btn = createMockElement('button');
            btn.className = 'v-btn btn-360';
            card.appendChild(btn);

            vi.stubGlobal('document', {
                createElement: (tag) => createMockElement(tag),
                getElementById: (id) => (id === 'video-test' ? video : null),
                querySelectorAll: () => []
            });
            vi.stubGlobal('window', {
                location: { origin: 'http://localhost' },
                devicePixelRatio: 1,
                addEventListener: vi.fn(),
                removeEventListener: vi.fn()
            });
        });

        afterEach(() => {
            vi.unstubAllGlobals();
        });

        it('initializes with default camera parameters and DOM elements', () => {
            const viewer = new PanoramaViewer(video);
            expect(viewer.yaw).toBe(0);
            expect(viewer.pitch).toBe(0);
            expect(viewer.fov).toBeCloseTo(80 * Math.PI / 180);
            expect(viewer.canvas).not.toBeNull();
            expect(viewer.compassEl).not.toBeNull();
        });

        it('enables and disables 360 mode on container', () => {
            const viewer = new PanoramaViewer(video);
            viewer.enable();
            expect(viewer.enabled).toBe(true);
            expect(container.classList.contains('is-360')).toBe(true);

            viewer.disable();
            expect(viewer.enabled).toBe(false);
            expect(container.classList.contains('is-360')).toBe(false);
        });

        it('clamps pitch angle to [-85°, +85°]', () => {
            const viewer = new PanoramaViewer(video);
            viewer.enabled = true;
            viewer.canvas.clientHeight = 400;

            // Simulate pointer down and large drag upward
            viewer.onPointerDown({ clientX: 100, clientY: 100 });
            viewer.onPointerMove({ clientX: 100, clientY: -1000 });
            const maxPitch = 85 * Math.PI / 180;
            expect(viewer.pitch).toBeCloseTo(-maxPitch, 2);

            // Drag way down
            viewer.onPointerMove({ clientX: 100, clientY: 1000 });
            expect(viewer.pitch).toBeCloseTo(maxPitch, 2);
        });

        it('clamps wheel zoom FOV within [20°, 140°]', () => {
            const viewer = new PanoramaViewer(video);
            viewer.enabled = true;

            // Zoom in excessively
            for (let i = 0; i < 50; i++) {
                viewer.onWheel({
                    deltaY: -100,
                    preventDefault: vi.fn(),
                    stopPropagation: vi.fn()
                });
            }
            expect(viewer.fov).toBeGreaterThanOrEqual(viewer.minFov - 0.001);

            // Zoom out excessively
            for (let i = 0; i < 100; i++) {
                viewer.onWheel({
                    deltaY: 100,
                    preventDefault: vi.fn(),
                    stopPropagation: vi.fn()
                });
            }
            expect(viewer.fov).toBeLessThanOrEqual(viewer.maxFov + 0.001);
            expect(viewer.maxFov).toBeCloseTo(140 * Math.PI / 180);
        });

        it('resets view to initial forward orientation on resetView or dblclick', () => {
            const viewer = new PanoramaViewer(video);
            viewer.yaw = 1.5;
            viewer.pitch = 0.8;
            viewer.fov = 1.0;

            viewer.resetView();
            expect(viewer.yaw).toBe(0);
            expect(viewer.pitch).toBe(0);
            expect(viewer.fov).toBeCloseTo(80 * Math.PI / 180);
        });

        it('syncVideo360Mode activates 360 mode for spherical videos', () => {
            video.dataset.spherical = 'true';
            const viewer = getOrCreatePanoramaViewer(video);
            viewer.gl = { bindTexture: vi.fn(), useProgram: vi.fn(), viewport: vi.fn() };

            syncVideo360Mode(video);
            expect(viewer.enabled).toBe(true);

            video.dataset.spherical = 'false';
            syncVideo360Mode(video);
            expect(viewer.enabled).toBe(false);
        });

        it('toggle360View toggles 360 mode', () => {
            const viewer = getOrCreatePanoramaViewer(video);
            viewer.gl = { bindTexture: vi.fn(), useProgram: vi.fn(), viewport: vi.fn() };

            toggle360View('video-test');
            expect(viewer.enabled).toBe(true);

            toggle360View('video-test');
            expect(viewer.enabled).toBe(false);
        });
    });
});
