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

"""Comprehensive tests for .brtc columnar storage, multi-block wire format, multi-stream upload, and compaction."""

from __future__ import annotations

import json
import math
from pathlib import Path
import struct
from typing import Any
from unittest.mock import MagicMock, patch

import brotli  # type: ignore[import-untyped]
import numpy as np
import pandas as pd
import pytest

import _telemetry_native
import app as flask_app
from mcp_server import image_tools
import telemetry_brtc
import telemetry_calculators
import telemetry_store
from utils import compact_sessions, compaction_daemon, recompute_channels


def _pack_brt1_stream(
    metadata: dict[str, Any],
    channels: list[dict[str, Any]],
    rows: list[list[float]],
    include_eos: bool = True,
) -> bytes:
    """Build a valid raw BRT1 binary stream from metadata, channel specs, and sample rows."""
    out = bytearray()
    out.extend(telemetry_store.pack_brt1_chunk(0x01, b'BRT1'))
    meta_bytes = json.dumps({'metadata': metadata, 'channels': channels}).encode('utf-8')
    out.extend(telemetry_store.pack_brt1_chunk(0x02, meta_bytes))

    m = len(channels)
    scales = [float(c['scale']) for c in channels]
    current: list[float] | None = None

    for row in rows:
        need_baseline = current is None
        deltas = [0] * m
        if not need_baseline and current is not None:
            for j in range(m):
                v = row[j]
                if math.isnan(v):
                    deltas[j] = -32768
                elif math.isnan(current[j]):
                    need_baseline = True
                    break
                else:
                    q = int(round((v - current[j]) / scales[j]))
                    if q < -32767 or q > 32767:
                        need_baseline = True
                        break
                    deltas[j] = q

        if need_baseline:
            out.extend(telemetry_store.pack_brt1_chunk(0x03, struct.pack(f'<{m}d', *row)))
            current = list(row)
        else:
            assert current is not None
            out.extend(telemetry_store.pack_brt1_chunk(0x04, struct.pack(f'<{m}h', *deltas)))
            for j in range(m):
                if deltas[j] == -32768:
                    current[j] = math.nan
                else:
                    current[j] += deltas[j] * scales[j]

    if include_eos:
        out.extend(telemetry_store.pack_brt1_chunk(0xFF, b''))
    return bytes(out)


def _count_raw_blocks(brotli_payload: bytes) -> list[tuple[float, float, int]]:
    """Return [(offset, scale, count), ...] across all 20-byte block headers in a Brotli channel payload."""
    raw = brotli.decompress(brotli_payload)
    blocks: list[tuple[float, float, int]] = []
    pos = 0
    while pos + 20 <= len(raw):
        offset_val, scale_val, count = struct.unpack('<ddI', raw[pos:pos + 20])
        blocks.append((offset_val, scale_val, count))
        pos += 20 + count * 2
    assert pos == len(raw)
    return blocks


def test_multiblock_encoding_pit_jumps_and_arbitrary_header_scales() -> None:
    """Verify multi-block splits on large jumps and strict adherence to header scale (never assuming 0.0001)."""
    # 1. Pit-lane pause in Time with preferred_scale=0.0001 (max single step ~3.2767s -> 180s jump forces new block)
    t_stint1 = np.linspace(36000.0, 36005.0, 51, dtype=np.float64)
    t_stint2 = np.linspace(36185.0, 36190.0, 51, dtype=np.float64)
    time_arr = np.concatenate([t_stint1, [np.nan, np.nan], t_stint2])

    encoded = _telemetry_native.encode_channel_blocks(time_arr, 0.0001)
    blocks = _count_raw_blocks(encoded)
    assert len(blocks) >= 2
    assert blocks[0][1] == pytest.approx(0.0001)

    decoded = _telemetry_native.decode_channel_blocks(encoded)
    assert len(decoded) == len(time_arr)
    assert np.isnan(decoded[51]) and np.isnan(decoded[52])
    finite_mask = np.isfinite(time_arr)
    assert np.max(np.abs(decoded[finite_mask] - time_arr[finite_mask])) < 1e-4

    # 2. Manually construct a two-block payload with completely distinct custom scales (0.25 and 0.007)
    # to guarantee decode_channel_blocks reads scale strictly from each 20-byte block header.
    b1_hdr = struct.pack('<ddI', 100.0, 0.25, 3)
    b1_del = struct.pack('<3h', 0, 4, -2)  # -> [100.0, 101.0, 100.5]
    b2_hdr = struct.pack('<ddI', -50.0, 0.007, 3)
    b2_del = struct.pack('<3h', 0, 100, -32768)  # -> [-50.0, -49.3, NaN]
    custom_compressed = brotli.compress(b1_hdr + b1_del + b2_hdr + b2_del, quality=4)

    dec_custom = _telemetry_native.decode_channel_blocks(custom_compressed)
    assert len(dec_custom) == 6
    assert np.allclose(dec_custom[:5], [100.0, 101.0, 100.5, -50.0, -49.3], atol=1e-9)
    assert np.isnan(dec_custom[5])


