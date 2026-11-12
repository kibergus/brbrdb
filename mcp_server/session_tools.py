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

from __future__ import annotations
import math
import os
from typing import Any
from flask import has_request_context
import numpy as np
import auth
from database import db
import location_handlers
from mcp_server import image_tools
import telemetry_store


def read_telemetry_header(csv_path: str) -> tuple[list[str], str | None]:
    """Fast header parser to extract available columns and driver name without reading data rows."""
    tel_dir = os.path.dirname(csv_path)
    stem = telemetry_store.strip_telemetry_suffix(os.path.basename(csv_path))
    return telemetry_store.read_session_header(tel_dir, stem)


def collect_session_telemetry_channels_and_drivers(
    tel_dir: str,
    session_id: str,
    drivers: list[str],
    driver_filter: str | None = None,
) -> tuple[list[str], list[str]]:
    """Collect union of active telemetry channels and discovered driver names for a session."""
    telemetry_channels: list[str] = []
    seen_channels: set[str] = set()
    skip = {'record', 'time', 'latitude', 'longitude', 'altitude'}
    updated_drivers = list(drivers)
    target_drv = driver_filter.strip().lower() if driver_filter else None

    if tel_dir and os.path.exists(tel_dir):
        for stem in telemetry_store.list_driver_stems(tel_dir):
            if stem.startswith(session_id) or session_id in stem:
                cols, tel_driver = telemetry_store.read_session_header(tel_dir, stem)
                if tel_driver and tel_driver not in updated_drivers:
                    updated_drivers.append(tel_driver)

                if target_drv and tel_driver and tel_driver.strip().lower() != target_drv:
                    continue

                for c in cols:
                    if c.lower() not in skip and c not in seen_channels:
                        seen_channels.add(c)
                        telemetry_channels.append(c)

    return telemetry_channels, updated_drivers


def list_sessions_impl(
    track: str | None = None,
    date: str | None = None,
    driver_name: str | None = None,
    league: str | None = None,
    class_name: str | None = None
) -> list[dict[str, Any]]:
    """
    List recorded sessions matching a set of optional filters.

    Parameters:
    - track: Track name, e.g. "Lydd" or "Lydd Karting 2026"
    - date: ISO date "2026-07-12" — omit to list all
    - driver_name: Filter by driver name
    - league: e.g. "kartsim"
    - class_name: e.g. "iame_waterswift_restricted_cadet_uk"

    Returns array of session descriptors.
    """
    raw_sessions = db.find_sessions(
        leagues=league,
        classes=class_name,
        date=date,
        track=track,
        driver_names=driver_name
    )

    results = []
    for s in raw_sessions:
        drivers = db.get_session_drivers(s.session_id, s.meeting_folder)
        tel_dir = os.path.join(s.meeting_dir, 'telemetry') if s.meeting_dir else ''
        telemetry_channels, drivers = collect_session_telemetry_channels_and_drivers(
            tel_dir=tel_dir,
            session_id=s.session_id,
            drivers=drivers,
            driver_filter=driver_name,
        )

        results.append({
            "session_id": s.session_id,
            "date": s.date,
            "track": s.track_name,
            "league": s.league,
            "class_name": s.class_name[0] if s.class_name else "",
            "session_type": s.short_name or s.session_name,
            "driver_names": drivers,
            "telemetry_channels": telemetry_channels
        })

    return results


