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

import struct
import gzip
import brotli  # type: ignore[import-untyped]
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
from flask import Flask

import location_handlers
import app as flask_app


class MockSession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


def test_get_display_name() -> None:
    assert location_handlers._get_display_name('14_59_practice_alice_driver.csv') == '14:59 Practice'
    assert location_handlers._get_display_name('10_00_qualifying.csv') == '10:00 Qualifying'
    assert location_handlers._get_display_name('simple.csv') == 'simple.csv'
    assert location_handlers._get_display_name('12_30_final_race.csv') == '12:30 Final'


def test_match_session() -> None:
    sessions = [MockSession('p1'), MockSession('p1_long'), MockSession('qualifying')]
    assert location_handlers._match_session('p1_driver.csv', sessions) == 'p1'
    assert location_handlers._match_session('p1_long_driver.csv', sessions) == 'p1_long'
    assert location_handlers._match_session('qualifying.csv', sessions) == 'qualifying'
    assert location_handlers._match_session('unknown.csv', sessions) is None

    # Test underscore boundary
    assert location_handlers._match_session('p1_2_driver.csv', sessions) == 'p1'


def test_override_with_official_laps() -> None:
    laps = [
        {'lap_num': 1, 'lap_time': '1:00.000'},
        {'lap_num': 2, 'lap_time': '1:01.000'}
    ]
    df_official = pd.DataFrame([
        {'SessionID': 'S1', 'Name': 'Driver A', 'Lap': 1, 'LapTime': '0:59.000'},
        {'SessionID': 'S1', 'Name': 'Driver A', 'Lap': 2, 'LapTimeSeconds': 60.5}
    ])

    location_handlers._override_with_official_laps(laps, df_official, 'S1', 'Driver A')

    assert laps[0]['lap_time'] == '0:59.000'
    assert laps[1]['lap_time'] == '1:00.500'

    # Test case-insensitivity
    laps_2 = [{'lap_num': 1, 'lap_time': '1:00.000'}]
    location_handlers._override_with_official_laps(laps_2, df_official, 'S1', 'driver a')
    assert laps_2[0]['lap_time'] == '0:59.000'


def test_compute_lap_segments() -> None:
    laps = [{
        'points': [
            {'dist': 0.0, 'time': 0.0},
            {'dist': 100.0, 'time': 10.0},
            {'dist': 200.0, 'time': 20.0},
            {'dist': 300.0, 'time': 30.0},
        ],
        'dists': [0.0, 100.0, 200.0, 300.0],
        'times': [0.0, 10.0, 20.0, 30.0]
    }]
    sector_ends = [150.0, 300.0]
    turns = [
        {'name': 'T1', 'end': 100.0},
        {'name': 'T2', 'end': 200.0},
        {'name': 'T3', 'end': 300.0}
    ]

    location_handlers._compute_lap_segments(laps, sector_ends, turns)

    # Sector 1: 0 to 150. Time at 150 is 15. Duration 15-0 = 15.
    # Sector 2: 150 to 300. Time at 300 is 30. Duration 30-15 = 15.
    assert laps[0]['sector_times'] == [15.0, 15.0]

    # Turn 1: ends at 100. Prev turn end is 300 (T3).
    # T3 end time is 30. T1 end time is 10.
    # dt = 10 - 30 = -20. Wrap around: -20 + 30 = 10.
    # Turn 2: ends at 200. Prev is T1 at 100. dt = 20 - 10 = 10.
    # Turn 3: ends at 300. Prev is T2 at 200. dt = 30 - 20 = 10.
    assert laps[0]['turn_times'] == [10.0, 10.0, 10.0]


def test_compute_lap_segments_with_starts() -> None:
    laps = [{
        'points': [
            {'dist': 0.0, 'time': 0.0},
            {'dist': 100.0, 'time': 10.0},
            {'dist': 200.0, 'time': 20.0},
            {'dist': 300.0, 'time': 30.0},
        ],
        'dists': [0.0, 100.0, 200.0, 300.0],
        'times': [0.0, 10.0, 20.0, 30.0]
    }]
    sector_ends = [150.0, 300.0]
    turns = [
        {'name': 'T1', 'start': 20.0, 'end': 80.0},
        {'name': 'T2', 'start': 120.0, 'end': 180.0},
        {'name': 'T3', 'start': 220.0, 'end': 280.0}
    ]

    location_handlers._compute_lap_segments(laps, sector_ends, turns)

    # Turn 1: spans [T1.start, T2.start] = [20.0, 120.0].
    # Time at 20 is 2.0. Time at 120 is 12.0. Duration = 10.0.
    # Turn 2: spans [T2.start, T3.start] = [120.0, 220.0].
    # Time at 120 is 12.0. Time at 220 is 22.0. Duration = 10.0.
    # Turn 3: spans [T3.start, T1.start] = [220.0, 20.0].
    # Time at 220 is 22.0. Time at 20 is 2.0. Wrap around: (2.0 - 22.0) + 30.0 = 10.0.
    assert laps[0]['turn_times'] == [10.0, 10.0, 10.0]


