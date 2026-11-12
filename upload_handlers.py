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
import struct
import math
import datetime
import time
import logging
import re
import fcntl
import contextlib
from urllib.parse import unquote
import zlib
import brotli  # type: ignore[import-untyped]
from typing import Any, Iterable, Iterator
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
import telemetry_brtc
import telemetry_store

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
_SAFE_SESSION_ID_RE = re.compile(r'^[A-Za-z0-9_-]+$')


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
    session_id: str | None = None
    stream: str = ''
    subsecond_us: int = 0


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
    if league in ('acevo', 'ac'):
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


def resolve_track(
    track_name: str,
    track_configuration: str | None = None,
) -> tuple[dict | None, str]:
    """Resolves track info and canonical track name using track name and optional configuration."""
    if not track_name:
        return None, track_name
    lookup_name = (
        f'{track_name} {track_configuration}'
        if track_configuration
        else track_name
    )
    track_info = db.get_track(lookup_name)
    if not track_info and track_configuration:
        track_info = db.get_track(track_name)
    resolved_track_name = track_name
    if track_info and track_info.get('track_name'):
        resolved_track_name = str(track_info['track_name'])
    return track_info, resolved_track_name


def detect_distance_to_rear_axle(target: str | None) -> float | None:
    """Auto-detect distance to rear axle (in meters) from a session path, league/class string, or URL.

    - FAT Cadet: 0.45 m (if target contains 'fat[^/]*/cadet')
    - Club 100 Cadet: 1.5 m (if target contains 'club100[^/]*/cade[td]')
    """
    if not target:
        return None
    decoded = unquote(str(target))
    if re.search(r'fat[^/]*/cade[td]', decoded, re.IGNORECASE):
        return 0.45
    if re.search(r'club100[^/]*/cade[td]', decoded, re.IGNORECASE):
        return 1.5
    return None


def parse_csv_metadata_rows(metadata_rows: Iterable[Iterable[str]]) -> CSVSessionMetadata:
    """Parses list of CSV rows containing metadata (key, value) pairs
    and returns a standardized CSVSessionMetadata dataclass.
    """
    driver_name = None
    track_name = None
    league = None
    class_name = None
    session_name = None
    session_id = None
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
    stream_name = ''

    date_str = None
    time_str = None

    for row_raw in metadata_rows:
        row = list(row_raw)
        if not row or len(row) < 2:
            continue
        key = row[0].strip().lower()
        val = row[1].strip()
        if key == 'stream':
            try:
                stream_name = telemetry_store.normalize_stream_name(val)
            except ValueError as e:
                raise BadRequest(str(e)) from e
            continue
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
        elif key in ('session id', 'session_id'):
            raw_sid = val.strip()
            if not _SAFE_SESSION_ID_RE.match(raw_sid):
                raise BadRequest(f'Invalid Session ID: {val!r}')
            sanitized = sanitize.sanitize_filename(raw_sid)
            if not sanitized:
                raise BadRequest(f'Invalid Session ID: {val!r}')
            session_id = sanitized
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
    subsecond_us = 0
    if date_str and time_str:
        date_clean = date_str.strip()
        time_clean = time_str.strip()
        if 'T' in date_clean or (len(date_clean) > 10 and ' ' in date_clean):
            session_start_datetime_str = date_clean
        else:
            session_start_datetime_str = f'{date_clean} {time_clean}'
        m_frac = re.search(r':\d{2}\.(\d+)', session_start_datetime_str)
        if m_frac:
            subsecond_us = int((m_frac.group(1) + '000000')[:6])
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
        session_id=session_id,
        stream=stream_name,
        subsecond_us=subsecond_us,
    )


