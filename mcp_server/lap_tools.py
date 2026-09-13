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

"""Lap analysis tools for MCP Server (get_stats, get_aggregates, get_pace_summary)."""
from __future__ import annotations
import os
from typing import Any
import numpy as np

from database import db
import location_handlers
from upload_handlers import is_lap_valid
from mcp_server import image_tools


def _load_all_telemetry_laps(
    track: str,
    date: str | None = None,
    session_id: str | None = None,
    driver_name: str | None = None
) -> list[dict[str, Any]]:
    raw_sessions = db.find_sessions(
        date=date,
        track=track,
        session_id=session_id,
        driver_names=driver_name
    )

    track_info = db.get_track(track) or {}
    lap_length = float(track_info.get('lap_length', 0.0))
    sector_ends = track_info.get('sector_end', [])
    turns_def = track_info.get('turns', [])

    all_laps_data: list[dict[str, Any]] = []
    seen_csv_paths: set[str] = set()

    for s in raw_sessions:
        tel_dir = os.path.join(s.meeting_dir, 'telemetry') if s.meeting_dir else ''
        if not tel_dir or not os.path.exists(tel_dir):
            continue

        session_drivers = db.get_session_drivers(s.session_id, s.meeting_folder)

        for csv_file in sorted(os.listdir(tel_dir)):
            if not csv_file.endswith('.csv'):
                continue
            if session_id and (session_id != csv_file and not csv_file.startswith(session_id)):
                continue

            csv_path = os.path.join(tel_dir, csv_file)
            if csv_path in seen_csv_paths:
                continue
            seen_csv_paths.add(csv_path)

            laps, columns, csv_driver = location_handlers.parse_telemetry_csv(csv_path)

            if not laps:
                continue

            if driver_name and csv_driver:
                if driver_name.lower() not in csv_driver.lower():
                    continue

            df_official = db.load(leagues=s.league, classes=s.class_name, date=s.date, track=track)
            location_handlers._override_with_official_laps(laps, df_official, s.session_id, csv_driver)

            location_handlers._compute_lap_segments(laps, sector_ends, turns_def)

            speed_col = None
            for candidate in ['Speed (km/h)', 'Speed', 'Speed (m/s)']:
                if candidate in columns:
                    speed_col = candidate
                    break

            steering_col = None
            for candidate in ['Steering Wheel Angle (deg)', 'Steering', 'SteeringWheelAngle']:
                if candidate in columns:
                    steering_col = candidate
                    break

            channels_to_fetch = []
            if speed_col:
                channels_to_fetch.append(speed_col)
            if steering_col:
                channels_to_fetch.append(steering_col)

            lap_numbers = [
                lap_item['lap_num']
                for lap_item in laps
                if not lap_item.get('is_outlap') and lap_item.get('is_valid', True)
            ]

            raw_channel_data: dict[int, dict[str, list[float]]] = {}
            if channels_to_fetch:
                raw_channel_data = image_tools.extract_raw_lap_data(
                    csv_path, lap_numbers, channels_to_fetch, parsed_laps=laps
                )

            driver_label = csv_driver or (session_drivers[0] if session_drivers else 'Unknown')

            for l_item in laps:
                if l_item.get('is_outlap') or not l_item.get('is_valid', True):
                    continue

                l_num = l_item['lap_num']
                dists = l_item.get('dists', [])
                times = l_item.get('times', [])

                if not is_lap_valid(np.array(dists), turns_def, lap_length):
                    continue

                duration = times[-1] - times[0] if len(times) > 1 else 0.0
                time_s = None
                lap_time_str = l_item.get('lap_time')
                if lap_time_str and lap_time_str != 'Unknown':
                    try:
                        if ':' in lap_time_str:
                            p = lap_time_str.split(':')
                            time_s = round(float(p[0]) * 60 + float(p[1]), 3)
                        else:
                            time_s = round(float(lap_time_str), 3)
                    except (ValueError, TypeError):
                        pass
                if time_s is None or time_s <= 0:
                    time_s = round(l_item.get('duration') or duration, 3)

                ch_lap = raw_channel_data.get(l_num, {})

                sp_vals = ch_lap.get(speed_col, []) if speed_col else []
                if speed_col and speed_col.lower() == 'speed (m/s)' and sp_vals:
                    sp_vals = [v * 3.6 if (v is not None and not np.isnan(v)) else np.nan for v in sp_vals]

                st_vals = ch_lap.get(steering_col, []) if steering_col else []

                points = []
                num_pts = min(len(dists), len(times))
                for i in range(num_pts):
                    sp_val = sp_vals[i] if i < len(sp_vals) else None
                    st_val = st_vals[i] if i < len(st_vals) else None
                    points.append({
                        'dist': dists[i],
                        'time': times[i],
                        'speed': sp_val if (sp_val is not None and not np.isnan(sp_val)) else None,
                        'steering': st_val if (st_val is not None and not np.isnan(st_val)) else None
                    })

                all_laps_data.append({
                    'session_id': csv_file,
                    'date': s.date,
                    'driver_name': driver_label,
                    'lap': l_num,
                    'time_s': time_s,
                    'points': points,
                    'sector_times': l_item.get('sector_times', []),
                    'turn_times': l_item.get('turn_times', []),
                    'turns_def': turns_def
                })

    return all_laps_data


