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

// Shared Custom Video Player controls and 360-degree panorama viewer

const viewerRegistry = new WeakMap();

/**
 * Check if a video element or video URL is a 360 spherical video.
 * @param {HTMLVideoElement} video
 * @param {string} [filename]
 * @returns {boolean}
 */
export function isSphericalVideo(video, filename = '') {
    if (!video) return false;
    if (video.dataset && video.dataset.spherical === 'true') return true;
    if (video.dataset && video.dataset.spherical === 'false') return false;
    const name = filename || video.src || '';
    if (name.includes('_D.mov') || name.includes('360')) return true;
    if (video.videoWidth && video.videoHeight) {
        const aspect = video.videoWidth / video.videoHeight;
        if (Math.abs(aspect - 2.0) < 0.05) return true;
    }
    return false;
}

/**
 * Helper to compile a WebGL shader.
 */
function createShader(gl, type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        console.error('Shader compile error:', gl.getShaderInfoLog(shader));
        gl.deleteShader(shader);
        return null;
    }
    return shader;
}

/**
 * WebGL 360-degree equirectangular panorama viewer.
 */
export class PanoramaViewer {
    constructor(video) {
        this.video = video;
        this.container = video.closest('.video-container');
        this.canvas = null;
        this.gl = null;
        this.program = null;
        this.texture = null;
        this.animId = null;
        this.enabled = false;

        // Interaction state
        this.isDragging = false;
        this.hasMoved = false;
        this.dragStartX = 0;
        this.dragStartY = 0;
        this.dragStartYaw = 0;
        this.dragStartPitch = 0;
        this.pinchStartDistance = 0;
        this.pinchStartFov = 0;

        // Camera angles and FOV in radians
        this.yaw = 0; // horizontal pan
        this.pitch = 0; // vertical tilt [-85°, +85°]
        this.defaultFov = 80 * Math.PI / 180;
        this.minFov = 20 * Math.PI / 180;
        this.maxFov = 140 * Math.PI / 180;
        this.fov = this.defaultFov;

        this.compassEl = null;
        this.compassNeedle = null;
        this.resizeObserver = null;

        this._boundRender = this.render.bind(this);
        this._boundLoop = this.loop.bind(this);
        this._boundOnPointerDown = this.onPointerDown.bind(this);
        this._boundOnPointerMove = this.onPointerMove.bind(this);
        this._boundOnPointerUp = this.onPointerUp.bind(this);
        this._boundOnWheel = this.onWheel.bind(this);
        this._boundOnDblClick = this.onDblClick.bind(this);
        this._boundOnResize = this.onResize.bind(this);

        this.init();
    }

