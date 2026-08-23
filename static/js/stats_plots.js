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
 * stats_plots.js - Unified core library for track minimaps, turn time distributions, and gap statistics.
 * Shared between the meeting dashboard (kartsim_meeting) and standalone coaching reports.
 */

// --- 1. Color Scale & Math Utilities ---

/**
 * Calculates the red threshold for the turn difference color scale.
 * Lowest power of 2 (minimum 0.5s) that is >= max gap.
 */
export function getRedThreshold(maxGap) {
  if (!maxGap || maxGap <= 0.5) return 0.5;
  var val = 0.5;
  while (val < maxGap) {
    val *= 2;
  }
  return val;
}

/**
 * Maps a time difference in seconds to a green-yellow-red hex color string based on redThreshold.
 */
export function getTurnDiffColor(diff, redThreshold) {
  if (diff === null || diff === undefined || isNaN(diff) || diff < 0) {
    diff = 0;
  }
  var maxVal = (redThreshold && redThreshold > 0) ? redThreshold : 1.0;
  var t = Math.min(Math.max(diff / maxVal, 0), 1.0);
  var r, g, b;
  if (t <= 0.5) {
    var ratio = t / 0.5;
    r = Math.round(34 + (234 - 34) * ratio);
    g = Math.round(197 + (179 - 197) * ratio);
    b = Math.round(94 + (8 - 94) * ratio);
  } else {
    var ratio2 = (t - 0.5) / 0.5;
    r = Math.round(234 + (239 - 234) * ratio2);
    g = Math.round(179 + (68 - 179) * ratio2);
    b = Math.round(8 + (68 - 8) * ratio2);
  }
  var toHex = function(n) { return n.toString(16).padStart(2, '0'); };
  return '#' + toHex(r) + toHex(g) + toHex(b);
}

/**
 * Computes mean - min time difference for each turn based on Group B laps.
 */
export function computeGroupBTurnDiffs(laps, numTurns) {
  var diffs = new Array(numTurns).fill(0);
  if (!laps || laps.length === 0 || !numTurns) return diffs;

  var groupBLaps = laps.filter(function(l) { return l.group === 'B' && l.is_valid !== false; });
  if (groupBLaps.length === 0) return diffs;

  for (var turnIdx = 0; turnIdx < numTurns; turnIdx++) {
    var turnTimes = groupBLaps
      .map(function(l) { return (l.turn_times && l.turn_times[turnIdx] > 0.5) ? l.turn_times[turnIdx] : null; })
      .filter(function(t) { return t !== null; });

    if (turnTimes.length > 0) {
      var minTime = Math.min.apply(null, turnTimes);
      var inRange = turnTimes.filter(function(t) { return t <= minTime + 2.0; });
      var validSubset = inRange.length > 0 ? inRange : turnTimes;
      var meanTime = validSubset.reduce(function(a, b) { return a + b; }, 0) / validSubset.length;
      diffs[turnIdx] = Math.max(0, meanTime - minTime);
    }
  }
  return diffs;
}

// --- 2. Data Fetching Utilities ---

export function fetchTrackData(options) {
  var track = options.track;
  var apiBase = options.api_base || '';
  if (!track) return Promise.reject(new Error('Missing required parameter: track'));

  var url = apiBase + '/api/track_data?track=' + encodeURIComponent(track);
  return fetch(url, { credentials: 'include' })
    .then(function(res) {
      if (!res.ok) throw new Error('Failed to fetch track data: ' + res.status);
      return res.json();
    });
}

