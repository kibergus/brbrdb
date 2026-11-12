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

"""Unified telemetry storage manager for .active/ BRT1 segments, .brtc columns, and legacy CSVs.

Directory layout inside {meeting_dir}/telemetry/:
  - {driver_stem}.active/_primary/{arrival_ns}_{uuid}.brt1       (live primary stream segments)
  - {driver_stem}.active/aux_{stream}/{arrival_ns}_{uuid}.brt1   (live secondary stream segments)
  - {driver_stem}.primary.brtc                                   (compacted raw primary channels)
  - {driver_stem}.aux.{stream}.brtc                              (compacted raw secondary channels)
  - {driver_stem}.derived.brtc                                   (versioned computed & aligned channels)
  - {driver_stem}.csv                                            (legacy read-only fallback prior to compaction)
"""

from __future__ import annotations

import contextlib
import csv
import datetime
import fcntl
import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import struct
import tempfile
import time
import uuid
from typing import Any, Callable, Iterator

import numpy as np

from database import db
import session_time
import _telemetry_native
import telemetry_brtc
import telemetry_calculators

logger = logging.getLogger(__name__)

PRIMARY_SUBDIR = '_primary'
AUX_SUBDIR_PREFIX = 'aux_'
PRIMARY_BRTC_SUFFIX = '.primary.brtc'
DERIVED_BRTC_SUFFIX = '.derived.brtc'
AUX_BRTC_INFIX = '.aux.'
ACTIVE_DIR_SUFFIX = '.active'

EPHEMERAL_ROOT = os.path.join(tempfile.gettempdir(), 'brbr_ephemeral')
STREAM_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,63}$')

SMOOTH_CHANNELS_LOWER = frozenset({
    'gforcelat', 'gforcelon', 'gforcevert',
    'slide pct fl', 'slide pct fr', 'slide pct rl', 'slide pct rr',
    'lat force fl', 'lat force fr', 'lat force rl', 'lat force rr',
    'long force fl', 'long force fr', 'long force rl', 'long force rr',
    'lat force front', 'lat force rear',
    'long force front', 'long force rear',
    'tyre load fl', 'tyre load fr', 'tyre load rl', 'tyre load rr',
})


def should_smooth_channel(channel: str) -> bool:
    """Return True if channel uses 5-sample moving-average smoothing in KartSim."""
    return channel.lower() in SMOOTH_CHANNELS_LOWER


def normalize_stream_name(raw_stream: str | None) -> str:
    """Validate and normalize BRT1/CSV stream name. Primary stream is always ''."""
    if raw_stream is None:
        return ''
    cleaned = str(raw_stream).strip().lower()
    if cleaned == '':
        return ''
    if not STREAM_NAME_RE.fullmatch(cleaned) or cleaned == 'primary':
        raise ValueError(
            f'Invalid stream name {raw_stream!r}. Primary stream must be empty (""); '
            'secondary streams must match [a-z0-9][a-z0-9_-]{0,63} and not be "primary".'
        )
    return cleaned


def strip_telemetry_suffix(filename_or_stem: str) -> str:
    """Strip .csv, .primary.brtc, .derived.brtc, .aux.<stream>.brtc, or .active from an identifier."""
    s = filename_or_stem.strip()
    if s.endswith(PRIMARY_BRTC_SUFFIX):
        return s[:-len(PRIMARY_BRTC_SUFFIX)]
    if s.endswith(DERIVED_BRTC_SUFFIX):
        return s[:-len(DERIVED_BRTC_SUFFIX)]
    if AUX_BRTC_INFIX in s and s.endswith('.brtc'):
        idx = s.find(AUX_BRTC_INFIX)
        return s[:idx]
    if s.endswith(ACTIVE_DIR_SUFFIX):
        return s[:-len(ACTIVE_DIR_SUFFIX)]
    if s.endswith('.csv'):
        return s[:-4]
    return s


def primary_brtc_path(telemetry_dir: str, driver_stem: str) -> str:
    return os.path.join(telemetry_dir, f'{driver_stem}{PRIMARY_BRTC_SUFFIX}')


def derived_brtc_path(telemetry_dir: str, driver_stem: str) -> str:
    return os.path.join(telemetry_dir, f'{driver_stem}{DERIVED_BRTC_SUFFIX}')


def aux_brtc_path(telemetry_dir: str, driver_stem: str, stream_name: str) -> str:
    return os.path.join(telemetry_dir, f'{driver_stem}{AUX_BRTC_INFIX}{stream_name}.brtc')


def active_dir_path(telemetry_dir: str, driver_stem: str) -> str:
    return os.path.join(telemetry_dir, f'{driver_stem}{ACTIVE_DIR_SUFFIX}')


def legacy_csv_path(telemetry_dir: str, driver_stem: str) -> str:
    return os.path.join(telemetry_dir, f'{driver_stem}.csv')


def list_aux_brtc_files(telemetry_dir: str, driver_stem: str) -> dict[str, str]:
    """Return {stream_name: path} for compacted {driver_stem}.aux.<stream>.brtc files."""
    if not os.path.isdir(telemetry_dir):
        return {}
    prefix = f'{driver_stem}{AUX_BRTC_INFIX}'
    out: dict[str, str] = {}
    try:
        entries = sorted(os.listdir(telemetry_dir))
    except OSError:
        return {}
    for fname in entries:
        if fname.startswith(prefix) and fname.endswith('.brtc'):
            stream_name = fname[len(prefix):-5]
            if stream_name:
                out[stream_name] = os.path.join(telemetry_dir, fname)
    return out


def _active_dir_has_primary_segments(act_dir: str) -> bool:
    prim_sub = os.path.join(act_dir, PRIMARY_SUBDIR)
    if not os.path.isdir(prim_sub):
        return False
    try:
        return any(f.endswith('.brt1') for f in os.listdir(prim_sub))
    except OSError:
        return False


def _active_dir_has_any_segments(act_dir: str) -> bool:
    if not os.path.isdir(act_dir):
        return False
    try:
        for sub in os.listdir(act_dir):
            sub_p = os.path.join(act_dir, sub)
            if os.path.isdir(sub_p) and any(f.endswith('.brt1') for f in os.listdir(sub_p)):
                return True
    except OSError:
        return False
    return False


def list_driver_stems(telemetry_dir: str) -> list[str]:
    """List sorted unique driver_stems having primary telemetry (.primary.brtc, .active/_primary, or .csv)."""
    if not os.path.exists(telemetry_dir):
        return []
    stems: set[str] = set()
    try:
        entries = os.listdir(telemetry_dir)
    except OSError:
        return []
    for fname in entries:
        if fname.endswith(PRIMARY_BRTC_SUFFIX):
            stems.add(fname[:-len(PRIMARY_BRTC_SUFFIX)])
        elif fname.endswith(ACTIVE_DIR_SUFFIX):
            full_act = os.path.join(telemetry_dir, fname)
            if _active_dir_has_primary_segments(full_act):
                stems.add(fname[:-len(ACTIVE_DIR_SUFFIX)])
        elif fname.endswith('.csv'):
            stems.add(fname[:-4])
    return sorted(stems)


def has_telemetry_in_dir(meeting_dir: str, session_id: str | None = None) -> bool:
    """Return True if meeting_dir/telemetry contains primary telemetry (optionally matching session_id)."""
    telemetry_dir = os.path.join(meeting_dir, 'telemetry')
    stems = list_driver_stems(telemetry_dir)
    if not stems:
        return False
    if not session_id:
        return True
    clean_sid = strip_telemetry_suffix(session_id)
    for stem in stems:
        if stem == clean_sid or stem.startswith(f'{clean_sid}_'):
            return True
    return False


@contextlib.contextmanager
def compaction_lock(telemetry_dir: str, driver_stem: str) -> Iterator[None]:
    """Acquire an exclusive flock for compacting or mutating a driver_stem's files."""
    os.makedirs(telemetry_dir, exist_ok=True)
    lock_path = os.path.join(telemetry_dir, f'.{driver_stem}.compact.lock')
    fd = -1
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fd != -1:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# BRT1 Segment Framing & Reading
# ---------------------------------------------------------------------------

def pack_brt1_chunk(chunk_type: int, payload: bytes) -> bytes:
    """Frame a single BRT1 chunk: <length: uint16_le, chunk_type: uint8, payload>."""
    return struct.pack('<HB', len(payload), chunk_type) + payload


def create_active_segment_path(telemetry_dir: str, driver_stem: str, stream_name: str = '') -> str:
    """Create and return a chronological .brt1 segment path inside {driver_stem}.active/."""
    subdir = PRIMARY_SUBDIR if stream_name == '' else f'{AUX_SUBDIR_PREFIX}{stream_name}'
    target_dir = os.path.join(active_dir_path(telemetry_dir, driver_stem), subdir)
    os.makedirs(target_dir, exist_ok=True)
    ts_ns = time.time_ns()
    seg_name = f'{ts_ns:020d}_{uuid.uuid4().hex[:8]}.brt1'
    return os.path.join(target_dir, seg_name)


