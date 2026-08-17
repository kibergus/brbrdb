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
import csv
import json
import io
import datetime
from unittest.mock import patch
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
import app
import upload_handlers
from database import db, config


@pytest.fixture(autouse=True)
def mock_upload_handlers_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upload_handlers, 'DATA_DIR', str(tmp_path))


def test_upload_endpoints_require_auth() -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'upload_sessions': {
                'drivers': ['*']
            }
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        # 1. No key -> should fail with 401
        response = client.post(
            '/api/upload/stream',
            json={},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 401
        assert response.get_json() == {'error': 'Unauthorized: Invalid or missing API key.'}

        # 2. Invalid key -> should fail with 401
        response = client.post(
            '/api/upload/stream',
            json={},
            headers={'X-API-Key': 'bad_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 401
        assert response.get_json() == {'error': 'Unauthorized: Invalid or missing API key.'}


def test_upload_endpoints_forbidden_acl() -> None:
    client = app.app.test_client()
    mock_keys = {
        'no_upload_key': {
            'upload_sessions': None
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        response = client.post(
            '/api/upload/stream',
            data='',
            headers={'X-API-Key': 'no_upload_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 403
        assert response.get_json() == {'error': 'Forbidden: No session upload permission.'}


def test_files_upload_endpoint() -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'upload_sessions': {
                'drivers': ['Driver A'],
                'leagues': ['*']
            }
        }
    }
    with (patch('auth.load_keys', return_value=mock_keys),
          patch('upload_handlers.populate_db.populate_single_session', return_value='session_123') as mock_populate):

        # 1. Missing files -> 400
        response = client.post(
            '/api/upload/files',
            data={},
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 400
        assert response.get_json() == {'error': 'Missing metadata or results files.'}

        # 2. Driver not allowed in ACL -> 403
        metadata = {
            'league': 'kartsim',
            'class_name': 'cadet',
            'track_name': 'Rowrah',
            'session_name': 'Practice',
            'session_start_datetime': '2026-05-10 14:30'
        }
        results_csv = 'Pos,Positions Gained,No,Name,Laps,Time\n1,0,10,Driver B,5,5:00.000\n'
        data = {
            'metadata': (io.BytesIO(json.dumps(metadata).encode('utf-8')), 'metadata.json'),
            'results': (io.BytesIO(results_csv.encode('utf-8')), 'results.csv')
        }
        response = client.post(
            '/api/upload/files',
            data=data,
            content_type='multipart/form-data',
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 403
        assert 'is not permitted' in response.get_json()['error']

        # 3. Successful upload -> 200
        results_csv_ok = 'Pos,Positions Gained,No,Name,Laps,Time\n1,0,10,Driver A,5,5:00.000\n'
        data_ok = {
            'metadata': (io.BytesIO(json.dumps(metadata).encode('utf-8')), 'metadata.json'),
            'results': (io.BytesIO(results_csv_ok.encode('utf-8')), 'results.csv')
        }
        response = client.post(
            '/api/upload/files',
            data=data_ok,
            content_type='multipart/form-data',
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 200
        assert response.get_json() == {
            'message': 'Files uploaded and session imported successfully.',
            'session_id': 'session_123'
        }
        mock_populate.assert_called_once()

        # 4. Successful upload with list class_name and alias resolution -> 200
        metadata_list = {
            'league': 'kartsim',
            'class_name': ['KSP_IWC_RE_UK', 'cadet'],
            'track_name': 'Rowrah',
            'session_name': 'Practice',
            'session_start_datetime': '2026-05-10 14:30'
        }
        data_list = {
            'metadata': (io.BytesIO(json.dumps(metadata_list).encode('utf-8')), 'metadata.json'),
            'results': (io.BytesIO(results_csv_ok.encode('utf-8')), 'results.csv')
        }
        mock_populate.reset_mock()
        response = client.post(
            '/api/upload/files',
            data=data_list,
            content_type='multipart/form-data',
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 200
        mock_populate.assert_called_once()
        # Verify the saved metadata JSON contains resolved class names
        called_meta_path = mock_populate.call_args[0][0]
        with open(called_meta_path, 'r') as f:
            saved_meta = json.load(f)
        assert saved_meta['class_name'] == ['iame_waterswift_restricted_cadet_uk', 'cadet']


def test_stream_upload_flow() -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'upload_sessions': {
                'drivers': ['Driver A'],
                'leagues': ['*']
            },
            'kartsim_data': True
        }
    }

    # Stream a raw CSV with all metadata in the header
    csv_data = (
        'Track name,Rowrah\n'
        'Date,2026-05-10\n'
        'Time,14:30:00\n'
        'Driver name,Driver A\n'
        'League,kartsim\n'
        'Class,cadet\n'
        'Session,Practice\n'
        '\n'
        'Time,Latitude,Longitude,Speed,Lap\n'
        '2026-05-10T14:30:00.000Z,54.123,-3.123,10.0,1\n'
    )

    # Stream a raw CSV with a forbidden driver in the header
    csv_data_forbidden = (
        'Track name,Rowrah\n'
        'Date,2026-05-10\n'
        'Time,14:30:00\n'
        'Driver name,Driver B\n'
        'League,kartsim\n'
        'Class,cadet\n'
        'Session,Practice\n'
        '\n'
        'Time,Latitude,Longitude,Speed,Lap\n'
        '2026-05-10T14:30:00.000Z,54.123,-3.123,10.0,1\n'
    )

    with patch('auth.load_keys', return_value=mock_keys):
        # 1. Test forbidden driver check
        response = client.post(
            '/api/upload/stream',
            data=csv_data_forbidden,
            headers={
                'X-API-Key': 'test_key'
            },
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 403
        assert 'Forbidden: Upload for driver' in response.get_json()['error']

        # 2. Test successful stream upload flow
        patch_path = 'upload_handlers.populate_db.populate_single_session'
        with patch(patch_path, return_value='session_stream_123') as mock_populate:
            with patch('upload_handlers.db.get_track', return_value={}) as mock_get_track:
                response = client.post(
                    '/api/upload/stream',
                    data=csv_data,
                    headers={
                        'X-API-Key': 'test_key'
                    },
                    environ_base={'REMOTE_ADDR': '192.168.1.100'}
                )
                assert response.status_code == 200
                assert response.get_json() == {
                    'message': 'Session completed and imported successfully.',
                    'session_id': 'session_stream_123'
                }
                mock_populate.assert_called_once()
                mock_get_track.assert_called()

        # 3. Test successful stream upload flow again
        with patch(patch_path, return_value='session_stream_456') as mock_populate:
            with patch('upload_handlers.db.get_track', return_value={}) as mock_get_track:
                response = client.post(
                    '/api/upload/stream',
                    data=csv_data,
                    headers={
                        'X-API-Key': 'test_key'
                    },
                    environ_base={'REMOTE_ADDR': '192.168.1.100'}
                )
                assert response.status_code == 200
                assert response.get_json() == {
                    'message': 'Session completed and imported successfully.',
                    'session_id': 'session_stream_456'
                }
                mock_populate.assert_called_once()
                mock_get_track.assert_called()


def test_stream_upload_missing_or_invalid_params() -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'upload_sessions': {
                'drivers': ['Driver A'],
                'leagues': ['*']
            },
            'kartsim_data': True
        }
    }

    with patch('auth.load_keys', return_value=mock_keys):
        # Missing all parameters
        response = client.post(
            '/api/upload/stream',
            data='',
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 400
        assert response.get_json() == {'error': 'League is missing'}

        # Missing one parameter (e.g. Class)
        csv_missing_class = (
            'Track name,Rowrah\n'
            'Date,2026-05-10\n'
            'Time,14:30:00\n'
            'Driver name,Driver A\n'
            'League,kartsim\n'
            'Session,Practice\n'
            '\n'
            'Time,Latitude,Longitude,Speed,Lap\n'
            '2026-05-10T14:30:00.000Z,54.123,-3.123,10.0,1\n'
        )
        response = client.post(
            '/api/upload/stream',
            data=csv_missing_class,
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 400
        assert response.get_json() == {'error': 'Class is missing'}

        # Invalid session_start_datetime format
        csv_invalid_date = (
            'Track name,Rowrah\n'
            'Date,invalid-date\n'
            'Time,14:30:00\n'
            'Driver name,Driver A\n'
            'League,kartsim\n'
            'Class,cadet\n'
            'Session,Practice\n'
            '\n'
            'Time,Latitude,Longitude,Speed,Lap\n'
            '2026-05-10T14:30:00.000Z,54.123,-3.123,10.0,1\n'
        )
        response = client.post(
            '/api/upload/stream',
            data=csv_invalid_date,
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 400
        assert response.get_json() == {'error': 'Invalid session_start_datetime format. Must be YYYY-MM-DD HH:MM.'}


def test_stream_upload_league_forbidden() -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'upload_sessions': {
                'drivers': ['Driver A'],
                'leagues': ['rotax']
            }
        }
    }
    csv_data = (
        'Track name,Rowrah\n'
        'Date,2026-05-10\n'
        'Time,14:30:00\n'
        'Driver name,Driver A\n'
        'League,kartsim\n'
        'Class,cadet\n'
        'Session,Practice\n'
        '\n'
        'Time,Latitude,Longitude,Speed,Lap\n'
        '2026-05-10T14:30:00.000Z,54.123,-3.123,10.0,1\n'
    )
    with patch('auth.load_keys', return_value=mock_keys):
        response = client.post(
            '/api/upload/stream',
            data=csv_data,
            headers={
                'X-API-Key': 'test_key'
            },
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 403
        assert "Upload for league 'kartsim' is not permitted" in response.get_json()['error']


def test_files_upload_league_forbidden() -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'upload_sessions': {
                'drivers': ['Driver A'],
                'leagues': ['rotax']
            }
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        metadata = {
            'league': 'kartsim',
            'class_name': 'cadet',
            'track_name': 'Rowrah',
            'session_name': 'Practice',
            'session_start_datetime': '2026-05-10 14:30'
        }
        results_csv = 'Pos,Positions Gained,No,Name,Laps,Time\n1,0,10,Driver A,5,5:00.000\n'
        data = {
            'metadata': (io.BytesIO(json.dumps(metadata).encode('utf-8')), 'metadata.json'),
            'results': (io.BytesIO(results_csv.encode('utf-8')), 'results.csv')
        }
        response = client.post(
            '/api/upload/files',
            data=data,
            content_type='multipart/form-data',
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 403
        assert "Upload for league 'kartsim' is not permitted" in response.get_json()['error']


def test_stream_upload_overwrite_existing_session_forbidden() -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'upload_sessions': {
                'drivers': ['Driver A'],
                'leagues': ['*']
            }
        }
    }
    csv_data = (
        'Track name,Rowrah\n'
        'Date,2026-05-10\n'
        'Time,14:30:00\n'
        'Driver name,Driver A\n'
        'League,kartsim\n'
        'Class,cadet\n'
        'Session,Practice\n'
        '\n'
        'Time,Latitude,Longitude,Speed,Lap\n'
        '2026-05-10T14:30:00.000Z,54.123,-3.123,10.0,1\n'
    )
    mock_df = pd.DataFrame([{'SessionID': '14_30_practice', 'Name': 'Driver B'}])
    with (patch('auth.load_keys', return_value=mock_keys),
          patch('race_db.RaceDB.load', return_value=mock_df)):
        response = client.post(
            '/api/upload/stream',
            data=csv_data,
            headers={
                'X-API-Key': 'test_key'
            },
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 403
        assert "You do not have permission to upload for driver 'Driver B'" in response.get_json()['error']


def test_files_upload_overwrite_existing_session_forbidden() -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'upload_sessions': {
                'drivers': ['Driver A'],
                'leagues': ['*']
            }
        }
    }
    mock_df = pd.DataFrame([{'SessionID': '14_30_practice', 'Name': 'Driver B'}])
    with (patch('auth.load_keys', return_value=mock_keys),
          patch('race_db.RaceDB.load', return_value=mock_df)):
        metadata = {
            'league': 'kartsim',
            'class_name': 'cadet',
            'track_name': 'Rowrah',
            'session_name': 'Practice',
            'session_start_datetime': '2026-05-10 14:30'
        }
        results_csv = 'Pos,Positions Gained,No,Name,Laps,Time\n1,0,10,Driver A,5,5:00.000\n'
        data = {
            'metadata': (io.BytesIO(json.dumps(metadata).encode('utf-8')), 'metadata.json'),
            'results': (io.BytesIO(results_csv.encode('utf-8')), 'results.csv')
        }
        response = client.post(
            '/api/upload/files',
            data=data,
            content_type='multipart/form-data',
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 403
        assert "You do not have permission to upload for driver 'Driver B'" in response.get_json()['error']


def test_stream_upload_exceeds_max_size_header() -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'upload_sessions': {
                'drivers': ['Driver A'],
                'leagues': ['*']
            }
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        # Set MAX_METADATA_SIZE to 10 bytes for testing
        with patch('upload_handlers.MAX_METADATA_SIZE', 10):
            response = client.post(
                '/api/upload/stream',
                headers={
                    'X-API-Key': 'test_key',
                    'Content-Length': '11'
                },
                data='12345678901',
                environ_base={'REMOTE_ADDR': '192.168.1.100'}
            )
            assert response.status_code == 400
            assert 'exceeds' in response.get_json()['error']


def test_stream_upload_exceeds_max_size_stream() -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'upload_sessions': {
                'drivers': ['Driver A'],
                'leagues': ['*']
            }
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        with patch('upload_handlers.MAX_METADATA_SIZE', 10):
            response = client.post(
                '/api/upload/stream',
                headers={
                    'X-API-Key': 'test_key'
                },
                input_stream=io.BytesIO(b'12345678901'),
                environ_base={'REMOTE_ADDR': '192.168.1.100'}
            )
            assert response.status_code == 400
            assert 'exceeds' in response.get_json()['error']


def test_interpolate_lap_distances() -> None:
    # 4-point square centerline
    centerline = [
        {'lat': 0.0, 'lon': 0.0, 'dist': 0.0},
        {'lat': 0.0, 'lon': 0.1, 'dist': 10.0},
        {'lat': 0.1, 'lon': 0.1, 'dist': 20.0},
        {'lat': 0.1, 'lon': 0.0, 'dist': 30.0}
    ]
    lap_length = 40.0

    # 3 points along the first segment (0, 0) -> (0, 0.1)
    # Point 1 is at 25% of the segment (0.0, 0.025) -> dist should be 2.5
    # Point 2 is at 50% of the segment (0.0, 0.05) -> dist should be 5.0
    # Point 3 is at 75% of the segment (0.0, 0.075) -> dist should be 7.5
    lat_arr = np.array([0.0, 0.0, 0.0])
    lon_arr = np.array([0.025, 0.05, 0.075])
    lap_col = np.array([1, 1, 1])

    cl_lat = np.array([p['lat'] for p in centerline])
    cl_lon = np.array([p['lon'] for p in centerline])

    lats_v = lat_arr[:, None]
    lons_v = lon_arr[:, None]

    # We use a latitude of 0.0, so cos(0) = 1.0 (no distortion)
    cos_lat_track = 1.0
    d2_matrix = (cl_lat[None, :] - lats_v)**2 + ((cl_lon[None, :] - lons_v) * cos_lat_track)**2

    dists = upload_handlers.interpolate_lap_distances(
        lat_arr, lon_arr, lap_col, centerline, lap_length, d2_matrix
    )

    # We expect close to [2.5, 5.0, 7.5]
    np.testing.assert_allclose(dists, [2.5, 5.0, 7.5], rtol=1e-5)


def test_process_telemetry_derivative_data_dynamic_freq() -> None:
    # 100 Hz sampling (10 ms interval)
    records = []
    # Create two laps: lap 1 has 100 points, lap 2 has 200 points
    start_dt = datetime.datetime(2026, 6, 6, 21, 35, 51)

    # Lap 1: 100 points (from t=0ms to t=990ms, elapsed time = 990ms)
    for i in range(100):
        t = start_dt + datetime.timedelta(milliseconds=i*10)
        records.append({
            'Time': t.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
            'Latitude': 54.123,
            'Longitude': -3.123,
            'Speed': 10.0,
            'Lap': 1,
            'LapDistance': float(i)
        })

    # Lap 2: 200 points (from t=1000ms to t=2990ms, elapsed time = 1990ms)
    for i in range(200):
        t = start_dt + datetime.timedelta(milliseconds=(100 + i)*10)
        records.append({
            'Time': t.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z',
            'Latitude': 54.123,
            'Longitude': -3.123,
            'Speed': 10.0,
            'Lap': 2,
            'LapDistance': float(i)
        })

    session_info = upload_handlers.CSVSessionMetadata(
        driver_name='Driver A',
        track_name='Rowrah',
        league='kartsim',
        class_name='cadet',
        session_name='Practice',
        session_start_datetime=start_dt,
        kart_number='None',
        track_conditions='Dry',
        temperature='20',
        weather='Sunny'
    )

    with (patch('upload_handlers.db.get_track', return_value={}),
          patch('upload_handlers.is_lap_valid', return_value=True)):
        df_telemetry, metadata_json, df_summary = upload_handlers.process_telemetry_derivative_data(
            records, session_info, '/tmp'
        )

        # Lap 1: elapsed time is 0.99s.
        # Lap 2: elapsed time is 1.99s.
        assert df_summary['Lap 1'].values[0] == '0.990'
        assert df_summary['Lap 2'].values[0] == '1.990'


def test_stream_upload_pruning_and_merging(tmp_path: Path) -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'upload_sessions': {
                'drivers': ['Driver A'],
                'leagues': ['*']
            },
            'kartsim_data': True
        }
    }

    # Use a temporary directory for DATA_DIR
    temp_data_dir = str(tmp_path)

    # We need to pre-create some telemetry in the temporary directory.
    # The session start datetime is 2026-05-10 14:30.
    # The meeting folder will be 2026_05_10_rowrah.
    # The telemetry file will be under:
    # cadet/2026_05_10_rowrah/telemetry/14_30_practice_driver_a.csv
    meeting_dir = os.path.join(temp_data_dir, 'kartsim', 'cadet', '2026_05_10_rowrah')
    telemetry_dir = os.path.join(meeting_dir, 'telemetry')
    os.makedirs(telemetry_dir, exist_ok=True)
    telemetry_path = os.path.join(telemetry_dir, '14_30_practice_driver_a.csv')

    # Pre-existing telemetry file with timestamps:
    # 2026-05-10T14:30:00.000Z
    # 2026-05-10T14:30:01.000Z
    # 2026-05-10T14:30:02.000Z
    pre_existing_csv = (
        'Format,RaceTools CSV\n'
        'Track name,Rowrah\n'
        'Date,2026-05-10\n'
        'Time,14:30:00\n'
        'Driver name,Driver A\n'
        'League,kartsim\n'
        'Class,cadet\n'
        'Session,Practice\n'
        '\n'
        'Record,Time,Latitude,Longitude,Speed (m/s),GForceLat,GForceLon,'
        'GForceVert,Throttle (%),Brake (%),Steering Wheel Angle (deg),Lap\n'
        '1,2026-05-10T14:30:00.000Z,54.123,-3.123,10.0,0.1,0.2,0.3,50.0,0.0,10.0,1\n'
        '2,2026-05-10T14:30:01.000Z,54.124,-3.124,11.0,0.1,0.2,0.3,60.0,0.0,12.0,1\n'
        '3,2026-05-10T14:30:02.000Z,54.125,-3.125,12.0,0.1,0.2,0.3,70.0,0.0,14.0,1\n'
    )
    with open(telemetry_path, 'w', encoding='utf-8') as f:
        f.write(pre_existing_csv)

    # New uploaded stream starting at 2026-05-10T14:30:01.500Z:
    # This means the minimal timestamp in the upload is 14:30:01.500.
    # Pre-existing timestamps >= 14:30:01.500 should be deleted (i.e. point 3 at 14:30:02.000Z).
    # Pre-existing timestamps < 14:30:01.500 should be kept (i.e. point 1 at 14:30:00.000Z, point 2 at 14:30:01.000Z).
    new_upload_csv = (
        'Track name,Rowrah\n'
        'Date,2026-05-10\n'
        'Time,14:30:00\n'
        'Driver name,Driver A\n'
        'League,kartsim\n'
        'Class,cadet\n'
        'Session,Practice\n'
        '\n'
        'Time,Latitude,Longitude,Speed,Lap\n'
        '2026-05-10T14:30:01.500Z,54.1245,-3.1245,11.5,1\n'
        '2026-05-10T14:30:02.500Z,54.1255,-3.1255,12.5,1\n'
    )

    with (patch('auth.load_keys', return_value=mock_keys),
          patch('upload_handlers.DATA_DIR', temp_data_dir),
          patch('upload_handlers.db.get_track', return_value={}),
          patch('upload_handlers.populate_db.populate_single_session', return_value='session_merged_123')):

        response = client.post(
            '/api/upload/stream',
            data=new_upload_csv,
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 200

    # Let's verify the final merged telemetry CSV file.
    # It should contain:
    # - Point 1 (14:30:00.000Z) from pre-existing
    # - Point 2 (14:30:01.000Z) from pre-existing
    # - New Point 1 (14:30:01.500Z) from new upload
    # - New Point 2 (14:30:02.500Z) from new upload
    # - Point 3 (14:30:02.000Z) should be GONE (deleted).

    with open(telemetry_path, 'r', encoding='utf-8') as f:
        lines = f.read().splitlines()

    # Find the data start
    blank_idx = -1
    for idx, line in enumerate(lines):
        if not line.strip():
            blank_idx = idx
            break

    data_lines = lines[blank_idx + 1:]
    reader = csv.DictReader(data_lines)
    records = list(reader)

    assert len(records) == 4

    # Check timestamps and order
    assert records[0]['Time'] == '2026-05-10T14:30:00.000Z'
    assert records[1]['Time'] == '2026-05-10T14:30:01.000Z'
    assert records[2]['Time'] == '2026-05-10T14:30:01.500Z'
    assert records[3]['Time'] == '2026-05-10T14:30:02.500Z'


def test_is_lap_valid_llandow_session() -> None:
    """Tests lap splitting and lap validity for Llandow telemetry session (testdata/llandow.csv).

    Verifies that:
    - Normal full laps (laps 2 through 13) are evaluated as valid (True).
    - Shortened/off-track laps (lap 14 cutthrough and lap 15 carpark) are evaluated as invalid (False).
    """
    testdata_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', 'testdata', 'llandow.csv')
    )
    with open(testdata_path, 'r', encoding='utf-8') as f:
        content = f.read()

    records, _ = upload_handlers.parse_streamed_csv_records(content)
    reader = csv.reader(content.splitlines())
    rows = list(reader)
    blank_idx = next(
        i for i, r in enumerate(rows)
        if not r or all(cell.strip() == '' for cell in r)
    )
    session_info = upload_handlers.parse_csv_metadata_rows(rows[:blank_idx])

    df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(
        records, session_info, config['data_dir']
    )

    db._track_mappings = None
    track_data = db.get_track(session_info.track_name)
    assert track_data is not None, f"Track data for '{session_info.track_name}' should exist"
    turns = track_data.get('turns', [])
    lap_length = float(track_data.get('lap_length', 1000.0))

    lap_numbers = sorted(df_telemetry['Lap'].astype(int).unique())
    assert len(lap_numbers) >= 14, f"Expected at least 14 laps, got {len(lap_numbers)}"

    validity = {}
    for lap_num in lap_numbers:
        lap_dists = (
            df_telemetry[df_telemetry['Lap'].astype(int) == lap_num]['Lap Distance']
            .astype(float)
            .values
        )
        validity[lap_num] = upload_handlers.is_lap_valid(lap_dists, turns, lap_length)

    # Full laps 2..13 must be valid
    for lap_num in range(2, 14):
        assert validity[lap_num] is True, f"Lap {lap_num} should be valid"

    # Laps 14 and 15 (if present) are cutthrough / carpark laps and must NOT be valid
    assert validity[14] is False, "Lap 14 (cutthrough shortcut) should be invalid"
    if 15 in validity:
        assert validity[15] is False, "Lap 15 (carpark section) should be invalid"


def test_stream_upload_preserves_alphatiming_session(tmp_path: Path) -> None:
    """Verifies that streaming telemetry for a session with pre-existing AlphaTiming data
    saves the driver's telemetry file without overwriting the official metadata, summary CSV, or DB session.
    """
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'upload_sessions': {
                'drivers': ['Driver A'],
                'leagues': ['*']
            }
        }
    }

    temp_data_dir = str(tmp_path)
    meeting_dir = os.path.join(temp_data_dir, 'club100', 'cadet', '2026_05_10_rowrah')
    os.makedirs(meeting_dir, exist_ok=True)

    meta_path = os.path.join(meeting_dir, '14_30_practice_metadata.json')
    summary_path = os.path.join(meeting_dir, '14_30_practice.csv')

    official_meta = {
        'alphatiming_url': 'https://results.alphatiming.co.uk/club100/e/123/s/456/result',
        'session_start_datetime': '2026-05-10 14:30',
        'track_name': 'Rowrah',
        'league': 'club100',
        'class_name': 'cadet',
        'session_name': 'Practice'
    }
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(official_meta, f, indent=4)

    official_summary_csv = (
        'Pos,Positions Gained,No,Name,Laps,Time,Gap,Best,On,Lap 1,Lap 2\n'
        '1,0,10,Driver B,2,2:00.000,,1:00.000,1,1:00.000,1:00.000\n'
        '2,0,12,Driver A,2,2:00.000,0.000,1:00.000,1,1:00.000,1:00.000\n'
    )
    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write(official_summary_csv)

    gopro_csv_data = (
        'Track name,Rowrah\n'
        'Date,2026-05-10\n'
        'Time,14:30:00\n'
        'Driver name,Driver A\n'
        'League,club100\n'
        'Class,cadet\n'
        'Session,Practice\n'
        '\n'
        'Time,Latitude,Longitude,Speed,Lap\n'
        '2026-05-10T14:30:00.000Z,54.123,-3.123,10.0,1\n'
    )

    with (patch('auth.load_keys', return_value=mock_keys),
          patch('upload_handlers.DATA_DIR', temp_data_dir),
          patch('upload_handlers.db.get_track', return_value={}),
          patch('upload_handlers.populate_db.populate_single_session') as mock_populate):

        response = client.post(
            '/api/upload/stream',
            data=gopro_csv_data,
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 200
        assert response.get_json()['session_id'] == '2026_05_10_rowrah_14_30_practice'

        # populate_single_session should NOT have been called (preserving official DB session)
        mock_populate.assert_not_called()

    # Metadata JSON should retain original alphatiming_url
    with open(meta_path, 'r', encoding='utf-8') as f:
        saved_meta = json.load(f)
    assert saved_meta['alphatiming_url'] == 'https://results.alphatiming.co.uk/club100/e/123/s/456/result'

    # Summary CSV should retain official results for all drivers
    with open(summary_path, 'r', encoding='utf-8') as f:
        saved_summary = f.read()
    assert saved_summary == official_summary_csv

    # Telemetry CSV for Driver A should be created in telemetry directory
    telemetry_path = os.path.join(meeting_dir, 'telemetry', '14_30_practice_driver_a.csv')
    assert os.path.exists(telemetry_path)


def test_process_telemetry_derivative_data_resolves_track_alias() -> None:
    """Ensure kartsim telemetry with known and alias track names resolves via track_data and computes coordinates."""
    db._track_mappings = None
    records = [
        {'Time': '2026-08-17T18:45:00.000Z', 'x': 10.0, 'z': 20.0, 'GPSSpeed': 15.0, 'Lap': 1},
        {'Time': '2026-08-17T18:45:00.100Z', 'x': 11.0, 'z': 21.0, 'GPSSpeed': 15.2, 'Lap': 1},
    ]
    known_tracks = [
        'Bayford Meadows',
        'Brentwood',
        'Buckmore Park',
        'Clay Pigeon',
        'Dunkeswell Raceway',
        'Ellough Park Kart Circuit',
        'Forest Edge',
        'Fulbeck',
        'GYG Championship Pro Circuit',
        'Llandow',
        'Lydd',
        'Rissington 2026',
        'Rowrah',
        'Rye House Layout 2 2026',
        'South Wales Karting Centre 2026',
    ]

    for track_name in known_tracks:
        session_info = upload_handlers.CSVSessionMetadata(
            track_name=track_name,
            session_name='Practice',
            driver_name='Alexey Guseynov',
            league='kartsim',
            class_name='iame_waterswift_restricted_cadet_uk',
            session_start_datetime=datetime.datetime(2026, 8, 17, 18, 45),
        )

        df_telemetry, metadata_json, _ = upload_handlers.process_telemetry_derivative_data(
            records, session_info, config['data_dir']
        )
        assert not df_telemetry.empty
        assert 'Latitude' in df_telemetry.columns
        assert 'Longitude' in df_telemetry.columns
        assert np.all(df_telemetry['Latitude'] != 0.0), f"Latitude is 0 for track '{track_name}'"
        assert np.all(df_telemetry['Longitude'] != 0.0), f"Longitude is 0 for track '{track_name}'"
        if track_name == 'GYG Championship Pro Circuit':
            assert metadata_json['track_name'] == 'Glan Y Gors'


def test_process_telemetry_derivative_data_does_not_inject_missing_columns() -> None:
    records = [
        {
            'Time': '2026-08-02T11:18:00.000Z',
            'Latitude': 51.4335,
            'Longitude': -3.4960,
            'Speed': 15.0,
            'GForceLat': 0.5,
            'GForceLon': 0.2
        },
        {
            'Time': '2026-08-02T11:18:00.100Z',
            'Latitude': 51.4336,
            'Longitude': -3.4961,
            'Speed': 15.5,
            'GForceLat': 0.6,
            'GForceLon': 0.3
        }
    ]
    session_info = upload_handlers.CSVSessionMetadata(
        track_name='Llandow',
        session_name='Practice',
        driver_name='Katia Guseinova',
        league='club100_south',
        class_name='cadet_lw',
        session_start_datetime=datetime.datetime(2026, 8, 2, 11, 18),
    )

    with (patch('upload_handlers.db.get_track', return_value={}),
          patch('upload_handlers.is_lap_valid', return_value=True)):
        df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(
            records, session_info, '/tmp'
        )

        assert 'Throttle' not in df_telemetry.columns
        assert 'Brake' not in df_telemetry.columns
        assert 'Steering Angle' not in df_telemetry.columns
        assert 'RPS FL' not in df_telemetry.columns
        assert 'Tyre Load FL' not in df_telemetry.columns
        assert 'Ori Quat X' not in df_telemetry.columns
        assert 'Toe FL' not in df_telemetry.columns
        assert 'Direction of Travel' not in df_telemetry.columns
        assert 'Speed' in df_telemetry.columns
        assert 'GForceLat' in df_telemetry.columns
        assert 'GForceLon' in df_telemetry.columns
