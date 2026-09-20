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

"""Image generation tools for MCP Server (telemetry plots, trajectories, etc.)."""
from __future__ import annotations
import base64
import csv
import io
import math
import os
from typing import Any
import matplotlib
import numpy as np

from database import db
from mcp_server import lap_tools
import location_handlers

# Use non-interactive Agg backend to render images in headless server environment without GUI dependencies
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as ticker  # noqa: E402


def _parse_time_seconds(time_str: str) -> float:
    """Parse time string HH:MM:SS.sss or float string into total seconds."""
    if 'T' in time_str:
        t_part = time_str.split('T')[1].replace('Z', '')
        h, m, s = t_part.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    return float(time_str)


def find_telemetry_csv(session_id: str, track: str, date: str) -> str:
    """Locate the telemetry CSV path matching session_id, track, and date."""
    raw_sessions = db.find_sessions(date=date, track=track, session_id=session_id)
    for s in raw_sessions:
        tel_dir = os.path.join(s.meeting_dir, 'telemetry') if s.meeting_dir else ''
        if tel_dir and os.path.exists(tel_dir):
            csv_files = sorted(os.listdir(tel_dir))
            if session_id:
                for csv_file in csv_files:
                    if csv_file.endswith('.csv') and session_id in csv_file:
                        return os.path.join(tel_dir, csv_file)
            for csv_file in csv_files:
                if csv_file.endswith('.csv'):
                    return os.path.join(tel_dir, csv_file)
    raise ValueError(
        f"Telemetry file not found for session_id={session_id!r}, track={track!r}, date={date!r}"
    )


def extract_raw_lap_data(
    csv_path: str,
    laps: list[int],
    channels: list[str],
    parsed_laps: list[dict] | None = None
) -> dict[int, dict[str, list[float]]]:
    """Read CSV file and extract raw distance, time, and requested channel values per lap."""
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        rows = list(reader)

    blank_idx = -1
    for i, row in enumerate(rows):
        if not row or all(cell.strip() == '' for cell in row):
            blank_idx = i
            break

    if blank_idx == -1 or blank_idx >= len(rows) - 1:
        raise ValueError(f"Invalid telemetry CSV format in {csv_path}")

    csv_rows = rows[blank_idx + 1:]
    header = [c.strip() for c in csv_rows[0]]
    data_rows = csv_rows[1:]

    time_idx = header.index('Time') if 'Time' in header else -1
    lap_idx = header.index('Lap') if 'Lap' in header else -1

    dist_idx = -1
    for col_name in ['Lap Distance (m)', 'Lap Distance', 'LapDistance']:
        if col_name in header:
            dist_idx = header.index(col_name)
            break

    if dist_idx == -1:
        raise ValueError(f"Distance column not found in telemetry file {csv_path}")

    def find_header_col(name: str) -> str:
        for h in header:
            if h == name or h.lower() == name.lower():
                return h
        raise ValueError(f"Channel {name!r} not found in telemetry columns: {header}")

    def is_delta_time_ch(ch_name: str) -> bool:
        return ch_name.lower().replace('_', ' ').strip() == 'delta time'

    channel_col_map: dict[str, str] = {}
    for ch in channels:
        if not is_delta_time_ch(ch):
            channel_col_map[ch] = find_header_col(ch)

    if parsed_laps is None:
        parsed_laps, _, csv_driver = location_handlers.parse_telemetry_csv(csv_path)
        csv_file = os.path.basename(csv_path)
        tel_dir = os.path.dirname(csv_path)
        meeting_dir = os.path.dirname(tel_dir)
        sessions = [s for s in db.find_sessions() if s.meeting_dir == meeting_dir]
        if sessions:
            matching_sid = location_handlers._match_session(csv_file, sessions)
            if matching_sid:
                s0 = next((s for s in sessions if s.session_id == matching_sid), sessions[0])
                df_official = db.load(leagues=s0.league, classes=s0.class_name, date=s0.date, track=s0.track_name)
                location_handlers._override_with_official_laps(parsed_laps, df_official, matching_sid, csv_driver)

    norm_to_raw: dict[int, int] = {}
    raw_laps_present: set[int] = set()
    for plap in parsed_laps:
        norm_to_raw[plap['lap_num']] = plap.get('raw_lap_num', plap['lap_num'])
        if 'raw_lap_num' in plap:
            raw_laps_present.add(plap['raw_lap_num'])

    raw_to_requested: dict[int, list[int]] = {}
    for l_num in laps:
        if l_num in norm_to_raw:
            raw_target = norm_to_raw[l_num]
        elif l_num in raw_laps_present:
            raw_target = l_num
        else:
            raw_target = l_num
        raw_to_requested.setdefault(raw_target, []).append(l_num)

    raw_lap_data: dict[int, dict[str, list[float]]] = {
        l_num: {'dist': [], 'time': []} for l_num in laps
    }
    for ch in channels:
        if not is_delta_time_ch(ch):
            for l_num in laps:
                raw_lap_data[l_num][ch] = []

    for row in data_rows:
        if not row or len(row) < len(header):
            continue
        try:
            lap_val = int(row[lap_idx])
            if lap_val not in raw_to_requested:
                continue

            dist_val = float(row[dist_idx])
            time_str = row[time_idx]
            time_val = _parse_time_seconds(time_str)

            for req_l_num in raw_to_requested[lap_val]:
                raw_lap_data[req_l_num]['dist'].append(dist_val)
                raw_lap_data[req_l_num]['time'].append(time_val)

                for ch in channels:
                    if not is_delta_time_ch(ch):
                        c_idx = header.index(channel_col_map[ch])
                        val_str = row[c_idx].strip()
                        val = float(val_str) if val_str != '' else np.nan
                        raw_lap_data[req_l_num][ch].append(val)
        except (ValueError, IndexError):
            continue

    for l_num in laps:
        if not raw_lap_data[l_num]['dist']:
            raise ValueError(f"Lap {l_num} has no telemetry data in {csv_path}")

    return raw_lap_data


