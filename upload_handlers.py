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
import json
import csv
import math
import datetime
import time
import logging
from typing import Any, Iterable
from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd
from flask import Blueprint, request, jsonify
from werkzeug.exceptions import BadRequest, Forbidden, HTTPException
from database import db, config
from utils import populate_db
from race_tools import rfactor, sanitize
import auth
import aliases
from plot_handlers import invalidate_plot_cache

logger = logging.getLogger(__name__)
upload_blueprint = Blueprint('upload', __name__)


@upload_blueprint.errorhandler(HTTPException)
def handle_http_exception(e: HTTPException) -> Any:
    """Return JSON instead of HTML for HTTP errors raised within this blueprint."""
    return jsonify({'error': e.description}), e.code or 500


# Directory for buffering in-progress uploads
DATA_DIR = os.path.realpath(os.path.expanduser(config['data_dir']))

MAX_METADATA_SIZE = 200 * 1024 * 1024  # 200 MB
STREAM_FLUSH_INTERVAL_SEC = 15.0


@dataclass
class CSVSessionMetadata:
    driver_name: str
    track_name: str
    league: str
    class_name: str
    session_name: str
    session_start_datetime: datetime.datetime
    kart_number: str | None = None
    track_conditions: str | None = None
    temperature: str | None = None
    weather: str | None = None
    distance_to_rear_axle: float | None = None


def parse_csv_metadata_rows(metadata_rows: Iterable[Iterable[str]]) -> CSVSessionMetadata:
    """Parses list of CSV rows containing metadata (key, value) pairs
    and returns a standardized CSVSessionMetadata dataclass.
    """
    driver_name = None
    track_name = None
    league = None
    class_name = None
    session_name = None
    session_start_datetime = None
    kart_number = None
    track_conditions = None
    temperature = None
    weather = None
    distance_to_rear_axle = None

    date_str = None
    time_str = None

    for row_raw in metadata_rows:
        row = list(row_raw)
        if not row or len(row) < 2:
            continue
        key = row[0].strip().lower()
        val = row[1].strip()
        if not val:
            continue
        if key == 'driver name':
            driver_name = val
        elif key == 'track name':
            track_name = val
        elif key == 'date':
            date_str = val
        elif key == 'time':
            time_str = val
        elif key == 'league':
            league = val
        elif key == 'class':
            class_name = val
        elif key == 'session':
            session_name = val
        elif key == 'kart_number':
            kart_number = val
        elif key == 'conditions':
            track_conditions = val
        elif key == 'temperature':
            temperature = val
        elif key == 'weather':
            weather = val
        elif key == 'distance to rear axle':
            try:
                distance_to_rear_axle = float(val)
            except (ValueError, TypeError):
                distance_to_rear_axle = None

    if date_str and time_str:
        session_start_datetime_str = f'{date_str} {time_str[:5]}'
        try:
            session_start_datetime = datetime.datetime.strptime(session_start_datetime_str, '%Y-%m-%d %H:%M')
        except ValueError:
            raise BadRequest('Invalid session_start_datetime format. Must be YYYY-MM-DD HH:MM.')

    # Validate that all required fields are present
    if not league:
        raise BadRequest('League is missing')
    if not class_name:
        raise BadRequest('Class is missing')
    if not track_name:
        raise BadRequest('Track name is missing')
    if not session_name:
        raise BadRequest('Session is missing')
    if not session_start_datetime:
        raise BadRequest('Date/Time is missing')
    if not driver_name:
        raise BadRequest('Driver name is missing')

    return CSVSessionMetadata(
        driver_name=driver_name,
        track_name=track_name,
        league=league,
        class_name=class_name,
        session_name=session_name,
        session_start_datetime=session_start_datetime,
        kart_number=kart_number,
        track_conditions=track_conditions,
        temperature=temperature,
        weather=weather,
        distance_to_rear_axle=distance_to_rear_axle
    )


def parse_streamed_csv_records(csv_content: str) -> tuple[list[dict], dict]:
    """Parses the streamed CSV string, skipping any RaceBox/metadata headers if present,
    and returns a list of dictionaries (telemetry records) and extracted metadata.
    """
    records = []
    reader = csv.reader(csv_content.splitlines())
    rows = list(reader)

    # Find the blank line separator
    blank_idx = -1
    for i, row in enumerate(rows):
        if not row or all(cell.strip() == '' for cell in row):
            blank_idx = i
            break

    metadata_rows = []
    csv_rows = []
    if blank_idx != -1:
        metadata_rows = rows[:blank_idx]
        csv_rows = rows[blank_idx+1:]
    else:
        csv_rows = rows

    metadata = parse_csv_metadata_rows(metadata_rows)
    metadata_dict = {k: v for k, v in asdict(metadata).items() if v is not None}

    header = None
    data_start_idx = -1
    for i, row in enumerate(csv_rows):
        if row and (row[0] == 'Record' or row[0] == 'Time' or 'Time' in row):
            header = row
            data_start_idx = i + 1
            break

    if not header:
        # Fallback: assume first row of CSV rows is the header
        if csv_rows:
            header = csv_rows[0]
            data_start_idx = 1

    if header and data_start_idx != -1:
        for row in csv_rows[data_start_idx:]:
            if not row or len(row) < len(header):
                continue
            # Map row columns to header fields
            record = {}
            for idx, h_name in enumerate(header):
                if idx < len(row):
                    record[h_name] = row[idx]
            records.append(record)

    return records, metadata_dict


def is_lap_valid(lap_dists: np.ndarray, turns: list[dict], lap_length: float) -> bool:
    """Determines whether a lap's telemetry data is valid.

    A lap is considered valid if:
    1. It contains at least one telemetry distance point.
    2. The lap's telemetry points span across the start/finish line (within a 10m buffer of
       both the start of the lap and the end of the lap).
    3. If track turns are defined, the driver must have crossed each turn (within a 5m buffer
       around the turn's start and end boundaries).
    4. If track turns are not defined, the total distance covered within the lap must be
       at least 90% of the total lap length.

    Args:
        lap_dists: An array of distance measurements (in meters) along the lap for each
            telemetry point.
        turns: A list of turn metadata dictionaries, where each turn has optional 'start'
            and 'end' distance markers (in meters).
        lap_length: The standard length of the lap in meters.

    Returns:
        True if the lap telemetry is valid according to the threshold criteria, False otherwise.
    """
    if len(lap_dists) == 0:
        return False
    sf_buffer = 10.0
    crossed_start = np.any(lap_dists <= sf_buffer)
    crossed_end = np.any(lap_dists >= lap_length - sf_buffer)
    if turns:
        # With turns defined, we need both S/F boundary crossings confirmed.
        if not (crossed_start and crossed_end):
            return False
        buffer = 5.0
        for turn in turns:
            start = turn.get('start')
            end = turn.get('end')
            if start is None or end is None:
                continue
            if start <= end:
                crossed = np.any((lap_dists >= start - buffer) & (lap_dists <= end + buffer))
            else:
                crossed = np.any((lap_dists >= start - buffer) | (lap_dists <= end + buffer))
            if not crossed:
                return False
        return True
    else:
        # Without turns we rely on dist_covered alone. crossed_end is NOT checked
        # as a hard gate because interpolate_lap_distances can map end-of-lap points
        # to ~0m via a modulo wrap, causing false negatives.
        if not crossed_start:
            return False
        dist_covered = np.max(lap_dists) - np.min(lap_dists)
        # A large dist_covered is sufficient even if crossed_end failed due to
        # the modulo-wrap issue in interpolate_lap_distances (points right before
        # the S/F line can be mapped to ~0m instead of ~lap_length by the % op).
        return dist_covered >= lap_length * 0.9