def test_brtc_container_zero_copy_and_post_processing(tmp_path: Path) -> None:
    """Verify .brtc atomic creation, zero-copy slice serving, Speed * 3.6, and KartSim smoothing."""
    brtc_file = str(tmp_path / 'sample.primary.brtc')
    cols = {
        'Time': np.array([1000.0, 1000.1, 1000.2, 1000.3, 1000.4], dtype=np.float64),
        'Speed': np.array([10.0, 15.0, 20.0, 25.0, 30.0], dtype=np.float64),
        'GForceLat': np.array([1.0, 2.0, 3.0, 4.0, 5.0], dtype=np.float64),
        'Throttle': np.array([20.0, 40.0, 60.0, 80.0, 100.0], dtype=np.float64),
    }
    manifest = telemetry_brtc.write_brtc(
        brtc_file,
        columns=cols,
        metadata={'driver_name': 'Alice', 'league': 'kartsim'},
        preferred_scales={'Time': 0.0001},
    )
    assert manifest['row_count'] == 5

    # 1. Zero-copy pass-through: served bytes for Throttle must equal exact raw file slice bytes
    th_meta = manifest['columns']['Throttle']
    with open(brtc_file, 'rb') as f:
        f.seek(int(th_meta['offset']))
        disk_slice = f.read(int(th_meta['length']))

    served_throttle = telemetry_brtc.serve_brtc_column(
        brtc_file, 'Throttle', is_speed=False, should_smooth=False
    )
    assert served_throttle == disk_slice

    # 2. Speed post-processing (* 3.6 m/s -> km/h)
    served_speed = telemetry_brtc.serve_brtc_column(
        brtc_file, 'Speed', is_speed=True, should_smooth=False
    )
    assert served_speed is not None
    dec_speed = _telemetry_native.decode_channel_blocks(served_speed)
    assert np.allclose(dec_speed, cols['Speed'] * 3.6, atol=1e-3)

    # 3. KartSim 5-sample smoothing on GForceLat ([1,2,3,4,5] -> [2.0, 2.5, 3.0, 3.5, 4.0])
    served_glat = telemetry_brtc.serve_brtc_column(
        brtc_file, 'GForceLat', is_speed=False, should_smooth=True
    )
    assert served_glat is not None
    dec_glat = _telemetry_native.decode_channel_blocks(served_glat)
    assert np.allclose(dec_glat, [2.0, 2.5, 3.0, 3.5, 4.0], atol=1e-3)


