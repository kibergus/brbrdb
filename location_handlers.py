# Copyright 2026 Alexey Guseynov (kibergus). All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

import os
import csv
import logging
import gzip
import brotli  # type: ignore[import-untyped]
from typing import Any
from flask import Blueprint, render_template, abort, request, jsonify, Response, url_for

from database import db, config
import plot_handlers
from race_tools import sanitize
import pandas as pd
import numpy as np
import auth
import struct
import report_parser


location_blueprint = Blueprint('location', __name__)
logger = logging.getLogger(__name__)


def _parse_lap_time(seconds: float) -> str:
    """Format seconds into M:SS.mmm format."""
    if not seconds or seconds <= 0:
        return 'Unknown'
    total_ms = round(seconds * 1000)
    if total_ms <= 0:
        return 'Unknown'
    m, rem_ms = divmod(total_ms, 60000)
    s, ms = divmod(rem_ms, 1000)
    if m > 0:
        return f'{m}:{s:02d}.{ms:03d}'
    return f'{s}.{ms:03d}'


def get_time_at_distance(points: list[dict], distance: float) -> float | None:
    """
    Interpolate time at a given distance from a list of points.

    Each point dict must contain:
    - 'dist': float (absolute distance in meters)
    - 'time': float (absolute time in seconds)
    """
    if not points:
        return None

    # Use numpy for clean linear interpolation. np.interp handles edge cases and sorting.
    dists = [p['dist'] for p in points]
    times = [p['time'] for p in points]
    return float(np.interp(distance, dists, times))


def parse_telemetry_csv(csv_path: str) -> tuple[list, list[str], str | None]:
    """
    Parse pre-generated RaceBox CSV file into laps and columns.
    Returns:
        (list, list[str], str|None): A tuple containing:
            - A list of dictionaries, each representing a lap with metadata.
            - A list of available columns.
            - The driver name extracted from the 'Configuration' metadata header.
    """
    session_laps: dict[int, dict] = {}  # lap_num -> list of points metadata
    columns = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)

        # Find the blank line separator between metadata and data
        blank_idx = -1
        for i, row in enumerate(rows):
            if not row or all(cell.strip() == '' for cell in row):
                blank_idx = i
                break

        if blank_idx == -1 or blank_idx >= len(rows) - 1:
            return [], [], None

        metadata_rows = rows[:blank_idx]
        csv_rows = rows[blank_idx+1:]

        driver_name = None
        for row in metadata_rows:
            if row and len(row) >= 2 and row[0] in ('Driver name', 'Configuration'):
                driver_name = row[1]
                break

        # The row immediately following the empty line is the column names (headers).
        header = csv_rows[0]
        columns = [col.strip() for col in header]
        data_rows = csv_rows[1:]

        time_idx = header.index('Time') if 'Time' in header else -1
        lat_idx = header.index('Latitude') if 'Latitude' in header else -1
        lon_idx = header.index('Longitude') if 'Longitude' in header else -1
        lap_idx = header.index('Lap') if 'Lap' in header else -1

        dist_col_indices = []
        for dist_col in ['Lap Distance (m)', 'Lap Distance']:
            if dist_col in header:
                dist_col_indices.append(header.index(dist_col))

        idx = 0
        for row in data_rows:
            if not row or len(row) < len(header):
                continue
            try:
                timestamp_str = row[time_idx] if time_idx >= 0 else None
                lat_val = row[lat_idx] if lat_idx >= 0 else None
                lon_val = row[lon_idx] if lon_idx >= 0 else None
                lap_val = row[lap_idx] if lap_idx >= 0 else None
                if not timestamp_str or lat_val is None or lon_val is None or lap_val is None:
                    continue

                # Check that coordinates are numbers.
                float(lat_val)
                float(lon_val)
                lap_num = int(lap_val)

                if lap_num not in session_laps:
                    session_laps[lap_num] = {
                        'lap_num': lap_num,
                        'start_idx': idx,
                        'end_idx': idx,
                        'times': [],
                        'dists': []
                    }

                session_laps[lap_num]['end_idx'] = idx

                t_part = timestamp_str.split('T')[1].replace('Z', '')
                h, m, s = t_part.split(':')
                total_sec = int(h) * 3600 + int(m) * 60 + float(s)
                session_laps[lap_num]['times'].append(total_sec)

                # Distance
                dist_val = None
                for dist_col_idx in dist_col_indices:
                    val_str = row[dist_col_idx]
                    if val_str != '':
                        try:
                            dist_val = float(val_str)
                            break
                        except ValueError:
                            pass
                if dist_val is not None:
                    session_laps[lap_num]['dists'].append(dist_val)

                idx += 1
            except (ValueError, KeyError, IndexError):
                continue

    laps_list = []
    sorted_laps = sorted(session_laps.items())
    for i, (lap_num, data) in enumerate(sorted_laps):
        times = data['times']
        lap_time_str = 'Unknown'
        if i < len(sorted_laps) - 1:
            # Start-to-start duration (Method B): difference between next lap start time and current lap start time
            duration = sorted_laps[i + 1][1]['times'][0] - times[0]
        elif len(times) > 1:
            # Fallback for the final lap in the session where no next lap exists
            duration = times[-1] - times[0]
        else:
            duration = 0.0

        if duration > 0:
            lap_time_str = _parse_lap_time(duration)

        laps_list.append({
            'lap_num': lap_num,
            'lap_time': lap_time_str,
            'is_valid': True,
            'start_idx': data['start_idx'],
            'end_idx': data['end_idx'],
            'dists': data['dists'],
            'times': times,
            'duration': duration
        })
    return laps_list, columns, driver_name


