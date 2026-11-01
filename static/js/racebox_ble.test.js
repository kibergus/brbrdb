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

import { describe, it, expect } from 'vitest';
import {
    computeFletcherChecksum,
    encodeMessage,
    RaceBoxStreamParser,
    decodeRecord,
    splitIntoSessions,
    pointsToCsvData,
    findMatchingSessionIndex,
    formatDuration,
    CLASS_CUSTOM,
    ID_DOWNLOAD,
    ID_HISTORY,
    ID_STATE_CHANGE
} from './racebox_ble.js';

describe('racebox_ble.js', () => {
    describe('computeFletcherChecksum & encodeMessage', () => {
        it('matches published RaceBox UBX example (FF 23 00 00 -> 0x22, 0x65)', () => {
            const ckData = new Uint8Array([0xFF, 0x23, 0x00, 0x00]);
            const [ckA, ckB] = computeFletcherChecksum(ckData);
            expect(ckA).toBe(0x22);
            expect(ckB).toBe(0x65);

            const wire = encodeMessage(CLASS_CUSTOM, ID_DOWNLOAD);
            expect(wire.length).toBe(8);
            expect(wire[0]).toBe(0xB5);
            expect(wire[1]).toBe(0x62);
            expect(wire[2]).toBe(0xFF);
            expect(wire[3]).toBe(0x23);
            expect(wire[4]).toBe(0x00);
            expect(wire[5]).toBe(0x00);
            expect(wire[6]).toBe(0x22);
            expect(wire[7]).toBe(0x65);
        });

        it('correctly encodes messages with non-empty payload', () => {
            const payload = new Uint8Array([0x01, 0x02, 0x03]);
            const wire = encodeMessage(0xFF, 0x02, payload);
            expect(wire.length).toBe(6 + 3 + 2);
            expect(wire[0]).toBe(0xB5);
            expect(wire[1]).toBe(0x62);
            expect(wire[2]).toBe(0xFF);
            expect(wire[3]).toBe(0x02);
            expect(wire[4]).toBe(3); // len lo
            expect(wire[5]).toBe(0); // len hi
            expect(wire[6]).toBe(1);
            expect(wire[7]).toBe(2);
            expect(wire[8]).toBe(3);

            const [expectedA, expectedB] = computeFletcherChecksum(new Uint8Array([0xFF, 0x02, 0x03, 0x00, 1, 2, 3]));
            expect(wire[9]).toBe(expectedA);
            expect(wire[10]).toBe(expectedB);
        });
    });

    describe('RaceBoxStreamParser', () => {
        it('parses a single packet delivered in one chunk', () => {
            const parser = new RaceBoxStreamParser();
            const wire = encodeMessage(0xFF, 0x02, new Uint8Array([1, 2, 3]));
            const pkts = parser.feed(wire);
            expect(pkts.length).toBe(1);
            expect(pkts[0].msgClass).toBe(0xFF);
            expect(pkts[0].msgId).toBe(0x02);
            expect(Array.from(pkts[0].payload)).toEqual([1, 2, 3]);
        });

        it('handles packet split across multiple chunks', () => {
            const parser = new RaceBoxStreamParser();
            const wire = encodeMessage(0xFF, 0x02, new Uint8Array([1, 2, 3, 4]));

            const chunk1 = wire.slice(0, 4);
            const chunk2 = wire.slice(4);

            expect(parser.feed(chunk1)).toEqual([]);
            const pkts = parser.feed(chunk2);
            expect(pkts.length).toBe(1);
            expect(pkts[0].msgId).toBe(0x02);
            expect(Array.from(pkts[0].payload)).toEqual([1, 2, 3, 4]);
        });

        it('handles multiple packets in a single chunk', () => {
            const parser = new RaceBoxStreamParser();
            const pkt1 = encodeMessage(0xFF, 0x02, new Uint8Array([1]));
            const pkt2 = encodeMessage(0xFF, 0x03, new Uint8Array([2, 3]));

            const combined = new Uint8Array(pkt1.length + pkt2.length);
            combined.set(pkt1, 0);
            combined.set(pkt2, pkt1.length);

            const result = parser.feed(combined);
            expect(result.length).toBe(2);
            expect(result[0].msgId).toBe(0x02);
            expect(result[1].msgId).toBe(0x03);
        });

        it('skips garbage bytes preceding valid packet', () => {
            const parser = new RaceBoxStreamParser();
            const wire = encodeMessage(0xFF, 0x02, new Uint8Array([42]));
            const withGarbage = new Uint8Array([0x12, 0x34, 0xB5, 0x00, ...wire]);

            const result = parser.feed(withGarbage);
            expect(result.length).toBe(1);
            expect(result[0].msgId).toBe(0x02);
            expect(Array.from(result[0].payload)).toEqual([42]);
        });

        it('discards packet with corrupted checksum and continues parsing', () => {
            const parser = new RaceBoxStreamParser();
            const badWire = encodeMessage(0xFF, 0x02, new Uint8Array([1, 2]));
            badWire[badWire.length - 1] ^= 0xFF; // Corrupt checksum

            const goodWire = encodeMessage(0xFF, 0x02, new Uint8Array([9, 9]));
            const stream = new Uint8Array([...badWire, ...goodWire]);

            const result = parser.feed(stream);
            expect(result.length).toBe(1);
            expect(Array.from(result[0].payload)).toEqual([9, 9]);
        });
    });

    describe('decodeRecord', () => {
        function createSampleRecordPayload(overrides = {}) {
            const buffer = new ArrayBuffer(80);
            const view = new DataView(buffer);

            view.setUint32(0, overrides.iTow ?? 12345678, true);
            view.setUint16(4, overrides.year ?? 2026, true);
            view.setUint8(6, overrides.month ?? 9);
            view.setUint8(7, overrides.day ?? 12);
            view.setUint8(8, overrides.hour ?? 11);
            view.setUint8(9, overrides.minute ?? 2);
            view.setUint8(10, overrides.second ?? 15);
            view.setUint8(11, overrides.validFlags ?? 0);
            view.setUint32(12, overrides.timeAcc ?? 100, true);
            view.setInt32(16, overrides.nano ?? 500000000, true); // 500 ms
            view.setUint8(20, overrides.fixStatus ?? 3); // 3D
            view.setUint8(21, 0);
            view.setUint8(22, 0);
            view.setUint8(23, overrides.numSvs ?? 16);

            view.setInt32(24, overrides.lonRaw ?? -1234567, true); // -0.1234567 deg
            view.setInt32(28, overrides.latRaw ?? 515000000, true); // 51.5000000 deg
            view.setInt32(32, 50000, true); // WGS alt
            view.setInt32(36, overrides.mslAltMm ?? 45000, true); // 45.0 m
            view.setUint32(40, 1000, true); // hAcc
            view.setUint32(44, 1500, true); // vAcc
            view.setInt32(48, overrides.speedMmS ?? 25000, true); // 25.0 m/s
            view.setInt32(52, overrides.headingRaw ?? 18000000, true); // 180.0 deg
            view.setUint32(56, 100, true);
            view.setUint32(60, 100, true);
            view.setUint16(64, 120, true); // PDOP 1.20
            view.setUint8(66, 0);
            view.setUint8(67, 41); // 4.1 V

            view.setInt16(68, overrides.gForceX ?? 150, true); // 0.15 g
            view.setInt16(70, overrides.gForceY ?? -250, true); // -0.25 g
            view.setInt16(72, overrides.gForceZ ?? 980, true); // 0.98 g

            view.setInt16(74, overrides.rotX ?? 500, true); // 5.0 deg/s roll
            view.setInt16(76, overrides.rotY ?? 1000, true); // 10.0 deg/s pitch
            view.setInt16(78, overrides.rotZ ?? -1500, true); // -15.0 deg/s yaw

            return new Uint8Array(buffer);
        }

        it('decodes 80-byte record into expected telemetry point', () => {
            const payload = createSampleRecordPayload();
            const pt = decodeRecord(payload);

            expect(pt).not.toBeNull();
            expect(pt.latitude).toBeCloseTo(51.5, 5);
            expect(pt.longitude).toBeCloseTo(-0.1234567, 6);
            expect(pt.altitudeM).toBeCloseTo(45.0, 2);
            expect(pt.speedMs).toBeCloseTo(25.0, 2);
            expect(pt.headingDeg).toBeCloseTo(180.0, 2);
            expect(pt.gForceX).toBeCloseTo(0.15, 3);
            expect(pt.gForceY).toBeCloseTo(-0.25, 3);
            expect(pt.gForceZ).toBeCloseTo(0.98, 3);
            expect(pt.gForceLat).toBeCloseTo(0.15, 3);
            expect(pt.gForceLon).toBeCloseTo(-0.25, 3);
            expect(pt.gForceVert).toBeCloseTo(0.98, 3);
            expect(pt.rollRateDegS).toBeCloseTo(5.0, 2);
            expect(pt.pitchRateDegS).toBeCloseTo(10.0, 2);
            expect(pt.yawRateDegS).toBeCloseTo(-15.0, 2);
            expect(pt.batteryVolts).toBeCloseTo(4.1, 1);
            expect(pt.numSvs).toBe(16);

            expect(pt.dateTime).not.toBeNull();
            expect(pt.dateTime.getUTCFullYear()).toBe(2026);
            expect(pt.dateTime.getUTCMonth()).toBe(8); // September (0-indexed)
            expect(pt.dateTime.getUTCDate()).toBe(12);
            expect(pt.dateTime.getUTCHours()).toBe(11);
            expect(pt.dateTime.getUTCMinutes()).toBe(2);
            expect(pt.dateTime.getUTCSeconds()).toBe(15);
            expect(pt.dateTime.getUTCMilliseconds()).toBe(500);
        });

        it('returns null for short payload', () => {
            expect(decodeRecord(new Uint8Array(79))).toBeNull();
        });
    });

    describe('splitIntoSessions', () => {
        function makeHistoryPackets(count, startEpochMs, stepMs = 40) {
            const packets = [];
            for (let i = 0; i < count; i++) {
                const epochMs = startEpochMs + i * stepMs;
                const d = new Date(epochMs);
                const buffer = new ArrayBuffer(80);
                const view = new DataView(buffer);
                view.setUint32(0, epochMs % 1000000, true);
                view.setUint16(4, d.getUTCFullYear(), true);
                view.setUint8(6, d.getUTCMonth() + 1);
                view.setUint8(7, d.getUTCDate());
                view.setUint8(8, d.getUTCHours());
                view.setUint8(9, d.getUTCMinutes());
                view.setUint8(10, d.getUTCSeconds());
                view.setInt32(16, d.getUTCMilliseconds() * 1000000, true);
                view.setInt32(24, 0, true);
                view.setInt32(28, 510000000, true);
                view.setInt32(48, 20000 + i * 10, true); // Speed 20m/s
                packets.push({
                    msgClass: CLASS_CUSTOM,
                    msgId: ID_HISTORY,
                    payload: new Uint8Array(buffer)
                });
            }
            return packets;
        }

        it('splits sessions on state change packet (0x26)', () => {
            const start1 = Date.UTC(2026, 8, 12, 10, 0, 0);
            const pkts1 = makeHistoryPackets(60, start1);

            const stateChangePkt = {
                msgClass: CLASS_CUSTOM,
                msgId: ID_STATE_CHANGE,
                payload: new Uint8Array(0)
            };

            const start2 = Date.UTC(2026, 8, 12, 11, 0, 0);
            const pkts2 = makeHistoryPackets(60, start2);

            const sessions = splitIntoSessions([...pkts1, stateChangePkt, ...pkts2]);
            expect(sessions.length).toBe(2);
            expect(sessions[0].pointsCount).toBe(60);
            expect(sessions[1].pointsCount).toBe(60);
            expect(sessions[0].startTime.getUTCHours()).toBe(10);
            expect(sessions[1].startTime.getUTCHours()).toBe(11);
        });

        it('splits sessions on time gap > 30 seconds', () => {
            const start1 = Date.UTC(2026, 8, 12, 10, 0, 0);
            const pkts1 = makeHistoryPackets(60, start1);

            // Jump ahead 60 seconds
            const start2 = start1 + (60 * 40) + 60000;
            const pkts2 = makeHistoryPackets(60, start2);

            const sessions = splitIntoSessions([...pkts1, ...pkts2]);
            expect(sessions.length).toBe(2);
            expect(sessions[0].pointsCount).toBe(60);
            expect(sessions[1].pointsCount).toBe(60);
        });

        it('discards tiny snippets under 50 records', () => {
            const start1 = Date.UTC(2026, 8, 12, 10, 0, 0);
            const tinyPkts = makeHistoryPackets(30, start1); // Only 30 points
            const sessions = splitIntoSessions(tinyPkts);
            expect(sessions.length).toBe(0);
        });
    });

    describe('pointsToCsvData', () => {
        it('generates RaceTools CSV data block with proper headers and coordinates', () => {
            const points = [
                {
                    timestampMs: 1000,
                    dateTime: new Date(Date.UTC(2026, 8, 12, 11, 2, 15, 0)),
                    latitude: 51.5000000,
                    longitude: -0.1234567,
                    altitudeM: 45.0,
                    speedMs: 25.0,
                    gForceX: 0.15,
                    gForceY: -0.25,
                    gForceZ: 0.98,
                    gForceLat: 0.15,
                    gForceLon: -0.25,
                    gForceVert: 0.98,
                    yawRateDegS: -15.0,
                    pitchRateDegS: 10.0,
                    rollRateDegS: 5.0
                },
                {
                    timestampMs: 1040,
                    dateTime: new Date(Date.UTC(2026, 8, 12, 11, 2, 15, 40)),
                    latitude: 51.5000100,
                    longitude: -0.1234560,
                    altitudeM: 45.1,
                    speedMs: 25.2,
                    gForceX: 0.16,
                    gForceY: -0.24,
                    gForceZ: 0.99,
                    gForceLat: 0.16,
                    gForceLon: -0.24,
                    gForceVert: 0.99,
                    yawRateDegS: -14.8,
                    pitchRateDegS: 10.1,
                    rollRateDegS: 5.1
                }
            ];

            const csv = pointsToCsvData(points);
            expect(csv).toContain('Record,Time,Latitude,Longitude,Altitude,Speed,GForceLat,GForceLon,GForceVert,Yaw Rate,Pitch Rate,Roll Rate,Lap,Lap Distance');
            const lines = csv.split('\n');
            expect(lines.length).toBe(3);
            expect(lines[1]).toBe('1,2026-09-12T11:02:15.000Z,51.5000000,-0.1234567,45.00,25.000,0.150,-0.250,0.980,-15.000,10.000,5.000,1,0.00');
            expect(lines[2]).toBe('2,2026-09-12T11:02:15.040Z,51.5000100,-0.1234560,45.10,25.200,0.160,-0.240,0.990,-14.800,10.100,5.100,1,0.00');
        });

        it('supports sideways mounting orientation with axis mapping', () => {
            const points = [
                {
                    timestampMs: 1000,
                    dateTime: new Date(Date.UTC(2026, 8, 12, 11, 2, 15, 0)),
                    latitude: 51.5,
                    longitude: -0.12,
                    altitudeM: 45.0,
                    speedMs: 25.0,
                    gForceX: 0.15,  // Device X
                    gForceY: -0.25, // Device Y
                    gForceZ: 0.98,  // Device Z
                    yawRateDegS: -15.0,
                    pitchRateDegS: 10.0,
                    rollRateDegS: 5.0
                }
            ];

            // Sideways: 90° CCW (Lat = +Y, Lon = -X)
            const csv = pointsToCsvData(points, { lat: '+y', lon: '-x', vert: '+z', yaw: '+z' });
            const lines = csv.split('\n');
            // GForceLat should be +Y (-0.250), GForceLon should be -X (-0.150), GForceVert should be +Z (0.980)
            expect(lines[1]).toContain('1,2026-09-12T11:02:15.000Z,51.5000000,-0.1200000,45.00,25.000,-0.250,-0.150,0.980,-15.000,10.000,5.000,1,0.00');
        });
    });

    describe('findMatchingSessionIndex', () => {
        it('matches session closest to target session date and time', () => {
            const sessions = [
                {
                    id: 1,
                    startTime: new Date(Date.UTC(2026, 8, 12, 9, 30, 0))
                },
                {
                    id: 2,
                    startTime: new Date(Date.UTC(2026, 8, 12, 11, 2, 0))
                },
                {
                    id: 3,
                    startTime: new Date(Date.UTC(2026, 8, 12, 14, 0, 0))
                }
            ];

            const idx = findMatchingSessionIndex(sessions, '2026-09-12', '11:02');
            expect(idx).toBe(1); // Session 2
        });

        it('returns 0 for single session', () => {
            const sessions = [{ id: 1, startTime: new Date() }];
            expect(findMatchingSessionIndex(sessions, '2026-09-12', '11:02')).toBe(0);
        });

        it('returns -1 for empty list', () => {
            expect(findMatchingSessionIndex([], '2026-09-12', '11:02')).toBe(-1);
        });
    });

    describe('formatDuration', () => {
        it('formats durations cleanly', () => {
            expect(formatDuration(45)).toBe('45s');
            expect(formatDuration(125)).toBe('2m 05s');
            expect(formatDuration(3665)).toBe('1h 01m 05s');
        });
    });
});