def default_brt1_scale_for_channel(col_name: str) -> float:
    """Choose high-precision default BRT1 quantization scale when transcoding legacy CSV streams."""
    if col_name == 'Time':
        return 0.0001
    if col_name in ('Latitude', 'Longitude'):
        return 1e-8
    if col_name in (
        'Ori Quat X', 'Ori Quat Y', 'Ori Quat Z', 'Ori Quat W',
        'q_x', 'q_y', 'q_z', 'q_w', 'Toe FL', 'Toe FR', 'toe_fl', 'toe_fr',
    ):
        return 1e-6
    if col_name == 'Speed':
        return 1e-5
    if col_name in ('Lap', 'Record'):
        return 1.0
    return 1e-4


class BRT1SegmentEncoder:
    """Streaming encoder that writes metadata + rows into a .brt1 segment file."""

    def __init__(
        self,
        segment_path: str,
        meta_dict: dict[str, Any],
        channel_names: list[str],
        channel_scales: list[float] | None = None,
    ) -> None:
        self.segment_path = segment_path
        self.channel_names = list(channel_names)
        self.scales = (
            [float(s) for s in channel_scales]
            if channel_scales is not None
            else [default_brt1_scale_for_channel(c) for c in self.channel_names]
        )
        self._m = len(self.channel_names)
        self._prev_values: list[float] | None = None
        self._delta_buf = bytearray()
        os.makedirs(os.path.dirname(os.path.abspath(segment_path)), exist_ok=True)
        self._file = open(segment_path, 'wb')
        self._write_header(meta_dict)

    def _write_header(self, meta_dict: dict[str, Any]) -> None:
        self._file.write(pack_brt1_chunk(0x01, b'BRT1'))
        channels_spec = [
            {'name': name, 'scale': scale}
            for name, scale in zip(self.channel_names, self.scales)
        ]
        meta_payload = json.dumps(
            {'metadata': meta_dict, 'channels': channels_spec},
            separators=(',', ':'),
            ensure_ascii=False,
        ).encode('utf-8')
        self._file.write(pack_brt1_chunk(0x02, meta_payload))
        self._file.flush()

    def _flush_delta_buf(self) -> None:
        if self._delta_buf:
            self._file.write(pack_brt1_chunk(0x04, bytes(self._delta_buf)))
            self._delta_buf.clear()

    def append_row(self, values: list[float]) -> None:
        """Append one sample row (length M), automatically emitting 0x03 keyframes when needed."""
        m = self._m
        if self._prev_values is None:
            self._file.write(pack_brt1_chunk(0x03, struct.pack(f'<{m}d', *values)))
            self._prev_values = list(values)
            return

        need_keyframe = False
        deltas: list[int] = []
        reconstructed: list[float] = []

        for j in range(m):
            c_val = values[j]
            p_val = self._prev_values[j]
            if math.isnan(c_val) or math.isinf(c_val):
                deltas.append(-32768)
                reconstructed.append(math.nan)
            elif math.isnan(p_val) or math.isinf(p_val):
                need_keyframe = True
                break
            else:
                q_diff = round((c_val - p_val) / self.scales[j])
                if q_diff < -32767 or q_diff > 32767:
                    need_keyframe = True
                    break
                deltas.append(int(q_diff))
                reconstructed.append(p_val + int(q_diff) * self.scales[j])

        if need_keyframe:
            self._flush_delta_buf()
            self._file.write(pack_brt1_chunk(0x03, struct.pack(f'<{m}d', *values)))
            self._prev_values = list(values)
        else:
            row_bytes = struct.pack(f'<{m}h', *deltas)
            if len(self._delta_buf) + len(row_bytes) > 60000:
                self._flush_delta_buf()
            self._delta_buf.extend(row_bytes)
            self._prev_values = reconstructed

    def flush(self) -> None:
        self._flush_delta_buf()
        self._file.flush()

    def close(self, write_eos: bool = True) -> None:
        if not self._file.closed:
            self._flush_delta_buf()
            if write_eos:
                self._file.write(pack_brt1_chunk(0xFF, b''))
            self._file.flush()
            self._file.close()


def _parse_segment_start_datetime(meta_raw: dict[str, Any]) -> datetime.datetime:
    """Parse segment start datetime preserving subsecond precision when present."""
    lower_map = {str(k).strip().lower(): str(v).strip() for k, v in meta_raw.items() if v is not None}
    date_str = lower_map.get('date', '')
    time_str = lower_map.get('time', '')
    track_name = lower_map.get('track name', '')
    if 'T' in date_str or (len(date_str) > 10 and ' ' in date_str):
        combined = date_str
    else:
        combined = f'{date_str} {time_str}'.strip()

    if not combined:
        return datetime.datetime(1970, 1, 1)

    # Preserve fractional seconds if provided in ISO / HH:MM:SS.fff
    subsec_us = 0
    m_frac = re.search(r':\d{2}\.(\d+)', combined)
    if m_frac:
        frac_digits = (m_frac.group(1) + '000000')[:6]
        subsec_us = int(frac_digits)

    try:
        dt_base, _ = session_time.parse_session_start_datetime(combined, track_name=track_name, db=db)
        return dt_base.replace(microsecond=subsec_us)
    except Exception:
        try:
            dt_tol = session_time.parse_session_start_datetime_tolerant(combined)
            return dt_tol
        except Exception:
            return datetime.datetime(1970, 1, 1)


def read_brt1_segment(
    segment_path: str,
) -> tuple[dict[str, Any], list[str], dict[str, float], dict[str, np.ndarray], datetime.datetime]:
    """Decode a .brt1 segment file into (raw_meta, channel_names, scales_map, columns_dict, start_dt).

    Tolerates a partially written trailing chunk so active mid-stream segments can be read safely
    without locks.
    """
    with open(segment_path, 'rb') as f:
        data = f.read()

    pos = 0
    n_bytes = len(data)

    def _next_chunk() -> tuple[int, bytes] | None:
        nonlocal pos
        if pos + 3 > n_bytes:
            return None
        c_len, c_type = struct.unpack_from('<HB', data, pos)
        if pos + 3 + c_len > n_bytes:
            return None
        payload = data[pos + 3:pos + 3 + c_len]
        pos += 3 + c_len
        return c_type, payload

    magic = _next_chunk()
    if magic is None or magic[0] != 0x01 or magic[1] != b'BRT1':
        raise ValueError(f'Invalid BRT1 header in segment {segment_path}')
    meta_chunk = _next_chunk()
    if meta_chunk is None or meta_chunk[0] != 0x02:
        raise ValueError(f'Missing BRT1 metadata chunk in segment {segment_path}')

    meta_json = json.loads(meta_chunk[1].decode('utf-8'))
    raw_meta: dict[str, Any] = dict(meta_json.get('metadata') or {})
    channels_list = meta_json.get('channels') or []
    channel_names = [str(c['name']) for c in channels_list]
    scales = [float(c['scale']) for c in channels_list]
    scales_map = {name: sc for name, sc in zip(channel_names, scales)}
    m = len(channel_names)
    time_idx = channel_names.index('Time') if 'Time' in channel_names else None
    start_dt = _parse_segment_start_datetime(raw_meta)

    if m == 0:
        return raw_meta, [], {}, {}, start_dt

    rows: list[list[float]] = []
    current_vals = [math.nan] * m

    while (chunk := _next_chunk()) is not None:
        c_type, payload = chunk
        if c_type == 0x03:
            if len(payload) != m * 8:
                break
            current_vals = list(struct.unpack(f'<{m}d', payload))
            if time_idx is None or not math.isnan(current_vals[time_idx]):
                rows.append(list(current_vals))
        elif c_type == 0x04:
            row_sz = m * 2
            if row_sz == 0 or len(payload) % row_sz != 0:
                break
            for deltas in struct.iter_unpack(f'<{m}h', payload):
                for j in range(m):
                    d_val = deltas[j]
                    if d_val == -32768:
                        current_vals[j] = math.nan
                    else:
                        current_vals[j] += d_val * scales[j]
                if time_idx is None or not math.isnan(current_vals[time_idx]):
                    rows.append(list(current_vals))
        elif c_type == 0xFF:
            break

    if not rows:
        empty_cols = {name: np.zeros(0, dtype=np.float64) for name in channel_names}
        return raw_meta, channel_names, scales_map, empty_cols, start_dt

    mat = np.array(rows, dtype=np.float64)
    cols_dict = {name: np.ascontiguousarray(mat[:, j]) for j, name in enumerate(channel_names)}
    return raw_meta, channel_names, scales_map, cols_dict, start_dt


# ---------------------------------------------------------------------------
# Legacy CSV Parsing (for fallback reading & archive migration)
# ---------------------------------------------------------------------------

