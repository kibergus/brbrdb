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
 * Logic for the meeting socials page.
 */

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', function () {
        const imgs = document.querySelectorAll('.social-img');

        imgs.forEach((img, index) => {
            const plotIndex = index + 1;
            const loading = document.getElementById(`social-loading-${plotIndex}`);
            if (!loading) return;

            if (img.complete) {
                loading.style.display = 'none';
                img.style.display = 'block';
            } else {
                img.onload = function () {
                    loading.style.display = 'none';
                    this.style.display = 'block';
                };
                img.onerror = function () {
                    loading.innerHTML = '<div style="color: var(--danger)">Failed to generate plot.</div>';
                };
            }
        });
    });
}

function reloadSocialPlot(index) {
    const img = document.getElementById(`social-img-${index}`);
    const loading = document.getElementById(`social-loading-${index}`);
    const gapInput = document.querySelector(`.gap-override-input[data-index="${index}"]`);
    const violinInput = document.querySelector(`.violin-override-input[data-index="${index}"]`);

    if (!img) return;

    img.style.display = 'none';
    if (loading) loading.style.display = 'flex';

    // Keep the original data-src or just use current src but strip existing maxy/maxy_violin/t
    let baseUrl = img.src.split('?')[0];
    let params = new URLSearchParams();

    if (gapInput && gapInput.value) {
        params.set('maxy', gapInput.value);
    }
    if (violinInput && violinInput.value) {
        params.set('maxy_violin', violinInput.value);
    }

    // Preserve other original parameters
    const originalUrl = new URL(img.src, window.location.origin);
    originalUrl.searchParams.forEach((value, key) => {
        if (key !== 'maxy' && key !== 'maxy_violin' && key !== 't') {
            params.set(key, value);
        }
    });

    params.set('t', new Date().getTime());
    img.src = baseUrl + '?' + params.toString();
}

async function downloadAllPlots() {
    const imgs = document.querySelectorAll('.social-img');
    const btn = document.getElementById('download-all-btn');
    if (!btn) return;
    const originalText = btn.textContent;

    btn.disabled = true;
    btn.style.opacity = '0.7';
    btn.textContent = 'Downloading...';

    for (let i = 0; i < imgs.length; i++) {
        const img = imgs[i];
        const downloadName = img.getAttribute('data-download-name');
        const filename = (downloadName || 'plot').toLowerCase().replace(/[^a-z0-9]/g, '-') + '.png';

        try {
            const response = await fetch(img.src);
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);
            // Tiny delay to avoid browser choking on many simultaneous downloads
            await new Promise(resolve => setTimeout(resolve, 400));
        } catch (e) {
            console.error('Failed to download', img.src, e);
        }
    }

    btn.disabled = false;
    btn.style.opacity = '1';
    btn.textContent = originalText;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { reloadSocialPlot, downloadAllPlots };
}
