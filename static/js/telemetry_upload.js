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
 * 1. RaceBox BLE Direct: Direct Web Bluetooth download from RaceBox Micro / Mini S,
 *    live unpacking of 25Hz binary history, session segmentation, and streaming upload.
 * 2. CsvTelemetryProcessor: GoPro / RaceBox / RaceTools CSV parsing and payload preparation.
 * 3. GoProVideoProcessor: Extensible scaffold for browser-based MP4/LRV GPMF extraction
 *    using File.slice() without uploading heavy video files.
 */

import {
    RaceBoxBleClient,
    pointsToCsvData,
    findMatchingSessionIndex,
    DEFAULT_AXIS_MAPPING,
    AXIS_MAPPING_PRESETS
} from './racebox_ble.js';

export const GYROSCOPE_CHANNELS = ['Yaw Rate', 'Pitch Rate', 'Roll Rate'];

export const BRT1_CHUNK_TYPES = {
    MAGIC: 0x01,
    METADATA: 0x02,
    BASELINE: 0x03,
    DELTA: 0x04,
    EOS: 0xFF
};

export const DEFAULT_BRT1_CHANNELS = [
    { name: 'Time', scale: 0.001 },
    { name: 'Latitude', scale: 1e-7 },
    { name: 'Longitude', scale: 1e-7 },
    { name: 'Speed', scale: 0.01 },
    { name: 'Throttle', scale: 0.1 },
    { name: 'Brake', scale: 0.1 },
    { name: 'Steering Angle', scale: 0.05 },
    { name: 'GForceLat', scale: 0.001 },
    { name: 'GForceLon', scale: 0.001 },
    { name: 'GForceVert', scale: 0.001 },
    { name: 'Yaw Rate', scale: 0.001 },
    { name: 'Pitch Rate', scale: 0.001 },
    { name: 'Roll Rate', scale: 0.001 },
    { name: 'Lap', scale: 1.0 }
];

/**
 * Encodes a single BRT1 chunk with 3-byte header: uint16_le length, uint8 type, and payload.
 * @param {number} chunkType - 1-byte chunk type
 * @param {Uint8Array} payload - Chunk payload bytes
 * @returns {Uint8Array} Framed chunk bytes
 */
export function encodeBrt1Chunk(chunkType, payload) {
    const payloadLen = payload ? payload.length : 0;
    if (payloadLen > 65535) {
        throw new Error(`Chunk payload size ${payloadLen} exceeds 65535 bytes limit.`);
    }
    const chunk = new Uint8Array(3 + payloadLen);
    const view = new DataView(chunk.buffer, chunk.byteOffset, chunk.byteLength);
    view.setUint16(0, payloadLen, true);
    view.setUint8(2, chunkType);
    if (payloadLen > 0) {
        chunk.set(payload, 3);
    }
    return chunk;
}

/**
 * Encodes metadata, channels, and records into a BRT1 binary stream payload.
 * @param {Object} metadata - Session metadata dictionary mapping canonical CSV header keys
 * @param {Array<{name: string, scale: number}>} channels - Channel specifications with quantization scales
 * @param {Array<Object>} records - Telemetry records with channel names as keys
 * @returns {Uint8Array} Complete BRT1 stream payload
 */