def _parse_iso_timestamp_to_midnight_sec(
    ts_str: str,
    base_midnight: datetime.datetime | None,
) -> tuple[float, datetime.date | None]:
    s = ts_str.strip()
    if not s:
        return math.nan, None
    if 'T' in s:
        date_part, time_part = s.split('T', 1)
        time_clean = time_part.rstrip('Z')
        if '+' in time_clean:
            time_clean = time_clean.split('+', 1)[0]
        parts = time_clean.split(':')
        if len(parts) == 3:
            try:
                d_obj = datetime.date.fromisoformat(date_part[:10])
                sec_of_day = int(parts[0]) * 3600.0 + int(parts[1]) * 60.0 + float(parts[2])
                if base_midnight is not None:
                    day_diff = (d_obj - base_midnight.date()).days
                    return day_diff * 86400.0 + sec_of_day, d_obj
                return sec_of_day, d_obj
            except ValueError:
                return math.nan, None
    try:
        return float(s), None
    except ValueError:
        return math.nan, None


def read_legacy_csv_raw_columns(
    csv_path: str,
    strip_derived: bool = True,
) -> tuple[dict[str, Any], list[str], dict[str, np.ndarray], datetime.date]:
    """Read a RaceTools CSV into (normalized_metadata, column_order, raw_columns, start_date)."""
    with open(csv_path, 'r', encoding='utf-8') as f:
        rows = list(csv.reader(f))

    blank_idx = -1
    for i, r in enumerate(rows):
        if not r or all(cell.strip() == '' for cell in r):
            blank_idx = i
            break

    if not rows:
        return {}, [], {}, datetime.date(1970, 1, 1)

    if blank_idx == -1:
        meta_rows = rows
        data_section: list[list[str]] = []
    else:
        meta_rows = rows[:blank_idx]
        data_section = rows[blank_idx + 1:]

    raw_meta_kv: dict[str, str] = {}
    for mr in meta_rows:
        if len(mr) >= 2 and mr[0].strip():
            raw_meta_kv[mr[0].strip()] = mr[1].strip()

    start_dt = _parse_segment_start_datetime(raw_meta_kv)
    has_valid_meta_date = (start_dt.year > 1970)

    if not data_section:
        if not raw_meta_kv:
            return {}, [], {}, datetime.date(1970, 1, 1)
        norm_meta = _normalize_metadata_dict(raw_meta_kv, start_dt)
        final_empty_date = start_dt.date() if has_valid_meta_date else datetime.date(1970, 1, 1)
        return norm_meta, ['Time'], {'Time': np.zeros(0, dtype=np.float64)}, final_empty_date

    header = [c.strip() for c in data_section[0]]
    data_rows = data_section[1:]

    n_cols = len(header)
    time_idx = header.index('Time') if 'Time' in header else -1

    valid_rows: list[list[str]] = []
    first_row_date: datetime.date | None = None
    row_base_midnight: datetime.datetime | None = None

    for r in data_rows:
        if not r or len(r) < n_cols:
            continue
        if time_idx >= 0 and not r[time_idx].strip():
            continue
        if first_row_date is None and time_idx >= 0:
            _, d_cand = _parse_iso_timestamp_to_midnight_sec(r[time_idx], None)
            if d_cand is not None:
                first_row_date = d_cand
                row_base_midnight = datetime.datetime(d_cand.year, d_cand.month, d_cand.day)
        valid_rows.append(r)

    if has_valid_meta_date:
        final_date = start_dt.date()
    elif first_row_date is not None:
        final_date = first_row_date
        start_dt = datetime.datetime(first_row_date.year, first_row_date.month, first_row_date.day)
    else:
        final_date = datetime.date(1970, 1, 1)

    n_rows = len(valid_rows)
    cols: dict[str, np.ndarray] = {}
    for c_idx, col_name in enumerate(header):
        if not col_name:
            continue
        arr = np.full(n_rows, np.nan, dtype=np.float64)
        if col_name == 'Time':
            for r_i, r in enumerate(valid_rows):
                t_val, _ = _parse_iso_timestamp_to_midnight_sec(r[c_idx], row_base_midnight)
                arr[r_i] = t_val
        else:
            for r_i, r in enumerate(valid_rows):
                cell = r[c_idx].strip()
                if cell and cell.lower() not in ('nan', 'none', 'null'):
                    try:
                        arr[r_i] = float(cell)
                    except ValueError:
                        pass
        cols[col_name] = arr

    norm_meta = _normalize_metadata_dict(raw_meta_kv, start_dt)

    if strip_derived:
        cols, col_order = _strip_baked_derived_columns(cols, header, norm_meta)
    else:
        col_order = [h for h in header if h in cols]

    return norm_meta, col_order, cols, final_date


def _strip_baked_derived_columns(
    cols: dict[str, np.ndarray],
    header: list[str],
    norm_meta: dict[str, Any],
) -> tuple[dict[str, np.ndarray], list[str]]:
    """Strip derived columns that were previously baked into RaceTools CSVs on disk."""
    has_quat = any(k in cols for k in ('Ori Quat X', 'q_x', 'Ori Quat W'))
    has_gyro = 'Yaw Rate' in cols and bool(np.any(np.isfinite(cols['Yaw Rate'])))
    has_dist_rear = norm_meta.get('distance_to_rear_axle') is not None
    has_xz = 'x' in cols and 'z' in cols and bool(np.any(np.isfinite(cols['x'])))

    has_wheel_forces = False
    for wf in ('Lat Force FL', 'Lat Force FR'):
        if wf in cols and bool(np.any(np.isfinite(cols[wf]) & (np.abs(cols[wf]) > 1e-4))):
            has_wheel_forces = True
            break

    to_drop: set[str] = {'Record'}
    # Note: Keep raw 'Lap' and 'Lap Distance' only if track has no centerline; wait:
    # keeping 'Lap' and 'Lap Distance' in primary_cols is completely harmless when
    # track_lap_indexing overwrites 'Lap' and 'Lap Distance' in derived_cols, AND
    # serves as the fallback if track centerline is not in DB!
    # However, if we re-upload / merge, 'Kart Heading', 'Yaw', 'Wheel Angle *', 'Wheel Heading *',
    # 'Slip Angle *', and IMU-derived 'Lat/Long Force Front/Rear' are pure derived outputs.
    if has_xz:
        resolved_info = _resolve_track_info_from_metadata(norm_meta)
        resolved_track = str((resolved_info or {}).get('track_name') or norm_meta.get('track_name') or '')
        if telemetry_calculators.resolve_simulator_origin(norm_meta, resolved_track) is not None:
            to_drop.update({'Latitude', 'Longitude'})
    if has_quat:
        to_drop.update({
            'Kart Heading', 'Yaw',
            'Wheel Angle FL', 'Wheel Angle FR',
            'Wheel Heading FL', 'Wheel Heading FR',
            'Slip Angle Front', 'Slip Angle Rear',
        })
    elif has_gyro and has_dist_rear:
        to_drop.update({'Kart Heading', 'Yaw', 'Slip Angle Rear'})

    if not has_quat and has_gyro and not has_wheel_forces:
        to_drop.update({'Lat Force Front', 'Lat Force Rear', 'Long Force Front', 'Long Force Rear'})

    filtered = {k: v for k, v in cols.items() if k not in to_drop}
    order = [h for h in header if h in filtered]
    return filtered, order


def _normalize_metadata_dict(raw_meta: dict[str, Any], start_dt: datetime.datetime) -> dict[str, Any]:
    """Normalize arbitrary CSV/BRT1 metadata keys into canonical .brtc manifest metadata."""
    lower = {str(k).strip().lower(): v for k, v in raw_meta.items() if v is not None and str(v).strip() != ''}
    driver_name = str(lower.get('driver name') or lower.get('configuration') or lower.get('driver_name') or '')
    track_name = str(lower.get('track name') or lower.get('track_name') or '')
    league = str(lower.get('league') or '')
    class_name = str(lower.get('class') or lower.get('class_name') or '')
    session_name = str(lower.get('session') or lower.get('session_name') or '')
    track_cfg = lower.get('track configuration') if 'track configuration' in lower else lower.get('track_configuration')

    dist_rear: float | None = None
    dist_raw = (
        lower.get('distance to rear axle')
        if 'distance to rear axle' in lower
        else lower.get('distance_to_rear_axle')
    )
    if dist_raw is not None:
        try:
            dist_rear = float(dist_raw)
        except (ValueError, TypeError):
            dist_rear = None

    def _opt_float(*keys: str) -> float | None:
        for k in keys:
            if k in lower and lower[k] is not None:
                try:
                    return float(lower[k])
                except (ValueError, TypeError):
                    pass
        return None

    origin_lat = _opt_float('track origin latitude', 'track_origin_lat')
    origin_lon = _opt_float('track origin longitude', 'track_origin_lon')
    origin_heading = _opt_float('track origin heading', 'track_origin_heading')

    out: dict[str, Any] = {
        'driver_name': driver_name,
        'track_name': track_name,
        'league': league,
        'class_name': class_name,
        'session_name': session_name,
        'session_start_date': start_dt.strftime('%Y-%m-%d'),
        'session_start_time': start_dt.strftime('%H:%M:%S'),
    }
    if track_cfg:
        out['track_configuration'] = str(track_cfg)
    if dist_rear is not None:
        out['distance_to_rear_axle'] = dist_rear
    if origin_lat is not None and origin_lon is not None:
        out['track_origin_lat'] = origin_lat
        out['track_origin_lon'] = origin_lon
        out['track_origin_heading'] = origin_heading if origin_heading is not None else 0.0
    for extra_key in ('kart_number', 'conditions', 'track_conditions', 'temperature', 'weather', 'session_id'):
        if extra_key in lower:
            out[extra_key] = str(lower[extra_key])
    if 'stream' in lower and str(lower['stream']).strip():
        out['stream'] = str(lower['stream']).strip().lower()
    return out