export function fetchTelemetrySessions(options) {
  var league = options.league || 'club100_south';
  var className = options.class_name || 'cadet_lw';
  var track = options.track;
  var date = options.date;
  var apiBase = options.api_base || '';

  if (!track || !date) {
    return Promise.reject(new Error('Missing required parameters: track and date'));
  }

  var url = apiBase + '/api/telemetry?league=' + encodeURIComponent(league) +
            '&class_name=' + encodeURIComponent(className) +
            '&date=' + encodeURIComponent(date) +
            '&track=' + encodeURIComponent(track);

  return fetch(url, { credentials: 'include' })
    .then(function(res) {
      if (!res.ok) throw new Error('Failed to fetch telemetry data: ' + res.status);
      return res.json();
    });
}

export function ensurePlotlyLoaded() {
  var glob = (typeof window !== 'undefined') ? window : globalThis;
  if (glob.Plotly) return Promise.resolve(glob.Plotly);

  return new Promise(function(resolve, reject) {
    var script = document.createElement('script');
    script.src = 'https://cdn.plot.ly/plotly-2.27.0.min.js';
    script.onload = function() { resolve(glob.Plotly); };
    script.onerror = function() { reject(new Error('Failed to load Plotly CDN')); };
    document.head.appendChild(script);
  });
}

// --- 3. Unified Track Minimap SVG Generator ---

/**
 * Generates an SVG string for track minimap geometry with optional turn highlighting or diff color coding.
 */