def _render_telemetry_meeting(
    league: str,
    class_name: str,
    date: str,
    track: str,
    session_id: str | None = None,
    is_report_mode: bool = False,
    report_title: str | None = None,
    report_html: str | None = None,
    report_state: dict | None = None,
    report_name: str | None = None
) -> str:
    acl = auth.get_current_acl()
    if not acl.get('see_telemetry'):
        abort(403, description='Access to telemetry data is restricted')

    sessions = db.find_sessions(leagues=league, classes=class_name, date=date, track=track)
    if not sessions:
        abort(404)

    hero_names = plot_handlers.get_hero_names()
    df = db.load(leagues=league, classes=class_name, date=date, track=track, driver_names=tuple(hero_names))
    df_pen = db.load_penalties(
        leagues=league, classes=class_name, date=date, track=track, driver_names=tuple(hero_names)
    )
    plot_titles = plot_handlers.get_meeting_plot_titles(df, df_pen)

    all_meetings = db.list_meetings(league)
    other_classes = sorted(list(set(m[0] for m in all_meetings if m[1] == date and m[2] == track)))

    # Find previous/next chronological meetings for the current class in this league
    meetings_for_class = db.list_meetings(league, class_name=class_name)
    meetings_for_class.sort(key=lambda m: m[1])

    current_idx = -1
    for i, m in enumerate(meetings_for_class):
        if m[1] == date and m[2] == track:
            current_idx = i
            break

    prev_meeting = meetings_for_class[current_idx - 1] if current_idx > 0 else None
    next_meeting = meetings_for_class[current_idx + 1] if current_idx < len(meetings_for_class) - 1 else None

    prev_meeting_url = url_for(
        'location.telemetry_view',
        league=league,
        class_name=class_name,
        date=prev_meeting[1],
        track=prev_meeting[2]
    ) if prev_meeting else ''

    next_meeting_url = url_for(
        'location.telemetry_view',
        league=league,
        class_name=class_name,
        date=next_meeting[1],
        track=next_meeting[2]
    ) if next_meeting else ''

    prev_meeting_title = f'{prev_meeting[1]} - {prev_meeting[2]}' if prev_meeting else ''
    next_meeting_title = f'{next_meeting[1]} - {next_meeting[2]}' if next_meeting else ''

    google_maps_api_key = config.get('google_maps_api_key', '')

    # Check if this meeting folder has telemetry
    meeting_dir = sessions[0].meeting_dir
    has_telemetry = db.has_telemetry(meeting_dir)
    telemetry_dir = os.path.join(meeting_dir, 'telemetry')
    telemetry_sessions = []
    if has_telemetry:
        csv_files = [f for f in os.listdir(telemetry_dir) if f.endswith('.csv')]
        seen_sids = set()
        for csv_file in csv_files:
            sid = _match_session(csv_file, sessions)
            if sid:
                seen_sids.add(sid)
        for s in sessions:
            if s.session_id in seen_sids:
                telemetry_sessions.append(s)

    return render_template(
        'kartsim_meeting.html',
        league=league,
        class_name=class_name,
        date=date,
        track=track,
        sessions=sessions,
        telemetry_sessions=telemetry_sessions,
        selected_session_id=session_id,
        plot_titles=plot_titles,
        available_classes=other_classes,
        google_maps_api_key=google_maps_api_key,
        has_telemetry=has_telemetry,
        prev_meeting_url=prev_meeting_url,
        next_meeting_url=next_meeting_url,
        prev_meeting_title=prev_meeting_title,
        next_meeting_title=next_meeting_title,
        is_report_mode=is_report_mode,
        report_title=report_title,
        report_html=report_html,
        report_state=report_state or {},
        report_name=report_name
    )


