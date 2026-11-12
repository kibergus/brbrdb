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
import datetime
import math
import os
from typing import Any
from flask import has_request_context
import numpy as np
import auth
from database import db
import location_handlers
from mcp_server import image_tools
import telemetry_brtc
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


def _interpolate_primary_lap_and_dist_to_aux(
    aux_time: np.ndarray,
    prim_time: np.ndarray,
    prim_lap: np.ndarray | None,
    prim_dist: np.ndarray | None,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Interpolate primary Lap (previous/nearest sample) and Lap Distance (linear within same lap) onto aux_time."""
    valid_p = np.isfinite(prim_time)
    if not np.any(valid_p) or aux_time.size == 0:
        out_lap = np.full(aux_time.shape, np.nan, dtype=np.float64) if prim_lap is not None else None
        out_dist = np.full(aux_time.shape, np.nan, dtype=np.float64) if prim_dist is not None else None
        return out_lap, out_dist

    p_idx = np.flatnonzero(valid_p)
    p_t_raw = prim_time[p_idx]
    p_order = np.argsort(p_t_raw, kind='mergesort')
    p_t = p_t_raw[p_order]
    n_p = int(p_t.size)

    valid_aux = (
        np.isfinite(aux_time)
        & (aux_time >= float(p_t[0]) - 0.05)
        & (aux_time <= float(p_t[-1]) + 0.05)
    )

    idx_right = np.searchsorted(p_t, np.nan_to_num(aux_time, nan=0.0), side='right')
    idx_prev = np.clip(idx_right - 1, 0, n_p - 1)
    idx_next = np.clip(idx_right, 0, n_p - 1)

    out_lap_arr: np.ndarray | None = None
    p_lap_sorted: np.ndarray | None = None
    if prim_lap is not None:
        p_lap_sorted = prim_lap[p_idx][p_order]
        out_lap_arr = np.full(aux_time.shape, np.nan, dtype=np.float64)
        out_lap_arr[valid_aux] = p_lap_sorted[idx_prev[valid_aux]]

    out_dist_arr: np.ndarray | None = None
    if prim_dist is not None:
        p_dist_sorted = prim_dist[p_idx][p_order]
        out_dist_arr = np.full(aux_time.shape, np.nan, dtype=np.float64)
        if np.any(valid_aux):
            ip = idx_prev[valid_aux]
            inext = idx_next[valid_aux]
            t_cur = aux_time[valid_aux]
            t0 = p_t[ip]
            t1 = p_t[inext]
            d0 = p_dist_sorted[ip]
            d1 = p_dist_sorted[inext]
            dt = t1 - t0

            same_lap = np.ones(ip.shape, dtype=bool)
            if p_lap_sorted is not None:
                l0 = p_lap_sorted[ip]
                l1 = p_lap_sorted[inext]
                same_lap = np.isfinite(l0) & np.isfinite(l1) & (np.round(l0) == np.round(l1))

            can_lerp = (
                same_lap
                & (dt > 1e-9)
                & (dt <= 2.0)
                & np.isfinite(d0)
                & np.isfinite(d1)
                & (d1 >= d0 - 1.0)
            )
            alpha = np.zeros(ip.shape, dtype=np.float64)
            alpha[can_lerp] = np.clip((t_cur[can_lerp] - t0[can_lerp]) / dt[can_lerp], 0.0, 1.0)
            interp_vals = np.where(can_lerp, d0 + alpha * (d1 - d0), d0)
            out_dist_arr[valid_aux] = interp_vals

    return out_lap_arr, out_dist_arr


def get_telemetry_data_impl(
    session_id: str,
    track: str,
    date: str,
    channels: list[str] | None = None,
    driver_name: str | None = None,
    laps: list[int] | None = None,
    start_m: float | None = None,
    end_m: float | None = None,
    raw_aux_stream: str | None = None,
) -> dict[str, Any]:
    """Download 100 Hz primary-grid (or native auxiliary-stream) numeric telemetry samples for a session."""
    if start_m is not None and end_m is not None and start_m >= end_m:
        raise ValueError("start_m must be strictly less than end_m")
    if laps is not None and len(laps) == 0:
        raise ValueError("laps parameter cannot be empty when provided")

    norm_aux_stream: str | None = None
    if raw_aux_stream is not None and raw_aux_stream.strip() != '':
        norm_aux_stream = telemetry_store.normalize_stream_name(raw_aux_stream)

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

    track_info = db.get_track(s0.track_name if s0 is not None else track) or {}

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

    if norm_aux_stream is not None:
        brtc_pair = telemetry_store.ensure_readable_brtc(tel_dir, stem, track_info=track_info)
        avail_aux_map = dict(telemetry_store.list_aux_brtc_files(tel_dir, stem))
        if brtc_pair is not None:
            prim_dir = os.path.dirname(brtc_pair[0])
            if os.path.realpath(prim_dir) != os.path.realpath(tel_dir):
                avail_aux_map.update(telemetry_store.list_aux_brtc_files(prim_dir, stem))

        aux_path = avail_aux_map.get(norm_aux_stream)
        if not aux_path or not os.path.exists(aux_path):
            avail_names = sorted(avail_aux_map.keys())
            raise ValueError(
                f"Auxiliary stream {norm_aux_stream!r} not found for session_id={session_id!r}. "
                f"Available aux streams: {avail_names}"
            )

        aux_manifest = telemetry_brtc.read_brtc_manifest(aux_path)
        aux_cols_meta = aux_manifest.get('columns') or {}
        aux_col_order = [
            str(c) for c in aux_manifest.get('column_order', list(aux_cols_meta.keys()))
            if c in aux_cols_meta
        ]
        aux_reserved_lower = {'record', 'time', 'lap', 'lap distance', 'lap distance (m)', 'lapdistance'}

        aux_selected_pairs: list[tuple[str, str]] = []
        seen_aux_out: set[str] = set()
        prefix_lower = f"{norm_aux_stream}:"
        if channels is None:
            for col_h in aux_col_order:
                if col_h.lower() in aux_reserved_lower:
                    continue
                col_entry = aux_cols_meta.get(col_h)
                if isinstance(col_entry, dict) and not col_entry.get('has_data', True):
                    continue
                if col_h not in seen_aux_out:
                    seen_aux_out.add(col_h)
                    aux_selected_pairs.append((col_h, col_h))
        else:
            for ch in channels:
                ch_clean = ch[len(prefix_lower):] if ch.lower().startswith(prefix_lower) else ch
                matched = next(
                    (h for h in aux_col_order if h == ch_clean or h.lower() == ch_clean.lower()),
                    None,
                )
                if matched is None:
                    raise ValueError(
                        f"Channel {ch!r} not found in auxiliary stream {norm_aux_stream!r} columns: {aux_col_order}"
                    )
                if matched.lower() in aux_reserved_lower:
                    continue
                if matched not in seen_aux_out:
                    seen_aux_out.add(matched)
                    aux_selected_pairs.append((matched, matched))

        aux_needed = ['Time']
        for _, src_col in aux_selected_pairs:
            if src_col not in aux_needed:
                aux_needed.append(src_col)
        aux_arrays = telemetry_brtc.read_brtc_columns(aux_path, channel_names=aux_needed, manifest=aux_manifest)
        aux_time_raw = aux_arrays.get('Time')
        if aux_time_raw is None:
            raise ValueError(f"Time column missing in auxiliary stream {norm_aux_stream!r} for {session_id!r}")

        prim_needed = ['Time']
        if lap_col_actual:
            prim_needed.append(lap_col_actual)
        if dist_col and dist_col not in prim_needed:
            prim_needed.append(dist_col)
        prim_arrays, _, meta = telemetry_store.read_driver_columns(
            tel_dir, stem, requested_channels=prim_needed, track_data=track_info
        )
        prim_time = prim_arrays.get('Time')
        if prim_time is None:
            raise ValueError(f"Time column missing in primary telemetry for session_id={session_id!r}")
        prim_lap = prim_arrays.get(lap_col_actual) if lap_col_actual else None
        prim_dist = prim_arrays.get(dist_col) if dist_col else None

        day_offset_s = 0.0
        prim_date_str = str(meta.get('session_start_date') or '')
        aux_date_str = str((aux_manifest.get('metadata') or {}).get('session_start_date') or '')
        if prim_date_str and aux_date_str:
            try:
                p_dt = datetime.date.fromisoformat(prim_date_str[:10])
                a_dt = datetime.date.fromisoformat(aux_date_str[:10])
                day_offset_s = float((a_dt - p_dt).days * 86400)
            except ValueError:
                day_offset_s = 0.0

        time_arr = aux_time_raw + day_offset_s if day_offset_s != 0.0 else aux_time_raw
        lap_arr, dist_arr = _interpolate_primary_lap_and_dist_to_aux(
            time_arr, prim_time, prim_lap, prim_dist
        )
        col_arrays = aux_arrays
        selected_pairs = aux_selected_pairs
        sample_rate_hz = 500.0
    else:
        reserved_lower = {'record', 'time'}
        if lap_col_actual:
            reserved_lower.add(lap_col_actual.lower())
        if dist_col:
            reserved_lower.add(dist_col.lower())

        selected_pairs = []
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

        col_arrays, _, meta = telemetry_store.read_driver_columns(
            tel_dir, stem, requested_channels=needed_cols, track_data=track_info
        )

        raw_time_arr = col_arrays.get('Time')
        if raw_time_arr is None:
            raise ValueError(f"Time column missing in telemetry for session_id={session_id!r}")
        time_arr = raw_time_arr
        lap_arr = col_arrays.get(lap_col_actual) if lap_col_actual else None
        dist_arr = col_arrays.get(dist_col) if dist_col else None
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

    time_precision = 5 if norm_aux_stream is not None else 4
    out_columns: list[str] = ['Time']
    data_out: dict[str, list[float | int | None]] = {
        'Time': [
            round(float(v), time_precision) if math.isfinite(v) else None
            for v in time_arr[mask].tolist()
        ]
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
        if norm_aux_stream is None and src_col.lower() == 'speed':
            sliced = sliced * 3.6
        data_out[out_label] = [
            round(float(v), 4) if math.isfinite(v) else None for v in sliced.tolist()
        ]

    drv_final = resolved_driver or str(meta.get('driver_name') or driver_name or '')
    res_dict: dict[str, Any] = {
        'session_id': s0.session_id if s0 is not None else session_id,
        'date': s0.date if s0 is not None else date,
        'track': s0.track_name if s0 is not None else track,
        'driver_name': drv_final,
        'sample_rate_hz': sample_rate_hz,
        'row_count': int(np.sum(mask)),
        'columns': out_columns,
        'data': data_out,
    }
    if norm_aux_stream is not None:
        res_dict['aux_stream'] = norm_aux_stream
    return res_dict