def get_stats_impl(
    track: str,
    date: str | None = None,
    session_id: str | None = None,
    driver_name: str | None = None,
    turn_index: int | None = None,
    percentile_center: float | None = None,
    percentile_half_width: float = 5.0
) -> list[dict[str, Any]]:
    """Returns combined lap or per-turn statistics."""
    laps_data = _load_all_telemetry_laps(track, date, session_id, driver_name)
    if not laps_data:
        return []

    if percentile_center is not None:
        laps_data.sort(key=lambda lap_item: lap_item['time_s'])
        n = len(laps_data)
        low_pct = max(0.0, percentile_center - percentile_half_width)
        high_pct = min(100.0, percentile_center + percentile_half_width)
        low_idx = int(np.floor(n * (low_pct / 100.0)))
        high_idx = int(np.ceil(n * (high_pct / 100.0)))
        high_idx = max(low_idx + 1, min(n, high_idx))
        laps_data = laps_data[low_idx:high_idx]

    results: list[dict[str, Any]] = []

    for lap_item in laps_data:
        pts = lap_item['points']
        dists = np.array([p['dist'] for p in pts])
        speeds = np.array([p['speed'] if p['speed'] is not None else np.nan for p in pts])
        steerings = np.array([p['steering'] if p['steering'] is not None else np.nan for p in pts])

        if turn_index is None:
            entry_sp = float(speeds[0]) if len(speeds) > 0 and not np.isnan(speeds[0]) else 0.0
            exit_sp = float(speeds[-1]) if len(speeds) > 0 and not np.isnan(speeds[-1]) else 0.0
            results.append({
                "session_id": lap_item['session_id'],
                "date": lap_item['date'],
                "driver_name": lap_item['driver_name'],
                "lap": lap_item['lap'],
                "time_s": lap_item['time_s'],
                "entry_speed_kmh": round(entry_sp, 1),
                "exit_speed_kmh": round(exit_sp, 1)
            })
        else:
            turns = lap_item['turns_def']
            if not turns or turn_index < 1 or turn_index > len(turns):
                continue

            t_idx = turn_index - 1
            t_def = turns[t_idx]
            prev_t_def = turns[(t_idx - 1 + len(turns)) % len(turns)]
            next_t_def = turns[(t_idx + 1) % len(turns)]

            t_start_m = float(t_def.get('start', prev_t_def.get('end', 0.0)))
            t_end_m = float(t_def.get('end', t_start_m + 50.0))
            next_t_start_m = float(next_t_def.get('start', t_end_m + 50.0))

            t_time = lap_item['turn_times'][t_idx] if t_idx < len(lap_item['turn_times']) else 0.0
            if t_time <= 0.0:
                continue

            entry_sp = float(np.interp(t_start_m, dists, speeds))
            exit_sp = float(np.interp(t_end_m, dists, speeds))
            straight_exit_sp = float(np.interp(next_t_start_m, dists, speeds))

            apexes_def = t_def.get('apexes_m') or t_def.get('apex', [])
            if isinstance(apexes_def, (int, float)):
                apexes_def = [float(apexes_def)]

            apexes_list = []
            for apex_m in apexes_def:
                apex_m = float(apex_m)
                mask = (dists >= apex_m - 20.0) & (dists <= apex_m + 20.0)
                window_speeds = speeds[mask]
                window_steer = steerings[mask]

                has_sp = len(window_speeds) > 0 and not np.all(np.isnan(window_speeds))
                min_sp = float(np.nanmin(window_speeds)) if has_sp else exit_sp

                has_st = len(window_steer) > 0 and not np.all(np.isnan(window_steer))
                max_st = float(np.nanmax(np.abs(window_steer))) if has_st else 0.0

                apexes_list.append({
                    "apex_m": round(apex_m, 1),
                    "min_speed_kmh": round(min_sp, 1),
                    "max_steering_deg": round(max_st, 1)
                })

            results.append({
                "session_id": lap_item['session_id'],
                "date": lap_item['date'],
                "driver_name": lap_item['driver_name'],
                "lap": lap_item['lap'],
                "turn_index": turn_index,
                "turn_name": t_def.get('name', f"Turn {turn_index}"),
                "time_s": t_time,
                "entry_speed_kmh": round(entry_sp, 1),
                "exit_speed_kmh": round(exit_sp, 1),
                "straight_exit_speed_kmh": round(straight_exit_sp, 1),
                "apexes": apexes_list
            })

    return results