export function encodeBrt1Stream(metadata, channels, records) {
    const chunks = [];

    // 1. Magic chunk (0x01)
    const magicPayload = new TextEncoder().encode('BRT1');
    chunks.push(encodeBrt1Chunk(BRT1_CHUNK_TYPES.MAGIC, magicPayload));

    // 2. Metadata JSON chunk (0x02)
    const metaPayload = new TextEncoder().encode(JSON.stringify({ metadata, channels }));
    chunks.push(encodeBrt1Chunk(BRT1_CHUNK_TYPES.METADATA, metaPayload));

    if (!records || records.length === 0) {
        return concatUint8Arrays(chunks);
    }

    const M = channels.length;
    const scales = channels.map(c => Number(c.scale));
    const channelNames = channels.map(c => c.name);

    let prevValues = new Float64Array(M);

    function extractChannelValue(rec, colName) {
        const v = rec[colName];
        if (v === null || v === undefined || v === '') return NaN;
        const num = Number(v);
        return isNaN(num) ? NaN : num;
    }

    // First sample as Baseline / Keyframe (0x03)
    const firstPayload = new Uint8Array(M * 8);
    const firstView = new DataView(firstPayload.buffer, firstPayload.byteOffset, firstPayload.byteLength);
    for (let j = 0; j < M; j++) {
        const val = extractChannelValue(records[0], channelNames[j]);
        firstView.setFloat64(j * 8, isNaN(val) ? NaN : val, true);
        prevValues[j] = val;
    }
    chunks.push(encodeBrt1Chunk(BRT1_CHUNK_TYPES.BASELINE, firstPayload));

    // Subsequent samples
    for (let i = 1; i < records.length; i++) {
        const rec = records[i];
        const currValues = new Float64Array(M);
        let needKeyframe = false;

        for (let j = 0; j < M; j++) {
            const val = extractChannelValue(rec, channelNames[j]);
            currValues[j] = val;
            if (isNaN(prevValues[j]) && !isNaN(val)) {
                needKeyframe = true;
            }
        }

        if (needKeyframe) {
            const kfPayload = new Uint8Array(M * 8);
            const kfView = new DataView(kfPayload.buffer, kfPayload.byteOffset, kfPayload.byteLength);
            for (let j = 0; j < M; j++) {
                kfView.setFloat64(j * 8, isNaN(currValues[j]) ? NaN : currValues[j], true);
            }
            chunks.push(encodeBrt1Chunk(BRT1_CHUNK_TYPES.BASELINE, kfPayload));
            prevValues = currValues;
        } else {
            const deltaPayload = new Uint8Array(M * 2);
            const deltaView = new DataView(deltaPayload.buffer, deltaPayload.byteOffset, deltaPayload.byteLength);
            for (let j = 0; j < M; j++) {
                const cVal = currValues[j];
                const pVal = prevValues[j];
                if (isNaN(cVal)) {
                    deltaView.setInt16(j * 2, -32768, true);
                } else {
                    const base = isNaN(pVal) ? 0 : pVal;
                    let d = Math.round((cVal - base) / scales[j]);
                    d = Math.max(-32767, Math.min(32767, d));
                    deltaView.setInt16(j * 2, d, true);
                    currValues[j] = base + d * scales[j];
                }
            }
            chunks.push(encodeBrt1Chunk(BRT1_CHUNK_TYPES.DELTA, deltaPayload));
            prevValues = currValues;
        }
    }

    return concatUint8Arrays(chunks);
}

/**
 * Helper to concatenate an array of Uint8Arrays.
 * @param {Array<Uint8Array>} arrays
 * @returns {Uint8Array}
 */
function concatUint8Arrays(arrays) {
    let totalLen = 0;
    for (const arr of arrays) {
        totalLen += arr.length;
    }
    const result = new Uint8Array(totalLen);
    let offset = 0;
    for (const arr of arrays) {
        result.set(arr, offset);
        offset += arr.length;
    }
    return result;
}

/**
 * Decodes a BRT1 binary stream into metadata, channels, and records.
 * @param {ArrayBuffer|Uint8Array} bufferOrUint8Array
 * @returns {{ metadata: Object, channels: Array<Object>, records: Array<Object> }}
 */