def test_compute_lap_segments_no_turns() -> None:
    laps = [{
        'points': [
            {'dist': 0.0, 'time': 0.0},
            {'dist': 100.0, 'time': 10.0},
        ],
        'dists': [0.0, 100.0],
        'times': [0.0, 10.0]
    }]
    location_handlers._compute_lap_segments(laps, [100.0], [])
    assert laps[0]['sector_times'] == [10.0]
    assert laps[0]['turn_times'] == []


def test_compute_lap_segments_empty_points() -> None:
    laps: list[dict[str, Any]] = [{'dists': [], 'times': []}]
    location_handlers._compute_lap_segments(laps, [100.0], [])
    assert 'sector_times' not in laps[0]


@patch('location_handlers.db')
@patch('location_handlers.plot_handlers')
@patch('location_handlers.os.path.exists')
@patch('location_handlers.os.listdir')
@patch('location_handlers.parse_telemetry_csv')
@patch('auth.get_current_acl')
def test_get_track_points_route(
    mock_get_current_acl: MagicMock,
    mock_parse: MagicMock,
    mock_listdir: MagicMock,
    mock_exists: MagicMock,
    mock_plot_handlers: MagicMock,
    mock_db: MagicMock
) -> None:
    app = Flask(__name__)
    app.register_blueprint(location_handlers.location_blueprint)
    client = app.test_client()

    mock_get_current_acl.return_value = {'see_telemetry': True}

    # Mocking DB sessions
    mock_session = MagicMock()
    mock_session.meeting_dir = '/fake/meeting'
    mock_session.session_id = 'S1'
    mock_db.find_sessions.return_value = [mock_session]

    # Mocking plot_handlers
    mock_plot_handlers.get_hero_names.return_value = ['Hero A']

    # Mocking file system
    mock_exists.return_value = True  # For telemetry dir
    mock_listdir.return_value = ['S1_hero_a.csv']

    # Mocking CSV parsing
    mock_parse.return_value = (
        [{'lap_num': 1, 'lap_time': '1:00.000', 'start_idx': 0, 'end_idx': 9, 'dists': [0.0], 'times': [0.0]}],
        ['Latitude', 'Longitude'],
        'Hero A'
    )

    # Mocking other DB calls
    mock_db.load.return_value = pd.DataFrame()
    mock_db.base_dir = '/fake'

    # Mocking get_track in DB
    mock_db.get_track.return_value = {'sector_end': [500], 'turns': []}

    response = client.get('/api/telemetry?league=kartsim&class_name=X30&date=2026-05-10&track=Rowrah')

    assert response.status_code == 200
    data = response.get_json()
    assert len(data) == 1
    assert data[0]['session_id'] == 'S1'
    assert data[0]['laps'][0]['lap_num'] == 1
    assert data[0]['columns'] == ['Latitude', 'Longitude']

    # Test with matching session_id filter
    url_match = '/api/telemetry?league=kartsim&class_name=X30&date=2026-05-10&track=Rowrah&session_id=S1'
    response_filter_match = client.get(url_match)
    assert response_filter_match.status_code == 200
    assert len(response_filter_match.get_json()) == 1

    # Test with non-matching session_id filter
    url_no_match = '/api/telemetry?league=kartsim&class_name=X30&date=2026-05-10&track=Rowrah&session_id=S2'
    response_filter_no_match = client.get(url_no_match)
    assert response_filter_no_match.status_code == 200
    assert len(response_filter_no_match.get_json()) == 0

    # Verify that requesting without key parameters returns 400 Bad Request
    response_no_param = client.get('/api/telemetry?league=kartsim&class_name=X30&date=2026-05-10')
    assert response_no_param.status_code == 400