# ---------------------------------------------------------------------------
# Chronological Segment Merging & GPS Bounds Filtering
# ---------------------------------------------------------------------------

def _append_or_prune_columns(
    acc_cols: dict[str, np.ndarray],
    acc_order: list[str],
    new_cols: dict[str, np.ndarray],
    new_order: list[str],
) -> tuple[dict[str, np.ndarray], list[str]]:
    """Merge new_cols onto acc_cols, pruning any acc rows with Time >= min(new_cols['Time'])."""
    new_time = new_cols.get('Time')
    if new_time is None or len(new_time) == 0:
        return acc_cols, acc_order

    valid_new_t = new_time[np.isfinite(new_time)]
    if valid_new_t.size == 0:
        return acc_cols, acc_order

    if not acc_cols or 'Time' not in acc_cols or len(acc_cols['Time']) == 0:
        copied = {k: v.copy() for k, v in new_cols.items()}
        order = [k for k in new_order if k in copied]
        for k in copied:
            if k not in order:
                order.append(k)
        return copied, order

    min_new_t = float(np.min(valid_new_t))
    acc_time = acc_cols['Time']
    keep_acc = np.isfinite(acc_time) & (acc_time < min_new_t - 1e-6)
    n_keep = int(np.sum(keep_acc))
    n_new = len(new_time)

    merged_order = list(acc_order)
    for k in new_order:
        if k not in merged_order:
            merged_order.append(k)
    for k in new_cols:
        if k not in merged_order:
            merged_order.append(k)

    out_cols: dict[str, np.ndarray] = {}
    for col in merged_order:
        part_a = (
            acc_cols[col][keep_acc]
            if col in acc_cols
            else np.full(n_keep, np.nan, dtype=np.float64)
        )
        part_b = (
            new_cols[col]
            if col in new_cols
            else np.full(n_new, np.nan, dtype=np.float64)
        )
        out_cols[col] = np.concatenate([part_a, part_b])

    return out_cols, merged_order


def _filter_primary_rows_by_gps_and_time(
    cols: dict[str, np.ndarray],
    metadata: dict[str, Any],
    track_info: dict[str, Any] | None,
) -> dict[str, np.ndarray]:
    """Filter primary stream rows for valid Time and in-bounds GPS coordinates (matching C++/CSV readers)."""
    time_arr = cols.get('Time')
    if time_arr is None or len(time_arr) == 0:
        return cols

    keep = np.isfinite(time_arr)

    lat_arr: np.ndarray | None = None
    lon_arr: np.ndarray | None = None

    if 'Latitude' in cols and 'Longitude' in cols:
        raw_lat = cols['Latitude']
        raw_lon = cols['Longitude']
        lat_arr = np.where(np.isfinite(raw_lat) & (raw_lat != 0.0), raw_lat, np.nan)
        lon_arr = np.where(np.isfinite(raw_lon) & (raw_lon != 0.0), raw_lon, np.nan)

    if (lat_arr is None or not np.any(np.isfinite(lat_arr))) and ('x' in cols and 'z' in cols):
        resolved_track = str((track_info or {}).get('track_name') or metadata.get('track_name') or '')
        try:
            origin = telemetry_calculators.resolve_simulator_origin(metadata, resolved_track)
        except ValueError:
            origin = None
        if origin is not None:
            lat_arr, lon_arr = telemetry_calculators.compute_gps_from_local_xz(cols['x'], cols['z'], origin)

    if lat_arr is not None and lon_arr is not None:
        # Compute reference coordinates (track centerline / origin / median of valid coords)
        ref_lat: float | None = None
        ref_lon: float | None = None
        max_dist_m = 5000.0
        if track_info:
            cl = track_info.get('center_line')
            if cl and isinstance(cl, list):
                cl_lats = [float(p['lat']) for p in cl if isinstance(p, dict) and 'lat' in p]
                cl_lons = [float(p['lon']) for p in cl if isinstance(p, dict) and 'lon' in p]
                if cl_lats and cl_lons:
                    ref_lat = float(np.mean(cl_lats))
                    ref_lon = float(np.mean(cl_lons))
            if ref_lat is None:
                orig = track_info.get('origin')
                if orig and isinstance(orig, dict) and 'lat' in orig and 'lon' in orig:
                    ref_lat = float(orig['lat'])
                    ref_lon = float(orig['lon'])

        valid_coord = (
            np.isfinite(lat_arr)
            & np.isfinite(lon_arr)
            & ~((lat_arr == 0.0) & (lon_arr == 0.0))
            & (lat_arr >= -90.0) & (lat_arr <= 90.0)
            & (lon_arr >= -180.0) & (lon_arr <= 180.0)
        )
        if ref_lat is None and np.any(valid_coord):
            ref_lat = float(np.median(lat_arr[valid_coord]))
            ref_lon = float(np.median(lon_arr[valid_coord]))
            max_dist_m = 10000.0

        # Apply coordinate filter whenever coordinates exist or track reference is known
        if ref_lat is not None and ref_lon is not None and np.any(valid_coord):
            dlat_m = (lat_arr - ref_lat) * 111000.0
            dlon_m = (lon_arr - ref_lon) * 111000.0 * math.cos(math.radians(ref_lat))
            in_bounds = valid_coord & ((dlat_m * dlat_m + dlon_m * dlon_m) <= (max_dist_m * max_dist_m))
            keep = keep & in_bounds
        else:
            keep = keep & valid_coord

    if bool(np.all(keep)):
        return cols
    return {k: np.ascontiguousarray(v[keep]) for k, v in cols.items()}


def _resolve_track_info_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    track_name = str(metadata.get('track_name') or '')
    track_cfg = metadata.get('track_configuration')
    if not track_name:
        return {}
    if track_cfg:
        info = db.get_track(f'{track_name} {track_cfg}')
        if info:
            return info
    return db.get_track(track_name) or {}


def _merge_stream_data(
    existing_brtc: str | None,
    existing_csv: str | None,
    segment_paths: list[str],
) -> tuple[dict[str, Any], list[str], dict[str, np.ndarray], dict[str, float]]:
    """Merge pre-existing .brtc (or .csv) with chronological .brt1 segments."""
    acc_meta: dict[str, Any] = {}
    acc_order: list[str] = []
    acc_cols: dict[str, np.ndarray] = {}
    pref_scales: dict[str, float] = {}
    base_date: datetime.date | None = None

    if existing_brtc and os.path.exists(existing_brtc):
        manifest = telemetry_brtc.read_brtc_manifest(existing_brtc)
        acc_meta = dict(manifest.get('metadata') or {})
        acc_order = list(manifest.get('column_order') or [])
        acc_cols = telemetry_brtc.read_brtc_columns(existing_brtc, manifest=manifest)
        d_str = str(acc_meta.get('session_start_date') or '')
        if d_str:
            try:
                base_date = datetime.date.fromisoformat(d_str[:10])
            except ValueError:
                base_date = None
    elif existing_csv and os.path.exists(existing_csv):
        csv_meta, csv_order, csv_cols, csv_date = read_legacy_csv_raw_columns(existing_csv, strip_derived=True)
        acc_meta = csv_meta
        acc_order = csv_order
        acc_cols = csv_cols
        if csv_date.year > 1970:
            base_date = csv_date

    for seg_path in segment_paths:
        try:
            raw_meta, ch_names, sc_map, seg_cols, start_dt = read_brt1_segment(seg_path)
        except Exception as e:
            logger.warning('Skipping unreadable BRT1 segment %s: %s', seg_path, e)
            continue

        norm_seg_meta = _normalize_metadata_dict(raw_meta, start_dt)
        if not acc_meta:
            acc_meta = norm_seg_meta
        else:
            for k, v in norm_seg_meta.items():
                if v not in (None, '') and (k not in acc_meta or k not in ('session_start_date', 'session_start_time')):
                    acc_meta[k] = v

        for ch_name, sc in sc_map.items():
            if ch_name not in pref_scales or sc < pref_scales[ch_name]:
                pref_scales[ch_name] = sc

        if base_date is None and start_dt.year > 1970:
            base_date = start_dt.date()
            acc_meta['session_start_date'] = base_date.isoformat()

        if 'Time' in seg_cols and len(seg_cols['Time']) > 0:
            ref_midnight = (
                datetime.datetime(base_date.year, base_date.month, base_date.day)
                if base_date is not None
                else start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            )
            seg_offset_s = (start_dt - ref_midnight).total_seconds()
            seg_cols = dict(seg_cols)
            seg_cols['Time'] = seg_cols['Time'] + seg_offset_s

        acc_cols, acc_order = _append_or_prune_columns(acc_cols, acc_order, seg_cols, ch_names)

    return acc_meta, acc_order, acc_cols, pref_scales


