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
 * Logic for the driver details page.
 * Expects DRIVER_CONFIG to be defined globally.
 */

let updateChart;

const createStickyYAxisPlugin = (targetCanvasId) => {
    return {
        id: 'stickyYAxis_' + targetCanvasId,
        afterDraw: (chart) => {
            if (!chart.scales || !chart.scales.y) return;

            const dpr = window.devicePixelRatio || 1;
            const sourceCanvas = chart.ctx.canvas;
            const targetCanvas = document.getElementById(targetCanvasId);
            if (!targetCanvas) return;

            const parentStyle = window.getComputedStyle(sourceCanvas.parentElement);
            const paddingLeft = Math.round(parseFloat(parentStyle.paddingLeft) || 0);

            const copyWidth = Math.round(chart.scales.y.right);
            const copyHeight = Math.min(Math.round(chart.scales.y.bottom) + 10, Math.round(chart.height));

            const targetWidth = copyWidth + paddingLeft;

            if (targetCanvas.width !== targetWidth * dpr || targetCanvas.height !== copyHeight * dpr) {
                targetCanvas.width = targetWidth * dpr;
                targetCanvas.height = copyHeight * dpr;
                targetCanvas.style.width = targetWidth + 'px';
                targetCanvas.style.height = copyHeight + 'px';
            }

            const container = targetCanvas.parentElement;
            if (container) {
                if (container.style.width !== targetWidth + 'px') {
                    container.style.width = targetWidth + 'px';
                }
                const expectedMarginLeft = -paddingLeft + 'px';
                if (container.style.marginLeft !== expectedMarginLeft) {
                    container.style.marginLeft = expectedMarginLeft;
                }
                if (container.style.display !== 'block') {
                    container.style.display = 'block';
                }
            }

            const targetCtx = targetCanvas.getContext('2d');
            targetCtx.clearRect(0, 0, targetCanvas.width, targetCanvas.height);

            targetCtx.fillStyle = parentStyle.backgroundColor || '#1e293b';
            targetCtx.fillRect(0, 0, targetCanvas.width, targetCanvas.height);

            targetCtx.drawImage(
                sourceCanvas,
                0, 0, copyWidth * dpr, copyHeight * dpr,
                paddingLeft * dpr, 0, copyWidth * dpr, copyHeight * dpr
            );
        }
    };
};