def interpolate_lap_channels(
    laps: list[int | str],
    channels: list[str],
    raw_lap_data: dict[int | str, dict[str, list[float]]],
    start_m: float,
    end_m: float
) -> tuple[np.ndarray, dict[int | str, dict[str, np.ndarray]]]:
    """Interpolate raw lap channels onto a uniform distance grid and compute Delta Time."""
    dists_grid = np.linspace(start_m, end_m, num=500)
    interp_lap_data: dict[int | str, dict[str, np.ndarray]] = {}

    def is_delta_time_ch(ch_name: str) -> bool:
        return ch_name.lower().replace('_', ' ').strip() == 'delta time'

    for l_num in laps:
        l_dist = np.array(raw_lap_data[l_num]['dist'])
        l_time = np.array(raw_lap_data[l_num]['time'])

        sort_order = np.argsort(l_dist)
        l_dist = l_dist[sort_order]
        l_time = l_time[sort_order]
        l_time = l_time - l_time[0]

        interp_time = np.interp(dists_grid, l_dist, l_time)
        interp_segment_time = interp_time - interp_time[0]
        interp_lap_data[l_num] = {'time': interp_segment_time}

        for ch in channels:
            if not is_delta_time_ch(ch):
                c_vals = np.array(raw_lap_data[l_num][ch])[sort_order]
                interp_lap_data[l_num][ch] = np.interp(dists_grid, l_dist, c_vals)

    delta_ch = next((ch for ch in channels if is_delta_time_ch(ch)), None)
    if delta_ch:
        ref_lap = laps[0]
        ref_time = interp_lap_data[ref_lap]['time']
        ref_segment_time = ref_time - ref_time[0]

        for l_num in laps:
            t = interp_lap_data[l_num]['time']
            l_segment_time = t - t[0]
            interp_lap_data[l_num][delta_ch] = l_segment_time - ref_segment_time

    return dists_grid, interp_lap_data