def interpolate_lap_distances(
    lat_arr: np.ndarray,
    lon_arr: np.ndarray,
    lap_col: np.ndarray,
    centerline: list[dict],
    lap_length: float,
    d2_matrix: np.ndarray
) -> np.ndarray:
    """Interpolates lap distance along the track by projecting coordinates onto track segments."""
    lap_distance_m = np.zeros(len(lat_arr))
    cl_lat = np.array([p['lat'] for p in centerline])
    cl_lon = np.array([p['lon'] for p in centerline])
    cl_dist = np.array([p['dist'] for p in centerline])
    N = len(centerline)

    for lap_num in sorted(np.unique(lap_col)):
        mask = (lap_col == lap_num)
        n_lap = np.sum(mask)
        if n_lap == 0:
            continue

        d2 = d2_matrix[mask, :]
        progress = np.linspace(0, 1, n_lap)[:, None]
        track_progress = cl_dist / lap_length
        epsilon = 1e-10
        d2_biased = d2 + epsilon * (track_progress - progress)**2
        idxs = np.argmin(d2_biased, axis=1)

        lat_P = lat_arr[mask]
        lon_P = lon_arr[mask]

        lat_A = cl_lat[idxs]
        lon_A = cl_lon[idxs]
        dist_A = cl_dist[idxs]

        idx_prev = (idxs - 1) % N
        idx_next = (idxs + 1) % N

        lat_prev = cl_lat[idx_prev]
        lon_prev = cl_lon[idx_prev]
        dist_prev = cl_dist[idx_prev]

        lat_next = cl_lat[idx_next]
        lon_next = cl_lon[idx_next]
        dist_next = cl_dist[idx_next]

        cos_lat = np.cos(np.radians(lat_A))
        dlat_P = lat_P - lat_A
        dlon_P = (lon_P - lon_A) * cos_lat

        # Next segment projection
        dlat_next = lat_next - lat_A
        dlon_next = (lon_next - lon_A) * cos_lat
        len2_next = dlat_next**2 + dlon_next**2
        dot_next = dlat_P * dlat_next + dlon_P * dlon_next
        t_next = np.zeros_like(dot_next)
        valid_next = len2_next > 1e-15
        t_next[valid_next] = dot_next[valid_next] / len2_next[valid_next]

        # Previous segment projection
        dlat_prev = lat_prev - lat_A
        dlon_prev = (lon_prev - lon_A) * cos_lat
        len2_prev = dlat_prev**2 + dlon_prev**2
        dot_prev = dlat_P * dlat_prev + dlon_P * dlon_prev
        t_prev = np.zeros_like(dot_prev)
        valid_prev = len2_prev > 1e-15
        t_prev[valid_prev] = dot_prev[valid_prev] / len2_prev[valid_prev]

        t_next_clipped = np.clip(t_next, -0.5, 0.5)
        t_prev_clipped = np.clip(t_prev, -0.5, 0.5)

        diff_next = (dist_next - dist_A + 0.5 * lap_length) % lap_length - 0.5 * lap_length
        diff_prev = (dist_prev - dist_A + 0.5 * lap_length) % lap_length - 0.5 * lap_length

        dist_if_next = dist_A + t_next_clipped * diff_next
        dist_if_prev = dist_A + t_prev_clipped * diff_prev

        use_next = t_next > t_prev
        interpolated_dist = np.where(use_next, dist_if_next, dist_if_prev)

        # Clamp any interpolated distance that overshoots lap_length back to just
        # below it. Without this, the % operator wraps end-of-lap points (e.g.
        # 1053m for a 1052.58m lap) to near-zero, making is_lap_valid incorrectly
        # conclude that no points are near the end of the lap (crossed_end=False).
        interpolated_dist = np.where(
            interpolated_dist >= lap_length,
            lap_length - 1e-3,
            interpolated_dist,
        )
        lap_distance_m[mask] = interpolated_dist % lap_length

    invalid_mask = np.isnan(lat_arr) | np.isnan(lon_arr)
    lap_distance_m[invalid_mask] = np.nan
    return lap_distance_m


def to_naive_datetime(val: Any) -> pd.Timestamp | None:
    if not val or pd.isna(val):
        return None
    try:
        dt = pd.to_datetime(val)
        if pd.isna(dt):
            return None
        if dt.tzinfo is not None:
            return dt.tz_localize(None)
        return dt
    except Exception:
        return None


def safe_float(val: Any, default: float = 0.0) -> float:
    if val is None or val == '':
        return default
    try:
        return float(val)
    except Exception:
        return default