def get_telemetry_data_impl(
    session_id: str,
    track: str,
    date: str,
    channels: list[str] | None = None,
    driver_name: str | None = None,
    laps: list[int] | None = None,
    start_m: float | None = None,
    end_m: float | None = None,
) -> dict[str, Any]:
    """Download 100 Hz primary-grid numeric telemetry samples for a session (optionally sliced by laps/distance)."""
    if start_m is not None and end_m is not None and start_m >= end_m:
        raise ValueError("start_m must be strictly less than end_m")
    if laps is not None and len(laps) == 0:
        raise ValueError("laps parameter cannot be empty when provided")

    csv_path = image_tools.find_telemetry_csv(
        session_id=session_id,
        track=track,
        date=date,
        driver_name=driver_name,
    )
    tel_dir = os.path.dirname(csv_path)
    stem = telemetry_store.strip_telemetry_suffix(os.path.basename(csv_path))

    raw_sessions = db.find_sessions(date=date, track=track, session_id=session_id)
    s0 = raw_sessions[0] if raw_sessions else None

    avail_cols, resolved_driver = telemetry_store.read_session_header(tel_dir, stem)
    if not avail_cols:
        raise ValueError(f"No telemetry columns found for session_id={session_id!r}")

    if has_request_context():
        acl = auth.get_current_acl()
        league_id = s0.league if s0 is not None else None
        if not auth.can_see_telemetry(acl, league=league_id, driver=resolved_driver or driver_name):
            raise PermissionError("Access to the telemetry data is restricted.")

    dist_col: str | None = None
    for cand in ('Lap Distance (m)', 'Lap Distance', 'LapDistance'):
        for col_h in avail_cols:
            if col_h == cand or col_h.lower() == cand.lower():
                dist_col = col_h
                break
        if dist_col:
            break

    has_lap_col = any(c.lower() == 'lap' for c in avail_cols)
    lap_col_actual = next((c for c in avail_cols if c.lower() == 'lap'), None)

    reserved_lower = {'record', 'time'}
    if lap_col_actual:
        reserved_lower.add(lap_col_actual.lower())
    if dist_col:
        reserved_lower.add(dist_col.lower())

    selected_pairs: list[tuple[str, str]] = []
    seen_out: set[str] = set()
    if channels is None:
        for col_h in avail_cols:
            if col_h.lower() not in reserved_lower and col_h not in seen_out:
                seen_out.add(col_h)
                selected_pairs.append((col_h, col_h))
    else:
        for ch in channels:
            matched = next((h for h in avail_cols if h == ch or h.lower() == ch.lower()), None)
            if matched is None:
                raise ValueError(f"Channel {ch!r} not found in telemetry columns: {avail_cols}")
            if matched.lower() in reserved_lower:
                continue
            out_label = matched
            if out_label not in seen_out:
                seen_out.add(out_label)
                selected_pairs.append((out_label, matched))

    needed_cols = ['Time']
    if lap_col_actual:
        needed_cols.append(lap_col_actual)
    if dist_col and dist_col not in needed_cols:
        needed_cols.append(dist_col)
    for _, src_col in selected_pairs:
        if src_col not in needed_cols:
            needed_cols.append(src_col)

    track_info = db.get_track(s0.track_name if s0 is not None else track) or {}
    col_arrays, _, meta = telemetry_store.read_driver_columns(
        tel_dir, stem, requested_channels=needed_cols, track_data=track_info
    )

    time_arr = col_arrays.get('Time')
    if time_arr is None:
        raise ValueError(f"Time column missing in telemetry for session_id={session_id!r}")
    lap_arr = col_arrays.get(lap_col_actual) if lap_col_actual else None
    dist_arr = col_arrays.get(dist_col) if dist_col else None

    raw_to_norm: dict[int, int] = {}
    norm_to_raw: dict[int, int] = {}
    if has_lap_col:
        parsed_laps, _, _ = telemetry_store.load_session_laps_and_columns(
            tel_dir, stem, track_data=track_info
        )
        if s0 is not None and parsed_laps:
            df_official = db.load(
                leagues=s0.league, classes=s0.class_name, date=s0.date, track=s0.track_name
            )
            location_handlers._override_with_official_laps(
                parsed_laps, df_official, s0.session_id, resolved_driver
            )
        for plap in parsed_laps:
            n_num = int(plap['lap_num'])
            r_num = int(plap.get('raw_lap_num', n_num))
            raw_to_norm[r_num] = n_num
            norm_to_raw[n_num] = r_num

    sample_rate_hz = 100.0
    valid_t_all = time_arr[np.isfinite(time_arr)]
    if valid_t_all.size > 1:
        dt_diffs = np.diff(valid_t_all)
        dt_diffs = dt_diffs[dt_diffs > 0.0]
        if dt_diffs.size > 0:
            med_dt = float(np.median(dt_diffs))
            if med_dt > 0.0:
                sample_rate_hz = round(1.0 / med_dt, 2)

    mask = np.isfinite(time_arr)
    if laps is not None:
        if lap_arr is None:
            raise ValueError(f"Lap column not available in telemetry for session_id={session_id!r}")
        target_raw = [norm_to_raw.get(int(lp), int(lp)) for lp in laps]
        lap_ints_full = np.round(np.nan_to_num(lap_arr, nan=-999999.0)).astype(np.int64)
        mask = mask & np.isin(lap_ints_full, target_raw)

    if start_m is not None or end_m is not None:
        if dist_arr is None:
            raise ValueError(f"Lap Distance column not available in telemetry for session_id={session_id!r}")
        mask = mask & np.isfinite(dist_arr)
        if start_m is not None:
            mask = mask & (dist_arr >= float(start_m))
        if end_m is not None:
            mask = mask & (dist_arr <= float(end_m))

    out_columns: list[str] = ['Time']
    data_out: dict[str, list[float | int | None]] = {
        'Time': [round(float(v), 4) if math.isfinite(v) else None for v in time_arr[mask].tolist()]
    }

    if lap_arr is not None:
        out_columns.append('Lap')
        lap_slice = lap_arr[mask].tolist()
        data_out['Lap'] = [
            raw_to_norm.get(int(round(v)), int(round(v))) if math.isfinite(v) else None
            for v in lap_slice
        ]

    if dist_arr is not None:
        out_columns.append('Lap Distance')
        data_out['Lap Distance'] = [
            round(float(v), 3) if math.isfinite(v) else None for v in dist_arr[mask].tolist()
        ]

    for out_label, src_col in selected_pairs:
        out_columns.append(out_label)
        arr_c = col_arrays.get(src_col)
        if arr_c is None:
            data_out[out_label] = [None] * int(np.sum(mask))
            continue
        sliced = arr_c[mask]
        if src_col.lower() == 'speed':
            sliced = sliced * 3.6
        data_out[out_label] = [
            round(float(v), 4) if math.isfinite(v) else None for v in sliced.tolist()
        ]

    drv_final = resolved_driver or str(meta.get('driver_name') or driver_name or '')
    return {
        'session_id': s0.session_id if s0 is not None else session_id,
        'date': s0.date if s0 is not None else date,
        'track': s0.track_name if s0 is not None else track,
        'driver_name': drv_final,
        'sample_rate_hz': sample_rate_hz,
        'row_count': int(np.sum(mask)),
        'columns': out_columns,
        'data': data_out,
    }
