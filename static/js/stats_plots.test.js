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
import { StatsPlot, TrackMinimap, generateMinimapSvg } from './stats_plots.js';

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

describe('stats_plots.js building bricks', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    global.document = {
      createElement: (tag) => createMockElement(tag),
      querySelector: vi.fn().mockReturnValue(createMockElement()),
      head: { appendChild: vi.fn() }
    };
  });

  describe('TrackMinimap.render', () => {
    it('renders SVG minimap with highlighted turn and centerline paths', async () => {
      const container = createMockElement('div');

      const mockTrackData = {
        lap_length: 893.0,
        center_line: [
          { lat: 51.442, lon: -3.484, dist: 0 },
          { lat: 51.443, lon: -3.483, dist: 300 },
          { lat: 51.444, lon: -3.485, dist: 600 },
          { lat: 51.442, lon: -3.484, dist: 893 }
        ],
        turns: [
          { name: '1', start: 50, end: 120, apex: [85] },
          { name: 'The Dell', start: 553, end: 645, apex: [577, 602, 629] }
        ]
      };

      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => mockTrackData
      });

      await TrackMinimap.render(container, {
        track: 'Llandow',
        highlight_turn: 'The Dell',
        highlight_color: '#10b981'
      });

      expect(container.innerHTML).toContain('<svg');
      expect(container.innerHTML).toContain('The Dell');
      expect(container.innerHTML).toContain('minimap-centerlines');
      expect(container.innerHTML).toContain('minimap-underlines');
      expect(container.innerHTML).toContain('stroke="#10b981"');
    });

    it('handles missing track geometry gracefully', async () => {
      const container = createMockElement('div');
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ center_line: [], turns: [] })
      });

      await TrackMinimap.render(container, { track: 'UnknownTrack' });
      expect(container.innerHTML).toContain('No track geometry available');
    });

    it('uses normalized viewBox and font size independent of track aspect ratio', () => {
      const compactTrack = {
        lap_length: 1000,
        turns: [{ name: 'T1', start: 100, end: 200, apex: [150] }],
        center_line: [
          { lat: 51.0, lon: 0.0, dist: 0 },
          { lat: 51.002, lon: 0.003, dist: 500 },
          { lat: 51.0, lon: 0.0, dist: 1000 }
        ]
      };
      const elongatedTrack = {
        lap_length: 1000,
        turns: [{ name: 'T1', start: 100, end: 200, apex: [150] }],
        center_line: [
          { lat: 51.0, lon: 0.0, dist: 0 },
          { lat: 51.010, lon: 0.001, dist: 500 },
          { lat: 51.0, lon: 0.0, dist: 1000 }
        ]
      };

      const compactSvg = generateMinimapSvg(compactTrack);
      const elongatedSvg = generateMinimapSvg(elongatedTrack);

      expect(compactSvg).toContain('viewBox="0 0 320 320"');
      expect(elongatedSvg).toContain('viewBox="0 0 320 320"');
      expect(compactSvg).toContain('font-size="14"');
      expect(elongatedSvg).toContain('font-size="14"');
      expect(compactSvg).toContain('stroke-width="4.5"');
      expect(elongatedSvg).toContain('stroke-width="4.5"');
    });
  });

  describe('StatsPlot.renderTurnViolin', () => {
    it('fetches real telemetry sessions, extracts valid turn times, and calls Plotly.newPlot', async () => {
      const container = createMockElement('div');

      const mockTrackData = {
        turns: [
          { name: 'The Hook', start: 46, end: 167 },
          { name: 'The Dell', start: 553, end: 645 }
        ]
      };

      const mockSessions = [
        {
          session_id: 'qualifying_sess',
          session_name: 'Qualifying',
          laps: [
            { lap_num: 1, is_valid: false, turn_times: [0, 0] },
            { lap_num: 11, is_valid: true, turn_times: [10.4, 14.075] },
            { lap_num: 13, is_valid: true, turn_times: [10.5, 14.232] }
          ]
        },
        {
          session_id: 'practice_sess',
          session_name: 'Practice',
          laps: [
            { lap_num: 5, is_valid: true, turn_times: [10.9, 14.471] }
          ]
        }
      ];

      global.fetch = vi.fn().mockImplementation((url) => {
        if (url.includes('/api/track_data')) {
          return Promise.resolve({ ok: true, json: async () => mockTrackData });
        }
        if (url.includes('/api/telemetry')) {
          return Promise.resolve({ ok: true, json: async () => mockSessions });
        }
        return Promise.reject(new Error('Unknown URL ' + url));
      });

      global.Plotly = {
        newPlot: vi.fn().mockResolvedValue({})
      };

      await StatsPlot.renderTurnViolin(container, {
        track: 'Llandow',
        date: '2026-08-02',
        league: 'club100_south',
        class_name: 'cadet_lw',
        turn_name: 'The Dell',
        highlight_laps: [
          { session_id: 'qualifying_sess', lap: 11, label: 'Benchmark', color: '#10b981' }
        ]
      });

      expect(global.Plotly.newPlot).toHaveBeenCalled();
      const args = global.Plotly.newPlot.mock.calls[0];
      const traces = args[1];
      const layout = args[2];
      expect(traces).toHaveLength(1);
      expect(traces[0].type).toBe('violin');
      // Should contain 3 valid laps (14.075, 14.232, 14.471), excluding lap 1
      expect(traces[0].y).toHaveLength(3);
      expect(traces[0].y).toContain(14.075);
      expect(traces[0].y).toContain(14.232);
      expect(traces[0].y).toContain(14.471);
      // yaxis should be bounded to 2 seconds from min
      expect(layout.yaxis.range[1]).toBeCloseTo(14.075 + 2.12, 1);
    });

    it('separates outliers (> 2.0s gap) into a scatter dot trace above the 2s line', async () => {
      const container = createMockElement('div');
      const mockTrackData = { turns: [{ name: 'The Dell', start: 500, end: 600 }] };
      const mockSessions = [{
        session_id: 's1',
        laps: [
          { lap_num: 1, is_valid: true, turn_times: [14.0] },
          { lap_num: 2, is_valid: true, turn_times: [15.5] },
          { lap_num: 3, is_valid: true, turn_times: [18.2] } // outlier (+4.2s)
        ]
      }];

      global.fetch = vi.fn().mockImplementation((url) => {
        if (url.includes('/api/track_data')) return Promise.resolve({ ok: true, json: async () => mockTrackData });
        if (url.includes('/api/telemetry')) return Promise.resolve({ ok: true, json: async () => mockSessions });
        return Promise.reject(new Error('Unknown URL'));
      });

      global.Plotly = { newPlot: vi.fn().mockResolvedValue({}) };

      await StatsPlot.renderTurnViolin(container, {
        track: 'Llandow',
        date: '2026-08-02',
        turn_name: 'The Dell'
      });

      expect(global.Plotly.newPlot).toHaveBeenCalled();
      const args = global.Plotly.newPlot.mock.calls[0];
      const traces = args[1];
      expect(traces).toHaveLength(2);
      // Main violin has in-range (14.0 and 15.5)
      expect(traces[0].type).toBe('violin');
      expect(traces[0].y).toEqual([14.0, 15.5]);
      // Outlier trace has 18.2 plotted at y = 14.0 + 2.06
      expect(traces[1].type).toBe('scatter');
      expect(traces[1].y[0]).toBeCloseTo(16.06, 2);
      expect(traces[1].text[0]).toContain('Outlier: +4.200s');
    });
  });
});