def render_stacked_telemetry_plot(
    session_id: str,
    laps: list[int | str],
    channels: list[str],
    dists_grid: np.ndarray,
    interp_lap_data: dict[int | str, dict[str, np.ndarray]],
    labels: dict[int | str, str] | None = None
) -> str:
    """Render matplotlib stacked comparison plot and return base64 encoded PNG string."""
    plt.style.use('dark_background')
    num_channels = len(channels)
    fig, axes = plt.subplots(
        num_channels, 1, figsize=(10, max(2.5 * num_channels, 4)), sharex=True
    )
    if num_channels == 1:
        axes = [axes]

    colors = ['#10b981', '#f43f5e', '#38bdf8', '#f59e0b', '#a855f7', '#ec4899', '#14b8a6']

    for idx, ch in enumerate(channels):
        ax = axes[idx]
        for lap_idx_i, l_num in enumerate(laps):
            color = colors[lap_idx_i % len(colors)]
            y_vals = interp_lap_data[l_num][ch]
            lap_lbl = labels.get(l_num) if labels else None
            if not lap_lbl:
                lap_lbl = f"Lap {l_num}" if (isinstance(l_num, int) or str(l_num).isdigit()) else str(l_num)
            ax.plot(dists_grid, y_vals, color=color, linewidth=2, label=lap_lbl)

        if 'speed' in ch.lower():
            all_s = [interp_lap_data[lp][ch] for lp in laps if ch in interp_lap_data[lp]]
            if all_s:
                min_s = float(np.nanmin([np.nanmin(s) for s in all_s]))
                max_s = float(np.nanmax([np.nanmax(s) for s in all_s]))
                pad = max(1.0, (max_s - min_s) * 0.1)
                ax.set_ylim(min_s - pad, max_s + pad)

        ax.set_ylabel(ch, fontsize=10)

        if idx == 0:
            ax.legend(loc='upper right', frameon=True, facecolor='#18181b', edgecolor='#27272a')
            ax.set_title(
                f"Telemetry Comparison - Session: {session_id}", fontsize=12, fontweight='bold'
            )

    dist_span = float(dists_grid[-1] - dists_grid[0]) if len(dists_grid) > 1 else 100.0
    if dist_span <= 150:
        major_step = 10.0
        minor_step = 2.0
    elif dist_span <= 500:
        major_step = 25.0
        minor_step = 5.0
    else:
        major_step = 50.0
        minor_step = 10.0

    for ax in axes:
        ax.xaxis.set_major_locator(ticker.MultipleLocator(major_step))
        ax.xaxis.set_minor_locator(ticker.MultipleLocator(minor_step))
        ax.grid(True, which='major', linestyle='--', alpha=0.4, color='#64748b')
        ax.grid(True, which='minor', linestyle=':', alpha=0.2, color='#475569')

    axes[-1].set_xlabel("Distance (m)", fontsize=10)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close(fig)

    return base64.b64encode(buf.getvalue()).decode('utf-8')


def parse_lap_spec(spec: Any) -> tuple[str, str, str, int]:
    """Parse lap specification tuple/list (date, track, session_id, lap) or dict."""
    if isinstance(spec, (list, tuple)) and len(spec) >= 4:
        return str(spec[0]), str(spec[1]), str(spec[2]), int(spec[3])
    elif isinstance(spec, dict):
        return str(spec['date']), str(spec['track']), str(spec['session_id']), int(spec.get('lap', 0))
    raise ValueError(f"Lap specification must be a tuple of (date, track, session_id, lap) or dict. Got: {spec!r}")


from matplotlib.collections import LineCollection  # noqa: E402

SPEED_COLOR_STOPS = [
    (0.00, (40, 20, 180)),   # Apex / Min speed: Deep Navy/Violet
    (0.25, (0, 200, 255)),   # Low speed / Exit: Cyan
    (0.55, (255, 30, 60)),   # Mid speed / Accel: Crimson Red
    (0.80, (255, 200, 0)),   # High speed: Gold Yellow
    (1.00, (0, 255, 80))     # Top speed: Neon Green
]

DELTA_T_COLOR_STOPS = [
    (0.0, (0, 255, 0)),     # -max_rate (gaining time): Pure Green
    (0.5, (255, 255, 0)),   # 0.0 (equal pace): Pure Yellow
    (1.0, (255, 0, 0))      # +max_rate (losing time): Pure Red
]


def _interpolate_delta_t_color(rate: float) -> tuple[float, float, float, float]:
    max_rate = 0.25
    s0 = 0.025
    asinh_max = math.asinh(max_rate / s0)
    norm_rate = max(-1.0, min(1.0, math.asinh(rate / s0) / asinh_max))
    val = 0.5 + 0.5 * norm_rate
    if val <= DELTA_T_COLOR_STOPS[0][0]:
        c = DELTA_T_COLOR_STOPS[0][1]
        return (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, 1.0)
    for i in range(len(DELTA_T_COLOR_STOPS) - 1):
        u1, c1 = DELTA_T_COLOR_STOPS[i]
        u2, c2 = DELTA_T_COLOR_STOPS[i + 1]
        if u1 <= val <= u2:
            f = (val - u1) / (u2 - u1 or 1.0)
            r = c1[0] + f * (c2[0] - c1[0])
            g = c1[1] + f * (c2[1] - c1[1])
            b = c1[2] + f * (c2[2] - c1[2])
            return (r / 255.0, g / 255.0, b / 255.0, 1.0)
    c = DELTA_T_COLOR_STOPS[-1][1]
    return (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, 1.0)