export function decodeBrt1Stream(bufferOrUint8Array) {
    const uint8 = bufferOrUint8Array instanceof Uint8Array
        ? bufferOrUint8Array
        : new Uint8Array(bufferOrUint8Array);

    if (uint8.length < 7) {
        throw new Error('Truncated BRT1 stream: less than minimum 7 bytes.');
    }

    const view = new DataView(uint8.buffer, uint8.byteOffset, uint8.byteLength);

    let offset = 0;
    let metadata = null;
    let channels = null;
    let channelNames = [];
    let channelScales = [];
    let M = 0;
    let currentValues = null;
    const records = [];
    let seenEos = false;

    while (offset + 3 <= uint8.length) {
        const chunkLen = view.getUint16(offset, true);
        const chunkType = view.getUint8(offset + 2);
        offset += 3;

        if (offset + chunkLen > uint8.length) {
            throw new Error(`Truncated chunk payload: expected ${chunkLen} bytes.`);
        }

        const payloadStart = uint8.byteOffset + offset;

        if (chunkType === BRT1_CHUNK_TYPES.MAGIC) {
            const magicStr = new TextDecoder().decode(uint8.subarray(offset, offset + chunkLen));
            if (magicStr !== 'BRT1') {
                throw new Error(`Invalid BRT1 magic: ${magicStr}`);
            }
        } else if (chunkType === BRT1_CHUNK_TYPES.METADATA) {
            const jsonStr = new TextDecoder().decode(uint8.subarray(offset, offset + chunkLen));
            const parsed = JSON.parse(jsonStr);
            metadata = parsed.metadata || {};
            channels = parsed.channels || [];
            channelNames = channels.map(c => c.name);
            channelScales = channels.map(c => Number(c.scale));
            M = channels.length;
            currentValues = new Float64Array(M).fill(NaN);
        } else if (chunkType === BRT1_CHUNK_TYPES.BASELINE) {
            if (!channels) throw new Error('Baseline chunk received before metadata.');
            if (chunkLen !== M * 8) throw new Error(`Invalid baseline chunk size ${chunkLen}, expected ${M * 8}.`);
            const chunkView = new DataView(uint8.buffer, payloadStart, chunkLen);
            for (let j = 0; j < M; j++) {
                currentValues[j] = chunkView.getFloat64(j * 8, true);
            }
            const rec = {};
            for (let j = 0; j < M; j++) {
                rec[channelNames[j]] = isNaN(currentValues[j]) ? null : currentValues[j];
            }
            records.push(rec);
        } else if (chunkType === BRT1_CHUNK_TYPES.DELTA) {
            if (!channels) throw new Error('Delta chunk received before metadata.');
            const sampleSize = M * 2;
            if (chunkLen % sampleSize !== 0) throw new Error(`Invalid delta chunk size ${chunkLen}.`);
            const numSamples = chunkLen / sampleSize;
            const chunkView = new DataView(uint8.buffer, payloadStart, chunkLen);
            for (let s = 0; s < numSamples; s++) {
                const sampleOffset = s * sampleSize;
                for (let j = 0; j < M; j++) {
                    const delta = chunkView.getInt16(sampleOffset + j * 2, true);
                    if (delta === -32768) {
                        currentValues[j] = NaN;
                    } else if (isNaN(currentValues[j])) {
                        currentValues[j] = delta * channelScales[j];
                    } else {
                        currentValues[j] += delta * channelScales[j];
                    }
                }
                const rec = {};
                for (let j = 0; j < M; j++) {
                    rec[channelNames[j]] = isNaN(currentValues[j]) ? null : currentValues[j];
                }
                records.push(rec);
            }
        } else if (chunkType === BRT1_CHUNK_TYPES.EOS) {
            offset += chunkLen;
            seenEos = true;
            break;
        } else {
            throw new Error(`Unknown BRT1 chunk type: 0x${chunkType.toString(16)}`);
        }

        offset += chunkLen;
    }

    if (!seenEos && offset < uint8.length) {
        throw new Error('Truncated chunk header at end of stream.');
    }

    return { metadata, channels, records };
}

/**
 * Merges telemetry points across multiple RaceBox sessions into a single
 * chronologically sorted array, deduplicating any points with identical timestamps.
 * @param {Array<Object>} sessionsList - List of RaceBox session objects
 * @returns {Array<Object>} Combined sorted points array
 */
