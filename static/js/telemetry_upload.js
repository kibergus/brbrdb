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
 * Client-side Telemetry Pre-processing and Upload Pipeline.
 *
 * Architecture supports:
 * 1. CsvTelemetryProcessor: GoPro / RaceBox / RaceTools CSV parsing and payload preparation.
 * 2. GoProVideoProcessor: Extensible scaffold for browser-based MP4/LRV GPMF extraction
 *    using File.slice() without uploading heavy video files.
 */

export const GYROSCOPE_CHANNELS = ['Yaw Rate', 'Pitch Rate', 'Roll Rate'];

/**
 * Checks whether the given CSV header line contains gyroscope channels.
 * @param {string} headerLine - CSV header row string
 * @returns {boolean} True if gyro channels are detected
 */
export function hasGyroscopeChannels(headerLine) {
    if (!headerLine || !headerLine.trim()) return false;
    const cols = headerLine.split(',').map(c => c.trim().toLowerCase());
    return GYROSCOPE_CHANNELS.some(ch => cols.includes(ch.toLowerCase()));
}

/**
 * Extracts tabular telemetry data and its column header row from raw CSV text,
 * skipping any pre-existing metadata header block separated by a blank line.
 * @param {string} text - Raw CSV content
 * @returns {{ headerLine: string, dataText: string }} Extracted header line and raw data text
 */
export function extractTelemetryData(text) {
    if (!text || !text.trim()) {
        return { headerLine: '', dataText: '' };
    }
    // If a RaceBox or similar metadata header exists before the first blank line, skip past it
    const blankMatch = text.match(/\r?\n\s*\r?\n/);
    const dataText = (blankMatch ? text.slice(blankMatch.index + blankMatch[0].length) : text).trimStart();
    if (!dataText) {
        return { headerLine: '', dataText: '' };
    }
    const newlineIndex = dataText.indexOf('\n');
    const headerLine = (newlineIndex === -1 ? dataText : dataText.slice(0, newlineIndex)).trim();
    return { headerLine, dataText };
}

/**
 * Constructs the streamed RaceTools CSV payload with embedded metadata header.
 * @param {Object} sessionMeta - Session metadata (track, date, time, league, class, session_name, session_id)
 * @param {string} driverName - Driver name
 * @param {string} dataText - Raw CSV tabular data string
 * @param {number|null} distanceToRearAxle - Gyro arm length in meters
 * @returns {string} Formatted RaceTools CSV payload
 */
export function buildRaceToolsPayload(sessionMeta, driverName, dataText, distanceToRearAxle = null) {
    const required = ['track', 'date', 'time', 'league', 'class_name', 'session_name', 'session_id'];
    for (const key of required) {
        if (!sessionMeta || !sessionMeta[key]) {
            throw new Error(`Missing required session metadata: ${key}`);
        }
    }
    if (!driverName) {
        throw new Error('Driver name is required.');
    }
    if (!dataText || !dataText.trim()) {
        throw new Error('Telemetry data is required.');
    }

    let headerBlock = `Format,RaceTools CSV\n` +
        `Track name,${sessionMeta.track}\n` +
        `Date,${sessionMeta.date}\n` +
        `Time,${sessionMeta.time}\n` +
        `Driver name,${driverName}\n` +
        `League,${sessionMeta.league}\n` +
        `Class,${sessionMeta.class_name}\n` +
        `Session,${sessionMeta.session_name}\n` +
        `Session ID,${sessionMeta.session_id}\n`;

    if (distanceToRearAxle != null) {
        headerBlock += `Distance to rear axle,${distanceToRearAxle}\n`;
    }
    headerBlock += '\n';

    return headerBlock + dataText;
}

/**
 * Base class for client-side telemetry processors.
 */
export class BaseTelemetryProcessor {
    /**
     * @param {File} file
     * @returns {boolean}
     */
    static canProcess() {
        return false;
    }

    /**
     * @param {File} file
     * @param {Object} options
     * @param {Function} onProgress
     * @returns {Promise<string>}
     */
    static async process() {
        throw new Error('process() not implemented');
    }
}

/**
 * Processor for CSV telemetry files (GoPro CSV, RaceBox CSV, RaceTools CSV).
 */
export class CsvTelemetryProcessor extends BaseTelemetryProcessor {
    static canProcess(file) {
        const ext = (file.name.split('.').pop() || '').toLowerCase();
        return ext === 'csv' || Boolean(file.type && file.type === 'text/csv');
    }

