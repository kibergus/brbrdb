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

"""Versioned derived-channel calculator registry and provenance checker for .derived.brtc.

Calculators operate purely on vectorized numpy float64 column dictionaries from
{driver_stem}.primary.brtc (and optional {driver_stem}.aux.<stream>.brtc files),
recording their algorithm version and input dependency hash inside .derived.brtc's
manifest so stale derived files can be regenerated without touching raw telemetry.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from race_tools import assetto_corsa, rfactor, track_data as rt_track_data
import _telemetry_native
import telemetry_brtc

logger = logging.getLogger(__name__)


@dataclass
class CalculatorContext:
    """Shared execution context passed to all derived calculators for one driver session."""
    primary_cols: dict[str, np.ndarray]
    derived_cols: dict[str, np.ndarray]
    metadata: dict[str, Any]
    track_info: dict[str, Any]
    aux_brtc_paths: dict[str, str]
    laps: list[dict[str, Any]]
    course_deg: np.ndarray | None = None


@dataclass(frozen=True)
class RegisteredCalculator:
    """Descriptor for a versioned derived-channel calculator."""
    name: str
    version: int
    deps_fn: Callable[[dict[str, Any], dict[str, Any], dict[str, str]], str]
    compute_fn: Callable[[CalculatorContext], list[str]]


CALCULATOR_REGISTRY: list[RegisteredCalculator] = []


def register_calculator(
    name: str,
    version: int,
    deps_fn: Callable[[dict[str, Any], dict[str, Any], dict[str, str]], str],
) -> Callable[[Callable[[CalculatorContext], list[str]]], Callable[[CalculatorContext], list[str]]]:
    """Decorator registering a versioned derived telemetry calculator in execution order."""
    def _decorator(
        fn: Callable[[CalculatorContext], list[str]],
    ) -> Callable[[CalculatorContext], list[str]]:
        CALCULATOR_REGISTRY.append(
            RegisteredCalculator(name=name, version=version, deps_fn=deps_fn, compute_fn=fn)
        )
        return fn
    return _decorator


def _stable_sha256(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()[:16]


def resolve_simulator_origin(
    metadata: dict[str, Any],
    resolved_track_name: str,
) -> rt_track_data.TrackOrigin | None:
    """Resolve simulator TrackOrigin from explicit metadata fields or built-in track tables."""
    league = str(metadata.get('league') or '')
    track_name = str(metadata.get('track_name') or resolved_track_name or '')
    lat_raw = metadata.get('track_origin_lat')
    lon_raw = metadata.get('track_origin_lon')
    heading_raw = metadata.get('track_origin_heading', 0.0)

    if lat_raw is not None and lon_raw is not None:
        lat = float(lat_raw)
        lon = float(lon_raw)
        heading = float(heading_raw) if heading_raw is not None else 0.0
        if league in ('acevo', 'ac'):
            ac_origin: assetto_corsa.AssettoCorsaTrackOrigin | None = None
            for cand_name in (resolved_track_name, track_name):
                if not cand_name:
                    continue
                try:
                    ac_origin = assetto_corsa.get_track_origin(cand_name)
                    break
                except ValueError:
                    pass
            if ac_origin is None:
                return None
            return assetto_corsa.AssettoCorsaTrackOrigin(
                lat=lat, lon=lon, correction=ac_origin.correction, heading=heading
            )
        return rfactor.RFactorTrackOrigin(lat=lat, lon=lon, heading=heading)

    for cand_name in (resolved_track_name, track_name):
        if not cand_name:
            continue
        try:
            if league == 'kartsim':
                return rfactor.get_track_origin(cand_name)
            if league in ('acevo', 'ac'):
                return assetto_corsa.get_track_origin(cand_name)
        except ValueError:
            pass
    return None


def compute_gps_from_local_xz(
    x_arr: np.ndarray,
    z_arr: np.ndarray,
    origin: rt_track_data.TrackOrigin,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert simulator local (x, z) meter arrays to GPS (Latitude, Longitude) arrays."""
    n = len(x_arr)
    lat_arr = np.full(n, np.nan, dtype=np.float64)
    lon_arr = np.full(n, np.nan, dtype=np.float64)
    valid = np.isfinite(x_arr) & np.isfinite(z_arr)
    for i in np.where(valid)[0]:
        p_lat, p_lon = origin.to_gps(float(x_arr[i]), float(z_arr[i]))
        lat_arr[i] = p_lat
        lon_arr[i] = p_lon
    return lat_arr, lon_arr


def compute_course_deg(lat_arr: np.ndarray, lon_arr: np.ndarray) -> np.ndarray:
    """Compute compass course over ground (degrees, unwrapped sign matching atan2(dlon, dlat))."""
    n = len(lat_arr)
    course = np.zeros(n, dtype=np.float64)
    if n == 0:
        return course
    valid_coords = np.isfinite(lat_arr) & np.isfinite(lon_arr) & ~((lat_arr == 0.0) & (lon_arr == 0.0))
    last_valid_idx: int | None = None
    for i in range(n):
        if not valid_coords[i]:
            course[i] = course[last_valid_idx] if last_valid_idx is not None else 0.0
            continue
        if last_valid_idx is not None:
            dlat = float(lat_arr[i] - lat_arr[last_valid_idx])
            dlon = float(lon_arr[i] - lon_arr[last_valid_idx]) * math.cos(math.radians(float(lat_arr[i])))
            if dlat != 0.0 or dlon != 0.0:
                course[i] = math.degrees(math.atan2(dlon, dlat))
            else:
                course[i] = course[last_valid_idx]
        else:
            course[i] = 0.0
        last_valid_idx = i

    first_valid_idxs = np.where(valid_coords)[0]
    if len(first_valid_idxs) > 0 and first_valid_idxs[0] > 0:
        course[:first_valid_idxs[0]] = course[first_valid_idxs[0]]
    return course


