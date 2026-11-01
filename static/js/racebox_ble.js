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
 * RaceBox Micro / Mini Bluetooth Low Energy client and protocol parser.
 *
 * Implements Nordic UART Service (NUS) communication, UBX framing,
 * Fletcher-8 checksum validation, 80-byte 25Hz telemetry record decoding,
 * session splitting, and RaceTools CSV generation for direct in-browser upload.
 */

// Nordic UART Service UUIDs
export const NUS_SERVICE_UUID = '6e400001-b5a3-f393-e0a9-e50e24dcca9e';
export const NUS_RX_UUID = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'; // Host writes commands
export const NUS_TX_UUID = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'; // Device notifies data
export const DEVICE_INFO_SERVICE_UUID = '0000180a-0000-1000-8000-00805f9b34fb';

// Framing & Message IDs (UBX-style)
export const SYNC_1 = 0xB5;
export const SYNC_2 = 0x62;

export const CLASS_CUSTOM = 0xFF;
export const ID_LIVE = 0x01;
export const ID_ACK = 0x02;
export const ID_NACK = 0x03;
export const ID_HISTORY = 0x21;
export const ID_STATUS = 0x22;
export const ID_DOWNLOAD = 0x23;
export const ID_STATE_CHANGE = 0x26;
export const ID_UNLOCK = 0x30;

/**
 * Compute Fletcher-8 checksum (UBX style) over byte buffer.
 * @param {Uint8Array|Array<number>} data
 * @param {number} [offset=0]
 * @param {number} [length=data.length]
 * @returns {[number, number]} [ckA, ckB]
 */
export function computeFletcherChecksum(data, offset = 0, length = data.length) {
    let ckA = 0;
    let ckB = 0;
    const end = offset + length;
    for (let i = offset; i < end; i++) {
        ckA = (ckA + (data[i] & 0xFF)) & 0xFF;
        ckB = (ckB + ckA) & 0xFF;
    }
    return [ckA, ckB];
}

/**
 * Encode a message into UBX wire format:
 * [SYNC_1, SYNC_2, CLASS, ID, LEN_L, LEN_H, ...PAYLOAD, CK_A, CK_B]
 * @param {number} msgClass
 * @param {number} msgId
 * @param {Uint8Array|Array<number>} [payload=new Uint8Array(0)]
 * @returns {Uint8Array} Encoded packet
 */
export function encodeMessage(msgClass, msgId, payload = new Uint8Array(0)) {
    const pBytes = payload instanceof Uint8Array ? payload : new Uint8Array(payload);
    const len = pBytes.length;
    const out = new Uint8Array(6 + len + 2);

    out[0] = SYNC_1;
    out[1] = SYNC_2;
    out[2] = msgClass & 0xFF;
    out[3] = msgId & 0xFF;
    out[4] = len & 0xFF;
    out[5] = (len >> 8) & 0xFF;
    out.set(pBytes, 6);

    const ckData = new Uint8Array(4 + len);
    ckData[0] = out[2];
    ckData[1] = out[3];
    ckData[2] = out[4];
    ckData[3] = out[5];
    ckData.set(pBytes, 4);

    const [ckA, ckB] = computeFletcherChecksum(ckData);
    out[6 + len] = ckA;
    out[6 + len + 1] = ckB;

    return out;
}

/**
 * Stream parser for RaceBox UBX packets.
 * Buffers arbitrary byte chunks, finds sync headers, verifies checksums,
 * and yields complete valid packets.
 */
export class RaceBoxStreamParser {
    constructor() {
        this.buffer = [];
    }

