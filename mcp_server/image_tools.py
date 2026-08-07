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
        interp_lap_data[l_num] = {'time': interp_time}

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


def render_trajectory_plot(
    laps: list[tuple[str, str, str, int] | list[Any] | dict[str, Any]],
    start_m: float | None = None,
    end_m: float | None = None
) -> str:
    """Render matplotlib trajectory comparison plot (GPS or X/Z) for lap tuples (date, track, session_id, lap)."""
    if not laps:
        raise ValueError("laps parameter cannot be empty")

    parsed_laps = [parse_lap_spec(item) for item in laps]
    first_date, first_track, first_sid, _ = parsed_laps[0]

    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(8, 8))
    colors = ['#10b981', '#f43f5e', '#38bdf8', '#f59e0b', '#a855f7', '#ec4899']

    full_track_rendered = False

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

        if lap_idx == -1 or dist_idx == -1 or x_idx == -1 or y_idx == -1:
            raise ValueError(f"Trajectory coordinates missing in {csv_path}")

        full_track_pts: list[tuple[float, float]] = []
        lap_pts: list[tuple[float, float, float]] = []

        for row in data_rows:
            if not row or len(row) < len(header):
                continue
            try:
                l_val = int(row[lap_idx])
                d_val = float(row[dist_idx])
                x_val = float(row[x_idx])
                y_val = float(row[y_idx])

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

                    lap_pts.append((x_val, y_val, d_val))
            except (ValueError, IndexError):
                continue

        if not full_track_rendered and full_track_pts and (start_m is not None or end_m is not None):
            fx = [p[0] for p in full_track_pts]
            fy = [p[1] for p in full_track_pts]
            ax.plot(fx, fy, color='#334155', linestyle='--', linewidth=1.5, alpha=0.6, label="Full Track")
            full_track_rendered = True

        if lap_pts:
            color = colors[idx_l % len(colors)]
            lx = [p[0] for p in lap_pts]
            ly = [p[1] for p in lap_pts]
            lap_label = f"Lap {lap_num} ({session_id})"
            ax.plot(lx, ly, color=color, linewidth=2.5, label=lap_label)
            ax.plot(lx[0], ly[0], marker='o', color=color, markersize=6)
            ax.plot(lx[-1], ly[-1], marker='s', color=color, markersize=6)

    ax.set_aspect('equal', adjustable='datalim')
    ax.grid(True, linestyle='--', alpha=0.3)
    ax.set_title(f"Trajectory Comparison ({first_track})", fontsize=12, fontweight='bold')
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
