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
import brotli  # type: ignore[import-untyped]
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
from flask import Flask

import location_handlers
import app as flask_app


def _decode_channel_payload(data: bytes) -> tuple[float, float, list[float]]:
    decompressed = brotli.decompress(data)
    mean_val, scale = struct.unpack("<dd", decompressed[:16])
    deltas = np.frombuffer(decompressed[16:], dtype=np.int16)
    reconstructed = []
    curr = 0.0
    for d in deltas:
        if d == -32768:
            reconstructed.append(np.nan)
        else:
            curr += int(d)
            reconstructed.append(mean_val + curr * scale)
    return mean_val, scale, reconstructed


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

    # Test that outlap (lap_num=0) remains invalid and official lap 1 matches flying lap 1
    laps_outlap = [
        {'lap_num': 0, 'lap_time': '0:55.000', 'is_outlap': True, 'is_valid': False},
        {'lap_num': 1, 'lap_time': '1:00.000', 'is_valid': True},
        {'lap_num': 2, 'lap_time': '1:01.000', 'is_valid': True}
    ]
    location_handlers._override_with_official_laps(laps_outlap, df_official, 'S1', 'Driver A')
    assert laps_outlap[0]['lap_num'] == 0
    assert laps_outlap[0]['is_outlap'] is True
    assert laps_outlap[0]['is_valid'] is False
    assert laps_outlap[1]['lap_time'] == '0:59.000'
    assert laps_outlap[1]['is_valid'] is True
    assert laps_outlap[2]['lap_time'] == '1:00.500'
    assert laps_outlap[2]['is_valid'] is True


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

    # 4. Test filtering of dummy columns that are all 0.0 or empty
    dummy_csv_content = """Format,RaceTools CSV
Driver name,Katia

Record,Time,Latitude,Longitude,Speed,Lap,Yaw Rate,Throttle,Brake,RPS FL,Slip Angle
1,2026-09-12T08:48:10.600Z,50.933,0.907,15.0,1,25.5,0.0,0.0,0.00,
2,2026-09-12T08:48:10.700Z,50.933,0.907,16.0,1,28.2,0.0,0.0,0.00,
"""
    dummy_file = tmp_path / "dummy.csv"
    dummy_file.write_text(dummy_csv_content)

    _, dummy_cols, _ = location_handlers.parse_telemetry_csv(str(dummy_file))
    assert 'Yaw Rate' in dummy_cols
    assert 'Speed' in dummy_cols
    assert 'Throttle' not in dummy_cols
    assert 'Brake' not in dummy_cols
    assert 'RPS FL' not in dummy_cols
    assert 'Slip Angle' not in dummy_cols

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


def test_detect_is_outlap() -> None:
    # 1. Pit exit downstream of S/F (e.g. Lydd starting at 117m)
    lap0_away = {'dists': [117.2, 125.0, 1040.0]}
    lap1_normal = {'dists': [0.0, 500.0, 1040.0]}
    assert location_handlers._detect_is_outlap(lap0_away, lap1_normal) is True

    # 2. Pit exit joining near end of lap (covers only small distance span <90%)
    lap0_short = {'dists': [900.0, 950.0, 1040.0]}
    lap1_full = {'dists': [0.0, 500.0, 1040.0]}
    assert location_handlers._detect_is_outlap(lap0_short, lap1_full) is True

    # 3. Normal flying lap starting at S/F line (0m) covering full distance
    lap0_standing = {'dists': [0.0, 500.0, 1040.0]}
    assert location_handlers._detect_is_outlap(lap0_standing, lap1_normal) is False

    # 4. No distance data available
    assert location_handlers._detect_is_outlap({'dists': []}, lap1_normal) is False