    /**
     * Feed byte chunks into the stream parser.
     * @param {Uint8Array|ArrayBuffer|Array<number>} chunk
     * @returns {Array<{ msgClass: number, msgId: number, payload: Uint8Array }>} List of extracted packets
     */
    feed(chunk) {
        let uint8;
        if (chunk instanceof Uint8Array) {
            uint8 = chunk;
        } else if (chunk && chunk.buffer instanceof ArrayBuffer) {
            uint8 = new Uint8Array(chunk.buffer, chunk.byteOffset || 0, chunk.byteLength);
        } else if (Array.isArray(chunk)) {
            uint8 = new Uint8Array(chunk);
        } else {
            return [];
        }

        for (let i = 0; i < uint8.length; i++) {
            this.buffer.push(uint8[i]);
        }

        const packets = [];

        while (this.buffer.length >= 8) {
            // Find 0xB5 0x62 sync header
            if (this.buffer[0] !== SYNC_1 || this.buffer[1] !== SYNC_2) {
                this.buffer.shift();
                continue;
            }

            const msgClass = this.buffer[2];
            const msgId = this.buffer[3];
            const len = this.buffer[4] | (this.buffer[5] << 8);

            // Sanity check length to avoid memory buffer exhaustion on corruption
            if (len > 1024) {
                this.buffer.shift();
                continue;
            }

            const totalExpected = 6 + len + 2;
            if (this.buffer.length < totalExpected) {
                // Incomplete packet in buffer, wait for more data
                break;
            }

            // Verify Fletcher-8 checksum
            const ckA = this.buffer[6 + len];
            const ckB = this.buffer[6 + len + 1];

            let calcA = 0;
            let calcB = 0;
            for (let i = 2; i < 6 + len; i++) {
                calcA = (calcA + this.buffer[i]) & 0xFF;
                calcB = (calcB + calcA) & 0xFF;
            }

            if (calcA === ckA && calcB === ckB) {
                const payload = new Uint8Array(this.buffer.slice(6, 6 + len));
                packets.push({ msgClass, msgId, payload });
                this.buffer.splice(0, totalExpected);
            } else {
                // Checksum failed, discard the first sync byte and resume scanning
                this.buffer.shift();
            }
        }

        return packets;
    }

    reset() {
        this.buffer = [];
    }
}

/**
 * Decode 80-byte record payload into telemetry data point.
 * @param {Uint8Array} payload
 * @returns {Object|null} Decoded point or null if payload is invalid
 */
export function decodeRecord(payload) {
    const u8 = payload instanceof Uint8Array ? payload : new Uint8Array(payload.buffer || payload);
    if (u8.length < 80) return null;

    const view = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);

    const iTow = view.getUint32(0, true);
    const year = view.getUint16(4, true);
    const month = view.getUint8(6);
    const day = view.getUint8(7);
    const hour = view.getUint8(8);
    const minute = view.getUint8(9);
    const second = view.getUint8(10);
    const validFlags = view.getUint8(11);
    const timeAcc = view.getUint32(12, true);
    const nano = view.getInt32(16, true);
    const fixStatus = view.getUint8(20);
    const fixFlags = view.getUint8(21);
    const dateTimeFlags = view.getUint8(22);
    const numSvs = view.getUint8(23);

    const lonRaw = view.getInt32(24, true);
    const latRaw = view.getInt32(28, true);
    const wgsAltMm = view.getInt32(32, true);
    const mslAltMm = view.getInt32(36, true);
    const hAccMm = view.getUint32(40, true);
    const vAccMm = view.getUint32(44, true);
    const speedMmS = view.getInt32(48, true);
    const headingRaw = view.getInt32(52, true);
    const sAccMmS = view.getUint32(56, true);
    const headAcc = view.getUint32(60, true);
    const pdop = view.getUint16(64, true) * 0.01;
    const latLonFlags = view.getUint8(66);
    const batteryVolts = view.getUint8(67) * 0.1;

    // G-Forces: milli-g to g
    const gForceX = view.getInt16(68, true) / 1000.0;
    const gForceY = view.getInt16(70, true) / 1000.0;
    const gForceZ = view.getInt16(72, true) / 1000.0;

    // Rotation rates: centi-deg/s to deg/s
    const rollRateDegS = view.getInt16(74, true) / 100.0;
    const pitchRateDegS = view.getInt16(76, true) / 100.0;
    const yawRateDegS = view.getInt16(78, true) / 100.0;

    let dt = null;
    let timestampMs = iTow;
    if (year >= 2020 && year <= 2040 && month >= 1 && month <= 12 && day >= 1 && day <= 31) {
        const ms = Math.floor(Math.max(0, nano) / 1000000);
        dt = new Date(Date.UTC(year, month - 1, day, hour, minute, second, ms));
        timestampMs = dt.getTime();
    }

    return {
        timestampMs,
        dateTime: dt,
        latitude: latRaw * 1e-7,
        longitude: lonRaw * 1e-7,
        altitudeM: mslAltMm / 1000.0,
        speedMs: Math.max(0, speedMmS) / 1000.0,
        headingDeg: headingRaw * 1e-5,
        gForceX,
        gForceY,
        gForceZ,
        gForceLat: gForceX,
        gForceLon: gForceY,
        gForceVert: gForceZ,
        yawRateDegS,
        pitchRateDegS,
        rollRateDegS,
        batteryVolts,
        fixStatus,
        numSvs,
        pdop,
        validFlags,
        timeAcc,
        hAccMm,
        vAccMm,
        sAccMmS,
        headAcc,
        wgsAltMm,
        fixFlags,
        dateTimeFlags,
        latLonFlags
    };
}