def parse_json_metadata(meta_dict: dict[str, Any]) -> CSVSessionMetadata:
    """Parses metadata dictionary from BRT1 metadata JSON chunk into CSVSessionMetadata.

    Follows the canonical CSV metadata format fields.
    """
    rows = [[str(k), str(v)] for k, v in meta_dict.items() if v is not None]
    return parse_csv_metadata_rows(rows)


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

    # Load track data
    track_info, resolved_track_name = resolve_track(
        session_info.track_name, session_info.track_configuration
    )
    centerline = None
    if track_info:
        centerline = track_info.get('center_line')
    else:
        logger.warning(f"Track data not found for: '{resolved_track_name}'")
        track_info = {}

    # Coordinate conversion
    lat_list = []
    lon_list = []

    origin: track_data.TrackOrigin | None = session_info.origin
    if origin is None:
        try:
            if session_info.league == 'kartsim':
                origin = rfactor.get_track_origin(resolved_track_name)
            elif session_info.league in ('acevo', 'ac'):
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

    speeds_ms = np.array([safe_float(r.get('Speed'), 0.0) for r in records], dtype=float)

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

    candidate_times = [lap['time'] for lap in laps_data if lap['time'] > 0 and lap['num'] in valid_lap_nums]
    if candidate_times:
        median_lap_time = float(np.median(candidate_times))
        if median_lap_time > 0:
            for lap in laps_data:
                if lap['num'] in valid_lap_nums and lap['time'] > 10.0 * median_lap_time:
                    valid_lap_nums.remove(lap['num'])

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

    kart_num_str = str(session_info.kart_number) if session_info.kart_number is not None else ''
    summary_row = [
        '1', '0', kart_num_str, session_info.driver_name, str(len(valid_laps)),
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
        or sanitize.humanize_track_name(resolved_track_name)
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


@contextlib.contextmanager
def session_file_lock(lock_path: str) -> Iterator[None]:
    """Process-level mutual-exclusion lock using fcntl.flock on a lockfile across worker processes."""
    lock_fd = -1
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    except OSError as e:
        logger.warning(f"Failed to open session lock file {lock_path}: {e}")
        yield
        return

    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        except OSError as e:
            logger.warning(f"Failed to flock session lock file {lock_path}: {e}")
        yield
    finally:
        if lock_fd != -1:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(lock_fd)
            except OSError:
                pass


def merge_driver_summary(summary_path: str, new_driver_df: pd.DataFrame) -> pd.DataFrame:
    """Merges new_driver_df with existing drivers in summary_path, re-calculating positions and gaps."""
    if new_driver_df is None or new_driver_df.empty:
        if os.path.exists(summary_path):
            try:
                return pd.read_csv(summary_path)
            except Exception:
                pass
        return pd.DataFrame()

    new_driver_name = str(new_driver_df['Name'].iloc[0]).strip()

    def _get_val(col_name: str, default: str = '') -> str:
        if col_name in new_driver_df.columns:
            v = new_driver_df[col_name].iloc[0]
            if pd.notna(v):
                return str(v)
        return default

    new_driver_entry: dict[str, Any] = {
        'No': _get_val('No'),
        'Name': new_driver_name,
        'Laps': _get_val('Laps', '0'),
        'Time': _get_val('Time'),
        'Avg Speed': _get_val('Avg Speed'),
        'Gap': _get_val('Gap'),
        'Best': _get_val('Best'),
        'On': _get_val('On'),
        'Positions Gained': _get_val('Positions Gained', '0'),
        'lap_times': {},
    }
    for col in new_driver_df.columns:
        m = re.match(r'^Lap (\d+)$', col)
        if m:
            lap_num = int(m.group(1))
            val = str(new_driver_df[col].iloc[0]) if pd.notna(new_driver_df[col].iloc[0]) else ''
            if val:
                new_driver_entry['lap_times'][lap_num] = val

    existing_drivers: list[dict[str, Any]] = []
    seen_drivers: set[str] = set()

    if os.path.exists(summary_path):
        try:
            with open(summary_path, 'r', encoding='utf-8') as f:
                reader = list(csv.reader(f))
            if reader and len(reader) >= 2:
                header = reader[0]
                col_map = {col_name.strip(): idx for idx, col_name in enumerate(header)}
                name_idx = col_map.get('Name')
                if name_idx is not None:
                    lap_col_map = {}
                    for idx, col_name in enumerate(header):
                        m = re.match(r'^Lap (\d+)$', col_name.strip())
                        if m:
                            lap_col_map[int(m.group(1))] = idx

                    for row in reader[1:]:
                        if not row or len(row) <= name_idx:
                            continue
                        d_name = row[name_idx].strip()
                        if not d_name:
                            continue
                        if d_name.lower() == new_driver_name.lower():
                            # If existing entry had a kart number and new entry doesn't, preserve it
                            no_idx = col_map.get('No')
                            if not new_driver_entry['No'] and no_idx is not None and no_idx < len(row):
                                new_driver_entry['No'] = row[no_idx].strip()
                            continue
                        if d_name.lower() in seen_drivers:
                            continue
                        seen_drivers.add(d_name.lower())

                        def get_cell(c_name: str, default: str = '') -> str:
                            idx = col_map.get(c_name)
                            if idx is not None and idx < len(row):
                                return row[idx].strip()
                            return default

                        d_entry: dict[str, Any] = {
                            'No': get_cell('No'),
                            'Name': d_name,
                            'Laps': get_cell('Laps', '0'),
                            'Time': get_cell('Time'),
                            'Avg Speed': get_cell('Avg Speed'),
                            'Gap': get_cell('Gap'),
                            'Best': get_cell('Best'),
                            'On': get_cell('On'),
                            'Positions Gained': get_cell('Positions Gained', '0'),
                            'lap_times': {},
                        }
                        for lap_num, idx in lap_col_map.items():
                            if idx < len(row):
                                l_val = row[idx].strip()
                                if l_val:
                                    d_entry['lap_times'][lap_num] = l_val
                        existing_drivers.append(d_entry)
        except Exception as e:
            logger.warning(f"Error reading existing summary CSV {summary_path}: {e}")

    all_drivers = existing_drivers + [new_driver_entry]

    def driver_sort_key(d: dict[str, Any]) -> tuple:
        best_sec = sanitize.parse_time(d.get('Best'))
        has_best = (best_sec is not None and best_sec > 0)
        laps_count = 0
        try:
            laps_count = int(float(d.get('Laps', 0)))
        except (ValueError, TypeError):
            laps_count = len(d.get('lap_times', {}))
        return (
            0 if has_best else 1,
            best_sec if has_best else -laps_count,
            -laps_count,
            d.get('Name', '').lower()
        )

    all_drivers.sort(key=driver_sort_key)

    leader_best_sec = sanitize.parse_time(all_drivers[0].get('Best'))
    for i, d in enumerate(all_drivers):
        d['Pos'] = str(i + 1)
        if i == 0:
            d['Gap'] = ''
        else:
            d_best = sanitize.parse_time(d.get('Best'))
            if leader_best_sec is not None and d_best is not None:
                diff = d_best - leader_best_sec
                d['Gap'] = f'+{diff:.3f}' if diff > 0 else '+0.000'
            else:
                d['Gap'] = ''

    max_lap_num = 0
    for d in all_drivers:
        if d['lap_times']:
            max_lap_num = max(max_lap_num, max(d['lap_times'].keys()))

    headers = ['Pos', 'Positions Gained', 'No', 'Name', 'Laps', 'Time', '', 'Avg Speed', 'Gap', 'Best', 'On']
    for idx in range(1, max_lap_num + 1):
        headers.append(f'Lap {idx}')

    rows: list[list[str]] = []
    for d in all_drivers:
        row = [
            d['Pos'],
            d['Positions Gained'],
            d['No'],
            d['Name'],
            d['Laps'],
            d['Time'],
            '',
            d['Avg Speed'],
            d['Gap'],
            d['Best'],
            d['On'],
        ]
        for idx in range(1, max_lap_num + 1):
            row.append(d['lap_times'].get(idx, ''))
        rows.append(row)

    return pd.DataFrame(rows, columns=headers)


class StreamingByteReader:
    """Reads bytes or lines incrementally from a streaming request body with optional compression.

    Supports uncompressed (identity), gzip, and brotli (br) Content-Encoding.
    Enforces maximum size limits on both raw incoming data and decompressed data.
    Provides read_exact(n) and peek(n) for binary stream decoding, and readline() for text.
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

    def _fill_buffer(self, min_bytes: int = 1) -> None:
        while len(self.buffer) - self.pos < min_bytes and not self.eof:
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

    def _compact_buffer(self) -> None:
        if self.pos >= len(self.buffer):
            self.buffer.clear()
            self.pos = 0
        elif self.pos > 32 * 1024:
            del self.buffer[:self.pos]
            self.pos = 0

    def peek(self, n: int) -> bytes:
        """Peeks up to n bytes ahead without advancing the read position."""
        self._fill_buffer(n)
        return bytes(self.buffer[self.pos:self.pos + n])

    def read_exact(self, n: int) -> bytes:
        """Reads exactly n bytes, or fewer if EOF is reached."""
        self._fill_buffer(n)
        available = min(n, len(self.buffer) - self.pos)
        res = bytes(self.buffer[self.pos:self.pos + available])
        self.pos += available
        self._compact_buffer()
        return res

    def readline(self) -> str | None:
        while True:
            idx = self.buffer.find(b'\n', self.pos)
            if idx != -1:
                line_bytes = self.buffer[self.pos:idx + 1]
                self.pos = idx + 1
                self._compact_buffer()
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

            self._fill_buffer(len(self.buffer) - self.pos + 1)

    def read_chunk(self) -> tuple[int, bytes] | None:
        """Reads a single framed BRT1 chunk: 2B length, 1B type, and payload bytes.

        Returns:
            A tuple of (chunk_type, payload), or None if at EOF.
        Raises:
            BadRequest: If the 3-byte header or chunk payload is truncated.
        """
        hdr = self.read_exact(3)
        if not hdr:
            return None
        if len(hdr) < 3:
            raise BadRequest('Truncated BRT1 chunk header.')
        chunk_len, chunk_type = struct.unpack('<HB', hdr)
        payload = self.read_exact(chunk_len)
        if len(payload) < chunk_len:
            raise BadRequest(
                f'Truncated BRT1 chunk payload: expected {chunk_len} bytes, got {len(payload)}.'
            )
        return chunk_type, payload

    def read_chunks(self) -> Iterator[tuple[int, bytes]]:
        """Yields framed BRT1 chunks (chunk_type, payload) until EOF."""
        while (chunk := self.read_chunk()) is not None:
            yield chunk


def parse_brt1_stream_header(
    reader: StreamingByteReader,
) -> tuple[CSVSessionMetadata, list[str], list[float]]:
    """Reads and validates BRT1 Magic chunk (0x01) and Metadata JSON chunk (0x02)."""
    magic_chunk = reader.read_chunk()
    if magic_chunk is None or magic_chunk[0] != 0x01 or magic_chunk[1] != b'BRT1':
        raise BadRequest('Invalid BRT1 magic chunk.')

    meta_chunk = reader.read_chunk()
    if meta_chunk is None:
        raise BadRequest('Truncated BRT1 metadata chunk header.')
    meta_type, meta_payload = meta_chunk
    if meta_type != 0x02:
        raise BadRequest(f'Expected BRT1 metadata chunk (0x02), got 0x{meta_type:02x}')

    try:
        meta_json = json.loads(meta_payload.decode('utf-8'))
    except Exception as e:
        raise BadRequest(f'Invalid JSON in BRT1 metadata chunk: {e}')

    metadata = parse_json_metadata(meta_json.get('metadata', {}))
    channels = meta_json.get('channels', [])
    if not channels or not isinstance(channels, list):
        raise BadRequest('BRT1 metadata chunk must contain a non-empty channels list.')

    channel_names: list[str] = []
    channel_scales: list[float] = []
    for ch in channels:
        if not isinstance(ch, dict) or 'name' not in ch or 'scale' not in ch:
            raise BadRequest('Each channel in channels must have "name" and "scale".')
        name = str(ch['name'])
        try:
            scale = float(ch['scale'])
        except (ValueError, TypeError):
            raise BadRequest(f'Invalid scale for channel {name!r}: {ch["scale"]!r}')
        if scale <= 0 or math.isnan(scale):
            raise BadRequest(f'Channel scale must be positive: {name!r}')
        channel_names.append(name)
        channel_scales.append(scale)

    return metadata, channel_names, channel_scales


def iter_brt1_records(
    reader: StreamingByteReader,
    session_start_datetime: datetime.datetime,
    channel_names: list[str],
    channel_scales: list[float],
) -> Iterator[dict[str, Any]]:
    """Yields telemetry record dictionaries decoded from BRT1 binary chunks (0x03, 0x04)."""
    M = len(channel_names)
    time_idx = channel_names.index('Time') if 'Time' in channel_names else None
    current_values = [math.nan] * M

    def record_from_current_values() -> dict[str, Any]:
        rec: dict[str, Any] = {}
        for idx, (c_name, c_val) in enumerate(zip(channel_names, current_values)):
            if math.isnan(c_val):
                rec[c_name] = ''
            elif idx == time_idx:
                dt_rec = (
                    session_start_datetime.replace(tzinfo=None)
                    + datetime.timedelta(seconds=c_val)
                )
                rec[c_name] = dt_rec.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
            elif c_name == 'Lap':
                rec[c_name] = str(int(round(c_val)))
            else:
                rec[c_name] = round(float(c_val), 8)
        return rec

    for chunk_type, payload in reader.read_chunks():
        if chunk_type == 0x03:  # Baseline / Keyframe
            if len(payload) != M * 8:
                raise BadRequest(
                    f'Invalid chunk length {len(payload)} for baseline chunk with {M} channels (expected {M * 8}).'
                )
            unpacked_floats = struct.unpack(f'<{M}d', payload)
            current_values = list(unpacked_floats)
            if time_idx is None or not math.isnan(current_values[time_idx]):
                yield record_from_current_values()
        elif chunk_type == 0x04:  # Telemetry Delta
            sample_size = M * 2
            if sample_size == 0 or len(payload) % sample_size != 0:
                raise BadRequest(
                    f'Invalid chunk length {len(payload)} for delta chunk with {M} channels '
                    f'(must be multiple of {sample_size}).'
                )
            for deltas in struct.iter_unpack(f'<{M}h', payload):
                for j in range(M):
                    d_val = deltas[j]
                    if d_val == -32768:
                        current_values[j] = math.nan
                    else:
                        current_values[j] += d_val * channel_scales[j]
                yield record_from_current_values()
        elif chunk_type in (0x01, 0x02):
            pass
        elif chunk_type == 0xFF:  # End of Stream (EOS)
            break
        else:
            raise BadRequest(f'Unknown BRT1 chunk type: 0x{chunk_type:02x}')


def iter_csv_records(
    reader: StreamingByteReader,
    csv_header_line_str: str,
) -> Iterator[dict[str, Any]]:
    """Yields telemetry record dictionaries decoded from CSV lines."""
    fieldnames = [c.strip() for c in next(csv.reader([csv_header_line_str]))]
    while (line_str := reader.readline()) is not None:
        if not line_str.strip():
            continue
        row_cols = next(csv.reader([line_str]))
        record = {
            fieldnames[idx]: val
            for idx, val in enumerate(row_cols)
            if idx < len(fieldnames) and val is not None
        }
        yield record


@dataclass
class _UploadSessionContext:
    metadata: CSVSessionMetadata
    meeting_dir: str
    telemetry_dir: str
    session_filename: str
    driver_stem: str
    meta_path: str
    summary_path: str
    has_alphatiming: bool


def _build_segment_meta_dict(metadata: CSVSessionMetadata) -> dict[str, Any]:
    """Build canonical metadata dictionary stored inside BRT1 segment 0x02 chunks."""
    time_val = metadata.session_start_datetime.strftime('%H:%M:%S')
    if metadata.subsecond_us > 0:
        frac = f'{metadata.subsecond_us:06d}'.rstrip('0')
        time_val = f'{time_val}.{frac}'
    out: dict[str, Any] = {
        'Track name': metadata.track_name,
        'Date': metadata.session_start_datetime.strftime('%Y-%m-%d'),
        'Time': time_val,
        'Driver name': metadata.driver_name,
        'League': metadata.league,
        'Class': metadata.class_name,
        'Session': metadata.session_name,
    }
    if metadata.session_id:
        out['Session ID'] = metadata.session_id
    if metadata.stream:
        out['stream'] = metadata.stream
    if metadata.kart_number is not None:
        out['kart_number'] = metadata.kart_number
    if metadata.track_conditions is not None:
        out['Conditions'] = metadata.track_conditions
    if metadata.temperature is not None:
        out['Temperature'] = metadata.temperature
    if metadata.weather is not None:
        out['Weather'] = metadata.weather
    if metadata.distance_to_rear_axle is not None:
        out['Distance to rear axle'] = metadata.distance_to_rear_axle
    if metadata.track_configuration is not None:
        out['Track configuration'] = metadata.track_configuration
    if metadata.origin is not None:
        out['Track Origin Latitude'] = metadata.origin.lat
        out['Track Origin Longitude'] = metadata.origin.lon
        out['Track Origin Heading'] = metadata.origin.heading
    return out


def _inherit_existing_driver_metadata(
    metadata: CSVSessionMetadata,
    telemetry_dir: str,
    driver_stem: str,
) -> None:
    """Inherit optional calibration/origin metadata from pre-existing driver telemetry when omitted."""
    existing_meta: dict[str, Any] = {}
    prim_brtc = telemetry_store.primary_brtc_path(telemetry_dir, driver_stem)
    act_prim_dir = os.path.join(
        telemetry_store.active_dir_path(telemetry_dir, driver_stem),
        telemetry_store.PRIMARY_SUBDIR,
    )
    csv_path = telemetry_store.legacy_csv_path(telemetry_dir, driver_stem)

    if os.path.exists(prim_brtc):
        try:
            existing_meta = dict(telemetry_brtc.read_brtc_manifest(prim_brtc).get('metadata') or {})
        except Exception:
            existing_meta = {}

    if not existing_meta and os.path.isdir(act_prim_dir):
        for fn in sorted(os.listdir(act_prim_dir)):
            if fn.endswith('.brt1'):
                try:
                    raw_m, _, _, _, s_dt = telemetry_store.read_brt1_segment(os.path.join(act_prim_dir, fn))
                    existing_meta = telemetry_store._normalize_metadata_dict(raw_m, s_dt)
                    if existing_meta:
                        break
                except Exception:
                    pass

    if existing_meta:
        if metadata.distance_to_rear_axle is None and existing_meta.get('distance_to_rear_axle') is not None:
            try:
                metadata.distance_to_rear_axle = float(existing_meta['distance_to_rear_axle'])
            except (ValueError, TypeError):
                pass
        if metadata.track_configuration is None and existing_meta.get('track_configuration'):
            metadata.track_configuration = str(existing_meta['track_configuration'])
        if metadata.origin is None and existing_meta.get('track_origin_lat') is not None:
            metadata.origin = create_track_origin(
                league=metadata.league,
                track_name=metadata.track_name,
                lat=float(existing_meta['track_origin_lat']),
                lon=float(existing_meta['track_origin_lon']),
                heading=float(existing_meta.get('track_origin_heading', 0.0)),
            )
        return

    existing_lines = load_existing_telemetry_lines(csv_path)
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


def _load_preexisting_primary_records_for_summary(
    telemetry_dir: str,
    driver_stem: str,
) -> list[dict[str, Any]]:
    """Load pre-existing primary records (from .brtc, .active/_primary, or .csv) for leaderboard merging."""
    prim_brtc = telemetry_store.primary_brtc_path(telemetry_dir, driver_stem)
    csv_p = telemetry_store.legacy_csv_path(telemetry_dir, driver_stem)
    act_prim_dir = os.path.join(
        telemetry_store.active_dir_path(telemetry_dir, driver_stem),
        telemetry_store.PRIMARY_SUBDIR,
    )
    seg_paths: list[str] = []
    if os.path.isdir(act_prim_dir):
        seg_paths = [
            os.path.join(act_prim_dir, fn)
            for fn in sorted(os.listdir(act_prim_dir))
            if fn.endswith('.brt1')
        ]

    if not seg_paths and not os.path.exists(prim_brtc) and not os.path.exists(csv_p):
        return []

    p_meta, p_order, p_cols, _ = telemetry_store._merge_stream_data(
        existing_brtc=prim_brtc if os.path.exists(prim_brtc) else None,
        existing_csv=csv_p if os.path.exists(csv_p) else None,
        segment_paths=seg_paths,
    )
    if not p_cols or 'Time' not in p_cols or len(p_cols['Time']) == 0:
        return []

    start_date_str = str(p_meta.get('session_start_date') or '1970-01-01')
    try:
        d_obj = datetime.date.fromisoformat(start_date_str[:10])
    except ValueError:
        d_obj = datetime.date(1970, 1, 1)
    midnight = datetime.datetime(d_obj.year, d_obj.month, d_obj.day)

    cols_to_read = [c for c in p_order if c in p_cols]
    n_rows = len(p_cols['Time'])
    records: list[dict[str, Any]] = []
    for i in range(n_rows):
        t_sec = float(p_cols['Time'][i])
        if math.isnan(t_sec):
            continue
        rec: dict[str, Any] = {}
        for c_name in cols_to_read:
            val = float(p_cols[c_name][i])
            if math.isnan(val) or math.isinf(val):
                rec[c_name] = ''
            elif c_name == 'Time':
                dt_sample = midnight + datetime.timedelta(seconds=val)
                rec['Time'] = dt_sample.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
            elif c_name == 'Lap':
                rec['Lap'] = str(int(round(val)))
            else:
                rec[c_name] = val
        records.append(rec)
    return records


def _match_existing_aux_session_filename(
    meeting_dir: str,
    telemetry_dir: str,
    default_session_filename: str,
    session_san: str,
    driver_san: str,
    incoming_dt: datetime.datetime,
) -> str:
    """Associate an auxiliary upload lacking an explicit Session ID with an ongoing primary session."""
    default_stem = f'{default_session_filename}_{driver_san}'
    if (
        os.path.exists(os.path.join(meeting_dir, f'{default_session_filename}_metadata.json'))
        or os.path.isdir(telemetry_store.active_dir_path(telemetry_dir, default_stem))
        or os.path.exists(telemetry_store.primary_brtc_path(telemetry_dir, default_stem))
        or os.path.exists(telemetry_store.legacy_csv_path(telemetry_dir, default_stem))
    ):
        return default_session_filename

    pattern = re.compile(rf'^(\d{{2}}_\d{{2}}_{re.escape(session_san)})_{re.escape(driver_san)}$')
    best_match: tuple[int, float, str] | None = None
    for stem in telemetry_store.list_driver_stems(telemetry_dir):
        m = pattern.match(stem)
        if not m:
            continue
        cand_session = m.group(1)
        cand_dt: datetime.datetime | None = None
        cand_meta_p = os.path.join(meeting_dir, f'{cand_session}_metadata.json')
        if os.path.exists(cand_meta_p):
            try:
                with open(cand_meta_p, 'r', encoding='utf-8') as f:
                    cm = json.load(f)
                if cm.get('session_start_datetime'):
                    cand_dt = session_time.parse_session_start_datetime_tolerant(cm['session_start_datetime'])
            except Exception:
                cand_dt = None
        if cand_dt is None:
            try:
                hh = int(cand_session[0:2])
                mm = int(cand_session[3:5])
                cand_dt = incoming_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
            except ValueError:
                continue

        delta_s = (incoming_dt - cand_dt).total_seconds()
        if -120.0 <= delta_s <= 7200.0:
            is_active = os.path.isdir(telemetry_store.active_dir_path(telemetry_dir, stem))
            rank = (0 if is_active else 1, abs(delta_s), cand_session)
            if best_match is None or rank < best_match:
                best_match = rank

    return best_match[2] if best_match is not None else default_session_filename


def _resolve_upload_session_context(
    metadata: CSVSessionMetadata,
    acl: dict[str, Any],
) -> _UploadSessionContext:
    """Validate upload ACLs, resolve canonical track/class aliases, and prepare session paths."""
    if not auth.can_upload_session_for_league(acl, metadata.league):
        raise Forbidden(f'Forbidden: Upload for league {metadata.league!r} is not permitted.')
    if not auth.can_upload_session_for_driver(acl, metadata.driver_name):
        raise Forbidden(f'Forbidden: Upload for driver {metadata.driver_name!r} not permitted.')

    metadata.class_name = aliases.resolve_car_name(metadata.class_name)
    _, metadata.track_name = resolve_track(metadata.track_name, metadata.track_configuration)

    time_str = metadata.session_start_datetime.strftime('%H_%M')
    start_date = metadata.session_start_datetime.strftime('%Y_%m_%d')
    track_sanitized = sanitize.sanitize_filename(metadata.track_name)
    meeting_folder = f'{start_date}_{track_sanitized}'
    meeting_dir = os.path.join(DATA_DIR, metadata.league, metadata.class_name, meeting_folder)
    telemetry_dir = os.path.join(meeting_dir, 'telemetry')
    os.makedirs(telemetry_dir, exist_ok=True)

    driver_san = sanitize.sanitize_filename(metadata.driver_name)
    if metadata.session_id:
        raw_sid = metadata.session_id.strip()
        if not _SAFE_SESSION_ID_RE.match(raw_sid):
            raise BadRequest(f'Invalid Session ID: {metadata.session_id!r}')
        session_filename = sanitize.sanitize_filename(raw_sid)
        if not session_filename:
            raise BadRequest(f'Invalid Session ID: {metadata.session_id!r}')
    else:
        session_san = sanitize.sanitize_filename(metadata.session_name)
        session_filename = f'{time_str}_{session_san}'
        if metadata.stream != '':
            session_filename = _match_existing_aux_session_filename(
                meeting_dir=meeting_dir,
                telemetry_dir=telemetry_dir,
                default_session_filename=session_filename,
                session_san=session_san,
                driver_san=driver_san,
                incoming_dt=metadata.session_start_datetime.replace(tzinfo=None),
            )

    driver_stem = f'{session_filename}_{driver_san}'
    meta_path = os.path.join(meeting_dir, f'{session_filename}_metadata.json')
    summary_path = os.path.join(meeting_dir, f'{session_filename}.csv')
    active_probe = telemetry_store.active_dir_path(telemetry_dir, driver_stem)

    meeting_real = os.path.realpath(meeting_dir)
    for p in (meta_path, summary_path, active_probe):
        if not os.path.realpath(p).startswith(meeting_real + os.sep):
            raise BadRequest('Invalid path traversal detected.')

    has_alphatiming = False
    if not aliases.is_sim_league(metadata.league):
        if os.path.exists(meta_path):
            with open(meta_path, 'r', encoding='utf-8') as f:
                existing_meta = json.load(f)
            if existing_meta.get('alphatiming_url', ''):
                has_alphatiming = True

    if not has_alphatiming:
        df = db.load(
            leagues=metadata.league,
            classes=metadata.class_name,
            date=metadata.session_start_datetime.strftime('%Y-%m-%d'),
            track=metadata.track_name,
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

    if metadata.stream == '':
        _inherit_existing_driver_metadata(metadata, telemetry_dir, driver_stem)

    return _UploadSessionContext(
        metadata=metadata,
        meeting_dir=meeting_dir,
        telemetry_dir=telemetry_dir,
        session_filename=session_filename,
        driver_stem=driver_stem,
        meta_path=meta_path,
        summary_path=summary_path,
        has_alphatiming=has_alphatiming,
    )


class _PrimarySummaryUpdater:
    """Maintains 15s / EOS session leaderboard summary updates for primary telemetry streams."""

    def __init__(self, ctx: _UploadSessionContext, preexisting_records: list[dict[str, Any]]) -> None:
        self.ctx = ctx
        self._preexisting = preexisting_records
        self._pruned_and_merged = False
        self._all_raw_records: list[dict[str, Any]] = []
        self._buffered_records: list[dict[str, Any]] = []
        self.db_session_id: str | None = None

    def add_record(self, rec: dict[str, Any]) -> None:
        self._buffered_records.append(rec)

    def has_buffered(self) -> bool:
        return bool(self._buffered_records)

    def flush(self) -> None:
        if not self._buffered_records:
            return
        new_records = list(self._buffered_records)
        self._buffered_records.clear()

        if not self._pruned_and_merged:
            new_timestamps = [
                dt for r in new_records
                if r.get('Time') and (dt := to_naive_datetime(r.get('Time'))) is not None
            ]
            if new_timestamps and self._preexisting:
                min_new_ts = min(new_timestamps)
                kept = [
                    r for r in self._preexisting
                    if (dt_old := to_naive_datetime(r.get('Time'))) is not None and dt_old < min_new_ts
                ]
                self._all_raw_records.extend(kept)
            self._all_raw_records.extend(new_records)
            self._pruned_and_merged = True
        else:
            self._all_raw_records.extend(new_records)

        if not self._all_raw_records:
            return

        metadata = self.ctx.metadata
        _, metadata_json, df_summary = process_telemetry_derivative_data(
            self._all_raw_records, metadata, DATA_DIR
        )

        if not self.ctx.has_alphatiming:
            lock_path = f'{self.ctx.summary_path}.lock'
            with session_file_lock(lock_path):
                if os.path.exists(self.ctx.meta_path):
                    try:
                        with open(self.ctx.meta_path, 'r', encoding='utf-8') as f:
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
                        for k in ('temperature', 'weather', 'track_conditions', 'track_temperature'):
                            if not metadata_json.get(k) and ex_meta.get(k):
                                metadata_json[k] = ex_meta[k]
                    except Exception:
                        pass

                with open(self.ctx.meta_path, 'w', encoding='utf-8') as f:
                    json.dump(metadata_json, f, indent=4)

                df_merged_summary = merge_driver_summary(self.ctx.summary_path, df_summary)
                df_merged_summary.to_csv(self.ctx.summary_path, index=False)
                self.db_session_id = populate_db.populate_single_session(self.ctx.meta_path, db.conn, DATA_DIR)
        else:
            meeting_folder_name = os.path.basename(self.ctx.meeting_dir)
            self.db_session_id = f'{meeting_folder_name}_{self.ctx.session_filename}'

        invalidate_plot_cache([self.db_session_id, self.ctx.session_filename])


def _cleanup_empty_segment(segment_path: str, telemetry_dir: str, driver_stem: str) -> None:
    """Remove an empty/aborted segment file and tidy empty .active directories."""
    if os.path.exists(segment_path):
        try:
            os.remove(segment_path)
        except OSError:
            pass
    act_dir = telemetry_store.active_dir_path(telemetry_dir, driver_stem)
    if os.path.isdir(act_dir):
        for sub in os.listdir(act_dir):
            sub_p = os.path.join(act_dir, sub)
            if os.path.isdir(sub_p):
                try:
                    if not os.listdir(sub_p):
                        os.rmdir(sub_p)
                except OSError:
                    pass
        try:
            if not os.listdir(act_dir):
                os.rmdir(act_dir)
        except OSError:
            pass


def _stream_brt1_into_segment(
    reader: StreamingByteReader,
    ctx: _UploadSessionContext,
    channel_names: list[str],
    channel_scales: list[float],
    segment_path: str,
    summary_updater: _PrimarySummaryUpdater | None,
) -> int:
    """Stream incoming BRT1 chunks directly into segment_path on disk (shared by primary & aux)."""
    m = len(channel_names)
    time_idx = channel_names.index('Time') if 'Time' in channel_names else None
    current_values = [math.nan] * m
    sample_count = 0
    last_flush = time.time()
    start_naive = ctx.metadata.session_start_datetime.replace(tzinfo=None)

    channels_spec = [
        {'name': name, 'scale': sc}
        for name, sc in zip(channel_names, channel_scales)
    ]
    header_meta = _build_segment_meta_dict(ctx.metadata)
    meta_bytes = json.dumps(
        {'metadata': header_meta, 'channels': channels_spec},
        separators=(',', ':'),
        ensure_ascii=False,
    ).encode('utf-8')

    def _make_summary_record() -> dict[str, Any]:
        rec: dict[str, Any] = {}
        for idx, (c_name, c_val) in enumerate(zip(channel_names, current_values)):
            if math.isnan(c_val):
                rec[c_name] = ''
            elif idx == time_idx:
                dt_rec = start_naive + datetime.timedelta(seconds=c_val)
                rec[c_name] = dt_rec.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
            elif c_name == 'Lap':
                rec[c_name] = str(int(round(c_val)))
            else:
                rec[c_name] = round(float(c_val), 8)
        return rec

    with open(segment_path, 'wb') as seg_file:
        seg_file.write(telemetry_store.pack_brt1_chunk(0x01, b'BRT1'))
        seg_file.write(telemetry_store.pack_brt1_chunk(0x02, meta_bytes))

        for chunk_type, payload in reader.read_chunks():
            if chunk_type == 0x03:
                if len(payload) != m * 8:
                    raise BadRequest(
                        f'Invalid chunk length {len(payload)} for baseline chunk with {m} channels '
                        f'(expected {m * 8}).'
                    )
                seg_file.write(telemetry_store.pack_brt1_chunk(0x03, payload))
                current_values = list(struct.unpack(f'<{m}d', payload))
                if time_idx is None or not math.isnan(current_values[time_idx]):
                    sample_count += 1
                    if summary_updater is not None:
                        summary_updater.add_record(_make_summary_record())
            elif chunk_type == 0x04:
                sample_size = m * 2
                if sample_size == 0 or len(payload) % sample_size != 0:
                    raise BadRequest(
                        f'Invalid chunk length {len(payload)} for delta chunk with {m} channels '
                        f'(must be multiple of {sample_size}).'
                    )
                seg_file.write(telemetry_store.pack_brt1_chunk(0x04, payload))
                if summary_updater is not None:
                    for deltas in struct.iter_unpack(f'<{m}h', payload):
                        for j in range(m):
                            d_val = deltas[j]
                            if d_val == -32768:
                                current_values[j] = math.nan
                            else:
                                current_values[j] += d_val * channel_scales[j]
                        sample_count += 1
                        summary_updater.add_record(_make_summary_record())
                else:
                    sample_count += len(payload) // sample_size
            elif chunk_type in (0x01, 0x02):
                pass
            elif chunk_type == 0xFF:
                seg_file.write(telemetry_store.pack_brt1_chunk(0xFF, b''))
                break
            else:
                raise BadRequest(f'Unknown BRT1 chunk type: 0x{chunk_type:02x}')

            now = time.time()
            if now - last_flush >= STREAM_FLUSH_INTERVAL_SEC:
                seg_file.flush()
                if summary_updater is not None and summary_updater.has_buffered():
                    summary_updater.flush()
                last_flush = time.time()

        seg_file.flush()
        if summary_updater is not None and summary_updater.has_buffered():
            summary_updater.flush()

    return sample_count


def _stream_csv_into_segment(
    records_iter: Iterator[dict[str, Any]],
    csv_header_line_str: str,
    ctx: _UploadSessionContext,
    segment_path: str,
    summary_updater: _PrimarySummaryUpdater | None,
) -> int:
    """Transcode incoming CSV telemetry rows directly into a .brt1 segment (zero CSV written to disk)."""
    raw_fields = [c.strip() for c in next(csv.reader([csv_header_line_str]))]
    channel_names = [f for f in raw_fields if f and f != 'Record']
    if not channel_names:
        return 0

    start_naive = ctx.metadata.session_start_datetime.replace(tzinfo=None)
    encoder = telemetry_store.BRT1SegmentEncoder(
        segment_path=segment_path,
        meta_dict=_build_segment_meta_dict(ctx.metadata),
        channel_names=channel_names,
    )
    sample_count = 0
    last_flush = time.time()

    try:
        for record in records_iter:
            row_vals: list[float] = []
            for ch in channel_names:
                raw_v = record.get(ch)
                if ch == 'Time':
                    dt = to_naive_datetime(raw_v)
                    if dt is not None:
                        row_vals.append((dt - start_naive).total_seconds())
                    else:
                        opt = parse_optional_float(raw_v)
                        row_vals.append(opt if opt is not None else math.nan)
                else:
                    opt = parse_optional_float(raw_v)
                    row_vals.append(opt if opt is not None else math.nan)

            encoder.append_row(row_vals)
            sample_count += 1
            if summary_updater is not None:
                summary_updater.add_record(record)

            now = time.time()
            if now - last_flush >= STREAM_FLUSH_INTERVAL_SEC:
                encoder.flush()
                if summary_updater is not None and summary_updater.has_buffered():
                    summary_updater.flush()
                last_flush = time.time()

        encoder.close(write_eos=True)
        if summary_updater is not None and summary_updater.has_buffered():
            summary_updater.flush()
    except Exception:
        encoder.close(write_eos=False)
        raise

    return sample_count


@upload_blueprint.route('/api/upload/stream', methods=['POST'])
def stream_upload() -> Any:
    """Stream telemetry (BRT1 binary or RaceTools CSV; primary or auxiliary stream) into .active/."""
    acl = auth.get_current_acl()
    if not acl.get('upload_sessions'):
        raise Forbidden('Forbidden: No session upload permission.')

    content_encoding = request.content_encoding or request.headers.get('Content-Encoding')
    reader = StreamingByteReader(request.stream, content_encoding=content_encoding)

    is_binary_brt1 = reader.peek(7) == b'\x04\x00\x01BRT1'
    channel_names: list[str] = []
    channel_scales: list[float] = []
    csv_header_line_str = ''

    if is_binary_brt1:
        metadata, channel_names, channel_scales = parse_brt1_stream_header(reader)
    else:
        header_lines: list[str] = []
        while line := reader.readline():
            if not line.strip():
                break
            header_lines.append(line)

        metadata = parse_csv_metadata_rows(csv.reader(header_lines))
        maybe_header = reader.readline()
        if not maybe_header:
            raise BadRequest('No telemetry records found in the streamed CSV.')
        csv_header_line_str = maybe_header

    ctx = _resolve_upload_session_context(metadata, acl)
    is_primary = (metadata.stream == '')

    preexisting_records = (
        _load_preexisting_primary_records_for_summary(ctx.telemetry_dir, ctx.driver_stem)
        if is_primary
        else []
    )
    summary_updater = _PrimarySummaryUpdater(ctx, preexisting_records) if is_primary else None
    segment_path = telemetry_store.create_active_segment_path(
        ctx.telemetry_dir, ctx.driver_stem, metadata.stream
    )

    sample_count = 0
    try:
        if is_binary_brt1:
            sample_count = _stream_brt1_into_segment(
                reader=reader,
                ctx=ctx,
                channel_names=channel_names,
                channel_scales=channel_scales,
                segment_path=segment_path,
                summary_updater=summary_updater,
            )
        else:
            records_iter = iter_csv_records(reader, csv_header_line_str)
            sample_count = _stream_csv_into_segment(
                records_iter=records_iter,
                csv_header_line_str=csv_header_line_str,
                ctx=ctx,
                segment_path=segment_path,
                summary_updater=summary_updater,
            )
    except Exception:
        # Clean up if no valid samples were written before the error
        try:
            _, _, _, seg_cols, _ = telemetry_store.read_brt1_segment(segment_path)
            has_any = any(len(arr) > 0 for arr in seg_cols.values())
        except Exception:
            has_any = False
        if not has_any:
            _cleanup_empty_segment(segment_path, ctx.telemetry_dir, ctx.driver_stem)
        elif summary_updater is not None and summary_updater.has_buffered():
            summary_updater.flush()
        raise

    if sample_count == 0 or (is_primary and summary_updater is not None and not summary_updater.db_session_id):
        _cleanup_empty_segment(segment_path, ctx.telemetry_dir, ctx.driver_stem)
        if is_binary_brt1:
            raise BadRequest('No telemetry records found in the binary stream.')
        raise BadRequest('No telemetry records found in the streamed CSV.')

    if is_primary and summary_updater is not None and summary_updater.db_session_id:
        db_session_id = summary_updater.db_session_id
    else:
        meeting_folder_name = os.path.basename(ctx.meeting_dir)
        db_session_id = f'{meeting_folder_name}_{ctx.session_filename}'
        invalidate_plot_cache([db_session_id, ctx.session_filename])

    resp_payload: dict[str, Any] = {
        'message': 'Session completed and imported successfully.',
        'session_id': db_session_id,
    }
    if metadata.stream:
        resp_payload['stream'] = metadata.stream
    return jsonify(resp_payload), 200


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
    track_info, track_name = resolve_track(
        track_name, meta.get('track_configuration')
    )
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

    # Supersede any stale sessions in the same meeting sharing the same AlphaTiming URL
    raw_alpha_url = meta.get('alphatiming_url') or ''
    base_alpha_url = re.sub(r'/(?:result|replay|laptimes)/?$', '', raw_alpha_url.strip()).rstrip('/')
    if base_alpha_url:
        meeting_rel = os.path.relpath(meeting_dir, DATA_DIR)
        with db.conn.cursor() as cur:
            cur.execute(
                """
                SELECT session_id FROM sessions
                WHERE meeting_id = %s
                  AND (alphatiming_url = %s OR alphatiming_url LIKE %s)
                  AND session_id != %s
                """,
                (meeting_rel, base_alpha_url, f'{base_alpha_url}/%', db_session_id)
            )
            stale_session_ids = [row[0] for row in cur.fetchall()]

        for old_id in stale_session_ids:
            prefix = f'{meeting_folder}_'
            old_filename = old_id[len(prefix):] if old_id.startswith(prefix) else old_id
            for ext in ('.csv', '_metadata.json', '_penalties.csv'):
                p = os.path.join(meeting_dir, f'{old_filename}{ext}')
                if os.path.exists(p):
                    os.remove(p)

            with db.conn.cursor() as cur:
                cur.execute('DELETE FROM sessions WHERE session_id = %s', (old_id,))
            db.conn.commit()

            invalidate_plot_cache([old_id, old_filename])

    return jsonify({
        'message': 'Files uploaded and session imported successfully.',
        'session_id': db_session_id
    }), 200