@location_blueprint.route('/telemetry/<league>/<class_name>/<path:date>/<track>')
def telemetry_view(league: str, class_name: str, date: str, track: str) -> str:
    session_id = request.args.get('session_id')
    report_param = request.args.get('report')
    is_report_mode = False
    report_title = None
    report_html = None
    report_state = None

    if report_param:
        res = report_parser.load_report(report_param)
        if res:
            meta, state, body = res
            is_report_mode = True
            report_title = meta.get('title', report_param)
            report_html = body
            report_state = state
            if not session_id and meta.get('session_id'):
                session_id = meta.get('session_id')

    return _render_telemetry_meeting(
        league=league,
        class_name=class_name,
        date=date,
        track=track,
        session_id=session_id,
        is_report_mode=is_report_mode,
        report_title=report_title,
        report_html=report_html,
        report_state=report_state,
        report_name=report_param
    )


@location_blueprint.route('/telemetry/report/<path:report_name>')
def telemetry_report_view(report_name: str) -> str:
    res = report_parser.load_report(report_name)
    if not res:
        abort(404)

    meta, state, body = res
    league = meta.get('league')
    class_name = meta.get('class_name') or meta.get('class')
    date = meta.get('date')
    track = meta.get('track')
    session_id = request.args.get('session_id') or meta.get('session_id')

    if (
        not isinstance(league, str)
        or not isinstance(class_name, str)
        or not isinstance(date, str)
        or not isinstance(track, str)
    ):
        abort(400)

    report_title = meta.get('title', report_name)
    if not isinstance(report_title, str):
        report_title = str(report_name)

    return _render_telemetry_meeting(
        league=league,
        class_name=class_name,
        date=date,
        track=track,
        session_id=session_id,
        is_report_mode=True,
        report_title=report_title,
        report_html=body,
        report_state=state,
        report_name=report_name
    )