    init() {
        if (!this.container) return;

        // Create canvas element
        this.canvas = document.createElement('canvas');
        this.canvas.className = 'video-360-canvas';
        this.canvas.style.display = 'none';
        this.container.appendChild(this.canvas);

        // Orientation indicator / compass in top right
        this.compassEl = document.createElement('div');
        this.compassEl.className = 'video-360-compass';
        this.compassEl.title = 'Reset view (double click)';
        this.compassEl.innerHTML = `
            <svg class="video-360-compass-arrow" viewBox="0 0 24 24" fill="none">
                <polygon points="12,3 16,13 12,10 8,13" fill="#ef4444"></polygon>
                <polygon points="12,21 16,11 12,14 8,11" fill="#94a3b8"></polygon>
                <circle cx="12" cy="12" r="2" fill="white"></circle>
            </svg>
        `;
        this.compassNeedle = this.compassEl.querySelector('.video-360-compass-arrow');
        this.compassEl.addEventListener('click', (e) => {
            e.stopPropagation();
            this.resetView();
        });
        this.container.appendChild(this.compassEl);

        // Attempt WebGL 2 first, then fallback to WebGL 1
        const gl = this.canvas.getContext('webgl2') || this.canvas.getContext('webgl');
        if (!gl) {
            return;
        }
        this.gl = gl;

        const vsSource = `
            attribute vec2 aPosition;
            void main() {
                gl_Position = vec4(aPosition, 0.0, 1.0);
            }
        `;

        const fsSource = `
            #ifdef GL_ES
            precision highp float;
            #endif

            uniform sampler2D uSampler;
            uniform vec2 uResolution;
            uniform float uFov;
            uniform float uYaw;
            uniform float uPitch;

            #define PI 3.14159265358979323846

            void main() {
                vec2 uv = (gl_FragCoord.xy - 0.5 * uResolution) / (0.5 * uResolution.y);
                float tanHalfFov = tan(uFov * 0.5);
                vec3 ray = normalize(vec3(uv.x * tanHalfFov, uv.y * tanHalfFov, 1.0));

                float cosP = cos(uPitch);
                float sinP = sin(uPitch);
                vec3 rayP = vec3(ray.x, ray.y * cosP + ray.z * sinP, -ray.y * sinP + ray.z * cosP);

                float cosY = cos(uYaw);
                float sinY = sin(uYaw);
                vec3 dir = vec3(rayP.x * cosY + rayP.z * sinY, rayP.y, -rayP.x * sinY + rayP.z * cosY);

                float lon = atan(dir.x, dir.z);
                float lat = asin(clamp(dir.y, -1.0, 1.0));

                float u = lon / (2.0 * PI) + 0.5;
                float v = 0.5 - lat / PI;

                gl_FragColor = texture2D(uSampler, vec2(fract(u), clamp(v, 0.0, 1.0)));
            }
        `;

        const vs = createShader(gl, gl.VERTEX_SHADER, vsSource);
        const fs = createShader(gl, gl.FRAGMENT_SHADER, fsSource);
        if (!vs || !fs) return;

        this.program = gl.createProgram();
        gl.attachShader(this.program, vs);
        gl.attachShader(this.program, fs);
        gl.linkProgram(this.program);
        if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) {
            console.error('Program link error:', gl.getProgramInfoLog(this.program));
            return;
        }

        this.uResolution = gl.getUniformLocation(this.program, 'uResolution');
        this.uFov = gl.getUniformLocation(this.program, 'uFov');
        this.uYaw = gl.getUniformLocation(this.program, 'uYaw');
        this.uPitch = gl.getUniformLocation(this.program, 'uPitch');
        this.uSampler = gl.getUniformLocation(this.program, 'uSampler');

        // Quad covering [-1, 1]
        const positionBuffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
        const positions = new Float32Array([
            -1, -1,
             1, -1,
            -1,  1,
             1,  1,
        ]);
        gl.bufferData(gl.ARRAY_BUFFER, positions, gl.STATIC_DRAW);
        const aPosition = gl.getAttribLocation(this.program, 'aPosition');
        gl.enableVertexAttribArray(aPosition);
        gl.vertexAttribPointer(aPosition, 2, gl.FLOAT, false, 0, 0);