def test_parse_telemetry_csv(tmp_path: Path) -> None:
    # 1. Test Old format (Record is first, Configuration is used)
    old_csv_content = """Format,RaceBox CSV
Data Source,KartSim
Configuration,Alice Driver

Record,Time,Latitude,Longitude,Lap
1,2026-03-28T15:29:05.916Z,51.86522138,-1.68510367,1
2,2026-03-28T15:29:06.116Z,51.86523913,-1.68513932,1
"""
    old_file = tmp_path / "old.csv"
    old_file.write_text(old_csv_content)

    laps, columns, driver_name = location_handlers.parse_telemetry_csv(str(old_file))
    assert driver_name == "Alice Driver"
    assert len(laps) == 1
    assert laps[0]['lap_num'] == 1
    assert laps[0]['start_idx'] == 0
    assert laps[0]['end_idx'] == 1
    assert columns == ['Record', 'Time', 'Latitude', 'Longitude', 'Lap']

    # 2. Test New format (Time is first, Record is in the middle, Driver name is used)
    new_csv_content = """Format,RaceTools CSV
Driver name,Alexey

Time,Latitude,Longitude,Record,Lap
2026-06-10T17:02:32.427Z,51.86439811,-1.68407354,1,1
2026-06-10T17:02:32.436Z,51.86439811,-1.68407354,2,1
"""
    new_file = tmp_path / "new.csv"
    new_file.write_text(new_csv_content)

    laps, columns, driver_name = location_handlers.parse_telemetry_csv(str(new_file))
    assert driver_name == "Alexey"
    assert len(laps) == 1
    assert laps[0]['lap_num'] == 1
    assert laps[0]['start_idx'] == 0
    assert laps[0]['end_idx'] == 1
    assert columns == ['Time', 'Latitude', 'Longitude', 'Record', 'Lap']

    # 3. Test Start-to-Start timing across adjacent laps
    multi_lap_csv = """Format,RaceTools CSV
Driver name,Katia

Time,Latitude,Longitude,Record,Lap
2026-08-02T12:30:00.000Z,51.0,-3.0,1,1
2026-08-02T12:30:50.000Z,51.0,-3.0,2,1
2026-08-02T12:30:56.000Z,51.0,-3.0,3,2
2026-08-02T12:31:52.000Z,51.0,-3.0,4,2
2026-08-02T12:31:52.100Z,51.0,-3.0,5,3
"""
    multi_file = tmp_path / "multi.csv"
    multi_file.write_text(multi_lap_csv)

    laps_multi, _, _ = location_handlers.parse_telemetry_csv(str(multi_file))
    assert len(laps_multi) == 3
    # Lap 1 start-to-start duration: 12:30:56.000 - 12:30:00.000 = 56.0s
    assert laps_multi[0]['lap_time'] == "56.000"
    assert np.isclose(laps_multi[0]['duration'], 56.0)
    # Lap 2 start-to-start duration: 12:31:52.100 - 12:30:56.000 = 56.1s
    assert laps_multi[1]['lap_time'] == "56.100"
    assert np.isclose(laps_multi[1]['duration'], 56.1)
    # Lap 3 final lap fallback duration: 12:31:52.100 - 12:31:52.100 = 0.0s (1 point)
    assert laps_multi[2]['lap_time'] == "Unknown"