/**
 * Formats duration in seconds into a friendly string (e.g. "12m 45s").
 * @param {number} totalSeconds
 * @returns {string}
 */
export function formatDuration(totalSeconds) {
    if (!totalSeconds || isNaN(totalSeconds) || totalSeconds < 0) return '0s';
    const s = Math.round(totalSeconds);
    const hours = Math.floor(s / 3600);
    const mins = Math.floor((s % 3600) / 60);
    const secs = s % 60;
    if (hours > 0) {
        return `${hours}h ${String(mins).padStart(2, '0')}m ${String(secs).padStart(2, '0')}s`;
    }
    if (mins > 0) {
        return `${mins}m ${String(secs).padStart(2, '0')}s`;
    }
    return `${secs}s`;
}

/**
 * Splits a list of packets into distinct driving sessions based on state changes (0x26)
 * or time gaps (> 30s).
 * @param {Array<{ msgClass: number, msgId: number, payload: Uint8Array }>} packets
 * @returns {Array<Object>} List of sessions
 */
export function splitIntoSessions(packets) {
    const sessions = [];
    let currentPoints = [];

    const flushCurrent = () => {
        if (currentPoints.length >= 50) { // Discard tiny snippets (< 2s at 25Hz)
            const firstPt = currentPoints[0];
            const lastPt = currentPoints[currentPoints.length - 1];
            const durationSeconds = Math.max(0, (lastPt.timestampMs - firstPt.timestampMs) / 1000.0);
            let maxSpeedMs = 0;
            for (let i = 0; i < currentPoints.length; i++) {
                if (currentPoints[i].speedMs > maxSpeedMs) {
                    maxSpeedMs = currentPoints[i].speedMs;
                }
            }

            const startTime = firstPt.dateTime || new Date(firstPt.timestampMs);
            sessions.push({
                id: sessions.length + 1,
                startTime,
                startTimeIso: startTime.toISOString(),
                startTimeFormatted: startTime.toUTCString().replace('GMT', 'UTC'),
                durationSeconds,
                durationFormatted: formatDuration(durationSeconds),
                pointsCount: currentPoints.length,
                maxSpeedMs,
                maxSpeedKmh: maxSpeedMs * 3.6,
                points: currentPoints
            });
        }
        currentPoints = [];
    };

    for (let i = 0; i < packets.length; i++) {
        const pkt = packets[i];
        if (pkt.msgClass === CLASS_CUSTOM && pkt.msgId === ID_STATE_CHANGE) {
            flushCurrent();
        } else if (pkt.msgClass === CLASS_CUSTOM && (pkt.msgId === ID_HISTORY || pkt.msgId === ID_LIVE)) {
            const pt = decodeRecord(pkt.payload);
            if (!pt) continue;

            if (currentPoints.length > 0) {
                const gapMs = pt.timestampMs - currentPoints[currentPoints.length - 1].timestampMs;
                if (gapMs > 30000) { // Gap > 30 sec: new session
                    flushCurrent();
                }
            }
            currentPoints.push(pt);
        }
    }

    flushCurrent();
    return sessions;
}