        // Texture
        this.texture = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, this.texture);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);

        gl.texImage2D(
            gl.TEXTURE_2D, 0, gl.RGBA, 1, 1, 0,
            gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([0, 0, 0, 255])
        );

        // Interaction listeners
        this.canvas.addEventListener('mousedown', this._boundOnPointerDown);
        window.addEventListener('mousemove', this._boundOnPointerMove);
        window.addEventListener('mouseup', this._boundOnPointerUp);

        this.canvas.addEventListener('touchstart', this._boundOnPointerDown, { passive: false });
        window.addEventListener('touchmove', this._boundOnPointerMove, { passive: false });
        window.addEventListener('touchend', this._boundOnPointerUp);
        window.addEventListener('touchcancel', this._boundOnPointerUp);

        this.canvas.addEventListener('wheel', this._boundOnWheel, { passive: false });
        this.canvas.addEventListener('dblclick', this._boundOnDblClick);

        // Video synchronization events
        this.video.addEventListener('play', () => {
            if (this.enabled) this.startLoop();
        });
        this.video.addEventListener('pause', () => {
            if (this.enabled) {
                this.stopLoop();
                this.updateTextureAndRender();
            }
        });
        this.video.addEventListener('seeked', () => {
            if (this.enabled) this.updateTextureAndRender();
        });

        // Resize handling
        if (typeof ResizeObserver !== 'undefined') {
            this.resizeObserver = new ResizeObserver(() => this.onResize());
            this.resizeObserver.observe(this.container);
        } else {
            window.addEventListener('resize', this._boundOnResize);
        }
    }

    startLoop() {
        if (this.animId) cancelAnimationFrame(this.animId);
        this.animId = requestAnimationFrame(this._boundLoop);
    }

    stopLoop() {
        if (this.animId) {
            cancelAnimationFrame(this.animId);
            this.animId = null;
        }
    }

    loop() {
        if (!this.enabled) return;
        this.updateTextureAndRender();
        if (!this.video.paused && !this.video.ended) {
            this.animId = requestAnimationFrame(this._boundLoop);
        } else {
            this.animId = null;
        }
    }

    updateTextureAndRender() {
        if (!this.gl || !this.video || this.video.readyState < 2 || typeof this.gl.bindTexture !== 'function') return;
        const gl = this.gl;
        gl.bindTexture(gl.TEXTURE_2D, this.texture);
        try {
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, this.video);
        } catch (e) {
            return;
        }
        this.render();
    }

    render() {
        if (!this.gl || !this.program || typeof this.gl.useProgram !== 'function') return;
        const gl = this.gl;
        gl.useProgram(this.program);

        gl.uniform2f(this.uResolution, this.canvas.width, this.canvas.height);
        gl.uniform1f(this.uFov, this.fov);
        gl.uniform1f(this.uYaw, this.yaw);
        gl.uniform1f(this.uPitch, this.pitch);

        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, this.texture);
        gl.uniform1i(this.uSampler, 0);

        gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);

        if (this.compassNeedle) {
            const deg = (-this.yaw * 180 / Math.PI);
            this.compassNeedle.style.transform = `rotate(${deg}deg)`;
        }
    }

    enable() {
        if (this.enabled) return;
        this.enabled = true;
        this.container.classList.add('is-360');
        if (this.canvas) this.canvas.style.display = 'block';
        if (this.compassEl) this.compassEl.style.display = 'flex';
        this.onResize();
        this.updateTextureAndRender();
        if (!this.video.paused) {
            this.startLoop();
        }
        this.updateBtnUI(true);
    }

    disable() {
        if (!this.enabled) return;
        this.enabled = false;
        this.stopLoop();
        this.container.classList.remove('is-360');
        if (this.canvas) this.canvas.style.display = 'none';
        if (this.compassEl) this.compassEl.style.display = 'none';
        this.updateBtnUI(false);
    }

    resetView() {
        this.yaw = 0;
        this.pitch = 0;
        this.fov = this.defaultFov;
        this.render();
    }

    onPointerDown(e) {
        if (!this.enabled) return;
        const clientX = e.touches ? e.touches[0].clientX : e.clientX;
        const clientY = e.touches ? e.touches[0].clientY : e.clientY;

        if (e.touches && e.touches.length === 2) {
            this.isDragging = false;
            this.pinchStartDistance = Math.hypot(
                e.touches[0].clientX - e.touches[1].clientX,
                e.touches[0].clientY - e.touches[1].clientY
            );
            this.pinchStartFov = this.fov;
            return;
        }

        this.isDragging = true;
        this.hasMoved = false;
        this.dragStartX = clientX;
        this.dragStartY = clientY;
        this.dragStartYaw = this.yaw;
        this.dragStartPitch = this.pitch;
        if (this.canvas) this.canvas.classList.add('dragging');
    }

    onPointerMove(e) {
        if (!this.enabled) return;

        if (e.touches && e.touches.length === 2 && this.pinchStartDistance > 0) {
            if (e.cancelable) e.preventDefault();
            const currentDist = Math.hypot(
                e.touches[0].clientX - e.touches[1].clientX,
                e.touches[0].clientY - e.touches[1].clientY
            );
            const ratio = this.pinchStartDistance / (currentDist || 1);
            this.fov = Math.max(this.minFov, Math.min(this.maxFov, this.pinchStartFov * ratio));
            this.render();
            return;
        }

        if (!this.isDragging) return;
        const clientX = e.touches ? e.touches[0].clientX : e.clientX;
        const clientY = e.touches ? e.touches[0].clientY : e.clientY;
        const dx = clientX - this.dragStartX;
        const dy = clientY - this.dragStartY;

        if (Math.hypot(dx, dy) > 5) {
            this.hasMoved = true;
        }

        if (e.cancelable && e.touches) {
            e.preventDefault();
        }

        const h = (this.canvas && this.canvas.clientHeight) || 400;
        const sensitivity = (this.fov / h) * 1.2;

        this.yaw = this.dragStartYaw - dx * sensitivity;
        const maxPitch = 85 * Math.PI / 180;
        this.pitch = Math.max(-maxPitch, Math.min(maxPitch, this.dragStartPitch + dy * sensitivity));

        this.render();
    }

    onPointerUp(e) {
        if (!this.isDragging) return;
        this.isDragging = false;
        this.pinchStartDistance = 0;
        if (this.canvas) this.canvas.classList.remove('dragging');

        if (!this.hasMoved) {
            togglePlay(this.video.id);
        }
    }

    onWheel(e) {
        if (!this.enabled) return;
        e.preventDefault();
        e.stopPropagation();
        const zoomDelta = (e.deltaY < 0 ? -1 : 1) * 0.08 * (this.fov / this.defaultFov);
        this.fov = Math.max(this.minFov, Math.min(this.maxFov, this.fov + zoomDelta));
        this.render();
    }

    onDblClick() {
        if (!this.enabled) return;
        this.resetView();
    }

    onResize() {
        if (!this.canvas || !this.container) return;
        const rect = this.container.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const w = Math.round(rect.width * dpr);
        const h = Math.round(rect.height * dpr);
        if (this.canvas.width !== w || this.canvas.height !== h) {
            this.canvas.width = w;
            this.canvas.height = h;
            if (this.gl && typeof this.gl.viewport === 'function') {
                this.gl.viewport(0, 0, w, h);
                this.render();
            }
        }
    }

    updateBtnUI(active) {
        const card = this.container.closest('.video-card');
        if (!card) return;
        const btn = card.querySelector('.btn-360');
        if (btn) {
            if (active) {
                btn.classList.add('active');
                btn.title = 'Switch to 2D Flat View';
            } else {
                btn.classList.remove('active');
                btn.title = 'Switch to 360° Spherical View';
            }
        }
    }
}

