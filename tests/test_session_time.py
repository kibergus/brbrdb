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

import io
import json
import os
import datetime
import logging
from unittest.mock import patch

import pytest
import session_time
import app


def test_accepted_formats() -> None:
    # 1. YYYY-MM-DD HH:MM (minute-resolution, naive)
    dt, has_s = session_time.parse_session_start_datetime('2026-05-10 14:30')
    assert dt == datetime.datetime(2026, 5, 10, 14, 30)
    assert has_s is False

    # 2. YYYY-MM-DD HH:MM:SS (naive, with seconds)
    dt, has_s = session_time.parse_session_start_datetime('2026-05-10 14:30:25')
    assert dt == datetime.datetime(2026, 5, 10, 14, 30, 25)
    assert has_s is True

    # 3. YYYY-MM-DD HH:MM:SS.fff (naive, with ms) -> trimmed to seconds
    dt, has_s = session_time.parse_session_start_datetime('2026-05-10 14:30:25.148')
    assert dt == datetime.datetime(2026, 5, 10, 14, 30, 25)
    assert has_s is True

    # 4. ISO 8601 with T and UTC offset (+00:00)
    dt, has_s = session_time.parse_session_start_datetime(
        '2026-05-10T13:30:25.148+00:00', track_name='Buckmore Park'
    )
    # Buckmore Park in May is BST (UTC+1) -> 14:30:25
    assert dt == datetime.datetime(2026, 5, 10, 14, 30, 25)
    assert has_s is True

    # 5. ISO 8601 with Z
    dt, has_s = session_time.parse_session_start_datetime(
        '2026-05-10T13:30:25Z', track_name='Buckmore Park'
    )
    assert dt == datetime.datetime(2026, 5, 10, 14, 30, 25)
    assert has_s is True

    # 6. ISO 8601 with +01:00
    dt, has_s = session_time.parse_session_start_datetime(
        '2026-05-10T14:30:25+01:00', track_name='Buckmore Park'
    )
    assert dt == datetime.datetime(2026, 5, 10, 14, 30, 25)
    assert has_s is True

    # 7. ISO 8601 minute-only with timezone
    dt, has_s = session_time.parse_session_start_datetime(
        '2026-05-10T13:30+00:00', track_name='Buckmore Park'
    )
    assert dt == datetime.datetime(2026, 5, 10, 14, 30)
    assert has_s is False


def test_utc_conversion_dst_bst_and_gmt() -> None:
    # BST case: July 2026 (UTC+1)
    dt_bst, _ = session_time.parse_session_start_datetime(
        '2026-07-15T10:00:00Z', track_name='Buckmore Park'
    )
    assert dt_bst == datetime.datetime(2026, 7, 15, 11, 0, 0)

    # GMT case: January 2026 (UTC+0)
    dt_gmt, _ = session_time.parse_session_start_datetime(
        '2026-01-15T10:00:00Z', track_name='Buckmore Park'
    )
    assert dt_gmt == datetime.datetime(2026, 1, 15, 10, 0, 0)

    # Spring forward transition (2026-03-29: 01:00 UTC -> 02:00 BST)
    dt_before_spring, _ = session_time.parse_session_start_datetime(
        '2026-03-29T00:30:00Z', track_name='Buckmore Park'
    )
    assert dt_before_spring == datetime.datetime(2026, 3, 29, 0, 30, 0)

    dt_after_spring, _ = session_time.parse_session_start_datetime(
        '2026-03-29T02:30:00Z', track_name='Buckmore Park'
    )
    assert dt_after_spring == datetime.datetime(2026, 3, 29, 3, 30, 0)

    # Fall back transition (2026-10-25: 02:00 BST -> 01:00 GMT)
    dt_before_fall, _ = session_time.parse_session_start_datetime(
        '2026-10-25T00:30:00Z', track_name='Buckmore Park'
    )
    assert dt_before_fall == datetime.datetime(2026, 10, 25, 1, 30, 0)

    dt_after_fall, _ = session_time.parse_session_start_datetime(
        '2026-10-25T03:30:00Z', track_name='Buckmore Park'
    )
    assert dt_after_fall == datetime.datetime(2026, 10, 25, 3, 30, 0)