export const DEFAULT_AXIS_MAPPING = {
    lat: '+x',
    lon: '+y',
    vert: '+z',
    yaw: '+z',
    pitch: '+y',
    roll: '+x'
};

export const AXIS_MAPPING_PRESETS = {
    default: {
        label: 'Default: Horizontal (Facing Forward)',
        mapping: { lat: '+x', lon: '+y', vert: '+z', yaw: '+z' }
    },
    cw90: {
        label: 'Sideways: 90° Clockwise (Facing Right)',
        mapping: { lat: '-y', lon: '+x', vert: '+z', yaw: '+z' }
    },
    ccw90: {
        label: 'Sideways: 90° Counter-Clockwise (Facing Left)',
        mapping: { lat: '+y', lon: '-x', vert: '+z', yaw: '+z' }
    },
    swap_xy: {
        label: 'Sideways: Swap Lat & Lon (Lat = +Y, Lon = +X)',
        mapping: { lat: '+y', lon: '+x', vert: '+z', yaw: '+z' }
    },
    swap_xy_inv: {
        label: 'Sideways: Swap Lat & Lon (Lat = -Y, Lon = +X)',
        mapping: { lat: '-y', lon: '+x', vert: '+z', yaw: '+z' }
    },
    vertical_right: {
        label: 'Vertical: Mounted on Right Sidepod (Facing Forward)',
        mapping: { lat: '+z', lon: '+y', vert: '-x', yaw: '+x' }
    },
    vertical_left: {
        label: 'Vertical: Mounted on Left Sidepod (Facing Forward)',
        mapping: { lat: '-z', lon: '+y', vert: '+x', yaw: '-x' }
    },
    backward: {
        label: 'Horizontal (Facing Backward)',
        mapping: { lat: '-x', lon: '-y', vert: '+z', yaw: '+z' }
    }
};

/**
 * Resolves a signed 3-axis channel from an axes dictionary { x, y, z }.
 * @param {{ x: number, y: number, z: number }} axes
 * @param {string} axisKey - e.g. "+x", "-x", "+y", "-y", "+z", "-z"
 * @returns {number}
 */
export function resolveAxisValue(axes, axisKey) {
    if (!axisKey || typeof axisKey !== 'string') return 0;
    const clean = axisKey.trim().toLowerCase();
    const sign = clean.startsWith('-') ? -1 : 1;
    const axis = clean.replace(/^[+-]/, '');
    const val = (axes && typeof axes[axis] === 'number') ? axes[axis] : 0;
    return sign * val;
}

/**
 * Converts telemetry points of a session into CSV data block rows.
 * Uses the standardized RaceTools convention: GForceLat, GForceLon, GForceVert.
 * Supports configurable IMU mounting axis mapping (e.g. for sideways mounting).
 *
 * @param {Array<Object>} points
 * @param {Object} [axisMapping=DEFAULT_AXIS_MAPPING]
 * @returns {string} Tabular CSV text (header row + rows)
 */
