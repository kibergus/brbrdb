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
import math
import datetime
from unittest.mock import patch
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
import app
import upload_handlers
from upload_handlers import CSVSessionMetadata
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
            'see_telemetry': True
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
            'see_telemetry': True
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
            'see_telemetry': True
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
        'GForceVert,Throttle,Brake,Steering Wheel Angle (deg),Lap\n'
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


def test_process_telemetry_derivative_data_empty_lat_lon_strings() -> None:
    records = [
        {
            'Time': '2026-09-05T09:21:00.000Z',
            'Latitude': '',
            'Longitude': '',
            'Speed': '',
            'GForceLat': '0.086',
            'GForceLon': '-0.076'
        },
        {
            'Time': '2026-09-05T09:21:00.100Z',
            'Latitude': '50.8523',
            'Longitude': '-2.5831',
            'Speed': '12.5',
            'GForceLat': '0.12',
            'GForceLon': '0.05'
        }
    ]
    session_info = upload_handlers.CSVSessionMetadata(
        track_name='Clay Pigeon',
        session_name='Practice',
        driver_name='Katia Guseinova',
        league='fat_pro',
        class_name='cadet',
        session_start_datetime=datetime.datetime(2026, 9, 5, 9, 21),
    )

    with (patch('upload_handlers.db.get_track', return_value={}),
          patch('upload_handlers.is_lap_valid', return_value=True)):
        df_telemetry, metadata_json, _ = upload_handlers.process_telemetry_derivative_data(
            records, session_info, '/tmp'
        )

        assert len(df_telemetry) == 2
        assert df_telemetry.iloc[0]['Latitude'] == ''
        assert df_telemetry.iloc[0]['Longitude'] == ''
        assert float(df_telemetry.iloc[1]['Latitude']) == 50.8523
        assert float(df_telemetry.iloc[1]['Longitude']) == -2.5831


def test_process_telemetry_derivative_data_gyroscope_channels() -> None:
    records = [
        {
            'Time': '2026-09-05T09:21:00.000Z',
            'Latitude': '50.8523',
            'Longitude': '-2.5831',
            'Speed': '12.5',
            'Yaw Rate': '15.2345',
            'Pitch Rate': '-2.100',
            'Roll Rate': '0.5'
        },
        {
            'Time': '2026-09-05T09:21:00.100Z',
            'Latitude': '50.8524',
            'Longitude': '-2.5830',
            'Speed': '13.0',
            'Yaw Rate': '-10.5',
            'Pitch Rate': '0.0',
            'Roll Rate': '-0.2'
        }
    ]
    session_info = upload_handlers.CSVSessionMetadata(
        track_name='Clay Pigeon',
        session_name='Practice',
        driver_name='Katia Guseinova',
        league='fat_pro',
        class_name='cadet',
        session_start_datetime=datetime.datetime(2026, 9, 5, 9, 21),
    )

    with (patch('upload_handlers.db.get_track', return_value={}),
          patch('upload_handlers.is_lap_valid', return_value=True)):
        df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(
            records, session_info, '/tmp'
        )

        assert 'Yaw Rate' in df_telemetry.columns
        assert 'Pitch Rate' in df_telemetry.columns
        assert 'Roll Rate' in df_telemetry.columns

        assert df_telemetry.iloc[0]['Yaw Rate'] == '15.235'
        assert df_telemetry.iloc[0]['Pitch Rate'] == '-2.100'
        assert df_telemetry.iloc[0]['Roll Rate'] == '0.500'

        assert df_telemetry.iloc[1]['Yaw Rate'] == '-10.500'
        assert df_telemetry.iloc[1]['Pitch Rate'] == '0.000'
        assert df_telemetry.iloc[1]['Roll Rate'] == '-0.200'


def test_process_telemetry_derivative_data_does_not_inject_gyro_channels() -> None:
    records = [
        {
            'Time': '2026-09-05T09:21:00.000Z',
            'Latitude': '50.8523',
            'Longitude': '-2.5831',
            'Speed': '12.5'
        }
    ]
    session_info = upload_handlers.CSVSessionMetadata(
        track_name='Clay Pigeon',
        session_name='Practice',
        driver_name='Katia Guseinova',
        league='fat_pro',
        class_name='cadet',
        session_start_datetime=datetime.datetime(2026, 9, 5, 9, 21),
    )

    with (patch('upload_handlers.db.get_track', return_value={}),
          patch('upload_handlers.is_lap_valid', return_value=True)):
        df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(
            records, session_info, '/tmp'
        )

        assert 'Yaw Rate' not in df_telemetry.columns
        assert 'Pitch Rate' not in df_telemetry.columns
        assert 'Roll Rate' not in df_telemetry.columns