def _interpolate_speed_color(val: float) -> tuple[float, float, float, float]:
    val = max(0.0, min(1.0, float(val)))
    if val <= SPEED_COLOR_STOPS[0][0]:
        c = SPEED_COLOR_STOPS[0][1]
        return (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, 1.0)
    for i in range(len(SPEED_COLOR_STOPS) - 1):
        u1, c1 = SPEED_COLOR_STOPS[i]
        u2, c2 = SPEED_COLOR_STOPS[i + 1]
        if u1 <= val <= u2:
            f = (val - u1) / (u2 - u1 or 1.0)
            r = c1[0] + f * (c2[0] - c1[0])
            g = c1[1] + f * (c2[1] - c1[1])
            b = c1[2] + f * (c2[2] - c1[2])
            return (r / 255.0, g / 255.0, b / 255.0, 1.0)
    c = SPEED_COLOR_STOPS[-1][1]
    return (c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, 1.0)


def _get_segment_color(
    mode: str,
    speed: float,
    acc: float,
    throttle: float,
    brake: float,
    min_speed: float,
    max_speed: float,
    lap_color: str
) -> tuple[float, float, float, float] | str:
    if mode == 'speed':
        rng = max_speed - min_speed
        u = (speed - min_speed) / rng if rng > 0 else 0.5
        return _interpolate_speed_color(u)
    elif mode == 'accel':
        if acc < 0:
            factor = min(1.0, abs(acc) / 4.0)
            intensity = factor ** 0.5
            gb = 1.0 - intensity
            return (1.0, gb, gb, 1.0)
        elif acc > 0:
            factor = min(1.0, acc / 2.5)
            intensity = factor ** 0.5
            rb = 1.0 - intensity
            return (rb, 1.0, rb, 1.0)
        return (1.0, 1.0, 1.0, 1.0)
    elif mode == 'pedals':
        if brake > 1.0:
            factor = min(1.0, brake / 100.0)
            intensity = factor ** 0.5
            gb = 1.0 - intensity
            return (1.0, gb, gb, 1.0)
        elif throttle > 1.0:
            factor = min(1.0, throttle / 100.0)
            intensity = factor ** 0.5
            rb = 1.0 - intensity
            return (rb, 1.0, rb, 1.0)
        return (1.0, 1.0, 1.0, 1.0)
    return lap_color


def _latlon_to_mercator(lon: float, lat: float) -> tuple[float, float]:
    """Convert (lon, lat) in degrees to Web Mercator (EPSG:3857) (x, y) meters to preserve angles."""
    r = 6378137.0
    x = r * math.radians(lon)
    lat_clamped = min(max(lat, -89.5), 89.5)
    lat_rad = math.radians(lat_clamped)
    y = r * math.log(math.tan(math.pi / 4.0 + lat_rad / 2.0))
    return x, y


def _render_track_boundaries(ax: plt.Axes, track_name: str) -> bool:
    """Render track boundary lines from database GeoJSON metadata in Mercator projection."""
    track_data = db.get_track(track_name)
    if not track_data or 'geojson' not in track_data:
        return False

    geojson = track_data.get('geojson', {})
    features = geojson.get('features', []) if isinstance(geojson, dict) else []
    if not features:
        return False

    rendered_any = False
    for feat in features:
        if not isinstance(feat, dict):
            continue
        geom = feat.get('geometry', {})
        geom_type = geom.get('type')
        coords = geom.get('coordinates', [])

        if geom_type == 'LineString' and coords:
            m_pts = [_latlon_to_mercator(c[0], c[1]) for c in coords if len(c) >= 2]
            bx = [p[0] for p in m_pts]
            by = [p[1] for p in m_pts]
            if bx and by:
                ax.plot(bx, by, color='#94a3b8', linewidth=1.5, linestyle='-', alpha=0.85, zorder=1)
                rendered_any = True
        elif geom_type == 'MultiLineString' and coords:
            for line in coords:
                m_pts = [_latlon_to_mercator(c[0], c[1]) for c in line if len(c) >= 2]
                bx = [p[0] for p in m_pts]
                by = [p[1] for p in m_pts]
                if bx and by:
                    ax.plot(bx, by, color='#94a3b8', linewidth=1.5, linestyle='-', alpha=0.85, zorder=1)
                    rendered_any = True
        elif geom_type == 'Polygon' and coords:
            for ring in coords:
                m_pts = [_latlon_to_mercator(c[0], c[1]) for c in ring if len(c) >= 2]
                bx = [p[0] for p in m_pts]
                by = [p[1] for p in m_pts]
                if bx and by:
                    ax.plot(bx, by, color='#94a3b8', linewidth=1.5, linestyle='-', alpha=0.85, zorder=1)
                    rendered_any = True

    return rendered_any


