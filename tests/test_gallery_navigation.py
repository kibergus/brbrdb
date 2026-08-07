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
from typing import Generator, Any
from unittest.mock import patch
import pytest
import psycopg2
import app
import database
import gallery_handlers
from gallery_handlers import _sort_albums_chronologically
import race_db
from race_db import RaceMetadata


@pytest.fixture
def populated_db_conn(ephemeral_postgres: dict) -> Generator:
    # Close old database connection if any
    try:
        database.db.close()
    except Exception:
        pass

    testdata_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'testdata', 'race_db'))
    # Initialize a new RaceDB pointing to the ephemeral database
    new_db = race_db.RaceDB(base_dir=testdata_dir, db_config=ephemeral_postgres)
    database.db = new_db
    app.db = new_db
    gallery_handlers.db = new_db

    # Connect to insert mock meetings/photos/videos to check list logic
    conn = psycopg2.connect(**ephemeral_postgres)
    with conn.cursor() as cur:
        # Testdata has bar/2026_05_24_radiator_springs in piston_cup league
        cur.execute("""
            INSERT INTO photos (meeting_id, filename, taken_at, album)
            VALUES ('bar/2026_05_24_radiator_springs', 'photo1.jpg', '2026-05-24 10:00:00+00', 'album1')
        """)
        cur.execute("""
            INSERT INTO videos (meeting_id, filename, start_time, end_time)
            VALUES ('bar/2026_05_24_radiator_springs', 'video1.mp4', '2026-05-24 10:00:00+00', '2026-05-24 10:10:00+00')
        """)

        # Mock meeting in 'rotax'
        cur.execute("""
            INSERT INTO meetings (id, league, meeting_name, meeting_dir, date, track_name)
            VALUES ('mock_rotax_meeting', 'rotax', 'Rotax Meeting 1', 'mock_rotax_meeting', '2026-03-15', 'Llandow')
        """)
        cur.execute("""
            INSERT INTO photos (meeting_id, filename, taken_at, album)
            VALUES ('mock_rotax_meeting', 'photo2.jpg', '2026-03-15 11:00:00+00', 'album2')
        """)

        # Mock meeting in 'kartsim'
        cur.execute("""
            INSERT INTO meetings (id, league, meeting_name, meeting_dir, date, track_name)
            VALUES (
                'mock_kartsim_meeting', 'kartsim', 'KartSim Meeting 1',
                'mock_kartsim_meeting', '2026-04-20', 'Virtual'
            )
        """)
        cur.execute("""
            INSERT INTO photos (meeting_id, filename, taken_at, album)
            VALUES ('mock_kartsim_meeting', 'photo3.jpg', '2026-04-20 12:00:00+00', 'album3')
        """)
    conn.commit()
    conn.close()
    yield