@patch('location_handlers.db')
def test_get_telemetry_channel_route(
    mock_db: MagicMock,
    tmp_path: Path
) -> None:
    app = Flask(__name__)
    app.register_blueprint(location_handlers.location_blueprint)
    client = app.test_client()

    # Create dummy CSV inside telemetry folder
    telemetry_dir = tmp_path / "telemetry"
    telemetry_dir.mkdir(parents=True, exist_ok=True)

    csv_content = """Format,RaceTools CSV
Driver name,Alexey

Time,Latitude,Longitude,Record,Lap,Steering Wheel Angle (deg)
2026-06-10T17:02:32.427Z,51.86439811,-1.68407354,1,1,12.5
2026-06-10T17:02:32.436Z,51.86439811,-1.68407354,2,1,14.2
"""
    test_file = telemetry_dir / "S1_hero_a.csv"
    test_file.write_text(csv_content)

    mock_session = MagicMock()
    mock_session.meeting_dir = str(tmp_path)
    mock_db.find_sessions.return_value = [mock_session]

    url = (
        '/api/telemetry/channel?league=kartsim&class_name=X30'
        '&date=2026-05-10&track=Rowrah&session_id=S1_hero_a.csv&channel=Steering Wheel Angle (deg)'
    )
    response = client.get(url)
    assert response.status_code == 200

    # Decode the binary response
    mean_val, scale = struct.unpack("<dd", response.data[:16])
    deltas = np.frombuffer(response.data[16:], dtype=np.int16)

    reconstructed = []
    curr = 0
    for d in deltas:
        if d == -32768:
            reconstructed.append(np.nan)
        else:
            curr += d
            reconstructed.append(mean_val + curr * scale)

    assert np.allclose(reconstructed, [12.5, 14.2], atol=1e-5)

    # Test exact column matching
    csv_speed_content = """Format,RaceTools CSV
Driver name,Alexey

Time,Latitude,Longitude,Record,Lap,Speed (m/s)
2026-06-10T17:02:32.427Z,51.86439811,-1.68407354,1,1,10.0
2026-06-10T17:02:32.436Z,51.86439811,-1.68407354,2,1,20.0
"""
    test_file.write_text(csv_speed_content)
    url_speed = (
        '/api/telemetry/channel?league=kartsim&class_name=X30'
        '&date=2026-05-10&track=Rowrah&session_id=S1_hero_a.csv&channel=Speed (m/s)'
    )
    response_speed = client.get(url_speed)
    assert response_speed.status_code == 200

    mean_val_speed, scale_speed = struct.unpack("<dd", response_speed.data[:16])
    deltas_speed = np.frombuffer(response_speed.data[16:], dtype=np.int16)

    reconstructed_speed = []
    curr = 0
    for d in deltas_speed:
        if d == -32768:
            reconstructed_speed.append(np.nan)
        else:
            curr += d
            reconstructed_speed.append(mean_val_speed + curr * scale_speed)

    assert np.allclose(reconstructed_speed, [36.0, 72.0], atol=1e-5)

    # Test 'Speed' column matching (when column in CSV is named 'Speed')
    csv_speed_no_units_content = """Format,RaceTools CSV
Driver name,Alexey

Time,Latitude,Longitude,Record,Lap,Speed
2026-06-10T17:02:32.427Z,51.86439811,-1.68407354,1,1,10.0
2026-06-10T17:02:32.436Z,51.86439811,-1.68407354,2,1,20.0
"""
    test_file.write_text(csv_speed_no_units_content)
    url_speed_no_units = (
        '/api/telemetry/channel?league=kartsim&class_name=X30'
        '&date=2026-05-10&track=Rowrah&session_id=S1_hero_a.csv&channel=Speed'
    )
    response_speed_no_units = client.get(url_speed_no_units)
    assert response_speed_no_units.status_code == 200

    mean_val_speed_no_units, scale_speed_no_units = struct.unpack("<dd", response_speed_no_units.data[:16])
    deltas_speed_no_units = np.frombuffer(response_speed_no_units.data[16:], dtype=np.int16)

    reconstructed_speed_no_units = []
    curr = 0
    for d in deltas_speed_no_units:
        if d == -32768:
            reconstructed_speed_no_units.append(np.nan)
        else:
            curr += d
            reconstructed_speed_no_units.append(mean_val_speed_no_units + curr * scale_speed_no_units)

    assert np.allclose(reconstructed_speed_no_units, [36.0, 72.0], atol=1e-5)

    # Test NaN / missing values
    csv_nan_content = """Format,RaceTools CSV
Driver name,Alexey

Time,Latitude,Longitude,Record,Lap,Steering Wheel Angle (deg)
2026-06-10T17:02:32.427Z,51.86439811,-1.68407354,1,1,12.5
2026-06-10T17:02:32.436Z,51.86439811,-1.68407354,2,1,
2026-06-10T17:02:32.445Z,51.86439811,-1.68407354,3,1,14.2
"""
    test_file.write_text(csv_nan_content)
    response_nan = client.get(url)
    assert response_nan.status_code == 200

    mean_val_nan, scale_nan = struct.unpack("<dd", response_nan.data[:16])
    deltas_nan = np.frombuffer(response_nan.data[16:], dtype=np.int16)

    reconstructed_nan = []
    curr = 0
    for d in deltas_nan:
        if d == -32768:
            reconstructed_nan.append(np.nan)
        else:
            curr += d
            reconstructed_nan.append(mean_val_nan + curr * scale_nan)

    assert len(reconstructed_nan) == 3
    assert np.isclose(reconstructed_nan[0], 12.5, atol=1e-5)
    assert np.isnan(reconstructed_nan[1])
    assert np.isclose(reconstructed_nan[2], 14.2, atol=1e-5)


@patch('location_handlers.db')
@patch('auth.get_current_acl')
def test_get_telemetry_channel_security(
    mock_get_current_acl: MagicMock,
    mock_db: MagicMock,
    tmp_path: Path
) -> None:
    app = Flask(__name__)
    app.register_blueprint(location_handlers.location_blueprint)
    client = app.test_client()

    telemetry_dir = tmp_path / "telemetry"
    telemetry_dir.mkdir(parents=True, exist_ok=True)

    csv_content = """Format,RaceTools CSV
Driver name,Alexey

Time,Latitude,Longitude,Record,Lap
2026-06-10T17:02:32.427Z,51.86439811,-1.68407354,1,1
"""
    test_file = telemetry_dir / "S1_hero_a.csv"
    test_file.write_text(csv_content)

    mock_session = MagicMock()
    mock_session.meeting_dir = str(tmp_path)
    mock_db.find_sessions.return_value = [mock_session]
    mock_get_current_acl.return_value = {'see_telemetry': True}

    # 1. Test directory traversal attempt
    url_traversal = (
        '/api/telemetry/channel?league=kartsim&class_name=X30'
        '&date=2026-05-10&track=Rowrah&session_id=../secrets.txt&channel=Time'
    )
    response = client.get(url_traversal)
    assert response.status_code == 403
    assert b"Unauthorized or invalid session ID" in response.data

    # 2. Test see_telemetry permission restriction
    mock_get_current_acl.return_value = {'see_telemetry': False}
    url_restricted = (
        '/api/telemetry/channel?league=kartsim&class_name=X30'
        '&date=2026-05-10&track=Rowrah&session_id=S1_hero_a.csv&channel=Time'
    )
    response = client.get(url_restricted)
    assert response.status_code == 403
    assert b"Access to the telemetry data is restricted" in response.data


