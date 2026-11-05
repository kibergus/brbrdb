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
    hasGyroscopeChannels,
    extractTelemetryData,
    buildRaceToolsPayload,
    mergeRaceBoxSessions,
    CsvTelemetryProcessor,
    TelemetryProcessor,
    BRT1_CHUNK_TYPES,
    DEFAULT_BRT1_CHANNELS,
    encodeBrt1Chunk,
    encodeBrt1Stream,
    decodeBrt1Stream
} from './telemetry_upload.js';
import { pointsToCsvData } from './racebox_ble.js';

describe('telemetry_upload.js', () => {
    const testSessionMeta = {
        track: 'Lydd',
        date: '2026-09-13',
        time: '10:31',
        league: 'club100_south',
        class_name: 'cadet_slw',
        session_name: 'Practice 1',
        session_id: 'practice_1'
    };

    describe('hasGyroscopeChannels', () => {
        it('detects Yaw Rate, Pitch Rate, or Roll Rate', () => {
            expect(hasGyroscopeChannels('Time,Latitude,Longitude,Speed,Yaw Rate')).toBe(true);
            expect(hasGyroscopeChannels('Time,Latitude,Longitude,Speed,Pitch Rate')).toBe(true);
            expect(hasGyroscopeChannels('Time,Latitude,Longitude,Speed,Roll Rate')).toBe(true);
        });

        it('returns false when no gyro channels exist', () => {
            expect(hasGyroscopeChannels('Time,Latitude,Longitude,Altitude,Speed,GForceX')).toBe(false);
            expect(hasGyroscopeChannels('')).toBe(false);
        });
    });

    describe('extractTelemetryData', () => {
        it('skips metadata block separated by blank line and returns header and data text', () => {
            const content = [
                'Camera,HERO11 Black',
                'Date,2026-09-13',
                'Firmware,1.20',
                '',
                'CustomCol1,CustomCol2,CustomCol3',
                'val1,val2,val3'
            ].join('\n');

            const result = extractTelemetryData(content);
            expect(result.headerLine).toBe('CustomCol1,CustomCol2,CustomCol3');
            expect(result.dataText).toBe('CustomCol1,CustomCol2,CustomCol3\nval1,val2,val3');
        });

        it('treats content starting with data rows directly without blank line', () => {
            const content = [
                'Time,Speed,Throttle',
                '0.0,10.5,100',
                '0.1,11.2,100'
            ].join('\n');

            const result = extractTelemetryData(content);
            expect(result.headerLine).toBe('Time,Speed,Throttle');
            expect(result.dataText).toBe(content);
        });

        it('handles empty or whitespace-only input', () => {
            expect(extractTelemetryData('')).toEqual({ headerLine: '', dataText: '' });
            expect(extractTelemetryData('   \n\n  ')).toEqual({ headerLine: '', dataText: '' });
        });
    });

    describe('buildRaceToolsPayload', () => {
        const sampleData = 'Record,Time,Latitude,Longitude,Speed (m/s)\n1,10:31:00.000,51.05,-0.15,15.2';

        it('constructs correct header block and appends data', () => {
            const payload = buildRaceToolsPayload(testSessionMeta, 'Test Driver', sampleData, 1.5);

            expect(payload).toContain('Format,RaceTools CSV');
            expect(payload).toContain('Track name,Lydd');
            expect(payload).toContain('Date,2026-09-13');
            expect(payload).toContain('Time,10:31');
            expect(payload).toContain('Driver name,Test Driver');
            expect(payload).toContain('League,club100_south');
            expect(payload).toContain('Class,cadet_slw');
            expect(payload).toContain('Session,Practice 1');
            expect(payload).toContain('Session ID,practice_1');
            expect(payload).toContain('Distance to rear axle,1.5');
            expect(payload).toContain('Record,Time,Latitude,Longitude,Speed (m/s)');
            expect(payload).toContain('1,10:31:00.000,51.05,-0.15,15.2');
        });

        it('omits distance to rear axle if null', () => {
            const payload = buildRaceToolsPayload(testSessionMeta, 'Test Driver', 'Record,Time', null);
            expect(payload).not.toContain('Distance to rear axle');
        });

        it('throws an error if any required metadata or data is missing', () => {
            expect(() => buildRaceToolsPayload({ ...testSessionMeta, session_id: '' }, 'Test Driver', 'Record,Time'))
                .toThrow(/Missing required session metadata: session_id/);
            expect(() => buildRaceToolsPayload({ ...testSessionMeta, league: '' }, 'Test Driver', 'Record,Time'))
                .toThrow(/Missing required session metadata: league/);
            expect(() => buildRaceToolsPayload({ ...testSessionMeta, class_name: '' }, 'Test Driver', 'Record,Time'))
                .toThrow(/Missing required session metadata: class_name/);
            expect(() => buildRaceToolsPayload(testSessionMeta, '', 'Record,Time'))
                .toThrow(/Driver name is required/);
            expect(() => buildRaceToolsPayload(testSessionMeta, 'Test Driver', ''))
                .toThrow(/Telemetry data is required/);
        });
    });

    describe('CsvTelemetryProcessor', () => {
        it('canProcess identifies csv files correctly', () => {
            expect(CsvTelemetryProcessor.canProcess({ name: 'track_data.csv' })).toBe(true);
            expect(CsvTelemetryProcessor.canProcess({ name: 'track_data.CSV' })).toBe(true);
            expect(CsvTelemetryProcessor.canProcess({ name: 'video.mp4' })).toBe(false);
        });

        it('requires distance to rear axle if gyroscope channels are present', async () => {
            const csvContent = 'Record,Time,Latitude,Longitude,Yaw Rate\n1,10:00.0,51.0,0.0,0.1';
            const mockFile = {
                name: 'test.csv',
                text: async () => csvContent
            };

            await expect(
                CsvTelemetryProcessor.process(
                    mockFile,
                    {
                        sessionMeta: testSessionMeta,
                        driverName: 'Test Driver',
                        distanceToRearAxle: null
                    }
                )
            ).rejects.toThrow(/Gyro arm length/);
        });

        it('passes through files already starting with Format,RaceTools CSV', async () => {
            const content = 'Format,RaceTools CSV\nTrack name,Lydd\n\nRecord,Time\n1,10:00';
            const mockFile = {
                name: 'preformatted.csv',
                text: async () => content
            };

            const result = await CsvTelemetryProcessor.process(mockFile, {});
            expect(result).toBe(content);
        });
    });

    describe('TelemetryProcessor', () => {
        it('rejects unsupported file extensions', async () => {
            const mockFile = { name: 'telemetry.json' };
            await expect(
                TelemetryProcessor.process(mockFile, {})
            ).rejects.toThrow(/Unsupported file type/);
        });
    });

    describe('RaceBox integration', () => {
        it('builds RaceTools payload from pointsToCsvData correctly', () => {
            const points = [
                {
                    timestampMs: 1000,
                    dateTime: new Date(Date.UTC(2026, 8, 13, 10, 31, 0)),
                    latitude: 51.05,
                    longitude: -0.15,
                    altitudeM: 45.0,
                    speedMs: 15.2,
                    gForceX: 0.15,
                    gForceY: -0.25,
                    gForceZ: 0.98,
                    gForceLat: 0.15,
                    gForceLon: -0.25,
                    gForceVert: 0.98,
                    yawRateDegS: -15.0,
                    pitchRateDegS: 10.0,
                    rollRateDegS: 5.0
                }
            ];
            const dataText = pointsToCsvData(points);
            const payload = buildRaceToolsPayload(testSessionMeta, 'Test Driver', dataText, 1.5);

            expect(payload).toContain('Format,RaceTools CSV');
            expect(payload).toContain('Session ID,practice_1');
            expect(payload).toContain('Distance to rear axle,1.5');
            expect(payload).toContain('GForceLat,GForceLon,GForceVert');
            expect(payload).not.toContain('GForceX');
            expect(payload).toContain('Yaw Rate');
            expect(payload).toContain('-15.000');
        });
    });

    describe('mergeRaceBoxSessions', () => {
        it('returns empty array when sessionsList is empty or null', () => {
            expect(mergeRaceBoxSessions([])).toEqual([]);
            expect(mergeRaceBoxSessions(null)).toEqual([]);
        });

        it('merges multiple sessions in chronological order and deduplicates identical timestamps', () => {
            const session1 = {
                id: 's1',
                points: [
                    { timestampMs: 1000, latitude: 51.1 },
                    { timestampMs: 1040, latitude: 51.2 }
                ]
            };
            const session2 = {
                id: 's2',
                points: [
                    { timestampMs: 2000, latitude: 51.3 },
                    { timestampMs: 2040, latitude: 51.4 }
                ]
            };
            // Test passing in reverse order to ensure chronological sorting
            const merged = mergeRaceBoxSessions([session2, session1]);
            expect(merged.length).toBe(4);
            expect(merged[0].timestampMs).toBe(1000);
            expect(merged[1].timestampMs).toBe(1040);
            expect(merged[2].timestampMs).toBe(2000);
            expect(merged[3].timestampMs).toBe(2040);
        });

        it('deduplicates overlapping points with exact same timestampMs', () => {
            const session1 = {
                id: 's1',
                points: [
                    { timestampMs: 1000, latitude: 51.1 },
                    { timestampMs: 1040, latitude: 51.2 }
                ]
            };
            const session2 = {
                id: 's2',
                points: [
                    { timestampMs: 1040, latitude: 51.2 },
                    { timestampMs: 1080, latitude: 51.3 }
                ]
            };
            const merged = mergeRaceBoxSessions([session1, session2]);
            expect(merged.length).toBe(3);
            expect(merged.map(p => p.timestampMs)).toEqual([1000, 1040, 1080]);
        });
    });

    describe('BRT1 Protocol (telemetry_upload.js)', () => {
        it('exports correct chunk types and default channels', () => {
            expect(BRT1_CHUNK_TYPES.MAGIC).toBe(0x01);
            expect(BRT1_CHUNK_TYPES.METADATA).toBe(0x02);
            expect(BRT1_CHUNK_TYPES.BASELINE).toBe(0x03);
            expect(BRT1_CHUNK_TYPES.DELTA).toBe(0x04);
            expect(BRT1_CHUNK_TYPES.EOS).toBe(0xFF);

            expect(DEFAULT_BRT1_CHANNELS.length).toBeGreaterThan(5);
            const timeChan = DEFAULT_BRT1_CHANNELS.find(c => c.name === 'Time');
            expect(timeChan).toBeDefined();
            expect(timeChan.scale).toBe(0.001);
        });

        it('correctly frames chunks with encodeBrt1Chunk', () => {
            const payload = new Uint8Array([0x42, 0x52, 0x54, 0x31]); // 'BRT1'
            const chunk = encodeBrt1Chunk(BRT1_CHUNK_TYPES.MAGIC, payload);

            expect(chunk.length).toBe(7);
            expect(chunk[0]).toBe(4); // length low byte
            expect(chunk[1]).toBe(0); // length high byte
            expect(chunk[2]).toBe(0x01); // type magic
            expect(chunk[3]).toBe(0x42);
            expect(chunk[4]).toBe(0x52);
            expect(chunk[5]).toBe(0x54);
            expect(chunk[6]).toBe(0x31);
        });

        it('encodes and decodes BRT1 telemetry stream in round-trip', () => {
            const channels = [
                { name: 'Time', scale: 0.001 },
                { name: 'Latitude', scale: 1e-7 },
                { name: 'Longitude', scale: 1e-7 },
                { name: 'Speed', scale: 0.01 },
                { name: 'Lap', scale: 1.0 }
            ];

            const records = [
                { Time: 0.0, Latitude: 51.5000000, Longitude: -0.1000000, Speed: 10.0, Lap: 1 },
                { Time: 0.010, Latitude: 51.5000100, Longitude: -0.1000050, Speed: 10.2, Lap: 1 },
                { Time: 0.020, Latitude: 51.5000200, Longitude: -0.1000100, Speed: 10.5, Lap: 1 }
            ];

            const streamBytes = encodeBrt1Stream(testSessionMeta, channels, records);
            expect(streamBytes.length).toBeGreaterThan(0);

            // First 7 bytes must be Magic chunk
            expect(streamBytes.slice(0, 7)).toEqual(new Uint8Array([4, 0, 1, 0x42, 0x52, 0x54, 0x31]));

            const decoded = decodeBrt1Stream(streamBytes);
            expect(decoded.metadata.track).toBe('Lydd');
            expect(decoded.channels.length).toBe(5);
            expect(decoded.records.length).toBe(3);

            // Sample 0 (Baseline)
            expect(decoded.records[0].Time).toBeCloseTo(0.0, 3);
            expect(decoded.records[0].Latitude).toBeCloseTo(51.5000000, 7);
            expect(decoded.records[0].Speed).toBeCloseTo(10.0, 2);
            expect(decoded.records[0].Lap).toBe(1);

            // Sample 1 (Delta)
            expect(decoded.records[1].Time).toBeCloseTo(0.010, 3);
            expect(decoded.records[1].Latitude).toBeCloseTo(51.5000100, 7);
            expect(decoded.records[1].Speed).toBeCloseTo(10.2, 2);

            // Sample 2 (Delta)
            expect(decoded.records[2].Time).toBeCloseTo(0.020, 3);
            expect(decoded.records[2].Latitude).toBeCloseTo(51.5000200, 7);
            expect(decoded.records[2].Speed).toBeCloseTo(10.5, 2);
        });

        it('handles NaN values and keyframe recovery', () => {
            const channels = [
                { name: 'Time', scale: 0.001 },
                { name: 'Speed', scale: 0.01 }
            ];

            const records = [
                { Time: 0.0, Speed: 10.0 },
                { Time: 0.01, Speed: null }, // NaN
                { Time: 0.02, Speed: 12.0 }  // Recovery from NaN requires keyframe
            ];

            const streamBytes = encodeBrt1Stream(testSessionMeta, channels, records);
            const decoded = decodeBrt1Stream(streamBytes);

            expect(decoded.records.length).toBe(3);
            expect(decoded.records[0].Speed).toBeCloseTo(10.0, 2);
            expect(decoded.records[1].Speed).toBeNull();
            expect(decoded.records[2].Speed).toBeCloseTo(12.0, 2);
        });

        it('throws error on corrupted chunk payload or truncated data', () => {
            // Only 2 bytes (cannot even read header)
            expect(() => decodeBrt1Stream(new Uint8Array([4, 0]))).toThrow(/Truncated BRT1 stream/);

            // Valid magic chunk, followed by metadata chunk claiming 10 bytes payload, but only 1 byte provided
            const truncatedPayload = new Uint8Array([4, 0, 1, 0x42, 0x52, 0x54, 0x31, 10, 0, 2, 0x7b]);
            expect(() => decodeBrt1Stream(truncatedPayload)).toThrow(/Truncated chunk payload/);
        });

        it('handles End of Stream (EOS, 0xFF) chunk cleanly', () => {
            const channels = [
                { name: 'Time', scale: 0.001 },
                { name: 'Speed', scale: 0.01 }
            ];
            const records = [
                { Time: 0.0, Speed: 15.0 },
                { Time: 0.01, Speed: 15.5 }
            ];

            const streamBytes = encodeBrt1Stream(testSessionMeta, channels, records);
            const eosChunk = encodeBrt1Chunk(BRT1_CHUNK_TYPES.EOS); // 0-length payload 0xFF
            const streamWithEos = new Uint8Array(streamBytes.length + eosChunk.length);
            streamWithEos.set(streamBytes, 0);
            streamWithEos.set(eosChunk, streamBytes.length);

            const decoded = decodeBrt1Stream(streamWithEos);
            expect(decoded.records.length).toBe(2);
            expect(decoded.records[0].Speed).toBeCloseTo(15.0, 2);
            expect(decoded.records[1].Speed).toBeCloseTo(15.5, 2);
        });
    });
});

