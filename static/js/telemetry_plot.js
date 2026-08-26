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
 * TelemetryPlot.js - Lightweight interactive JavaScript library for telemetry plots in HTML Coaching Reports.
 * Renders stacked multi-channel telemetry comparison charts using Plotly.js.
 */

(function(global) {
  'use strict';

  var TelemetryPlot = {
    /**
     * Load Plotly library dynamically if not present.
     */
    ensurePlotlyLoaded: function() {
      if (global.Plotly) {
        return Promise.resolve(global.Plotly);
      }
      return new Promise(function(resolve, reject) {
        var script = document.createElement('script');
        script.src = 'https://cdn.plot.ly/plotly-2.27.0.min.js';
        script.onload = function() { resolve(global.Plotly); };
        script.onerror = function() { reject(new Error('Failed to load Plotly CDN')); };
        document.head.appendChild(script);
      });
    },

    /**
     * Parse delta-encoded binary payload from /api/telemetry/channel endpoint.
     * Header (16 bytes): meanVal (float64), scale (float64)
     * Body: Int16Array of cumulative delta quantization values.
     */
    parseBinaryFloat64: function(arrayBuffer) {
      if (!arrayBuffer || arrayBuffer.byteLength < 16) {
        return [];
      }

      var view = new DataView(arrayBuffer);
      var meanVal = view.getFloat64(0, true);
      var scale = view.getFloat64(8, true);
      var deltas = new Int16Array(arrayBuffer.slice(16));

      var length = deltas.length;
      var values = new Array(length);
      var currentQuantized = 0;

      for (var i = 0; i < length; i++) {
        if (deltas[i] === -32768) {
          values[i] = null;
        } else {
          currentQuantized += deltas[i];
          values[i] = meanVal + currentQuantized * scale;
        }
      }
      return values;
    },

    /**
     * Fetch a single binary channel array from /api/telemetry/channel.
     */
    fetchChannel: function(options, csvFilename, channelName) {
      var league = options.league || 'kartsim';
      var className = options.class_name || 'iame_waterswift_restricted_cadet_uk';
      var track = options.track;
      var date = options.date;
      var apiBase = options.api_base || '';

      var url = apiBase + '/api/telemetry/channel?league=' + encodeURIComponent(league) +
                '&class_name=' + encodeURIComponent(className) +
                '&date=' + encodeURIComponent(date) +
                '&track=' + encodeURIComponent(track) +
                '&session_id=' + encodeURIComponent(csvFilename) +
                '&channel=' + encodeURIComponent(channelName);

      return fetch(url, { credentials: 'include' })
        .then(function(res) {
          if (!res.ok) {
            var err = new Error('Failed to fetch channel ' + channelName + ' (status ' + res.status + ')');
            err.status = res.status;
            err.isAuthError = (res.status === 401 || res.status === 403);
            throw err;
          }
          return res.arrayBuffer();
        })
        .then(function(buffer) {
          return TelemetryPlot.parseBinaryFloat64(buffer);
        });
    },

    /**
     * Fetch a single session's metadata and channel data.
    /**
     * Fetch a single session's metadata and channel data.
     */
    fetchSessionData: function(options, targetSessionId, targetDate, targetTrack) {
      var self = this;
      var league = options.league || 'kartsim';
      var className = options.class_name || 'iame_waterswift_restricted_cadet_uk';
      var track = targetTrack || options.track;
      var date = targetDate || options.date;
      var sessionId = targetSessionId || options.session_id;
      var apiBase = options.api_base || '';

      var url = apiBase + '/api/telemetry?league=' + encodeURIComponent(league) +
                '&class_name=' + encodeURIComponent(className) +
                '&date=' + encodeURIComponent(date) +
                '&track=' + encodeURIComponent(track);

      return fetch(url, { credentials: 'include' })
        .then(function(res) {
          if (!res.ok) {
            var err = new Error('API request failed with status ' + res.status);
            err.status = res.status;
            err.isAuthError = (res.status === 401 || res.status === 403);
            throw err;
          }
          return res.json();
        })
        .then(function(sessionsData) {
          var targetSession = null;
          for (var i = 0; i < sessionsData.length; i++) {
            if (sessionsData[i].session_id === sessionId || sessionsData[i].session_id.indexOf(sessionId) !== -1) {
              targetSession = sessionsData[i];
              break;
            }
          }
          if (!targetSession && sessionsData.length > 0) {
            targetSession = sessionsData[0];
          }
          if (!targetSession) {
            throw new Error('Session ' + sessionId + ' not found in telemetry response');
          }

          var csvFilename = targetSession.session_id;
          var availableCols = targetSession.columns || [];

          function findCol(candidates) {
            for (var c = 0; c < candidates.length; c++) {
              for (var a = 0; a < availableCols.length; a++) {
                if (availableCols[a].toLowerCase() === candidates[c].toLowerCase()) {
                  return availableCols[a];
                }
              }
            }
            return candidates[0];
          }

          var distCol = findCol(['Lap Distance (m)', 'Lap Distance', 'LapDistance']);
          var timeCol = findCol(['Time']);

          var requestedChannels = options.channels || ['Speed', 'Throttle', 'Brake', 'Steering Angle', 'Delta Time'];
          var channelColMap = {};

          function isDeltaTime(name) {
            var s = (name || '').toLowerCase().replace('_', ' ').trim();
            return s === 'delta time' || s === 'delta';
          }

          requestedChannels.forEach(function(ch) {
            var normCh = ch.toLowerCase().trim();
            if (normCh === 'speed' || normCh === 'speed (km/h)') channelColMap[ch] = findCol(['Speed', 'Speed (km/h)']);
            else if (normCh === 'brake' || normCh === 'brake (%)') channelColMap[ch] = findCol(['Brake', 'Brake (%)']);
            else if (normCh === 'throttle' || normCh === 'throttle (%)') channelColMap[ch] = findCol(['Throttle', 'Throttle (%)']);
            else if (normCh === 'steering' || normCh === 'steering angle' || normCh.indexOf('steering') !== -1) channelColMap[ch] = findCol(['Steering Angle', 'Steering Wheel Angle (deg)', 'Steering']);
            else if (!isDeltaTime(ch)) channelColMap[ch] = findCol([ch]);
          });

          var channelKeys = Object.keys(channelColMap);
          var fetchPromises = [
            self.fetchChannel({ league: league, class_name: className, track: track, date: date, api_base: apiBase }, csvFilename, distCol),
            self.fetchChannel({ league: league, class_name: className, track: track, date: date, api_base: apiBase }, csvFilename, timeCol)
          ];
          channelKeys.forEach(function(chKey) {
            fetchPromises.push(self.fetchChannel({ league: league, class_name: className, track: track, date: date, api_base: apiBase }, csvFilename, channelColMap[chKey]));
          });

          return Promise.all(fetchPromises).then(function(results) {
            var distsArr = results[0];
            var timesArr = results[1];
            var channelArrays = {};
            channelKeys.forEach(function(chKey, idx) {
              channelArrays[chKey] = results[2 + idx];
            });

            if (targetSession.laps) {
              targetSession.laps.forEach(function(lap) {
                var startIdx = lap.start_idx || 0;
                var endIdx = (lap.end_idx !== undefined && lap.end_idx >= startIdx) ? lap.end_idx : (distsArr.length - 1);
                var points = [];
                var t0 = timesArr[startIdx] || 0;

                for (var k = startIdx; k <= endIdx && k < distsArr.length; k++) {
                  var pt = {
                    dist: distsArr[k],
                    time: timesArr[k] !== undefined ? (timesArr[k] - t0) : 0
                  };
                  channelKeys.forEach(function(chKey) {
                    pt[chKey] = channelArrays[chKey] ? channelArrays[chKey][k] : null;
                  });
                  points.push(pt);
                }
                lap.points = points;
              });
            }

            return targetSession;
          });
        });
    },

    /**
     * Parse lap spec item into uniform lap spec object.
     * Item format can be:
     * - Tuple/Array: [date, track, session_id, lap_number, label, color, style]
     * - Object: { date, track, session_id, lap, label, color, style }
     * - Number: 12 (uses top-level default options for date, track, session_id)
     */
    parseLapSpec: function(item, defaultOptions) {
      defaultOptions = defaultOptions || {};
      var spec = {};
      if (Array.isArray(item) && item.length >= 4) {
        spec.date = String(item[0]);
        spec.track = String(item[1]);
        spec.session_id = String(item[2]);
        spec.lap = Number(item[3]);
        if (item[4]) spec.label = String(item[4]);
        if (item[5]) spec.color = String(item[5]);
        if (item[6]) spec.style = String(item[6]);
      } else if (typeof item === 'object' && item !== null) {
        spec.date = String(item.date || defaultOptions.date || '');
        spec.track = String(item.track || defaultOptions.track || '');
        spec.session_id = String(item.session_id || defaultOptions.session_id || '');
        spec.lap = Number(item.lap !== undefined ? item.lap : (item.lap_num !== undefined ? item.lap_num : 0));
        if (item.label) spec.label = String(item.label);
        if (item.color) spec.color = String(item.color);
        if (item.style) spec.style = String(item.style);
        if (item.key) spec.key = String(item.key);
      } else {
        spec.date = String(defaultOptions.date || '');
        spec.track = String(defaultOptions.track || '');
        spec.session_id = String(defaultOptions.session_id || '');
        spec.lap = Number(item);
      }
      spec.key = spec.key || (spec.session_id + '_L' + spec.lap);
      return spec;
    },

    /**
     * Fetch channel telemetry for all lap specs uniformly.
     */
    fetchData: function(options) {
      if (options.data || options.telemetry_data) {
        return Promise.resolve(options.data || options.telemetry_data);
      }

      var self = this;
      var rawLaps = options.laps || [];
      if (rawLaps.length === 0) {
        return Promise.resolve({ laps: [] });
      }

      var lapSpecs = rawLaps.map(function(item) { return self.parseLapSpec(item, options); });

      // Group specs by unique session key: date + '_' + track + '_' + session_id
      var sessionMap = {};
      lapSpecs.forEach(function(spec) {
        var sKey = spec.date + '::' + spec.track + '::' + spec.session_id;
        if (!sessionMap[sKey]) {
          sessionMap[sKey] = {
            date: spec.date,
            track: spec.track,
            session_id: spec.session_id,
            specs: []
          };
        }
        sessionMap[sKey].specs.push(spec);
      });

      var sKeys = Object.keys(sessionMap);
      var fetchPromises = sKeys.map(function(sKey) {
        var sInfo = sessionMap[sKey];
        return self.fetchSessionData(options, sInfo.session_id, sInfo.date, sInfo.track);
      });

      return Promise.all(fetchPromises).then(function(sessionResults) {
        var resultSessionsByKey = {};
        sKeys.forEach(function(sKey, idx) {
          resultSessionsByKey[sKey] = sessionResults[idx];
        });

        var loadedLaps = [];
        lapSpecs.forEach(function(spec) {
          var sKey = spec.date + '::' + spec.track + '::' + spec.session_id;
          var sData = resultSessionsByKey[sKey];
          var matchingLap = null;

          if (sData && sData.laps) {
            for (var l = 0; l < sData.laps.length; l++) {
              if (Number(sData.laps[l].lap_num) === spec.lap) {
                matchingLap = JSON.parse(JSON.stringify(sData.laps[l]));
                break;
              }
            }
          }

          if (matchingLap) {
            matchingLap.custom_key = spec.key;
            matchingLap.custom_label = spec.label;
            matchingLap.custom_color = spec.color;
            matchingLap.custom_style = spec.style;
            matchingLap.session_id = spec.session_id;
            matchingLap.date = spec.date;
            loadedLaps.push(matchingLap);
          }
        });

        return { laps: loadedLaps };
      });
    },

    /**
     * Render the interactive Telemetry Plot into container.
     */
    render: function(container, options) {
      var self = this;
      var targetElem = typeof container === 'string' ? document.querySelector(container) : container;
      if (!targetElem) {
        console.error('TelemetryPlot: container element not found', container);
        return Promise.reject(new Error('Container not found'));
      }

      targetElem.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:300px;color:#94a3b8;">Loading telemetry plot...</div>';

      return this.ensurePlotlyLoaded().then(function(Plotly) {
        return self.fetchData(options).then(function(sessionData) {
          targetElem.innerHTML = ''; // clear loading state

          var startM = options.start_m !== undefined ? options.start_m : 0;
          var endM = options.end_m !== undefined ? options.end_m : 1000;
          var requestedLapsNum = (options.laps || []).map(function(v) { return Number(v); });
          var channels = options.channels || ['Speed', 'Throttle', 'Brake', 'Steering Angle', 'Delta Time'];
          var verticalLines = options.vertical_lines || [];
          var highlightRanges = options.highlight_ranges || [];
          var lapStyles = options.lap_styles || {};
          var annotations = options.annotations || [];

          function isDeltaTime(name) {
            var s = (name || '').toLowerCase().replace('_', ' ').trim();
            return s === 'delta time' || s === 'delta';
          }

          var defaultColors = ['#10b981', '#f43f5e', '#38bdf8', '#f59e0b', '#a855f7', '#ec4899'];
          var numChannels = channels.length;

          var traces = [];
          var shapes = [];
          var plotlyAnnotations = [];

          // Highlight ranges (background rects)
          highlightRanges.forEach(function(hr) {
            var hrStart = hr.start_m !== undefined ? hr.start_m : 0;
            var hrEnd = hr.end_m !== undefined ? hr.end_m : 1000;
            shapes.push({
              type: 'rect',
              xref: 'x',
              yref: 'paper',
              x0: hrStart,
              x1: hrEnd,
              y0: 0,
              y1: 1,
              fillcolor: hr.color || 'rgba(255, 82, 82, 0.12)',
              line: { width: 0 },
              layer: 'below'
            });
            if (hr.label) {
              plotlyAnnotations.push({
                x: (hrStart + hrEnd) / 2,
                y: 1,
                xref: 'x',
                yref: 'paper',
                text: hr.label,
                showarrow: false,
                font: { color: '#94a3b8', size: 10 },
                yanchor: 'bottom'
              });
            }
          });

          // Vertical lines (Apexes, Brake points)
          verticalLines.forEach(function(vl) {
            var vlDist = vl.distance_m !== undefined ? vl.distance_m : 0;

            shapes.push({
              type: 'line',
              xref: 'x',
              yref: 'paper',
              x0: vlDist,
              x1: vlDist,
              y0: 0,
              y1: 1,
              line: {
                color: vl.color || '#ef4444',
                width: 1.5,
                dash: vl.style || 'dash'
              }
            });
            if (vl.label) {
              plotlyAnnotations.push({
                x: vlDist,
                y: 1.02,
                xref: 'x',
                yref: 'paper',
                text: vl.label,
                showarrow: false,
                font: { color: vl.color || '#ef4444', size: 11, weight: 'bold' },
                yanchor: 'bottom'
              });
            }
          });

          // Laps to render (in exact order returned by fetchData)
          var lapsToRender = sessionData.laps || [];

          function interpolateTimeAtDist(pts, targetDist) {
            if (!pts || pts.length === 0) return 0;
            if (targetDist <= pts[0].dist) return pts[0].time;
            if (targetDist >= pts[pts.length - 1].dist) return pts[pts.length - 1].time;
            for (var i = 0; i < pts.length - 1; i++) {
              if (pts[i].dist <= targetDist && pts[i + 1].dist >= targetDist) {
                var dSpan = pts[i + 1].dist - pts[i].dist;
                var frac = dSpan > 0.0001 ? (targetDist - pts[i].dist) / dSpan : 0;
                return pts[i].time + frac * (pts[i + 1].time - pts[i].time);
              }
            }
            return pts[pts.length - 1].time;
          }

          var channelMinMax = {};
          channels.forEach(function(ch) {
            channelMinMax[ch] = { min: Infinity, max: -Infinity };
          });

          // Reference lap for Delta Time (first lap in lapsToRender)
          var refLapObj = lapsToRender.length > 0 ? lapsToRender[0] : null;
          var refPoints = (refLapObj && refLapObj.points) ? refLapObj.points : [];
          var refT_at_start = interpolateTimeAtDist(refPoints, startM);

          // Build time and channel series per lap
          lapsToRender.forEach(function(lap, lapIdx) {
            var lapKey = lap.custom_key || String(lap.lap_num);
            var style = lapStyles[lapKey] || lapStyles[String(lap.lap_num)] || {};
            var lineConfig = {
              color: lap.custom_color || style.color || defaultColors[lapIdx % defaultColors.length],
              width: style.width || 2,
              dash: lap.custom_style || style.style || 'solid'
            };
            var lapLabel = lap.custom_label || style.label || ('Lap ' + lap.lap_num + (lap.session_id ? ' (' + lap.session_id + ')' : ''));

            var points = lap.points || [];
            var lapDists = [];
            var lapTimes = [];
            var lapChannels = {};

            channels.forEach(function(ch) { lapChannels[ch] = []; });

            for (var p = 0; p < points.length; p++) {
              var pt = points[p];
              if (pt && pt.dist !== null && pt.dist !== undefined && !isNaN(pt.dist)) {
                if (pt.dist >= startM - 5 && pt.dist <= endM + 5) {
                  lapDists.push(pt.dist);
                  lapTimes.push(pt.time || 0);
                  channels.forEach(function(ch) {
                    if (!isDeltaTime(ch)) {
                      var normCh = ch.toLowerCase();
                      var val = (pt[ch] !== undefined && pt[ch] !== null && !isNaN(pt[ch])) ? pt[ch] :
                        ((pt[normCh] !== undefined && pt[normCh] !== null && !isNaN(pt[normCh])) ? pt[normCh] : null);
                      lapChannels[ch].push(val);
                    }
                  });
                }
              }
            }

            var lapT_at_start = interpolateTimeAtDist(points, startM);

            // Create subplots for channels
            channels.forEach(function(ch, chIdx) {
              var axisSuffix = chIdx === 0 ? '' : String(chIdx + 1);
              var yData = lapChannels[ch] || [];

              if (isDeltaTime(ch)) {
                yData = lapDists.map(function(d) {
                  var currentLapSegTime = interpolateTimeAtDist(points, d) - lapT_at_start;
                  var refSegTime = interpolateTimeAtDist(refPoints, d) - refT_at_start;
                  return Math.round((currentLapSegTime - refSegTime) * 1000) / 1000;
                });
              }

              yData.forEach(function(v) {
                if (v !== null && v !== undefined && !isNaN(v)) {
                  if (v < channelMinMax[ch].min) channelMinMax[ch].min = v;
                  if (v > channelMinMax[ch].max) channelMinMax[ch].max = v;
                }
              });

              traces.push({
                x: lapDists,
                y: yData,
                name: lapLabel,
                legendgroup: lapKey,
                showlegend: chIdx === 0,
                xaxis: 'x',
                yaxis: 'y' + axisSuffix,
                type: 'scatter',
                mode: 'lines',
                line: lineConfig,
                hovertemplate: ch + ': %{y:.2f}<br>Dist: %{x:.1f}m<extra>' + lapLabel + '</extra>'
              });
            });
          });

          // Text Annotations
          annotations.forEach(function(ann) {
            var annDist = ann.distance_m !== undefined ? ann.distance_m : 0;
            var targetCh = (ann.channel || channels[0] || '').toLowerCase().trim();
            var chIdx = -1;
            for (var c = 0; c < channels.length; c++) {
              var normC = (channels[c] || '').toLowerCase().trim();
              if (normC === targetCh || normC.indexOf(targetCh) !== -1 || targetCh.indexOf(normC) !== -1) {
                chIdx = c;
                break;
              }
            }
            if (chIdx === -1) chIdx = 0;
            var yAxisRef = chIdx === 0 ? 'y' : 'y' + (chIdx + 1);

            var annObj = {
              x: annDist,
              yref: yAxisRef,
              text: ann.text || '',
              showarrow: true,
              arrowhead: 2,
              arrowcolor: ann.color || '#38bdf8',
              font: { color: '#f8fafc', size: 11 },
              bgcolor: '#1e293b',
              bordercolor: ann.color || '#38bdf8',
              borderwidth: 1,
              borderpad: 4
            };

            if (ann.y !== undefined && ann.y !== null) {
              annObj.y = ann.y;
            }

            plotlyAnnotations.push(annObj);
          });

          // Layout grid configuration
          var plotHeight = options.height || (numChannels * 210);
          var layout = {
            paper_bgcolor: 'transparent',
            plot_bgcolor: '#0f172a',
            font: { family: 'Inter, system-ui, sans-serif', color: '#94a3b8' },
            grid: { rows: numChannels, columns: 1, pattern: 'coupled' },
            shapes: shapes,
            annotations: plotlyAnnotations,
            margin: { t: 30, b: 40, l: 60, r: 20 },
            hovermode: 'x unified',
            dragmode: 'pan',
            height: plotHeight,
            showlegend: true,
            legend: {
              orientation: 'h',
              y: 1.15,
              x: 0,
              font: { color: '#f8fafc', size: 11 },
              bgcolor: 'rgba(15, 23, 42, 0.8)'
            },
            xaxis: {
              title: { text: 'Distance (m)', font: { size: 12, color: '#94a3b8' } },
              range: [startM, endM],
              gridcolor: '#334155',
              zerolinecolor: '#475569',
              fixedrange: false
            }
          };

          // Y-Axes configuration
          channels.forEach(function(ch, chIdx) {
            var yKey = chIdx === 0 ? 'yaxis' : 'yaxis' + (chIdx + 1);
            var normCh = ch.toLowerCase().trim();
            var titleText = ch;
            if (normCh === 'speed') titleText = 'Speed (km/h)';
            else if (normCh === 'brake') titleText = 'Brake (%)';
            else if (normCh === 'throttle') titleText = 'Throttle (%)';
            else if (normCh === 'steering' || normCh === 'steering angle') titleText = 'Steering Angle (deg)';
            else if (isDeltaTime(ch)) titleText = 'Delta Time (s)';

            var mm = channelMinMax[ch];
            var yConfig = {
              title: { text: titleText, font: { size: 10, color: '#cbd5e1' } },
              gridcolor: '#1e293b',
              zerolinecolor: '#334155',
              fixedrange: true
            };

            if (normCh === 'speed' && mm && isFinite(mm.min) && isFinite(mm.max)) {
              var span = mm.max - mm.min;
              var pad = Math.max(1.0, span * 0.1);
              yConfig.range = [Math.floor(mm.min - pad), Math.ceil(mm.max + pad)];
              yConfig.autorange = false;
            } else if (isDeltaTime(ch) && mm && isFinite(mm.min) && isFinite(mm.max)) {
              var dtMin = Math.min(0, mm.min);
              var dtMax = Math.max(0.05, mm.max);
              var pad = (dtMax - dtMin) * 0.15;
              yConfig.range = [dtMin - pad, dtMax + pad];
              yConfig.autorange = false;
            }

            layout[yKey] = yConfig;
          });

          var config = {
            responsive: true,
            displayModeBar: false,
            scrollZoom: true
          };

          return Plotly.newPlot(targetElem, traces, layout, config);
        }).catch(function(err) {
          var apiBase = options.api_base || '';
          var authUrl = apiBase ? (apiBase + '/auth') : '/auth';

          if (err.isAuthError || err.status === 401 || err.status === 403) {
            targetElem.innerHTML =
              '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:360px;padding:2rem;background:#0f172a;border:1px solid #334155;border-radius:12px;text-align:center;color:#f8fafc;font-family:Inter,system-ui,sans-serif;">' +
                '<svg style="width:48px;height:48px;fill:#f59e0b;margin-bottom:1rem;" viewBox="0 0 24 24">' +
                  '<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/>' +
                '</svg>' +
                '<h3 style="margin-bottom:0.5rem;font-size:1.15rem;font-weight:700;color:#f8fafc;">Authentication Required</h3>' +
                '<p style="margin-bottom:1.25rem;font-size:0.9rem;color:#94a3b8;max-width:440px;line-height:1.5;">' +
                  'Access to telemetry data is restricted. Please authenticate with <strong>brbrdb</strong> to view interactive telemetry plots.' +
                '</p>' +
                '<div style="display:flex;gap:0.75rem;align-items:center;flex-wrap:wrap;justify-content:center;">' +
                  '<a href="' + authUrl + '" target="_blank" rel="noopener noreferrer" style="display:inline-flex;align-items:center;gap:0.5rem;padding:0.6rem 1.25rem;background:#38bdf8;color:#0f172a;font-weight:700;font-size:0.9rem;border-radius:8px;text-decoration:none;transition:all 0.2s;">' +
                    'Log In on brbrdb ↗' +
                  '</a>' +
                  '<button class="telemetry-retry-btn" style="padding:0.6rem 1.25rem;background:#1e293b;color:#f8fafc;border:1px solid #475569;font-weight:600;font-size:0.9rem;border-radius:8px;cursor:pointer;">' +
                    '↻ Retry' +
                  '</button>' +
                '</div>' +
              '</div>';

            var retryBtn = targetElem.querySelector('.telemetry-retry-btn');
            if (retryBtn) {
              retryBtn.addEventListener('click', function() {
                self.render(container, options);
              });
            }
          } else {
            targetElem.innerHTML =
              '<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:300px;padding:2rem;color:#f43f5e;font-family:Inter,sans-serif;text-align:center;">' +
                '<div style="font-weight:700;font-size:1.05rem;margin-bottom:0.5rem;">Failed to load telemetry plot</div>' +
                '<div style="font-size:0.85rem;color:#94a3b8;">' + (err.message || 'Unknown error') + '</div>' +
              '</div>';
          }
          throw err;
        });
      });
    }
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { TelemetryPlot: TelemetryPlot };
  }
  global.TelemetryPlot = TelemetryPlot;
})(typeof window !== 'undefined' ? window : (typeof global !== 'undefined' ? global : this));