@patch('location_handlers.db')
@patch('auth.get_current_acl')
def test_get_track_points_security(
    mock_get_current_acl: MagicMock,
    mock_db: MagicMock
) -> None:
    app = Flask(__name__)
    app.register_blueprint(location_handlers.location_blueprint)
    client = app.test_client()

    mock_get_current_acl.return_value = {'see_telemetry': False}

    response = client.get('/api/telemetry?league=kartsim&class_name=X30&date=2026-05-10&track=Rowrah')
    assert response.status_code == 403
    assert b"Access to the telemetry data is restricted" in response.data


@patch('location_handlers.db')
@patch('auth.get_current_acl')
def test_get_telemetry_channel_brotli_and_gzip(
    mock_get_current_acl: MagicMock,
    mock_db: MagicMock,
    tmp_path: Path
) -> None:
    app = Flask(__name__)
    app.register_blueprint(location_handlers.location_blueprint)
    client = app.test_client()

    telemetry_dir = tmp_path / "telemetry"
    telemetry_dir.mkdir(parents=True, exist_ok=True)

    csv_content = """Format,RaceTools CSV
Driver name,Alexey

Time,Latitude,Longitude,Record,Lap,Steering Wheel Angle (deg)
2026-06-10T17:02:32.427Z,51.86439811,-1.68407354,1,1,12.5
2026-06-10T17:02:32.436Z,51.86439811,-1.68407354,2,1,14.2
"""
    test_file = telemetry_dir / "S1_hero_a.csv"
    test_file.write_text(csv_content)

    mock_session = MagicMock()
    mock_session.meeting_dir = str(tmp_path)
    mock_db.find_sessions.return_value = [mock_session]
    mock_get_current_acl.return_value = {'see_telemetry': True}

    url = (
        '/api/telemetry/channel?league=kartsim&class_name=X30'
        '&date=2026-05-10&track=Rowrah&session_id=S1_hero_a.csv&channel=Steering Wheel Angle (deg)'
    )

    # 1. Test Brotli compression when client accepts it
    headers_br = {'Accept-Encoding': 'br, gzip'}
    response_br = client.get(url, headers=headers_br)
    assert response_br.status_code == 200
    assert response_br.headers.get('Content-Encoding') == 'br'

    decompressed_br = brotli.decompress(response_br.data)
    mean_val, scale = struct.unpack("<dd", decompressed_br[:16])
    deltas = np.frombuffer(decompressed_br[16:], dtype=np.int16)
    assert len(deltas) == 2

    # 2. Test fallback to Gzip when client only accepts gzip
    headers_gzip = {'Accept-Encoding': 'gzip'}
    response_gzip = client.get(url, headers=headers_gzip)
    assert response_gzip.status_code == 200
    assert response_gzip.headers.get('Content-Encoding') == 'gzip'

    decompressed_gzip = gzip.decompress(response_gzip.data)
    mean_val, scale = struct.unpack("<dd", decompressed_gzip[:16])
    deltas = np.frombuffer(decompressed_gzip[16:], dtype=np.int16)
    assert len(deltas) == 2

    # 3. Test uncompressed when client does not accept gzip or br
    headers_none = {'Accept-Encoding': 'identity'}
    response_none = client.get(url, headers=headers_none)
    assert response_none.status_code == 200
    assert 'Content-Encoding' not in response_none.headers

    mean_val, scale = struct.unpack("<dd", response_none.data[:16])
    deltas = np.frombuffer(response_none.data[16:], dtype=np.int16)
    assert len(deltas) == 2


def test_should_smooth_channel() -> None:
    assert location_handlers._should_smooth_channel("GForceLat") is True
    assert location_handlers._should_smooth_channel("GForceLon") is True
    assert location_handlers._should_smooth_channel("GForceVert") is True
    assert location_handlers._should_smooth_channel("Slide Pct FL") is True
    assert location_handlers._should_smooth_channel("Lat Force FL") is True
    assert location_handlers._should_smooth_channel("Tyre Load FL") is True
    assert location_handlers._should_smooth_channel("Steering Angle") is False
    assert location_handlers._should_smooth_channel("Speed") is False
    assert location_handlers._should_smooth_channel("RPS FL") is False


def test_smooth_telemetry_data() -> None:
    # 1. Standard test
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    smoothed = location_handlers.smooth_telemetry_data(vals)
    assert np.allclose(smoothed, [2.0, 2.5, 3.0, 3.5, 4.0])

    # 2. Test with NaN values
    vals_nan = [1.0, 2.0, np.nan, 4.0, 5.0]
    smoothed_nan = location_handlers.smooth_telemetry_data(vals_nan)
    assert np.isclose(smoothed_nan[0], 1.5)
    assert np.isclose(smoothed_nan[1], 7.0 / 3.0)
    assert np.isnan(smoothed_nan[2])
    assert np.isclose(smoothed_nan[3], 11.0 / 3.0)
    assert np.isclose(smoothed_nan[4], 4.5)