def test_gallery_root_view(populated_db_conn: Any) -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key_with_kartsim': {
            'see_gallery': True,
            'kartsim_data': True,
        },
        'test_key_no_kartsim': {
            'see_gallery': True,
            'kartsim_data': False,
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        # 1. Access root gallery WITH kartsim access
        client.set_cookie('auth_key', 'test_key_with_kartsim')
        response = client.get('/gallery', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Piston Cup' in html or 'piston_cup' in html
        assert 'Rotax' in html or 'rotax' in html
        assert 'KartSim' in html or 'kartsim' in html
        assert '/gallery?league=piston_cup' in html
        assert '/gallery?league=rotax' in html
        assert '/gallery?league=kartsim' in html

        # 2. Access root gallery WITHOUT kartsim access
        client.set_cookie('auth_key', 'test_key_no_kartsim')
        response = client.get('/gallery', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Piston Cup' in html or 'piston_cup' in html
        assert 'Rotax' in html or 'rotax' in html
        assert '/gallery?league=kartsim' not in html


def test_gallery_league_view(populated_db_conn: Any) -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'see_gallery': True,
            'kartsim_data': True,
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        client.set_cookie('auth_key', 'test_key')

        # 1. Access a league page to list meetings
        response = client.get('/gallery?league=piston_cup', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Radiator Springs' in html
        # Both formats are acceptable depending on url_for query parameter encoding
        assert (
            '/gallery?league=piston_cup&amp;meeting=2026-05-24' in html
            or '/gallery?league=piston_cup&meeting=2026-05-24' in html
        )

        # 2. Access invalid league
        response_invalid = client.get('/gallery?league=invalid_league', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert response_invalid.status_code == 404

        # 3. Access restricted kartsim without permission
        mock_keys_no_ks = {'test_key': {'see_gallery': True, 'kartsim_data': False}}
        with patch('auth.load_keys', return_value=mock_keys_no_ks):
            response_ks = client.get('/gallery?league=kartsim', environ_base={'REMOTE_ADDR': '192.168.1.100'})
            assert response_ks.status_code == 403


def test_gallery_meeting_breadcrumbs(populated_db_conn: Any) -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'see_gallery': True,
            'kartsim_data': True,
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        client.set_cookie('auth_key', 'test_key')

        # Access specific meeting gallery
        response = client.get(
            '/gallery?league=piston_cup&meeting=2026-05-24',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert '/gallery' in html
        assert 'league=piston_cup' in html
        assert 'Meeting' in html
        assert '/meeting/piston_cup/' in html


def test_gallery_hamburger_menu_session_link(populated_db_conn: Any) -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'see_gallery': True,
            'kartsim_data': True,
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        client.set_cookie('auth_key', 'test_key')

        # Mock sessions returned by db.find_sessions
        mock_session = RaceMetadata(
            league='piston_cup',
            class_name=['cadet'],
            track_name='Radiator Springs',
            session_name='Cadet Heat 1',
            meeting_name='Radiator Springs',
            meeting_dir='bar/2026_05_24_radiator_springs',
            meeting_folder='2026_05_24_radiator_springs',
            session_id='session_123',
            session_start_datetime='2026-05-24 10:20:00',
        )

        with patch('database.db.find_sessions', return_value=[mock_session]):
            # 1. No album selected -> meeting link present, session link absent
            resp = client.get(
                '/gallery?league=piston_cup&meeting=2026-05-24',
                environ_base={'REMOTE_ADDR': '192.168.1.100'}
            )
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)
            assert '/meeting/piston_cup/' in html
            assert 'Meeting results' in html
            assert 'Session results' not in html
            assert 'nav-dropdown-divider' in html

            # 2. Album corresponding to session selected -> both meeting and session link present
            resp_album = client.get(
                '/gallery?league=piston_cup&meeting=2026-05-24&album=Cadet+Heat+1',
                environ_base={'REMOTE_ADDR': '192.168.1.100'}
            )
            assert resp_album.status_code == 200
            html_album = resp_album.get_data(as_text=True)
            assert '/meeting/piston_cup/' in html_album
            assert 'Meeting results' in html_album
            assert '/session/piston_cup/' in html_album
            assert 'Session results' in html_album
            assert 'nav-dropdown-divider' in html_album


def test_context_aware_gallery_link(populated_db_conn: Any) -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'see_gallery': True,
            'kartsim_data': True,
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        client.set_cookie('auth_key', 'test_key')

        # 1. Access meeting page where photos exist (mock_rotax_meeting on 2026-03-15 in rotax league)
        response = client.get(
            '/meeting/rotax/cadet/2026-03-15/Llandow',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        # Gallery link should point specifically to the meeting's gallery page
        expected_amp = 'href="/gallery?league=rotax&amp;meeting=2026-03-15"'
        expected_raw = 'href="/gallery?league=rotax&meeting=2026-03-15"'
        assert expected_amp in html or expected_raw in html


def test_about_page() -> None:
    client = app.app.test_client()
    response = client.get('/about', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'Why' in html
    assert 'Terms of use' in html
    assert 'Privacy' in html
    assert 'Gallery' in html


def test_chronological_album_sorting() -> None:
    # Mock sessions
    sessions = [
        RaceMetadata(
            league='club100',
            session_name='Cadet Heat 1',
            session_id='s1',
            session_start_datetime='2026-02-21 10:00',
        ),
        RaceMetadata(
            league='club100',
            session_name='Cadet Heat 2',
            session_id='s2',
            session_start_datetime='2026-02-21 11:30',
        ),
    ]

    albums = {'Podium', 'Cadet Heat 2', 'Cadet Heat 1', 'Paddock'}

    sorted_albums = _sort_albums_chronologically(
        albums,
        sessions,
    )

    # Expected order: Cadet Heat 1, Cadet Heat 2, followed by non-session albums alphabetically (Paddock, Podium)
    assert sorted_albums == ['Cadet Heat 1', 'Cadet Heat 2', 'Paddock', 'Podium']


def test_serve_photo_dir_traversal_protection() -> None:
    client = app.app.test_client()
    # Path traversal in meeting_folder parameter
    res1 = client.get('/photo_file/club100/..%2F..%2Fetc/test.jpg', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert res1.status_code == 400

    # Path traversal in league parameter
    res2 = client.get('/photo_file/..%2Fclub100/meeting_1/test.jpg', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert res2.status_code == 400