export function pointsToCsvData(points, axisMapping = DEFAULT_AXIS_MAPPING) {
    if (!points || points.length === 0) return '';

    const mapping = { ...DEFAULT_AXIS_MAPPING, ...axisMapping };
    const lines = [];
    lines.push('Record,Time,Latitude,Longitude,Altitude,Speed,GForceLat,GForceLon,GForceVert,Yaw Rate,Pitch Rate,Roll Rate,Lap,Lap Distance');

    const startTimeMs = points[0].timestampMs;

    for (let i = 0; i < points.length; i++) {
        const p = points[i];
        let timeStr;
        if (p.dateTime) {
            timeStr = p.dateTime.toISOString();
        } else {
            const relSec = Math.max(0, (p.timestampMs - startTimeMs) / 1000.0);
            timeStr = relSec.toFixed(3);
        }

        const accelAxes = {
            x: p.gForceX || 0,
            y: p.gForceY || 0,
            z: p.gForceZ || 0
        };

        const gyroAxes = {
            x: p.rollRateDegS || 0,
            y: p.pitchRateDegS || 0,
            z: p.yawRateDegS || 0
        };

        const lat = p.latitude.toFixed(7);
        const lon = p.longitude.toFixed(7);
        const alt = p.altitudeM.toFixed(2);
        const spd = p.speedMs.toFixed(3);

        const glat = resolveAxisValue(accelAxes, mapping.lat).toFixed(3);
        const glon = resolveAxisValue(accelAxes, mapping.lon).toFixed(3);
        const gvert = resolveAxisValue(accelAxes, mapping.vert).toFixed(3);

        const yr = resolveAxisValue(gyroAxes, mapping.yaw).toFixed(3);
        const pr = resolveAxisValue(gyroAxes, mapping.pitch).toFixed(3);
        const rr = resolveAxisValue(gyroAxes, mapping.roll).toFixed(3);

        lines.push(`${i + 1},${timeStr},${lat},${lon},${alt},${spd},${glat},${glon},${gvert},${yr},${pr},${rr},1,0.00`);
    }

    return lines.join('\n');
}

/**
 * Finds the index of the session that best matches the target session date & time.
 * @param {Array<Object>} sessions
 * @param {string|null} targetDateStr - e.g. "2026-09-12"
 * @param {string|null} targetTimeStr - e.g. "11:02" or "11:02:00"
 * @returns {number} Index of best matching session, or -1 if sessions is empty
 */
export function findMatchingSessionIndex(sessions, targetDateStr, targetTimeStr) {
    if (!sessions || sessions.length === 0) return -1;
    if (sessions.length === 1) return 0;
    if (!targetDateStr) return 0;

    let targetMs;
    try {
        const timeClean = (targetTimeStr || '00:00').trim().slice(0, 5);
        const [h, m] = timeClean.split(':').map(Number);
        const [year, month, day] = targetDateStr.split('-').map(Number);
        targetMs = Date.UTC(year, month - 1, day, h || 0, m || 0);
    } catch {
        return 0;
    }

    let bestIdx = 0;
    let minDiff = Infinity;

    for (let i = 0; i < sessions.length; i++) {
        const sTime = sessions[i].startTime;
        if (!sTime) continue;
        const diff = Math.abs(sTime.getTime() - targetMs);
        if (diff < minDiff) {
            minDiff = diff;
            bestIdx = i;
        }
    }

    return bestIdx;
}

/**
 * RaceBox Web Bluetooth client.
 */
export class RaceBoxBleClient {
    constructor() {
        this.device = null;
        this.server = null;
        this.rxChar = null;
        this.txChar = null;
        this.wakeLock = null;
        this.parser = new RaceBoxStreamParser();
        this.receivedPackets = [];
        this.expectedRecords = 0;
        this.isDownloading = false;
        this.onProgress = null;
        this.onStatus = null;
        this._resolveDownload = null;
        this._rejectDownload = null;
        this._onCharChangedBound = this._handleNotification.bind(this);
        this._onDisconnectedBound = this._handleDisconnect.bind(this);
    }

    /**
     * Checks if Web Bluetooth is supported in the current environment.
     * @returns {boolean}
     */
    static isSupported() {
        return typeof navigator !== 'undefined' && Boolean(navigator.bluetooth);
    }