def test_stream_upload_with_gyroscope_data(tmp_path: Path) -> None:
    mock_keys = {
        'test_key': {
            'upload_sessions': {
                'drivers': ['Driver A'],
                'leagues': ['*']
            },
            'see_telemetry': True
        }
    }
    client = app.app.test_client()
    temp_data_dir = str(tmp_path)

    csv_data = (
        'Track name,Rowrah\n'
        'Date,2026-05-10\n'
        'Time,14:30:00\n'
        'Driver name,Driver A\n'
        'League,club100\n'
        'Class,cadet\n'
        'Session,Practice\n'
        '\n'
        'Time,Latitude,Longitude,Speed,Yaw Rate,Pitch Rate,Roll Rate\n'
        '2026-05-10T14:30:00.000Z,54.123,-3.123,10.0,12.345,-1.200,0.500\n'
        '2026-05-10T14:30:00.100Z,54.124,-3.122,12.0,14.500,-1.100,0.450\n'
    )

    with (patch('auth.load_keys', return_value=mock_keys),
          patch('upload_handlers.DATA_DIR', temp_data_dir),
          patch('upload_handlers.db.get_track', return_value={}),
          patch('upload_handlers.populate_db.populate_single_session', return_value='session_123')):

        response = client.post(
            '/api/upload/stream',
            data=csv_data,
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 200

        # Check that saved telemetry file has gyroscope columns
        telemetry_dir = tmp_path / 'club100' / 'cadet' / '2026_05_10_rowrah' / 'telemetry'
        csv_files = list(telemetry_dir.glob('*.csv'))
        assert len(csv_files) == 1

        content = csv_files[0].read_text()
        assert 'Yaw Rate' in content
        assert 'Pitch Rate' in content
        assert 'Roll Rate' in content
        assert '12.345' in content
        assert '-1.200' in content


def test_parse_csv_metadata_rows_distance_to_rear_axle() -> None:
    rows = [
        ['Track name', 'Rowrah'],
        ['Date', '2026-05-10'],
        ['Time', '14:30:00'],
        ['Driver name', 'Driver A'],
        ['League', 'club100'],
        ['Class', 'cadet'],
        ['Session', 'Practice'],
        ['Distance to rear axle', '1.5'],
    ]
    meta = upload_handlers.parse_csv_metadata_rows(rows)
    assert meta.distance_to_rear_axle == 1.5

    # Missing distance
    rows_no_dist = [
        ['Track name', 'Rowrah'],
        ['Date', '2026-05-10'],
        ['Time', '14:30:00'],
        ['Driver name', 'Driver A'],
        ['League', 'club100'],
        ['Class', 'cadet'],
        ['Session', 'Practice'],
    ]
    meta_no_dist = upload_handlers.parse_csv_metadata_rows(rows_no_dist)
    assert meta_no_dist.distance_to_rear_axle is None

    # Invalid distance string
    rows_invalid = rows_no_dist + [['Distance to rear axle', 'invalid_num']]
    meta_invalid = upload_handlers.parse_csv_metadata_rows(rows_invalid)
    assert meta_invalid.distance_to_rear_axle is None


def test_process_telemetry_derivative_data_distance_to_rear_axle() -> None:
    session_info = upload_handlers.CSVSessionMetadata(
        driver_name='Driver A',
        track_name='Rowrah',
        league='club100',
        class_name='cadet',
        session_name='Practice',
        session_start_datetime=datetime.datetime(2026, 5, 10, 14, 30),
        distance_to_rear_axle=1.75
    )
    records = [
        {
            'Time': '2026-05-10T14:30:00.000Z',
            'Latitude': '54.123',
            'Longitude': '-3.123',
            'Speed': '10.0',
        }
    ]
    with patch('upload_handlers.db.get_track', return_value={}):
        _, metadata_json, _ = upload_handlers.process_telemetry_derivative_data(
            records, session_info, '/dummy/dir'
        )
        assert metadata_json.get('distance_to_rear_axle') == 1.75

    session_info_none = upload_handlers.CSVSessionMetadata(
        driver_name='Driver A',
        track_name='Rowrah',
        league='club100',
        class_name='cadet',
        session_name='Practice',
        session_start_datetime=datetime.datetime(2026, 5, 10, 14, 30),
        distance_to_rear_axle=None
    )
    with patch('upload_handlers.db.get_track', return_value={}):
        _, metadata_json_none, _ = upload_handlers.process_telemetry_derivative_data(
            records, session_info_none, '/dummy/dir'
        )
        assert 'distance_to_rear_axle' not in metadata_json_none


def test_stream_upload_with_distance_to_rear_axle(tmp_path: Path) -> None:
    mock_keys = {
        'test_key': {
            'upload_sessions': {
                'drivers': ['Driver A'],
                'leagues': ['*']
            },
            'see_telemetry': True
        }
    }
    client = app.app.test_client()
    temp_data_dir = str(tmp_path)

    csv_data = (
        'Format,RaceBox CSV\n'
        'Data Source,GoPro Telemetry\n'
        'Track,Lydd Karting 2026\n'
        'Track name,Rowrah\n'
        'Date,2026-05-10\n'
        'Time,14:30:00\n'
        'Driver name,Driver A\n'
        'League,club100\n'
        'Class,cadet\n'
        'Session,Practice\n'
        'Distance to rear axle,1.5\n'
        '\n'
        'Time,Latitude,Longitude,Speed\n'
        '2026-05-10T14:30:00.000Z,54.123,-3.123,10.0\n'
        '2026-05-10T14:30:00.100Z,54.124,-3.122,12.0\n'
    )

    with (patch('auth.load_keys', return_value=mock_keys),
          patch('upload_handlers.DATA_DIR', temp_data_dir),
          patch('upload_handlers.db.get_track', return_value={}),
          patch('upload_handlers.populate_db.populate_single_session', return_value='session_123')):

        response = client.post(
            '/api/upload/stream',
            data=csv_data,
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 200

        meeting_dir = tmp_path / 'club100' / 'cadet' / '2026_05_10_rowrah'
        telemetry_dir = meeting_dir / 'telemetry'
        csv_files = list(telemetry_dir.glob('*.csv'))
        assert len(csv_files) == 1

        content = csv_files[0].read_text()
        assert 'Distance to rear axle,1.5' in content

        meta_files = list(meeting_dir.glob('*_metadata.json'))
        assert len(meta_files) == 1
        with open(meta_files[0], 'r', encoding='utf-8') as f:
            meta_json = json.load(f)
        assert meta_json.get('distance_to_rear_axle') == 1.5


def test_compute_gyro_rear_slip_angle_straight() -> None:
    records = [
        {'Time': f'2026-05-10T14:30:0{i:02d}.000Z', 'Yaw Rate': '0.0', 'GForceLat': '0.0'}
        for i in range(10)
    ]
    course = np.full(10, 90.0)
    speeds = np.full(10, 10.0)
    dist = 1.5

    heading, slip_rear = upload_handlers.compute_gyro_rear_slip_angle(records, course, speeds, dist)
    assert len(heading) == 10
    assert len(slip_rear) == 10
    # On a straight, heading matches course (90°) and slip angle is 0.0
    for h in heading:
        assert abs(h - 90.0) < 1e-4
    for s in slip_rear:
        assert abs(s) < 1e-4


def test_compute_gyro_rear_slip_angle_cornering_lever_arm() -> None:
    # Test that lever arm correction subtracts camera lateral swing
    # Pure roll on circle of radius 10m at 10 m/s:
    # Omega_z = 1 rad/s = 57.2958 deg/s (CW, right turn)
    # GoPro Yaw Rate = -57.2958 deg/s
    # Camera at L=1.0m ahead moves at angle atan(1/10) = 0.09967 rad = 5.71 deg to the right of heading
    # With lever arm L=1.0, slip angle at rear axle should be ~0 deg
    # With lever arm L=0.0, slip angle remains ~5.71 deg
    heading_ref = 90.0
    omega_deg = 57.2958
    cam_angle = math.degrees(math.atan2(1.0, 10.0))  # ~5.71°

    # Point 0 is on a straight to anchor initial heading to heading_ref
    records = [{'Time': '2026-05-10T14:30:00.000Z', 'Yaw Rate': '0.0', 'GForceLat': '0.0'}]
    course_list = [heading_ref]

    for i in range(1, 10):
        records.append({
            'Time': f'2026-05-10T14:30:00.{i}00Z',
            'Yaw Rate': str(-omega_deg),
            'GForceLat': '1.0',
        })
        curr_heading = heading_ref + omega_deg * (i * 0.1)
        course_list.append(curr_heading + cam_angle)

    course = np.array(course_list)
    speeds = np.full(10, 10.0)

    # With L = 1.0 m, lever arm eliminates the false slip angle
    _, slip_with_lever = upload_handlers.compute_gyro_rear_slip_angle(
        records, course, speeds, distance_to_rear_axle=1.0
    )
    # With L = 0.0 m, false slip angle of ~5.71° remains
    _, slip_no_lever = upload_handlers.compute_gyro_rear_slip_angle(
        records, course, speeds, distance_to_rear_axle=0.0
    )

    assert abs(slip_with_lever[-1]) < 0.1
    assert abs(slip_no_lever[-1] - cam_angle) < 0.1


def test_process_telemetry_derivative_data_gyro_slip_angle() -> None:
    records = [
        {
            'Time': f'2026-05-10T14:30:00.{i}00Z',
            'Latitude': f'54.{i}',
            'Longitude': '-3.123',
            'Speed': '10.0',
            'Yaw Rate': '15.0',
            'GForceLat': '0.5',
        }
        for i in range(10)
    ]
    meta = CSVSessionMetadata(
        track_name='Rowrah',
        session_start_datetime=datetime.datetime(2026, 5, 10, 14, 30),
        driver_name='Driver A',
        league='club100',
        class_name='cadet',
        session_name='Practice',
        distance_to_rear_axle=1.5,
    )

    with patch('upload_handlers.db.get_track', return_value={}):
        df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(records, meta, '/tmp')

    assert 'Slip Angle Rear' in df_telemetry.columns
    assert 'Kart Heading' in df_telemetry.columns
    assert 'Yaw' in df_telemetry.columns
    # Check that it's populated and not empty
    assert df_telemetry.iloc[-1]['Slip Angle Rear'] != ''


def test_process_telemetry_derivative_data_no_distance_skips_slip_angle() -> None:
    # When distance_to_rear_axle is None, slip angle calculation must be skipped
    records = [
        {
            'Time': f'2026-05-10T14:30:00.{i}00Z',
            'Latitude': f'54.{i}',
            'Longitude': '-3.123',
            'Speed': '10.0',
            'Yaw Rate': '15.0',
        }
        for i in range(5)
    ]
    meta = CSVSessionMetadata(
        track_name='Rowrah',
        session_start_datetime=datetime.datetime(2026, 5, 10, 14, 30),
        driver_name='Driver A',
        league='club100',
        class_name='cadet',
        session_name='Practice',
        distance_to_rear_axle=None,
    )

    with patch('upload_handlers.db.get_track', return_value={}):
        df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(records, meta, '/tmp')

    # Slip Angle Rear should NOT be computed
    assert 'Slip Angle Rear' not in df_telemetry.columns or (df_telemetry['Slip Angle Rear'] == '').all()


def test_process_telemetry_derivative_data_kartsim_quats_unaffected() -> None:
    # When quaternions are present (KartSim), gyro slip angle must be bypassed
    records = [
        {
            'Time': f'2026-05-10T14:30:00.{i}00Z',
            'Latitude': '54.123',
            'Longitude': '-3.123',
            'Speed': '10.0',
            'Ori Quat X': '0.0',
            'Ori Quat Y': '0.0',
            'Ori Quat Z': '0.0',
            'Ori Quat W': '1.0',
            'Yaw Rate': '50.0',  # Gyro channel also present
        }
        for i in range(5)
    ]
    meta = CSVSessionMetadata(
        track_name='Rowrah',
        session_start_datetime=datetime.datetime(2026, 5, 10, 14, 30),
        driver_name='Driver A',
        league='kartsim',
        class_name='cadet',
        session_name='Practice',
        distance_to_rear_axle=1.5,
    )

    with patch('upload_handlers.db.get_track', return_value={}):
        df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(records, meta, '/tmp')

    # Should have run quat_to_yaw path, not gyro integration
    assert 'Ori Quat X' in df_telemetry.columns
    assert 'Slip Angle Rear' in df_telemetry.columns


def test_process_telemetry_derivative_data_existing_raw_slip_unaffected() -> None:
    # When Slip Angle Rear is already in input, do not overwrite it with gyro calculation
    records = [
        {
            'Time': f'2026-05-10T14:30:00.{i}00Z',
            'Latitude': '54.123',
            'Longitude': '-3.123',
            'Speed': '10.0',
            'Yaw Rate': '50.0',
            'Slip Angle Rear': '99.50',
        }
        for i in range(5)
    ]
    meta = CSVSessionMetadata(
        track_name='Rowrah',
        session_start_datetime=datetime.datetime(2026, 5, 10, 14, 30),
        driver_name='Driver A',
        league='club100',
        class_name='cadet',
        session_name='Practice',
        distance_to_rear_axle=1.5,
    )

    with patch('upload_handlers.db.get_track', return_value={}):
        df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(records, meta, '/tmp')
    assert df_telemetry.iloc[0]['Slip Angle Rear'] == '99.50'


def test_compute_gyro_rear_slip_angle_missing_data() -> None:
    # Test handling when gyro data is missing/NaN on some points
    records = [
        {'Time': '2026-05-10T14:30:00.000Z', 'Yaw Rate': '0.0', 'GForceLat': '0.0'},
        {'Time': '2026-05-10T14:30:00.100Z', 'Yaw Rate': '10.0', 'GForceLat': '0.2'},
        {'Time': '2026-05-10T14:30:00.200Z', 'Yaw Rate': '', 'GForceLat': '0.2'},  # missing
        {'Time': '2026-05-10T14:30:00.300Z', 'Yaw Rate': 'nan', 'GForceLat': '0.2'},  # NaN string
        {'Time': '2026-05-10T14:30:00.400Z', 'Yaw Rate': '12.0', 'GForceLat': '0.2'},  # resumed
    ]
    course = np.full(5, 90.0)
    speeds = np.full(5, 10.0)

    heading, slip_rear = upload_handlers.compute_gyro_rear_slip_angle(
        records, course, speeds, distance_to_rear_axle=1.0
    )
    assert not np.isnan(slip_rear[1])
    assert np.isnan(slip_rear[2])  # point 2 missing gyro -> NaN slip
    assert np.isnan(slip_rear[3])  # point 3 NaN gyro -> NaN slip
    assert not np.isnan(slip_rear[4])  # resumed


def test_process_telemetry_derivative_data_all_gyro_nan_skips_slip() -> None:
    # When all Yaw Rate values are NaN or empty, slip angle must not be calculated
    records = [
        {
            'Time': f'2026-05-10T14:30:00.{i}00Z',
            'Latitude': f'54.{i}',
            'Longitude': '-3.123',
            'Speed': '10.0',
            'Yaw Rate': 'nan',
        }
        for i in range(5)
    ]
    meta = CSVSessionMetadata(
        track_name='Rowrah',
        session_start_datetime=datetime.datetime(2026, 5, 10, 14, 30),
        driver_name='Driver A',
        league='club100',
        class_name='cadet',
        session_name='Practice',
        distance_to_rear_axle=1.5,
    )

    with patch('upload_handlers.db.get_track', return_value={}):
        df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(records, meta, '/tmp')

    # Slip Angle Rear should not be computed (column absent or completely empty)
    assert 'Slip Angle Rear' not in df_telemetry.columns or (df_telemetry['Slip Angle Rear'] == '').all()


def test_process_telemetry_derivative_data_partial_gyro_nan() -> None:
    # When some points have NaN gyro, only valid points receive Slip Angle Rear
    records = [
        {
            'Time': '2026-05-10T14:30:00.000Z',
            'Latitude': '54.123',
            'Longitude': '-3.123',
            'Speed': '10.0',
            'Yaw Rate': '0.0',
            'GForceLat': '0.0',
        },
        {
            'Time': '2026-05-10T14:30:00.100Z',
            'Latitude': '54.124',
            'Longitude': '-3.123',
            'Speed': '10.0',
            'Yaw Rate': '15.0',
            'GForceLat': '0.5',
        },
        {
            'Time': '2026-05-10T14:30:00.200Z',
            'Latitude': '54.125',
            'Longitude': '-3.123',
            'Speed': '10.0',
            'Yaw Rate': 'nan',  # NaN gyro
            'GForceLat': '0.5',
        },
    ]
    meta = CSVSessionMetadata(
        track_name='Rowrah',
        session_start_datetime=datetime.datetime(2026, 5, 10, 14, 30),
        driver_name='Driver A',
        league='club100',
        class_name='cadet',
        session_name='Practice',
        distance_to_rear_axle=1.5,
    )

    with patch('upload_handlers.db.get_track', return_value={}):
        df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(records, meta, '/tmp')

    assert df_telemetry.iloc[1]['Slip Angle Rear'] != ''
    assert df_telemetry.iloc[2]['Slip Angle Rear'] == ''
    assert df_telemetry.iloc[2]['Yaw Rate'] == ''


def test_compute_gyro_forces_basic() -> None:
    records = [
        {
            'Time': f'2026-05-10T14:30:00.{i}00Z',
            'Yaw Rate': '10.0',
            'GForceLat': '1.0',
            'GForceLon': '0.2',
            'Steering Angle': '5.0',
        }
        for i in range(10)
    ]
    speeds = np.full(10, 15.0)
    f_yf, f_yr, f_xf, f_xr = upload_handlers.compute_gyro_forces(records, speeds, 0.44)

    assert len(f_yf) == 10
    # In steady state yaw rate, front lateral force should be roughly W_f * 1.0 ≈ 0.423
    assert 0.35 < f_yf[5] < 0.50
    # Rear lateral force should be roughly W_r * 1.0 ≈ 0.577
    assert 0.50 < f_yr[5] < 0.65
    # Sum of front and rear lateral force should equal total lateral acceleration (1.0g)
    assert np.isclose(f_yf[5] + f_yr[5], 1.0, atol=1e-3)
    # Front longitudinal force should be retarding (-f_yf * sin(delta))
    assert f_xf[5] < 0.0
    # Sum of front and rear longitudinal force should equal total longitudinal acceleration (0.2g)
    assert np.isclose(f_xf[5] + f_xr[5], 0.2, atol=1e-3)


def test_process_telemetry_derivative_data_gyro_forces() -> None:
    records = [
        {
            'Time': f'2026-05-10T14:30:00.{i}00Z',
            'Latitude': f'54.{i}',
            'Longitude': '-3.123',
            'Speed': '15.0',
            'Yaw Rate': '12.0',
            'GForceLat': '0.8',
            'GForceLon': '0.1',
        }
        for i in range(10)
    ]
    meta = CSVSessionMetadata(
        track_name='Rowrah',
        session_start_datetime=datetime.datetime(2026, 5, 10, 14, 30),
        driver_name='Driver A',
        league='club100',
        class_name='cadet',
        session_name='Practice',
        distance_to_rear_axle=0.44,
    )

    with patch('upload_handlers.db.get_track', return_value={}):
        df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(records, meta, '/tmp')

    assert 'Lat Force Front' in df_telemetry.columns
    assert 'Lat Force Rear' in df_telemetry.columns
    assert 'Long Force Front' in df_telemetry.columns
    assert 'Long Force Rear' in df_telemetry.columns

    # Verify values are populated and non-empty
    assert df_telemetry.iloc[-1]['Lat Force Front'] != ''
    assert df_telemetry.iloc[-1]['Lat Force Rear'] != ''
    assert df_telemetry.iloc[-1]['Long Force Front'] != ''
    assert df_telemetry.iloc[-1]['Long Force Rear'] != ''


def test_process_telemetry_derivative_data_no_gyro_skips_forces() -> None:
    records = [
        {
            'Time': f'2026-05-10T14:30:00.{i}00Z',
            'Latitude': f'54.{i}',
            'Longitude': '-3.123',
            'Speed': '15.0',
            'GForceLat': '0.8',
            'GForceLon': '0.1',
        }
        for i in range(5)
    ]
    meta = CSVSessionMetadata(
        track_name='Rowrah',
        session_start_datetime=datetime.datetime(2026, 5, 10, 14, 30),
        driver_name='Driver A',
        league='club100',
        class_name='cadet',
        session_name='Practice',
        distance_to_rear_axle=0.44,
    )

    with patch('upload_handlers.db.get_track', return_value={}):
        df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(records, meta, '/tmp')

    # Force channels should NOT be computed/injected when gyro is absent
    assert 'Lat Force Front' not in df_telemetry.columns or (df_telemetry['Lat Force Front'] == '').all()


def test_process_telemetry_derivative_data_forces_partial_nan_gyro() -> None:
    records = [
        {
            'Time': '2026-05-10T14:30:00.000Z',
            'Latitude': '54.123',
            'Longitude': '-3.123',
            'Speed': '10.0',
            'Yaw Rate': '10.0',
            'GForceLat': '0.5',
            'GForceLon': '0.1',
        },
        {
            'Time': '2026-05-10T14:30:00.100Z',
            'Latitude': '54.124',
            'Longitude': '-3.123',
            'Speed': '10.0',
            'Yaw Rate': 'nan',
            'GForceLat': '0.5',
            'GForceLon': '0.1',
        },
    ]
    meta = CSVSessionMetadata(
        track_name='Rowrah',
        session_start_datetime=datetime.datetime(2026, 5, 10, 14, 30),
        driver_name='Driver A',
        league='club100',
        class_name='cadet',
        session_name='Practice',
        distance_to_rear_axle=0.44,
    )

    with patch('upload_handlers.db.get_track', return_value={}):
        df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(records, meta, '/tmp')

    assert df_telemetry.iloc[0]['Lat Force Front'] != ''
    assert df_telemetry.iloc[1]['Lat Force Front'] == ''
    assert df_telemetry.iloc[1]['Long Force Rear'] == ''


def test_process_telemetry_derivative_data_gopro_dummy_columns_allows_slip_and_forces() -> None:
    # GoPro telemetry exported by RaceTools often contains dummy 0.00 for Lat Force FL and
    # partial/empty Slip Angle Rear. These should not block gyro slip angle or per-axle forces.
    records = [
        {
            'Time': f'2026-05-10T14:30:00.{i}00Z',
            'Latitude': f'54.{i}',
            'Longitude': '-3.123',
            'Speed': '15.0',
            'Yaw Rate': '12.0',
            'GForceLat': '0.8',
            'GForceLon': '0.1',
            'Lat Force FL': '0.00',
            'Lat Force FR': '0.00',
            'Slip Angle Rear': '0.00' if i == 0 else '',
        }
        for i in range(5)
    ]
    meta = CSVSessionMetadata(
        track_name='Rowrah',
        session_start_datetime=datetime.datetime(2026, 5, 10, 14, 30),
        driver_name='Driver A',
        league='club100',
        class_name='cadet',
        session_name='Practice',
        distance_to_rear_axle=1.5,
    )

    with patch('upload_handlers.db.get_track', return_value={}):
        df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(records, meta, '/tmp')

    # All points should have Slip Angle Rear and per-axle forces computed
    for i in range(5):
        assert df_telemetry.iloc[i]['Slip Angle Rear'] != ''
        assert df_telemetry.iloc[i]['Lat Force Front'] != ''
        assert df_telemetry.iloc[i]['Lat Force Rear'] != ''


def test_process_telemetry_derivative_data_kartsim_per_wheel_forces_blocks_per_axle_forces() -> None:
    # KartSim physics engine provides real per-wheel forces (e.g. Lat Force FL = 30.0).
    # Per-axle forces must NOT be computed for KartSim.
    records = [
        {
            'Time': f'2026-05-10T14:30:00.{i}00Z',
            'Latitude': f'54.{i}',
            'Longitude': '-3.123',
            'Speed': '15.0',
            'Yaw Rate': '12.0',
            'GForceLat': '0.8',
            'GForceLon': '0.1',
            'Lat Force FL': '30.5',
            'Lat Force FR': '-20.2',
        }
        for i in range(5)
    ]
    meta = CSVSessionMetadata(
        track_name='Rowrah',
        session_start_datetime=datetime.datetime(2026, 5, 10, 14, 30),
        driver_name='Driver A',
        league='kartsim',
        class_name='cadet',
        session_name='Practice',
        distance_to_rear_axle=1.5,
    )

    with patch('upload_handlers.db.get_track', return_value={}):
        df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(records, meta, '/tmp')

    # Per-axle forces should NOT be computed
    assert 'Lat Force Front' not in df_telemetry.columns or (df_telemetry['Lat Force Front'] == '').all()


def test_coordinate_reference_and_bounds() -> None:
    # 1. Test with track centerline
    track_data = {
        'center_line': [
            {'lat': 54.551, 'lon': -3.442, 'dist': 0.0},
            {'lat': 54.552, 'lon': -3.443, 'dist': 100.0}
        ]
    }
    ref_lat, ref_lon, max_dist = upload_handlers.get_track_coordinate_reference(track_data, [], [])
    assert ref_lat is not None and ref_lon is not None
    assert math.isclose(ref_lat, 54.5515, rel_tol=1e-4)
    assert math.isclose(ref_lon, -3.4425, rel_tol=1e-4)
    assert max_dist == 5000.0

    # Within bounds (nearby)
    assert upload_handlers.is_coordinate_within_bounds(54.551, -3.442, ref_lat, ref_lon, max_dist)
    # Outside bounds (North Pole / overseas)
    assert not upload_handlers.is_coordinate_within_bounds(83.7, 73.2, ref_lat, ref_lon, max_dist)
    assert not upload_handlers.is_coordinate_within_bounds(0.0, 0.0, ref_lat, ref_lon, max_dist)
    assert not upload_handlers.is_coordinate_within_bounds(float('nan'), -3.442, ref_lat, ref_lon, max_dist)

    # 2. Test fallback to median when track_data has no coordinates
    lats = [54.551, 54.552, 54.553, 83.7]
    lons = [-3.442, -3.443, -3.441, 73.2]
    ref_lat_med, ref_lon_med, max_dist_med = upload_handlers.get_track_coordinate_reference({}, lats, lons)
    assert ref_lat_med is not None and ref_lon_med is not None
    assert math.isclose(ref_lat_med, 54.552, rel_tol=1e-3)
    assert math.isclose(ref_lon_med, -3.442, rel_tol=1e-3)
    assert upload_handlers.is_coordinate_within_bounds(54.551, -3.442, ref_lat_med, ref_lon_med, max_dist_med)
    assert not upload_handlers.is_coordinate_within_bounds(83.7, 73.2, ref_lat_med, ref_lon_med, max_dist_med)


def test_process_telemetry_derivative_data_filters_outlier_coordinates() -> None:
    records = [
        {
            'Time': f'2026-05-10T14:30:0{i}.000Z',
            'Latitude': '83.779642' if i == 0 else f'{54.551 + i * 0.0001:.6f}',
            'Longitude': '73.235724' if i == 0 else f'{-3.442 + i * 0.0001:.6f}',
            'Speed': '10.0',
            'Lap': '1',
        }
        for i in range(5)
    ]
    meta = CSVSessionMetadata(
        track_name='Rowrah',
        session_start_datetime=datetime.datetime(2026, 5, 10, 14, 30),
        driver_name='Driver A',
        league='test_league',
        class_name='cadet',
        session_name='Practice',
    )
    track_data = {
        'center_line': [
            {'lat': 54.551, 'lon': -3.442, 'dist': 0.0},
            {'lat': 54.552, 'lon': -3.443, 'dist': 100.0}
        ]
    }
    with patch('upload_handlers.db.get_track', return_value=track_data):
        df_telemetry, _, _ = upload_handlers.process_telemetry_derivative_data(records, meta, '/tmp')

    # The first row with outlier coords (83.7, 73.2) should have blank Latitude and Longitude
    assert df_telemetry['Latitude'].iloc[0] == ''
    assert df_telemetry['Longitude'].iloc[0] == ''
    # Remaining rows should have valid coordinates
    assert df_telemetry['Latitude'].iloc[1] != ''
    assert df_telemetry['Longitude'].iloc[1] != ''