/**
 * Retrieve or create the PanoramaViewer instance for a given video element.
 * @param {HTMLVideoElement} video
 * @returns {PanoramaViewer|null}
 */
export function getOrCreatePanoramaViewer(video) {
    if (!video) return null;
    let viewer = viewerRegistry.get(video);
    if (!viewer) {
        viewer = new PanoramaViewer(video);
        viewerRegistry.set(video, viewer);
    }
    return viewer;
}

/**
 * Toggle 360 spherical view mode for a video.
 * @param {string} videoId
 */
export function toggle360View(videoId) {
    const video = document.getElementById(videoId);
    if (!video) return;
    const viewer = getOrCreatePanoramaViewer(video);
    if (!viewer || !viewer.gl) return;
    if (viewer.enabled) {
        viewer.disable();
    } else {
        viewer.enable();
    }
}

/**
 * Synchronize 360 view mode with video metadata or data-spherical attribute.
 * @param {HTMLVideoElement} video
 */
export function syncVideo360Mode(video) {
    if (!video) return;
    const shouldBe360 = isSphericalVideo(video, video.src);
    const viewer = getOrCreatePanoramaViewer(video);
    if (!viewer || !viewer.gl) return;
    if (shouldBe360) {
        viewer.enable();
    } else {
        viewer.disable();
    }
}