def test_stream_upload_reconnects_aux_streams_ephemeral_serving_and_compaction(tmp_path: Path) -> None:
    """End-to-end test: aux-before-primary, primary reconnect overlap pruning, live serving, and 10m compaction."""
    client = flask_app.app.test_client()
    mock_keys = {
        'test_key': {
            'upload_sessions': {'drivers': ['Driver A'], 'leagues': ['*']},
            'see_telemetry': True,
        }
    }
    base_meta = {
        'Track name': 'Rowrah',
        'Date': '2026-05-10',
        'Time': '14:30:00',
        'Driver name': 'Driver A',
        'League': 'kartsim',
        'Class': 'cadet',
        'Session': 'Practice',
        'Session ID': '14_30_practice',
    }

    # 1. Reject invalid stream names ("primary" explicitly forbidden; primary must be "" or omitted)
    bad_meta = dict(base_meta, stream='primary')
    bad_bytes = _pack_brt1_stream(
        bad_meta,
        [{'name': 'Time', 'scale': 0.001}, {'name': 'Speed', 'scale': 0.01}],
        [[0.0, 10.0]],
    )
    with patch('auth.load_keys', return_value=mock_keys), patch('upload_handlers.DATA_DIR', str(tmp_path)):
        resp_bad = client.post(
            '/api/upload/stream', data=bad_bytes, headers={'X-API-Key': 'test_key'}
        )
        assert resp_bad.status_code == 400

    # 2. Upload auxiliary EEG stream BEFORE primary stream starts (with a 15s disconnect gap between 1.0s and 16.0s)
    eeg_meta = dict(base_meta, stream='eeg_muse')
    eeg_channels = [{'name': 'Time', 'scale': 0.001}, {'name': 'AlphaPower', 'scale': 0.01}]
    eeg_rows = [
        [0.0, 10.0],
        [0.5, 20.0],
        [1.0, 30.0],
        # 15.0s gap (> 10x median dt disconnect threshold -> midpoint samples must become NaN!)
        [16.0, 70.0],
        [17.0, 80.0],
    ]
    eeg_bytes = _pack_brt1_stream(eeg_meta, eeg_channels, eeg_rows)

    # 3. Upload Primary Segment 1 ("stream" omitted for backward compatibility)
    prim_channels = [
        {'name': 'Time', 'scale': 0.001},
        {'name': 'Latitude', 'scale': 1e-7},
        {'name': 'Longitude', 'scale': 1e-7},
        {'name': 'Speed', 'scale': 0.01},
        {'name': 'Lap', 'scale': 1.0},
        {'name': 'Lap Distance', 'scale': 0.01},
    ]
    prim_rows_1 = [
        [0.0, 54.55100, -3.44200, 10.0, 1.0, 0.0],
        [0.5, 54.55105, -3.44205, 12.0, 1.0, 10.0],
        [1.0, 54.55110, -3.44210, 99.0, 1.0, 20.0],  # Will be overwritten by reconnect segment 2!
    ]
    # Primary Segment 2 reconnects at t=0.75s with explicit stream=""
    prim_meta_2 = dict(base_meta, stream='')
    prim_rows_2 = [
        [0.75, 54.55108, -3.44208, 14.0, 1.0, 15.0],
        [8.0, 54.55120, -3.44220, 16.0, 1.0, 50.0],  # Inside EEG 15s disconnect gap!
        [16.5, 54.55130, -3.44230, 18.0, 1.0, 90.0],  # Halfway between EEG 16.0 (70) and 17.0 (80) -> 75.0!
    ]

    with (
        patch('auth.load_keys', return_value=mock_keys),
        patch('upload_handlers.DATA_DIR', str(tmp_path)),
        patch('upload_handlers.db.get_track', return_value={}),
        patch('upload_handlers.db.load', return_value=pd.DataFrame()),
        patch('upload_handlers.populate_db.populate_single_session', return_value='sess_1430'),
    ):
        r_eeg = client.post(
            '/api/upload/stream',
            data=eeg_bytes,
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'},
        )
        assert r_eeg.status_code == 200

        r_p1 = client.post(
            '/api/upload/stream',
            data=_pack_brt1_stream(base_meta, prim_channels, prim_rows_1, include_eos=False),
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'},
        )
        assert r_p1.status_code == 200

        r_p2 = client.post(
            '/api/upload/stream',
            data=_pack_brt1_stream(prim_meta_2, prim_channels, prim_rows_2, include_eos=True),
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'},
        )
        assert r_p2.status_code == 200

    meeting_dir = tmp_path / 'kartsim' / 'cadet' / '2026_05_10_rowrah'
    tel_dir = meeting_dir / 'telemetry'
    stem = '14_30_practice_driver_a'

    # Zero intermediate CSV files written to disk!
    assert list(tel_dir.glob('*.csv')) == []
    assert (tel_dir / f'{stem}.active' / '_primary').is_dir()
    assert (tel_dir / f'{stem}.active' / 'aux_eeg_muse').is_dir()

    # 4. Serve LIVE uncompacted session via /api/telemetry, /api/telemetry/channel, and /api/telemetry/export_csv
    mock_session = MagicMock()
    mock_session.session_id = '14_30_practice'
    mock_session.session_name = '14:30 Practice'
    mock_session.meeting_dir = str(meeting_dir)

    with (
        patch('location_handlers.auth.get_current_acl', return_value={'see_telemetry': True}),
        patch('location_handlers.auth.can_see_telemetry', return_value=True),
        patch('location_handlers.db.find_sessions', return_value=[mock_session]),
        patch('location_handlers.db.load', return_value=pd.DataFrame()),
        patch('location_handlers.db.get_track', return_value={'sector_end': [], 'turns': []}),
        patch('location_handlers.plot_handlers.get_hero_names', return_value=[]),
    ):
        resp_meta = client.get('/api/telemetry?league=kartsim&class_name=cadet&date=2026-05-10&track=Rowrah')
        assert resp_meta.status_code == 200
        meta_list = resp_meta.get_json()
        assert len(meta_list) == 1
        assert 'AlphaPower' in meta_list[0]['columns']

        # Fetch pruned/merged Speed (* 3.6 -> [36.0, 43.2, 50.4, 57.6, 64.8]; stale 99.0 at t=1.0s pruned!)
        resp_sp = client.get(
            '/api/telemetry/channel?league=kartsim&class_name=cadet'
            '&date=2026-05-10&track=Rowrah&session_id=14_30_practice&channel=Speed'
        )
        assert resp_sp.status_code == 200
        dec_sp = _telemetry_native.decode_channel_blocks(resp_sp.data)
        assert np.allclose(dec_sp, [36.0, 43.2, 50.4, 57.6, 64.8], atol=0.1)

        # Fetch aligned AlphaPower on primary Time [0.0, 0.5, 0.75, 4.0 (gap->NaN), 7.5] -> [10, 20, 25, NaN, 75]
        resp_eeg = client.get(
            '/api/telemetry/channel?league=kartsim&class_name=cadet'
            '&date=2026-05-10&track=Rowrah&session_id=14_30_practice&channel=AlphaPower'
        )
        assert resp_eeg.status_code == 200
        dec_eeg = _telemetry_native.decode_channel_blocks(resp_eeg.data)
        assert len(dec_eeg) == 5
        assert np.allclose(dec_eeg[:3], [10.0, 20.0, 25.0], atol=0.1)
        assert np.isnan(dec_eeg[3])
        assert dec_eeg[4] == pytest.approx(75.0, abs=0.1)

        # On-demand CSV export works without writing any CSV file to disk
        resp_exp = client.get(
            '/api/telemetry/export_csv?league=kartsim&class_name=cadet'
            '&date=2026-05-10&track=Rowrah&session_id=14_30_practice'
        )
        assert resp_exp.status_code == 200
        assert 'AlphaPower' in resp_exp.get_data(as_text=True)
        assert list(tel_dir.glob('*.csv')) == []

    # 5. Compaction daemon respects 10-minute (600s) inactivity window
    now_ts = compaction_daemon.newest_mtime_in_active_dir(str(tel_dir / f'{stem}.active'))
    assert now_ts is not None

    # At +300s (5 minutes idle): must NOT compact
    assert compaction_daemon.scan_and_compact_idle_sessions(
        str(tmp_path), idle_seconds=600.0, now=now_ts + 300.0
    ) == 0
    assert (tel_dir / f'{stem}.active').is_dir()
    assert not (tel_dir / f'{stem}.primary.brtc').exists()

    # At +601s (>10 minutes idle): compacts into .primary.brtc + .aux.eeg_muse.brtc + .derived.brtc
    assert compaction_daemon.scan_and_compact_idle_sessions(
        str(tmp_path), idle_seconds=600.0, now=now_ts + 601.0
    ) == 1
    assert not (tel_dir / f'{stem}.active').exists()
    assert (tel_dir / f'{stem}.primary.brtc').exists()
    assert (tel_dir / f'{stem}.aux.eeg_muse.brtc').exists()
    assert (tel_dir / f'{stem}.derived.brtc').exists()

    # Native cadence preserved in .aux.eeg_muse.brtc (5 native EEG samples, while primary has 5 samples at diff times)
    aux_man = telemetry_brtc.read_brtc_manifest(str(tel_dir / f'{stem}.aux.eeg_muse.brtc'))
    assert aux_man['row_count'] == 5

    # MCP extract_raw_lap_data works seamlessly against compacted .brtc files with zero .csv on disk
    mcp_laps = image_tools.extract_raw_lap_data(
        str(tel_dir / f'{stem}.csv'), [1], ['Speed', 'AlphaPower']
    )
    assert len(mcp_laps[1]['Speed']) == 5
    assert mcp_laps[1]['Speed'][0] == pytest.approx(10.0, abs=0.05)