    /**
     * Prompt browser device picker for RaceBox Micro / Mini devices.
     * Must be called in direct response to a user click gesture.
     */
    async requestDevice() {
        if (!RaceBoxBleClient.isSupported()) {
            throw new Error('Web Bluetooth is not supported in this browser. Please use Chrome or Edge on Android, Windows, Mac, or Linux.');
        }

        this.device = await navigator.bluetooth.requestDevice({
            filters: [
                { namePrefix: 'RaceBox' },
                { namePrefix: 'Racebox' },
                { namePrefix: 'Micro' },
                { namePrefix: 'micro' }
            ],
            optionalServices: [NUS_SERVICE_UUID, DEVICE_INFO_SERVICE_UUID]
        });

        this.device.addEventListener('gattserverdisconnected', this._onDisconnectedBound);
        return this.device;
    }

    /**
     * Connect to GATT server and discover Nordic UART Service.
     */
    async connect() {
        if (!this.device) {
            throw new Error('No device selected. Please select a RaceBox device.');
        }

        if (this.onStatus) this.onStatus(`Connecting to ${this.device.name || 'RaceBox'}...`);
        this.server = await this.device.gatt.connect();

        if (this.onStatus) this.onStatus('Discovering Nordic UART Service...');
        const service = await this.server.getPrimaryService(NUS_SERVICE_UUID);

        this.rxChar = await service.getCharacteristic(NUS_RX_UUID);
        this.txChar = await service.getCharacteristic(NUS_TX_UUID);

        if (this.onStatus) this.onStatus('Connected to RaceBox Micro.');
        return this.server;
    }

    /**
     * Starts standalone recording memory download over BLE.
     * @param {Object} [options]
     * @param {string} [options.pin] - Optional device memory unlock PIN
     * @param {Function} [options.onProgress] - Callback (progress: { percent, current, expected })
     * @param {Function} [options.onStatus] - Callback (statusMessage: string)
     * @returns {Promise<Array<Object>>} Resolves with list of decoded sessions
     */
    async startDownload(options = {}) {
        if (!this.server || !this.server.connected || !this.rxChar || !this.txChar) {
            await this.connect();
        }

        this.onProgress = options.onProgress || null;
        this.onStatus = options.onStatus || null;
        this.parser.reset();
        this.receivedPackets = [];
        this.expectedRecords = 0;
        this.isDownloading = true;

        // Acquire screen wakeLock on Android / Chrome to avoid sleep throttling
        await this._acquireWakeLock();

        return new Promise((resolve, reject) => {
            this._resolveDownload = resolve;
            this._rejectDownload = reject;

            (async () => {
                try {
                    // Subscribe to TX notifications
                    if (this.onStatus) this.onStatus('Subscribing to notifications...');
                    await this.txChar.startNotifications();
                    this.txChar.addEventListener('characteristicvaluechanged', this._onCharChangedBound);

                    // Optional PIN unlock
                    if (options.pin && options.pin.trim()) {
                        if (this.onStatus) this.onStatus('Unlocking memory with PIN...');
                        const pinBytes = new TextEncoder().encode(options.pin.trim());
                        const unlockCmd = encodeMessage(CLASS_CUSTOM, ID_UNLOCK, pinBytes);
                        await this._writeCommand(unlockCmd);
                    }

                    // Send Download Start command: FF 23 with empty payload
                    if (this.onStatus) this.onStatus('Requesting session history...');
                    const dlCmd = encodeMessage(CLASS_CUSTOM, ID_DOWNLOAD);
                    await this._writeCommand(dlCmd);
                } catch (err) {
                    this._cleanup();
                    reject(err);
                }
            })();
        });
    }

    /**
     * Cancel ongoing download.
     */
    async cancel() {
        if (!this.isDownloading) return;
        try {
            if (this.rxChar && this.server && this.server.connected) {
                // 1-byte payload marks cancel
                const cancelCmd = encodeMessage(CLASS_CUSTOM, ID_DOWNLOAD, new Uint8Array([0x01]));
                await this._writeCommand(cancelCmd).catch(() => {});
            }
        } finally {
            const reject = this._rejectDownload;
            this._cleanup();
            if (reject) {
                reject(new Error('Download cancelled by user.'));
            }
        }
    }