@location_blueprint.route('/telemetry/report_content/<path:report_name>')
def telemetry_report_content(report_name: str) -> Response:
    res = report_parser.load_report(report_name)
    if not res:
        abort(404)

    meta, state, body = res
    postmessage_script = """
<script>
  window.Telemetry = {
    jump: function(opts) {
      window.parent.postMessage(Object.assign({ type: 'telemetry_jump' }, opts || {}), '*');
    },
    setRange: function(startM, endM) {
      var r = (endM !== undefined) ? (startM + ',' + endM) : startM;
      window.parent.postMessage({ type: 'telemetry_jump', xlim: r }, '*');
    },
    focusMap: function(startM, endM) {
      var r = (endM !== undefined) ? (startM + ',' + endM) : startM;
      window.parent.postMessage({ type: 'telemetry_jump', mapRange: r }, '*');
    },
    focusRange: function(startM, endM) {
      var r = (endM !== undefined) ? (startM + ',' + endM) : startM;
      window.parent.postMessage({ type: 'telemetry_jump', focusRange: r }, '*');
    },
    togglePlot: function(plot) {
      window.parent.postMessage({ type: 'telemetry_jump', togglePlot: plot }, '*');
    },
    setPlots: function(plots) {
      window.parent.postMessage({
        type: 'telemetry_jump',
        plots: Array.isArray(plots) ? plots.join(',') : plots
      }, '*');
    },
    selectLaps: function(lapsA, lapsB) {
      window.parent.postMessage({
        type: 'telemetry_jump',
        lapsA: Array.isArray(lapsA) ? lapsA.join(',') : lapsA,
        lapsB: lapsB !== undefined ? (Array.isArray(lapsB) ? lapsB.join(',') : lapsB) : 'none'
      }, '*');
    }
  };

  document.addEventListener('click', function(e) {
    var sel = '[data-dist], [data-xlim], [data-range], [data-plot-range], [data-map-range], ' +
              '[data-map-focus], [data-center-map], [data-focus-map], [data-focus-range], [data-zoom-range], ' +
              '[data-turn], [data-laps], [data-laps-a], [data-laps-b], [data-tab], [data-rtab], ' +
              '[data-toggle-plot], [data-plots], [data-plot]';
    var trigger = e.target.closest(sel);
    if (!trigger) return;
    var mapRange = trigger.dataset.mapRange || trigger.dataset.mapFocus ||
                   trigger.dataset.centerMap || trigger.dataset.focusMap;
    window.parent.postMessage({
      type: 'telemetry_jump',
      dist: trigger.dataset.dist,
      xlim: trigger.dataset.xlim || trigger.dataset.range || trigger.dataset.plotRange,
      mapRange: mapRange,
      focusRange: trigger.dataset.focusRange || trigger.dataset.zoomRange,
      turn: trigger.dataset.turn,
      laps: trigger.dataset.laps,
      lapsA: trigger.dataset.lapsA,
      lapsB: trigger.dataset.lapsB,
      tab: trigger.dataset.tab,
      rtab: trigger.dataset.rtab,
      togglePlot: trigger.dataset.togglePlot,
      plots: trigger.dataset.plots || trigger.dataset.plot
    }, '*');
  });
</script>
"""
    if '<html' not in body.lower() or '<head' not in body.lower():
        title = meta.get('title', 'Telemetry Report')
        css_url = url_for('static', filename='css/telemetry_report.css')
        body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="{css_url}">
</head>
<body class="report-iframe-body">
  {body}
  {postmessage_script}
