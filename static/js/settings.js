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
 * Logic for the settings page.
 */

function addPilot() {
    const container = document.getElementById('hero-pilots-list');
    if (!container) return;
    const div = document.createElement('div');
    div.className = 'pilot-item pilot-active';
    div.style.display = 'flex';
    div.style.gap = '0.5rem';
    div.style.marginBottom = '0.5rem';
    div.innerHTML = `
        <input type="text" name="hero_pilot" value="" class="form-control" placeholder="Driver Name">
        <button type="button" class="btn btn-icon toggle-btn active" onclick="togglePilot(this)" title="Toggle Highlight">
            <span class="icon">👁️</span>
        </button>
        <button type="button" class="btn btn-icon btn-danger" onclick="this.parentElement.remove()">&times;</button>
    `;
    container.appendChild(div);
    div.querySelector('input').focus();
}

function togglePilot(btn) {
    const item = btn.parentElement;
    if (!item) return;
    const input = item.querySelector('input');
    if (!input) return;
    
    if (btn.classList.contains('active')) {
        // Switch to disabled
        btn.classList.remove('active');
        btn.classList.add('disabled');
        input.name = 'disabled_hero';
        input.classList.add('disabled-input');
    } else {
        // Switch to active
        btn.classList.remove('disabled');
        btn.classList.add('active');
        input.name = 'hero_pilot';
        input.classList.remove('disabled-input');
    }
}
