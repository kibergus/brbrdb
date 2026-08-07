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

// Shared Custom Video Player controls and functions

document.addEventListener('DOMContentLoaded', () => {
    // Initialize custom video players
    document.querySelectorAll('.video-container video').forEach(video => {
        video.addEventListener('timeupdate', () => updateVideoUI(video));
        video.addEventListener('play', () => updateVideoUI(video));
        video.addEventListener('pause', () => updateVideoUI(video));
        video.addEventListener('loadedmetadata', () => updateVideoUI(video));
        
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

// Global video helper functions

function stepVideo(id, step) {
    const video = document.getElementById(id);
    if (video) {
        video.pause();
        video.currentTime += step;
    }
}

function togglePlay(id) {
    const video = document.getElementById(id);
    if (!video) return;
    if (video.paused) {
        stopAllOthers(video);
        video.play();
    } else {
        video.pause();
    }
}

function stopAllOthers(currentVideo) {
    document.querySelectorAll('video').forEach(v => {
        if (v !== currentVideo) {
            v.pause();
        }
    });
}

function playAtTime(id, time) {
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

function switchVideo(btn, cardIndex) {
    const video = document.getElementById(`video-${cardIndex}`);
    const title = document.getElementById(`title-${cardIndex}`);
    if (!video) return;

    const url = btn.dataset.url;
    const filename = btn.dataset.filename;
    const source = btn.dataset.source;
    const time = parseFloat(btn.dataset.videoTime);

    stopAllOthers(video);

    if (video.src !== window.location.origin + url && !video.src.endsWith(url)) {
        video.src = url;
        if (title) title.textContent = `${source}: ${filename}`;
        video.load();
        video.oncanplay = () => {
            video.currentTime = time;
            video.play();
            video.oncanplay = null;
        };
    } else {
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
}

function seekVideo(e, id) {
    const video = document.getElementById(id);
    if (!video) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const clientX = e.clientX || (e.touches && e.touches[0].clientX);
    const x = clientX - rect.left;
    const pos = Math.max(0, Math.min(1, x / rect.width));
    video.currentTime = pos * video.duration;
}

function formatTime(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    if (h > 0) {
        return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
    }
    return `${m}:${s.toString().padStart(2, '0')}`;
}

function updateVideoUI(video) {
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

function toggleFullscreen(id) {
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

// Keyboard shortcuts
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

function syncSpeedUI(video) {
    const card = video.closest('.video-card');
    if (!card) return;
    const select = card.querySelector('.speed-select-mini');
    if (select) select.value = video.playbackRate.toString();
}