export function generateMinimapSvg(trackData, options) {
  options = options || {};
  var rawPoints = (trackData && trackData.center_line) ? trackData.center_line : [];
  var turns = (trackData && trackData.turns) ? trackData.turns : [];
  var numTurns = turns.length;

  if (rawPoints.length < 2 || numTurns === 0) {
    return '<div style="padding:1.5rem;text-align:center;color:#94a3b8;font-size:0.85rem;">No track geometry available</div>';
  }

  var points = rawPoints.map(function(p) {
    return {
      lat: p.lat,
      lon: p.lon !== undefined ? p.lon : (p.lng !== undefined ? p.lng : 0),
      dist: p.dist
    };
  }).filter(function(p) {
    return p.lat !== undefined && p.lon !== undefined;
  }).sort(function(a, b) {
    return a.dist - b.dist;
  });

  if (points.length < 2) {
    return '<div style="padding:1.5rem;text-align:center;color:#94a3b8;font-size:0.85rem;">No track geometry available</div>';
  }

  var latAvg = points.reduce(function(sum, p) { return sum + p.lat; }, 0) / points.length;
  var cosLat = Math.cos((latAvg * Math.PI) / 180);

  var projected = points.map(function(p) {
    return {
      x: p.lon * 111320 * cosLat,
      y: p.lat * 111320,
      dist: p.dist
    };
  });

  var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  projected.forEach(function(p) {
    if (p.x < minX) minX = p.x;
    if (p.x > maxX) maxX = p.x;
    if (p.y < minY) minY = p.y;
    if (p.y > maxY) maxY = p.y;
  });

  var widthM = maxX - minX;
  var heightM = maxY - minY;
  var centerX_m = (minX + maxX) / 2;
  var centerY_m = (minY + maxY) / 2;

  var padding = options.padding !== undefined ? options.padding : 18;
  var viewBoxW = options.width || 320;
  var availableW = Math.max(viewBoxW - 2 * padding, 10);
  var targetAspect = widthM / (heightM || 1);
  var availableH = Math.max(availableW / (targetAspect || 1), 80);
  var viewBoxH = options.height || Math.round(availableH + 2 * padding);

  var scale = Math.max(widthM / availableW, heightM / availableH, 0.00001);

  function toSvgCoords(x_m, y_m) {
    var svgX = (viewBoxW / 2) + (x_m - centerX_m) / scale;
    var svgY = (viewBoxH / 2) - (y_m - centerY_m) / scale;
    return { x: svgX, y: svgY };
  }

  var svgPoints = projected.map(function(p) {
    var coords = toSvgCoords(p.x, p.y);
    return {
      dist: p.dist,
      x: p.x,
      y: p.y,
      svgX: coords.x,
      svgY: coords.y
    };
  });

  var lapLength = trackData.lap_length || svgPoints[svgPoints.length - 1].dist;

  function normDist(d) {
    var nd = d % lapLength;
    if (nd < 0) nd += lapLength;
    return nd;
  }

  function getPointAtDist(targetDist) {
    var dist = normDist(targetDist);
    for (var i = 0; i < svgPoints.length - 1; i++) {
      var p1 = svgPoints[i];
      var p2 = svgPoints[i + 1];
      if (dist >= p1.dist && dist <= p2.dist) {
        var frac = (p2.dist > p1.dist) ? (dist - p1.dist) / (p2.dist - p1.dist) : 0;
        return {
          svgX: p1.svgX + (p2.svgX - p1.svgX) * frac,
          svgY: p1.svgY + (p2.svgY - p1.svgY) * frac,
          dist: dist
        };
      }
    }
    if (dist >= svgPoints[svgPoints.length - 1].dist) return svgPoints[svgPoints.length - 1];
    return svgPoints[0];
  }

  function getPointsForRange(startDist, endDist) {
    var startPt = getPointAtDist(startDist);
    var endPt = getPointAtDist(endDist);
    var result = [startPt];
    var sDist = normDist(startDist);
    var eDist = normDist(endDist);

    if (sDist <= eDist) {
      svgPoints.forEach(function(p) {
        if (p.dist > sDist && p.dist < eDist) result.push(p);
      });
    } else {
      svgPoints.forEach(function(p) { if (p.dist > sDist) result.push(p); });
      svgPoints.forEach(function(p) { if (p.dist < eDist) result.push(p); });
    }
    result.push(endPt);
    return result;
  }

  // Determine highlighted turn index if provided
  var highlightTurnIdx = -1;
  if (options.highlight_turn !== undefined && options.highlight_turn !== null) {
    if (typeof options.highlight_turn === 'number') {
      highlightTurnIdx = options.highlight_turn - 1;
    } else {
      var searchTurn = String(options.highlight_turn).toLowerCase().replace(/^turn\s+/, '');
      for (var tIdx = 0; tIdx < turns.length; tIdx++) {
        var nameStr = String(turns[tIdx].name || '').toLowerCase().replace(/^turn\s+/, '');
        if (nameStr === searchTurn || nameStr.indexOf(searchTurn) !== -1 || searchTurn.indexOf(nameStr) !== -1) {
          highlightTurnIdx = tIdx;
          break;
        }
      }
    }
  }

  var turnDiffs = options.turn_diffs || null;
  var redThreshold = options.red_threshold || (turnDiffs ? getRedThreshold(Math.max.apply(null, turnDiffs)) : 1.0);
  var baseColor = options.base_color || '#475569';
  var underlayColor = options.underlay_color || '#cbd5e1';
  var highlightColor = options.highlight_color || '#38bdf8';
  var showLabels = options.show_labels !== false;
  var showTicks = options.show_ticks !== false;

  var underlaysSvg = '';
  var centerlinesSvg = '';
  var ticksSvg = '';
  var labelsSvg = '';
  var highlightGlowSvg = '';

  turns.forEach(function(turn, i) {
    var nextTurn = turns[(i + 1) % numTurns];
    var turnStart = turn.start !== undefined ? turn.start : 0;
    var turnEnd = turn.end !== undefined ? turn.end : turnStart;
    var segmentEnd = nextTurn.start !== undefined ? nextTurn.start : turnEnd;

    var isHighlighted = (i === highlightTurnIdx);
    var color = isHighlighted ? highlightColor : (turnDiffs ? getTurnDiffColor(turnDiffs[i], redThreshold) : baseColor);
    var lineWidth = isHighlighted ? 6.0 : (turnDiffs ? 4.5 : 4.0);

    // 1. Turn Underlays
    if (turnStart !== turnEnd) {
      var turnPts = getPointsForRange(turnStart, turnEnd);
      var dStr = 'M ' + turnPts.map(function(p) { return p.svgX.toFixed(1) + ',' + p.svgY.toFixed(1); }).join(' L ');
      var uColor = isHighlighted ? highlightColor : underlayColor;
      var uOpacity = isHighlighted ? '0.35' : '0.9';
      var uWidth = isHighlighted ? '14' : '9';
      underlaysSvg += '<path d="' + dStr + '" stroke="' + uColor + '" stroke-width="' + uWidth + '" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity="' + uOpacity + '" />';

      if (isHighlighted) {
        highlightGlowSvg += '<path d="' + dStr + '" stroke="' + highlightColor + '" stroke-width="22" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity="0.15" filter="url(#glow)" />';
      }
    }

    // 2. Centerline
    var segPts = getPointsForRange(turnStart, segmentEnd);
    var segDStr = 'M ' + segPts.map(function(p) { return p.svgX.toFixed(1) + ',' + p.svgY.toFixed(1); }).join(' L ');
    centerlinesSvg += '<path d="' + segDStr + '" stroke="' + color + '" stroke-width="' + lineWidth + '" stroke-linecap="round" stroke-linejoin="round" fill="none" />';

    // 3. Boundary Ticks
    if (showTicks) {
      [turnStart, turnEnd].forEach(function(bDist) {
        var pt = getPointAtDist(bDist);
        var pA = getPointAtDist(bDist - 0.5);
        var pB = getPointAtDist(bDist + 0.5);
        var tx = pB.svgX - pA.svgX;
        var ty = pB.svgY - pA.svgY;
        var tLen = Math.sqrt(tx * tx + ty * ty);
        if (tLen > 0) {
          var nx = -ty / tLen;
          var ny = tx / tLen;
          var x1 = (pt.svgX - nx * 5.5).toFixed(1);
          var y1 = (pt.svgY - ny * 5.5).toFixed(1);
          var x2 = (pt.svgX + nx * 5.5).toFixed(1);
          var y2 = (pt.svgY + ny * 5.5).toFixed(1);
          var tickColor = isHighlighted ? highlightColor : '#cbd5e1';
          ticksSvg += '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 + '" stroke="' + tickColor + '" stroke-width="2" opacity="0.9" />';
        }
      });
    }

    // 4. Labels
    if (showLabels) {
      var apexDist = (turn.apex && turn.apex.length > 0) ? turn.apex[0] : (turnStart + turnEnd) / 2;
      var apexPt = getPointAtDist(apexDist);
      var vx = apexPt.svgX - (viewBoxW / 2);
      var vy = apexPt.svgY - (viewBoxH / 2);
      var vLen = Math.sqrt(vx * vx + vy * vy);
      var ux = vLen > 0 ? (vx / vLen) : 0;
      var uy = vLen > 0 ? (vy / vLen) : -1;

      var offsetDist = isHighlighted ? 22 : 18;
      var lblX = (apexPt.svgX + ux * offsetDist).toFixed(1);
      var lblY = (apexPt.svgY + uy * offsetDist).toFixed(1);
      var turnName = turn.name || ('T' + (i + 1));
      var textColor = isHighlighted ? highlightColor : (turnDiffs ? color : '#e2e8f0');
      var fontSize = isHighlighted ? '13' : '11';
      var fontWeight = isHighlighted ? '900' : '800';

      labelsSvg += '<text x="' + lblX + '" y="' + lblY + '" fill="' + textColor + '" font-size="' + fontSize + '" font-weight="' + fontWeight + '" text-anchor="middle" dominant-baseline="central" style="paint-order: stroke fill; stroke: #000000; stroke-width: 3.5px; stroke-linejoin: round; text-shadow: 0 0 6px #000000, 0 0 10px #000000;">' + turnName + '</text>';
    }
  });

  var maxHeightStyle = options.max_height ? 'max-height: ' + options.max_height + ';' : '';
  var titleHtml = options.title ? '<div style="flex-shrink: 0; font-size: 0.85rem; font-weight: 700; color: #94a3b8; margin-bottom: 0.4rem; text-align: center;">' + options.title + '</div>' : '';

  var legendHtml = '';
  if (turnDiffs && options.show_legend !== false) {
    legendHtml =
      '<div style="flex-shrink: 0; display: flex; align-items: center; justify-content: center; gap: 0.6rem; font-size: 0.75rem; color: #94a3b8; margin-top: 0.4rem;">' +
        '<span>0s (min)</span>' +
        '<div style="width: 80px; height: 6px; border-radius: 3px; background: linear-gradient(to right, #22c55e, #eab308, #ef4444);"></div>' +
        '<span>' + redThreshold + 's+ (mean)</span>' +
      '</div>';
  }

  return (
    titleHtml +
    '<div style="flex: 1; min-height: 0; width: 100%; display: flex; align-items: center; justify-content: center;">' +
      '<svg viewBox="0 0 ' + viewBoxW + ' ' + viewBoxH + '" style="width: 100%; height: 100%; ' + maxHeightStyle + '">' +
        '<defs>' +
          '<filter id="glow" x="-20%" y="-20%" width="140%" height="140%">' +
            '<feGaussianBlur stdDeviation="4" result="blur" />' +
            '<feComposite in="SourceGraphic" in2="blur" operator="over" />' +
          '</filter>' +
        '</defs>' +
        '<g id="minimap-glow">' + highlightGlowSvg + '</g>' +
        '<g id="minimap-underlines">' + underlaysSvg + '</g>' +
        '<g id="minimap-centerlines">' + centerlinesSvg + '</g>' +
        '<g id="minimap-ticks">' + ticksSvg + '</g>' +
        '<g id="minimap-labels">' + labelsSvg + '</g>' +
      '</svg>' +
    '</div>' +
    legendHtml
  );
}