def test_archive_csv_compaction_deletes_csv_and_recomputes_stale_derived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify compact_sessions migrates + deletes legacy .csv and recompute_channels refreshes stale .derived.brtc."""
    tel_dir = tmp_path / 'club100' / 'cadet' / '2026_08_29_lydd' / 'telemetry'
    tel_dir.mkdir(parents=True)
    stem = '14_59_practice_driver_a'
    csv_file = tel_dir / f'{stem}.csv'
    csv_file.write_text(
        'Format,RaceTools CSV\n'
        'Track name,Lydd\n'
        'Date,2026-08-29\n'
        'Time,14:59:00\n'
        'Driver name,Driver A\n'
        'League,club100\n'
        'Class,cadet\n'
        'Session,Practice\n'
        'Distance to rear axle,0.45\n\n'
        'Record,Time,Latitude,Longitude,Speed,Lap,Lap Distance,Yaw Rate,GForceLat\n'
        '1,2026-08-29T14:59:00.000Z,50.95000,0.90000,18.0,1,0.0,10.0,0.5\n'
        '2,2026-08-29T14:59:00.100Z,50.95010,0.90010,18.5,1,1.8,12.0,0.6\n'
        '3,2026-08-29T14:59:00.200Z,50.95020,0.90020,19.0,1,3.7,14.0,0.7\n',
        encoding='utf-8',
    )

    with patch('telemetry_store.db.get_track', return_value={}):
        matched, compacted = compact_sessions.compact_archive(str(tmp_path), dry_run=False, keep_csv=False)
        assert matched == 1
        assert compacted == 1

        # Legacy .csv is deleted and replaced by .primary.brtc + .derived.brtc
        assert not csv_file.exists()
        prim_p = tel_dir / f'{stem}.primary.brtc'
        deriv_p = tel_dir / f'{stem}.derived.brtc'
        assert prim_p.exists()
        assert deriv_p.exists()

        # Derived channels (Slip Angle Rear, Lat Force Front/Rear) computed cleanly
        d_man_1 = telemetry_brtc.read_brtc_manifest(str(deriv_p))
        assert 'Slip Angle Rear' in d_man_1['columns']
        assert d_man_1['calculators']['gyro_rear_slip']['version'] == 1

        # When fresh, recompute_archive_derived skips recomputation
        inspected, recomputed = recompute_channels.recompute_archive_derived(str(tmp_path), force=False)
        assert inspected == 1
        assert recomputed == 0

        # Bump calculator version -> recompute_archive_derived detects staleness and regenerates .derived.brtc!
        orig_calc = next(c for c in telemetry_calculators.CALCULATOR_REGISTRY if c.name == 'gyro_rear_slip')
        bumped_calc = telemetry_calculators.RegisteredCalculator(
            name=orig_calc.name,
            version=orig_calc.version + 1,
            deps_fn=orig_calc.deps_fn,
            compute_fn=orig_calc.compute_fn,
        )
        patched_registry = [
            bumped_calc if c.name == 'gyro_rear_slip' else c
            for c in telemetry_calculators.CALCULATOR_REGISTRY
        ]
        monkeypatch.setattr(telemetry_calculators, 'CALCULATOR_REGISTRY', patched_registry)

        prim_mtime_before = prim_p.stat().st_mtime_ns
        inspected2, recomputed2 = recompute_channels.recompute_archive_derived(str(tmp_path), force=False)
        assert inspected2 == 1
        assert recomputed2 == 1
        # Immutable .primary.brtc was NOT rewritten
        assert prim_p.stat().st_mtime_ns == prim_mtime_before

        d_man_2 = telemetry_brtc.read_brtc_manifest(str(deriv_p))
        assert d_man_2['calculators']['gyro_rear_slip']['version'] == orig_calc.version + 1


def test_legacy_csv_archive_edge_cases(tmp_path: Path) -> None:
    """Verify synthetic sim row dates, unresolved x/z origins keeping baked GPS, and empty CSV stubs."""
    tel_dir = tmp_path / 'acevo' / 'f1' / '2026_11_11_mod_track' / 'telemetry'
    tel_dir.mkdir(parents=True)

    # 1. Synthetic sim row date (2083-09-21 vs metadata 2026-11-11) + unknown x/z track origin
    sim_stem = '17_20_practice_driver_a'
    sim_csv = tel_dir / f'{sim_stem}.csv'
    sim_csv.write_text(
        'Format,RaceTools CSV\n'
        'Track name,Unregistered Mod Circuit\n'
        'Date,2026-11-11\n'
        'Time,17:20:00\n'
        'Driver name,Driver A\n'
        'League,acevo\n'
        'Class,f1\n'
        'Session,Practice\n\n'
        'Record,Time,x,z,Latitude,Longitude,Speed,Lap,Lap Distance\n'
        '1,2083-09-21T17:20:01.407Z,100.0,200.0,44.3439,11.7167,50.0,1,10.0\n'
        '2,2083-09-21T17:20:01.507Z,105.0,202.0,44.3440,11.7168,51.0,1,15.0\n',
        encoding='utf-8',
    )

    # 2. Header-only 9-line empty CSV stub
    empty_stem = '18_00_practice_driver_b'
    empty_csv = tel_dir / f'{empty_stem}.csv'
    empty_csv.write_text(
        'Format,RaceTools CSV\n'
        'Track name,Monza\n'
        'Date,2026-11-11\n'
        'Time,18:00:00\n'
        'Driver name,Driver B\n'
        'League,kartsim\n'
        'Class,cadet\n'
        'Session,Practice\n\n',
        encoding='utf-8',
    )

    with patch('telemetry_store.db.get_track', return_value={}):
        matched, compacted = compact_sessions.compact_archive(str(tmp_path), dry_run=False, keep_csv=False)
        assert matched == 2
        assert compacted == 2
        assert not sim_csv.exists()
        assert not empty_csv.exists()

        # Verify synthetic row date anchored to row day (17:20:01.407 = 62401.407s) & metadata date preserved
        sim_man = telemetry_brtc.read_brtc_manifest(str(tel_dir / f'{sim_stem}.primary.brtc'))
        assert sim_man['metadata']['session_start_date'] == '2026-11-11'
        assert 'Latitude' in sim_man['columns']
        assert 'Longitude' in sim_man['columns']
        t_col = telemetry_brtc.read_brtc_column(str(tel_dir / f'{sim_stem}.primary.brtc'), 'Time')
        assert t_col is not None
        assert np.allclose(t_col, [62401.407, 62401.507], atol=1e-3)

        # Verify empty stub compacted to 0-row .brtc with driver_name preserved and no pending candidates left
        assert telemetry_store.extract_driver_name(str(tel_dir), empty_stem) == 'Driver B'
        laps, cols, drv = telemetry_store.load_session_laps_and_columns(str(tel_dir), empty_stem)
        assert laps == []
        assert cols == []
        assert drv == 'Driver B'
        matched_after, _ = compact_sessions.compact_archive(str(tmp_path), dry_run=True)
        assert matched_after == 0