# ---------------------------------------------------------------------------
# Compaction & Ephemeral On-Demand Reader
# ---------------------------------------------------------------------------

def compact_driver_session(
    telemetry_dir: str,
    driver_stem: str,
    track_info: dict[str, Any] | None = None,
    delete_legacy_csv: bool = True,
) -> bool:
    """Compact all .active/ segments (and/or legacy .csv) for driver_stem into .brtc files.

    Returns True if any .brtc file was created or updated.
    """
    with compaction_lock(telemetry_dir, driver_stem):
        act_dir = active_dir_path(telemetry_dir, driver_stem)
        prim_brtc = primary_brtc_path(telemetry_dir, driver_stem)
        deriv_brtc = derived_brtc_path(telemetry_dir, driver_stem)
        csv_p = legacy_csv_path(telemetry_dir, driver_stem)

        # Snapshot segments with (path, size, mtime_ns) so concurrently arriving segments stay intact
        prim_segs: list[tuple[str, int, int]] = []
        aux_segs: dict[str, list[tuple[str, int, int]]] = {}

        if os.path.isdir(act_dir):
            for sub in sorted(os.listdir(act_dir)):
                sub_dir = os.path.join(act_dir, sub)
                if not os.path.isdir(sub_dir):
                    continue
                seg_files: list[tuple[str, int, int]] = []
                for fn in sorted(os.listdir(sub_dir)):
                    if fn.endswith('.brt1'):
                        fp = os.path.join(sub_dir, fn)
                        try:
                            st = os.stat(fp)
                            seg_files.append((fp, st.st_size, st.st_mtime_ns))
                        except OSError:
                            pass
                if not seg_files:
                    continue
                if sub == PRIMARY_SUBDIR:
                    prim_segs = seg_files
                elif sub.startswith(AUX_SUBDIR_PREFIX):
                    s_name = sub[len(AUX_SUBDIR_PREFIX):]
                    if s_name:
                        aux_segs[s_name] = seg_files

        has_csv = os.path.exists(csv_p)
        did_work = False

        # 1. Compact secondary (auxiliary) streams first so derived alignment sees them immediately
        for s_name, seg_tuples in sorted(aux_segs.items()):
            aux_out = aux_brtc_path(telemetry_dir, driver_stem, s_name)
            a_meta, a_order, a_cols, a_scales = _merge_stream_data(
                existing_brtc=aux_out if os.path.exists(aux_out) else None,
                existing_csv=None,
                segment_paths=[t[0] for t in seg_tuples],
            )
            if a_cols and 'Time' in a_cols and len(a_cols['Time']) > 0:
                valid_t = np.isfinite(a_cols['Time'])
                a_cols_clean = {k: np.ascontiguousarray(v[valid_t]) for k, v in a_cols.items()}
                telemetry_brtc.write_brtc(
                    aux_out,
                    columns=a_cols_clean,
                    metadata=a_meta,
                    column_order=a_order,
                    preferred_scales=a_scales,
                )
                did_work = True
            _cleanup_consumed_segments(seg_tuples)

        # 2. Compact primary stream (or migrate legacy .csv if no .primary.brtc exists yet)
        need_primary = bool(prim_segs) or (has_csv and not os.path.exists(prim_brtc))
        if need_primary:
            p_meta, p_order, p_cols, p_scales = _merge_stream_data(
                existing_brtc=prim_brtc if os.path.exists(prim_brtc) else None,
                existing_csv=csv_p if has_csv else None,
                segment_paths=[t[0] for t in prim_segs],
            )
            can_write_empty_csv = has_csv and not prim_segs and bool(p_meta)
            if p_cols and 'Time' in p_cols and (len(p_cols['Time']) > 0 or can_write_empty_csv):
                resolved_track_info = (
                    track_info if track_info is not None else _resolve_track_info_from_metadata(p_meta)
                )
                filtered_p_cols = _filter_primary_rows_by_gps_and_time(p_cols, p_meta, resolved_track_info)
                telemetry_brtc.write_brtc(
                    prim_brtc,
                    columns=filtered_p_cols,
                    metadata=p_meta,
                    column_order=p_order,
                    preferred_scales=p_scales,
                )
                did_work = True
            _cleanup_consumed_segments(prim_segs)

        # 3. Clean up empty .active subdirectories / directory & legacy CSV
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

        if delete_legacy_csv and has_csv and os.path.exists(prim_brtc):
            try:
                os.remove(csv_p)
                did_work = True
            except OSError as e:
                logger.warning('Failed to remove compacted legacy CSV %s: %s', csv_p, e)

        # 4. Build or refresh .derived.brtc whenever .primary.brtc exists
        if os.path.exists(prim_brtc):
            p_manifest = telemetry_brtc.read_brtc_manifest(prim_brtc)
            p_meta = dict(p_manifest.get('metadata') or {})
            resolved_track_info = (
                track_info if track_info is not None else _resolve_track_info_from_metadata(p_meta)
            )
            aux_map = list_aux_brtc_files(telemetry_dir, driver_stem)
            need_derived = did_work or not os.path.exists(deriv_brtc)
            if not need_derived:
                try:
                    d_manifest = telemetry_brtc.read_brtc_manifest(deriv_brtc)
                    need_derived = not telemetry_calculators.is_derived_manifest_fresh(
                        d_manifest,
                        int(p_manifest.get('row_count', 0)),
                        p_meta,
                        resolved_track_info,
                        aux_map,
                    )
                except Exception:
                    need_derived = True

            if need_derived:
                _write_derived_from_primary(prim_brtc, deriv_brtc, p_manifest, resolved_track_info, aux_map)
                did_work = True

        _clear_ephemeral_cache(telemetry_dir, driver_stem)
        return did_work


def recompute_driver_derived(
    telemetry_dir: str,
    driver_stem: str,
    force: bool = False,
    calculator_name: str | None = None,
    track_info: dict[str, Any] | None = None,
) -> bool:
    """Recompute {driver_stem}.derived.brtc from {driver_stem}.primary.brtc + .aux.*.brtc if stale or forced."""
    with compaction_lock(telemetry_dir, driver_stem):
        prim_brtc = primary_brtc_path(telemetry_dir, driver_stem)
        deriv_brtc = derived_brtc_path(telemetry_dir, driver_stem)
        if not os.path.exists(prim_brtc):
            return False

        p_manifest = telemetry_brtc.read_brtc_manifest(prim_brtc)
        p_meta = dict(p_manifest.get('metadata') or {})
        resolved_track = track_info if track_info is not None else _resolve_track_info_from_metadata(p_meta)
        aux_map = list_aux_brtc_files(telemetry_dir, driver_stem)

        should_run = force or not os.path.exists(deriv_brtc)
        if not should_run:
            try:
                d_manifest = telemetry_brtc.read_brtc_manifest(deriv_brtc)
                if calculator_name:
                    expected = telemetry_calculators.expected_calculator_provenance(
                        p_meta, resolved_track, aux_map
                    )
                    recorded = d_manifest.get('calculators') or {}
                    should_run = (recorded.get(calculator_name) != expected.get(calculator_name))
                else:
                    should_run = not telemetry_calculators.is_derived_manifest_fresh(
                        d_manifest, int(p_manifest.get('row_count', 0)), p_meta, resolved_track, aux_map
                    )
            except Exception:
                should_run = True

        if not should_run:
            return False

        _write_derived_from_primary(prim_brtc, deriv_brtc, p_manifest, resolved_track, aux_map)
        return True


def _cleanup_consumed_segments(seg_tuples: list[tuple[str, int, int]]) -> None:
    for fp, orig_size, orig_mtime_ns in seg_tuples:
        try:
            st = os.stat(fp)
            if st.st_size == orig_size and st.st_mtime_ns == orig_mtime_ns:
                os.remove(fp)
        except OSError:
            pass


def _write_derived_from_primary(
    prim_brtc: str,
    deriv_brtc: str,
    prim_manifest: dict[str, Any],
    track_info: dict[str, Any],
    aux_map: dict[str, str],
) -> dict[str, Any]:
    prim_cols = telemetry_brtc.read_brtc_columns(prim_brtc, manifest=prim_manifest)
    prim_meta = dict(prim_manifest.get('metadata') or {})
    d_cols, d_order, laps, provenance = telemetry_calculators.compute_derived_columns_and_laps(
        primary_cols=prim_cols,
        metadata=prim_meta,
        track_info=track_info,
        aux_brtc_paths=aux_map,
    )
    return telemetry_brtc.write_brtc(
        deriv_brtc,
        columns=d_cols,
        metadata=prim_meta,
        column_order=d_order,
        laps=laps,
        calculators=provenance,
    )


