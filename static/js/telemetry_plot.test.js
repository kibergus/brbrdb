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

import { describe, it, expect, beforeEach, vi } from 'vitest';
import { TelemetryPlot } from './telemetry_plot.js';

function createMockElement(tagName = 'div') {
  return {
    tagName: tagName.toUpperCase(),
    innerHTML: '',
    style: {},
    appendChild: vi.fn(),
    querySelector: vi.fn().mockReturnValue(null),
    querySelectorAll: vi.fn().mockReturnValue([])
  };
}

describe('telemetry_plot.js', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    global.document = {
      createElement: (tag) => createMockElement(tag),
      querySelector: vi.fn().mockReturnValue(createMockElement()),
      head: { appendChild: vi.fn() }
    };
  });

  describe('parseBinaryFloat64', () => {
    it('correctly decodes single and multi-block delta-quantized binary buffer', () => {
      // Block 1: 20-byte header + 3 Int16 values; Block 2: 20-byte header + 2 Int16 values
      const b1Len = 20 + 6;
      const b2Len = 20 + 4;
      const buffer = new ArrayBuffer(b1Len + b2Len);
      const view = new DataView(buffer);

      view.setFloat64(0, 50.0, true);
      view.setFloat64(8, 0.5, true);
      view.setUint32(16, 3, true);
      const deltas1 = new Int16Array(buffer, 20, 3);
      deltas1[0] = 2;   // 50 + 2*0.5 = 51.0
      deltas1[1] = 4;   // 50 + (2+4)*0.5 = 53.0
      deltas1[2] = -32768; // sentinel for null

      view.setFloat64(b1Len, 200.0, true);
      view.setFloat64(b1Len + 8, 0.25, true);
      view.setUint32(b1Len + 16, 2, true);
      const deltas2 = new Int16Array(buffer, b1Len + 20, 2);
      deltas2[0] = 0;   // 200.0
      deltas2[1] = 8;   // 200.0 + 8*0.25 = 202.0

      const decoded = TelemetryPlot.parseBinaryFloat64(buffer);
      expect(decoded.length).toBe(5);
      expect(decoded[0]).toBeCloseTo(51.0);
      expect(decoded[1]).toBeCloseTo(53.0);
      expect(decoded[2]).toBeNull();
      expect(decoded[3]).toBeCloseTo(200.0);
      expect(decoded[4]).toBeCloseTo(202.0);
    });
  });

  describe('render configuration', () => {
    it('sets 2x height, dragmode pan, fixedrange on y-axes, and scrollZoom', async () => {
      const container = createMockElement('div');
      global.Plotly = {
        newPlot: vi.fn().mockResolvedValue({})
      };

      const mockSessionMeta = [{
        session_id: '18_09_practice',
        columns: ['Distance', 'Speed', 'Throttle', 'Brake', 'Steering Angle'],
        laps: [
          { lap: 16, lap_num: 16, start_idx: 0, end_idx: 4, lap_time: '38.50' },
          { lap: 8, lap_num: 8, start_idx: 0, end_idx: 4, lap_time: '38.60' }
        ]
      }];

      // Mock fetch responses: first for telemetry metadata, then binary channels
      global.fetch = vi.fn().mockImplementation((url) => {
        if (url.includes('/api/telemetry?')) {
          return Promise.resolve({
            ok: true,
            status: 200,
            json: async () => mockSessionMeta
          });
        }
        // Channel binary data (20-byte block header + 5 Int16 values)
        const buf = new ArrayBuffer(20 + 10);
        const view = new DataView(buf);
        view.setFloat64(0, 0.0, true);
        view.setFloat64(8, 1.0, true);
        view.setUint32(16, 5, true);
        const int16s = new Int16Array(buf, 20, 5);
        int16s[0] = 400;
        int16s[1] = 50;
        int16s[2] = 50;
        int16s[3] = 50;
        int16s[4] = 50;
        return Promise.resolve({
          ok: true,
          status: 200,
          arrayBuffer: async () => buf
        });
      });

      await TelemetryPlot.render(container, {
        league: 'kartsim',
        class_name: 'iame_waterswift_restricted_cadet_uk',
        track: 'Clay Pigeon',
        date: '2026-08-21',
        session_id: '18_09_practice',
        start_m: 410,
        end_m: 520,
        laps: [16, 8],
        channels: ['Speed', 'Throttle', 'Brake', 'Steering Angle', 'Delta Time']
      });

      expect(global.Plotly.newPlot).toHaveBeenCalled();
      const [elem, traces, layout, config] = global.Plotly.newPlot.mock.calls[0];

      // 1. Verify height is at least 1000px (2x higher for 5 channels)
      expect(layout.height).toBeGreaterThanOrEqual(1000);

      // 2. Verify dragmode is 'pan' for horizontal dragging
      expect(layout.dragmode).toBe('pan');

      // 3. Verify X-axis has fixedrange false (allows horizontal panning & zooming)
      expect(layout.xaxis.fixedrange).toBe(false);

      // 4. Verify all Y-axes have fixedrange true (locks Y-axis so drag and wheel only operate on X)
      expect(layout.yaxis.fixedrange).toBe(true);
      expect(layout.yaxis2.fixedrange).toBe(true);
      expect(layout.yaxis3.fixedrange).toBe(true);
      expect(layout.yaxis4.fixedrange).toBe(true);
      expect(layout.yaxis5.fixedrange).toBe(true);

      // 5. Verify scrollZoom is enabled in config for mouse wheel x-zoom
      expect(config.scrollZoom).toBe(true);
      expect(config.displayModeBar).toBe(false);
      expect(config.responsive).toBe(true);

      // 6. Verify yaxis labels include units of measurement and Brake/Throttle are scaled 0-100%
      expect(layout.yaxis.title.text).toBe('Speed (km/h)');
      expect(layout.yaxis2.title.text).toBe('Throttle (%)');
      expect(layout.yaxis2.range).toEqual([0, 100]);
      expect(layout.yaxis2.autorange).toBe(false);
      expect(layout.yaxis3.title.text).toBe('Brake (%)');
      expect(layout.yaxis3.range).toEqual([0, 100]);
      expect(layout.yaxis3.autorange).toBe(false);
      expect(layout.yaxis4.title.text).toBe('Steering Angle (deg)');
      expect(layout.yaxis5.title.text).toBe('Delta Time (s)');
    });
  });
});