def parse_optional_float(val: Any) -> float | None:
    """Parse float returning None if value is missing, empty, or NaN."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        if np.isnan(val):
            return None
        return float(val)
    val_str = str(val).strip()
    if val_str == '' or val_str.lower() in ('nan', 'none', 'null'):
        return None
    try:
        f = float(val_str)
        if math.isnan(f):
            return None
        return f
    except Exception:
        return None


def parse_coordinate(val: Any) -> float:
    """Parse coordinate, returning np.nan if uninitialized (None, empty, NaN, or 0.0)."""
    if val is None:
        return np.nan
    if isinstance(val, (int, float)):
        if np.isnan(val) or val == 0.0:
            return np.nan
        return float(val)
    val_str = str(val).strip()
    if val_str == '' or val_str.lower() in ('nan', 'none', 'null'):
        return np.nan
    try:
        f = float(val_str)
        if np.isnan(f) or f == 0.0:
            return np.nan
        return f
    except (ValueError, TypeError):
        return np.nan


def get_track_coordinate_reference(
    track_data: dict | None,
    lats: list[float] | np.ndarray,
    lons: list[float] | np.ndarray
) -> tuple[float | None, float | None, float]:
    """Get reference (latitude, longitude, max_distance_meters) for geographic sanity checks.

    Uses track centerline or origin if available, otherwise computes median coordinates
    of valid data points.
    """
    if track_data:
        centerline = track_data.get('center_line')
        if centerline and isinstance(centerline, list):
            cl_lat = [p['lat'] for p in centerline if isinstance(p, dict) and 'lat' in p]
            cl_lon = [p['lon'] for p in centerline if isinstance(p, dict) and 'lon' in p]
            if cl_lat and cl_lon:
                return float(np.mean(cl_lat)), float(np.mean(cl_lon)), 5000.0
        origin = track_data.get('origin')
        if origin and isinstance(origin, dict) and 'lat' in origin and 'lon' in origin:
            return float(origin['lat']), float(origin['lon']), 5000.0

    valid_coords = [
        (float(lat), float(lon)) for lat, lon in zip(lats, lons)
        if not np.isnan(lat) and not np.isnan(lon) and not (lat == 0.0 and lon == 0.0)
        and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0
    ]
    if not valid_coords:
        return None, None, 0.0

    v_lats = [c[0] for c in valid_coords]
    v_lons = [c[1] for c in valid_coords]
    return float(np.median(v_lats)), float(np.median(v_lons)), 10000.0


def is_coordinate_within_bounds(
    lat: float,
    lon: float,
    ref_lat: float | None,
    ref_lon: float | None,
    max_dist_m: float = 5000.0
) -> bool:
    """Check if coordinate is within geographic bounds and reasonable distance of track/median."""
    if np.isnan(lat) or np.isnan(lon) or (lat == 0.0 and lon == 0.0):
        return False
    if lat < -90.0 or lat > 90.0 or lon < -180.0 or lon > 180.0:
        return False
    if ref_lat is None or ref_lon is None:
        return True
    dlat = (lat - ref_lat) * 111_000.0
    dlon = (lon - ref_lon) * 111_000.0 * math.cos(math.radians(ref_lat))
    return (dlat * dlat + dlon * dlon) <= (max_dist_m * max_dist_m)


def infer_conditions_from_avg_track_wetness(records: list[dict]) -> str | None:
    """Derives session conditions from the 'Avg Track Wetness' column in telemetry records.

    Returns None if the column is absent or contains no valid values.
    """
    wetness_values = []
    for r in records:
        raw = r.get('Avg Track Wetness')
        if raw is not None and raw != '':
            try:
                wetness_values.append(float(raw))
            except (ValueError, TypeError):
                pass

    if not wetness_values:
        return None

    avg_wetness = sum(wetness_values) / len(wetness_values)
    if avg_wetness > 0.1:
        return 'Wet'
    elif avg_wetness > 0.02:
        return 'Damp'
    return 'Dry'


def load_existing_telemetry_lines(telemetry_path: str) -> list[str]:
    if not os.path.exists(telemetry_path):
        return []
    with open(telemetry_path, 'r', encoding='utf-8') as f:
        return f.read().splitlines()


def compute_gyro_rear_slip_angle(
    records: list[dict],
    course: np.ndarray,
    speeds_ms: np.ndarray,
    distance_to_rear_axle: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Computes chassis heading and rear tire slip angle from gyro yaw rate and GPS course.

    Uses straight-line intervals (low yaw rate, low lateral G, speed > 6 m/s) to
    anchor the heading to GPS course and estimate gyro zero-rate bias.
    Translates the velocity vector from the camera location to the rear axle using
    distance_to_rear_axle and the yaw rate.
    Points with missing/NaN gyro data are not assigned a slip angle (yielding NaN).
    """
    N = len(records)
    if N == 0:
        return np.zeros(0), np.zeros(0)

    # Extract time deltas between points
    dts = np.full(N, 0.1)
    timestamps = [to_naive_datetime(r.get('Time')) for r in records]

    for i in range(1, N):
        t_prev = timestamps[i - 1]
        t_curr = timestamps[i]
        if t_prev is not None and t_curr is not None:
            delta = (t_curr - t_prev).total_seconds()
            if 0 < delta <= 1.0:
                dts[i] = delta

    yaw_opts = [parse_optional_float(r.get('Yaw Rate')) for r in records]
    is_valid_yaw = np.array([y is not None for y in yaw_opts], dtype=bool)

    if not np.any(is_valid_yaw):
        return course.copy(), np.full(N, np.nan)

    yaw_rates = np.array([(y if y is not None else 0.0) for y in yaw_opts], dtype=float)
    glat = np.array([safe_float(r.get('GForceLat'), 0.0) for r in records])

    heading = np.zeros(N)
    slip_rear = np.full(N, np.nan)

    # Find initial anchor point where kart is moving and gyro is valid
    moving_idxs = np.where((speeds_ms > 3.0) & is_valid_yaw)[0]
    start_idx = int(moving_idxs[0]) if len(moving_idxs) > 0 else 0
    init_course = course[start_idx] if len(course) > start_idx else 0.0
    heading[:start_idx + 1] = init_course

    bias = 0.0
    bias_samples: list[float] = []

    def _calc_slip(cog: float, head: float, v: float, yr: float, b: float) -> float:
        if v < 2.0:
            return 0.0
        beta_cam_deg = (cog - head + 180.0) % 360.0 - 180.0
        beta_cam_rad = math.radians(beta_cam_deg)
        vx_cam = v * math.cos(beta_cam_rad)
        vy_cam = v * math.sin(beta_cam_rad)
        omega_rad = math.radians(-(yr - b))
        vy_rear = vy_cam - omega_rad * distance_to_rear_axle
        vx_rear = max(vx_cam, 0.5)
        return math.degrees(math.atan2(vy_rear, vx_rear))

    for k in range(0, start_idx + 1):
        if is_valid_yaw[k]:
            slip_rear[k] = _calc_slip(course[k], heading[k], speeds_ms[k], yaw_rates[k], bias)

    for i in range(start_idx + 1, N):
        cog = course[i]
        v = speeds_ms[i]

        if not is_valid_yaw[i]:
            # Missing gyro data: cannot integrate heading or calculate slip angle
            heading[i] = cog
            slip_rear[i] = np.nan
            continue

        dt = dts[i]
        yr = yaw_rates[i]
        lat_g = glat[i]

        if not is_valid_yaw[i - 1]:
            heading[i - 1] = course[i - 1]

        # Integrate heading: in compass coordinates (CW), dpsi/dt = -(yaw_rate - bias)
        dpsi = -(yr - bias) * dt
        psi_pred = (heading[i - 1] + dpsi) % 360.0

        # Straight-line detection: speed > 6 m/s, low yaw rate, low lateral G
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

        slip_rear[i] = _calc_slip(cog, heading[i], v, yr, bias)

    return heading, slip_rear


