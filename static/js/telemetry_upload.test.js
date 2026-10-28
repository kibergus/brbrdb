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
    CsvTelemetryProcessor,
    TelemetryProcessor
} from './telemetry_upload.js';

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
});
