/**
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
 */

document.addEventListener('DOMContentLoaded', () => {
    const keyInput = document.getElementById('apiKeyInput');
    const targetInput = document.getElementById('targetInput');
    const resultBox = document.getElementById('resultBox');
    const copyBtn = document.getElementById('copyBtn');
    const openLink = document.getElementById('openLink');
    const copyToast = document.getElementById('copyToast');
    const presetChips = document.querySelectorAll('.preset-chip');

    let currentUrl = '';
    let toastTimeout = null;

    function formatRedirectPath(rawPath) {
        const trimmed = (rawPath || '').trim();
        if (!trimmed) {
            return '/';
        }
        if (trimmed.startsWith('/') || trimmed.startsWith('http://') || trimmed.startsWith('https://')) {
            return trimmed;
        }
        return '/' + trimmed;
    }

    function updateUrl() {
        const key = (keyInput.value || '').trim();
        const target = formatRedirectPath(targetInput.value);

        if (!key) {
            currentUrl = '';
            resultBox.innerHTML = '<span class="result-placeholder">Enter an API key above to generate the shareable URL...</span>';
            copyBtn.disabled = true;
            openLink.classList.add('disabled');
            openLink.removeAttribute('href');
            return;
        }

        const origin = window.location.origin;
        currentUrl = `${origin}/auth?key=${encodeURIComponent(key)}&next=${encodeURIComponent(target)}`;

        resultBox.textContent = currentUrl;
        copyBtn.disabled = false;
        openLink.classList.remove('disabled');
        openLink.href = currentUrl;
    }

    keyInput.addEventListener('input', updateUrl);
    targetInput.addEventListener('input', updateUrl);

    presetChips.forEach(chip => {
        chip.addEventListener('click', () => {
            const path = chip.getAttribute('data-path');
            if (path) {
                targetInput.value = path;
                updateUrl();
            }
        });
    });

    copyBtn.addEventListener('click', async () => {
        if (!currentUrl) return;

        try {
            await navigator.clipboard.writeText(currentUrl);
            if (toastTimeout) clearTimeout(toastTimeout);
            copyToast.classList.add('show');
            toastTimeout = setTimeout(() => {
                copyToast.classList.remove('show');
            }, 2500);
        } catch (err) {
            console.error('Failed to copy text: ', err);
            // Fallback for older browsers or non-secure contexts
            const tempArea = document.createElement('textarea');
            tempArea.value = currentUrl;
            document.body.appendChild(tempArea);
            tempArea.select();
            document.execCommand('copy');
            document.body.removeChild(tempArea);
            
            if (toastTimeout) clearTimeout(toastTimeout);
            copyToast.classList.add('show');
            toastTimeout = setTimeout(() => {
                copyToast.classList.remove('show');
            }, 2500);
        }
    });

    // Initialize state on page load
    updateUrl();
});