def compute_gyro_forces(
    records: list[dict],
    speeds_ms: np.ndarray,
    distance_to_rear_axle: float | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Computes front and rear lateral and longitudinal specific forces (in g)
    from gyro yaw rate, accelerometers (GForceLat, GForceLon), and speed.

    Governing vehicle dynamics (single-track / planar model):
      f_yf = (b / L) * a_y + (k_z^2 / (L * g)) * d_omega_z
      f_yr = (a / L) * a_y - (k_z^2 / (L * g)) * d_omega_z
      f_xf = -f_yf * sin(delta)
      f_xr = a_x + f_yf * sin(delta)

    Returns:
      (f_yf, f_yr, f_xf, f_xr) arrays in g, with NaNs where data is invalid/missing.
    """
    N = len(records)
    if N == 0:
        return np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(0)

    # Standard kart geometry (CIK-FIA senior chassis defaults)
    L = 1.04  # Wheelbase in meters
    g = 9.80665

    if distance_to_rear_axle is not None and 0.1 < distance_to_rear_axle < L:
        b = float(distance_to_rear_axle)
    else:
        b = 0.42 * L  # Default ~0.437 m
    a = L - b
    W_f = b / L
    W_r = a / L
    k_z = 0.48 * L  # Radius of gyration in yaw ~0.50 m
    moment_coeff = (k_z ** 2) / (L * g)

    # Extract time deltas between points
    dts = np.full(N, 0.1)
    timestamps = [to_naive_datetime(r.get('Time')) for r in records]
    for i in range(1, N):
        t_prev = timestamps[i - 1]
        t_curr = timestamps[i]
        if t_prev is not None and t_curr is not None:
            delta = (t_curr - t_prev).total_seconds()
            if 0 < delta <= 1.0:
                dts[i] = delta

    yaw_opts = [parse_optional_float(r.get('Yaw Rate')) for r in records]
    is_valid_yaw = np.array([y is not None for y in yaw_opts], dtype=bool)

    if not np.any(is_valid_yaw):
        nan_arr = np.full(N, np.nan)
        return nan_arr.copy(), nan_arr.copy(), nan_arr.copy(), nan_arr.copy()

    yaw_rates_deg = np.array([(y if y is not None else 0.0) for y in yaw_opts], dtype=float)
    yaw_rates_rad = np.radians(yaw_rates_deg)

    glat_opts = [parse_optional_float(r.get('GForceLat')) for r in records]
    glon_opts = [parse_optional_float(r.get('GForceLon')) for r in records]

    # Compute raw yaw angular acceleration (rad/s^2)
    d_omega_z = np.zeros(N)
    for i in range(1, N):
        if is_valid_yaw[i] and is_valid_yaw[i - 1]:
            d_omega_z[i] = (yaw_rates_rad[i] - yaw_rates_rad[i - 1]) / dts[i]
    if N > 1 and is_valid_yaw[0] and is_valid_yaw[1]:
        d_omega_z[0] = d_omega_z[1]

    # Smooth d_omega_z with a 5-point moving window over valid points to filter vibration
    smoothed_d_omega = np.zeros(N)
    for i in range(N):
        if not is_valid_yaw[i]:
            smoothed_d_omega[i] = np.nan
            continue
        window_vals = [
            d_omega_z[j]
            for j in range(max(0, i - 2), min(N, i + 3))
            if is_valid_yaw[j]
        ]
        smoothed_d_omega[i] = float(np.mean(window_vals)) if window_vals else 0.0

    f_yf = np.full(N, np.nan)
    f_yr = np.full(N, np.nan)
    f_xf = np.full(N, np.nan)
    f_xr = np.full(N, np.nan)

    for i in range(N):
        if not is_valid_yaw[i]:
            continue

        lat_g = glat_opts[i]
        if lat_g is None:
            # Estimate centripetal acceleration a_y = v * omega_z / g if accelerometer missing
            lat_g = (speeds_ms[i] * yaw_rates_rad[i]) / g

        lon_g = glon_opts[i]
        if lon_g is None:
            lon_g = 0.0

        dw = smoothed_d_omega[i]
        moment_term = moment_coeff * dw

        fy_front = W_f * lat_g + moment_term
        fy_rear = W_r * lat_g - moment_term

        # Steering angle: use sensor if available, else kinematic approximation delta ≈ L * omega_z / v
        steer_opt = parse_optional_float(records[i].get('Steering Angle'))
        if steer_opt is not None:
            delta_rad = math.radians(steer_opt)
        else:
            v = speeds_ms[i]
            if v > 2.0:
                delta_rad = (L * yaw_rates_rad[i]) / v
                delta_rad = max(-0.5, min(0.5, delta_rad))  # Clamp to ~±28.6 degrees
            else:
                delta_rad = 0.0

        sin_delta = math.sin(delta_rad)
        fx_front = -fy_front * sin_delta
        fx_rear = lon_g + fy_front * sin_delta

        f_yf[i] = fy_front
        f_yr[i] = fy_rear
        f_xf[i] = fx_front
        f_xr[i] = fx_rear

    return f_yf, f_yr, f_xf, f_xr


def process_telemetry_derivative_data(
    records: list[dict], session_info: CSVSessionMetadata, data_dir: str
) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """Computes derivative data from raw telemetry records."""
    dt = session_info.session_start_datetime

    track_name_raw = session_info.track_name

    # Load track data
    track_data = db.get_track(track_name_raw)
    centerline = None
    if track_data:
        centerline = track_data.get('center_line')
    else:
        logger.warning(f"Track data not found for: '{track_name_raw}'")
        track_data = {}

    # Coordinate conversion
    lat_list = []
    lon_list = []

    origin = None
    league = session_info.league
    if league == 'kartsim' or 'x' in records[0]:
        resolved_track_name = (track_data.get('track_name') if track_data else None) or track_name_raw
        origin = rfactor.get_track_origin(resolved_track_name)

    for r in records:
        if origin is not None:
            x_m = r.get('x')
            z_m = r.get('z')

            if x_m is not None and z_m is not None and x_m != '' and z_m != '':
                p_lat, p_lon = rfactor.rfactor_to_gps(safe_float(x_m), safe_float(z_m), origin)
                lat_list.append(p_lat)
                lon_list.append(p_lon)
                continue

        raw_lat = r.get('Latitude')
        raw_lon = r.get('Longitude')
        lat_val = parse_coordinate(raw_lat)
        lon_val = parse_coordinate(raw_lon)
        if np.isnan(lat_val) or np.isnan(lon_val):
            lat_list.append(np.nan)
            lon_list.append(np.nan)
        else:
            lat_list.append(lat_val)
            lon_list.append(lon_val)

    # Filter out geographic outliers far outside track limits
    ref_lat, ref_lon, max_dist_m = get_track_coordinate_reference(track_data, lat_list, lon_list)
    if ref_lat is not None and ref_lon is not None:
        for i in range(len(lat_list)):
            if not np.isnan(lat_list[i]) and not np.isnan(lon_list[i]):
                if not is_coordinate_within_bounds(lat_list[i], lon_list[i], ref_lat, ref_lon, max_dist_m):
                    lat_list[i] = np.nan
                    lon_list[i] = np.nan

    lat_arr = np.array(lat_list)
    lon_arr = np.array(lon_list)

    # Calculate course
    course = np.zeros(len(lat_arr))
    valid_coords = ~np.isnan(lat_arr) & ~np.isnan(lon_arr)
    last_valid_idx = None
    for i in range(len(lat_arr)):
        if not valid_coords[i]:
            course[i] = course[last_valid_idx] if last_valid_idx is not None else 0.0
            continue
        if last_valid_idx is not None:
            dlat = lat_arr[i] - lat_arr[last_valid_idx]
            dlon = lon_arr[i] - lon_arr[last_valid_idx]
            dlon *= math.cos(math.radians(lat_arr[i]))
            if dlat != 0 or dlon != 0:
                course[i] = math.degrees(math.atan2(dlon, dlat))
            else:
                course[i] = course[last_valid_idx]
        else:
            course[i] = 0.0
        last_valid_idx = i

    if len(course) > 0:
        first_valid_idxs = np.where(valid_coords)[0]
        if len(first_valid_idxs) > 0 and first_valid_idxs[0] > 0:
            course[:first_valid_idxs[0]] = course[first_valid_idxs[0]]

    # Lap distance & lap detection
    lap_col = np.ones(len(records), dtype=int)
    lap_distance_m = np.full(len(records), np.nan)
    turns = []
    lap_length = 1000.0
    sector_end = []

    if track_data and centerline:
        cl_lat = np.array([p['lat'] for p in centerline])
        cl_lon = np.array([p['lon'] for p in centerline])
        cl_dist = np.array([p['dist'] for p in centerline])
        lap_length = float(track_data.get('lap_length', 1000.0))
        turns = track_data.get('turns', [])
        sector_end = track_data.get('sector_end', [])

        lats_v = lat_arr[:, None]
        lons_v = lon_arr[:, None]
        avg_lat = np.mean(cl_lat)
        cos_lat_track = np.cos(np.radians(avg_lat))
        d2_matrix = (cl_lat[None, :] - lats_v)**2 + ((cl_lon[None, :] - lons_v) * cos_lat_track)**2
        idxs_all = np.argmin(d2_matrix, axis=1)
        raw_dist = cl_dist[idxs_all]

        crossings = []
        last_valid_dist = None
        for i in range(len(raw_dist)):
            if not valid_coords[i]:
                continue
            if last_valid_dist is not None:
                if raw_dist[i] < last_valid_dist - (0.5 * lap_length):
                    crossings.append(i)
            last_valid_dist = raw_dist[i]

        for i, crossing in enumerate(crossings):
            lap_col[crossing:] = i + 2

        lap_distance_m = interpolate_lap_distances(
            lat_arr, lon_arr, lap_col, centerline, lap_length, d2_matrix
        )
    else:
        for i in range(len(records)):
            lap_raw = records[i].get('Lap')
            try:
                lap_col[i] = int(float(lap_raw)) if lap_raw else 1
            except (ValueError, TypeError):
                lap_col[i] = 1
            raw_dist = records[i].get('Lap Distance')
            if raw_dist is not None and raw_dist != '':
                lap_distance_m[i] = safe_float(raw_dist, np.nan)
            else:
                lap_distance_m[i] = np.nan

    # Build Laps list
    laps_data = []
    unique_laps = sorted(np.unique(lap_col))
    for l_num in unique_laps:
        mask = (lap_col == l_num)
        lap_idxs = np.where(mask)[0]

        # Calculate lap time directly from the first and last timestamps of the lap
        t_first_val = records[lap_idxs[0]].get('Time')
        t_last_val = records[lap_idxs[-1]].get('Time')
        try:
            lap_time_seconds = (pd.to_datetime(t_last_val) - pd.to_datetime(t_first_val)).total_seconds()
        except Exception:
            lap_time_seconds = 0.0

        laps_data.append({'num': int(l_num), 'time': lap_time_seconds})

    # Quaternions and angles
    q_x = [safe_float(r.get('Ori Quat X'), 0.0) for r in records]
    q_y = [safe_float(r.get('Ori Quat Y'), 0.0) for r in records]
    q_z = [safe_float(r.get('Ori Quat Z'), 0.0) for r in records]
    q_w = [safe_float(r.get('Ori Quat W'), 1.0) for r in records]

    def quat_to_yaw(x: float, y: float, z: float, w: float) -> float:
        siny_cosp = 2 * (w * y + x * z)
        cosy_cosp = 1 - 2 * (y * y + z * z)
        return math.degrees(math.atan2(siny_cosp, cosy_cosp))

    def wrap_360(a: float) -> float:
        return a % 360

    def wrap_180(a: float) -> float:
        return (a + 180) % 360 - 180

    speeds_ms = np.zeros(len(records))
    for i, r in enumerate(records):
        s_val = safe_float(r.get('Speed'), 0.0)
        speeds_ms[i] = s_val / 3.6 if s_val > 50.0 else s_val

    telemetry_rows = []
    has_quat = any('Ori Quat X' in r or 'q_x' in r or 'Ori Quat W' in r for r in records)
    has_toe = any('Toe FL' in r or 'toe_fl' in r for r in records)

    valid_raw_slip_count = sum(
        1 for r in records
        if r.get('Slip Angle Rear') is not None and str(r.get('Slip Angle Rear')).strip() != ''
    )
    has_raw_slip_rear = (valid_raw_slip_count > len(records) * 0.5) if records else False

    has_gyro = any(
        parse_optional_float(r.get('Yaw Rate')) is not None
        for r in records
    )
    can_compute_gyro_slip = (
        not has_quat
        and not has_raw_slip_rear
        and has_gyro
        and session_info.distance_to_rear_axle is not None
    )

    if can_compute_gyro_slip and session_info.distance_to_rear_axle is not None:
        gyro_heading_arr, gyro_slip_rear_arr = compute_gyro_rear_slip_angle(
            records, course, speeds_ms, session_info.distance_to_rear_axle
        )
    else:
        gyro_heading_arr, gyro_slip_rear_arr = np.zeros(len(records)), np.full(len(records), np.nan)

    # Physics-engine per-wheel forces (from KartSim) have non-zero lateral forces.
    # In real kart telemetry (e.g. GoPro), RaceTools CSV headers often contain
    # dummy placeholder columns with 0.00 values.
    has_per_wheel_forces = any(
        abs(safe_float(r.get('Lat Force FL'), 0.0)) > 1e-4
        or abs(safe_float(r.get('Lat Force FR'), 0.0)) > 1e-4
        for r in records
    )
    valid_raw_forces_count = sum(
        1 for r in records
        if r.get('Lat Force Front') is not None and str(r.get('Lat Force Front')).strip() != ''
    )
    has_raw_per_axle_forces = (valid_raw_forces_count > len(records) * 0.5) if records else False
    has_raw_forces = has_per_wheel_forces or has_raw_per_axle_forces

    can_compute_gyro_forces = (
        not has_quat
        and not has_raw_forces
        and has_gyro
    )

    if can_compute_gyro_forces:
        gyro_lat_f_arr, gyro_lat_r_arr, gyro_lon_f_arr, gyro_lon_r_arr = compute_gyro_forces(
            records, speeds_ms, session_info.distance_to_rear_axle
        )
    else:
        gyro_lat_f_arr = np.full(len(records), np.nan)
        gyro_lat_r_arr = np.full(len(records), np.nan)
        gyro_lon_f_arr = np.full(len(records), np.nan)
        gyro_lon_r_arr = np.full(len(records), np.nan)

    sim_channels = [
        'RPS FL', 'RPS FR', 'RPS RL', 'RPS RR',
        'Lat Patch Vel FL', 'Lat Patch Vel FR', 'Lat Patch Vel RL', 'Lat Patch Vel RR',
        'Long Patch Vel FL', 'Long Patch Vel FR', 'Long Patch Vel RL', 'Long Patch Vel RR',
        'Tyre Load FL', 'Tyre Load FR', 'Tyre Load RL', 'Tyre Load RR',
        'Lat Force FL', 'Lat Force FR', 'Lat Force RL', 'Lat Force RR',
        'Long Force FL', 'Long Force FR', 'Long Force RL', 'Long Force RR',
        'Lat Force Front', 'Lat Force Rear', 'Long Force Front', 'Long Force Rear',
        'Slide Pct FL', 'Slide Pct FR', 'Slide Pct RL', 'Slide Pct RR'
    ]

    for i in range(len(records)):
        r = records[i]
        speed_val = safe_float(r.get('Speed'), 0.0)
        if speed_val > 50.0:
            speed_ms = speed_val / 3.6
        else:
            speed_ms = speed_val

        course_val = wrap_360(course[i])
        time_str_point = r.get('Time') or ''

        # Start with all original columns from the input record
        row = dict(r)

        # Core and derived geo/time/speed/lap columns
        row['Record'] = i + 1
        row['Time'] = time_str_point
        row['Latitude'] = '' if np.isnan(lat_arr[i]) else f'{lat_arr[i]:.8f}'
        row['Longitude'] = '' if np.isnan(lon_arr[i]) else f'{lon_arr[i]:.8f}'
        row['Speed'] = f'{speed_ms:.5f}'
        row['Lap'] = str(lap_col[i])
        row['Lap Distance'] = '' if np.isnan(lap_distance_m[i]) else f'{lap_distance_m[i]:.2f}'

        # Format optional base columns ONLY if present in input
        if 'Direction of Travel' in r:
            if not valid_coords[i] and (r.get('Direction of Travel') is None or r.get('Direction of Travel') == ''):
                row['Direction of Travel'] = ''
            else:
                row['Direction of Travel'] = f'{course_val:.2f}'

        if 'Altitude' in r:
            row['Altitude'] = f"{safe_float(r.get('Altitude'), 0.0):.2f}"

        if 'GForceLat' in r:
            row['GForceLat'] = f"{safe_float(r.get('GForceLat'), 0.0):.3f}"

        if 'GForceLon' in r:
            row['GForceLon'] = f"{safe_float(r.get('GForceLon'), 0.0):.3f}"

        if 'GForceVert' in r:
            row['GForceVert'] = f"{safe_float(r.get('GForceVert'), 0.0):.3f}"

        if 'Throttle' in r:
            row['Throttle'] = f"{safe_float(r.get('Throttle'), 0.0):.1f}"

        if 'Brake' in r:
            row['Brake'] = f"{safe_float(r.get('Brake'), 0.0):.1f}"

        if 'Steering Angle' in r:
            row['Steering Angle'] = f"{safe_float(r.get('Steering Angle'), 0.0):.1f}"

        if 'Yaw Rate' in r:
            y_val = parse_optional_float(r.get('Yaw Rate'))
            row['Yaw Rate'] = f"{y_val:.3f}" if y_val is not None else ''

        if 'Pitch Rate' in r:
            p_val = parse_optional_float(r.get('Pitch Rate'))
            row['Pitch Rate'] = f"{p_val:.3f}" if p_val is not None else ''

        if 'Roll Rate' in r:
            r_val = parse_optional_float(r.get('Roll Rate'))
            row['Roll Rate'] = f"{r_val:.3f}" if r_val is not None else ''

        # Orientation / Heading / Slip angles
        if has_quat:
            kart_heading = wrap_360(quat_to_yaw(q_x[i], q_y[i], q_z[i], q_w[i]))
            row['Kart Heading'] = f'{kart_heading:.2f}'
            row['Yaw'] = f'{kart_heading:.2f}'
            row['Ori Quat X'] = f"{q_x[i]:.6f}"
            row['Ori Quat Y'] = f"{q_y[i]:.6f}"
            row['Ori Quat Z'] = f"{q_z[i]:.6f}"
            row['Ori Quat W'] = f"{q_w[i]:.6f}"

            if has_toe:
                toe_fl = safe_float(r.get('Toe FL', r.get('toe_fl')), 0.0)
                toe_fr = safe_float(r.get('Toe FR', r.get('toe_fr')), 0.0)
                wa_fl = math.degrees(toe_fl)
                wa_fr = math.degrees(toe_fr)
                slip_front = wrap_180(course_val - (kart_heading + (wa_fl + wa_fr) / 2) + 180)
                slip_rear = wrap_180(course_val - kart_heading + 180)

                row['Toe FL'] = f"{toe_fl:.6f}"
                row['Toe FR'] = f"{toe_fr:.6f}"
                row['Wheel Angle FL'] = f'{wa_fl:.2f}'
                row['Wheel Angle FR'] = f'{wa_fr:.2f}'
                row['Wheel Heading FL'] = f'{wrap_360(kart_heading + wa_fl):.2f}'
                row['Wheel Heading FR'] = f'{wrap_360(kart_heading + wa_fr):.2f}'
                row['Slip Angle Front'] = f'{slip_front:.2f}'
                row['Slip Angle Rear'] = f'{slip_rear:.2f}'
            else:
                slip_rear = wrap_180(course_val - kart_heading + 180)
                row['Slip Angle Rear'] = f'{slip_rear:.2f}'
        elif can_compute_gyro_slip:
            row['Kart Heading'] = f'{gyro_heading_arr[i]:.2f}'
            row['Yaw'] = f'{gyro_heading_arr[i]:.2f}'
            if not np.isnan(gyro_slip_rear_arr[i]):
                row['Slip Angle Rear'] = f'{gyro_slip_rear_arr[i]:.2f}'
            else:
                row['Slip Angle Rear'] = ''
        else:
            if 'Kart Heading' in r:
                row['Kart Heading'] = f"{safe_float(r.get('Kart Heading'), 0.0):.2f}"
            if 'Yaw' in r:
                row['Yaw'] = f"{safe_float(r.get('Yaw'), 0.0):.2f}"
            if 'Slip Angle Rear' in r and r.get('Slip Angle Rear') not in (None, ''):
                row['Slip Angle Rear'] = f"{safe_float(r.get('Slip Angle Rear'), 0.0):.2f}"

        # Front & Rear specific forces derived from IMU / Gyro
        if can_compute_gyro_forces:
            if not np.isnan(gyro_lat_f_arr[i]):
                row['Lat Force Front'] = f'{gyro_lat_f_arr[i]:.3f}'
                row['Lat Force Rear'] = f'{gyro_lat_r_arr[i]:.3f}'
                row['Long Force Front'] = f'{gyro_lon_f_arr[i]:.3f}'
                row['Long Force Rear'] = f'{gyro_lon_r_arr[i]:.3f}'
            else:
                row['Lat Force Front'] = ''
                row['Lat Force Rear'] = ''
                row['Long Force Front'] = ''
                row['Long Force Rear'] = ''

        # Optional simulation physics channels
        for ch in sim_channels:
            if (
                ch in ('Lat Force Front', 'Lat Force Rear', 'Long Force Front', 'Long Force Rear')
                and can_compute_gyro_forces
            ):
                continue
            if ch in r:
                row[ch] = f"{safe_float(r.get(ch), 0.0):.2f}"

        telemetry_rows.append(row)

    df_telemetry = pd.DataFrame(telemetry_rows)

    best_lap_time = ''
    best_lap_num = ''

    valid_lap_nums = set()
    for lap in laps_data:
        lap_dists = lap_distance_m[lap_col == lap['num']]
        if is_lap_valid(lap_dists, turns, lap_length):
            valid_lap_nums.add(lap['num'])

    valid_laps = [lap for lap in laps_data if lap['time'] > 0 and lap['num'] in valid_lap_nums]
    if valid_laps:
        best_lap_info = min(valid_laps, key=lambda x: x['time'])
        best_lap_time = sanitize.format_time(best_lap_info['time'])
        best_lap_num = str(best_lap_info['num'])

    max_lap_num = int(max(lap['num'] for lap in laps_data)) if laps_data else 0

    headers = ['Pos', 'Positions Gained', 'No', 'Name', 'Laps', 'Time', '', 'Avg Speed', 'Gap', 'Best', 'On']
    for idx in range(1, max_lap_num + 1):
        headers.append(f'Lap {idx}')

    lap_dict = {
        lap['num']: sanitize.format_time(lap['time'])
        for lap in laps_data
        if lap['num'] in valid_lap_nums
    }

    summary_row = [
        '1', '0', str(session_info.kart_number), session_info.driver_name, str(len(valid_laps)),
        '', '', '', '', best_lap_time, best_lap_num
    ]
    for idx in range(1, max_lap_num + 1):
        summary_row.append(lap_dict.get(idx, ''))

    df_summary = pd.DataFrame([summary_row], columns=headers)

    # Derive track conditions from telemetry data when not provided in metadata.
    track_conditions = session_info.track_conditions
    weather = session_info.weather
    if not track_conditions:
        inferred = infer_conditions_from_avg_track_wetness(records)
        if inferred is not None:
            track_conditions = inferred
            if not weather:
                weather = inferred

    canonical_track_name = (
        (track_data.get('track_name') if track_data else None)
        or sanitize.humanize_track_name(track_name_raw)
    )

    metadata_json = {
        'alphatiming_url': '',
        'session_start_datetime': dt.strftime('%Y-%m-%d %H:%M'),
        'track_name': canonical_track_name,
        'meeting_name': canonical_track_name,
        'league': session_info.league,
        'class_name': session_info.class_name,
        'session_name': session_info.session_name,
        'short_name': session_info.session_name,
        'track_conditions': track_conditions,
        'temperature': session_info.temperature,
        'weather': weather,
        'track_temperature': '',
        'sector_end': sector_end,
    }

    if session_info.distance_to_rear_axle is not None:
        metadata_json['distance_to_rear_axle'] = session_info.distance_to_rear_axle

    return df_telemetry, metadata_json, df_summary


@upload_blueprint.route('/api/upload/stream', methods=['POST'])
def stream_upload() -> Any:
    """Stream telemetry CSV directly into the handler.
    Consumes the stream and populates the database and files continuously.
    """
    acl = auth.get_current_acl()
    if not acl.get('upload_sessions'):
        raise Forbidden('Forbidden: No session upload permission.')

    total_bytes_read = 0

    def read_line_with_limit() -> str | None:
        nonlocal total_bytes_read
        line = request.stream.readline()
        if not line:
            return None
        total_bytes_read += len(line)
        if total_bytes_read > MAX_METADATA_SIZE:
            raise BadRequest(f'File exceeds maximum size limit of {MAX_METADATA_SIZE} bytes.')
        return line.decode('utf-8')

    # 1. Read first real lines until an empty line (metadata block/header)
    header_lines: list[str] = []
    while line := read_line_with_limit():
        if not line.strip():
            break
        header_lines.append(line)

    # Parse the header metadata
    metadata = parse_csv_metadata_rows(csv.reader(header_lines))

    # ACL validation
    if not auth.can_upload_session_for_league(acl, metadata.league):
        raise Forbidden(f'Forbidden: Upload for league {metadata.league!r} is not permitted.')
    if not auth.can_upload_session_for_driver(acl, metadata.driver_name):
        raise Forbidden(f'Forbidden: Upload for driver {metadata.driver_name!r} not permitted.')

    # Resolve any car/class name aliases
    metadata.class_name = aliases.resolve_car_name(metadata.class_name)
    track_data = db.get_track(metadata.track_name)
    if track_data and track_data.get('track_name'):
        metadata.track_name = track_data['track_name']

    # Prepare directories and file paths
    time_str = metadata.session_start_datetime.strftime('%H_%M')
    start_date = metadata.session_start_datetime.strftime('%Y_%m_%d')
    track_sanitized = sanitize.sanitize_filename(metadata.track_name)
    meeting_folder = f'{start_date}_{track_sanitized}'
    meeting_dir = os.path.join(DATA_DIR, metadata.league, metadata.class_name, meeting_folder)
    os.makedirs(os.path.join(meeting_dir, 'telemetry'), exist_ok=True)

    session_filename = f'{time_str}_{sanitize.sanitize_filename(metadata.session_name)}'

    meta_path = os.path.join(meeting_dir, f'{session_filename}_metadata.json')
    summary_path = os.path.join(meeting_dir, f'{session_filename}.csv')
    telemetry_path = os.path.join(
        meeting_dir, 'telemetry', f'{session_filename}_{sanitize.sanitize_filename(metadata.driver_name)}.csv'
    )

    # Check if session already exists and has AlphaTiming data
    has_alphatiming = False
    if metadata.league != 'kartsim':
        if os.path.exists(meta_path):
            with open(meta_path, 'r', encoding='utf-8') as f:
                existing_meta = json.load(f)
            if existing_meta.get('alphatiming_url', ''):
                has_alphatiming = True

    # If session does not have AlphaTiming data, check permissions for existing drivers before overwriting
    if not has_alphatiming:
        df = db.load(
            leagues=metadata.league,
            classes=metadata.class_name,
            date=metadata.session_start_datetime.strftime('%Y-%m-%d'),
            track=metadata.track_name
        )
        if not df.empty:
            session_df = df[df['SessionID'] == session_filename]
            if not session_df.empty:
                existing_drivers = session_df['Name'].unique().tolist()
                for driver in existing_drivers:
                    if not auth.can_upload_session_for_driver(acl, driver):
                        raise Forbidden(
                            'Forbidden: Cannot overwrite session. You do not '
                            f'have permission to upload for driver {driver!r} '
                            'in the existing session.'
                        )

    # 2. Read the CSV header line and remember it
    csv_header_line_str = read_line_with_limit()
    if not csv_header_line_str:
        raise BadRequest('No telemetry records found in the streamed CSV.')

    # 3. Read lines one by one, process them, write processed results to a buffer,
    # and after each line check if 15 seconds have passed (or the file has been closed)
    # and write data to the filesystem and to the DB.
    existing_lines = load_existing_telemetry_lines(telemetry_path)
    if metadata.distance_to_rear_axle is None and existing_lines:
        for ex_line in existing_lines:
            if not ex_line.strip():
                break
            parts = [p.strip() for p in ex_line.split(',', 1)]
            if len(parts) == 2 and parts[0].lower() == 'distance to rear axle':
                try:
                    metadata.distance_to_rear_axle = float(parts[1])
                except (ValueError, TypeError):
                    pass
                break
    pruned_and_merged = False

    written_rows_count = 0
    last_write_time = time.time()
    db_session_id = None
    # Accumulate ALL raw records across the entire stream so that lap detection
    # and derivative processing always sees the full session data on each flush.
    all_raw_records: list[dict] = []

    with open(telemetry_path, 'w', newline='', encoding='utf-8') as telemetry_file:
        writer = csv.writer(telemetry_file)
        writer.writerow(['Format', 'RaceTools CSV'])
        writer.writerow(['Track name', metadata.track_name])
        writer.writerow(['Date', metadata.session_start_datetime.strftime('%Y-%m-%d')])
        writer.writerow(['Time', metadata.session_start_datetime.strftime('%H:%M:%S')])
        writer.writerow(['Driver name', metadata.driver_name])
        writer.writerow(['League', metadata.league])
        writer.writerow(['Class', metadata.class_name])
        writer.writerow(['Session', metadata.session_name])
        if metadata.distance_to_rear_axle is not None:
            writer.writerow(['Distance to rear axle', metadata.distance_to_rear_axle])
        writer.writerow([])  # Mandatory separator

        buffered_lines: list[str] = []
        while True:
            line_str = read_line_with_limit()
            if line_str is not None:
                buffered_lines.append(line_str)

            now = time.time()
            should_write = (now - last_write_time >= STREAM_FLUSH_INTERVAL_SEC) or (line_str is None)

            if should_write and buffered_lines:
                # Parse newly buffered lines
                reader = csv.DictReader([csv_header_line_str] + buffered_lines)
                new_records = []
                for row_dict in reader:
                    record = {
                        k: v for k, v in row_dict.items()
                        if k is not None and v is not None
                    }
                    new_records.append(record)
                buffered_lines.clear()

                if new_records:
                    if not pruned_and_merged:
                        # Find the minimal timestamp in the newly uploaded records
                        new_timestamps = []
                        for r in new_records:
                            t_val = r.get('Time')
                            if t_val:
                                dt = to_naive_datetime(t_val)
                                if dt is not None:
                                    new_timestamps.append(dt)

                        if new_timestamps:
                            min_new_timestamp = min(new_timestamps)

                            # Find the data separator blank line
                            blank_idx = -1
                            for idx, line in enumerate(existing_lines):
                                if not line.strip():
                                    blank_idx = idx
                                    break

                            kept_pre_existing_records = []
                            if blank_idx != -1 and blank_idx + 1 < len(existing_lines):
                                # The line after the blank line is the header line
                                existing_header_line = existing_lines[blank_idx + 1]
                                existing_header_cols = [c.strip() for c in next(csv.reader([existing_header_line]))]

                                if 'Time' in existing_header_cols:
                                    existing_time_idx = existing_header_cols.index('Time')

                                    # Extract data lines
                                    data_lines = []
                                    for line in existing_lines[blank_idx + 2:]:
                                        if not line.strip():
                                            continue
                                        try:
                                            row_cols = next(csv.reader([line]))
                                            if len(row_cols) > existing_time_idx:
                                                t_val = row_cols[existing_time_idx].strip()
                                                dt = to_naive_datetime(t_val)
                                                if dt is not None and dt < min_new_timestamp:
                                                    data_lines.append(line)
                                        except Exception:
                                            pass

                                    # Dynamically parse the kept data lines using the existing file's header
                                    if data_lines:
                                        reader_existing = csv.DictReader([existing_header_line] + data_lines)
                                        derived_cols_to_strip = {
                                            'Lap', 'Lap Distance', 'Kart Heading', 'Yaw',
                                            'Slip Angle Rear', 'Slip Angle Front',
                                            'Lat Force Front', 'Lat Force Rear',
                                            'Long Force Front', 'Long Force Rear'
                                        }
                                        kept_pre_existing_records = [
                                            {k: v for k, v in row.items() if k not in derived_cols_to_strip}
                                            for row in reader_existing
                                        ]

                            all_raw_records = kept_pre_existing_records + new_records
                        else:
                            all_raw_records = new_records

                        pruned_and_merged = True
                    else:
                        all_raw_records.extend(new_records)

                if all_raw_records:
                    # Process telemetry derivative data
                    df_telemetry, metadata_json, df_summary = process_telemetry_derivative_data(
                        all_raw_records, metadata, DATA_DIR
                    )

                    # Append new processed rows
                    new_df = df_telemetry.iloc[written_rows_count:]
                    if not new_df.empty:
                        # Write with header if it's the first chunk. The header will be based on standard column names.
                        new_df.to_csv(telemetry_file, index=False, header=(written_rows_count == 0))
                        telemetry_file.flush()
                        written_rows_count = len(df_telemetry)

                    if not has_alphatiming:
                        # Write metadata.json
                        with open(meta_path, 'w', encoding='utf-8') as f:
                            json.dump(metadata_json, f, indent=4)

                        # Write summary csv
                        df_summary.to_csv(summary_path, index=False)

                        # Populate DB
                        db_session_id = populate_db.populate_single_session(meta_path, db.conn, DATA_DIR)
                    else:
                        meeting_folder = os.path.basename(meeting_dir)
                        db_session_id = f'{meeting_folder}_{session_filename}'

                    invalidate_plot_cache([db_session_id])

                    last_write_time = now

            if line_str is None:
                break

    if not db_session_id:
        raise BadRequest('No telemetry records found in the streamed CSV.')

    return jsonify({
        'message': 'Session completed and imported successfully.',
        'session_id': db_session_id
    }), 200


@upload_blueprint.route('/api/upload/files', methods=['POST'])
def files_upload() -> Any:
    """Direct multipart file upload endpoint (metadata.json, results.csv, optional penalties.csv)."""
    acl = auth.get_current_acl()
    if not acl.get('upload_sessions'):
        raise Forbidden('Forbidden: No session upload permission.')

    metadata_file = request.files.get('metadata')
    results_file = request.files.get('results')
    penalties_file = request.files.get('penalties')

    if not metadata_file or not results_file:
        raise BadRequest('Missing metadata or results files.')

    # Load and parse metadata
    metadata_bytes = metadata_file.read()
    meta = json.loads(metadata_bytes.decode('utf-8'))
    league = meta.get('league')
    class_name = meta.get('class_name')
    track_name = meta.get('track_name')
    session_name = meta.get('session_name')
    session_start_datetime = meta.get('session_start_datetime')

    if not league or not class_name or not track_name or not session_name or not session_start_datetime:
        raise BadRequest(
            'Metadata missing required fields (league, class_name, '
            'track_name, session_name, session_start_datetime).'
        )

    # Resolve any car/class name aliases and normalize format
    if isinstance(class_name, list):
        class_name = [aliases.resolve_car_name(c) for c in class_name if c]
        primary_class = class_name[0] if class_name else 'unknown'
    else:
        class_name = aliases.resolve_car_name(str(class_name))
        primary_class = class_name
    meta['class_name'] = class_name

    # Resolve any track name aliases
    track_data = db.get_track(track_name)
    if track_data and track_data.get('track_name'):
        track_name = track_data['track_name']
    meta['track_name'] = track_name

    # ACL Check for league
    if not auth.can_upload_session_for_league(acl, league):
        raise Forbidden(f'Forbidden: Upload for league {league!r} is not permitted.')

    # ACL check for driver permissions
    # Parse CSV to identify driver names in results
    results_bytes = results_file.read()
    reader = csv.DictReader(results_bytes.decode('utf-8').splitlines())
    drivers = [row['Name'] for row in reader if row.get('Name')]

    for driver in drivers:
        if not auth.can_upload_session_for_driver(acl, driver):
            raise Forbidden(f'Forbidden: Upload for driver {driver!r} is not permitted.')

    # Form paths
    try:
        dt = datetime.datetime.strptime(session_start_datetime, '%Y-%m-%d %H:%M')
    except Exception:
        raise BadRequest('Invalid session_start_datetime format. Must be YYYY-MM-DD HH:MM.')

    date_str = dt.strftime('%Y_%m_%d')
    time_str = dt.strftime('%H_%M')
    meeting_folder = f'{date_str}_{sanitize.sanitize_filename(track_name)}'
    meeting_dir = os.path.join(DATA_DIR, league, primary_class, meeting_folder)
    os.makedirs(meeting_dir, exist_ok=True)

    session_filename = f'{time_str}_{sanitize.sanitize_filename(session_name)}'

    meta_path = os.path.join(meeting_dir, f'{session_filename}_metadata.json')
    results_path = os.path.join(meeting_dir, f'{session_filename}.csv')
    penalties_path = os.path.join(meeting_dir, f'{session_filename}_penalties.csv')

    # Check if session already exists and check permissions for its drivers
    df = db.load(
        leagues=league,
        classes=class_name,
        date=dt.strftime('%Y-%m-%d'),
        track=track_name
    )
    if not df.empty:
        session_df = df[df['SessionID'] == session_filename]
        if not session_df.empty:
            existing_drivers = session_df['Name'].unique().tolist()
            for driver in existing_drivers:
                if not auth.can_upload_session_for_driver(acl, driver):
                    raise Forbidden(
                        'Forbidden: Cannot overwrite session. You do not '
                        f'have permission to upload for driver {driver!r} '
                        'in the existing session.'
                    )

    # Save files (Files are primary!)
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=4)

    with open(results_path, 'wb') as f:
        f.write(results_bytes)

    if penalties_file:
        with open(penalties_path, 'wb') as f:
            f.write(penalties_file.read())

    # Update database using populate_db code
    db_session_id = populate_db.populate_single_session(meta_path, db.conn, DATA_DIR)
    invalidate_plot_cache([db_session_id])

    return jsonify({
        'message': 'Files uploaded and session imported successfully.',
        'session_id': db_session_id
    }), 200