    static async process(file, options, onProgress) {
        if (onProgress) onProgress(10, 'Reading CSV file...');
        const text = await file.text();

        // If file already has RaceTools header block, pass it through directly
        if (text.startsWith('Format,RaceTools CSV')) {
            if (onProgress) onProgress(90, 'Validated RaceTools CSV');
            return text;
        }

        if (onProgress) onProgress(30, 'Inspecting telemetry headers...');
        const { headerLine, dataText } = extractTelemetryData(text);

        if (!dataText || !headerLine) {
            throw new Error('No telemetry data rows found in the CSV file.');
        }

        const hasGyro = hasGyroscopeChannels(headerLine);

        let distanceToRearAxle = options.distanceToRearAxle;
        if (hasGyro && (distanceToRearAxle === null || distanceToRearAxle === undefined || isNaN(distanceToRearAxle))) {
            throw new Error(
                'Gyroscope channels (Yaw Rate, Pitch Rate, Roll Rate) were detected. ' +
                'Gyro arm length (distance to rear axle) is required.'
            );
        }

        if (onProgress) onProgress(70, 'Generating payload...');
        const payload = buildRaceToolsPayload(
            options.sessionMeta,
            options.driverName,
            dataText,
            distanceToRearAxle
        );

        if (onProgress) onProgress(100, 'Payload ready for upload');
        return payload;
    }
}

/**
 * Scaffold for direct GoPro video files (.mp4 / .lrv).
 * Planned extension: client-side GPMF demuxer using File.slice() so video files
 * are parsed locally in the browser without uploading multi-gigabyte files.
 */
export class GoProVideoProcessor extends BaseTelemetryProcessor {
    static canProcess(file) {
        const ext = (file.name.split('.').pop() || '').toLowerCase();
        return ext === 'mp4' || ext === 'lrv' || Boolean(file.type && file.type.startsWith('video/'));
    }

    static async process(file, options, onProgress) {
        if (onProgress) onProgress(10, 'Inspecting GoPro video container...');
        // Extensibility hook:
        // 1. Demux MP4 boxes (ftyp, moov, trak, mdia, minf, stbl, stco/co64, stsz, stsc)
        // 2. Locate gpmd track
        // 3. Extract GPMF samples using File.slice()
        // 4. Decode GPS5/GPS9, ACCL, GYRO packets
        // 5. Build RaceTools CSV payload
        throw new Error(
            'Direct GoPro video (.mp4 / .lrv) in-browser telemetry extraction is being prepared. ' +
            'Please export telemetry to CSV first or use the GoPro CSV upload option.'
        );
    }
}

/**
 * Unified Telemetry Processor Dispatcher.
 */
export class TelemetryProcessor {
    static getProcessor(file) {
        if (CsvTelemetryProcessor.canProcess(file)) {
            return CsvTelemetryProcessor;
        }
        if (GoProVideoProcessor.canProcess(file)) {
            return GoProVideoProcessor;
        }
        return null;
    }

    static async process(file, options, onProgress) {
        const processor = this.getProcessor(file);
        if (!processor) {
            const ext = (file.name.split('.').pop() || '').toLowerCase();
            throw new Error(`Unsupported file type: .${ext}. Please select a .csv file.`);
        }
        return await processor.process(file, options, onProgress);
    }
}

/**
 * Initialize the Upload Telemetry page UI bindings.
 * @param {Object} config - Page configuration (sessionMeta, heroDriver, defaultArmLength)
 */