def get_aggregates_impl(
    track: str,
    date: str | None = None,
    session_id: str | None = None,
    driver_name: str | None = None,
    exclude_outlier_pct: float = 0.0
) -> dict[str, Any]:
    """Returns aggregate statistics across laps for overall and each turn."""
    laps_data = _load_all_telemetry_laps(track, date, session_id, driver_name)
    if not laps_data:
        return {
            "driver_name": driver_name or "Unknown",
            "date": date or "",
            "lap_count": 0,
            "overall": {
                "lap_time_min_s": 0.0,
                "lap_time_max_s": 0.0,
                "lap_time_avg_s": 0.0,
                "lap_time_std_s": 0.0
            }
        }

    outlier_pct = min(20.0, max(0.0, float(exclude_outlier_pct)))
    if outlier_pct > 0:
        laps_data.sort(key=lambda lap_item: lap_item['time_s'])
        n = len(laps_data)
        trim = int(np.floor(n * (outlier_pct / 100.0)))
        if trim > 0 and n - 2 * trim > 0:
            laps_data = laps_data[trim:n - trim]

    lap_times = [lap_item['time_s'] for lap_item in laps_data]
    driver_lbl = driver_name or laps_data[0]['driver_name']
    date_lbl = date or laps_data[0]['date']

    res: dict[str, Any] = {
        "driver_name": driver_lbl,
        "date": date_lbl,
        "lap_count": len(laps_data),
        "overall": {
            "lap_time_min_s": round(float(np.min(lap_times)), 3),
            "lap_time_max_s": round(float(np.max(lap_times)), 3),
            "lap_time_avg_s": round(float(np.mean(lap_times)), 3),
            "lap_time_std_s": round(float(np.std(lap_times)), 3)
        }
    }

    turns_def = laps_data[0].get('turns_def', [])
    for idx_t, t_def in enumerate(turns_def):
        t_name = t_def.get('name', f"Turn {idx_t + 1}")
        if not t_name.startswith("Turn"):
            t_name = f"Turn {t_name}"

        t_times = [
            lap_item['turn_times'][idx_t]
            for lap_item in laps_data
            if idx_t < len(lap_item['turn_times']) and lap_item['turn_times'][idx_t] > 0.0
        ]
        if not t_times:
            continue

        turn_stats = get_stats_impl(track, date, session_id, driver_name, turn_index=idx_t + 1)
        min_speeds = []
        max_steers = []
        for ts in turn_stats:
            for ap in ts.get('apexes', []):
                min_speeds.append(ap['min_speed_kmh'])
                max_steers.append(ap['max_steering_deg'])

        res[t_name] = {
            "turn_time_min_s": round(float(np.min(t_times)), 3),
            "turn_time_max_s": round(float(np.max(t_times)), 3),
            "turn_time_avg_s": round(float(np.mean(t_times)), 3),
            "turn_time_std_s": round(float(np.std(t_times)), 3),
            "min_speed_avg_kmh": round(float(np.mean(min_speeds)), 1) if min_speeds else 0.0,
            "min_speed_std_kmh": round(float(np.std(min_speeds)), 1) if min_speeds else 0.0,
            "max_steering_avg_deg": round(float(np.mean(max_steers)), 1) if max_steers else 0.0,
            "max_steering_std_deg": round(float(np.std(max_steers)), 1) if max_steers else 0.0
        }

    return res


