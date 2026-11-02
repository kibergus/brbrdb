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

from pathlib import Path
from unittest.mock import patch, MagicMock
import pandas as pd
import pytest
import app
import upload_handlers


@pytest.fixture(autouse=True)
def mock_upload_handlers_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upload_handlers, 'DATA_DIR', str(tmp_path))


def test_detect_distance_to_rear_axle() -> None:
    assert upload_handlers.detect_distance_to_rear_axle('club100_south/cadet_slw') == 1.5
    assert upload_handlers.detect_distance_to_rear_axle('fat_karting/cadet_uk') == 0.45
    assert upload_handlers.detect_distance_to_rear_axle('rotax/senior') is None
    assert upload_handlers.detect_distance_to_rear_axle(None) is None


def test_upload_telemetry_page_acl_get() -> None:
    client = app.app.test_client()
    mock_keys = {
        'no_tel_key': {
            'see_telemetry': False,
            'see_leagues': ['*'],
        },
        'has_tel_key': {
            'see_telemetry': {
                'leagues': ['club100_south'],
            },
            'see_leagues': ['*'],
        },
    }
    mock_session = MagicMock()
    mock_session.meeting_dir = '/dummy'
    mock_session.session_id = 'practice_1'
    mock_session.session_name = 'Practice 1'
    mock_session.session_start_datetime = '2026-09-13 10:31:00'
    mock_session.meeting_folder = '2026_09_13_Lydd'
    mock_session.track_name = 'Lydd'

    with patch('auth.load_keys', return_value=mock_keys), \
         patch('app.db.find_sessions', return_value=[mock_session]), \
         patch('app.db.has_telemetry', return_value=True), \
         patch('app.db.get_session_drivers', return_value=['Test Driver']), \
         patch('app.plot_handlers.get_hero_names', return_value=['Test Driver']):

        # 1. No access -> 403 Forbidden
        client.set_cookie('auth_key', 'no_tel_key')
        resp_forbidden = client.get(
            '/session/club100_south/cadet_slw/2026-09-13/Lydd/practice_1/upload_telemetry',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_forbidden.status_code == 403

        # 2. Has access -> 200 OK with upload form
        client.set_cookie('auth_key', 'has_tel_key')
        resp_ok = client.get(
            '/session/club100_south/cadet_slw/2026-09-13/Lydd/practice_1/upload_telemetry',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_ok.status_code == 200
        html = resp_ok.get_data(as_text=True)
        assert 'Upload Telemetry' in html
        assert 'Test Driver' in html
        assert '1.5' in html  # Auto-detected arm length for club100_south/cadet_slw
        assert '/api/upload/stream' in html
        assert 'RaceBox Micro Direct Download' in html
        assert 'raceboxConnectBtn' in html
        assert 'Web Bluetooth' in html
        assert 'tabBtnRacebox' in html
        assert 'tabBtnFile' in html

    # 3. When has_telemetry=False, "No Telemetry Yet" badge is dropped
    with patch('auth.load_keys', return_value=mock_keys), \
         patch('app.db.find_sessions', return_value=[mock_session]), \
         patch('app.db.has_telemetry', return_value=False), \
         patch('app.db.get_session_drivers', return_value=['Test Driver']), \
         patch('app.plot_handlers.get_hero_names', return_value=['Test Driver']):

        client.set_cookie('auth_key', 'has_tel_key')
        resp_no_tel = client.get(
            '/session/club100_south/cadet_slw/2026-09-13/Lydd/practice_1/upload_telemetry',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_no_tel.status_code == 200
        html_no_tel = resp_no_tel.get_data(as_text=True)
        assert 'No Telemetry Yet' not in html_no_tel
        assert 'Telemetry Available' not in html_no_tel


def test_session_view_shows_upload_button() -> None:
    client = app.app.test_client()
    mock_keys = {
        'has_tel_key': {
            'see_telemetry': {
                'leagues': ['club100_south'],
            },
            'see_leagues': ['*'],
        },
    }
    mock_df = pd.DataFrame([{
        'SessionID': 'practice_1',
        'Name': 'Test Driver',
        'Lap': 1,
        'LapTime': '1:00.000',
        'LapTimeSeconds': 60.0,
        'LapTimeDeleted': False,
        'Pos': 1,
        'PositionsGained': 0,
        'SessionName': 'Practice 1',
        'DateTime': '2026-09-13 10:31:00',
        'TrackName': 'Lydd',
    }])
    mock_session = MagicMock()
    mock_session.meeting_dir = '/dummy'
    mock_session.session_id = 'practice_1'
    mock_session.session_name = 'Practice 1'
    mock_session.session_start_datetime = '2026-09-13 10:31:00'
    mock_session.track_name = 'Lydd'

    # Case A: When has_telemetry is True -> telemetry pill + round icon button
    with patch('auth.load_keys', return_value=mock_keys), \
         patch('app.db.find_sessions', return_value=[mock_session]), \
         patch('app.db.has_telemetry', return_value=True), \
         patch('app.db.load', return_value=mock_df), \
         patch('app.db.load_penalties', return_value=pd.DataFrame()), \
         patch('app.db.get_overlapping_videos', return_value=[]), \
         patch('app.plot_handlers.get_hero_names', return_value=['Test Driver']):

        client.set_cookie('auth_key', 'has_tel_key')
        resp = client.get(
            '/session/club100_south/cadet_slw/2026-09-13/Lydd/practice_1',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Telemetry' in html
        assert 'upload-telemetry-btn' in html
        assert 'upload-telemetry-btn-pill' not in html
        assert '/session/club100_south/cadet_slw/2026-09-13/Lydd/practice_1/upload_telemetry' in html

    # Case B: When has_telemetry is False -> "<upload icon> Telemetry" pill button
    with patch('auth.load_keys', return_value=mock_keys), \
         patch('app.db.find_sessions', return_value=[mock_session]), \
         patch('app.db.has_telemetry', return_value=False), \
         patch('app.db.load', return_value=mock_df), \
         patch('app.db.load_penalties', return_value=pd.DataFrame()), \
         patch('app.db.get_overlapping_videos', return_value=[]), \
         patch('app.plot_handlers.get_hero_names', return_value=['Test Driver']):

        client.set_cookie('auth_key', 'has_tel_key')
        resp_no_tel = client.get(
            '/session/club100_south/cadet_slw/2026-09-13/Lydd/practice_1',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_no_tel.status_code == 200
        html_no_tel = resp_no_tel.get_data(as_text=True)
        assert 'upload-telemetry-btn upload-telemetry-btn-pill' in html_no_tel
        assert 'Telemetry' in html_no_tel


def test_upload_telemetry_post_stream_endpoint() -> None:
    client = app.app.test_client()
    mock_keys = {
        'has_tel_key': {
            'see_telemetry': {
                'leagues': ['club100_south'],
                'drivers': ['*'],
            },
            'upload_sessions': {
                'leagues': ['club100_south'],
                'drivers': ['Test Driver'],
            },
            'see_leagues': ['*'],
        },
    }

    csv_payload = (
        'Format,RaceTools CSV\n'
        'Track name,Lydd\n'
        'Date,2026-09-13\n'
        'Time,10:31:00\n'
        'Driver name,Test Driver\n'
        'League,club100_south\n'
        'Class,cadet_slw\n'
        'Session,Practice 1\n'
        'Session ID,practice_1\n'
        'Distance to rear axle,1.5\n'
        '\n'
        'Record,Time,Latitude,Longitude,Speed\n'
        '1,10:31:00.000,51.05,-0.15,15.2\n'
    )

    with (
        patch('auth.load_keys', return_value=mock_keys),
        patch('upload_handlers.process_telemetry_derivative_data', return_value=(
            pd.DataFrame([{'Time': 0.0, 'Speed': 15.2}]),
            {'session_id': 'practice_1'},
            pd.DataFrame([{'Name': 'Test Driver', 'LapTimeSeconds': 60.0}])
        )),
        patch('upload_handlers.populate_db.populate_single_session', return_value='2026_09_13_Lydd_practice_1'),
        patch('upload_handlers.invalidate_plot_cache'),
    ):

        client.set_cookie('auth_key', 'has_tel_key')
        resp = client.post(
            '/api/upload/stream',
            data=csv_payload,
            content_type='text/csv; charset=utf-8',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'imported successfully' in data['message']


def test_stream_upload_rejects_malicious_session_id() -> None:
    client = app.app.test_client()
    mock_keys = {
        'has_tel_key': {
            'upload_sessions': {
                'leagues': ['club100_south'],
                'drivers': ['Test Driver'],
            },
            'see_leagues': ['*'],
        },
    }

    malicious_ids = [
        '../../evil',
        '/tmp/evil',
        'evil/test',
        '..\\evil',
    ]

    for mal_id in malicious_ids:
        csv_payload = (
            'Format,RaceTools CSV\n'
            'Track name,Lydd\n'
            'Date,2026-09-13\n'
            'Time,10:31:00\n'
            'Driver name,Test Driver\n'
            'League,club100_south\n'
            'Class,cadet_slw\n'
            'Session,Practice 1\n'
            f'Session ID,{mal_id}\n'
            '\n'
            'Record,Time,Latitude,Longitude,Speed\n'
            '1,10:31:00.000,51.05,-0.15,15.2\n'
        )

        with patch('auth.load_keys', return_value=mock_keys):
            client.set_cookie('auth_key', 'has_tel_key')
            resp = client.post(
                '/api/upload/stream',
                data=csv_payload,
                content_type='text/csv; charset=utf-8',
                environ_base={'REMOTE_ADDR': '192.168.1.100'}
            )
            assert resp.status_code == 400
            data = resp.get_json()
            assert 'Invalid Session ID' in data['error'] or 'Invalid path' in data['error']


def test_stream_upload_racebox_channels() -> None:
    client = app.app.test_client()
    mock_keys = {
        'has_tel_key': {
            'upload_sessions': {
                'leagues': ['club100_south'],
                'drivers': ['Test Driver'],
            },
            'see_leagues': ['*'],
        },
    }

    csv_payload = (
        'Format,RaceTools CSV\n'
        'Track name,Lydd\n'
        'Date,2026-09-13\n'
        'Time,10:31:00\n'
        'Driver name,Test Driver\n'
        'League,club100_south\n'
        'Class,cadet_slw\n'
        'Session,Practice 1\n'
        'Session ID,practice_1\n'
        'Distance to rear axle,1.5\n'
        '\n'
        'Record,Time,Latitude,Longitude,Altitude,Speed,GForceLat,GForceLon,'
        'GForceVert,Yaw Rate,Pitch Rate,Roll Rate,Lap,Lap Distance\n'
        '1,10:31:00.000,51.0500000,-0.1500000,45.00,15.200,0.150,-0.250,0.980,-15.000,10.000,5.000,1,0.00\n'
        '2,10:31:00.040,51.0500100,-0.1500010,45.02,15.250,0.155,-0.245,0.982,-14.800,9.900,5.050,1,0.61\n'
    )

    with (
        patch('auth.load_keys', return_value=mock_keys),
        patch('upload_handlers.process_telemetry_derivative_data', return_value=(
            pd.DataFrame([{'Time': 0.0, 'Speed': 15.2, 'Yaw Rate': -15.0}]),
            {'session_id': 'practice_1'},
            pd.DataFrame([{'Name': 'Test Driver', 'LapTimeSeconds': 60.0}])
        )),
        patch('upload_handlers.populate_db.populate_single_session', return_value='2026_09_13_Lydd_practice_1'),
        patch('upload_handlers.invalidate_plot_cache'),
    ):
        client.set_cookie('auth_key', 'has_tel_key')
        resp = client.post(
            '/api/upload/stream',
            data=csv_payload,
            content_type='text/csv; charset=utf-8',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'imported successfully' in data['message']