</body>
</html>"""
    else:
        if '</body>' in body:
            body = body.replace('</body>', f'{postmessage_script}</body>')
        else:
            body = body + postmessage_script

    return Response(body, mimetype='text/html')


def _get_display_name(csv_file: str) -> str:
    """Determine session name from filename: e.g. 14_59_practice_alice_driver.csv"""
    name_parts = csv_file.replace('.csv', '').split('_')
    if len(name_parts) >= 2:
        return f'{name_parts[0]}:{name_parts[1]} {name_parts[2].capitalize()}'
    return csv_file


def _match_session(csv_file: str, sessions: list) -> str | None:
    """Find the matching official session ID for this telemetry file."""
    matching_sid = None
    best_match_len = -1
    for s in sessions:
        # We look for the longest session_id that is a prefix of the telemetry filename.
        # To avoid partial matches (e.g. "p" matching "practice"), we check for an underscore.
        sid = getattr(s, 'session_id', None)
        if not isinstance(sid, str):
            continue
        if csv_file.startswith(sid) and len(sid) > best_match_len:
            if len(csv_file) == len(sid) or csv_file[len(sid)] in ('_', '.'):
                matching_sid = sid
                best_match_len = len(sid)
    return matching_sid


def _override_with_official_laps(
    laps: list, df_official: pd.DataFrame, matching_sid: str | None, driver_name: str | None
) -> None:
    """Override GPS-calculated lap times with official ones if available."""
    if not matching_sid or not driver_name or df_official.empty:
        return

    # Filter official lap times for this session and driver
    official_laps = df_official[
        (df_official['SessionID'] == matching_sid) &
        (df_official['Name'].str.lower() == driver_name.lower())
    ]

    if not official_laps.empty:
        # Create mapping of lap_num -> lap_time string
        # We use the raw 'LapTime' string if available, otherwise format 'LapTimeSeconds'
        lap_map = {}
        for _, row in official_laps.iterrows():
            l_num = int(row['Lap'])
            l_time = row.get('LapTime')
            # In rFactor 2 results, LapTimeDeleted=True means the lap was invalidated (e.g. cut)
            is_deleted = row.get('LapTimeDeleted', False)
            if pd.isna(is_deleted):
                is_deleted = False

            if pd.isna(l_time) or not l_time:
                l_time = _parse_lap_time(row['LapTimeSeconds'])
            lap_map[l_num] = {'time': str(l_time), 'is_valid': not is_deleted}

        # Override computed lap times and validity with official ones
        for lap in laps:
            l_num = lap['lap_num']
            if l_num in lap_map:
                lap['lap_time'] = lap_map[l_num]['time']
                lap['is_valid'] = lap_map[l_num]['is_valid']


def _compute_lap_segments(laps: list, sector_ends: list, turns: list) -> None:
    """Compute turn and sector times for each lap."""
    for lap in laps:
        dists = np.array(lap.get('dists', []))
        times = np.array(lap.get('times', []))
        if len(dists) == 0 or len(times) == 0:
            continue

        # Sector times
        lap['sector_times'] = []
        prev_dist = 0
        for s_end in sector_ends:
            t_start = float(np.interp(prev_dist, dists, times))
            t_end = float(np.interp(s_end, dists, times))
            lap['sector_times'].append(round(t_end - t_start, 3))
            prev_dist = s_end

        # Turn times
        lap['turn_times'] = []
        if turns:
            for i in range(len(turns)):
                turn = turns[i]
                prev_turn = turns[(i - 1 + len(turns)) % len(turns)]
                next_turn = turns[(i + 1) % len(turns)]

                # If turn 'start' exists, segment is [turn.start, next_turn.start] (straight after)
                # Otherwise, fallback to [prev_turn.end, turn.end] (straight before, backward compatible)
                turn_start = turn.get('start', prev_turn['end'])
                next_turn_start = next_turn.get('start', turn['end'])

                # Verify lap points actually cover the turn boundaries
                if next_turn_start > turn_start:
                    if dists[0] > turn_start + 5.0 or dists[-1] < next_turn_start - 5.0:
                        lap['turn_times'].append(0.0)
                        continue
                else:
                    # Wrap around finish line
                    if dists[-1] < turn_start - 5.0 or dists[0] > next_turn_start + 5.0:
                        lap['turn_times'].append(0.0)
                        continue

                t_start = float(np.interp(turn_start, dists, times))
                t_end = float(np.interp(next_turn_start, dists, times))

                dt = t_end - t_start
                if dt < 0:
                    # Wrap around finish line
                    duration = lap.get('duration') if lap.get('duration') is not None else (times[-1] - times[0])
                    dt += duration
                lap['turn_times'].append(round(dt, 3))


@location_blueprint.route('/api/telemetry')
def get_track_points() -> Response | tuple[Response, int]:
    # Check see_telemetry permission
    acl = auth.get_current_acl()
    if not acl.get('see_telemetry'):
        return jsonify({'error': 'Access to the telemetry data is restricted'}), 403

    league = request.args.get('league')
    class_name = request.args.get('class_name')
    date = request.args.get('date')
    track = request.args.get('track')
    filter_session_id = request.args.get('session_id')

    if not league or not class_name or not date or not track:
        return jsonify({'error': 'Missing parameters'}), 400

    sessions = db.find_sessions(leagues=league, classes=class_name, date=date, track=track)
    if not sessions:
        return jsonify([])

    meeting_dir = sessions[0].meeting_dir
    telemetry_dir = os.path.join(meeting_dir, 'telemetry')

    if not os.path.exists(telemetry_dir):
        return jsonify([])

    all_session_data = []

    csv_files = sorted([f for f in os.listdir(telemetry_dir) if f.endswith('.csv')])

    hero_names = plot_handlers.get_hero_names()
    sanitized_heroes = [sanitize.sanitize_filename(h) for h in hero_names]

    df_official = db.load(leagues=league, classes=class_name, date=date, track=track)
    track_data = db.get_track(track) or {}

    sector_ends = track_data.get('sector_end', [])
    turns = track_data.get('turns', [])
    lap_length = track_data.get('lap_length')

    for csv_file in csv_files:
        if sanitized_heroes:
            # Check if any hero name is part of the filename
            if not any(h in csv_file.lower() for h in sanitized_heroes):
                continue

        matching_sid = _match_session(csv_file, sessions)
        if filter_session_id and filter_session_id != 'all':
            if matching_sid != filter_session_id and csv_file != filter_session_id:
                continue

        csv_path = os.path.join(telemetry_dir, csv_file)
        display_name = _get_display_name(csv_file)

        laps, columns, driver_name = parse_telemetry_csv(csv_path)
        if laps:
            _override_with_official_laps(laps, df_official, matching_sid, driver_name)

            # Mark laps as invalid based on distance (automatic check)
            for lap in laps:
                if lap_length:
                    dists = lap.get('dists', [])
                    if dists:
                        dist_covered = max(dists) - min(dists)
                        if dist_covered < lap_length * 0.9:
                            lap['is_valid'] = False

            _compute_lap_segments(laps, sector_ends, turns)

            # Clean up temporary arrays before JSON encoding to keep payload minimal
            for lap in laps:
                lap.pop('dists', None)
                lap.pop('times', None)
            all_session_data.append({
                'session_id': matching_sid or csv_file,
                'session_name': display_name,
                'columns': columns,
                'laps': laps
            })

    return jsonify(all_session_data)


def _should_smooth_channel(channel: str) -> bool:
    """
    Check if the channel name corresponds to g-force, slide, force, or tyre load.
    """
    smooth_columns = {
        # g-force
        "gforcelat", "gforcelon", "gforcevert",
        # slide
        "slide pct fl", "slide pct fr", "slide pct rl", "slide pct rr",
        # force
        "lat force fl", "lat force fr", "lat force rl", "lat force rr",
        "long force fl", "long force fr", "long force rl", "long force rr",
        # tyre load
        "tyre load fl", "tyre load fr", "tyre load rl", "tyre load rr"
    }
    return channel.lower() in smooth_columns


def smooth_telemetry_data(values: list[float]) -> list[float]:
    """
    Apply a 5-sample moving average smoothing window to the values.
    Uses a centered window of size 5 (index - 2 to index + 2).
    NaN values are ignored when computing the average in the window.
    If the original value at the index is NaN, it remains NaN.
    """
    n = len(values)
    smoothed = []
    for i in range(n):
        if np.isnan(values[i]):
            smoothed.append(np.nan)
            continue

        window_vals = []
        for k in range(-2, 3):
            idx = i + k
            if 0 <= idx < n:
                val = values[idx]
                if not np.isnan(val):
                    window_vals.append(val)

        if window_vals:
            smoothed.append(float(np.mean(window_vals)))
        else:
            smoothed.append(np.nan)
    return smoothed


@location_blueprint.route('/api/telemetry/channel')
def get_telemetry_channel() -> Response | tuple[Response, int]:
    # Check see_telemetry permission
    acl = auth.get_current_acl()
    if not acl.get('see_telemetry'):
        return jsonify({'error': 'Access to the telemetry data is restricted'}), 403

    league = request.args.get('league')
    class_name = request.args.get('class_name')
    date = request.args.get('date')
    track = request.args.get('track')
    session_id = request.args.get('session_id')
    channel = request.args.get('channel')

    if not league or not class_name or not date or not track or not session_id or not channel:
        return jsonify({'error': 'Missing parameters'}), 400

    sessions = db.find_sessions(leagues=league, classes=class_name, date=date, track=track)
    if not sessions:
        return jsonify({'error': 'Session not found'}), 404

    meeting_dir = sessions[0].meeting_dir
    telemetry_dir = os.path.join(meeting_dir, 'telemetry')
    if not os.path.exists(telemetry_dir):
        return jsonify({'error': 'Telemetry directory not found'}), 404

    # Validate that session_id matches a file in the telemetry directory (prevents directory traversal)
    try:
        csv_files = os.listdir(telemetry_dir)
    except OSError:
        return jsonify({'error': 'Failed to read telemetry directory'}), 500

    target_csv = None
    if session_id in csv_files:
        target_csv = session_id
    else:
        for csv_f in csv_files:
            if csv_f.endswith('.csv') and _match_session(csv_f, sessions) == session_id:
                target_csv = csv_f
                break

    if not target_csv:
        return jsonify({'error': 'Unauthorized or invalid session ID'}), 403

    csv_path = os.path.join(telemetry_dir, target_csv)

    values = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)

        blank_idx = -1
        for i, row in enumerate(rows):
            if not row or all(cell.strip() == '' for cell in row):
                blank_idx = i
                break

        if blank_idx == -1 or blank_idx >= len(rows) - 1:
            empty_header_bytes = struct.pack("<dd", 0.0, 1.0)
            return Response(empty_header_bytes, mimetype='application/octet-stream')

        csv_rows = rows[blank_idx+1:]
        header = csv_rows[0]
        data_rows = csv_rows[1:]

        time_idx = header.index('Time') if 'Time' in header else -1
        lat_idx = header.index('Latitude') if 'Latitude' in header else -1
        lon_idx = header.index('Longitude') if 'Longitude' in header else -1
        lap_idx = header.index('Lap') if 'Lap' in header else -1
        col_idx = header.index(channel) if channel in header else -1

        is_time = (channel == 'Time')
        is_speed = (channel.lower() in ('speed', 'speed (m/s)'))

        for row in data_rows:
            if not row or len(row) < len(header):
                continue

            # Filter rows exactly as parse_telemetry_csv does
            timestamp_str = row[time_idx] if time_idx >= 0 else None
            lat_val = row[lat_idx] if lat_idx >= 0 else None
            lon_val = row[lon_idx] if lon_idx >= 0 else None
            lap_val = row[lap_idx] if lap_idx >= 0 else None
            if not timestamp_str or lat_val is None or lon_val is None or lap_val is None:
                continue

            try:
                float(lat_val)
                float(lon_val)
                int(lap_val)
            except ValueError:
                continue

            if is_time:
                try:
                    t_part = timestamp_str.split('T')[1].replace('Z', '')
                    h, m, s = t_part.split(':')
                    total_sec = int(h) * 3600 + int(m) * 60 + float(s)
                    values.append(total_sec)
                except (ValueError, IndexError):
                    values.append(np.nan)
            elif col_idx != -1:
                val_str = row[col_idx].strip()
                if val_str == '':
                    values.append(np.nan)
                else:
                    try:
                        val = float(val_str)
                        if is_speed:
                            val *= 3.6
                        values.append(val)
                    except ValueError:
                        values.append(np.nan)
            else:
                values.append(np.nan)

    if league == 'kartsim' and _should_smooth_channel(channel):
        values = smooth_telemetry_data(values)

    # Convert values list to numpy array
    arr = np.array(values, dtype=np.float64)
    non_nan_mask = ~np.isnan(arr)

    if np.any(non_nan_mask):
        mean_val = float(np.mean(arr[non_nan_mask]))
        diffs = np.diff(arr[non_nan_mask])
        scale_base = np.max(np.abs(arr[non_nan_mask] - mean_val)) / 32760
        scale_delta = np.max(np.abs(diffs)) / 32760 if len(diffs) > 0 else 0.0
        scale = max(scale_base, scale_delta)
        if scale == 0:
            scale = 1.0
    else:
        mean_val = 0.0
        scale = 1.0

    # Quantize non-nan values
    quantized = np.zeros(len(arr), dtype=np.int64)
    if np.any(non_nan_mask):
        quantized[non_nan_mask] = np.round((arr[non_nan_mask] - mean_val) / scale).astype(np.int64)

    deltas = np.zeros(len(arr), dtype=np.int16)
    last_valid_q = 0
    has_seen_valid = False

    for i in range(len(arr)):
        if not non_nan_mask[i]:
            deltas[i] = -32768  # Sentinel for NaN / unset value
        else:
            q_val = quantized[i]
            if not has_seen_valid:
                deltas[i] = np.clip(q_val, -32767, 32767)
                has_seen_valid = True
            else:
                d = q_val - last_valid_q
                deltas[i] = np.clip(d, -32767, 32767)
            last_valid_q = q_val

    # Pack the binary payload
    header_bytes = struct.pack("<dd", mean_val, scale)
    body_bytes = deltas.tobytes()
    binary_payload = header_bytes + body_bytes

    # Compress if client supports brotli or gzip
    accept_encoding = request.headers.get('Accept-Encoding', '')
    if 'br' in accept_encoding:
        compressed_payload = brotli.compress(binary_payload)
        response = Response(compressed_payload, mimetype='application/octet-stream')
        response.headers['Content-Encoding'] = 'br'
        response.headers['Content-Length'] = str(len(compressed_payload))
        return response
    elif 'gzip' in accept_encoding:
        compressed_payload = gzip.compress(binary_payload)
        response = Response(compressed_payload, mimetype='application/octet-stream')
        response.headers['Content-Encoding'] = 'gzip'
        response.headers['Content-Length'] = str(len(compressed_payload))
        return response
    else:
        return Response(binary_payload, mimetype='application/octet-stream')


@location_blueprint.route('/api/track_data')
def get_track_data() -> Response | tuple[Response, int]:
    track_name = request.args.get('track')
    if not track_name:
        return jsonify({'error': 'Missing track parameter'}), 400

    data = db.get_track(track_name)
    if not data:
        return jsonify({'error': f'Track data not found for {track_name}'}), 404

    return jsonify(data)


@location_blueprint.route('/api/track_progression')
def get_track_progression() -> Response | tuple[Response, int]:
    league = request.args.get('league')
    class_name = request.args.get('class_name')
    track = request.args.get('track')
    date = request.args.get('date', '')

    if not league or not class_name or not track:
        return jsonify({'error': 'Missing required parameters'}), 400

    sessions = db.find_sessions(leagues=league, classes=class_name, track=track)
    recorded_conditions = set(s.track_conditions for s in sessions if s.track_conditions)

    def _condition_sort_key(c: str) -> tuple[int, str]:
        c_lower = c.lower().strip()
        if 'dry' in c_lower:
            rank = 0
        elif 'damp' in c_lower:
            rank = 1
        elif 'wet' in c_lower:
            rank = 2
        else:
            rank = 3
        return (rank, c)

    sorted_conditions = sorted(list(recorded_conditions), key=_condition_sort_key)

    plots: list[dict[str, Any]] = []
    if sorted_conditions:
        for cond in sorted_conditions:
            plot_url = url_for(
                'plots.track_plot',
                league=league,
                class_name=class_name,
                track=track,
                track_conditions=cond,
                v=date
            )
            plots.append({
                'condition': cond,
                'title': f'{cond} Conditions',
                'plot_url': plot_url
            })
    else:
        plot_url = url_for(
            'plots.track_plot',
            league=league,
            class_name=class_name,
            track=track,
            v=date
        )
        plots.append({
            'condition': None,
            'title': 'All Conditions',
            'plot_url': plot_url
        })

    return jsonify({'plots': plots})