@patch('location_handlers.db')
@patch('auth.get_current_acl')
def test_get_telemetry_channel_smoothing(
    mock_get_current_acl: MagicMock,
    mock_db: MagicMock,
    tmp_path: Path
) -> None:
    app = Flask(__name__)
    app.register_blueprint(location_handlers.location_blueprint)
    client = app.test_client()

    telemetry_dir = tmp_path / "telemetry"
    telemetry_dir.mkdir(parents=True, exist_ok=True)

    csv_content = """Format,RaceTools CSV
Driver name,Alexey

Time,Latitude,Longitude,Record,Lap,GForceLat
2026-06-10T17:02:32.427Z,51.86439811,-1.68407354,1,1,1.0
2026-06-10T17:02:32.436Z,51.86439811,-1.68407354,2,1,2.0
2026-06-10T17:02:32.445Z,51.86439811,-1.68407354,3,1,3.0
2026-06-10T17:02:32.454Z,51.86439811,-1.68407354,4,1,4.0
2026-06-10T17:02:32.463Z,51.86439811,-1.68407354,5,1,5.0
"""
    test_file = telemetry_dir / "S1_hero_a.csv"
    test_file.write_text(csv_content)

    mock_session = MagicMock()
    mock_session.meeting_dir = str(tmp_path)
    mock_db.find_sessions.return_value = [mock_session]
    mock_get_current_acl.return_value = {'see_telemetry': True}

    # Case A: league is kartsim (should smooth: [2.0, 2.5, 3.0, 3.5, 4.0])
    url_smooth = (
        '/api/telemetry/channel?league=kartsim&class_name=X30'
        '&date=2026-05-10&track=Rowrah&session_id=S1_hero_a.csv&channel=GForceLat'
    )
    response_smooth = client.get(url_smooth)
    assert response_smooth.status_code == 200

    mean_val, scale = struct.unpack("<dd", response_smooth.data[:16])
    deltas = np.frombuffer(response_smooth.data[16:], dtype=np.int16)
    reconstructed = []
    curr = 0
    for d in deltas:
        if d == -32768:
            reconstructed.append(np.nan)
        else:
            curr += d
            reconstructed.append(mean_val + curr * scale)

    assert np.allclose(reconstructed, [2.0, 2.5, 3.0, 3.5, 4.0], atol=1e-5)

    # Case B: league is NOT kartsim (should NOT smooth: [1.0, 2.0, 3.0, 4.0, 5.0])
    url_no_smooth = (
        '/api/telemetry/channel?league=other_league&class_name=X30'
        '&date=2026-05-10&track=Rowrah&session_id=S1_hero_a.csv&channel=GForceLat'
    )
    response_no_smooth = client.get(url_no_smooth)
    assert response_no_smooth.status_code == 200

    mean_val, scale = struct.unpack("<dd", response_no_smooth.data[:16])
    deltas = np.frombuffer(response_no_smooth.data[16:], dtype=np.int16)
    reconstructed = []
    curr = 0
    for d in deltas:
        if d == -32768:
            reconstructed.append(np.nan)
        else:
            curr += d
            reconstructed.append(mean_val + curr * scale)
    assert np.allclose(reconstructed, [1.0, 2.0, 3.0, 4.0, 5.0], atol=1e-5)


def test_get_track_progression() -> None:
    app = Flask(__name__)
    app.register_blueprint(location_handlers.location_blueprint)
    app.register_blueprint(location_handlers.plot_handlers.plots_blueprint)
    client = app.test_client()

    response = client.get('/api/track_progression')
    assert response.status_code == 400

    s_dry = MagicMock()
    s_dry.track_conditions = 'Dry'
    s_damp = MagicMock()
    s_damp.track_conditions = 'Damp'

    with patch('location_handlers.db.find_sessions', return_value=[s_dry, s_damp]):
        res = client.get('/api/track_progression?league=kartsim&class_name=iame&track=Dunkeswell&date=2026-07-29')
        assert res.status_code == 200
        data = res.get_json()
        assert 'plots' in data
        assert len(data['plots']) == 2
        assert data['plots'][0]['condition'] == 'Dry'
        assert data['plots'][1]['condition'] == 'Damp'
        assert 'Dry Conditions' in data['plots'][0]['title']
        assert 'Damp Conditions' in data['plots'][1]['title']