def render_trajectory_plot(
    laps: list[tuple[str, str, str, int] | list[Any] | dict[str, Any]],
    start_m: float | None = None,
    end_m: float | None = None,
    color_mode: str = "lap"
) -> str:
    """Render matplotlib trajectory comparison plot (GPS or X/Z) for lap tuples (date, track, session_id, lap)."""
    if not laps:
        raise ValueError("laps parameter cannot be empty")

    color_mode = (color_mode or "lap").lower()
    if color_mode not in ("lap", "pedals", "accel", "speed", "delta_t"):
        color_mode = "lap"

    parsed_laps = [parse_lap_spec(item) for item in laps]
    first_date, first_track, first_sid, _ = parsed_laps[0]

    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(8, 8))
    colors = ['#10b981', '#f43f5e', '#38bdf8', '#f59e0b', '#a855f7', '#ec4899']

    has_boundaries = _render_track_boundaries(ax, first_track)
    full_track_pts: list[tuple[float, float]] = []
    all_lap_pts = []

    for idx_l, (date, track, session_id, lap_num) in enumerate(parsed_laps):
        csv_path = find_telemetry_csv(session_id, track, date)
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            rows = list(reader)

        blank_idx = -1
        for i, row in enumerate(rows):
            if not row or all(cell.strip() == '' for cell in row):
                blank_idx = i
                break

        if blank_idx == -1 or blank_idx >= len(rows) - 1:
            raise ValueError(f"Invalid telemetry CSV format in {csv_path}")

        csv_rows = rows[blank_idx + 1:]
        header = [c.strip() for c in csv_rows[0]]
        data_rows = csv_rows[1:]

        def find_col(*candidates: str) -> int:
            for cand in candidates:
                for idx, h in enumerate(header):
                    if h == cand or h.lower() == cand.lower():
                        return idx
            return -1

        lap_idx = find_col('Lap')
        dist_idx = find_col('Lap Distance', 'Lap Distance (m)', 'LapDistance')
        x_idx = find_col('Longitude', 'x')
        y_idx = find_col('Latitude', 'y', 'z')
        speed_idx = find_col('Speed')
        throttle_idx = find_col('Throttle')
        brake_idx = find_col('Brake')
        glon_idx = find_col('GForceLon')
        time_idx = find_col('Time')

        if lap_idx == -1 or dist_idx == -1 or x_idx == -1 or y_idx == -1:
            raise ValueError(f"Trajectory coordinates missing in {csv_path}")

        lap_pts: list[dict[str, float]] = []
        prev_speed = 0.0
        prev_time = 0.0

        for row in data_rows:
            if not row or len(row) < len(header):
                continue
            try:
                l_val = int(row[lap_idx])
                d_val = float(row[dist_idx])
                x_val = float(row[x_idx])
                y_val = float(row[y_idx])
                if -180.0 <= x_val <= 180.0 and -90.0 <= y_val <= 90.0:
                    x_val, y_val = _latlon_to_mercator(x_val, y_val)

                speed_val = float(row[speed_idx]) if speed_idx != -1 else 0.0
                if speed_val <= 50.0 and speed_val > 0.0:
                    speed_kmh = speed_val * 3.6
                else:
                    speed_kmh = speed_val

                throttle_val = float(row[throttle_idx]) if throttle_idx != -1 else 0.0
                brake_val = float(row[brake_idx]) if brake_idx != -1 else 0.0

                t_sec = _parse_time_seconds(row[time_idx]) if time_idx != -1 else 0.0
                if glon_idx != -1:
                    acc_val = float(row[glon_idx]) * 9.81
                else:
                    dt = t_sec - prev_time
                    dv = (speed_kmh / 3.6) - (prev_speed / 3.6)
                    acc_val = dv / dt if dt > 0.001 else 0.0
                    prev_speed = speed_kmh
                    prev_time = t_sec

                if not has_boundaries and idx_l == 0:
                    full_track_pts.append((x_val, y_val))

                if l_val == lap_num:
                    lap_pts.append({
                        'x': x_val,
                        'y': y_val,
                        'd': d_val,
                        'time': t_sec,
                        'speed': speed_kmh,
                        'acc': acc_val,
                        'throttle': throttle_val,
                        'brake': brake_val
                    })
            except (ValueError, IndexError):
                continue

        if not has_boundaries and full_track_pts and (start_m is not None or end_m is not None):
            fx = [p[0] for p in full_track_pts]
            fy = [p[1] for p in full_track_pts]
            ax.plot(fx, fy, color='#334155', linestyle='--', linewidth=1.5, alpha=0.6, label="Full Track")

        all_lap_pts.append((lap_num, session_id, colors[idx_l % len(colors)], lap_pts))

    # Global min/max speed across all rendered lap points
    speeds = [p['speed'] for _, _, _, pts in all_lap_pts for p in pts]
    min_speed = min(speeds) if speeds else 0.0
    max_speed = max(speeds) if speeds else 100.0

    ref_lap_pts = all_lap_pts[0][3] if all_lap_pts else []
    ref_dists = [p['d'] for p in ref_lap_pts]
    ref_times = [p['time'] for p in ref_lap_pts]

    for idx_l, (lap_num, session_id, lap_color, lap_pts) in enumerate(all_lap_pts):
        if not lap_pts:
            continue

        lx = [p['x'] for p in lap_pts]
        ly = [p['y'] for p in lap_pts]
        is_ref = (idx_l == 0)
        lap_label = f"Lap {lap_num} ({session_id})" + (" [Ref]" if (color_mode == 'delta_t' and is_ref) else "")

        if color_mode == 'lap' or len(lap_pts) < 2:
            ax.plot(lx, ly, color=lap_color, linewidth=2.5, label=lap_label, zorder=3)
        else:
            segments = []
            segment_colors: list[tuple[float, float, float, float] | str] = []
            lap_dists = [p['d'] for p in lap_pts]
            lap_times = [p['time'] for p in lap_pts]

            for i in range(len(lap_pts) - 1):
                p1 = lap_pts[i]
                p2 = lap_pts[i + 1]
                segments.append([[p1['x'], p1['y']], [p2['x'], p2['y']]])

                col: tuple[float, float, float, float] | str
                if color_mode == 'delta_t':
                    if is_ref:
                        col = (1.0, 1.0, 1.0, 1.0)
                    else:
                        s_mid = (p1['d'] + p2['d']) / 2.0
                        w_start = max(0.0, s_mid - 5.0)
                        w_end = s_mid + 5.0
                        t_lap1 = float(np.interp(w_start, lap_dists, lap_times))
                        t_lap2 = float(np.interp(w_end, lap_dists, lap_times))
                        t_ref1 = float(np.interp(w_start, ref_dists, ref_times))
                        t_ref2 = float(np.interp(w_end, ref_dists, ref_times))
                        dt_lap = t_lap2 - t_lap1
                        dt_ref = t_ref2 - t_ref1
                        if dt_ref > 0.0 and dt_lap > 0.0:
                            rate = (dt_lap - dt_ref) / dt_ref
                        else:
                            ref_s = float(np.interp(s_mid, ref_dists, [p['speed'] for p in ref_lap_pts]))
                            rate = (ref_s - p1['speed']) / p1['speed'] if p1['speed'] > 0 else 0.0
                        col = _interpolate_delta_t_color(rate)
                else:
                    col = _get_segment_color(
                        color_mode, p1['speed'], p1['acc'], p1['throttle'], p1['brake'],
                        min_speed, max_speed, lap_color
                    )
                segment_colors.append(col)

            lc_z = 2 if (color_mode == 'delta_t' and is_ref) else 3
            lc = LineCollection(segments, colors=segment_colors, linewidths=2.5, zorder=lc_z)
            ax.add_collection(lc)
            # Dummy line for legend entry
            legend_col = '#ffffff' if (color_mode == 'delta_t' and is_ref) else lap_color
            ax.plot([], [], color=legend_col, linewidth=2.5, label=lap_label)

        # Whole lap start and end markers (if not zoomed into a specific corner)
        if start_m is None and end_m is None:
            marker_col = '#ffffff' if (color_mode == 'delta_t' and is_ref) else lap_color
            ax.plot(lx[0], ly[0], marker='o', color=marker_col, markersize=6, zorder=4)
            ax.plot(lx[-1], ly[-1], marker='s', color=marker_col, markersize=6, zorder=4)

    # Cross-track distance markers and Turn boundaries along official track center_line
    track_data = db.get_track(first_track) or {}
    cl = track_data.get('center_line', [])

    if cl:
        m_pts = [_latlon_to_mercator(p['lon'], p['lat']) for p in cl]
        cl_d = np.array([p['dist'] for p in cl])
        cl_x = np.array([p[0] for p in m_pts])
        cl_y = np.array([p[1] for p in m_pts])
        sort_idx = np.argsort(cl_d)
        cl_d_sorted = cl_d[sort_idx]
        cl_x_sorted = cl_x[sort_idx]
        cl_y_sorted = cl_y[sort_idx]
    elif ref_lap_pts:
        cl_d = np.array([p['d'] for p in ref_lap_pts])
        cl_x = np.array([p['x'] for p in ref_lap_pts])
        cl_y = np.array([p['y'] for p in ref_lap_pts])
        sort_idx = np.argsort(cl_d)
        cl_d_sorted = cl_d[sort_idx]
        cl_x_sorted = cl_x[sort_idx]
        cl_y_sorted = cl_y[sort_idx]
    else:
        cl_d_sorted = np.array([])
        cl_x_sorted = np.array([])
        cl_y_sorted = np.array([])

    if len(cl_d_sorted) > 1:
        def _draw_perpendicular_line(
            d_target: float,
            half_width_m: float,
            color: str,
            linewidth: float = 1.2,
            linestyle: str = '-',
            zorder: int = 5,
            label: str | None = None
        ) -> None:
            d_min, d_max = float(cl_d_sorted[0]), float(cl_d_sorted[-1])
            if not (d_min <= d_target <= d_max):
                return

            cx = float(np.interp(d_target, cl_d_sorted, cl_x_sorted))
            cy = float(np.interp(d_target, cl_d_sorted, cl_y_sorted))

            d_prev = max(d_min, d_target - 1.0)
            d_next = min(d_max, d_target + 1.0)
            x_prev = float(np.interp(d_prev, cl_d_sorted, cl_x_sorted))
            y_prev = float(np.interp(d_prev, cl_d_sorted, cl_y_sorted))
            x_next = float(np.interp(d_next, cl_d_sorted, cl_x_sorted))
            y_next = float(np.interp(d_next, cl_d_sorted, cl_y_sorted))

            dx = x_next - x_prev
            dy = y_next - y_prev
            length_m = math.hypot(dx, dy)
            if length_m < 1e-6:
                return

            nx = -dy / length_m
            ny = dx / length_m

            x1 = cx - half_width_m * nx
            y1 = cy - half_width_m * ny
            x2 = cx + half_width_m * nx
            y2 = cy + half_width_m * ny

            ax.plot(
                [x1, x2], [y1, y2], color=color, linewidth=linewidth, linestyle=linestyle, zorder=zorder, label=label
            )

        # 5m cross-track distance markers
        d_max_val = float(cl_d_sorted[-1])
        d_curr = 0.0
        while d_curr <= d_max_val:
            is_major = (int(round(d_curr)) % 25 == 0)
            hw = 5.0 if is_major else 3.5
            col = '#64748b' if is_major else '#334155'
            lw = 1.2 if is_major else 0.8
            _draw_perpendicular_line(d_curr, half_width_m=hw, color=col, linewidth=lw, zorder=2)
            d_curr += 5.0

        # Prominent Turn Start and End boundary lines across the track
        if start_m is not None:
            _draw_perpendicular_line(
                start_m,
                half_width_m=7.0,
                color='#38bdf8',
                linewidth=2.5,
                zorder=6,
                label=f"Turn Start ({start_m:.0f}m)",
            )

        if end_m is not None:
            _draw_perpendicular_line(
                end_m,
                half_width_m=7.0,
                color='#f43f5e',
                linewidth=2.5,
                zorder=6,
                label=f"Turn End ({end_m:.0f}m)",
            )

    # Focus axis limits onto the corner while showing full trajectories flowing through
    if (start_m is not None or end_m is not None):
        turn_pts = []
        for _, _, _, pts in all_lap_pts:
            for p in pts:
                d = p['d']
                if start_m is not None and end_m is not None:
                    if start_m <= end_m:
                        in_turn = (start_m <= d <= end_m)
                    else:
                        in_turn = (d >= start_m or d <= end_m)
                elif start_m is not None:
                    in_turn = (d >= start_m)
                elif end_m is not None:
                    in_turn = (d <= end_m)
                else:
                    in_turn = True
                if in_turn:
                    turn_pts.append(p)

        if turn_pts:
            all_x = [p['x'] for p in turn_pts]
            all_y = [p['y'] for p in turn_pts]
            x_min, x_max = min(all_x), max(all_x)
            y_min, y_max = min(all_y), max(all_y)
            span_x = x_max - x_min
            span_y = y_max - y_min
            pad = max(span_x, span_y) * 0.20
            pad = max(pad, 0.0001)
            center_x = (x_min + x_max) / 2.0
            center_y = (y_min + y_max) / 2.0
            half_span = max(span_x, span_y) / 2.0 + pad
            ax.set_xlim(center_x - half_span, center_x + half_span)
            ax.set_ylim(center_y - half_span, center_y + half_span)
            ax.set_aspect('equal', adjustable='box')
    else:
        ax.set_aspect('equal', adjustable='datalim')

    # Remove latitude and longitude coordinate text, ticks, and exponent offsets
    ax.xaxis.set_major_formatter(plt.NullFormatter())
    ax.yaxis.set_major_formatter(plt.NullFormatter())
    ax.xaxis.offsetText.set_visible(False)
    ax.yaxis.offsetText.set_visible(False)
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    ax.set_xlabel("")
    ax.set_ylabel("")

    # Drop background rectangular grid
    ax.grid(False)

    title_suffix = f" [{color_mode.capitalize()} Mode]" if color_mode != 'lap' else ""
    ax.set_title(f"Trajectory Comparison ({first_track}){title_suffix}", fontsize=12, fontweight='bold')
    ax.legend(loc='upper right', frameon=True, facecolor='#18181b', edgecolor='#27272a')

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close(fig)

    return base64.b64encode(buf.getvalue()).decode('utf-8')