// Global initialization
if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('.video-container video').forEach(video => {
            video.addEventListener('timeupdate', () => updateVideoUI(video));
            video.addEventListener('play', () => updateVideoUI(video));
            video.addEventListener('pause', () => updateVideoUI(video));
            video.addEventListener('loadedmetadata', () => {
                updateVideoUI(video);
                if (isSphericalVideo(video, video.src)) {
                    syncVideo360Mode(video);
                }
            });

            if (video.readyState >= 1) {
                if (isSphericalVideo(video, video.src)) {
                    syncVideo360Mode(video);
                }
            }

            let timeout;
            const container = video.closest('.video-container');
            if (container) {
                container.addEventListener('mousemove', () => {
                    container.classList.add('controls-visible');
                    clearTimeout(timeout);
                    timeout = setTimeout(() => {
                        if (!video.paused) container.classList.remove('controls-visible');
                    }, 3000);
                });
            }
        });
    });
}

// Global video helper functions

export function stepVideo(id, step) {
    const video = document.getElementById(id);
    if (video) {
        video.pause();
        video.currentTime += step;
    }
}

export function togglePlay(id) {
    const video = document.getElementById(id);
    if (!video) return;
    if (video.paused) {
        stopAllOthers(video);
        video.play();
    } else {
        video.pause();
    }
}

export function stopAllOthers(currentVideo) {
    document.querySelectorAll('video').forEach(v => {
        if (v !== currentVideo) {
            v.pause();
        }
    });
}

export function playAtTime(id, time) {
    const video = document.getElementById(id);
    if (!video) return;
    stopAllOthers(video);
    if (video.readyState === 0) {
        video.load();
        video.oncanplay = () => {
            video.currentTime = time;
            video.play();
            video.oncanplay = null;
        };
    } else {
        video.currentTime = time;
        video.play();
    }
}

export function switchVideo(btn, cardIndex) {
    const video = document.getElementById(`video-${cardIndex}`);
    const title = document.getElementById(`title-${cardIndex}`);
    if (!video) return;

    const url = btn.dataset.url;
    const filename = btn.dataset.filename;
    const source = btn.dataset.source;
    const time = parseFloat(btn.dataset.videoTime);
    const isSpherical = btn.dataset.spherical === 'true';

    video.dataset.spherical = isSpherical ? 'true' : 'false';

    stopAllOthers(video);

    const onReady = () => {
        video.currentTime = time;
        video.play();
        syncVideo360Mode(video);
        video.oncanplay = null;
    };

    if (video.src !== window.location.origin + url && !video.src.endsWith(url)) {
        video.src = url;
        if (title) title.textContent = `${source}: ${filename}`;
        video.load();
        video.oncanplay = onReady;
    } else {
        if (video.readyState === 0) {
            video.load();
            video.oncanplay = onReady;
        } else {
            video.currentTime = time;
            video.play();
            syncVideo360Mode(video);
        }
    }
}

export function seekVideo(e, id) {
    const video = document.getElementById(id);
    if (!video) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const clientX = e.clientX || (e.touches && e.touches[0].clientX);
    const x = clientX - rect.left;
    const pos = Math.max(0, Math.min(1, x / rect.width));
    video.currentTime = pos * video.duration;
}

export function formatTime(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) {
        return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    }
    return `${m}:${s.toString().padStart(2, '0')}`;
}