def _ephemeral_dir_for(telemetry_dir: str, driver_stem: str) -> str:
    real_dir = os.path.realpath(telemetry_dir)
    h = hashlib.sha256(f'{real_dir}/{driver_stem}'.encode('utf-8')).hexdigest()[:16]
    return os.path.join(EPHEMERAL_ROOT, f'{h}_{driver_stem}')


def _clear_ephemeral_cache(telemetry_dir: str, driver_stem: str) -> None:
    eph_dir = _ephemeral_dir_for(telemetry_dir, driver_stem)
    if os.path.isdir(eph_dir):
        shutil.rmtree(eph_dir, ignore_errors=True)


def ensure_readable_brtc(
    telemetry_dir: str,
    driver_stem: str,
    track_info: dict[str, Any] | None = None,
) -> tuple[str, str] | None:
    """Return (primary_brtc_path, derived_brtc_path) for reading/serving, or None if only legacy CSV exists.

    - If session is compacted (no .active/ segments) and .primary.brtc exists: lazily refreshes
      .derived.brtc if stale and returns the on-disk paths.
    - If session has live/uncompacted .active/ segments: builds or reuses an up-to-date ephemeral
      .brtc pair in /tmp/brbr_ephemeral/ with zero intermediate CSV.
    """
    if not os.path.exists(telemetry_dir):
        return None
    try:
        dir_entries = set(os.listdir(telemetry_dir))
    except OSError:
        return None

    act_name = f'{driver_stem}{ACTIVE_DIR_SUFFIX}'
    prim_name = f'{driver_stem}{PRIMARY_BRTC_SUFFIX}'
    deriv_name = f'{driver_stem}{DERIVED_BRTC_SUFFIX}'
    csv_name = f'{driver_stem}.csv'

    act_dir = os.path.join(telemetry_dir, act_name)
    prim_on_disk = os.path.join(telemetry_dir, prim_name)
    deriv_on_disk = os.path.join(telemetry_dir, deriv_name)
    csv_on_disk = os.path.join(telemetry_dir, csv_name)

    has_active = (act_name in dir_entries) and _active_dir_has_any_segments(act_dir)
    if not has_active:
        if prim_name not in dir_entries:
            return None
        p_manifest = telemetry_brtc.read_brtc_manifest(prim_on_disk)
        p_meta = dict(p_manifest.get('metadata') or {})
        resolved_track = track_info if track_info is not None else _resolve_track_info_from_metadata(p_meta)
        aux_map = list_aux_brtc_files(telemetry_dir, driver_stem)
        fresh = False
        if deriv_name in dir_entries and os.path.exists(deriv_on_disk):
            try:
                d_manifest = telemetry_brtc.read_brtc_manifest(deriv_on_disk)
                fresh = telemetry_calculators.is_derived_manifest_fresh(
                    d_manifest, int(p_manifest.get('row_count', 0)), p_meta, resolved_track, aux_map
                )
            except Exception:
                fresh = False
        if not fresh:
            with compaction_lock(telemetry_dir, driver_stem):
                _write_derived_from_primary(prim_on_disk, deriv_on_disk, p_manifest, resolved_track, aux_map)
        return prim_on_disk, deriv_on_disk

    # Active uncompacted segments exist -> build or reuse ephemeral .brtc in /tmp
    prim_seg_paths: list[str] = []
    aux_seg_paths: dict[str, list[str]] = {}
    stat_fingerprint: list[Any] = []

    for check_p in [prim_on_disk, csv_on_disk, *sorted(list_aux_brtc_files(telemetry_dir, driver_stem).values())]:
        if os.path.exists(check_p):
            try:
                st = os.stat(check_p)
                stat_fingerprint.append([check_p, st.st_size, st.st_mtime_ns])
            except OSError:
                pass

    if os.path.isdir(act_dir):
        for sub in sorted(os.listdir(act_dir)):
            sub_dir = os.path.join(act_dir, sub)
            if not os.path.isdir(sub_dir):
                continue
            s_list: list[str] = []
            for fn in sorted(os.listdir(sub_dir)):
                if fn.endswith('.brt1'):
                    fp = os.path.join(sub_dir, fn)
                    try:
                        st = os.stat(fp)
                        stat_fingerprint.append([fp, st.st_size, st.st_mtime_ns])
                        s_list.append(fp)
                    except OSError:
                        pass
            if not s_list:
                continue
            if sub == PRIMARY_SUBDIR:
                prim_seg_paths = s_list
            elif sub.startswith(AUX_SUBDIR_PREFIX):
                s_name = sub[len(AUX_SUBDIR_PREFIX):]
                if s_name:
                    aux_seg_paths[s_name] = s_list

    if not prim_seg_paths and not os.path.exists(prim_on_disk) and not os.path.exists(csv_on_disk):
        return None

    eph_dir = _ephemeral_dir_for(telemetry_dir, driver_stem)
    os.makedirs(eph_dir, exist_ok=True)
    eph_prim = os.path.join(eph_dir, f'{driver_stem}{PRIMARY_BRTC_SUFFIX}')
    eph_deriv = os.path.join(eph_dir, f'{driver_stem}{DERIVED_BRTC_SUFFIX}')
    state_file = os.path.join(eph_dir, 'state.json')

    fp_hash = hashlib.sha256(
        json.dumps(stat_fingerprint, separators=(',', ':')).encode('utf-8')
    ).hexdigest()[:24]

    if os.path.exists(state_file) and os.path.exists(eph_prim) and os.path.exists(eph_deriv):
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                st_data = json.load(f)
            if st_data.get('fp_hash') == fp_hash:
                p_man = telemetry_brtc.read_brtc_manifest(eph_prim)
                d_man = telemetry_brtc.read_brtc_manifest(eph_deriv)
                p_meta_cached = dict(p_man.get('metadata') or {})
                res_track = (
                    track_info if track_info is not None else _resolve_track_info_from_metadata(p_meta_cached)
                )
                eph_aux_map = list_aux_brtc_files(eph_dir, driver_stem)
                if telemetry_calculators.is_derived_manifest_fresh(
                    d_man, int(p_man.get('row_count', 0)), p_meta_cached, res_track, eph_aux_map
                ):
                    return eph_prim, eph_deriv
        except Exception:
            pass

    # Rebuild ephemeral .brtc files
    disk_aux_map = list_aux_brtc_files(telemetry_dir, driver_stem)
    eph_aux_paths: dict[str, str] = {}
    all_aux_names = set(disk_aux_map.keys()) | set(aux_seg_paths.keys())
    for s_name in sorted(all_aux_names):
        segs = aux_seg_paths.get(s_name, [])
        disk_aux = disk_aux_map.get(s_name)
        if not segs and disk_aux:
            eph_aux_paths[s_name] = disk_aux
            continue
        eph_aux_out = aux_brtc_path(eph_dir, driver_stem, s_name)
        a_meta, a_order, a_cols, a_scales = _merge_stream_data(disk_aux, None, segs)
        if a_cols and 'Time' in a_cols and len(a_cols['Time']) > 0:
            valid_t = np.isfinite(a_cols['Time'])
            a_clean = {k: np.ascontiguousarray(v[valid_t]) for k, v in a_cols.items()}
            telemetry_brtc.write_brtc(
                eph_aux_out,
                columns=a_clean,
                metadata=a_meta,
                column_order=a_order,
                preferred_scales=a_scales,
            )
            eph_aux_paths[s_name] = eph_aux_out

    p_meta, p_order, p_cols, p_scales = _merge_stream_data(
        existing_brtc=prim_on_disk if os.path.exists(prim_on_disk) else None,
        existing_csv=csv_on_disk if os.path.exists(csv_on_disk) else None,
        segment_paths=prim_seg_paths,
    )
    if not p_cols or 'Time' not in p_cols or len(p_cols['Time']) == 0:
        return None

    res_track = track_info if track_info is not None else _resolve_track_info_from_metadata(p_meta)
    filtered_p_cols = _filter_primary_rows_by_gps_and_time(p_cols, p_meta, res_track)
    p_manifest = telemetry_brtc.write_brtc(
        eph_prim,
        columns=filtered_p_cols,
        metadata=p_meta,
        column_order=p_order,
        preferred_scales=p_scales,
    )
    _write_derived_from_primary(eph_prim, eph_deriv, p_manifest, res_track, eph_aux_paths)

    try:
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump({'fp_hash': fp_hash}, f)
    except OSError:
        pass

    return eph_prim, eph_deriv


# ---------------------------------------------------------------------------
# Unified High-Level Read, Serve & CSV Export API
# ---------------------------------------------------------------------------