def format_lap_time_str(seconds: float) -> str:
    """Format seconds into M:SS.mmm or SS.mmm matching location_handlers._parse_lap_time."""
    if not seconds or seconds <= 0.0:
        return 'Unknown'
    total_ms = round(seconds * 1000)
    if total_ms <= 0:
        return 'Unknown'
    m, rem_ms = divmod(total_ms, 60000)
    s, ms = divmod(rem_ms, 1000)
    if m > 0:
        return f'{m}:{s:02d}.{ms:03d}'
    return f'{s}.{ms:03d}'


def detect_is_outlap(d0: np.ndarray, d1: np.ndarray | None) -> bool:
    """Detect whether the first lap of a session is a pit outlap."""
    valid0 = d0[np.isfinite(d0)]
    if valid0.size == 0:
        return False
    head_n = min(int(valid0.size), 5)
    starts_away = bool(valid0[0] > 20.0 or np.min(valid0[:head_n]) > 20.0)
    is_short = False
    if d1 is not None:
        valid1 = d1[np.isfinite(d1)]
        if valid1.size > 0:
            span0 = float(np.max(valid0) - np.min(valid0))
            span1 = float(np.max(valid1) - np.min(valid1))
            if span1 > 0.0 and span0 < span1 * 0.9:
                is_short = True
    return starts_away or is_short


def build_laps_manifest(
    lap_col: np.ndarray,
    lap_distance_m: np.ndarray,
    time_arr: np.ndarray,
) -> list[dict[str, Any]]:
    """Build the per-lap index manifest matching location_handlers.parse_telemetry_csv."""
    n = len(lap_col)
    if n == 0:
        return []

    lap_ints = np.round(np.nan_to_num(lap_col, nan=1.0)).astype(np.int64)
    session_laps: dict[int, dict[str, Any]] = {}
    for idx in range(n):
        t_val = float(time_arr[idx]) if idx < len(time_arr) else math.nan
        if math.isnan(t_val):
            continue
        l_num = int(lap_ints[idx])
        entry = session_laps.get(l_num)
        if entry is None:
            session_laps[l_num] = {
                'raw_lap_num': l_num,
                'start_idx': idx,
                'end_idx': idx,
                'first_time': t_val,
                'last_time': t_val,
                'count': 1,
            }
        else:
            entry['end_idx'] = idx
            entry['last_time'] = t_val
            entry['count'] += 1

    sorted_laps = sorted(session_laps.items())
    if not sorted_laps:
        return []

    has_outlap = False
    if len(sorted_laps) > 1 and sorted_laps[0][0] == 1:
        s0, e0 = sorted_laps[0][1]['start_idx'], sorted_laps[0][1]['end_idx']
        s1, e1 = sorted_laps[1][1]['start_idx'], sorted_laps[1][1]['end_idx']
        has_outlap = detect_is_outlap(lap_distance_m[s0:e0 + 1], lap_distance_m[s1:e1 + 1])

    laps_list: list[dict[str, Any]] = []
    for i, (raw_lap, data) in enumerate(sorted_laps):
        if i < len(sorted_laps) - 1:
            duration = float(sorted_laps[i + 1][1]['first_time'] - data['first_time'])
        elif data['count'] > 1:
            duration = float(data['last_time'] - data['first_time'])
        else:
            duration = 0.0

        lap_time_str = format_lap_time_str(duration) if duration > 0.0 else 'Unknown'
        if has_outlap:
            if i == 0:
                lap_num = 0
                is_outlap = True
                is_valid = False
            else:
                lap_num = i
                is_outlap = False
                is_valid = True
        else:
            lap_num = raw_lap
            is_outlap = (raw_lap == 0)
            is_valid = (raw_lap != 0)

        laps_list.append({
            'lap_num': lap_num,
            'raw_lap_num': raw_lap,
            'lap_time': lap_time_str,
            'is_valid': is_valid,
            'is_outlap': is_outlap,
            'start_idx': int(data['start_idx']),
            'end_idx': int(data['end_idx']),
            'duration': duration,
        })

    valid_durs = [
        float(lp['duration']) for lp in laps_list
        if not lp['is_outlap'] and lp['is_valid'] and lp['duration'] > 0.0
    ]
    if valid_durs:
        med = float(np.median(valid_durs))
        if med > 0.0:
            for lp in laps_list:
                if not lp['is_outlap'] and lp['is_valid'] and float(lp['duration']) > 10.0 * med:
                    lp['is_valid'] = False

    return laps_list