def test_us_and_germany_tracks() -> None:
    # US Texas: Gulf Coast Karters (America/Chicago, CDT UTC-5 in October)
    dt_tx, _ = session_time.parse_session_start_datetime(
        '2026-10-10T17:34:25Z', track_name='Gulf Coast Karters'
    )
    assert dt_tx == datetime.datetime(2026, 10, 10, 12, 34, 25)

    # US California: Willow Springs reverse (America/Los_Angeles, PDT UTC-7 in October)
    dt_ca, _ = session_time.parse_session_start_datetime(
        '2026-10-10T19:34:25Z', track_name='Willow Springs reverse'
    )
    assert dt_ca == datetime.datetime(2026, 10, 10, 12, 34, 25)

    # Germany: Kartbahn Walldorf (Europe/Berlin, CEST UTC+2 in October)
    dt_de, _ = session_time.parse_session_start_datetime(
        '2026-10-10T10:34:25Z', track_name='Kartbahn Walldorf'
    )
    assert dt_de == datetime.datetime(2026, 10, 10, 12, 34, 25)


def test_unknown_track_fallback(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        dt, _ = session_time.parse_session_start_datetime(
            '2026-07-15T10:00:00Z', track_name='Totally Unknown Fantasy Kartway'
        )
    # Should fall back to Europe/London (BST in July -> 11:00:00)
    assert dt == datetime.datetime(2026, 7, 15, 11, 0, 0)
    assert 'falling back to Europe/London' in caplog.text


def test_invalid_input_error() -> None:
    invalid_inputs = [
        'garbage',
        '2026/05/10 14:30',
        '2026-05-10',
        '14:30:00',
        '',
        '2026-05-10T14:30',  # T without timezone offset
    ]
    for inp in invalid_inputs:
        with pytest.raises(ValueError, match='Invalid session_start_datetime format'):
            session_time.parse_session_start_datetime(inp)


def test_overwrite_rule() -> None:
    stored = datetime.datetime(2026, 10, 10, 12, 34, 25)

    # 1. Minute-only matching within 5 minutes -> keep stored (seconds preserved)
    inc_same_min = datetime.datetime(2026, 10, 10, 12, 34, 0)
    assert session_time.resolve_start_datetime_overwrite(stored, inc_same_min, False) == stored

    # 2. Minute-only rounded to next minute (+35s, within 5 min) -> keep stored
    inc_next_min = datetime.datetime(2026, 10, 10, 12, 35, 0)
    assert session_time.resolve_start_datetime_overwrite(stored, inc_next_min, False) == stored

    # 3. Minute-only rounded down by 2 minutes (within 5 min) -> keep stored
    inc_prev_min = datetime.datetime(2026, 10, 10, 12, 32, 0)
    assert session_time.resolve_start_datetime_overwrite(stored, inc_prev_min, False) == stored

    # 4. Minute-only differing by >= 5 minutes -> genuine change: overwrite
    inc_diff_min = datetime.datetime(2026, 10, 10, 12, 45, 0)
    assert session_time.resolve_start_datetime_overwrite(stored, inc_diff_min, False) == inc_diff_min

    # 5. Incoming with seconds -> overwrite (latest high-resolution wins)
    inc_new_sec = datetime.datetime(2026, 10, 10, 12, 34, 50)
    assert session_time.resolve_start_datetime_overwrite(stored, inc_new_sec, True) == inc_new_sec

    # 6. Incoming with seconds == 00 (e.g. 12:34:00 with has_seconds=True) -> overwrite
    inc_zero_sec = datetime.datetime(2026, 10, 10, 12, 34, 0)
    assert session_time.resolve_start_datetime_overwrite(stored, inc_zero_sec, True) == inc_zero_sec

    # 7. No existing stored -> incoming wins
    assert session_time.resolve_start_datetime_overwrite(None, inc_same_min, False) == inc_same_min


def test_files_upload_endpoint_formats_and_preservation(tmp_path: pytest.TempPathFactory) -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'upload_sessions': {
                'drivers': ['Driver A'],
                'leagues': ['*']
            }
        }
    }
    data_dir = str(tmp_path)

    with (patch('auth.load_keys', return_value=mock_keys),
          patch('upload_handlers.DATA_DIR', data_dir),
          patch('upload_handlers.populate_db.populate_single_session', return_value='test_session_id')):

        # 1. Invalid format -> 400 with helpful error message
        meta_invalid = {
            'league': 'kartsim',
            'class_name': 'cadet',
            'track_name': 'Rowrah',
            'session_name': 'Practice',
            'session_start_datetime': 'bad-format'
        }
        res = client.post(
            '/api/upload/files',
            data={
                'metadata': (io.BytesIO(json.dumps(meta_invalid).encode('utf-8')), 'metadata.json'),
                'results': (io.BytesIO(b'Pos,Name\n1,Driver A\n'), 'results.csv'),
            },
            content_type='multipart/form-data',
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '127.0.0.1'}
        )
        assert res.status_code == 400
        assert 'Invalid session_start_datetime format' in res.get_json()['error']

        # 2. First upload: partial live timing with UTC offset and milliseconds
        meta_partial = {
            'league': 'kartsim',
            'class_name': 'cadet',
            'track_name': 'Buckmore Park',
            'session_name': 'Race 1',
            # 11:34:25.148 UTC in October = 12:34:25 BST local
            'session_start_datetime': '2026-10-10T11:34:25.148+00:00'
        }
        res_partial = client.post(
            '/api/upload/files',
            data={
                'metadata': (io.BytesIO(json.dumps(meta_partial).encode('utf-8')), 'metadata.json'),
                'results': (io.BytesIO(b'Pos,Name\n1,Driver A\n'), 'results.csv'),
            },
            content_type='multipart/form-data',
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '127.0.0.1'}
        )
        assert res_partial.status_code == 200

        # Check metadata file generated on disk
        expected_dir = os.path.join(data_dir, 'kartsim', 'cadet', '2026_10_10_buckmore_park')
        expected_meta_file = os.path.join(expected_dir, '12_34_race_1_metadata.json')
        assert os.path.exists(expected_meta_file)

        with open(expected_meta_file, 'r', encoding='utf-8') as f:
            saved_meta = json.load(f)
        assert saved_meta['session_start_datetime'] == '2026-10-10 12:34:25'

        # 3. Second upload: final scrape with minute-only local time ("2026-10-10 12:34")
        meta_final = {
            'league': 'kartsim',
            'class_name': 'cadet',
            'track_name': 'Buckmore Park',
            'session_name': 'Race 1',
            'session_start_datetime': '2026-10-10 12:34'
        }
        res_final = client.post(
            '/api/upload/files',
            data={
                'metadata': (io.BytesIO(json.dumps(meta_final).encode('utf-8')), 'metadata.json'),
                'results': (io.BytesIO(b'Pos,Name\n1,Driver A\n'), 'results.csv'),
            },
            content_type='multipart/form-data',
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '127.0.0.1'}
        )
        assert res_final.status_code == 200

        # High-resolution seconds must be PRESERVED in metadata.json!
        with open(expected_meta_file, 'r', encoding='utf-8') as f:
            saved_meta_after_final = json.load(f)
        assert saved_meta_after_final['session_start_datetime'] == '2026-10-10 12:34:25'

        # 4. Third upload: genuine change with different minute (12:45) -> overwrites
        meta_changed = {
            'league': 'kartsim',
            'class_name': 'cadet',
            'track_name': 'Buckmore Park',
            'session_name': 'Race 1',
            'session_start_datetime': '2026-10-10 12:45'
        }
        res_changed = client.post(
            '/api/upload/files',
            data={
                'metadata': (io.BytesIO(json.dumps(meta_changed).encode('utf-8')), 'metadata.json'),
                'results': (io.BytesIO(b'Pos,Name\n1,Driver A\n'), 'results.csv'),
            },
            content_type='multipart/form-data',
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '127.0.0.1'}
        )
        assert res_changed.status_code == 200
        new_meta_file = os.path.join(expected_dir, '12_45_race_1_metadata.json')
        assert os.path.exists(new_meta_file)
        with open(new_meta_file, 'r', encoding='utf-8') as f:
            saved_meta_changed = json.load(f)
        assert saved_meta_changed['session_start_datetime'] == '2026-10-10 12:45'

        # 5. Fourth upload: new seconds-bearing upload overwrites previous
        meta_seconds = {
            'league': 'kartsim',
            'class_name': 'cadet',
            'track_name': 'Buckmore Park',
            'session_name': 'Race 1',
            'session_start_datetime': '2026-10-10 12:45:50'
        }
        res_seconds = client.post(
            '/api/upload/files',
            data={
                'metadata': (io.BytesIO(json.dumps(meta_seconds).encode('utf-8')), 'metadata.json'),
                'results': (io.BytesIO(b'Pos,Name\n1,Driver A\n'), 'results.csv'),
            },
            content_type='multipart/form-data',
            headers={'X-API-Key': 'test_key'},
            environ_base={'REMOTE_ADDR': '127.0.0.1'}
        )
        assert res_seconds.status_code == 200
        with open(new_meta_file, 'r', encoding='utf-8') as f:
            saved_meta_seconds = json.load(f)
        assert saved_meta_seconds['session_start_datetime'] == '2026-10-10 12:45:50'