def test_parse_telemetry_csv_with_outlap(tmp_path: Path) -> None:
    # Session with Lap 1 starting at 117m (outlap) and Lap 2 & 3 starting at 0m (flying laps)
    csv_content = """Format,RaceTools CSV
Driver name,Alexey

Time,Latitude,Longitude,Record,Lap,Lap Distance (m),Speed (km/h)
2026-09-12T11:02:00.000Z,50.934,0.907,1,1,117.2,30.0
2026-09-12T11:02:30.000Z,50.934,0.907,2,1,1040.0,75.0
2026-09-12T11:02:40.000Z,50.934,0.907,3,2,0.0,70.0
2026-09-12T11:03:28.409Z,50.934,0.907,4,2,1040.0,72.0
2026-09-12T11:03:28.500Z,50.934,0.907,5,3,0.0,71.0
2026-09-12T11:04:16.800Z,50.934,0.907,6,3,1040.0,73.0
"""
    test_file = tmp_path / "outlap_test.csv"
    test_file.write_text(csv_content)

    laps, columns, driver_name = location_handlers.parse_telemetry_csv(str(test_file))
    assert driver_name == "Alexey"
    assert len(laps) == 3

    # Outlap: Lap 0, invalid, raw_lap_num = 1
    assert laps[0]['lap_num'] == 0
    assert laps[0]['raw_lap_num'] == 1
    assert laps[0]['is_outlap'] is True
    assert laps[0]['is_valid'] is False
    assert np.isclose(laps[0]['duration'], 40.0)

    # Flying Lap 1: Lap 1, valid, raw_lap_num = 2
    assert laps[1]['lap_num'] == 1
    assert laps[1]['raw_lap_num'] == 2
    assert laps[1]['is_outlap'] is False
    assert laps[1]['is_valid'] is True
    assert np.isclose(laps[1]['duration'], 48.5)

    # Flying Lap 2: Lap 2, valid, raw_lap_num = 3
    assert laps[2]['lap_num'] == 2
    assert laps[2]['raw_lap_num'] == 3
    assert laps[2]['is_outlap'] is False
    assert laps[2]['is_valid'] is True


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
    _, _, reconstructed = _decode_channel_payload(response.data)
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

    _, _, reconstructed_speed = _decode_channel_payload(response_speed.data)
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

    _, _, reconstructed_speed_no_units = _decode_channel_payload(response_speed_no_units.data)
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

    _, _, reconstructed_nan = _decode_channel_payload(response_nan.data)
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
def test_get_telemetry_channel_always_brotli(
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

    # 2. Test that it always returns Brotli even if client sends identity or gzip
    headers_none = {'Accept-Encoding': 'identity'}
    response_none = client.get(url, headers=headers_none)
    assert response_none.status_code == 200
    assert response_none.headers.get('Content-Encoding') == 'br'

    decompressed_none = brotli.decompress(response_none.data)
    mean_val_none, scale_none = struct.unpack("<dd", decompressed_none[:16])
    deltas_none = np.frombuffer(decompressed_none[16:], dtype=np.int16)
    assert len(deltas_none) == 2


def test_should_smooth_channel() -> None:
    assert location_handlers._should_smooth_channel("GForceLat") is True
    assert location_handlers._should_smooth_channel("GForceLon") is True
    assert location_handlers._should_smooth_channel("GForceVert") is True
    assert location_handlers._should_smooth_channel("Slide Pct FL") is True
    assert location_handlers._should_smooth_channel("Lat Force FL") is True
    assert location_handlers._should_smooth_channel("Lat Force Front") is True
    assert location_handlers._should_smooth_channel("Lat Force Rear") is True
    assert location_handlers._should_smooth_channel("Long Force Front") is True
    assert location_handlers._should_smooth_channel("Long Force Rear") is True
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

    _, _, reconstructed = _decode_channel_payload(response_smooth.data)
    assert np.allclose(reconstructed, [2.0, 2.5, 3.0, 3.5, 4.0], atol=1e-5)

    # Case B: league is NOT kartsim (should NOT smooth: [1.0, 2.0, 3.0, 4.0, 5.0])
    url_no_smooth = (
        '/api/telemetry/channel?league=other_league&class_name=X30'
        '&date=2026-05-10&track=Rowrah&session_id=S1_hero_a.csv&channel=GForceLat'
    )
    response_no_smooth = client.get(url_no_smooth)
    assert response_no_smooth.status_code == 200

    _, _, reconstructed_no_smooth = _decode_channel_payload(response_no_smooth.data)
    assert np.allclose(reconstructed_no_smooth, [1.0, 2.0, 3.0, 4.0, 5.0], atol=1e-5)


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


def test_telemetry_track_view_with_report_param() -> None:
    app = Flask(__name__, template_folder='../templates')
    app.register_blueprint(location_handlers.location_blueprint)
    client = app.test_client()

    mock_meta = {
        "title": "Turn 3 Mystery Solved",
        "track": "Lydd",
        "state": {"xlim": [292.0, 458.0]}
    }
    mock_state = {"xlim": [292.0, 458.0]}
    mock_body = "<p>Mystery Solved Content</p>"
    s1 = MagicMock()
    s1.session_id = 'race_1'
    s1.session_name = 'Race 1'
    s1.session_start_datetime = '2026-09-12T12:00:00'
    s2 = MagicMock()
    s2.session_id = 'race_2'
    s2.session_name = 'Race 2'
    s2.session_start_datetime = '2026-09-12T14:00:00'

    def mock_find_sessions(leagues: str, classes: str, date: str, track: str) -> list[MagicMock]:
        if leagues == 'club100_south':
            return [s1]
        return [s2]

    with patch('location_handlers.report_parser.load_report', return_value=(mock_meta, mock_state, mock_body)), \
         patch('location_handlers.auth.get_current_acl', return_value={'see_telemetry': True}), \
         patch('location_handlers.db.find_sessions', side_effect=mock_find_sessions), \
         patch('location_handlers.db.list_meetings', return_value=[]), \
         patch('location_handlers.auth.can_see_telemetry', return_value=True), \
         patch('location_handlers.render_template', return_value="RENDERED_TRACK_REPORT") as mock_render:

        url = (
            '/telemetry/Lydd?report=lydd_turn3_mystery_solved&'
            'session=club100_south/cadet_lw/2026-09-12/Lydd/race_1&'
            'session=club100/cadet/2026-09-12/Lydd/race_2'
        )
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.data.decode('utf-8') == "RENDERED_TRACK_REPORT"
        mock_render.assert_called_once()
        call_kwargs = mock_render.call_args[1]
        assert call_kwargs['is_report_mode'] is True
        assert call_kwargs['report_title'] == "Turn 3 Mystery Solved"
        assert call_kwargs['report_html'] == mock_body
        assert call_kwargs['report_state'] == mock_state
        assert call_kwargs['report_name'] == "lydd_turn3_mystery_solved"


def test_telemetry_report_view_multi_session() -> None:
    app = Flask(__name__, template_folder='../templates')
    app.register_blueprint(location_handlers.location_blueprint)
    client = app.test_client()

    mock_meta = {
        "title": "Turn 3 Mystery Solved",
        "date": "2026-09-12",
        "track": "Lydd",
        "session_id": "09_48_cadet_group_b,11_02_cadet_lightweight_south_group_2_practice"
    }
    mock_state = {"lapsA": ["09_48_cadet_group_b-13", "11_02_cadet_lightweight_south_group_2_practice-11"]}
    mock_body = "<p>Mystery Solved Content</p>"

    s1 = MagicMock()
    s1.league = 'club100'
    s1.class_name = ['cadet']
    s1.date = '2026-09-12'
    s1.track_name = 'Lydd'
    s1.session_id = '09_48_cadet_group_b'
    s1.session_name = '09:48 Cadet Group B'
    s1.session_start_datetime = '2026-09-12 09:48'

    s2 = MagicMock()
    s2.league = 'club100_south'
    s2.class_name = ['cadet_lw']
    s2.date = '2026-09-12'
    s2.track_name = 'Lydd'
    s2.session_id = '11_02_cadet_lightweight_south_group_2_practice'
    s2.session_name = '11:02 Cadet Lightweight Practice'
    s2.session_start_datetime = '2026-09-12 11:02'

    def mock_find_sessions(**kwargs: Any) -> list[MagicMock]:
        if 'session_id' in kwargs and kwargs['session_id']:
            return [s1, s2]
        if kwargs.get('leagues') == 'club100':
            return [s1]
        return [s2]

    with patch('location_handlers.report_parser.load_report', return_value=(mock_meta, mock_state, mock_body)), \
         patch('location_handlers.auth.get_current_acl', return_value={'see_telemetry': True}), \
         patch('location_handlers.db.find_sessions', side_effect=mock_find_sessions), \
         patch('location_handlers.auth.can_see_telemetry', return_value=True), \
         patch('location_handlers.render_template', return_value="RENDERED_REPORT_VIEW") as mock_render:

        resp = client.get('/telemetry/report/lydd_turn3_mystery_solved')
        assert resp.status_code == 200
        assert resp.data.decode('utf-8') == "RENDERED_REPORT_VIEW"
        mock_render.assert_called_once()
        call_kwargs = mock_render.call_args[1]
        assert call_kwargs['is_report_mode'] is True
        assert call_kwargs['report_title'] == "Turn 3 Mystery Solved"
        assert call_kwargs['report_html'] == mock_body
        assert call_kwargs['report_state'] == mock_state
        assert call_kwargs['report_name'] == "lydd_turn3_mystery_solved"
        assert len(call_kwargs['telemetry_sessions']) == 2


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


def test_parse_session_urls() -> None:
    text = (
        "Check this session:\n"
        "https://brbrdb.brbrkitten.com/session/club100_south/cadet_lw/2026-09-12/Lydd/"
        "15_12_race_13_cadet_lightweight_south_c_final\n"
        "and another relative one: /session/club100_south/cadet_lw/2026-09-12/Lydd/"
        "12_14_race_3_cadet_lightweight_south_group_2_qualifying?param=1"
    )
    sessions = location_handlers.parse_session_urls(text)
    assert len(sessions) == 2
    assert sessions[0]['league'] == 'club100_south'
    assert sessions[0]['class_name'] == 'cadet_lw'
    assert sessions[0]['date'] == '2026-09-12'
    assert sessions[0]['track'] == 'Lydd'
    assert sessions[0]['session_id'] == '15_12_race_13_cadet_lightweight_south_c_final'

    assert sessions[1]['session_id'] == '12_14_race_3_cadet_lightweight_south_group_2_qualifying'

    # Test telemetry URL support (meeting-level without session_id and with session_id)
    telem_text = (
        "https://brbrdb.brbrkitten.com/telemetry/kartsim/"
        "iame_waterswift_restricted_cadet_uk/2026-10-03/Whilton%20Mill\n"
        "https://brbrdb.brbrkitten.com/telemetry/kartsim/"
        "iame_waterswift_restricted_cadet_uk/2026-10-03/Whilton%20Mill?session_id=sess1,sess2"
    )
    telem_sessions = location_handlers.parse_session_urls(telem_text)
    assert len(telem_sessions) == 3
    assert telem_sessions[0]['league'] == 'kartsim'
    assert telem_sessions[0]['class_name'] == 'iame_waterswift_restricted_cadet_uk'
    assert telem_sessions[0]['date'] == '2026-10-03'
    assert telem_sessions[0]['track'] == 'Whilton Mill'
    assert telem_sessions[0]['session_id'] is None

    assert telem_sessions[1]['session_id'] == 'sess1'
    assert telem_sessions[2]['session_id'] == 'sess2'


def test_telemetry_launcher_get() -> None:
    client = flask_app.app.test_client()
    with patch('location_handlers.auth.get_current_acl', return_value={'see_telemetry': True}):
        resp = client.get('/telemetry/')
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'Telemetry Session Viewer' in html
        assert 'Session Links' in html

        resp2 = client.get('/telemetry')
        assert resp2.status_code in (200, 308)  # standard or trailing-slash redirect


def test_telemetry_launcher_get_restricted() -> None:
    client = flask_app.app.test_client()
    with patch('location_handlers.auth.get_current_acl', return_value={'see_telemetry': False}):
        resp = client.get('/telemetry/')
        assert resp.status_code == 403


def test_telemetry_launcher_post_valid() -> None:
    client = flask_app.app.test_client()
    url = (
        'https://brbrdb.brbrkitten.com/session/club100_south/cadet_lw/2026-09-12/Lydd/'
        '15_12_race_13_cadet_lightweight_south_c_final'
    )
    with patch('location_handlers.auth.get_current_acl', return_value={'see_telemetry': True}):
        resp = client.post('/telemetry/', data={'session_links': url})
        assert resp.status_code == 302
        expected_loc = (
            '/telemetry/club100_south/cadet_lw/2026-09-12/Lydd?'
            'session_id=15_12_race_13_cadet_lightweight_south_c_final'
        )
        assert resp.headers['Location'] == expected_loc


def test_telemetry_launcher_post_multiple_valid() -> None:
    client = flask_app.app.test_client()
    links = (
        "https://brbrdb.brbrkitten.com/session/club100_south/cadet_lw/2026-09-12/Lydd/12_14_race_3\n"
        "https://brbrdb.brbrkitten.com/session/club100_south/cadet_lw/2026-09-12/Lydd/15_12_race_13\n"
    )
    with patch('location_handlers.auth.get_current_acl', return_value={'see_telemetry': True}):
        resp = client.post('/telemetry/', data={'session_links': links})
        assert resp.status_code == 302
        assert resp.headers['Location'] == (
            '/telemetry/club100_south/cadet_lw/2026-09-12/Lydd?session_id=12_14_race_3,15_12_race_13'
        )


def test_telemetry_launcher_post_telemetry_meeting_link() -> None:
    client = flask_app.app.test_client()
    url = (
        'https://brbrdb.brbrkitten.com/telemetry/kartsim/iame_waterswift_restricted_cadet_uk/'
        '2026-10-03/Whilton%20Mill'
    )
    with patch('location_handlers.auth.get_current_acl', return_value={'see_telemetry': True}):
        resp = client.post('/telemetry/', data={'session_links': url})
        assert resp.status_code == 302
        assert resp.headers['Location'] == (
            '/telemetry/kartsim/iame_waterswift_restricted_cadet_uk/2026-10-03/Whilton%20Mill'
        )


def test_telemetry_launcher_post_telemetry_link_with_params() -> None:
    client = flask_app.app.test_client()
    url = (
        'https://brbrdb.brbrkitten.com/telemetry/kartsim/iame_waterswift_restricted_cadet_uk/'
        '2026-10-03/Whilton%20Mill?session_id=12_30_practice&tab=map#lap-5'
    )
    with patch('location_handlers.auth.get_current_acl', return_value={'see_telemetry': True}):
        resp = client.post('/telemetry/', data={'session_links': url})
        assert resp.status_code == 302
        loc = resp.headers['Location']
        assert loc.startswith('/telemetry/kartsim/iame_waterswift_restricted_cadet_uk/2026-10-03/Whilton%20Mill?')
        assert 'session_id=12_30_practice' in loc
        assert 'tab=map' in loc
        assert loc.endswith('#lap-5')


def test_telemetry_launcher_post_different_tracks() -> None:
    client = flask_app.app.test_client()
    links = (
        "https://brbrdb.brbrkitten.com/session/club100_south/cadet_lw/2026-09-12/Lydd/race_1\n"
        "https://brbrdb.brbrkitten.com/session/club100_south/cadet_lw/2026-09-12/Shenington/race_2\n"
    )
    with patch('location_handlers.auth.get_current_acl', return_value={'see_telemetry': True}):
        resp = client.post('/telemetry/', data={'session_links': links})
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'different tracks' in html


def test_telemetry_launcher_post_different_meetings() -> None:
    client = flask_app.app.test_client()
    links = (
        "https://brbrdb.brbrkitten.com/session/club100_south/cadet_lw/2026-09-12/Lydd/race_1\n"
        "https://brbrdb.brbrkitten.com/session/club100/cadet/2026-09-12/Lydd/race_2\n"
    )
    with patch('location_handlers.auth.get_current_acl', return_value={'see_telemetry': True}):
        resp = client.post('/telemetry/', data={'session_links': links})
        assert resp.status_code == 302
        assert resp.headers['Location'] == (
            '/telemetry/Lydd?session=club100_south/cadet_lw/2026-09-12/Lydd/race_1'
            '&session=club100/cadet/2026-09-12/Lydd/race_2'
        )


def test_telemetry_track_view() -> None:
    client = flask_app.app.test_client()
    s1 = MagicMock()
    s1.session_id = 'race_1'
    s1.session_name = 'Race 1'
    s1.session_start_datetime = '2026-09-12T12:00:00'
    s2 = MagicMock()
    s2.session_id = 'race_2'
    s2.session_name = 'Race 2'
    s2.session_start_datetime = '2026-09-12T14:00:00'

    def mock_find_sessions(leagues: str, classes: str, date: str, track: str) -> list[MagicMock]:
        if leagues == 'club100_south':
            return [s1]
        return [s2]

    with patch('location_handlers.auth.get_current_acl', return_value={'see_telemetry': True}), \
         patch('location_handlers.db.find_sessions', side_effect=mock_find_sessions), \
         patch('location_handlers.db.list_meetings', return_value=[]), \
         patch('location_handlers.auth.can_see_telemetry', return_value=True):
        url = (
            '/telemetry/Lydd?'
            'session=club100_south/cadet_lw/2026-09-12/Lydd/race_1&'
            'session=club100/cadet/2026-09-12/Lydd/race_2'
        )
        resp = client.get(url)
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'Lydd' in html
        assert 'getTrackPointsUrl' in html
        assert '/api/telemetry?track=Lydd' in html
        assert 'session=club100_south/cadet_lw/2026-09-12/Lydd/race_1' in html
        assert 'session=club100/cadet/2026-09-12/Lydd/race_2' in html


def test_telemetry_launcher_post_invalid() -> None:
    client = flask_app.app.test_client()
    with patch('location_handlers.auth.get_current_acl', return_value={'see_telemetry': True}):
        resp = client.post('/telemetry/', data={'session_links': 'just random invalid text'})
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')
        assert 'No valid session links found' in html


def test_get_track_points_multi_session_filter(tmp_path: Path) -> None:
    client = flask_app.app.test_client()

    meeting_dir = tmp_path / "meeting"
    meeting_dir.mkdir()
    telemetry_dir = meeting_dir / "telemetry"
    telemetry_dir.mkdir()
    sample_csv = (
        "Format,RaceBox CSV\nData Source,KartSim\nConfiguration,Alice\n\n"
        "Record,Time,Latitude,Longitude,Lap\n1,2026-08-29T14:59:00Z,50.0,-1.0,1\n"
    )
    (telemetry_dir / "sess1.csv").write_text(sample_csv)
    (telemetry_dir / "sess2.csv").write_text(sample_csv)
    (telemetry_dir / "sess3.csv").write_text(sample_csv)

    s1 = MagicMock()
    s1.session_id = 'sess1'
    s1.meeting_dir = str(meeting_dir)

    s2 = MagicMock()
    s2.session_id = 'sess2'
    s2.meeting_dir = str(meeting_dir)

    s3 = MagicMock()
    s3.session_id = 'sess3'
    s3.meeting_dir = str(meeting_dir)

    with patch('location_handlers.db.find_sessions', return_value=[s1, s2, s3]), \
         patch('location_handlers.db.load', return_value=pd.DataFrame()), \
         patch('location_handlers.db.get_track', return_value={'sector_end': [], 'turns': []}), \
         patch('location_handlers.plot_handlers.get_hero_names', return_value=[]), \
         patch('location_handlers.auth.can_see_telemetry', return_value=True):
        url = '/api/telemetry?league=kartsim&class_name=cadet&date=2026-08-29&track=Lydd&session_id=sess1,sess3'
        resp = client.get(url)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2
        returned_sids = {d['session_id'] for d in data}
        assert returned_sids == {'sess1', 'sess3'}


def test_get_track_points_multi_meeting(tmp_path: Path) -> None:
    client = flask_app.app.test_client()

    meeting_dir_1 = tmp_path / "m1"
    meeting_dir_1.mkdir()
    telem_1 = meeting_dir_1 / "telemetry"
    telem_1.mkdir()

    meeting_dir_2 = tmp_path / "m2"
    meeting_dir_2.mkdir()
    telem_2 = meeting_dir_2 / "telemetry"
    telem_2.mkdir()

    sample_csv = (
        "Format,RaceBox CSV\nData Source,KartSim\nConfiguration,Alice\n\n"
        "Record,Time,Latitude,Longitude,Lap\n1,2026-08-29T14:59:00Z,50.0,-1.0,1\n"
    )
    (telem_1 / "race_1.csv").write_text(sample_csv)
    (telem_2 / "race_2.csv").write_text(sample_csv)

    s1 = MagicMock()
    s1.session_id = 'race_1'
    s1.session_name = 'Race 1'
    s1.meeting_dir = str(meeting_dir_1)

    s2 = MagicMock()
    s2.session_id = 'race_2'
    s2.session_name = 'Race 2'
    s2.meeting_dir = str(meeting_dir_2)

    def mock_find_sessions(leagues: str, classes: str, date: str, track: str) -> list[MagicMock]:
        if leagues == 'club100_south':
            return [s1]
        return [s2]

    with patch('location_handlers.db.find_sessions', side_effect=mock_find_sessions), \
         patch('location_handlers.db.load', return_value=pd.DataFrame()), \
         patch('location_handlers.db.get_track', return_value={'sector_end': [], 'turns': []}), \
         patch('location_handlers.plot_handlers.get_hero_names', return_value=[]), \
         patch('location_handlers.auth.can_see_telemetry', return_value=True):
        url = (
            '/api/telemetry?track=Lydd&'
            'session=club100_south/cadet_lw/2026-09-12/Lydd/race_1&'
            'session=club100/cadet/2026-09-12/Lydd/race_2'
        )
        resp = client.get(url)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 2
        assert data[0]['session_id'] == 'club100_south/cadet_lw/2026-09-12/Lydd/race_1'
        assert data[1]['session_id'] == 'club100/cadet/2026-09-12/Lydd/race_2'


def test_get_telemetry_channel_multi_meeting(tmp_path: Path) -> None:
    client = flask_app.app.test_client()

    meeting_dir = tmp_path / "meeting"
    meeting_dir.mkdir()
    telem = meeting_dir / "telemetry"
    telem.mkdir()

    sample_csv = (
        "Format,RaceBox CSV\nData Source,KartSim\nConfiguration,Alice\n\n"
        "Record,Time,Speed,Lap\n1,2026-08-29T14:59:00Z,25.5,1\n"
    )
    (telem / "race_1.csv").write_text(sample_csv)

    s1 = MagicMock()
    s1.session_id = 'race_1'
    s1.meeting_dir = str(meeting_dir)

    with patch('location_handlers.db.find_sessions', return_value=[s1]), \
         patch('location_handlers.auth.can_see_telemetry', return_value=True):
        # Pass composite session_id
        url = (
            '/api/telemetry/channel?session_id=club100_south/cadet_lw/2026-09-12/Lydd/race_1&channel=Speed'
        )
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp.mimetype == 'application/octet-stream'


def test_get_telemetry_channel_and_track_points_gyroscope(tmp_path: Path) -> None:
    client = flask_app.app.test_client()

    meeting_dir = tmp_path / "meeting"
    meeting_dir.mkdir()
    telem = meeting_dir / "telemetry"
    telem.mkdir()

    sample_csv = (
        "Format,RaceBox CSV\nDriver name,Driver A\n\n"
        "Record,Time,Latitude,Longitude,Speed,Lap,Yaw Rate,Pitch Rate,Roll Rate\n"
        "1,2026-08-29T14:59:00.000Z,51.864398,-1.684073,15.0,1,25.500,-1.200,0.800\n"
        "2,2026-08-29T14:59:00.100Z,51.864400,-1.684070,16.0,1,28.200,-1.150,0.850\n"
    )
    (telem / "14_59_practice_driver_a.csv").write_text(sample_csv)

    s1 = MagicMock()
    s1.session_id = '14_59_practice'
    s1.session_name = '14:59 Practice'
    s1.meeting_dir = str(meeting_dir)
    s1.league = 'club100'
    s1.class_name = 'cadet'
    s1.date = '2026-08-29'
    s1.track = 'Lydd'

    with patch('location_handlers.db.find_sessions', return_value=[s1]), \
         patch('location_handlers.db.get_track', return_value={}), \
         patch('location_handlers.db.load', return_value=pd.DataFrame()), \
         patch('location_handlers.auth.can_see_telemetry', return_value=True):

        # 1. Test /api/telemetry contains gyro columns
        resp_points = client.get('/api/telemetry?league=club100&class_name=cadet&date=2026-08-29&track=Lydd')
        assert resp_points.status_code == 200
        data = resp_points.get_json()
        assert len(data) == 1
        assert 'Yaw Rate' in data[0]['columns']
        assert 'Pitch Rate' in data[0]['columns']
        assert 'Roll Rate' in data[0]['columns']

        # 2. Test /api/telemetry/channel for Yaw Rate
        url_yaw = '/api/telemetry/channel?session_id=club100/cadet/2026-08-29/Lydd/14_59_practice&channel=Yaw Rate'
        resp_yaw = client.get(url_yaw)
        assert resp_yaw.status_code == 200
        _, _, reconstructed = _decode_channel_payload(resp_yaw.data)
        assert np.allclose(reconstructed, [25.5, 28.2], atol=1e-3)

        # 3. Test /api/telemetry/channel for Pitch Rate
        url_pitch = '/api/telemetry/channel?session_id=club100/cadet/2026-08-29/Lydd/14_59_practice&channel=Pitch Rate'
        resp_pitch = client.get(url_pitch)
        assert resp_pitch.status_code == 200
        _, _, reconstructed_pitch = _decode_channel_payload(resp_pitch.data)
        assert np.allclose(reconstructed_pitch, [-1.2, -1.15], atol=1e-3)

        # 4. Test /api/telemetry/channel for Roll Rate
        url_roll = '/api/telemetry/channel?session_id=club100/cadet/2026-08-29/Lydd/14_59_practice&channel=Roll Rate'
        resp_roll = client.get(url_roll)
        assert resp_roll.status_code == 200
        _, _, reconstructed_roll = _decode_channel_payload(resp_roll.data)
        assert np.allclose(reconstructed_roll, [0.8, 0.85], atol=1e-3)


def test_parse_telemetry_csv_filters_outlier_coordinates(tmp_path: Path) -> None:
    csv_content = """Format,RaceBox CSV
Configuration,Test Driver

Record,Time,Latitude,Longitude,Speed,Lap
1,2026-05-23T10:13:00.000Z,83.779642,73.235724,0.0,1
2,2026-05-23T10:13:01.000Z,54.551000,-3.442000,10.0,1
3,2026-05-23T10:13:02.000Z,54.551050,-3.442050,11.0,1
"""
    f = tmp_path / "outliers.csv"
    f.write_text(csv_content)

    laps, columns, driver = location_handlers.parse_telemetry_csv(str(f))
    assert driver == "Test Driver"
    assert len(laps) == 1
    # Only records 2 and 3 should be included (start_idx 0, end_idx 1)
    assert laps[0]['start_idx'] == 0
    assert laps[0]['end_idx'] == 1
    assert len(laps[0]['times']) == 2


def test_telemetry_view_track_alias_redirect() -> None:
    client = flask_app.app.test_client()
    mock_session = MagicMock()
    mock_session.session_id = 'sess1'

    def find_sessions_mock(**kwargs: Any) -> list:
        if kwargs.get('track') == 'Whilton Mill':
            return [mock_session]
        return []

    with patch('location_handlers.auth.can_see_telemetry', return_value=True), \
         patch('location_handlers.db.get_track', return_value={'track_name': 'Whilton Mill'}), \
         patch('location_handlers.db.find_sessions', side_effect=find_sessions_mock):
        resp = client.get('/telemetry/kartsim/cadet/2026-09-27/Whilton%20Mill%20International')
        assert resp.status_code == 302
        assert resp.headers['Location'] == '/telemetry/kartsim/cadet/2026-09-27/Whilton%20Mill'


def test_get_track_points_track_alias_fallback(tmp_path: Path) -> None:
    client = flask_app.app.test_client()
    meeting_dir = tmp_path / "meeting"
    meeting_dir.mkdir()
    telem_dir = meeting_dir / "telemetry"
    telem_dir.mkdir()

    sample_csv = (
        "Format,RaceBox CSV\nData Source,KartSim\nConfiguration,Alice\n\n"
        "Record,Time,Latitude,Longitude,Lap Distance,Lap\n1,2026-08-29T14:59:00Z,50.0,-1.0,10.0,1\n"
    )
    (telem_dir / "sess1.csv").write_text(sample_csv)

    mock_session = MagicMock()
    mock_session.session_id = 'sess1'
    mock_session.meeting_dir = str(meeting_dir)

    def find_sessions_mock(**kwargs: Any) -> list:
        if kwargs.get('track') == 'Whilton Mill':
            return [mock_session]
        return []

    mock_track = {'track_name': 'Whilton Mill', 'sector_end': [], 'turns': []}
    with patch('location_handlers.auth.can_see_telemetry', return_value=True), \
         patch('location_handlers.db.get_track', return_value=mock_track), \
         patch('location_handlers.db.load', return_value=pd.DataFrame()), \
         patch('location_handlers.plot_handlers.get_hero_names', return_value=[]), \
         patch('location_handlers.db.find_sessions', side_effect=find_sessions_mock):
        url = (
            '/api/telemetry?league=kartsim&class_name=cadet&'
            'date=2026-09-27&track=Whilton%20Mill%20International'
        )
        resp = client.get(url)
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]['session_id'] == 'sess1'
