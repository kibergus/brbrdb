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

# Use non-interactive Agg backend to render images in headless server environment without GUI dependencies
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402


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
    channels: list[str]
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

    for ch in channels:
        if ch != 'delta_time' and ch not in header:
            raise ValueError(f"Channel {ch!r} not found in telemetry columns: {header}")

    lap_set = set(laps)
    raw_lap_data: dict[int, dict[str, list[float]]] = {
        l_num: {'dist': [], 'time': []} for l_num in laps
    }
    for ch in channels:
        if ch != 'delta_time':
            for l_num in laps:
                raw_lap_data[l_num][ch] = []

    for row in data_rows:
        if not row or len(row) < len(header):
            continue
        try:
            lap_val = int(row[lap_idx])
            if lap_val not in lap_set:
                continue

            dist_val = float(row[dist_idx])
            time_str = row[time_idx]
            time_val = _parse_time_seconds(time_str)

            raw_lap_data[lap_val]['dist'].append(dist_val)
            raw_lap_data[lap_val]['time'].append(time_val)

            for ch in channels:
                if ch != 'delta_time':
                    c_idx = header.index(ch)
                    val_str = row[c_idx].strip()
                    val = float(val_str) if val_str != '' else np.nan
                    raw_lap_data[lap_val][ch].append(val)
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
    """Interpolate raw lap channels onto a uniform distance grid and compute delta_time."""
    dists_grid = np.linspace(start_m, end_m, num=500)
    interp_lap_data: dict[int | str, dict[str, np.ndarray]] = {}

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
            if ch != 'delta_time':
                c_vals = np.array(raw_lap_data[l_num][ch])[sort_order]
                interp_lap_data[l_num][ch] = np.interp(dists_grid, l_dist, c_vals)

    if 'delta_time' in channels:
        ref_lap = laps[0]
        ref_time = interp_lap_data[ref_lap]['time']
        ref_segment_time = ref_time - ref_time[0]

        for l_num in laps:
            t = interp_lap_data[l_num]['time']
            l_segment_time = t - t[0]
            interp_lap_data[l_num]['delta_time'] = l_segment_time - ref_segment_time

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
        ax.grid(True, linestyle='--', alpha=0.3)

        if idx == 0:
            ax.legend(loc='upper right', frameon=True, facecolor='#18181b', edgecolor='#27272a')
            ax.set_title(
                f"Telemetry Comparison - Session: {session_id}", fontsize=12, fontweight='bold'
            )

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

    full_track_rendered = False
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

        lap_idx = header.index('Lap') if 'Lap' in header else -1
        dist_idx = -1
        for col_name in ['Lap Distance (m)', 'Lap Distance', 'LapDistance']:
            if col_name in header:
                dist_idx = header.index(col_name)
                break

        x_idx = -1
        for col_name in ['Longitude', 'x', 'pos_x']:
            if col_name in header:
                x_idx = header.index(col_name)
                break

        y_idx = -1
        for col_name in ['Latitude', 'y', 'z', 'pos_y', 'pos_z']:
            if col_name in header:
                y_idx = header.index(col_name)
                break

        speed_idx = -1
        for col_name in ['Speed', 'speed', 'Ground Speed']:
            if col_name in header:
                speed_idx = header.index(col_name)
                break

        throttle_idx = -1
        for col_name in ['Throttle', 'Throttle (%)', 'throttle']:
            if col_name in header:
                throttle_idx = header.index(col_name)
                break

        brake_idx = -1
        for col_name in ['Brake', 'Brake (%)', 'brake']:
            if col_name in header:
                brake_idx = header.index(col_name)
                break

        glon_idx = -1
        for col_name in ['GForceLon', 'gforcelon']:
            if col_name in header:
                glon_idx = header.index(col_name)
                break

        time_idx = header.index('Time') if 'Time' in header else -1

        if lap_idx == -1 or dist_idx == -1 or x_idx == -1 or y_idx == -1:
            raise ValueError(f"Trajectory coordinates missing in {csv_path}")

        full_track_pts: list[tuple[float, float]] = []
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

                if not full_track_rendered:
                    full_track_pts.append((x_val, y_val))

                if l_val == lap_num:
                    if start_m is not None and end_m is not None:
                        if start_m <= end_m:
                            if not (start_m <= d_val <= end_m):
                                continue
                        else:
                            if not (d_val >= start_m or d_val <= end_m):
                                continue
                    elif start_m is not None and d_val < start_m:
                        continue
                    elif end_m is not None and d_val > end_m:
                        continue

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

        if not full_track_rendered and full_track_pts and (start_m is not None or end_m is not None):
            fx = [p[0] for p in full_track_pts]
            fy = [p[1] for p in full_track_pts]
            ax.plot(fx, fy, color='#334155', linestyle='--', linewidth=1.5, alpha=0.6, label="Full Track")
            full_track_rendered = True

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
            ax.plot(lx, ly, color=lap_color, linewidth=2.5, label=lap_label)
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

            lc_z = 1 if (color_mode == 'delta_t' and is_ref) else 2
            lc = LineCollection(segments, colors=segment_colors, linewidths=2.5, zorder=lc_z)
            ax.add_collection(lc)
            # Dummy line for legend entry
            legend_col = '#ffffff' if (color_mode == 'delta_t' and is_ref) else lap_color
            ax.plot([], [], color=legend_col, linewidth=2.5, label=lap_label)

        # Start and end markers
        marker_col = '#ffffff' if (color_mode == 'delta_t' and is_ref) else lap_color
        ax.plot(lx[0], ly[0], marker='o', color=marker_col, markersize=6)
        ax.plot(lx[-1], ly[-1], marker='s', color=marker_col, markersize=6)

    ax.set_aspect('equal', adjustable='datalim')
    ax.grid(True, linestyle='--', alpha=0.3)
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