export function initTelemetryUpload(config) {
    const dropzone = document.getElementById('uploadDropzone');
    const fileInput = document.getElementById('telemetryFileInput');
    const fileInfo = document.getElementById('selectedFileInfo');
    const fileNameSpan = document.getElementById('selectedFileName');
    const fileSizeSpan = document.getElementById('selectedFileSize');
    const removeFileBtn = document.getElementById('removeFileBtn');
    const driverInput = document.getElementById('driverNameInput');
    const armLengthInput = document.getElementById('armLengthInput');
    const uploadForm = document.getElementById('telemetryUploadForm');
    const submitBtn = document.getElementById('submitUploadBtn');
    const progressContainer = document.getElementById('uploadProgressContainer');
    const progressBar = document.getElementById('uploadProgressBar');
    const progressStatus = document.getElementById('uploadProgressStatus');
    const alertBox = document.getElementById('uploadAlert');
    const successCard = document.getElementById('uploadSuccessCard');
    const viewTelemetryBtn = document.getElementById('viewTelemetryBtn');
    const returnSessionBtn = document.getElementById('returnSessionBtn');
    const gyroHint = document.getElementById('gyroHint');

    let selectedFile = null;

    function showAlert(message, type = 'error') {
        if (!alertBox) return;
        alertBox.textContent = message;
        alertBox.className = `alert alert-${type}`;
        alertBox.style.display = 'block';
    }

    function hideAlert() {
        if (!alertBox) return;
        alertBox.style.display = 'none';
        alertBox.textContent = '';
    }

    function updateProgress(percent, text) {
        if (!progressContainer) return;
        progressContainer.style.display = 'block';
        if (progressBar) progressBar.style.width = `${percent}%`;
        if (progressStatus) progressStatus.textContent = text;
    }

    function setSelectedFile(file) {
        selectedFile = file;
        hideAlert();
        if (file) {
            if (dropzone) dropzone.style.display = 'none';
            if (fileInfo) fileInfo.style.display = 'flex';
            if (fileNameSpan) fileNameSpan.textContent = file.name;
            if (fileSizeSpan) {
                const sizeMb = (file.size / (1024 * 1024)).toFixed(2);
                fileSizeSpan.textContent = `${sizeMb} MB`;
            }

            // Quick peek at CSV to check for gyro channels
            if (CsvTelemetryProcessor.canProcess(file)) {
                const slice = file.slice(0, 16384);
                slice.text().then(chunk => {
                    const { headerLine } = extractTelemetryData(chunk);
                    if (headerLine && hasGyroscopeChannels(headerLine)) {
                        if (gyroHint) {
                            gyroHint.style.display = 'block';
                            gyroHint.textContent = 'ℹ Gyroscope channels detected in file. Please ensure Gyro Arm Length is set.';
                        }
                    } else if (gyroHint) {
                        gyroHint.style.display = 'none';
                    }
                }).catch(() => {});
            }
        } else {
            if (dropzone) dropzone.style.display = 'block';
            if (fileInfo) fileInfo.style.display = 'none';
            if (fileInput) fileInput.value = '';
            if (gyroHint) gyroHint.style.display = 'none';
        }
    }

    if (dropzone && fileInput) {
        dropzone.addEventListener('click', () => fileInput.click());

        ['dragenter', 'dragover'].forEach(eventName => {
            dropzone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.add('drag-over');
            });
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropzone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.remove('drag-over');
            });
        });

        dropzone.addEventListener('drop', (e) => {
            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                setSelectedFile(e.dataTransfer.files[0]);
            }
        });

        fileInput.addEventListener('change', () => {
            if (fileInput.files && fileInput.files.length > 0) {
                setSelectedFile(fileInput.files[0]);
            }
        });
    }

    if (removeFileBtn) {
        removeFileBtn.addEventListener('click', () => setSelectedFile(null));
    }

    if (uploadForm) {
        uploadForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            hideAlert();

            if (!selectedFile) {
                showAlert('Please select a telemetry CSV file to upload.', 'error');
                return;
            }

            const driverName = (driverInput ? driverInput.value : '').trim();
            if (!driverName) {
                showAlert('Driver name is required.', 'error');
                if (driverInput) driverInput.focus();
                return;
            }

            let armLength = null;
            if (armLengthInput && armLengthInput.value.trim() !== '') {
                const parsed = parseFloat(armLengthInput.value);
                if (isNaN(parsed) || parsed < 0) {
                    showAlert('Please enter a valid positive number for gyro arm length.', 'error');
                    armLengthInput.focus();
                    return;
                }
                armLength = parsed;
            }

            try {
                if (submitBtn) {
                    submitBtn.disabled = true;
                    submitBtn.textContent = 'Processing...';
                }

                updateProgress(5, 'Pre-processing telemetry locally...');

                // Run client-side pre-processing pipeline
                const payload = await TelemetryProcessor.process(
                    selectedFile,
                    {
                        sessionMeta: config.sessionMeta,
                        driverName,
                        distanceToRearAxle: armLength
                    },
                    (percent, status) => {
                        updateProgress(Math.min(percent, 70), status);
                    }
                );

                updateProgress(75, 'Uploading telemetry to server...');

                // Upload directly as CSV to /api/upload/stream
                const uploadUrl = config.uploadUrl || (uploadForm ? uploadForm.action : '') || '/api/upload/stream';
                const response = await fetch(uploadUrl, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'text/csv; charset=utf-8',
                        'X-Requested-With': 'XMLHttpRequest'
                    },
                    body: payload
                });

                updateProgress(90, 'Processing derivative data and lap detection...');

                if (!response.ok) {
                    const errData = await response.json().catch(() => ({}));
                    throw new Error(errData.message || errData.error || `Server responded with status ${response.status}`);
                }

                const result = await response.json();
                updateProgress(100, 'Upload complete!');

                if (uploadForm) uploadForm.style.display = 'none';
                if (successCard) {
                    successCard.style.display = 'block';
                    const telUrl = result.telemetry_url || config.telemetryUrl;
                    const sessUrl = result.session_url || config.sessionUrl;
                    if (viewTelemetryBtn && telUrl) {
                        viewTelemetryBtn.href = telUrl;
                    }
                    if (returnSessionBtn && sessUrl) {
                        returnSessionBtn.href = sessUrl;
                    }
                }
            } catch (err) {
                showAlert(err.message || 'An error occurred during upload.', 'error');
                if (progressContainer) progressContainer.style.display = 'none';
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Upload Telemetry';
                }
            }
        });
    }
}