def extract_driver_name(telemetry_dir: str, driver_stem: str) -> str | None:
    """Fast metadata lookup of driver_name without decoding telemetry sample arrays."""
    try:
        dir_entries = set(os.listdir(telemetry_dir))
    except OSError:
        dir_entries = set()

    prim_name = f'{driver_stem}{PRIMARY_BRTC_SUFFIX}'
    if prim_name in dir_entries:
        prim_p = primary_brtc_path(telemetry_dir, driver_stem)
        if os.path.exists(prim_p):
            try:
                man = telemetry_brtc.read_brtc_manifest(prim_p)
                drv = (man.get('metadata') or {}).get('driver_name')
                if drv:
                    return str(drv)
            except Exception:
                pass

    act_name = f'{driver_stem}{ACTIVE_DIR_SUFFIX}'
    if act_name in dir_entries:
        act_p = os.path.join(active_dir_path(telemetry_dir, driver_stem), PRIMARY_SUBDIR)
        if os.path.isdir(act_p):
            for fn in sorted(os.listdir(act_p)):
                if fn.endswith('.brt1'):
                    try:
                        raw_meta, _, _, _, start_dt = read_brt1_segment(os.path.join(act_p, fn))
                        norm = _normalize_metadata_dict(raw_meta, start_dt)
                        if norm.get('driver_name'):
                            return str(norm['driver_name'])
                    except Exception:
                        pass

    csv_p = legacy_csv_path(telemetry_dir, driver_stem)
    if f'{driver_stem}.csv' in dir_entries and os.path.exists(csv_p):
        drv_csv = _telemetry_native.extract_driver_name(csv_p)
        return drv_csv or None
    return None