def get_pace_summary_impl(
    track: str,
    date: str,
    session_id: str | None = None,
    driver_name: str | None = None
) -> dict[str, Any]:
    """Returns a structured summary of theoretical vs actual best lap and per-turn improvement priority."""
    laps_data = _load_all_telemetry_laps(track, date, session_id, driver_name)
    if not laps_data:
        raise ValueError(f"No valid telemetry data found for track={track!r}, date={date!r}")

    laps_data.sort(key=lambda lap_item: lap_item['time_s'])
    best_lap_obj = laps_data[0]

    turns_def = best_lap_obj.get('turns_def', [])
    num_turns = len(turns_def)

    turn_bests: list[dict[str, Any]] = []
    for idx_t in range(num_turns):
        t_def = turns_def[idx_t]
        t_name = t_def.get('name', f"Turn {idx_t + 1}")
        if not t_name.startswith("Turn"):
            t_name = f"Turn {t_name}"

        actual_t_time = best_lap_obj['turn_times'][idx_t] if idx_t < len(best_lap_obj['turn_times']) else 0.0

        min_t_time = float('inf')
        best_l_num = best_lap_obj['lap']
        best_sess = best_lap_obj['session_id']

        for lap_item in laps_data:
            if idx_t < len(lap_item['turn_times']):
                tt = lap_item['turn_times'][idx_t]
                if tt > 0.0 and tt < min_t_time:
                    min_t_time = tt
                    best_l_num = lap_item['lap']
                    best_sess = lap_item['session_id']

        if min_t_time == float('inf'):
            min_t_time = actual_t_time

        delta_s = round(max(0.0, actual_t_time - min_t_time), 3)

        turn_bests.append({
            "name": t_name,
            "actual_s": round(actual_t_time, 3),
            "best_s": round(min_t_time, 3),
            "delta_s": delta_s,
            "best_lap": best_l_num,
            "best_session": best_sess,
            "turn_index": idx_t
        })

    turn_bests_sorted = sorted(turn_bests, key=lambda t: t['delta_s'], reverse=True)
    for rank, t in enumerate(turn_bests_sorted, start=1):
        t['priority'] = rank

    turn_bests.sort(key=lambda t: t['turn_index'])
    for t in turn_bests:
        t.pop('turn_index', None)

    theoretical_best = sum(t['best_s'] for t in turn_bests)
    actual_best = best_lap_obj['time_s']
    total_delta = round(max(0.0, actual_best - theoretical_best), 3)

    return {
        "driver_name": driver_name or best_lap_obj['driver_name'],
        "actual_best_lap": best_lap_obj['lap'],
        "actual_best_session": best_lap_obj['session_id'],
        "actual_best_s": actual_best,
        "theoretical_best_s": round(theoretical_best, 3),
        "total_delta_s": total_delta,
        "turns": turn_bests
    }
