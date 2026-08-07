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
 * Logic for the session details page.
 * Expects SESSION_CONFIG to be defined globally.
 */

document.addEventListener('DOMContentLoaded', () => {
    if (typeof SESSION_CONFIG === 'undefined') {
        console.error('SESSION_CONFIG is not defined.');
        return;
    }

    const {
        lapData,
        driversList,
        sessionDrivers,
        heroNames
    } = SESSION_CONFIG;

    const ctx = document.getElementById('lapChart').getContext('2d');
    const colors = ['#38bdf8', '#f87171', '#4ade80', '#fbbf24', '#a78bfa', '#ec4899', '#6366f1', '#f59e0b', '#10b981', '#ef4444'];

    // Determine max laps
    let maxLaps = 0;
    Object.values(lapData).forEach(d => {
        maxLaps = Math.max(maxLaps, ...d.laps);
    });

    const datasets = driversList.map((driver, index) => {
        const name = driver.name;
        const d = lapData[name];
        return {
            label: name,
            data: d ? d.times : [],
            borderColor: colors[index % colors.length],
            backgroundColor: colors[index % colors.length] + '22',
            tension: 0.3,
            fill: false,
            pointRadius: 4,
            pointHoverRadius: 6
        };
    });

    const chart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: Array.from({ length: maxLaps }, (_, i) => i + 1),
            datasets: datasets
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    title: { display: true, text: 'Lap Time (s)', color: '#94a3b8' },
                    grid: { color: 'rgba(148, 163, 184, 0.1)' },
                    ticks: { color: '#94a3b8' }
                },
                x: {
                    title: { display: true, text: 'Lap Number', color: '#94a3b8' },
                    grid: { color: 'rgba(148, 163, 184, 0.1)' },
                    ticks: { color: '#94a3b8' }
                }
            },
            plugins: {
                legend: {
                    display: false // Hide default legend
                },
                tooltip: {
                    backgroundColor: '#1e293b',
                    titleColor: '#f8fafc',
                    bodyColor: '#f8fafc',
                    borderColor: '#334155',
                    borderWidth: 1
                }
            }
        }
    });

    // Handle checkboxes
    const toggles = document.querySelectorAll('.driver-toggle');
    const selectAll = document.getElementById('selectAll');

    toggles.forEach((checkbox) => {
        checkbox.addEventListener('change', () => {
            const driverName = checkbox.getAttribute('data-driver');
            const isVisible = checkbox.checked;

            // Find dataset index by label for robustness
            const datasetIndex = chart.data.datasets.findIndex(ds => ds.label === driverName);
            if (datasetIndex !== -1) {
                chart.setDatasetVisibility(datasetIndex, isVisible);
                chart.update();
            }

            // Update selectAll state
            const allChecked = Array.from(toggles).every(cb => cb.checked);
            const anyChecked = Array.from(toggles).some(cb => cb.checked);
            if (selectAll) {
                selectAll.checked = allChecked;
                selectAll.indeterminate = anyChecked && !allChecked;
            }
        });
    });

    if (selectAll) {
        selectAll.addEventListener('change', () => {
            const isVisible = selectAll.checked;
            toggles.forEach((checkbox) => {
                checkbox.checked = isVisible;
                const driverName = checkbox.getAttribute('data-driver');
                const datasetIndex = chart.data.datasets.findIndex(ds => ds.label === driverName);
                if (datasetIndex !== -1) {
                    chart.setDatasetVisibility(datasetIndex, isVisible);
                }
            });
            chart.update();
        });
    }

    // Handle marker filtering
    const heroDriver = sessionDrivers.find(d => heroNames.some(h => d.includes(h)));
    const defaultDriver = heroDriver ? heroDriver : (sessionDrivers.length > 0 ? sessionDrivers[0] : null);

    document.querySelectorAll('.driver-marker-select').forEach(select => {
        const videoIndex = select.dataset.videoIndex;
        const markerContainer = document.getElementById(`markers-${videoIndex}`);
        if (!markerContainer) return;
        
        const pills = markerContainer.querySelectorAll('.marker-pill');
        const noMarkersMsg = markerContainer.querySelector('.no-markers-msg');

        const filterMarkers = (selectedDriver) => {
            let visibleCount = 0;
            pills.forEach(pill => {
                if (pill.dataset.driver === selectedDriver) {
                    pill.style.display = 'block';
                    visibleCount++;
                } else {
                    pill.style.display = 'none';
                }
            });
            if (noMarkersMsg) noMarkersMsg.style.display = visibleCount === 0 ? 'block' : 'none';
        };

        select.addEventListener('change', (e) => filterMarkers(e.target.value));

        // Initial setup
        if (defaultDriver) {
            select.value = defaultDriver;
            filterMarkers(defaultDriver);
        }
    });
});