def render_session_consistency_image(
    session_id: str,
    track: str,
    date: str,
    driver_name: str | None = None
) -> str:
    """Render lap time evolution scatter plot over the session and return base64 encoded PNG."""
    laps_data = lap_tools._load_all_telemetry_laps(track, date, session_id, driver_name)
    if not laps_data:
        raise ValueError(f"No laps found for session_id={session_id!r}")

    laps_data.sort(key=lambda lap_item: lap_item['lap'])
    lap_nums = [lap_item['lap'] for lap_item in laps_data]
    lap_times = [lap_item['time_s'] for lap_item in laps_data]

    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 5))

    ax.scatter(lap_nums, lap_times, color='#38bdf8', s=45, zorder=3, label="Lap Times")

    if len(lap_times) >= 3:
        rolling_avg = np.convolve(lap_times, np.ones(3)/3, mode='valid')
        ax.plot(lap_nums[1:-1], rolling_avg, color='#10b981', linewidth=2, linestyle='-', label="3-Lap Rolling Avg")

    best_idx = int(np.argmin(lap_times))
    best_t = lap_times[best_idx]
    ax.scatter([lap_nums[best_idx]], [best_t], color='#f43f5e', s=90, zorder=4, label=f"Best Lap ({best_t}s)")

    ax.set_xlabel("Lap Number", fontsize=10)
    ax.set_ylabel("Lap Time (s)", fontsize=10)
    ax.set_title(f"Session Consistency - Session: {session_id}", fontsize=12, fontweight='bold')
    ax.grid(True, linestyle='--', alpha=0.3)
    ax.legend(loc='upper right', frameon=True, facecolor='#18181b', edgecolor='#27272a')

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close(fig)

    return base64.b64encode(buf.getvalue()).decode('utf-8')