def test_telemetry_report_view() -> None:
    app = Flask(__name__, template_folder='../templates')
    app.register_blueprint(location_handlers.location_blueprint)
    client = app.test_client()

    # 404 for missing report
    with patch('location_handlers.report_parser.load_report', return_value=None):
        resp = client.get('/telemetry/report/non_existent_report')
        assert resp.status_code == 404

    # 400 for report missing league/track metadata
    with patch(
        'location_handlers.report_parser.load_report',
        return_value=({"title": "Incomplete"}, {}, "<p>content</p>")
    ):
        resp = client.get('/telemetry/report/incomplete_report')
        assert resp.status_code == 400

    # Successful report rendering
    mock_meta = {
        "title": "Turn 4 Hairpin",
        "league": "kartsim",
        "class_name": "cadet",
        "date": "2026-08-21",
        "track": "Clay Pigeon",
        "session_id": "18_09_practice"
    }
    mock_state = {
        "tab": "map",
        "sort": "turn",
        "turn": 2,
        "lapsA": ["lap-6"]
    }
    mock_body = "<div class='report-test-content'>Hairpin deep dive</div>"
    mock_session = MagicMock()
    mock_session.session_id = '18_09_practice'
    mock_session.meeting_dir = '/tmp/fake_meeting'

    with patch('location_handlers.report_parser.load_report', return_value=(mock_meta, mock_state, mock_body)), \
         patch('location_handlers.db.find_sessions', return_value=[mock_session]), \
         patch('location_handlers.db.load', return_value=pd.DataFrame()), \
         patch('location_handlers.db.load_penalties', return_value=pd.DataFrame()), \
         patch('location_handlers.db.list_meetings', return_value=[]), \
         patch('location_handlers.db.has_telemetry', return_value=False), \
         patch('location_handlers.render_template', return_value="RENDERED_REPORT") as mock_render:

        resp = client.get('/telemetry/report/clay_pigeon_v2_hairpin')
        assert resp.status_code == 200
        assert resp.data.decode('utf-8') == "RENDERED_REPORT"
        mock_render.assert_called_once()
        call_kwargs = mock_render.call_args[1]
        assert call_kwargs['is_report_mode'] is True
        assert call_kwargs['report_title'] == "Turn 4 Hairpin"
        assert call_kwargs['report_html'] == mock_body
        assert call_kwargs['report_state'] == mock_state


def test_telemetry_view_with_report_param() -> None:
    app = Flask(__name__, template_folder='../templates')
    app.register_blueprint(location_handlers.location_blueprint)
    client = app.test_client()

    mock_meta = {
        "title": "Turn 4 Hairpin",
        "league": "kartsim",
        "class_name": "cadet",
        "date": "2026-08-21",
        "track": "Clay Pigeon"
    }
    mock_state = {"lapsA": ["lap-6"]}
    mock_body = "<p>Report html</p>"
    mock_session = MagicMock()
    mock_session.session_id = '18_09_practice'
    mock_session.meeting_dir = '/tmp/fake_meeting'

    with patch('location_handlers.report_parser.load_report', return_value=(mock_meta, mock_state, mock_body)), \
         patch('location_handlers.db.find_sessions', return_value=[mock_session]), \
         patch('location_handlers.db.load', return_value=pd.DataFrame()), \
         patch('location_handlers.db.load_penalties', return_value=pd.DataFrame()), \
         patch('location_handlers.db.list_meetings', return_value=[]), \
         patch('location_handlers.db.has_telemetry', return_value=False), \
         patch('location_handlers.render_template', return_value="RENDERED_WITH_REPORT") as mock_render:

        resp = client.get('/telemetry/kartsim/cadet/2026-08-21/Clay%20Pigeon?report=clay_pigeon_v2_hairpin')
        assert resp.status_code == 200
        assert resp.data.decode('utf-8') == "RENDERED_WITH_REPORT"
        call_kwargs = mock_render.call_args[1]
        assert call_kwargs['is_report_mode'] is True
        assert call_kwargs['report_title'] == "Turn 4 Hairpin"
        assert call_kwargs['report_html'] == mock_body


def test_telemetry_report_content_view() -> None:
    app = Flask(__name__, template_folder='../templates')
    app.register_blueprint(location_handlers.location_blueprint)
    client = app.test_client()

    # 404 for missing report
    with patch('location_handlers.report_parser.load_report', return_value=None):
        resp = client.get('/telemetry/report_content/missing_report')
        assert resp.status_code == 404

    # Fragment wraps in full HTML document with telemetry_report.css
    mock_meta: dict[str, Any] = {"title": "Fragment Report"}
    mock_state: dict[str, Any] = {}
    mock_body = "<div class='test-fragment'>Fragment Body</div>"

    with patch('location_handlers.report_parser.load_report', return_value=(mock_meta, mock_state, mock_body)):
        resp = client.get('/telemetry/report_content/sample_fragment')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert "telemetry_report.css" in html
        assert "<div class='test-fragment'>Fragment Body</div>" in html
        assert "window.parent.postMessage" in html