// --- 4. TrackMinimap Module ---

export var TrackMinimap = {
  render: function(container, options) {
    options = options || {};
    var targetElem = typeof container === 'string' ? document.querySelector(container) : container;
    if (!targetElem) return Promise.reject(new Error('Container element not found: ' + container));

    if (options.trackData && (options.turn_diffs || !options.date)) {
      targetElem.innerHTML = generateMinimapSvg(options.trackData, options);
      return Promise.resolve();
    }

    targetElem.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;min-height:160px;color:#94a3b8;font-size:0.85rem;">Loading track map...</div>';

    var fetches = [
      options.trackData ? Promise.resolve(options.trackData) : fetchTrackData(options)
    ];

    if (options.color_by_time_loss !== false && options.date && options.track) {
      fetches.push(fetchTelemetrySessions(options));
    }

    return Promise.all(fetches).then(function(results) {
      var trackData = results[0];
      var sessions = results[1] || null;

      var svgOptions = Object.assign({}, options);
      if (sessions && !svgOptions.turn_diffs) {
        var numTurns = (trackData.turns || []).length;
        var validLaps = [];
        var filteredSessions = sessions;
        if (options.session_ids && options.session_ids.length > 0) {
          filteredSessions = sessions.filter(function(s) {
            return options.session_ids.some(function(id) {
              return s.session_id === id || s.session_id.indexOf(id) !== -1;
            });
          });
        }
        filteredSessions.forEach(function(s) {
          (s.laps || []).forEach(function(l) {
            if (l.is_valid !== false && l.turn_times) {
              validLaps.push(l);
            }
          });
        });

        if (validLaps.length > 0 && numTurns > 0) {
          var diffs = new Array(numTurns).fill(0);
          for (var tIdx = 0; tIdx < numTurns; tIdx++) {
            var times = validLaps
              .map(function(l) { return (l.turn_times && l.turn_times[tIdx] > 0.5) ? l.turn_times[tIdx] : null; })
              .filter(function(t) { return t !== null; });
            if (times.length > 0) {
              var minT = Math.min.apply(null, times);
              var inRangeT = times.filter(function(t) { return t <= minT + 2.0; });
              var validSubsetT = inRangeT.length > 0 ? inRangeT : times;
              var meanT = validSubsetT.reduce(function(a, b) { return a + b; }, 0) / validSubsetT.length;
              diffs[tIdx] = Math.max(0, meanT - minT);
            }
          }
          svgOptions.turn_diffs = diffs;
        }
      }

      targetElem.innerHTML = generateMinimapSvg(trackData, svgOptions);
    });
  }
};

