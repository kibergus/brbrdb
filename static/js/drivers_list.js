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
 * Logic for the drivers list page.
 */

document.addEventListener('DOMContentLoaded', function () {
    const searchInput = document.getElementById('driverSearch');
    if (!searchInput) return;

    // Search functionality
    searchInput.addEventListener('input', function (e) {
        const query = e.target.value.toLowerCase();
        const cards = document.querySelectorAll('.driver-card');
        const sections = document.querySelectorAll('.letter-section');

        cards.forEach(card => {
            const name = card.dataset.name.toLowerCase();
            if (name.includes(query)) {
                card.style.display = 'flex';
            } else {
                card.style.display = 'none';
            }
        });

        // Hide entire sections if they have no visible drivers
        sections.forEach(section => {
            const visibleCards = Array.from(section.querySelectorAll('.driver-card'))
                .filter(card => card.style.display !== 'none');

            if (visibleCards.length === 0) {
                section.style.display = 'none';
            } else {
                section.style.display = 'block';
            }
        });
    });

    // Sticky header height and top adjustment for mobile
    const navbar = document.querySelector('.navbar');
    const navContent = document.querySelector('.nav-content');

    function updateStickyStyles() {
        if (!navbar) return;

        if (window.innerWidth <= 768) {
            if (navContent) {
                const navContentHeight = navContent.offsetHeight;
                navbar.style.top = `-${navContentHeight}px`;
                
                // Recalculate the remaining visible height for scroll-margin-top
                const visibleHeight = navbar.offsetHeight - navContentHeight;
                document.documentElement.style.setProperty('--sticky-header-height', `${visibleHeight + 16}px`);
            }
        } else {
            navbar.style.top = '';
            document.documentElement.style.setProperty('--sticky-header-height', `${navbar.offsetHeight + 16}px`);
        }
    }

    // Run on resize and initial load
    window.addEventListener('resize', updateStickyStyles);
    // Run after a tiny delay to ensure layout has settled
    setTimeout(updateStickyStyles, 100);

    // Alphabet Toggle Logic for mobile
    const toggleBtn = document.getElementById('alphabetToggleBtn');
    const headerContainer = document.querySelector('.drivers-header-container');

    if (toggleBtn && headerContainer) {
        toggleBtn.addEventListener('click', function () {
            const isAlphabetActive = headerContainer.classList.toggle('show-alphabet');
            toggleBtn.classList.toggle('active', isAlphabetActive);
            
            // Recalculate sticky styles as the height might have changed
            updateStickyStyles();
            // Just in case there is a transition/layout settle delay, recalculate again
            setTimeout(updateStickyStyles, 50);
        });

        // Close alphabet view and show search when a letter is clicked
        const paginationItems = headerContainer.querySelectorAll('.pagination-item');
        paginationItems.forEach(item => {
            item.addEventListener('click', function () {
                if (window.innerWidth <= 768) {
                    headerContainer.classList.remove('show-alphabet');
                    toggleBtn.classList.remove('active');
                    updateStickyStyles();
                }
            });
        });
    }
});