def test_telemetry_view_session_selector_time(tmp_path: Path) -> None:
    client = flask_app.app.test_client()

    meeting_dir = tmp_path / "meeting"
    meeting_dir.mkdir()
    telemetry_dir = meeting_dir / "telemetry"
    telemetry_dir.mkdir()
    sample_csv = (
        "Format,RaceBox CSV\nData Source,KartSim\nConfiguration,Alice\n\n"
        "Record,Time,Latitude,Longitude,Lap\n1,2026-08-29T14:59:00Z,50.0,-1.0,1\n"
    )
    (telemetry_dir / "14_59_practice.csv").write_text(sample_csv)
    (telemetry_dir / "18_09_practice.csv").write_text(sample_csv)

    s1 = MagicMock()
    s1.session_id = '14_59_practice'
    s1.session_name = 'Practice'
    s1.session_start_datetime = '2026-08-29 14:59'
    s1.meeting_dir = str(meeting_dir)

    s2 = MagicMock()
    s2.session_id = '18_09_practice'
    s2.session_name = 'Practice'
    s2.session_start_datetime = '2026-08-29 18:09'
    s2.meeting_dir = str(meeting_dir)

    with patch('location_handlers.db.find_sessions', return_value=[s1, s2]), \
         patch('location_handlers.db.load', return_value=pd.DataFrame()), \
         patch('location_handlers.db.load_penalties', return_value=pd.DataFrame()), \
         patch('location_handlers.db.list_meetings', return_value=[]), \
         patch('location_handlers.db.has_telemetry', return_value=True):
        resp = client.get('/telemetry/kartsim/cadet/2026-08-29/Clay%20Pigeon')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert '14:59 Practice' in html
        assert '18:09 Practice' in html


def test_telemetry_view_filter_track_conditions(tmp_path: Path) -> None:
    client = flask_app.app.test_client()

    meeting_dir = tmp_path / "meeting"
    meeting_dir.mkdir()
    telemetry_dir = meeting_dir / "telemetry"
    telemetry_dir.mkdir()
    sample_csv = (
        "Format,RaceBox CSV\nData Source,KartSim\nConfiguration,Alice\n\n"
        "Record,Time,Latitude,Longitude,Lap\n1,2026-08-29T14:59:00Z,50.0,-1.0,1\n"
    )
    (telemetry_dir / "14_59_practice.csv").write_text(sample_csv)

    s1 = MagicMock()
    s1.session_id = '14_59_practice'
    s1.session_name = 'Practice'
    s1.session_start_datetime = '2026-08-29 14:59'
    s1.track_conditions = 'Dry'
    s1.meeting_dir = str(meeting_dir)

    def find_sessions_side_effect(**kwargs: Any) -> list[MagicMock]:
        if kwargs.get('track_conditions') == 'Dry':
            return [s1]
        return []

    with patch('location_handlers.db.find_sessions', side_effect=find_sessions_side_effect), \
         patch('location_handlers.db.load', return_value=pd.DataFrame()), \
         patch('location_handlers.db.load_penalties', return_value=pd.DataFrame()), \
         patch('location_handlers.db.list_meetings', return_value=[]), \
         patch('location_handlers.db.has_telemetry', return_value=True):
        resp = client.get('/telemetry/kartsim/cadet/2026-08-29/Clay%20Pigeon?track_conditions=Dry')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert '14:59 Practice' in html
        assert 'track_conditions=Dry' in html


def test_get_track_points_filter_track_conditions(tmp_path: Path) -> None:
    client = flask_app.app.test_client()

    meeting_dir = tmp_path / "meeting"
    meeting_dir.mkdir()
    telemetry_dir = meeting_dir / "telemetry"
    telemetry_dir.mkdir()
    sample_csv = (
        "Format,RaceBox CSV\nData Source,KartSim\nConfiguration,Alice\n\n"
        "Record,Time,Latitude,Longitude,Lap\n1,2026-08-29T14:59:00Z,50.0,-1.0,1\n"
    )
    (telemetry_dir / "14_59_practice.csv").write_text(sample_csv)
    (telemetry_dir / "18_09_practice.csv").write_text(sample_csv)

    s1 = MagicMock()
    s1.session_id = '14_59_practice'
    s1.meeting_dir = str(meeting_dir)

    def find_sessions_side_effect(**kwargs: Any) -> list[MagicMock]:
        if kwargs.get('track_conditions') == 'Dry':
            return [s1]
        return []

    with patch('location_handlers.db.find_sessions', side_effect=find_sessions_side_effect), \
         patch('location_handlers.db.load', return_value=pd.DataFrame()), \
         patch('location_handlers.db.get_track', return_value={'sector_end': [], 'turns': []}), \
         patch('location_handlers.plot_handlers.get_hero_names', return_value=[]):
        url = (
            '/api/telemetry?league=kartsim&class_name=cadet'
            '&date=2026-08-29&track=Clay%20Pigeon&track_conditions=Dry'
        )
        resp = client.get(url)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['session_id'] == '14_59_practice'