document.addEventListener('DOMContentLoaded', () => {
    if (typeof DRIVER_CONFIG === 'undefined') {
        console.error('DRIVER_CONFIG is not defined.');
        return;
    }

    const {
        rawProgressionData,
        violinPlotBaseUrl,
        percentilePlotBaseUrl
    } = DRIVER_CONFIG;

    let isZoomedManually = false;

    document.querySelectorAll('.selector-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const itemId = item.id.replace('selector-', '');

            // Update active state
            document.querySelectorAll('.selector-item').forEach(i => i.classList.remove('active'));
            item.classList.add('active');

            // Update content visibility
            document.querySelectorAll('.tab-content').forEach(content => {
                content.style.display = 'none';
            });
            const contentDiv = document.getElementById('tab-content-' + itemId);
            if (contentDiv) {
                contentDiv.style.display = 'block';
                // Trigger chart update if switching to progression tab
                if (itemId === 'progression' && typeof updateChart === 'function') {
                    updateChart();
                }
            }

            // Update URL hash without jumping
            history.pushState(null, null, '#' + itemId);
        });
    });

    // Handle initial hash
    const hash = window.location.hash.replace('#', '');
    if (hash) {
        const activeItem = document.getElementById('selector-' + hash);
        if (activeItem) {
            activeItem.click();
        }
    } else {
        // Default: progression. Make sure charts update.
        if (typeof updateChart === 'function') {
            updateChart();
        }
    }

    if (rawProgressionData && rawProgressionData.length > 0) {
        // Group data by Series/Class
        const groupedData = {};
        rawProgressionData.forEach(d => {
            const key = d.league_name + ' ' + d.class;
            if (!groupedData[key]) {
                groupedData[key] = {
                    label: key,
                    data: [],
                    league: d.league,
                    class: d.class,
                    isVisible: d.league !== 'kartsim' // default true for non-kartsim
                };
            }
            groupedData[key].data.push(d);
        });

        const colors = ['#38bdf8', '#f87171', '#4ade80', '#fbbf24', '#a78bfa', '#ec4899', '#6366f1', '#f59e0b', '#10b981', '#ef4444'];

        Object.values(groupedData).forEach(group => {
            // Sort group data by date_time
            group.data.sort((a, b) => a.date_time.localeCompare(b.date_time));

            const smoothedVals = [];
            group.data.forEach((p, i) => {
                const windowStart = Math.max(0, i - 2);
                const windowEnd = Math.min(group.data.length, i + 3);
                // Only include values in the window that have the same orig_class and orig_league
                const windowVals = group.data.slice(windowStart, windowEnd)
                    .filter(dp => dp.orig_class === p.orig_class && dp.orig_league === p.orig_league)
                    .map(dp => dp.gap);
                smoothedVals.push(getMedian(windowVals));
            });
            group.smoothedVals = smoothedVals;
        });

        // Custom HTML tooltip handler
        const externalTooltipHandler = (context) => {
            const { chart, tooltip } = context;

            // Find or create tooltip element inside chart container
            let tooltipEl = chart.canvas.parentNode.querySelector('.custom-chartjs-tooltip');
            if (!tooltipEl) {
                tooltipEl = document.createElement('div');
                tooltipEl.className = 'custom-chartjs-tooltip';
                chart.canvas.parentNode.appendChild(tooltipEl);
            }

            // Hide if no tooltip or opacity is 0
            if (tooltip.opacity === 0) {
                tooltipEl.style.opacity = 0;
                return;
            }

            // Set Text
            if (tooltip.body) {
                const titleLines = tooltip.title || [];
                const bodyLines = tooltip.body.map(b => b.lines);

                // Find the condition for the current dataPoint
                const dataIndex = tooltip.dataPoints[0].dataIndex;
                const dt = chart.data.rawDates[dataIndex];
                const p = rawProgressionData.find(d => d.date_time === dt);
                const condition = p ? p.track_conditions : '';

                let conditionHtml = '';
                if (condition) {
                    let color = '#ffffff';
                    const condLower = condition.toLowerCase();
                    if (condLower.includes('dry')) color = '#facc15';
                    else if (condLower.includes('wet')) color = '#3b82f6';
                    else if (condLower.includes('damp')) color = '#7dd3fc';

                    conditionHtml = `<span style="color: ${color}; font-weight: bold; margin-left: 8px;">${condition}</span>`;
                }

                let innerHtml = '<div style="font-weight: 600; margin-bottom: 6px; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 4px; display: flex; align-items: center; justify-content: space-between;">';
                titleLines.forEach(function (title) {
                    innerHtml += '<span>' + title + '</span>';
                });
                innerHtml += conditionHtml + '</div>';

                innerHtml += '<div style="display: flex; flex-direction: column; gap: 4px;">';
                bodyLines.forEach(function (body) {
                    innerHtml += '<div style="display: flex; align-items: center; gap: 6px;">' + body + '</div>';
                });
                innerHtml += '</div>';

                tooltipEl.innerHTML = innerHtml;
            }

            // Display and position
            tooltipEl.style.opacity = 1;

            const caretX = tooltip.caretX;
            const caretY = tooltip.caretY;

            tooltipEl.style.left = caretX + 'px';
            if (caretY < 120) {
                tooltipEl.style.top = (caretY + 35) + 'px';
                tooltipEl.style.transform = 'translate(-50%, 0)';
            } else {
                tooltipEl.style.top = (caretY + 10) + 'px';
                tooltipEl.style.transform = '';
            }
        };

        // Initialize Gap Chart
        const ctxGap = document.getElementById('progressionChart').getContext('2d');
        const progressionChart = new Chart(ctxGap, {
            type: 'line',
            data: { labels: [], datasets: [] },
            plugins: [createStickyYAxisPlugin('progressionChartYAxis')],
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        title: { display: true, text: 'Gap to Leader (s)', color: '#94a3b8' },
                        grid: { color: 'rgba(148, 163, 184, 0.1)' },
                        ticks: { color: '#94a3b8' }
                    },
                    x: {
                        offset: true,
                        grid: { color: 'rgba(148, 163, 184, 0.1)' },
                        ticks: { color: '#94a3b8', maxRotation: 45, minRotation: 45, font: { size: 10 } }
                    }
                },
                onClick: (e, elements) => {
                    if (elements.length > 0) {
                        const element = elements[elements.length - 1];
                        const datasetIndex = element.datasetIndex;
                        const index = element.index;
                        const dsLabel = progressionChart.data.datasets[datasetIndex].label;
                        const groupKey = dsLabel.replace(' (Actual)', '');
                        const dt = progressionChart.data.rawDates[index];
                        const pt = groupedData[groupKey].data.find(d => d.date_time === dt);
                        if (pt && pt.session_id) {
                            const url = `/session/${encodeURIComponent(pt.orig_league)}/${encodeURIComponent(pt.orig_class)}/${encodeURIComponent(pt.date)}/${encodeURIComponent(pt.track)}/${encodeURIComponent(pt.session_id)}`;
                            window.open(url, '_blank');
                        }
                    }
                },
                onHover: (e, elements) => {
                    e.native.target.style.cursor = elements.length > 0 ? 'pointer' : 'default';
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        enabled: false,
                        external: externalTooltipHandler,
                        callbacks: {
                            label: function (context) {
                                const dsLabel = context.dataset.label;
                                const groupKey = dsLabel.replace(' (Actual)', '');
                                const isActual = dsLabel.includes('(Actual)');
                                const dt = context.chart.data.rawDates[context.dataIndex];

                                if (isActual) {
                                    const pt = groupedData[groupKey].data.find(d => d.date_time === dt);
                                    if (pt) {
                                        return pt.league_name + ' ' + pt.class + ' | ' + pt.session_name + ': +' + pt.gap.toFixed(3) + 's';
                                    }
                                } else {
                                    const val = context.parsed.y;
                                    if (val !== null && !isNaN(val)) {
                                        return groupKey + ' (Smoothed): +' + val.toFixed(3) + 's';
                                    }
                                }
                                return '';
                            }
                        }
                    }
                }
            }
        });

        const canvasGap = document.getElementById('progressionChart');
        if (canvasGap) {
            canvasGap.addEventListener('wheel', (event) => {
                if (event.ctrlKey) {
                    event.preventDefault();
                    isZoomedManually = true;

                    let currentMax = progressionChart.options.scales.y.max;
                    if (currentMax === undefined || currentMax === null) {
                        currentMax = progressionChart.scales.y.max;
                    }

                    if (typeof currentMax !== 'number' || isNaN(currentMax) || currentMax <= 0) {
                        currentMax = 1.0;
                    }

                    // deltaY > 0: scroll down -> zoom out (increase maxy)
                    // deltaY < 0: scroll up -> zoom in (decrease maxy)
                    const zoomIntensity = 0.06; // 6% zoom step
                    const normalizedDelta = Math.max(-1, Math.min(1, event.deltaY / 100));
                    const factor = 1 + (normalizedDelta * zoomIntensity);
                    let newMax = currentMax * factor;

                    // Ensure minimum remains 0 (min = 0) and maxy doesn't go below a small positive value
                    if (newMax < 0.01) {
                        newMax = 0.01;
                    }

                    progressionChart.options.scales.y.min = 0;
                    progressionChart.options.scales.y.max = newMax;
                    progressionChart.update();
                }
            }, { passive: false });

            canvasGap.addEventListener('dblclick', () => {
                isZoomedManually = false;
                updateChart();
            });
        }

        // Initialize Position Chart
        const ctxPos = document.getElementById('positionChart').getContext('2d');
        const positionChart = new Chart(ctxPos, {
            type: 'line',
            data: { labels: [], datasets: [] },
            plugins: [createStickyYAxisPlugin('positionChartYAxis')],
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                scales: {
                    y: {
                        reverse: true,
                        beginAtZero: false,
                        title: { display: true, text: 'Position', color: '#94a3b8' },
                        grid: { color: 'rgba(148, 163, 184, 0.1)' },
                        ticks: {
                            color: '#94a3b8',
                            stepSize: 1,
                            precision: 0,
                            callback: function (value) {
                                if (document.getElementById('normalizePos').checked) {
                                    if (Math.abs(value - 0) < 0.0001) return 'first';
                                    if (Math.abs(value - 0.5) < 0.0001) return '1/2';
                                    if (Math.abs(value - 1) < 0.0001) return 'last';
                                    return '';
                                }
                                return value;
                            }
                        }
                    },
                    x: {
                        offset: true,
                        grid: { color: 'rgba(148, 163, 184, 0.1)' },
                        ticks: { color: '#94a3b8', maxRotation: 45, minRotation: 45, font: { size: 10 } }
                    }
                },
                onClick: (e, elements) => {
                    if (elements.length > 0) {
                        const element = elements[0];
                        const datasetIndex = element.datasetIndex;
                        const index = element.index;
                        const dsLabel = positionChart.data.datasets[datasetIndex].label;
                        const groupKey = dsLabel.replace(' (Position)', '');
                        const dt = positionChart.data.rawDates[index];
                        const pt = groupedData[groupKey].data.find(d => d.date_time === dt);
                        if (pt && pt.session_id) {
                            const url = `/session/${encodeURIComponent(pt.orig_league)}/${encodeURIComponent(pt.orig_class)}/${encodeURIComponent(pt.date)}/${encodeURIComponent(pt.track)}/${encodeURIComponent(pt.session_id)}`;
                            window.open(url, '_blank');
                        }
                    }
                },
                onHover: (e, elements) => {
                    e.native.target.style.cursor = elements.length > 0 ? 'pointer' : 'default';
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        enabled: false,
                        external: externalTooltipHandler,
                        callbacks: {
                            label: function (context) {
                                const dsLabel = context.dataset.label;
                                if (dsLabel === 'Field Size') {
                                    const dt = context.chart.data.rawDates[context.dataIndex];
                                    const pt = rawProgressionData.find(d => d.date_time === dt);
                                    return pt ? pt.league_name + ' ' + pt.class + ' | ' + pt.session_name + ': ' + pt.participants + ' participants' : '';
                                }
                                const groupKey = dsLabel.replace(' (Position)', '');
                                const dt = context.chart.data.rawDates[context.dataIndex];
                                const pt = groupedData[groupKey].data.find(d => d.date_time === dt);
                                if (pt) {
                                    let label = pt.league_name + ' ' + pt.class + ' | ' + pt.session_name + ': P' + pt.pos + ' / ' + pt.participants;
                                    if (document.getElementById('normalizePos').checked) {
                                        label += ' (Norm: ' + context.parsed.y.toFixed(3) + ')';
                                    }
                                    return label;
                                }
                                return '';
                            }
                        }
                    }
                }
            }
        });

        document.getElementById('normalizePos').addEventListener('change', () => {
            const isNormalized = document.getElementById('normalizePos').checked;
            positionChart.options.scales.y.title.text = isNormalized ? 'Normalized Position' : 'Position';
            positionChart.options.scales.y.min = isNormalized ? 0 : undefined;
            positionChart.options.scales.y.max = isNormalized ? 1 : undefined;
            positionChart.options.scales.y.ticks.stepSize = isNormalized ? 0.5 : 1;
            updateChart();
        });

        updateChart = () => {
            const selectedTrack = document.getElementById('track-filter').value;
            const selectedYear = document.getElementById('year-filter').value;
            const activeDateTimes = new Set();
            Object.values(groupedData).forEach(group => {
                if (group.isVisible) {
                    group.data.forEach(d => {
                        const matchesTrack = (selectedTrack === 'all' || d.track === selectedTrack);
                        const matchesYear = (selectedYear === 'all' || (d.date && d.date.startsWith(selectedYear)));
                        if (matchesTrack && matchesYear) {
                            activeDateTimes.add(d.date_time);
                        }
                    });
                }
            });
            const activeDatesArray = [...activeDateTimes].sort();

            // Calculate 90th percentile of active gaps for default y-axis limit
            if (!isZoomedManually) {
                const activeGaps = [];
                Object.values(groupedData).forEach(group => {
                    if (group.isVisible) {
                        group.data.forEach(d => {
                            const matchesTrack = (selectedTrack === 'all' || d.track === selectedTrack);
                            const matchesYear = (selectedYear === 'all' || (d.date && d.date.startsWith(selectedYear)));
                            if (matchesTrack && matchesYear) {
                                if (typeof d.gap === 'number' && !isNaN(d.gap)) {
                                    activeGaps.push(d.gap);
                                }
                            }
                        });
                    }
                });

                if (activeGaps.length > 0) {
                    const p90 = getPercentile(activeGaps, 90);
                    // Ensure the max y-limit is at least some minimum positive value (e.g. 0.1s)
                    progressionChart.options.scales.y.min = 0;
                    progressionChart.options.scales.y.max = Math.max(0.1, p90);
                } else {
                    progressionChart.options.scales.y.min = 0;
                    progressionChart.options.scales.y.max = undefined;
                }
            }

            // Dynamic width scaling
            const minWidthPerPoint = 15;
            const maxWidthPerPoint = 80; // Prevent huge gaps between few points
            const containerBase = document.getElementById('tab-content-progression');

            let availableWidth = 1200 - 64;
            if (containerBase && containerBase.offsetWidth > 0) {
                availableWidth = containerBase.clientWidth - 32;
            } else {
                const mainContainer = document.querySelector('.container');
                if (mainContainer && mainContainer.offsetWidth > 0) {
                    availableWidth = mainContainer.clientWidth - 32;
                }
            }

            // For few points, don't stretch them across the whole screen.
            // For many points, ensure at least minWidthPerPoint.
            let finalWidth = activeDatesArray.length * minWidthPerPoint;

            // If it can fit on screen, maybe stretch a bit but not too much.
            if (finalWidth < availableWidth) {
                finalWidth = Math.min(availableWidth, activeDatesArray.length * maxWidthPerPoint);
            }

            // Ensure some minimum width for visibility if no points
            if (activeDatesArray.length === 0) finalWidth = availableWidth;

            const pcContainer = document.getElementById('progressionChart').parentElement;
            const posContainer = document.getElementById('positionChart').parentElement;

            pcContainer.style.width = finalWidth + 'px';
            posContainer.style.width = finalWidth + 'px';

            const labels = activeDatesArray.map(dt => {
                const p = rawProgressionData.find(d => d.date_time === dt);
                return p.date + ' - ' + p.track;
            });

            // Update Gap Chart
            progressionChart.data.labels = labels;
            progressionChart.data.rawDates = activeDatesArray;
            const gapDatasets = [];

            // Update Position Chart
            positionChart.data.labels = labels;
            positionChart.data.rawDates = activeDatesArray;
            const posDatasets = [];

            const isNormalized = document.getElementById('normalizePos').checked;
            Object.values(groupedData).forEach((group, index) => {
                if (!group.isVisible) return;

                const color = colors[index % colors.length];

                // Gap Data
                const markersData = [];
                const pointColors = [];
                activeDatesArray.forEach(dt => {
                    const groupIdx = group.data.findIndex(d => d.date_time === dt);
                    if (groupIdx !== -1) {
                        markersData.push(group.data[groupIdx].gap);
                        const tc = group.data[groupIdx].track_conditions || '';
                        const ts = tc.toLowerCase();
                        if (ts.includes('dry')) pointColors.push('#facc15');
                        else if (ts.includes('wet')) pointColors.push('#3b82f6');
                        else if (ts.includes('damp')) pointColors.push('#7dd3fc');
                        else pointColors.push('#ffffff');
                    } else {
                        markersData.push(null);
                        pointColors.push('transparent');
                    }
                });

                const metaData = activeDatesArray.map(dt => {
                    const d = group.data.find(d => d.date_time === dt);
                    return d ? { orig_class: d.orig_class, orig_league: d.orig_league } : null;
                });

                const segmentRule = {
                    borderColor: ctx => {
                        const m1 = metaData[ctx.p0DataIndex];
                        const m2 = metaData[ctx.p1DataIndex];
                        if (m1 && m2 && (m1.orig_class !== m2.orig_class || m1.orig_league !== m2.orig_league)) {
                            return 'transparent';
                        }
                        return undefined;
                    }
                };

                const smoothedData = activeDatesArray.map(dt => {
                    const groupIdx = group.data.findIndex(d => d.date_time === dt);
                    return groupIdx !== -1 ? group.smoothedVals[groupIdx] : null;
                });

                gapDatasets.push({
                    label: group.label + ' (Actual)',
                    data: markersData,
                    borderColor: 'transparent',
                    backgroundColor: pointColors,
                    pointBackgroundColor: pointColors,
                    pointBorderColor: pointColors,
                    showLine: false,
                    pointRadius: 4,
                    pointHoverRadius: 6
                });

                gapDatasets.push({
                    label: group.label,
                    data: smoothedData,
                    borderColor: color,
                    backgroundColor: 'transparent',
                    tension: 0.4,
                    fill: false,
                    spanGaps: false,
                    segment: segmentRule,
                    pointRadius: 0,
                    pointHoverRadius: 0
                });

                // Position Data
                const posData = activeDatesArray.map(dt => {
                    const groupIdx = group.data.findIndex(d => d.date_time === dt);
                    if (groupIdx !== -1) {
                        const pt = group.data[groupIdx];
                        const val = parseInt(pt.pos);
                        if (isNaN(val)) return null;

                        if (isNormalized) {
                            const n = pt.participants;
                            return n > 1 ? (val - 1) / (n - 1) : 0;
                        }
                        return val;
                    }
                    return null;
                });

                posDatasets.push({
                    label: group.label + ' (Position)',
                    data: posData,
                    borderColor: color,
                    backgroundColor: color + '44',
                    tension: 0.1,
                    fill: false,
                    spanGaps: false,
                    segment: segmentRule,
                    pointRadius: 5,
                    pointHoverRadius: 8,
                    pointBackgroundColor: pointColors,
                    pointBorderColor: pointColors,
                });

            });

            // Add a shared Participants / Field Size Line
            const sharedParticipantsData = activeDatesArray.map(dt => {
                const p = rawProgressionData.find(d => d.date_time === dt);
                if (p) {
                    return isNormalized ? 1 : p.participants;
                }
                return null;
            });

            posDatasets.push({
                label: 'Field Size',
                data: sharedParticipantsData,
                borderColor: 'rgba(148, 163, 184, 0.4)', // Slightly more visible grey
                borderWidth: 2,
                pointRadius: 0,
                fill: false,
                spanGaps: true,
                tension: 0.1,
                order: 10
            });

            // Update Plot Images (Violin and Percentile)
            const leagues = [];
            const classes = [];
            Object.values(groupedData).forEach(group => {
                if (group.isVisible) {
                    leagues.push(group.league);
                    classes.push(group.class);
                }
            });

            const params = new URLSearchParams();
            leagues.forEach(l => params.append('leagues', l));
            classes.forEach(c => params.append('classes', c));
            if (selectedTrack !== 'all') params.append('track', selectedTrack);
            if (selectedYear !== 'all') params.append('year', selectedYear);

            const updateImg = (id, baseUrl) => {
                const img = document.getElementById(id);
                if (!img) return;
                const url = baseUrl + (baseUrl.includes('?') ? '&' : '?') + params.toString();
                if (img.getAttribute('src') !== url) {
                    const loading = document.getElementById('loading-' + id);
                    if (loading) {
                        loading.style.display = 'flex';
                        const spinner = loading.querySelector('.spinner');
                        const text = loading.querySelector('.loading-text');
                        if (spinner) spinner.style.display = 'block';
                        if (text) text.textContent = id.includes('percentile') ? 'Generating performance baseline...' : 'Generating lap distributions...';
                        img.style.display = 'none';
                    }
                    img.src = url;
                }
            };

            const violinSection = document.getElementById('violin-plot-section');
            if (violinSection) {
                violinSection.style.display = selectedTrack === 'all' ? 'none' : 'block';
            }

            updateImg('violinPlotImg', violinPlotBaseUrl);
            updateImg('percentilePlotImg', percentilePlotBaseUrl);


            progressionChart.data.datasets = gapDatasets;
            progressionChart.update();

            positionChart.data.datasets = posDatasets;
            positionChart.update();

            if (typeof updateCollapsedPills === 'function') {
                updateCollapsedPills();
            }

            // Scroll to the right end after a short delay to ensure rendering
            setTimeout(() => {
                const containers = [
                    document.querySelector('#progressionChart').closest('.chart-scroll-container'),
                    document.querySelector('#positionChart').closest('.chart-scroll-container'),
                    document.querySelector('#violinScrollContainer'),
                    document.querySelector('#percentileScrollContainer')
                ];
                containers.forEach(container => {
                    if (container) {
                        container.scrollLeft = container.scrollWidth;
                    }
                });
            }, 50);
        };

        // Generate custom toggles grouped by KartSim vs Other
        const selectorsContainer = document.getElementById('progression-selectors');
        selectorsContainer.innerHTML = '';

        const kartsimGroups = [];
        const generalGroups = [];
        Object.values(groupedData).forEach(group => {
            if (group.data[0].league === 'kartsim') {
                kartsimGroups.push(group);
            } else {
                generalGroups.push(group);
            }
        });

        const sortedGroupedArray = Object.values(groupedData);

        // Render General Groups first (Flat)
        if (generalGroups.length > 0) {
            const container = document.createElement('div');
            container.className = 'progression-items-container';
            container.style.marginBottom = '2rem';
            container.style.width = '100%';

            generalGroups.sort((a, b) => a.label.localeCompare(b.label)).forEach(group => {
                const index = sortedGroupedArray.indexOf(group);
                const toggle = createToggle(group, index, true);
                container.appendChild(toggle);
            });
            selectorsContainer.appendChild(container);
        }

        // Render KartSim Group second (Section)
        if (kartsimGroups.length > 0) {
            const section = document.createElement('div');
            section.className = 'progression-league-section';

            const title = document.createElement('div');
            title.className = 'progression-league-title';
            title.textContent = 'KartSim';
            section.appendChild(title);

            const itemsContainer = document.createElement('div');
            itemsContainer.className = 'progression-items-container';
            section.appendChild(itemsContainer);

            kartsimGroups.sort((a, b) => a.class.localeCompare(b.class)).forEach(group => {
                const index = sortedGroupedArray.indexOf(group);
                const toggle = createToggle(group, index, false);
                itemsContainer.appendChild(toggle);
            });
            selectorsContainer.appendChild(section);
        }

        function createToggle(group, index, showFullLabel) {
            const toggleContainer = document.createElement('div');
            toggleContainer.style.display = 'flex';
            toggleContainer.style.alignItems = 'center';
            toggleContainer.style.gap = '0.5rem';

            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.checked = group.isVisible;
            checkbox.className = 'driver-checkbox';
            checkbox.style.setProperty('--check-color', colors[index % colors.length]);

            checkbox.addEventListener('change', () => {
                group.isVisible = checkbox.checked;
                updateChart();
            });

            const label = document.createElement('span');
            label.textContent = showFullLabel ? group.label : group.class;
            label.style.color = '#94a3b8';
            label.style.fontWeight = '600';
            label.style.cursor = 'pointer';

            label.addEventListener('click', () => {
                checkbox.click();
            });

            toggleContainer.appendChild(checkbox);
            toggleContainer.appendChild(label);
            return toggleContainer;
        }

        const collapsedPills = document.getElementById('collapsed-pills');
        const toggleBtn = document.getElementById('toggle-filter-btn');
        const toggleText = document.getElementById('toggle-filter-text');
        const toggleIcon = document.getElementById('toggle-filter-icon');
        const expandableContent = document.getElementById('filter-expandable-content');

        // Check stored state, default to collapsed (true) if not set
        let isCollapsed = localStorage.getItem('driver_filter_collapsed') !== 'false';

        function updateCollapsedPills() {
            if (!collapsedPills) return;
            collapsedPills.innerHTML = '';

            // 1. Render league pills
            Object.values(groupedData).forEach((group, index) => {
                if (group.isVisible) {
                    const color = colors[index % colors.length];
                    const pill = document.createElement('div');
                    pill.className = 'filter-pill';
                    pill.style.borderColor = color + '44';
                    pill.style.backgroundColor = color + '15';
                    pill.style.color = color;
                    pill.style.display = 'inline-flex';
                    pill.style.alignItems = 'center';
                    pill.style.gap = '0.35rem';
                    pill.style.padding = '0.25rem 0.75rem';
                    pill.style.borderRadius = '9999px';
                    pill.style.fontSize = '0.8rem';
                    pill.style.fontWeight = '600';
                    pill.style.border = '1px solid ' + color + '44';
                    pill.style.cursor = 'pointer';
                    pill.style.transition = 'all 0.2s ease';

                    pill.onmouseenter = () => {
                        pill.style.backgroundColor = color + '25';
                        pill.style.borderColor = color + '66';
                    };
                    pill.onmouseleave = () => {
                        pill.style.backgroundColor = color + '15';
                        pill.style.borderColor = color + '44';
                    };

                    const dot = document.createElement('span');
                    dot.style.display = 'inline-block';
                    dot.style.width = '6px';
                    dot.style.height = '6px';
                    dot.style.borderRadius = '50%';
                    dot.style.backgroundColor = color;

                    const text = document.createElement('span');
                    text.textContent = group.label;

                    pill.appendChild(dot);
                    pill.appendChild(text);

                    pill.addEventListener('click', (e) => {
                        e.stopPropagation();
                        isCollapsed = false;
                        localStorage.setItem('driver_filter_collapsed', 'false');
                        updateFilterState();
                    });

                    collapsedPills.appendChild(pill);
                }
            });

            // 2. Render track pill (if selected)
            const selectedTrack = document.getElementById('track-filter').value;
            if (selectedTrack && selectedTrack !== 'all') {
                const pill = document.createElement('div');
                pill.className = 'filter-pill track-pill';
                pill.style.borderColor = 'rgba(167, 139, 250, 0.3)';
                pill.style.backgroundColor = 'rgba(167, 139, 250, 0.1)';
                pill.style.color = '#ffffff';
                pill.style.display = 'inline-flex';
                pill.style.alignItems = 'center';
                pill.style.gap = '0.35rem';
                pill.style.padding = '0.25rem 0.75rem';
                pill.style.borderRadius = '9999px';
                pill.style.fontSize = '0.8rem';
                pill.style.fontWeight = '600';
                pill.style.border = '1px solid rgba(167, 139, 250, 0.3)';
                pill.style.cursor = 'pointer';
                pill.style.transition = 'all 0.2s ease';

                pill.onmouseenter = () => {
                    pill.style.backgroundColor = 'rgba(167, 139, 250, 0.2)';
                    pill.style.borderColor = 'rgba(167, 139, 250, 0.5)';
                };
                pill.onmouseleave = () => {
                    pill.style.backgroundColor = 'rgba(167, 139, 250, 0.1)';
                    pill.style.borderColor = 'rgba(167, 139, 250, 0.3)';
                };

                pill.innerHTML = `
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display: inline-block; vertical-align: middle;">
                        <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path>
                        <circle cx="12" cy="10" r="3"></circle>
                    </svg>
                    <span>${selectedTrack}</span>
                `;

                pill.addEventListener('click', (e) => {
                    e.stopPropagation();
                    isCollapsed = false;
                    localStorage.setItem('driver_filter_collapsed', 'false');
                    updateFilterState();
                    setTimeout(() => {
                        document.getElementById('track-filter').focus();
                    }, 50);
                });

                collapsedPills.appendChild(pill);
            }

            // 3. Render year pill (if selected)
            const selectedYear = document.getElementById('year-filter').value;
            if (selectedYear && selectedYear !== 'all') {
                const pill = document.createElement('div');
                pill.className = 'filter-pill year-pill';
                pill.style.borderColor = 'rgba(52, 211, 153, 0.3)';
                pill.style.backgroundColor = 'rgba(52, 211, 153, 0.1)';
                pill.style.color = '#ffffff';
                pill.style.display = 'inline-flex';
                pill.style.alignItems = 'center';
                pill.style.gap = '0.35rem';
                pill.style.padding = '0.25rem 0.75rem';
                pill.style.borderRadius = '9999px';
                pill.style.fontSize = '0.8rem';
                pill.style.fontWeight = '600';
                pill.style.border = '1px solid rgba(52, 211, 153, 0.3)';
                pill.style.cursor = 'pointer';
                pill.style.transition = 'all 0.2s ease';

                pill.onmouseenter = () => {
                    pill.style.backgroundColor = 'rgba(52, 211, 153, 0.2)';
                    pill.style.borderColor = 'rgba(52, 211, 153, 0.5)';
                };
                pill.onmouseleave = () => {
                    pill.style.backgroundColor = 'rgba(52, 211, 153, 0.1)';
                    pill.style.borderColor = 'rgba(52, 211, 153, 0.3)';
                };

                pill.innerHTML = `
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="display: inline-block; vertical-align: middle;">
                        <rect x="3" y="4" width="18" height="18" rx="2" ry="2"></rect>
                        <line x1="16" y1="2" x2="16" y2="6"></line>
                        <line x1="8" y1="2" x2="8" y2="6"></line>
                        <line x1="3" y1="10" x2="21" y2="10"></line>
                    </svg>
                    <span>${selectedYear}</span>
                `;

                pill.addEventListener('click', (e) => {
                    e.stopPropagation();
                    isCollapsed = false;
                    localStorage.setItem('driver_filter_collapsed', 'false');
                    updateFilterState();
                    setTimeout(() => {
                        document.getElementById('year-filter').focus();
                    }, 50);
                });

                collapsedPills.appendChild(pill);
            }
        }

        function updateFilterState() {
            if (isCollapsed) {
                expandableContent.style.display = 'none';
                collapsedPills.style.display = 'flex';
                toggleText.textContent = 'Filters';
                toggleIcon.style.transform = 'rotate(180deg)';
                updateCollapsedPills();
            } else {
                expandableContent.style.display = 'block';
                collapsedPills.style.display = 'none';
                toggleText.textContent = 'Collapse';
                toggleIcon.style.transform = 'rotate(0deg)';
            }
        }

        if (toggleBtn) {
            toggleBtn.addEventListener('click', () => {
                isCollapsed = !isCollapsed;
                localStorage.setItem('driver_filter_collapsed', isCollapsed);
                updateFilterState();
            });
        }

        // Initialize state
        updateFilterState();

        updateChart();

        window.addEventListener('resize', () => {
            if (document.getElementById('tab-content-progression').style.display !== 'none') {
                updateChart();
            }
        });
        const filterTracksTab = () => {
            const selectedTrack = document.getElementById('track-filter').value;
            const selectedYear = document.getElementById('year-filter').value;

            document.querySelectorAll('.track-row').forEach(row => {
                const matchesTrack = (selectedTrack === 'all' || row.dataset.track === selectedTrack);

                let hasVisibleItems = false;
                row.querySelectorAll('.class-group').forEach(group => {
                    let hasVisibleInGroup = false;
                    group.querySelectorAll('.history-item').forEach(item => {
                        const dateText = item.querySelector('.history-date').textContent;
                        const matchesYear = (selectedYear === 'all' || (dateText && dateText.startsWith(selectedYear)));
                        if (matchesYear) {
                            item.style.display = 'block';
                            hasVisibleInGroup = true;
                        } else {
                            item.style.display = 'none';
                        }
                    });
                    group.style.display = hasVisibleInGroup ? 'block' : 'none';
                    if (hasVisibleInGroup) {
                        hasVisibleItems = true;
                    }
                });

                if (matchesTrack && hasVisibleItems) {
                    row.style.display = 'block';
                } else {
                    row.style.display = 'none';
                }
            });

            document.querySelectorAll('.league-block').forEach(block => {
                const hasVisible = Array.from(block.querySelectorAll('.track-row')).some(r => r.style.display !== 'none');
                block.style.display = hasVisible ? 'block' : 'none';
            });
        };

        const handleFilterChange = () => {
            filterTracksTab();
            updateChart();
        };

        document.getElementById('track-filter').addEventListener('change', handleFilterChange);
        document.getElementById('year-filter').addEventListener('change', handleFilterChange);

        // Initial track tab filter state
        filterTracksTab();

        // Synchronize scrolling for violin plots
        const vScroll = document.getElementById('violinScrollContainer');
        const pScroll = document.getElementById('percentileScrollContainer');
        if (vScroll && pScroll) {
            let isSyncing = false;
            vScroll.addEventListener('scroll', () => {
                if (isSyncing) return;
                isSyncing = true;
                pScroll.scrollLeft = vScroll.scrollLeft;
                setTimeout(() => { isSyncing = false; }, 10);
            });
            pScroll.addEventListener('scroll', () => {
                if (isSyncing) return;
                isSyncing = true;
                vScroll.scrollLeft = pScroll.scrollLeft;
                setTimeout(() => { isSyncing = false; }, 10);
            });
        }
    } else {
        const filterCard = document.getElementById('driver-filter-card');
        if (filterCard) filterCard.style.display = 'none';

        const tabContent = document.getElementById('tab-content-progression');
        if (tabContent) {
            const noDataMsg = document.createElement('div');
            noDataMsg.className = 'card';
            noDataMsg.style.padding = '2rem';
            noDataMsg.innerHTML = '<p style="color: var(--text-secondary); text-align: center;">No progression data available.</p>';
            tabContent.innerHTML = '';
            tabContent.appendChild(noDataMsg);
        }
    }
});