    /**
     * Disconnect GATT connection.
     */
    disconnect() {
        this._cleanup();
        if (this.device && this.device.gatt && this.device.gatt.connected) {
            this.device.gatt.disconnect();
        }
        this.device = null;
        this.server = null;
        this.rxChar = null;
        this.txChar = null;
    }

    async _writeCommand(data) {
        if (!this.rxChar) return;
        if (typeof this.rxChar.writeValueWithResponse === 'function') {
            await this.rxChar.writeValueWithResponse(data);
        } else {
            await this.rxChar.writeValue(data);
        }
    }

    _handleNotification(event) {
        if (!this.isDownloading) return;
        const value = event.target.value;
        const newPackets = this.parser.feed(value);

        for (let i = 0; i < newPackets.length; i++) {
            const pkt = newPackets[i];
            this.receivedPackets.push(pkt);

            if (pkt.msgClass === CLASS_CUSTOM && pkt.msgId === ID_DOWNLOAD) {
                // Expected count in 4-byte LE payload
                if (pkt.payload.length >= 4) {
                    this.expectedRecords = pkt.payload[0] |
                        (pkt.payload[1] << 8) |
                        (pkt.payload[2] << 16) |
                        (pkt.payload[3] << 24);
                }
            } else if (pkt.msgClass === CLASS_CUSTOM && pkt.msgId === ID_ACK) {
                // Download complete!
                this._finishDownload();
                return;
            } else if (pkt.msgClass === CLASS_CUSTOM && pkt.msgId === ID_NACK) {
                const reject = this._rejectDownload;
                this._cleanup();
                if (reject) {
                    reject(new Error('RaceBox returned NACK (command rejected or memory locked).'));
                }
                return;
            }
        }

        if (this.onProgress) {
            const currentRecords = this.receivedPackets.filter(
                p => p.msgId === ID_HISTORY || p.msgId === ID_LIVE
            ).length;

            const expected = this.expectedRecords;
            const percent = expected > 0 ? Math.min(100, Math.round((currentRecords / expected) * 100)) : null;
            this.onProgress({
                current: currentRecords,
                expected,
                percent
            });
        }
    }

    _finishDownload() {
        const resolve = this._resolveDownload;
        const packets = this.receivedPackets;
        this._cleanup();

        if (this.onStatus) this.onStatus('Download finished. Processing sessions...');
        const sessions = splitIntoSessions(packets);

        if (resolve) {
            resolve(sessions);
        }
    }

    _handleDisconnect() {
        if (this.isDownloading) {
            const reject = this._rejectDownload;
            const resolve = this._resolveDownload;
            const packets = this.receivedPackets;
            this._cleanup();

            // If we have downloaded valid records, salvage them
            const sessions = splitIntoSessions(packets);
            if (sessions.length > 0 && resolve) {
                resolve(sessions);
                return;
            }

            if (reject) {
                reject(new Error('RaceBox disconnected before download could complete.'));
            }
        }
    }

    async _acquireWakeLock() {
        try {
            if (typeof navigator !== 'undefined' && 'wakeLock' in navigator) {
                this.wakeLock = await navigator.wakeLock.request('screen');
                this.wakeLock.addEventListener('release', () => {
                    this.wakeLock = null;
                });
            }
        } catch {
            // Wake lock is non-essential; continue without it
            this.wakeLock = null;
        }
    }

    _releaseWakeLock() {
        if (this.wakeLock) {
            try {
                this.wakeLock.release();
            } catch {
                // Ignore wake lock release errors
            }
            this.wakeLock = null;
        }
    }

    _cleanup() {
        this.isDownloading = false;
        this._releaseWakeLock();
        if (this.txChar) {
            try {
                this.txChar.removeEventListener('characteristicvaluechanged', this._onCharChangedBound);
                this.txChar.stopNotifications().catch(() => {});
            } catch {
                // Ignore notification listener detachment errors
            }
        }
        this._resolveDownload = null;
        this._rejectDownload = null;
    }
}
