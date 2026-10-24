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
import zlib
import brotli  # type: ignore[import-untyped]
from typing import Any, Iterable
from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd
from flask import Blueprint, request, jsonify
from werkzeug.exceptions import BadRequest, Forbidden, HTTPException, NotFound
from database import db, config
from utils import populate_db
from race_tools import assetto_corsa, rfactor, sanitize, track_data
import auth
import aliases
import session_time
from plot_handlers import invalidate_plot_cache
import _telemetry_native

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
    has_seconds: bool = False
    kart_number: str | None = None
    track_conditions: str | None = None
    temperature: str | None = None
    weather: str | None = None
    distance_to_rear_axle: float | None = None
    track_configuration: str | None = None
    origin: track_data.TrackOrigin | None = None


def create_track_origin(
    league: str,
    track_name: str,
    lat: float | None,
    lon: float | None,
    heading: float = 0.0,
) -> track_data.TrackOrigin | None:
    """Create a simulator-specific TrackOrigin instance if coordinates are provided."""
    if lat is None or lon is None:
        return None
    if league == 'acevo':
        try:
            corr = assetto_corsa.get_track_origin(track_name).correction
        except ValueError as e:
            raise NotFound(str(e)) from e
        return assetto_corsa.AssettoCorsaTrackOrigin(
            lat=lat, lon=lon, correction=corr, heading=heading
        )
    return rfactor.RFactorTrackOrigin(
        lat=lat, lon=lon, heading=heading
    )


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
    track_configuration = None
    origin_lat = None
    origin_lon = None
    origin_heading = 0.0

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
        elif key == 'track configuration':
            track_configuration = val
        elif key == 'track origin latitude':
            origin_lat = float(val)
        elif key == 'track origin longitude':
            origin_lon = float(val)
        elif key == 'track origin heading':
            origin_heading = float(val)

    has_seconds = False
    if date_str and time_str:
        date_clean = date_str.strip()
        time_clean = time_str.strip()
        if 'T' in date_clean or (len(date_clean) > 10 and ' ' in date_clean):
            session_start_datetime_str = date_clean
        else:
            session_start_datetime_str = f'{date_clean} {time_clean}'
        try:
            session_start_datetime, has_seconds = session_time.parse_session_start_datetime(
                session_start_datetime_str, track_name=track_name, db=db
            )
        except Exception:
            raise BadRequest(session_time.INVALID_FORMAT_MESSAGE)

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

    origin = create_track_origin(
        league=league,
        track_name=track_name,
        lat=origin_lat,
        lon=origin_lon,
        heading=origin_heading,
    )

    return CSVSessionMetadata(
        driver_name=driver_name,
        track_name=track_name,
        league=league,
        class_name=class_name,
        session_name=session_name,
        session_start_datetime=session_start_datetime,
        has_seconds=has_seconds,
        kart_number=kart_number,
        track_conditions=track_conditions,
        temperature=temperature,
        weather=weather,
        distance_to_rear_axle=distance_to_rear_axle,
        track_configuration=track_configuration,
        origin=origin,
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
    2. The lap's telemetry points span across the start/finish line (within a 20m buffer of
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
    sf_buffer = 20.0
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
    d2_matrix: np.ndarray | None = None
) -> np.ndarray:
    """Interpolates lap distance along the track by projecting coordinates onto track segments."""
    cl_lat = np.array([p['lat'] for p in centerline], dtype=float)
    cl_lon = np.array([p['lon'] for p in centerline], dtype=float)
    cl_dist = np.array([p['dist'] for p in centerline], dtype=float)
    return _telemetry_native.interpolate_lap_distances(
        lat_arr, lon_arr, lap_col.astype(np.int32), cl_lat, cl_lon, cl_dist, float(lap_length)
    )


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
    track_info: dict | None,
    lats: list[float] | np.ndarray,
    lons: list[float] | np.ndarray
) -> tuple[float | None, float | None, float]:
    """Get reference (latitude, longitude, max_distance_meters) for geographic sanity checks.

    Uses track centerline or origin if available, otherwise computes median coordinates
    of valid data points.
    """
    if track_info:
        centerline = track_info.get('center_line')
        if centerline and isinstance(centerline, list):
            cl_lat = [p['lat'] for p in centerline if isinstance(p, dict) and 'lat' in p]
            cl_lon = [p['lon'] for p in centerline if isinstance(p, dict) and 'lon' in p]
            if cl_lat and cl_lon:
                return float(np.mean(cl_lat)), float(np.mean(cl_lon)), 5000.0
        origin = track_info.get('origin')
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
    track_info = db.get_track(track_name_raw)
    centerline = None
    if track_info:
        centerline = track_info.get('center_line')
    else:
        logger.warning(f"Track data not found for: '{track_name_raw}'")
        track_info = {}

    # Coordinate conversion
    lat_list = []
    lon_list = []

    origin: track_data.TrackOrigin | None = session_info.origin
    if origin is None:
        resolved_track_name = (track_info.get('track_name') if track_info else None) or track_name_raw
        try:
            if session_info.league == 'kartsim':
                origin = rfactor.get_track_origin(resolved_track_name)
            elif session_info.league == 'acevo':
                origin = assetto_corsa.get_track_origin(resolved_track_name)
        except ValueError as e:
            raise NotFound(str(e)) from e

    for r in records:
        if origin is not None:
            x_m = r.get('x')
            z_m = r.get('z')

            if x_m is not None and z_m is not None and x_m != '' and z_m != '':
                p_lat, p_lon = origin.to_gps(safe_float(x_m), safe_float(z_m))
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
    ref_lat, ref_lon, max_dist_m = get_track_coordinate_reference(track_info, lat_list, lon_list)
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

    if track_info and centerline:
        cl_lat = np.array([p['lat'] for p in centerline], dtype=float)
        cl_lon = np.array([p['lon'] for p in centerline], dtype=float)
        cl_dist = np.array([p['dist'] for p in centerline], dtype=float)
        lap_length = float(track_info.get('lap_length', 1000.0))
        turns = track_info.get('turns', [])
        sector_end = track_info.get('sector_end', [])

        lap_col = _telemetry_native.detect_lap_crossings(
            lat_arr, lon_arr, cl_lat, cl_lon, cl_dist, lap_length
        )
        lap_distance_m = _telemetry_native.interpolate_lap_distances(
            lat_arr, lon_arr, lap_col, cl_lat, cl_lon, cl_dist, lap_length
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

    # Initialize DataFrame from records in one batch
    df_telemetry = pd.DataFrame(records)
    n_records = len(records)

    speeds_raw = np.array([safe_float(r.get('Speed'), 0.0) for r in records], dtype=float)
    speeds_ms = np.where(speeds_raw > 50.0, speeds_raw / 3.6, speeds_raw)

    has_quat = any(k in df_telemetry.columns for k in ('Ori Quat X', 'q_x', 'Ori Quat W'))
    has_toe = any(k in df_telemetry.columns for k in ('Toe FL', 'toe_fl'))

    if has_quat:
        quat_x_col = df_telemetry['Ori Quat X'] if 'Ori Quat X' in df_telemetry.columns else df_telemetry.get('q_x', [])
        quat_y_col = df_telemetry['Ori Quat Y'] if 'Ori Quat Y' in df_telemetry.columns else df_telemetry.get('q_y', [])
        quat_z_col = df_telemetry['Ori Quat Z'] if 'Ori Quat Z' in df_telemetry.columns else df_telemetry.get('q_z', [])
        quat_w_col = df_telemetry['Ori Quat W'] if 'Ori Quat W' in df_telemetry.columns else df_telemetry.get('q_w', [])
        q_x = np.array([safe_float(v, 0.0) for v in quat_x_col], dtype=float)
        q_y = np.array([safe_float(v, 0.0) for v in quat_y_col], dtype=float)
        q_z = np.array([safe_float(v, 0.0) for v in quat_z_col], dtype=float)
        q_w = np.array([safe_float(v, 1.0) for v in quat_w_col], dtype=float)

    if 'Slip Angle Rear' in df_telemetry.columns:
        valid_raw_slip_count = sum(
            1 for v in df_telemetry['Slip Angle Rear']
            if v is not None and str(v).strip() != ''
        )
        has_raw_slip_rear = (valid_raw_slip_count > n_records * 0.5)
    else:
        has_raw_slip_rear = False

    if 'Yaw Rate' in df_telemetry.columns:
        has_gyro = any(
            parse_optional_float(v) is not None
            for v in df_telemetry['Yaw Rate']
        )
    else:
        has_gyro = False

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
        gyro_heading_arr, gyro_slip_rear_arr = np.zeros(n_records), np.full(n_records, np.nan)

    # Physics-engine per-wheel forces (from KartSim) have non-zero lateral forces.
    # In real kart telemetry (e.g. GoPro), RaceTools CSV headers often contain
    # dummy placeholder columns with 0.00 values.
    if 'Lat Force FL' in df_telemetry.columns or 'Lat Force FR' in df_telemetry.columns:
        fl_col = df_telemetry['Lat Force FL'] if 'Lat Force FL' in df_telemetry.columns else []
        fr_col = df_telemetry['Lat Force FR'] if 'Lat Force FR' in df_telemetry.columns else []
        has_per_wheel_forces = any(
            abs(safe_float(v, 0.0)) > 1e-4 for v in fl_col
        ) or any(
            abs(safe_float(v, 0.0)) > 1e-4 for v in fr_col
        )
    else:
        has_per_wheel_forces = False

    if 'Lat Force Front' in df_telemetry.columns:
        valid_raw_forces_count = sum(
            1 for v in df_telemetry['Lat Force Front']
            if v is not None and str(v).strip() != ''
        )
        has_raw_per_axle_forces = (valid_raw_forces_count > n_records * 0.5)
    else:
        has_raw_per_axle_forces = False
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
        gyro_lat_f_arr = np.full(n_records, np.nan)
        gyro_lat_r_arr = np.full(n_records, np.nan)
        gyro_lon_f_arr = np.full(n_records, np.nan)
        gyro_lon_r_arr = np.full(n_records, np.nan)

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

    # Core and derived geo/time/speed/lap columns
    df_telemetry['Record'] = np.arange(1, n_records + 1)
    df_telemetry['Time'] = [r.get('Time') or '' for r in records]
    df_telemetry['Latitude'] = ['' if np.isnan(x) else f'{x:.8f}' for x in lat_arr]
    df_telemetry['Longitude'] = ['' if np.isnan(x) else f'{x:.8f}' for x in lon_arr]
    df_telemetry['Speed'] = [f'{s:.5f}' for s in speeds_ms]
    df_telemetry['Lap'] = [str(lap_val) for lap_val in lap_col]
    df_telemetry['Lap Distance'] = ['' if np.isnan(d) else f'{d:.2f}' for d in lap_distance_m]

    # Format optional base columns ONLY if present in input
    if 'Direction of Travel' in df_telemetry.columns:
        raw_dot = df_telemetry['Direction of Travel'].values
        course_mod = course % 360.0
        dot_col = []
        for i in range(n_records):
            raw_val = raw_dot[i]
            is_empty = (
                raw_val is None
                or raw_val == ''
                or (isinstance(raw_val, float) and np.isnan(raw_val))
            )
            if not valid_coords[i] and is_empty:
                dot_col.append('')
            else:
                dot_col.append(f'{course_mod[i]:.2f}')
        df_telemetry['Direction of Travel'] = dot_col

    if 'Altitude' in df_telemetry.columns:
        df_telemetry['Altitude'] = [f'{safe_float(v, 0.0):.2f}' for v in df_telemetry['Altitude']]

    if 'GForceLat' in df_telemetry.columns:
        df_telemetry['GForceLat'] = [f'{safe_float(v, 0.0):.3f}' for v in df_telemetry['GForceLat']]

    if 'GForceLon' in df_telemetry.columns:
        df_telemetry['GForceLon'] = [f'{safe_float(v, 0.0):.3f}' for v in df_telemetry['GForceLon']]

    if 'GForceVert' in df_telemetry.columns:
        df_telemetry['GForceVert'] = [f'{safe_float(v, 0.0):.3f}' for v in df_telemetry['GForceVert']]

    if 'Throttle' in df_telemetry.columns:
        df_telemetry['Throttle'] = [f'{safe_float(v, 0.0):.1f}' for v in df_telemetry['Throttle']]

    if 'Brake' in df_telemetry.columns:
        df_telemetry['Brake'] = [f'{safe_float(v, 0.0):.1f}' for v in df_telemetry['Brake']]

    if 'Steering Angle' in df_telemetry.columns:
        df_telemetry['Steering Angle'] = [f'{safe_float(v, 0.0):.1f}' for v in df_telemetry['Steering Angle']]

    if 'Yaw Rate' in df_telemetry.columns:
        df_telemetry['Yaw Rate'] = [
            f'{y:.3f}' if (y := parse_optional_float(v)) is not None else ''
            for v in df_telemetry['Yaw Rate']
        ]

    if 'Pitch Rate' in df_telemetry.columns:
        df_telemetry['Pitch Rate'] = [
            f'{p:.3f}' if (p := parse_optional_float(v)) is not None else ''
            for v in df_telemetry['Pitch Rate']
        ]

    if 'Roll Rate' in df_telemetry.columns:
        df_telemetry['Roll Rate'] = [
            f'{r_val:.3f}' if (r_val := parse_optional_float(v)) is not None else ''
            for v in df_telemetry['Roll Rate']
        ]

    # Orientation / Heading / Slip angles
    if has_quat:
        siny_cosp = 2.0 * (q_w * q_y + q_x * q_z)
        cosy_cosp = 1.0 - 2.0 * (q_y * q_y + q_z * q_z)
        kart_heading = np.degrees(np.arctan2(siny_cosp, cosy_cosp)) % 360.0
        course_val = course % 360.0

        df_telemetry['Kart Heading'] = [f'{x:.2f}' for x in kart_heading]
        df_telemetry['Yaw'] = df_telemetry['Kart Heading']
        df_telemetry['Ori Quat X'] = [f'{x:.6f}' for x in q_x]
        df_telemetry['Ori Quat Y'] = [f'{x:.6f}' for x in q_y]
        df_telemetry['Ori Quat Z'] = [f'{x:.6f}' for x in q_z]
        df_telemetry['Ori Quat W'] = [f'{x:.6f}' for x in q_w]

        if has_toe:
            toe_fl_col = df_telemetry['Toe FL'] if 'Toe FL' in df_telemetry.columns else df_telemetry.get('toe_fl', [])
            toe_fr_col = df_telemetry['Toe FR'] if 'Toe FR' in df_telemetry.columns else df_telemetry.get('toe_fr', [])
            toe_fl_arr = np.array([safe_float(v, 0.0) for v in toe_fl_col])
            toe_fr_arr = np.array([safe_float(v, 0.0) for v in toe_fr_col])
            wa_fl_arr = np.degrees(toe_fl_arr)
            wa_fr_arr = np.degrees(toe_fr_arr)
            slip_front = (course_val - (kart_heading + (wa_fl_arr + wa_fr_arr) / 2.0) + 360.0) % 360.0 - 180.0
            slip_rear = (course_val - kart_heading + 360.0) % 360.0 - 180.0

            df_telemetry['Toe FL'] = [f'{x:.6f}' for x in toe_fl_arr]
            df_telemetry['Toe FR'] = [f'{x:.6f}' for x in toe_fr_arr]
            df_telemetry['Wheel Angle FL'] = [f'{x:.2f}' for x in wa_fl_arr]
            df_telemetry['Wheel Angle FR'] = [f'{x:.2f}' for x in wa_fr_arr]
            df_telemetry['Wheel Heading FL'] = [f'{(h + w) % 360.0:.2f}' for h, w in zip(kart_heading, wa_fl_arr)]
            df_telemetry['Wheel Heading FR'] = [f'{(h + w) % 360.0:.2f}' for h, w in zip(kart_heading, wa_fr_arr)]
            df_telemetry['Slip Angle Front'] = [f'{x:.2f}' for x in slip_front]
            df_telemetry['Slip Angle Rear'] = [f'{x:.2f}' for x in slip_rear]
        else:
            slip_rear = (course_val - kart_heading + 360.0) % 360.0 - 180.0
            df_telemetry['Slip Angle Rear'] = [f'{x:.2f}' for x in slip_rear]
    elif can_compute_gyro_slip:
        df_telemetry['Kart Heading'] = [f'{x:.2f}' for x in gyro_heading_arr]
        df_telemetry['Yaw'] = df_telemetry['Kart Heading']
        df_telemetry['Slip Angle Rear'] = [
            '' if np.isnan(x) else f'{x:.2f}' for x in gyro_slip_rear_arr
        ]
    else:
        if 'Kart Heading' in df_telemetry.columns:
            df_telemetry['Kart Heading'] = [f'{safe_float(v, 0.0):.2f}' for v in df_telemetry['Kart Heading']]
        if 'Yaw' in df_telemetry.columns:
            df_telemetry['Yaw'] = [f'{safe_float(v, 0.0):.2f}' for v in df_telemetry['Yaw']]
        if 'Slip Angle Rear' in df_telemetry.columns:
            df_telemetry['Slip Angle Rear'] = [
                (
                    f'{safe_float(v, 0.0):.2f}'
                    if v is not None and v != '' and not (isinstance(v, float) and np.isnan(v))
                    else ''
                )
                for v in df_telemetry['Slip Angle Rear']
            ]

    # Front & Rear specific forces derived from IMU / Gyro
    if can_compute_gyro_forces:
        df_telemetry['Lat Force Front'] = ['' if np.isnan(x) else f'{x:.3f}' for x in gyro_lat_f_arr]
        df_telemetry['Lat Force Rear'] = ['' if np.isnan(x) else f'{x:.3f}' for x in gyro_lat_r_arr]
        df_telemetry['Long Force Front'] = ['' if np.isnan(x) else f'{x:.3f}' for x in gyro_lon_f_arr]
        df_telemetry['Long Force Rear'] = ['' if np.isnan(x) else f'{x:.3f}' for x in gyro_lon_r_arr]

    # Optional simulation physics channels
    for ch in sim_channels:
        if (
            ch in ('Lat Force Front', 'Lat Force Rear', 'Long Force Front', 'Long Force Rear')
            and can_compute_gyro_forces
        ):
            continue
        if ch in df_telemetry.columns:
            df_telemetry[ch] = [f'{safe_float(v, 0.0):.2f}' for v in df_telemetry[ch]]

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
        (track_info.get('track_name') if track_info else None)
        or sanitize.humanize_track_name(track_name_raw)
    )

    metadata_json = {
        'alphatiming_url': '',
        'session_start_datetime': (
            dt.strftime('%Y-%m-%d %H:%M:%S')
            if (getattr(session_info, 'has_seconds', False) or dt.second)
            else dt.strftime('%Y-%m-%d %H:%M')
        ),
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
    if session_info.track_configuration is not None:
        metadata_json['track_configuration'] = session_info.track_configuration
    if session_info.origin is not None:
        metadata_json['track_origin_lat'] = session_info.origin.lat
        metadata_json['track_origin_lon'] = session_info.origin.lon
        metadata_json['track_origin_heading'] = session_info.origin.heading

    return df_telemetry, metadata_json, df_summary


class StreamingLineReader:
    """Reads lines incrementally from a streaming request body with optional compression.

    Supports uncompressed (identity), gzip, and brotli (br) Content-Encoding.
    Enforces maximum size limits on both raw incoming data and decompressed data.
    """

    CHUNK_SIZE = 64 * 1024

    def __init__(self, stream: Any, content_encoding: str | None = None) -> None:
        self.stream = stream
        raw_encoding = (content_encoding or '').strip().lower()
        if raw_encoding in ('', 'identity'):
            self.encoding = None
            self.decompressor: Any = None
        elif raw_encoding in ('gzip', 'x-gzip'):
            self.encoding = 'gzip'
            self.decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
        elif raw_encoding in ('deflate', 'zlib'):
            self.encoding = 'deflate'
            self.decompressor = zlib.decompressobj(32 + zlib.MAX_WBITS)
        elif raw_encoding in ('br', 'brotli'):
            self.encoding = 'br'
            self.decompressor = brotli.Decompressor()
        else:
            raise BadRequest(f'Unsupported Content-Encoding: {content_encoding}')

        self.raw_bytes_read = 0
        self.decompressed_bytes_read = 0
        self.buffer = bytearray()
        self.pos = 0
        self.eof = False

    def readline(self) -> str | None:
        while True:
            idx = self.buffer.find(b'\n', self.pos)
            if idx != -1:
                line_bytes = self.buffer[self.pos:idx + 1]
                self.pos = idx + 1
                try:
                    return line_bytes.decode('utf-8')
                except UnicodeDecodeError as e:
                    raise BadRequest(f'Invalid UTF-8 encoding in CSV data: {e}')

            if self.eof:
                if self.pos < len(self.buffer):
                    line_bytes = self.buffer[self.pos:]
                    self.buffer.clear()
                    self.pos = 0
                    try:
                        return line_bytes.decode('utf-8')
                    except UnicodeDecodeError as e:
                        raise BadRequest(f'Invalid UTF-8 encoding in CSV data: {e}')
                return None

            if self.pos > 0:
                if self.pos >= len(self.buffer):
                    self.buffer.clear()
                else:
                    del self.buffer[:self.pos]
                self.pos = 0

            chunk = self.stream.read(self.CHUNK_SIZE)
            if chunk:
                self.raw_bytes_read += len(chunk)
                if self.raw_bytes_read > MAX_METADATA_SIZE:
                    raise BadRequest(f'File exceeds maximum size limit of {MAX_METADATA_SIZE} bytes.')

                if self.decompressor is not None:
                    try:
                        if self.encoding in ('gzip', 'deflate'):
                            decompressed = self.decompressor.decompress(chunk)
                            while self.decompressor.unused_data:
                                unused = self.decompressor.unused_data
                                self.decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
                                decompressed += self.decompressor.decompress(unused)
                        elif self.encoding == 'br':
                            decompressed = self.decompressor.process(chunk)
                        else:
                            decompressed = chunk
                    except Exception as e:
                        raise BadRequest(f'Decompression error: {e}')
                else:
                    decompressed = chunk

                self.decompressed_bytes_read += len(decompressed)
                if self.decompressed_bytes_read > MAX_METADATA_SIZE:
                    raise BadRequest(f'File exceeds maximum size limit of {MAX_METADATA_SIZE} bytes.')

                self.buffer.extend(decompressed)
            else:
                self.eof = True
                if self.decompressor is not None:
                    try:
                        if self.encoding in ('gzip', 'deflate'):
                            decompressed = self.decompressor.flush()
                            if decompressed:
                                self.decompressed_bytes_read += len(decompressed)
                                if self.decompressed_bytes_read > MAX_METADATA_SIZE:
                                    raise BadRequest(f'File exceeds maximum size limit of {MAX_METADATA_SIZE} bytes.')
                                self.buffer.extend(decompressed)
                        elif self.encoding == 'br':
                            if not self.decompressor.is_finished() and self.raw_bytes_read > 0:
                                raise BadRequest('Decompression error: truncated compressed data.')
                    except Exception as e:
                        if isinstance(e, BadRequest):
                            raise
                        raise BadRequest(f'Decompression error: {e}')


@upload_blueprint.route('/api/upload/stream', methods=['POST'])
def stream_upload() -> Any:
    """Stream telemetry CSV directly into the handler.
    Consumes the stream and populates the database and files continuously.
    """
    acl = auth.get_current_acl()
    if not acl.get('upload_sessions'):
        raise Forbidden('Forbidden: No session upload permission.')

    content_encoding = request.content_encoding or request.headers.get('Content-Encoding')
    line_reader = StreamingLineReader(request.stream, content_encoding=content_encoding)
    read_line_with_limit = line_reader.readline

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
    track_info = db.get_track(metadata.track_name)
    if track_info and track_info.get('track_name'):
        metadata.track_name = track_info['track_name']

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
    if not aliases.is_sim_league(metadata.league):
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
    if existing_lines:
        ex_origin_lat = None
        ex_origin_lon = None
        ex_origin_heading = 0.0
        for ex_line in existing_lines:
            if not ex_line.strip():
                break
            parts = [p.strip() for p in ex_line.split(',', 1)]
            if len(parts) == 2:
                pk = parts[0].lower()
                pv = parts[1]
                if pk == 'distance to rear axle' and metadata.distance_to_rear_axle is None:
                    try:
                        metadata.distance_to_rear_axle = float(pv)
                    except (ValueError, TypeError):
                        pass
                elif pk == 'track configuration' and metadata.track_configuration is None:
                    metadata.track_configuration = pv
                elif pk == 'track origin latitude':
                    ex_origin_lat = float(pv)
                elif pk == 'track origin longitude':
                    ex_origin_lon = float(pv)
                elif pk == 'track origin heading':
                    ex_origin_heading = float(pv)
        if metadata.origin is None:
            metadata.origin = create_track_origin(
                league=metadata.league,
                track_name=metadata.track_name,
                lat=ex_origin_lat,
                lon=ex_origin_lon,
                heading=ex_origin_heading,
            )
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
        if metadata.track_configuration is not None:
            writer.writerow(['Track configuration', metadata.track_configuration])
        writer.writerow(['Date', metadata.session_start_datetime.strftime('%Y-%m-%d')])
        writer.writerow(['Time', metadata.session_start_datetime.strftime('%H:%M:%S')])
        writer.writerow(['Driver name', metadata.driver_name])
        writer.writerow(['League', metadata.league])
        writer.writerow(['Class', metadata.class_name])
        writer.writerow(['Session', metadata.session_name])
        if metadata.distance_to_rear_axle is not None:
            writer.writerow(['Distance to rear axle', metadata.distance_to_rear_axle])
        if metadata.origin is not None:
            writer.writerow(['Track Origin Latitude', metadata.origin.lat])
            writer.writerow(['Track Origin Longitude', metadata.origin.lon])
            writer.writerow(['Track Origin Heading', metadata.origin.heading])
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
                        if os.path.exists(meta_path):
                            try:
                                with open(meta_path, 'r', encoding='utf-8') as f:
                                    ex_meta = json.load(f)
                                ex_dt_str = ex_meta.get('session_start_datetime')
                                if ex_dt_str:
                                    ex_dt = session_time.parse_session_start_datetime_tolerant(ex_dt_str)
                                    resolved_dt = session_time.resolve_start_datetime_overwrite(
                                        ex_dt, metadata.session_start_datetime, getattr(metadata, 'has_seconds', False)
                                    )
                                    metadata_json['session_start_datetime'] = (
                                        resolved_dt.strftime('%Y-%m-%d %H:%M:%S')
                                        if (getattr(metadata, 'has_seconds', False) or resolved_dt.second)
                                        else resolved_dt.strftime('%Y-%m-%d %H:%M')
                                    )
                            except Exception:
                                pass

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

                    invalidate_plot_cache([db_session_id, session_filename])

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
    track_info = db.get_track(track_name)
    if track_info and track_info.get('track_name'):
        track_name = track_info['track_name']
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
        dt, has_seconds = session_time.parse_session_start_datetime(
            session_start_datetime, track_name=track_name, db=db
        )
    except Exception:
        raise BadRequest(session_time.INVALID_FORMAT_MESSAGE)

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

    # Check if session already exists to apply overwrite retention rule for seconds
    existing_dt = None
    if os.path.exists(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                ex_meta = json.load(f)
            ex_str = ex_meta.get('session_start_datetime')
            if ex_str:
                existing_dt = session_time.parse_session_start_datetime_tolerant(ex_str)
        except Exception:
            pass

    if existing_dt is None:
        candidate_session_id = f'{meeting_folder}_{session_filename}'
        try:
            with db.conn.cursor() as cur:
                cur.execute(
                    'SELECT session_start_datetime FROM sessions WHERE session_id = %s',
                    (candidate_session_id,)
                )
                row = cur.fetchone()
                if row and row[0]:
                    existing_dt = row[0]
        except Exception:
            pass

    resolved_dt = session_time.resolve_start_datetime_overwrite(existing_dt, dt, has_seconds)
    if has_seconds or (existing_dt is not None and resolved_dt == existing_dt and resolved_dt.second):
        meta['session_start_datetime'] = resolved_dt.strftime('%Y-%m-%d %H:%M:%S')
    else:
        meta['session_start_datetime'] = resolved_dt.strftime('%Y-%m-%d %H:%M')

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
    invalidate_plot_cache([db_session_id, session_filename])

    return jsonify({
        'message': 'Files uploaded and session imported successfully.',
        'session_id': db_session_id
    }), 200