// --- 5. StatsPlot Module ---

export var StatsPlot = {
  ensurePlotlyLoaded: ensurePlotlyLoaded,

  /**
   * Renders a single turn time distribution violin plot with outliers capped at 2.0s.
   */
  renderTurnViolin: function(container, options) {
    var targetElem = typeof container === 'string' ? document.querySelector(container) : container;
    if (!targetElem) return Promise.reject(new Error('Container element not found: ' + container));

    targetElem.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;min-height:160px;color:#94a3b8;font-size:0.85rem;">Loading turn distribution...</div>';

    return Promise.all([
      ensurePlotlyLoaded(),
      fetchTrackData(options),
      fetchTelemetrySessions(options)
    ]).then(function(results) {
      var Plotly = results[0];
      var trackData = results[1];
      var sessions = results[2] || [];

      targetElem.innerHTML = '';

      var turns = trackData.turns || [];
      var targetTurnIdx = -1;

      if (options.turn_index !== undefined && options.turn_index !== null) {
        targetTurnIdx = Number(options.turn_index) - 1;
      } else if (options.turn_name) {
        var searchName = String(options.turn_name).toLowerCase().replace(/^turn\s+/, '');
        for (var i = 0; i < turns.length; i++) {
          var tName = String(turns[i].name || '').toLowerCase().replace(/^turn\s+/, '');
          if (tName === searchName || tName.indexOf(searchName) !== -1 || searchName.indexOf(tName) !== -1) {
            targetTurnIdx = i;
            break;
          }
        }
      }
      if (targetTurnIdx < 0 || targetTurnIdx >= turns.length) targetTurnIdx = 0;

      var filteredSessions = sessions;
      if (options.session_ids && options.session_ids.length > 0) {
        filteredSessions = sessions.filter(function(s) {
          return options.session_ids.some(function(id) {
            return s.session_id === id || s.session_id.indexOf(id) !== -1;
          });
        });
      }

      var lapRecords = [];
      filteredSessions.forEach(function(s) {
        var sId = s.session_id;
        var sName = s.session_name || sId;
        (s.laps || []).forEach(function(l) {
          if (l.is_valid === false) return;
          var tTimes = l.turn_times || [];
          var tTime = tTimes[targetTurnIdx];
          if (tTime !== undefined && tTime !== null && tTime > 0.5) {
            lapRecords.push({
              session_id: sId,
              session_name: sName,
              lap: Number(l.lap_num),
              turn_time: Number(tTime),
              lap_time: l.lap_time
            });
          }
        });
      });

      if (lapRecords.length === 0) {
        targetElem.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#94a3b8;font-size:0.85rem;">No turn time data available</div>';
        return;
      }

      lapRecords.sort(function(a, b) { return a.turn_time - b.turn_time; });

      var highlightMap = {};
      (options.highlight_laps || []).forEach(function(hl) {
        var key = (hl.session_id ? hl.session_id + '_L' : '') + hl.lap;
        highlightMap[key] = hl;
        highlightMap[String(hl.lap)] = hl;
      });

      var minTurnTime = lapRecords[0].turn_time;
      var maxAllowedGap = 2.0;
      var maxAllowedTime = minTurnTime + maxAllowedGap;
      var outlierY = minTurnTime + 2.06;

      var inRangeY = [];
      var inRangeText = [];
      var inRangeColors = [];
      var inRangeSizes = [];

      var outlierYList = [];
      var outlierXList = [];
      var outlierText = [];
      var outlierColors = [];
      var outlierSizes = [];

      var defaultPointColor = options.default_point_color || '#64748b';
      var defaultPointSize = options.default_point_size || 5;

      lapRecords.forEach(function(rec) {
        var sKey = rec.session_id + '_L' + rec.lap;
        var hl = highlightMap[sKey] || highlightMap[String(rec.lap)];
        var color = hl ? (hl.color || '#38bdf8') : defaultPointColor;
        var size = hl ? (hl.size || 10) : defaultPointSize;
        var label = hl ? (hl.label || ('Lap ' + rec.lap + ' (' + rec.turn_time.toFixed(3) + 's)')) :
                         ('Lap ' + rec.lap + ' - ' + rec.turn_time.toFixed(3) + 's (' + rec.session_name + ')');

        if (rec.turn_time <= maxAllowedTime) {
          inRangeY.push(rec.turn_time);
          inRangeText.push(label);
          inRangeColors.push(color);
          inRangeSizes.push(size);
        } else {
          outlierYList.push(outlierY);
          outlierXList.push((Math.random() - 0.5) * 0.3);
          outlierText.push(label + ' [Outlier: +' + (rec.turn_time - minTurnTime).toFixed(3) + 's]');
          outlierColors.push(color);
          outlierSizes.push(Math.max(size, 6));
        }
      });

      var traces = [];
      if (inRangeY.length > 0) {
        traces.push({
          type: 'violin',
          y: inRangeY,
          box: { visible: false },
          line: { color: options.violin_color || '#38bdf8', width: 1.5 },
          fillcolor: options.violin_fill || 'rgba(56, 189, 248, 0.15)',
          meanline: { visible: true },
          points: 'all',
          jitter: 0.5,
          pointpos: 0,
          text: inRangeText,
          marker: { color: inRangeColors, size: inRangeSizes },
          hoverinfo: 'y+text',
          orientation: 'v',
          showlegend: false
        });
      }

      if (outlierYList.length > 0) {
        traces.push({
          type: 'scatter',
          mode: 'markers',
          y: outlierYList,
          x: outlierXList,
          text: outlierText,
          marker: { color: outlierColors, size: outlierSizes, symbol: 'circle' },
          hoverinfo: 'text',
          showlegend: false
        });
      }

      var layout = {
        autosize: true,
        height: options.height || 215,
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: '#e2e8f0', family: 'Inter, sans-serif' },
        margin: { l: 55, r: 15, t: 8, b: 18 },
        yaxis: {
          title: { text: options.y_title || 'Turn Time (s)', standoff: 8 },
          range: [minTurnTime - 0.05, minTurnTime + 2.12],
          autorange: false,
          gridcolor: 'rgba(255,255,255,0.1)',
          zerolinecolor: 'rgba(255,255,255,0.1)',
          tickformat: '.3f'
        },
        xaxis: { showticklabels: false, gridcolor: 'rgba(255,255,255,0.1)', range: [-0.6, 0.6] },
        showlegend: false,
        hovermode: 'closest'
      };

      return Plotly.newPlot(targetElem, traces, layout, { displayModeBar: false, responsive: true });
    });
  }
};

// Expose to global window / module exports
var glob = (typeof window !== 'undefined') ? window : (typeof global !== 'undefined' ? global : this);
if (glob) {
  glob.StatsPlot = StatsPlot;
  glob.TrackMinimap = TrackMinimap;
  glob.getRedThreshold = getRedThreshold;
  glob.getTurnDiffColor = getTurnDiffColor;
  glob.computeGroupBTurnDiffs = computeGroupBTurnDiffs;
  glob.generateMinimapSvg = generateMinimapSvg;
}