def compute_gyro_slip_arrays(
    time_arr: np.ndarray,
    yaw_rate_arr: np.ndarray,
    glat_arr: np.ndarray | None,
    course: np.ndarray,
    speeds_ms: np.ndarray,
    distance_to_rear_axle: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Vector-input equivalent of upload_handlers.compute_gyro_rear_slip_angle."""
    n = len(time_arr)
    if n == 0:
        return np.zeros(0, dtype=np.float64), np.zeros(0, dtype=np.float64)

    dts = np.full(n, 0.1, dtype=np.float64)
    if n > 1:
        raw_dt = np.diff(time_arr)
        valid_dt = np.isfinite(raw_dt) & (raw_dt > 0.0) & (raw_dt <= 1.0)
        dts[1:] = np.where(valid_dt, raw_dt, 0.1)

    is_valid_yaw = np.isfinite(yaw_rate_arr)
    if not np.any(is_valid_yaw):
        return course.copy(), np.full(n, np.nan, dtype=np.float64)

    yaw_rates = np.where(is_valid_yaw, yaw_rate_arr, 0.0)
    glat = (
        np.where(np.isfinite(glat_arr), glat_arr, 0.0)
        if glat_arr is not None
        else np.zeros(n, dtype=np.float64)
    )

    heading = np.zeros(n, dtype=np.float64)
    slip_rear = np.full(n, np.nan, dtype=np.float64)

    moving_idxs = np.where((speeds_ms > 3.0) & is_valid_yaw)[0]
    start_idx = int(moving_idxs[0]) if len(moving_idxs) > 0 else 0
    init_course = float(course[start_idx]) if len(course) > start_idx else 0.0
    heading[:start_idx + 1] = init_course

    bias = 0.0
    bias_samples: list[float] = []

    def _calc_slip(cog: float, head: float, v: float, yr: float, b_val: float) -> float:
        if v < 2.0:
            return 0.0
        beta_cam_deg = (cog - head + 180.0) % 360.0 - 180.0
        beta_cam_rad = math.radians(beta_cam_deg)
        vx_cam = v * math.cos(beta_cam_rad)
        vy_cam = v * math.sin(beta_cam_rad)
        omega_rad = math.radians(-(yr - b_val))
        vy_rear = vy_cam - omega_rad * distance_to_rear_axle
        vx_rear = max(vx_cam, 0.5)
        return math.degrees(math.atan2(vy_rear, vx_rear))

    for k in range(0, start_idx + 1):
        if is_valid_yaw[k]:
            slip_rear[k] = _calc_slip(
                float(course[k]), float(heading[k]), float(speeds_ms[k]), float(yaw_rates[k]), bias
            )

    for i in range(start_idx + 1, n):
        cog = float(course[i])
        v = float(speeds_ms[i])
        if not is_valid_yaw[i]:
            heading[i] = cog
            slip_rear[i] = np.nan
            continue

        dt = float(dts[i])
        yr = float(yaw_rates[i])
        lat_g = float(glat[i])

        if not is_valid_yaw[i - 1]:
            heading[i - 1] = course[i - 1]

        dpsi = -(yr - bias) * dt
        psi_pred = (float(heading[i - 1]) + dpsi) % 360.0

        is_straight = (v > 6.0) and (abs(yr) < 4.0) and (abs(lat_g) < 0.25)
        if is_straight:
            err = (cog - psi_pred + 180.0) % 360.0 - 180.0
            heading[i] = (psi_pred + 0.3 * err) % 360.0
            bias_samples.append(yr)
            if len(bias_samples) > 50:
                bias_samples.pop(0)
            bias = float(np.median(bias_samples))
        else:
            heading[i] = psi_pred

        slip_rear[i] = _calc_slip(cog, float(heading[i]), v, yr, bias)

    return heading, slip_rear


def compute_gyro_force_arrays(
    time_arr: np.ndarray,
    yaw_rate_arr: np.ndarray,
    glat_arr: np.ndarray | None,
    glon_arr: np.ndarray | None,
    steer_arr: np.ndarray | None,
    speeds_ms: np.ndarray,
    distance_to_rear_axle: float | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Vector-input equivalent of upload_handlers.compute_gyro_forces."""
    n = len(time_arr)
    if n == 0:
        empty = np.zeros(0, dtype=np.float64)
        return empty, empty.copy(), empty.copy(), empty.copy()

    wheelbase_m = 1.04
    g_acc = 9.80665
    if distance_to_rear_axle is not None and 0.1 < distance_to_rear_axle < wheelbase_m:
        b_dist = float(distance_to_rear_axle)
    else:
        b_dist = 0.42 * wheelbase_m
    a_dist = wheelbase_m - b_dist
    w_f = b_dist / wheelbase_m
    w_r = a_dist / wheelbase_m
    k_z = 0.48 * wheelbase_m
    moment_coeff = (k_z ** 2) / (wheelbase_m * g_acc)

    dts = np.full(n, 0.1, dtype=np.float64)
    if n > 1:
        raw_dt = np.diff(time_arr)
        valid_dt = np.isfinite(raw_dt) & (raw_dt > 0.0) & (raw_dt <= 1.0)
        dts[1:] = np.where(valid_dt, raw_dt, 0.1)

    is_valid_yaw = np.isfinite(yaw_rate_arr)
    if not np.any(is_valid_yaw):
        nan_arr = np.full(n, np.nan, dtype=np.float64)
        return nan_arr, nan_arr.copy(), nan_arr.copy(), nan_arr.copy()

    yaw_rates_rad = np.radians(np.where(is_valid_yaw, yaw_rate_arr, 0.0))

    d_omega_z = np.zeros(n, dtype=np.float64)
    for i in range(1, n):
        if is_valid_yaw[i] and is_valid_yaw[i - 1]:
            d_omega_z[i] = float(yaw_rates_rad[i] - yaw_rates_rad[i - 1]) / float(dts[i])
    if n > 1 and is_valid_yaw[0] and is_valid_yaw[1]:
        d_omega_z[0] = d_omega_z[1]

    smoothed_d_omega = np.full(n, np.nan, dtype=np.float64)
    for i in range(n):
        if not is_valid_yaw[i]:
            continue
        win = [
            float(d_omega_z[j])
            for j in range(max(0, i - 2), min(n, i + 3))
            if is_valid_yaw[j]
        ]
        smoothed_d_omega[i] = float(np.mean(win)) if win else 0.0

    f_yf = np.full(n, np.nan, dtype=np.float64)
    f_yr = np.full(n, np.nan, dtype=np.float64)
    f_xf = np.full(n, np.nan, dtype=np.float64)
    f_xr = np.full(n, np.nan, dtype=np.float64)

    for i in range(n):
        if not is_valid_yaw[i]:
            continue
        if glat_arr is not None and np.isfinite(glat_arr[i]):
            lat_g = float(glat_arr[i])
        else:
            lat_g = (float(speeds_ms[i]) * float(yaw_rates_rad[i])) / g_acc

        lon_g = float(glon_arr[i]) if (glon_arr is not None and np.isfinite(glon_arr[i])) else 0.0
        moment_term = moment_coeff * float(smoothed_d_omega[i])

        fy_front = w_f * lat_g + moment_term
        fy_rear = w_r * lat_g - moment_term

        if steer_arr is not None and np.isfinite(steer_arr[i]):
            delta_rad = math.radians(float(steer_arr[i]))
        else:
            v = float(speeds_ms[i])
            if v > 2.0:
                delta_rad = max(-0.5, min(0.5, (wheelbase_m * float(yaw_rates_rad[i])) / v))
            else:
                delta_rad = 0.0

        sin_delta = math.sin(delta_rad)
        f_yf[i] = fy_front
        f_yr[i] = fy_rear
        f_xf[i] = -fy_front * sin_delta
        f_xr[i] = lon_g + fy_front * sin_delta

    return f_yf, f_yr, f_xf, f_xr


# ---------------------------------------------------------------------------
# Registered Calculators
# ---------------------------------------------------------------------------

def _deps_sim_local_to_gps(
    metadata: dict[str, Any], track_info: dict[str, Any], _aux: dict[str, str]
) -> str:
    return _stable_sha256({
        'league': metadata.get('league'),
        'track_name': metadata.get('track_name'),
        'track_configuration': metadata.get('track_configuration'),
        'origin_lat': metadata.get('track_origin_lat'),
        'origin_lon': metadata.get('track_origin_lon'),
        'origin_heading': metadata.get('track_origin_heading'),
        'db_origin': track_info.get('origin') if track_info else None,
    })


@register_calculator('sim_local_to_gps', version=1, deps_fn=_deps_sim_local_to_gps)
def _calc_sim_local_to_gps(ctx: CalculatorContext) -> list[str]:
    if 'x' not in ctx.primary_cols or 'z' not in ctx.primary_cols:
        return []
    has_raw_gps = (
        'Latitude' in ctx.primary_cols
        and 'Longitude' in ctx.primary_cols
        and bool(np.any(np.isfinite(ctx.primary_cols['Latitude']) & (ctx.primary_cols['Latitude'] != 0.0)))
    )
    if has_raw_gps:
        return []
    resolved_track = str((ctx.track_info or {}).get('track_name') or ctx.metadata.get('track_name') or '')
    try:
        origin = resolve_simulator_origin(ctx.metadata, resolved_track)
    except ValueError:
        origin = None
    if origin is None:
        return []
    lat_arr, lon_arr = compute_gps_from_local_xz(ctx.primary_cols['x'], ctx.primary_cols['z'], origin)
    ctx.derived_cols['Latitude'] = lat_arr
    ctx.derived_cols['Longitude'] = lon_arr
    return ['Latitude', 'Longitude']


def _deps_track_lap_indexing(
    metadata: dict[str, Any], track_info: dict[str, Any], _aux: dict[str, str]
) -> str:
    return _stable_sha256({
        'sim_deps': _deps_sim_local_to_gps(metadata, track_info, _aux),
        'lap_length': track_info.get('lap_length') if track_info else None,
        'center_line': track_info.get('center_line') if track_info else None,
    })


@register_calculator('track_lap_indexing', version=1, deps_fn=_deps_track_lap_indexing)
def _calc_track_lap_indexing(ctx: CalculatorContext) -> list[str]:
    lat_arr = ctx.derived_cols.get('Latitude', ctx.primary_cols.get('Latitude'))
    lon_arr = ctx.derived_cols.get('Longitude', ctx.primary_cols.get('Longitude'))
    time_arr = ctx.primary_cols.get('Time', np.zeros(0, dtype=np.float64))
    n = len(time_arr)
    if n == 0:
        return []

    if lat_arr is None or lon_arr is None:
        lat_arr = np.full(n, np.nan, dtype=np.float64)
        lon_arr = np.full(n, np.nan, dtype=np.float64)

    course = compute_course_deg(lat_arr, lon_arr)
    ctx.course_deg = course

    written: list[str] = []
    if 'Direction of Travel' in ctx.primary_cols:
        valid_coords = np.isfinite(lat_arr) & np.isfinite(lon_arr) & ~((lat_arr == 0.0) & (lon_arr == 0.0))
        raw_dot = ctx.primary_cols['Direction of Travel']
        dot_out = np.where(valid_coords | np.isfinite(raw_dot), course % 360.0, np.nan)
        ctx.derived_cols['Direction of Travel'] = dot_out
        written.append('Direction of Travel')

    centerline = ctx.track_info.get('center_line') if ctx.track_info else None
    has_valid_coords = bool(np.any(np.isfinite(lat_arr) & np.isfinite(lon_arr)))
    if centerline and has_valid_coords:
        cl_lat = np.array([p['lat'] for p in centerline], dtype=np.float64)
        cl_lon = np.array([p['lon'] for p in centerline], dtype=np.float64)
        cl_dist = np.array([p['dist'] for p in centerline], dtype=np.float64)
        lap_length = float(ctx.track_info.get('lap_length', 1000.0))
        lap_col_int = _telemetry_native.detect_lap_crossings(
            lat_arr, lon_arr, cl_lat, cl_lon, cl_dist, lap_length
        )
        lap_dist_m = _telemetry_native.interpolate_lap_distances(
            lat_arr, lon_arr, lap_col_int, cl_lat, cl_lon, cl_dist, lap_length
        )
        lap_col = lap_col_int.astype(np.float64)
    else:
        raw_lap = ctx.primary_cols.get('Lap')
        if raw_lap is not None:
            lap_col = np.where(np.isfinite(raw_lap) & (raw_lap >= 0.0), np.round(raw_lap), 1.0)
        else:
            lap_col = np.ones(n, dtype=np.float64)
        raw_dist = ctx.primary_cols.get('Lap Distance')
        if raw_dist is None:
            raw_dist = ctx.primary_cols.get('Lap Distance (m)')
        lap_dist_m = raw_dist.copy() if raw_dist is not None else np.full(n, np.nan, dtype=np.float64)

    ctx.derived_cols['Lap'] = lap_col
    ctx.derived_cols['Lap Distance'] = lap_dist_m
    ctx.laps = build_laps_manifest(lap_col, lap_dist_m, time_arr)
    written.extend(['Lap', 'Lap Distance'])
    return written


def _deps_sim_quat_kinematics(
    metadata: dict[str, Any], track_info: dict[str, Any], _aux: dict[str, str]
) -> str:
    return _deps_sim_local_to_gps(metadata, track_info, _aux)


@register_calculator('sim_quat_kinematics', version=1, deps_fn=_deps_sim_quat_kinematics)
def _calc_sim_quat_kinematics(ctx: CalculatorContext) -> list[str]:
    p = ctx.primary_cols
    has_quat = any(k in p for k in ('Ori Quat X', 'q_x', 'Ori Quat W'))
    if not has_quat:
        return []
    n = len(p.get('Time', np.zeros(0)))
    if n == 0:
        return []

    def _col(primary_key: str, alt_key: str, default_val: float) -> np.ndarray:
        arr = p.get(primary_key)
        if arr is None:
            arr = p.get(alt_key)
        if arr is None:
            return np.full(n, default_val, dtype=np.float64)
        return np.where(np.isfinite(arr), arr, default_val)

    q_x = _col('Ori Quat X', 'q_x', 0.0)
    q_y = _col('Ori Quat Y', 'q_y', 0.0)
    q_z = _col('Ori Quat Z', 'q_z', 0.0)
    q_w = _col('Ori Quat W', 'q_w', 1.0)

    siny_cosp = 2.0 * (q_w * q_y + q_x * q_z)
    cosy_cosp = 1.0 - 2.0 * (q_y * q_y + q_z * q_z)
    kart_heading = np.degrees(np.arctan2(siny_cosp, cosy_cosp)) % 360.0
    course_val = (ctx.course_deg if ctx.course_deg is not None else np.zeros(n)) % 360.0

    ctx.derived_cols['Kart Heading'] = kart_heading
    ctx.derived_cols['Yaw'] = kart_heading.copy()
    written = ['Kart Heading', 'Yaw']

    has_toe = any(k in p for k in ('Toe FL', 'toe_fl'))
    if has_toe:
        toe_fl = _col('Toe FL', 'toe_fl', 0.0)
        toe_fr = _col('Toe FR', 'toe_fr', 0.0)
        wa_fl = np.degrees(toe_fl)
        wa_fr = np.degrees(toe_fr)
        slip_front = (course_val - (kart_heading + (wa_fl + wa_fr) / 2.0) + 360.0) % 360.0 - 180.0
        slip_rear = (course_val - kart_heading + 360.0) % 360.0 - 180.0
        ctx.derived_cols['Wheel Angle FL'] = wa_fl
        ctx.derived_cols['Wheel Angle FR'] = wa_fr
        ctx.derived_cols['Wheel Heading FL'] = (kart_heading + wa_fl) % 360.0
        ctx.derived_cols['Wheel Heading FR'] = (kart_heading + wa_fr) % 360.0
        ctx.derived_cols['Slip Angle Front'] = slip_front
        ctx.derived_cols['Slip Angle Rear'] = slip_rear
        written.extend([
            'Wheel Angle FL', 'Wheel Angle FR',
            'Wheel Heading FL', 'Wheel Heading FR',
            'Slip Angle Front', 'Slip Angle Rear',
        ])
    else:
        slip_rear = (course_val - kart_heading + 360.0) % 360.0 - 180.0
        ctx.derived_cols['Slip Angle Rear'] = slip_rear
        written.append('Slip Angle Rear')

    return written


def _deps_gyro_rear_slip(
    metadata: dict[str, Any], _track_info: dict[str, Any], _aux: dict[str, str]
) -> str:
    return _stable_sha256({'distance_to_rear_axle': metadata.get('distance_to_rear_axle')})


@register_calculator('gyro_rear_slip', version=1, deps_fn=_deps_gyro_rear_slip)
def _calc_gyro_rear_slip(ctx: CalculatorContext) -> list[str]:
    p = ctx.primary_cols
    if any(k in p for k in ('Ori Quat X', 'q_x', 'Ori Quat W')):
        return []
    time_arr = p.get('Time')
    yaw_arr = p.get('Yaw Rate')
    dist_rear = ctx.metadata.get('distance_to_rear_axle')
    if time_arr is None or yaw_arr is None or dist_rear is None or len(time_arr) == 0:
        return []
    if not np.any(np.isfinite(yaw_arr)):
        return []
    if 'Slip Angle Rear' in p:
        valid_raw = int(np.sum(np.isfinite(p['Slip Angle Rear'])))
        if valid_raw > len(time_arr) * 0.5:
            return []

    raw_speed = p.get('Speed')
    speeds_ms = np.where(np.isfinite(raw_speed), raw_speed, 0.0) if raw_speed is not None else np.zeros(len(time_arr))
    course = ctx.course_deg if ctx.course_deg is not None else np.zeros(len(time_arr))
    heading, slip_rear = compute_gyro_slip_arrays(
        time_arr=time_arr,
        yaw_rate_arr=yaw_arr,
        glat_arr=p.get('GForceLat'),
        course=course,
        speeds_ms=speeds_ms,
        distance_to_rear_axle=float(dist_rear),
    )
    ctx.derived_cols['Kart Heading'] = heading
    ctx.derived_cols['Yaw'] = heading.copy()
    ctx.derived_cols['Slip Angle Rear'] = slip_rear
    return ['Kart Heading', 'Yaw', 'Slip Angle Rear']


def _deps_gyro_axle_forces(
    metadata: dict[str, Any], _track_info: dict[str, Any], _aux: dict[str, str]
) -> str:
    return _stable_sha256({'distance_to_rear_axle': metadata.get('distance_to_rear_axle')})


@register_calculator('gyro_axle_forces', version=1, deps_fn=_deps_gyro_axle_forces)
def _calc_gyro_axle_forces(ctx: CalculatorContext) -> list[str]:
    p = ctx.primary_cols
    if any(k in p for k in ('Ori Quat X', 'q_x', 'Ori Quat W')):
        return []
    time_arr = p.get('Time')
    yaw_arr = p.get('Yaw Rate')
    if time_arr is None or yaw_arr is None or len(time_arr) == 0 or not np.any(np.isfinite(yaw_arr)):
        return []

    has_wheel_forces = False
    for wf_col in ('Lat Force FL', 'Lat Force FR'):
        if wf_col in p:
            arr = p[wf_col]
            if bool(np.any(np.isfinite(arr) & (np.abs(arr) > 1e-4))):
                has_wheel_forces = True
                break
    has_axle_forces = (
        'Lat Force Front' in p
        and int(np.sum(np.isfinite(p['Lat Force Front']))) > len(time_arr) * 0.5
    )
    if has_wheel_forces or has_axle_forces:
        return []

    raw_speed = p.get('Speed')
    speeds_ms = np.where(np.isfinite(raw_speed), raw_speed, 0.0) if raw_speed is not None else np.zeros(len(time_arr))
    dist_raw = ctx.metadata.get('distance_to_rear_axle')
    dist_val = float(dist_raw) if dist_raw is not None else None

    f_yf, f_yr, f_xf, f_xr = compute_gyro_force_arrays(
        time_arr=time_arr,
        yaw_rate_arr=yaw_arr,
        glat_arr=p.get('GForceLat'),
        glon_arr=p.get('GForceLon'),
        steer_arr=p.get('Steering Angle'),
        speeds_ms=speeds_ms,
        distance_to_rear_axle=dist_val,
    )
    ctx.derived_cols['Lat Force Front'] = f_yf
    ctx.derived_cols['Lat Force Rear'] = f_yr
    ctx.derived_cols['Long Force Front'] = f_xf
    ctx.derived_cols['Long Force Rear'] = f_xr
    return ['Lat Force Front', 'Lat Force Rear', 'Long Force Front', 'Long Force Rear']


def _deps_aux_stream_align(
    metadata: dict[str, Any], _track_info: dict[str, Any], aux_brtc_paths: dict[str, str]
) -> str:
    aux_state: dict[str, Any] = {}
    for stream_name, aux_path in sorted(aux_brtc_paths.items()):
        try:
            st = os.stat(aux_path)
            aux_state[stream_name] = [st.st_mtime_ns, st.st_size]
        except OSError:
            aux_state[stream_name] = None
    return _stable_sha256({
        'session_start_date': metadata.get('session_start_date'),
        'aux': aux_state,
    })


def _estimate_stream_dt(sorted_times: np.ndarray, default_dt: float = 0.01) -> float:
    """Estimate regular sampling step dt while ignoring large pit/disconnect gaps."""
    if len(sorted_times) <= 1:
        return default_dt
    diffs = np.diff(sorted_times)
    diffs = diffs[diffs > 1e-9]
    if diffs.size == 0:
        return default_dt
    p25 = float(np.percentile(diffs, 25))
    regular = diffs[diffs <= p25 * 4.0]
    return float(np.median(regular)) if regular.size > 0 else float(np.median(diffs))


def _build_primary_time_bins(
    p_sorted: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Construct non-overlapping Voronoi bins [b_left, b_right) around sorted primary timestamps."""
    k = len(p_sorted)
    median_dt_prim = _estimate_stream_dt(p_sorted, default_dt=0.01)

    half_dt = 0.5 * median_dt_prim
    b_left = np.empty(k, dtype=np.float64)
    b_right = np.empty(k, dtype=np.float64)
    b_left[0] = p_sorted[0] - half_dt
    b_right[-1] = p_sorted[-1] + half_dt

    if k > 1:
        steps = p_sorted[1:] - p_sorted[:-1]
        midpoints = 0.5 * (p_sorted[:-1] + p_sorted[1:])
        contiguous = (steps > 0.0) & (steps <= 2.5 * median_dt_prim)
        b_right[:-1] = np.where(contiguous, midpoints, p_sorted[:-1] + half_dt)
        b_left[1:] = np.where(contiguous, midpoints, p_sorted[1:] - half_dt)

    degenerate = b_right <= b_left
    if np.any(degenerate):
        b_left[degenerate] = p_sorted[degenerate] - half_dt
        b_right[degenerate] = p_sorted[degenerate] + half_dt

    return b_left, b_right, median_dt_prim


def _align_aux_column_to_primary_bins(
    p_sorted: np.ndarray,
    b_left: np.ndarray,
    b_right: np.ndarray,
    median_dt_prim: float,
    t_sorted: np.ndarray,
    raw_vals: np.ndarray,
) -> np.ndarray:
    """Downsample high-rate aux channels via exact bin averaging; interpolate coarser aux streams."""
    k = len(p_sorted)
    aligned_sorted = np.full(k, np.nan, dtype=np.float64)
    valid_v = np.isfinite(raw_vals)
    if not np.any(valid_v):
        return aligned_sorted

    tv = t_sorted[valid_v]
    vv = raw_vals[valid_v]

    median_dt_col = _estimate_stream_dt(tv, default_dt=math.inf)

    # High-rate auxiliary stream (e.g. 500 Hz Spiker:bit -> 100 Hz primary): average all samples in each bin
    if median_dt_col <= 0.5 * median_dt_prim:
        idx_l = np.searchsorted(tv, b_left, side='left')
        idx_r = np.searchsorted(tv, b_right, side='left')
        counts = idx_r - idx_l
        has_bin_samples = counts > 0
        if np.any(has_bin_samples):
            csum = np.empty(len(vv) + 1, dtype=np.float64)
            csum[0] = 0.0
            np.cumsum(vv, dtype=np.float64, out=csum[1:])
            aligned_sorted[has_bin_samples] = (
                csum[idx_r[has_bin_samples]] - csum[idx_l[has_bin_samples]]
            ) / counts[has_bin_samples]
        return aligned_sorted

    # Lower- or equal-rate auxiliary stream: linear interpolation within gap_threshold
    tnan = t_sorted[~valid_v]
    if tnan.size > 0:
        nan_in_bin = (
            np.searchsorted(tnan, b_right, side='left')
            - np.searchsorted(tnan, b_left, side='left')
        ) > 0
    else:
        nan_in_bin = np.zeros(k, dtype=bool)

    gap_threshold = max(2.0, median_dt_col * 10.0) if math.isfinite(median_dt_col) else 2.0
    in_range = (
        (~nan_in_bin)
        & (p_sorted >= float(tv[0]) - 0.05)
        & (p_sorted <= float(tv[-1]) + 0.05)
    )
    if len(tv) > 1 and np.any(in_range):
        r_idx = np.searchsorted(tv, p_sorted, side='right')
        l_idx = np.clip(r_idx - 1, 0, len(tv) - 1)
        r_clamped = np.clip(r_idx, 0, len(tv) - 1)
        span_tv = tv[r_clamped] - tv[l_idx]
        in_range = in_range & (span_tv <= gap_threshold)
    if np.any(in_range):
        aligned_sorted[in_range] = np.interp(p_sorted[in_range], tv, vv)

    return aligned_sorted


@register_calculator('aux_stream_align', version=2, deps_fn=_deps_aux_stream_align)
def _calc_aux_stream_align(ctx: CalculatorContext) -> list[str]:
    if not ctx.aux_brtc_paths:
        return []
    prim_time = ctx.primary_cols.get('Time')
    if prim_time is None or len(prim_time) == 0:
        return []

    valid_prim_mask = np.isfinite(prim_time)
    valid_prim_indices = np.flatnonzero(valid_prim_mask)
    has_valid_prim = valid_prim_indices.size > 0
    if has_valid_prim:
        p_valid = prim_time[valid_prim_indices]
        p_order = np.argsort(p_valid, kind='mergesort')
        p_sorted = p_valid[p_order]
        b_left, b_right, median_dt_prim = _build_primary_time_bins(p_sorted)
    else:
        p_order = np.empty(0, dtype=np.int64)
        p_sorted = np.empty(0, dtype=np.float64)
        b_left = np.empty(0, dtype=np.float64)
        b_right = np.empty(0, dtype=np.float64)
        median_dt_prim = 0.01

    prim_date_str = str(ctx.metadata.get('session_start_date') or '')
    prim_date: datetime.date | None = None
    if prim_date_str:
        try:
            prim_date = datetime.date.fromisoformat(prim_date_str[:10])
        except ValueError:
            prim_date = None

    written: list[str] = []
    for stream_name, aux_path in sorted(ctx.aux_brtc_paths.items()):
        if not os.path.exists(aux_path):
            continue
        try:
            aux_manifest = telemetry_brtc.read_brtc_manifest(aux_path)
            aux_cols = telemetry_brtc.read_brtc_columns(aux_path, manifest=aux_manifest)
        except Exception as e:
            logger.warning('Failed to read auxiliary stream %s (%s): %s', stream_name, aux_path, e)
            continue

        aux_time = aux_cols.get('Time')
        if aux_time is None or len(aux_time) == 0:
            continue

        day_offset_s = 0.0
        aux_date_str = str((aux_manifest.get('metadata') or {}).get('session_start_date') or '')
        if prim_date is not None and aux_date_str:
            try:
                aux_date = datetime.date.fromisoformat(aux_date_str[:10])
                day_offset_s = float((aux_date - prim_date).days * 86400)
            except ValueError:
                day_offset_s = 0.0

        valid_t_mask = np.isfinite(aux_time)
        if not np.any(valid_t_mask):
            continue
        t_src = aux_time[valid_t_mask] + day_offset_s
        order = np.argsort(t_src, kind='mergesort')
        t_sorted = t_src[order]

        for col_name in aux_manifest.get('column_order', list(aux_cols.keys())):
            if col_name == 'Time' or col_name not in aux_cols:
                continue
            raw_vals = aux_cols[col_name][valid_t_mask][order]
            out_name = (
                col_name
                if (col_name not in ctx.primary_cols and col_name not in ctx.derived_cols)
                else f'{stream_name}:{col_name}'
            )
            aligned = np.full(len(prim_time), np.nan, dtype=np.float64)
            if has_valid_prim:
                aligned_sorted = _align_aux_column_to_primary_bins(
                    p_sorted=p_sorted,
                    b_left=b_left,
                    b_right=b_right,
                    median_dt_prim=median_dt_prim,
                    t_sorted=t_sorted,
                    raw_vals=raw_vals,
                )
                aligned[valid_prim_indices[p_order]] = aligned_sorted
            ctx.derived_cols[out_name] = aligned
            written.append(out_name)

    return written


# ---------------------------------------------------------------------------
# Execution & Staleness Verification
# ---------------------------------------------------------------------------

def expected_calculator_provenance(
    metadata: dict[str, Any],
    track_info: dict[str, Any] | None,
    aux_brtc_paths: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Compute the expected {calc_name: {version, deps_hash}} dictionary for current inputs."""
    t_info = track_info or {}
    aux_map = aux_brtc_paths or {}
    out: dict[str, dict[str, Any]] = {}
    for calc in CALCULATOR_REGISTRY:
        out[calc.name] = {
            'version': calc.version,
            'deps_hash': calc.deps_fn(metadata, t_info, aux_map),
        }
    return out


def is_derived_manifest_fresh(
    derived_manifest: dict[str, Any],
    primary_row_count: int,
    metadata: dict[str, Any],
    track_info: dict[str, Any] | None,
    aux_brtc_paths: dict[str, str] | None = None,
) -> bool:
    """Return True if .derived.brtc matches primary row count and all calculator versions + hashes."""
    if int(derived_manifest.get('row_count', -1)) != int(primary_row_count):
        return False
    recorded = derived_manifest.get('calculators')
    if not isinstance(recorded, dict):
        return False
    expected = expected_calculator_provenance(metadata, track_info, aux_brtc_paths)
    has_aux = bool(aux_brtc_paths)
    for name, exp_info in expected.items():
        rec_info = recorded.get(name)
        if not isinstance(rec_info, dict):
            return False
        if rec_info.get('deps_hash') != exp_info['deps_hash']:
            return False
        if rec_info.get('version') != exp_info['version']:
            if name == 'aux_stream_align' and not has_aux and not (rec_info.get('columns') or []):
                continue
            return False
    return True


def compute_derived_columns_and_laps(
    primary_cols: dict[str, np.ndarray],
    metadata: dict[str, Any],
    track_info: dict[str, Any] | None = None,
    aux_brtc_paths: dict[str, str] | None = None,
) -> tuple[dict[str, np.ndarray], list[str], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Run all registered calculators and return (derived_cols, column_order, laps, provenance)."""
    t_info = track_info or {}
    aux_map = aux_brtc_paths or {}
    ctx = CalculatorContext(
        primary_cols=primary_cols,
        derived_cols={},
        metadata=metadata,
        track_info=t_info,
        aux_brtc_paths=aux_map,
        laps=[],
    )
    col_order: list[str] = []
    provenance: dict[str, dict[str, Any]] = {}
    for calc in CALCULATOR_REGISTRY:
        written = calc.compute_fn(ctx)
        for col in written:
            if col not in col_order:
                col_order.append(col)
        provenance[calc.name] = {
            'version': calc.version,
            'deps_hash': calc.deps_fn(metadata, t_info, aux_map),
            'columns': written,
        }
    return ctx.derived_cols, col_order, ctx.laps, provenance