export function updateVideoUI(video) {
    const index = video.id.split('-')[1];
    const progress = document.getElementById(`progress-${index}`);
    const timeDisplay = document.getElementById(`time-${index}`);
    const card = video.closest('.video-card');
    if (!card) return;
    const playPauseBtn = card.querySelector('.play-pause-btn');

    const current = formatTime(video.currentTime);
    const total = formatTime(video.duration || 0);
    if (timeDisplay) timeDisplay.textContent = `${current} / ${total}`;

    if (progress) {
        const percent = (video.currentTime / video.duration) * 100;
        progress.style.width = `${percent}%`;
    }

    if (playPauseBtn) {
        if (video.paused) {
            playPauseBtn.querySelector('.play-icon').style.display = 'block';
            playPauseBtn.querySelector('.pause-icon').style.display = 'none';
        } else {
            playPauseBtn.querySelector('.play-icon').style.display = 'none';
            playPauseBtn.querySelector('.pause-icon').style.display = 'block';
        }
    }
}

export function toggleFullscreen(id) {
    const element = document.getElementById(id);
    if (!element) return;
    if (!document.fullscreenElement) {
        element.requestFullscreen().catch(err => {
            alert(`Error attempting to enable full-screen mode: ${err.message} (${err.name})`);
        });
    } else {
        document.exitFullscreen();
    }
}

function syncSpeedUI(video) {
    const card = video.closest('.video-card');
    if (!card) return;
    const select = card.querySelector('.speed-select-mini');
    if (select) select.value = video.playbackRate.toString();
}

// Keyboard shortcuts
if (typeof document !== 'undefined') {
    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;

        let video = null;
        if (document.fullscreenElement) {
            video = document.fullscreenElement.querySelector('video');
        } else {
            const videos = document.querySelectorAll('video');
            if (videos.length === 0) return;
            for (let v of videos) {
                const rect = v.getBoundingClientRect();
                if (rect.top >= 0 && rect.top <= window.innerHeight) {
                    video = v;
                    break;
                }
            }
            if (!video) video = videos[0];
        }

        if (!video) return;

        if (e.key === '.' || e.key === '>') {
            stepVideo(video.id, 0.033);
        } else if (e.key === ',' || e.key === '<') {
            stepVideo(video.id, -0.033);
        } else if (e.key === ' ') {
            e.preventDefault();
            togglePlay(video.id);
        } else if (e.key === '[') {
            const rates = [0.1, 0.25, 0.5, 1, 1.25, 1.5, 2, 4, 8];
            let currentIdx = rates.indexOf(video.playbackRate);
            if (currentIdx === -1) {
                currentIdx = rates.reduce((prev, curr, idx) =>
                    Math.abs(curr - video.playbackRate) < Math.abs(rates[prev] - video.playbackRate) ? idx : prev, 0);
            }
            video.playbackRate = rates[Math.max(0, currentIdx - 1)];
            syncSpeedUI(video);
        } else if (e.key === ']') {
            const rates = [0.1, 0.25, 0.5, 1, 1.25, 1.5, 2, 4, 8];
            let currentIdx = rates.indexOf(video.playbackRate);
            if (currentIdx === -1) {
                currentIdx = rates.reduce((prev, curr, idx) =>
                    Math.abs(curr - video.playbackRate) < Math.abs(rates[prev] - video.playbackRate) ? idx : prev, 0);
            }
            video.playbackRate = rates[Math.min(rates.length - 1, currentIdx + 1)];
            syncSpeedUI(video);
        }
    });
}

// Attach all functions to window for inline onclick handlers and non-module consumers
if (typeof window !== 'undefined') {
    window.stepVideo = stepVideo;
    window.togglePlay = togglePlay;
    window.stopAllOthers = stopAllOthers;
    window.playAtTime = playAtTime;
    window.switchVideo = switchVideo;
    window.seekVideo = seekVideo;
    window.formatTime = formatTime;
    window.updateVideoUI = updateVideoUI;
    window.toggleFullscreen = toggleFullscreen;
    window.toggle360View = toggle360View;
    window.syncVideo360Mode = syncVideo360Mode;
    window.isSphericalVideo = isSphericalVideo;
    window.getOrCreatePanoramaViewer = getOrCreatePanoramaViewer;
    window.PanoramaViewer = PanoramaViewer;
}