export function mergeRaceBoxSessions(sessionsList) {
    if (!sessionsList || sessionsList.length === 0) return [];
    const sorted = [...sessionsList].sort((a, b) => {
        const tA = (a.points && a.points[0]) ? a.points[0].timestampMs : 0;
        const tB = (b.points && b.points[0]) ? b.points[0].timestampMs : 0;
        return tA - tB;
    });
    const allPoints = sorted.flatMap(s => s.points || []);
    allPoints.sort((a, b) => a.timestampMs - b.timestampMs);

    const deduped = [];
    let lastTs = null;
    for (const pt of allPoints) {
        if (pt.timestampMs !== lastTs) {
            deduped.push(pt);
            lastTs = pt.timestampMs;
        }
    }
    return deduped;
}

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

    if (distanceToRearAxle !== null && distanceToRearAxle !== undefined) {
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
 */
export class GoProVideoProcessor extends BaseTelemetryProcessor {
    static canProcess(file) {
        const ext = (file.name.split('.').pop() || '').toLowerCase();
        return ext === 'mp4' || ext === 'lrv' || Boolean(file.type && file.type.startsWith('video/'));
    }

    static async process(file, options, onProgress) {
        if (onProgress) onProgress(10, 'Inspecting GoPro video container...');
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

    // RaceBox Direct BLE Elements
    const raceboxCard = document.getElementById('raceboxCard');
    const raceboxConnectBtn = document.getElementById('raceboxConnectBtn');
    const raceboxUnsupportedNotice = document.getElementById('raceboxUnsupportedNotice');
    const raceboxControlPanel = document.getElementById('raceboxControlPanel');
    const raceboxPinToggle = document.getElementById('raceboxPinToggle');
    const raceboxPinContainer = document.getElementById('raceboxPinContainer');
    const raceboxPinInput = document.getElementById('raceboxPinInput');
    const raceboxProgressPanel = document.getElementById('raceboxProgressPanel');
    const raceboxProgressBar = document.getElementById('raceboxProgressBar');
    const raceboxStatusText = document.getElementById('raceboxStatusText');
    const raceboxProgressStats = document.getElementById('raceboxProgressStats');
    const raceboxCancelBtn = document.getElementById('raceboxCancelBtn');
    const raceboxSessionsPanel = document.getElementById('raceboxSessionsPanel');
    const raceboxSessionsCountTitle = document.getElementById('raceboxSessionsCountTitle');
    const raceboxSessionsList = document.getElementById('raceboxSessionsList');
    const raceboxSessionActions = document.getElementById('raceboxSessionActions');
    const raceboxUploadSelectedBtn = document.getElementById('raceboxUploadSelectedBtn');
    const raceboxDownloadCsvBtn = document.getElementById('raceboxDownloadCsvBtn');
    const raceboxRedoBtn = document.getElementById('raceboxRedoBtn');

    // RaceBox Axis Mapping Elements
    const raceboxOrientationPreset = document.getElementById('raceboxOrientationPreset');
    const raceboxCustomAxesToggle = document.getElementById('raceboxCustomAxesToggle');
    const raceboxCustomAxesGrid = document.getElementById('raceboxCustomAxesGrid');
    const raceboxAxisLat = document.getElementById('raceboxAxisLat');
    const raceboxAxisLon = document.getElementById('raceboxAxisLon');
    const raceboxAxisVert = document.getElementById('raceboxAxisVert');
    const raceboxAxisYaw = document.getElementById('raceboxAxisYaw');

    let selectedFile = null;
    let raceboxClient = null;
    let selectedRaceBoxSessions = [];
    let currentAxisMapping = { ...DEFAULT_AXIS_MAPPING };

    // Tab Navigation
    const tabButtons = document.querySelectorAll('.upload-tab-btn');
    const tabPanes = document.querySelectorAll('.upload-tab-pane');
    let activeTab = 'racebox';

    function switchTab(tabName) {
        activeTab = tabName;
        tabButtons.forEach(btn => {
            const isActive = btn.dataset.tab === tabName;
            btn.classList.toggle('active', isActive);
            btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });
        tabPanes.forEach(pane => {
            const isActive = pane.dataset.tabPane === tabName;
            pane.classList.toggle('active', isActive);
            pane.style.display = isActive ? 'block' : 'none';
        });

        if (tabName === 'file') {
            if (submitBtn) submitBtn.style.display = 'inline-flex';
            if (raceboxSessionActions) raceboxSessionActions.style.display = 'none';
            if (selectedFile) {
                if (CsvTelemetryProcessor.canProcess(selectedFile)) {
                    const slice = selectedFile.slice(0, 16384);
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
            } else if (gyroHint) {
                gyroHint.style.display = 'none';
            }
        } else {
            if (submitBtn) submitBtn.style.display = 'none';
            if (raceboxSessionActions) {
                raceboxSessionActions.style.display = (selectedRaceBoxSessions && selectedRaceBoxSessions.length > 0) ? 'flex' : 'none';
            }
            if (selectedRaceBoxSessions && selectedRaceBoxSessions.length > 0) {
                if (gyroHint) {
                    gyroHint.style.display = 'block';
                    gyroHint.textContent = 'ℹ RaceBox 25Hz telemetry includes gyroscope channels (Yaw, Pitch, Roll). Gyro Arm Length is configured.';
                }
            } else if (gyroHint) {
                gyroHint.style.display = 'none';
            }
        }
        hideAlert();
    }

    tabButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            switchTab(btn.dataset.tab);
        });
    });

    if (!('bluetooth' in navigator)) {
        switchTab('file');
    } else {
        switchTab('racebox');
    }

    // Restore axis mapping from localStorage if available
    try {
        const saved = localStorage.getItem('racebox_axis_mapping');
        if (saved) {
            const parsed = JSON.parse(saved);
            if (parsed && typeof parsed === 'object') {
                currentAxisMapping = { ...DEFAULT_AXIS_MAPPING, ...parsed };
            }
        }
    } catch {
        // Ignore localStorage access errors
    }

    function syncAxisMappingUI() {
        if (raceboxAxisLat) raceboxAxisLat.value = currentAxisMapping.lat;
        if (raceboxAxisLon) raceboxAxisLon.value = currentAxisMapping.lon;
        if (raceboxAxisVert) raceboxAxisVert.value = currentAxisMapping.vert;
        if (raceboxAxisYaw) raceboxAxisYaw.value = currentAxisMapping.yaw;

        let matchingPresetKey = 'custom';
        for (const [key, p] of Object.entries(AXIS_MAPPING_PRESETS)) {
            if (
                p.mapping.lat === currentAxisMapping.lat &&
                p.mapping.lon === currentAxisMapping.lon &&
                p.mapping.vert === currentAxisMapping.vert &&
                p.mapping.yaw === currentAxisMapping.yaw
            ) {
                matchingPresetKey = key;
                break;
            }
        }
        if (raceboxOrientationPreset) {
            raceboxOrientationPreset.value = matchingPresetKey;
        }
    }

    function saveAxisMapping() {
        try {
            localStorage.setItem('racebox_axis_mapping', JSON.stringify(currentAxisMapping));
        } catch {
            // Ignore localStorage error
        }
    }

    syncAxisMappingUI();

    if (raceboxCustomAxesToggle && raceboxCustomAxesGrid) {
        raceboxCustomAxesToggle.addEventListener('click', () => {
            const isHidden = raceboxCustomAxesGrid.style.display === 'none';
            raceboxCustomAxesGrid.style.display = isHidden ? 'grid' : 'none';
            raceboxCustomAxesToggle.textContent = isHidden ? 'Hide custom axes' : 'Customize axes';
        });
    }

    if (raceboxOrientationPreset) {
        raceboxOrientationPreset.addEventListener('change', () => {
            const val = raceboxOrientationPreset.value;
            if (val === 'custom') {
                if (raceboxCustomAxesGrid) raceboxCustomAxesGrid.style.display = 'grid';
                if (raceboxCustomAxesToggle) raceboxCustomAxesToggle.textContent = 'Hide custom axes';
                return;
            }
            if (AXIS_MAPPING_PRESETS[val]) {
                currentAxisMapping = {
                    ...DEFAULT_AXIS_MAPPING,
                    ...AXIS_MAPPING_PRESETS[val].mapping
                };
                syncAxisMappingUI();
                saveAxisMapping();
            }
        });
    }

    [raceboxAxisLat, raceboxAxisLon, raceboxAxisVert, raceboxAxisYaw].forEach(sel => {
        if (sel) {
            sel.addEventListener('change', () => {
                currentAxisMapping.lat = raceboxAxisLat ? raceboxAxisLat.value : currentAxisMapping.lat;
                currentAxisMapping.lon = raceboxAxisLon ? raceboxAxisLon.value : currentAxisMapping.lon;
                currentAxisMapping.vert = raceboxAxisVert ? raceboxAxisVert.value : currentAxisMapping.vert;
                currentAxisMapping.yaw = raceboxAxisYaw ? raceboxAxisYaw.value : currentAxisMapping.yaw;
                syncAxisMappingUI();
                saveAxisMapping();
            });
        }
    });

    // Check Web Bluetooth support
    if (raceboxCard) {
        if (!RaceBoxBleClient.isSupported()) {
            if (raceboxUnsupportedNotice) raceboxUnsupportedNotice.style.display = 'flex';
            if (raceboxConnectBtn) raceboxConnectBtn.disabled = true;
        } else {
            if (raceboxUnsupportedNotice) raceboxUnsupportedNotice.style.display = 'none';
        }
    }

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
        selectedRaceBoxSessions = [];
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

    function setSelectedRaceBoxSessions(sessionsList) {
        selectedRaceBoxSessions = sessionsList || [];
        selectedFile = null;
        hideAlert();

        if (selectedRaceBoxSessions.length > 0) {
            if (activeTab === 'racebox' && gyroHint) {
                gyroHint.style.display = 'block';
                gyroHint.textContent = 'ℹ RaceBox 25Hz telemetry includes gyroscope channels (Yaw, Pitch, Roll). Gyro Arm Length is configured.';
            }
            if (activeTab === 'racebox' && raceboxSessionActions) {
                raceboxSessionActions.style.display = 'flex';
            }
            if (raceboxUploadSelectedBtn) {
                const label = selectedRaceBoxSessions.length > 1
                    ? `Upload ${selectedRaceBoxSessions.length} Selected Recordings`
                    : 'Upload Selected Recording';
                const svg = raceboxUploadSelectedBtn.querySelector('svg');
                raceboxUploadSelectedBtn.innerHTML = '';
                if (svg) raceboxUploadSelectedBtn.appendChild(svg);
                raceboxUploadSelectedBtn.appendChild(document.createTextNode(' ' + label));
            }
        } else {
            if (activeTab === 'racebox' && gyroHint) gyroHint.style.display = 'none';
            if (raceboxSessionActions) raceboxSessionActions.style.display = 'none';
        }
    }

    function renderSessionsList(sessions) {
        if (!raceboxSessionsList) return;
        raceboxSessionsList.innerHTML = '';

        if (!sessions || sessions.length === 0) {
            raceboxSessionsList.innerHTML = '<div class="racebox-empty-sessions">No driving sessions found in device memory.</div>';
            return;
        }

        const bestIdx = findMatchingSessionIndex(
            sessions,
            config.sessionMeta?.date,
            config.sessionMeta?.time
        );

        const selectedIds = new Set(selectedRaceBoxSessions.map(s => s.id));
        if (selectedIds.size === 0) {
            const defaultSession = (bestIdx >= 0 && sessions[bestIdx]) ? sessions[bestIdx] : sessions[0];
            if (defaultSession) selectedIds.add(defaultSession.id);
        }

        sessions.forEach((s, idx) => {
            const isBestMatch = (idx === bestIdx);
            const isSelected = selectedIds.has(s.id);

            const item = document.createElement('div');
            item.className = `racebox-session-item ${isSelected ? 'selected' : ''}`;
            item.dataset.idx = String(idx);

            item.innerHTML = `
                <div class="racebox-session-checkbox">
                    <input type="checkbox" name="racebox_session_choice" value="${idx}" ${isSelected ? 'checked' : ''}>
                </div>
                <div class="racebox-session-details">
                    <div class="racebox-session-title-line">
                        <span class="racebox-session-time">${s.startTimeFormatted || s.startTimeIso}</span>
                        ${isBestMatch ? '<span class="badge badge-match">⭐ Recommended Match</span>' : ''}
                    </div>
                    <div class="racebox-session-meta-line">
                        <span>Duration: <strong>${s.durationFormatted}</strong></span> &bull;
                        <span>Points: <strong>${s.pointsCount.toLocaleString()}</strong> (25 Hz)</span> &bull;
                        <span>Max Speed: <strong>${s.maxSpeedKmh.toFixed(1)} km/h</strong></span>
                    </div>
                </div>
            `;

            const checkbox = item.querySelector('input[type="checkbox"]');

            const toggle = () => {
                if (selectedIds.has(s.id)) {
                    selectedIds.delete(s.id);
                    checkbox.checked = false;
                    item.classList.remove('selected');
                } else {
                    selectedIds.add(s.id);
                    checkbox.checked = true;
                    item.classList.add('selected');
                }
                const active = sessions.filter(sess => selectedIds.has(sess.id));
                setSelectedRaceBoxSessions(active);
            };

            checkbox.addEventListener('change', (e) => {
                e.stopPropagation();
                if (checkbox.checked) {
                    selectedIds.add(s.id);
                    item.classList.add('selected');
                } else {
                    selectedIds.delete(s.id);
                    item.classList.remove('selected');
                }
                const active = sessions.filter(sess => selectedIds.has(sess.id));
                setSelectedRaceBoxSessions(active);
            });

            item.addEventListener('click', (e) => {
                if (e.target !== checkbox) {
                    toggle();
                }
            });

            raceboxSessionsList.appendChild(item);
        });

        // Set default selection
        const initialActive = sessions.filter(sess => selectedIds.has(sess.id));
        setSelectedRaceBoxSessions(initialActive);
    }

    // PIN toggle
    if (raceboxPinToggle && raceboxPinContainer) {
        raceboxPinToggle.addEventListener('click', () => {
            const isHidden = raceboxPinContainer.style.display === 'none';
            raceboxPinContainer.style.display = isHidden ? 'block' : 'none';
            if (isHidden && raceboxPinInput) {
                raceboxPinInput.focus();
            }
        });
    }

    // Connect & Download from RaceBox Micro
    if (raceboxConnectBtn) {
        raceboxConnectBtn.addEventListener('click', async () => {
            hideAlert();
            if (!RaceBoxBleClient.isSupported()) {
                showAlert('Web Bluetooth is not supported in this browser. Please use Chrome on Android, Linux, macOS, or Windows.', 'error');
                return;
            }

            try {
                if (!raceboxClient) {
                    raceboxClient = new RaceBoxBleClient();
                }

                await raceboxClient.requestDevice();

                if (raceboxControlPanel) raceboxControlPanel.style.display = 'none';
                if (raceboxSessionsPanel) raceboxSessionsPanel.style.display = 'none';
                if (raceboxProgressPanel) raceboxProgressPanel.style.display = 'block';
                if (raceboxProgressBar) raceboxProgressBar.style.width = '0%';
                if (raceboxStatusText) raceboxStatusText.textContent = 'Connecting to RaceBox Micro...';
                if (raceboxProgressStats) raceboxProgressStats.textContent = '';

                const pin = (raceboxPinInput ? raceboxPinInput.value : '').trim();
                const sessions = await raceboxClient.startDownload({
                    pin,
                    onStatus: (msg) => {
                        if (raceboxStatusText) raceboxStatusText.textContent = msg;
                    },
                    onProgress: (p) => {
                        if (raceboxProgressBar && p.percent !== null) {
                            raceboxProgressBar.style.width = `${p.percent}%`;
                        }
                        if (raceboxProgressStats) {
                            const exp = p.expected > 0 ? ` / ${p.expected.toLocaleString()}` : '';
                            const pct = p.percent !== null ? ` (${p.percent}%)` : '';
                            raceboxProgressStats.textContent = `Downloaded ${p.current.toLocaleString()}${exp} records${pct}`;
                        }
                    }
                });

                if (raceboxProgressPanel) raceboxProgressPanel.style.display = 'none';

                if (!sessions || sessions.length === 0) {
                    showAlert('Download complete, but no driving sessions were found in RaceBox memory.', 'error');
                    if (raceboxControlPanel) raceboxControlPanel.style.display = 'block';
                    return;
                }

                if (raceboxSessionsCountTitle) {
                    raceboxSessionsCountTitle.textContent = `${sessions.length} session${sessions.length > 1 ? 's' : ''} downloaded from RaceBox`;
                }
                if (raceboxSessionsPanel) raceboxSessionsPanel.style.display = 'block';
                renderSessionsList(sessions);

            } catch (err) {
                if (raceboxProgressPanel) raceboxProgressPanel.style.display = 'none';
                if (raceboxControlPanel) raceboxControlPanel.style.display = 'block';
                showAlert(err.message || 'Failed to download from RaceBox.', 'error');
            }
        });
    }

    // Cancel download
    if (raceboxCancelBtn) {
        raceboxCancelBtn.addEventListener('click', async () => {
            if (raceboxClient) {
                await raceboxClient.cancel();
            }
            if (raceboxProgressPanel) raceboxProgressPanel.style.display = 'none';
            if (raceboxControlPanel) raceboxControlPanel.style.display = 'block';
        });
    }

    // Redo / Download again
    if (raceboxRedoBtn) {
        raceboxRedoBtn.addEventListener('click', () => {
            if (raceboxSessionsPanel) raceboxSessionsPanel.style.display = 'none';
            if (raceboxControlPanel) raceboxControlPanel.style.display = 'block';
        });
    }

    // Save CSV Locally
    if (raceboxDownloadCsvBtn) {
        raceboxDownloadCsvBtn.addEventListener('click', () => {
            if (!selectedRaceBoxSessions || selectedRaceBoxSessions.length === 0) {
                showAlert('Please select at least one RaceBox recording to save.', 'error');
                return;
            }

            const driverName = (driverInput ? driverInput.value : '').trim() || config.heroDriver || 'Driver';
            let armLength = null;
            if (armLengthInput && armLengthInput.value.trim() !== '') {
                armLength = parseFloat(armLengthInput.value) || null;
            }

            const allPoints = mergeRaceBoxSessions(selectedRaceBoxSessions);
            const dataText = pointsToCsvData(allPoints, currentAxisMapping);
            const payload = buildRaceToolsPayload(
                config.sessionMeta,
                driverName,
                dataText,
                armLength
            );

            const blob = new Blob([payload], { type: 'text/csv;charset=utf-8;' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            const safeTrack = (config.sessionMeta?.track || 'track').replace(/[^a-zA-Z0-9_-]/g, '_');
            const safeSession = (config.sessionMeta?.session_name || 'session').replace(/[^a-zA-Z0-9_-]/g, '_');
            a.href = url;
            a.download = `RaceBox_${safeTrack}_${safeSession}.csv`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        });
    }

    // Unified Upload Execution
    async function executeTelemetryUpload() {
        hideAlert();

        if (activeTab === 'file' && !selectedFile) {
            showAlert('Please select a telemetry CSV file to upload.', 'error');
            return;
        }

        if (activeTab === 'racebox' && (!selectedRaceBoxSessions || selectedRaceBoxSessions.length === 0)) {
            showAlert('Please select at least one RaceBox recording to upload.', 'error');
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
            if (raceboxUploadSelectedBtn) {
                raceboxUploadSelectedBtn.disabled = true;
                raceboxUploadSelectedBtn.textContent = 'Uploading...';
            }

            updateProgress(5, 'Pre-processing telemetry locally...');

            let payload;
            if (activeTab === 'racebox') {
                const countMsg = selectedRaceBoxSessions.length > 1
                    ? `Formatting ${selectedRaceBoxSessions.length} RaceBox recordings into single continuous telemetry...`
                    : 'Formatting RaceBox 25Hz telemetry into RaceTools format...';
                updateProgress(30, countMsg);
                const allPoints = mergeRaceBoxSessions(selectedRaceBoxSessions);
                const dataText = pointsToCsvData(allPoints, currentAxisMapping);
                payload = buildRaceToolsPayload(
                    config.sessionMeta,
                    driverName,
                    dataText,
                    armLength
                );
            } else {
                payload = await TelemetryProcessor.process(
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
            }

            updateProgress(75, 'Uploading telemetry to server...');

            const isBinary = payload instanceof ArrayBuffer || payload instanceof Uint8Array;
            const contentType = isBinary ? 'application/octet-stream' : 'text/csv; charset=utf-8';
            const uploadUrl = config.uploadUrl || (uploadForm ? uploadForm.action : '') || '/api/upload/stream';
            const response = await fetch(uploadUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': contentType,
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
            if (raceboxCard) raceboxCard.style.display = 'none';
            const uploadTabsNav = document.querySelector('.upload-tabs-nav');
            if (uploadTabsNav) uploadTabsNav.style.display = 'none';
            const commonFields = document.querySelector('.upload-common-fields');
            if (commonFields) commonFields.style.display = 'none';
            const divider = document.querySelector('.divider-text');
            if (divider) divider.style.display = 'none';

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
            if (raceboxUploadSelectedBtn) {
                raceboxUploadSelectedBtn.disabled = false;
                const label = selectedRaceBoxSessions.length > 1
                    ? `Upload ${selectedRaceBoxSessions.length} Selected Recordings`
                    : 'Upload Selected Recording';
                const svg = raceboxUploadSelectedBtn.querySelector('svg');
                raceboxUploadSelectedBtn.innerHTML = '';
                if (svg) raceboxUploadSelectedBtn.appendChild(svg);
                raceboxUploadSelectedBtn.appendChild(document.createTextNode(' ' + label));
            }
        }
    }

    if (raceboxUploadSelectedBtn) {
        raceboxUploadSelectedBtn.addEventListener('click', executeTelemetryUpload);
    }

    // Drag and drop / file input bindings
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
        removeFileBtn.addEventListener('click', () => {
            setSelectedFile(null);
            selectedRaceBoxSessions = [];
            if (raceboxSessionsList) {
                raceboxSessionsList.querySelectorAll('.racebox-session-item').forEach(el => el.classList.remove('selected'));
                raceboxSessionsList.querySelectorAll('input[type="checkbox"]').forEach(cb => { cb.checked = false; });
            }
            setSelectedRaceBoxSessions([]);
        });
    }

    if (uploadForm) {
        uploadForm.addEventListener('submit', (e) => {
            e.preventDefault();
            executeTelemetryUpload();
        });
    }
}