def read_session_header(telemetry_dir: str, driver_stem: str) -> tuple[list[str], str | None]:
    """Fast header inspection returning (active_columns, driver_name) for MCP list_sessions."""
    pair = ensure_readable_brtc(telemetry_dir, driver_stem)
    if pair is not None:
        prim_p, deriv_p = pair
        p_man = telemetry_brtc.read_brtc_manifest(prim_p)
        d_man = telemetry_brtc.read_brtc_manifest(deriv_p)
        cols = _combine_active_column_names(p_man, d_man)
        drv = (p_man.get('metadata') or {}).get('driver_name')
        return cols, (str(drv) if drv else None)

    csv_p = legacy_csv_path(telemetry_dir, driver_stem)
    if os.path.exists(csv_p):
        try:
            with open(csv_p, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                meta_rows: list[list[str]] = []
                header: list[str] = []
                found_blank = False
                for row in reader:
                    if not found_blank:
                        if not row or all(c.strip() == '' for c in row):
                            found_blank = True
                        else:
                            meta_rows.append(row)
                    else:
                        header = [c.strip() for c in row]
                        break
                drv_name = None
                for mr in meta_rows:
                    if len(mr) >= 2 and mr[0] in ('Driver name', 'Configuration'):
                        drv_name = mr[1]
                        break
                return header, drv_name
        except (OSError, UnicodeDecodeError):
            pass
    return [], None


def _combine_active_column_names(p_man: dict[str, Any], d_man: dict[str, Any]) -> list[str]:
    p_cols = p_man.get('columns') or {}
    d_cols = d_man.get('columns') or {}
    ordered: list[str] = ['Record']
    for name in p_man.get('column_order', list(p_cols.keys())):
        info = d_cols.get(name) if name in d_cols else p_cols.get(name)
        if info and (info.get('has_data', True) or name in telemetry_brtc.CORE_ALWAYS_ACTIVE_COLUMNS):
            if name not in ordered:
                ordered.append(name)
    for name in d_man.get('column_order', list(d_cols.keys())):
        info = d_cols.get(name)
        if info and (info.get('has_data', True) or name in telemetry_brtc.CORE_ALWAYS_ACTIVE_COLUMNS):
            if name not in ordered:
                ordered.append(name)
    return ordered


def _track_coord_reference(track_data: dict[str, Any] | None) -> tuple[float | None, float | None, float]:
    """Extract (ref_lat, ref_lon, max_dist_m) from track_data dict without importing upload_handlers."""
    ref_lat: float | None = None
    ref_lon: float | None = None
    if track_data:
        cl = track_data.get('center_line')
        if cl and isinstance(cl, list):
            cl_lats = [float(p['lat']) for p in cl if isinstance(p, dict) and 'lat' in p]
            cl_lons = [float(p['lon']) for p in cl if isinstance(p, dict) and 'lon' in p]
            if cl_lats and cl_lons:
                ref_lat = float(np.mean(cl_lats))
                ref_lon = float(np.mean(cl_lons))
        if ref_lat is None:
            orig = track_data.get('origin')
            if orig and isinstance(orig, dict) and 'lat' in orig and 'lon' in orig:
                ref_lat = float(orig['lat'])
                ref_lon = float(orig['lon'])
    return ref_lat, ref_lon, 5000.0


def _parse_legacy_csv_laps_fallback(
    csv_path: str,
    track_data: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    """Self-contained legacy CSV lap & column parser when no external parser is supplied."""
    meta, order, raw_cols, _ = read_legacy_csv_raw_columns(csv_path, strip_derived=False)
    drv_raw = meta.get('driver_name')
    driver_name = str(drv_raw) if drv_raw else None
    filtered_cols = _filter_primary_rows_by_gps_and_time(raw_cols, meta, track_data)
    time_arr = filtered_cols.get('Time')
    if time_arr is None or len(time_arr) == 0:
        return [], [], driver_name

    lap_arr = filtered_cols.get('Lap')
    if lap_arr is None:
        lap_arr = np.ones(len(time_arr), dtype=np.float64)
    dist_arr: np.ndarray | None = None
    for cand in ('Lap Distance (m)', 'Lap Distance', 'LapDistance'):
        if cand in filtered_cols:
            dist_arr = filtered_cols[cand]
            break
    if dist_arr is None:
        dist_arr = np.zeros(len(time_arr), dtype=np.float64)

    laps_meta = telemetry_calculators.build_laps_manifest(lap_arr, dist_arr, time_arr)
    laps_out: list[dict[str, Any]] = []
    for l_meta in laps_meta:
        s_idx = int(l_meta['start_idx'])
        e_idx = int(l_meta['end_idx'])
        t_slice = time_arr[s_idx:e_idx + 1]
        d_slice = dist_arr[s_idx:e_idx + 1]
        lap_copy = dict(l_meta)
        lap_copy['times'] = t_slice[np.isfinite(t_slice)].tolist()
        lap_copy['dists'] = d_slice[np.isfinite(d_slice)].tolist()
        laps_out.append(lap_copy)

    active_cols: list[str] = []
    for col_name in order:
        if col_name in telemetry_brtc.CORE_ALWAYS_ACTIVE_COLUMNS:
            active_cols.append(col_name)
        elif col_name in filtered_cols:
            arr_c = filtered_cols[col_name]
            if bool(np.any(np.isfinite(arr_c) & (np.abs(arr_c) > 1e-6))):
                active_cols.append(col_name)
    return laps_out, active_cols, driver_name


def load_session_laps_and_columns(
    telemetry_dir: str,
    driver_stem: str,
    track_data: dict[str, Any] | None = None,
    legacy_csv_parser: Callable[..., tuple[list[Any], list[str], str | None]] | None = None,
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    """Load (laps, active_columns, driver_name) from .brtc/.active/ (or legacy .csv fallback).

    Each returned lap dict includes temporary 'dists' and 'times' lists matching
    location_handlers.parse_telemetry_csv so callers can run _compute_lap_segments().
    """
    pair = ensure_readable_brtc(telemetry_dir, driver_stem, track_info=track_data)
    if pair is not None:
        prim_p, deriv_p = pair
        p_man = telemetry_brtc.read_brtc_manifest(prim_p)
        d_man = telemetry_brtc.read_brtc_manifest(deriv_p)
        row_count = int(p_man.get('row_count', 0))
        drv_raw = (p_man.get('metadata') or {}).get('driver_name')
        driver_name = str(drv_raw) if drv_raw else None
        if row_count == 0:
            return [], [], driver_name

        time_arr = telemetry_brtc.read_brtc_column(prim_p, 'Time', manifest=p_man)
        dist_arr = telemetry_brtc.read_brtc_column(deriv_p, 'Lap Distance', manifest=d_man)
        if time_arr is None:
            return [], [], driver_name

        laps_out: list[dict[str, Any]] = []
        for l_meta in (d_man.get('laps') or []):
            s_idx = int(l_meta['start_idx'])
            e_idx = int(l_meta['end_idx'])
            t_slice = time_arr[s_idx:e_idx + 1]
            t_valid = t_slice[np.isfinite(t_slice)].tolist()
            if dist_arr is not None:
                d_slice = dist_arr[s_idx:e_idx + 1]
                d_valid = d_slice[np.isfinite(d_slice)].tolist()
            else:
                d_valid = []
            lap_copy = dict(l_meta)
            lap_copy['times'] = t_valid
            lap_copy['dists'] = d_valid
            laps_out.append(lap_copy)

        columns = _combine_active_column_names(p_man, d_man)
        return laps_out, columns, driver_name

    csv_p = legacy_csv_path(telemetry_dir, driver_stem)
    if os.path.exists(csv_p):
        if legacy_csv_parser is not None:
            return legacy_csv_parser(csv_p, track_data=track_data)
        return _parse_legacy_csv_laps_fallback(csv_p, track_data=track_data)
    return [], [], None


def serve_channel_payload(
    telemetry_dir: str,
    driver_stem: str,
    channel_name: str,
    league: str | None = None,
    track_data: dict[str, Any] | None = None,
) -> bytes:
    """Serve Brotli-compressed multi-block wire bytes for channel_name (.brtc/.active/ or legacy .csv)."""
    should_smooth = (league == 'kartsim' and should_smooth_channel(channel_name))
    is_speed = (channel_name == 'Speed')

    pair = ensure_readable_brtc(telemetry_dir, driver_stem, track_info=track_data)
    if pair is not None:
        prim_p, deriv_p = pair
        d_man = telemetry_brtc.read_brtc_manifest(deriv_p)
        if telemetry_brtc.resolve_column_name(d_man, channel_name) is not None:
            res = telemetry_brtc.serve_brtc_column(
                deriv_p, channel_name, is_speed=is_speed, should_smooth=should_smooth, manifest=d_man
            )
            if res is not None:
                return res

        p_man = telemetry_brtc.read_brtc_manifest(prim_p)
        if telemetry_brtc.resolve_column_name(p_man, channel_name) is not None:
            res = telemetry_brtc.serve_brtc_column(
                prim_p, channel_name, is_speed=is_speed, should_smooth=should_smooth, manifest=p_man
            )
            if res is not None:
                return res

        if channel_name == 'Record':
            n = int(p_man.get('row_count', 0))
            rec_arr = np.arange(1, n + 1, dtype=np.float64)
            return _telemetry_native.encode_channel_blocks(rec_arr, 1.0)

        # Channel absent in this session -> N NaNs matching process_telemetry_channel behavior
        n = int(p_man.get('row_count', 0))
        nan_arr = np.full(n, np.nan, dtype=np.float64)
        return _telemetry_native.encode_channel_blocks(nan_arr, 1.0)

    csv_p = legacy_csv_path(telemetry_dir, driver_stem)
    if os.path.exists(csv_p):
        ref_lat, ref_lon, max_dist_m = _track_coord_reference(track_data)
        has_ref = (ref_lat is not None and ref_lon is not None)
        return _telemetry_native.process_telemetry_channel(
            csv_p,
            channel_name,
            should_smooth,
            ref_lat or 0.0,
            ref_lon or 0.0,
            max_dist_m or 5000.0,
            has_ref,
        )

    return _telemetry_native.encode_channel_blocks(np.zeros(0, dtype=np.float64), 1.0)


def read_driver_columns(
    telemetry_dir: str,
    driver_stem: str,
    requested_channels: list[str] | None = None,
    track_data: dict[str, Any] | None = None,
) -> tuple[dict[str, np.ndarray], list[str], dict[str, Any]]:
    """Read merged (primary + derived) float64 column arrays, column order, and metadata."""
    pair = ensure_readable_brtc(telemetry_dir, driver_stem, track_info=track_data)
    if pair is not None:
        prim_p, deriv_p = pair
        p_man = telemetry_brtc.read_brtc_manifest(prim_p)
        d_man = telemetry_brtc.read_brtc_manifest(deriv_p)
        meta = dict(p_man.get('metadata') or {})
        n = int(p_man.get('row_count', 0))

        if requested_channels is None:
            p_all = telemetry_brtc.read_brtc_columns(prim_p, manifest=p_man)
            d_all = telemetry_brtc.read_brtc_columns(deriv_p, manifest=d_man)
            merged = {'Record': np.arange(1, n + 1, dtype=np.float64)}
            merged.update(p_all)
            merged.update(d_all)
            order = ['Record']
            for k in p_man.get('column_order', list(p_all.keys())):
                if k not in order:
                    order.append(k)
            for k in d_man.get('column_order', list(d_all.keys())):
                if k not in order:
                    order.append(k)
            return merged, order, meta

        out: dict[str, np.ndarray] = {}
        order = _combine_active_column_names(p_man, d_man)
        for req in requested_channels:
            d_res = telemetry_brtc.resolve_column_name(d_man, req)
            if d_res is not None:
                arr = telemetry_brtc.read_brtc_column(deriv_p, d_res, manifest=d_man)
                if arr is not None:
                    out[req] = arr
                    continue
            p_res = telemetry_brtc.resolve_column_name(p_man, req)
            if p_res is not None:
                arr = telemetry_brtc.read_brtc_column(prim_p, p_res, manifest=p_man)
                if arr is not None:
                    out[req] = arr
                    continue
            if req.lower() == 'record':
                out[req] = np.arange(1, n + 1, dtype=np.float64)
        return out, order, meta

    csv_p = legacy_csv_path(telemetry_dir, driver_stem)
    if os.path.exists(csv_p):
        meta, order, cols, _ = read_legacy_csv_raw_columns(csv_p, strip_derived=False)
        if requested_channels is None:
            return cols, order, meta
        out_csv: dict[str, np.ndarray] = {}
        for req in requested_channels:
            if req in cols:
                out_csv[req] = cols[req]
            else:
                match_k = next((k for k in cols if k.lower() == req.lower()), None)
                if match_k is not None:
                    out_csv[req] = cols[match_k]
        return out_csv, order, meta

    return {}, [], {}


def export_session_csv(
    telemetry_dir: str,
    driver_stem: str,
    track_data: dict[str, Any] | None = None,
) -> str | None:
    """Generate a canonical RaceTools CSV string in memory from .brtc/.active/ (or legacy .csv)."""
    csv_p = legacy_csv_path(telemetry_dir, driver_stem)
    pair = ensure_readable_brtc(telemetry_dir, driver_stem, track_info=track_data)
    if pair is None:
        if os.path.exists(csv_p):
            with open(csv_p, 'r', encoding='utf-8') as f:
                return f.read()
        return None

    cols, col_order, meta = read_driver_columns(telemetry_dir, driver_stem, track_data=track_data)
    if not cols or 'Time' not in cols:
        return None

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator='\n')
    writer.writerow(['Format', 'RaceTools CSV'])
    if meta.get('track_name'):
        writer.writerow(['Track name', meta['track_name']])
    if meta.get('track_configuration') is not None:
        writer.writerow(['Track configuration', meta['track_configuration']])
    if meta.get('session_start_date'):
        writer.writerow(['Date', meta['session_start_date']])
    if meta.get('session_start_time'):
        writer.writerow(['Time', meta['session_start_time']])
    if meta.get('driver_name'):
        writer.writerow(['Driver name', meta['driver_name']])
    if meta.get('league'):
        writer.writerow(['League', meta['league']])
    if meta.get('class_name'):
        writer.writerow(['Class', meta['class_name']])
    if meta.get('session_name'):
        writer.writerow(['Session', meta['session_name']])
    if meta.get('distance_to_rear_axle') is not None:
        writer.writerow(['Distance to rear axle', meta['distance_to_rear_axle']])
    if meta.get('track_origin_lat') is not None and meta.get('track_origin_lon') is not None:
        writer.writerow(['Track Origin Latitude', meta['track_origin_lat']])
        writer.writerow(['Track Origin Longitude', meta['track_origin_lon']])
        writer.writerow(['Track Origin Heading', meta.get('track_origin_heading', 0.0)])
    writer.writerow([])

    export_cols = [c for c in col_order if c in cols]
    writer.writerow(export_cols)

    start_date_str = str(meta.get('session_start_date') or '1970-01-01')
    try:
        d_obj = datetime.date.fromisoformat(start_date_str[:10])
    except ValueError:
        d_obj = datetime.date(1970, 1, 1)
    midnight = datetime.datetime(d_obj.year, d_obj.month, d_obj.day)

    n_rows = len(cols['Time'])
    for i in range(n_rows):
        row_out: list[str] = []
        for col_name in export_cols:
            val = float(cols[col_name][i])
            if math.isnan(val) or math.isinf(val):
                row_out.append('')
            elif col_name == 'Time':
                dt_sample = midnight + datetime.timedelta(seconds=val)
                row_out.append(dt_sample.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z')
            elif col_name in ('Record', 'Lap'):
                row_out.append(str(int(round(val))))
            elif col_name in ('Latitude', 'Longitude'):
                row_out.append(f'{val:.8f}')
            elif col_name == 'Speed':
                row_out.append(f'{val:.5f}')
            elif col_name in (
                'Ori Quat X', 'Ori Quat Y', 'Ori Quat Z', 'Ori Quat W', 'Toe FL', 'Toe FR'
            ):
                row_out.append(f'{val:.6f}')
            elif col_name in (
                'GForceLat', 'GForceLon', 'GForceVert',
                'Yaw Rate', 'Pitch Rate', 'Roll Rate',
                'Lat Force Front', 'Lat Force Rear', 'Long Force Front', 'Long Force Rear',
            ):
                row_out.append(f'{val:.3f}')
            elif col_name in ('Throttle', 'Brake', 'Steering Angle'):
                row_out.append(f'{val:.1f}')
            else:
                row_out.append(f'{val:.2f}')
        writer.writerow(row_out)

    return buf.getvalue()
