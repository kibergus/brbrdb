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

import json
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
        'test_key_with_gallery': {
            'see_gallery': True,
        },
        'test_key_no_gallery': {
            'see_gallery': False,
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        # 1. Access root gallery WITH see_gallery access
        client.set_cookie('auth_key', 'test_key_with_gallery')
        response = client.get('/gallery', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'Piston Cup' in html or 'piston_cup' in html
        assert 'Rotax' in html or 'rotax' in html
        assert 'KartSim' in html or 'kartsim' in html
        assert '/gallery?league=piston_cup' in html
        assert '/gallery?league=rotax' in html
        assert '/gallery?league=kartsim' in html

        # 2. Access root gallery WITHOUT see_gallery access -> 403
        client.set_cookie('auth_key', 'test_key_no_gallery')
        response = client.get('/gallery', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert response.status_code == 403


def test_gallery_league_view(populated_db_conn: Any) -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'see_gallery': True,
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

        # 3. Access without see_gallery permission -> 403
        mock_keys_no_gal = {'test_key': {'see_gallery': False}}
        with patch('auth.load_keys', return_value=mock_keys_no_gal):
            response_gal = client.get('/gallery?league=kartsim', environ_base={'REMOTE_ADDR': '192.168.1.100'})
            assert response_gal.status_code == 403


def test_gallery_meeting_breadcrumbs(populated_db_conn: Any) -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'see_gallery': True,
            'see_leagues': ['*'],
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
        assert 'data-url="/video_file/piston_cup/2026_05_24_radiator_springs/video1.mp4"' in html


def test_gallery_hamburger_menu_session_link(populated_db_conn: Any) -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'see_gallery': True,
            'see_leagues': ['*'],
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
            'see_leagues': ['*'],
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


def test_serve_video_dir_traversal_protection() -> None:
    client = app.app.test_client()
    # Path traversal in meeting_folder parameter
    res1 = client.get('/video_file/club100/..%2F..%2Fetc/test.mp4', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert res1.status_code == 400

    # Path traversal in league parameter
    res2 = client.get('/video_file/..%2Fclub100/meeting_1/test.mp4', environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert res2.status_code == 400


def test_serve_video_serving(tmp_path: Any) -> None:
    client = app.app.test_client()
    media_dir = tmp_path / "media"
    video_dir = media_dir / "club100" / "2026_05_24_whilton" / "video"
    video_dir.mkdir(parents=True)
    video_file = video_dir / "race.mp4"
    video_file.write_bytes(b"dummy video content")

    mock_keys_with_video = {
        'test_key': {
            'see_videos': True,
            'see_leagues': ['*'],
        }
    }
    mock_keys_no_video = {
        'test_key_no_vid': {
            'see_videos': False,
            'see_leagues': ['*'],
        }
    }

    with patch.dict(database.config, {'media_dir': str(media_dir)}):
        # 1. Access without see_videos permission -> 403
        with patch('auth.load_keys', return_value=mock_keys_no_video):
            client.set_cookie('auth_key', 'test_key_no_vid')
            res_403 = client.get(
                '/video_file/club100/2026_05_24_whilton/race.mp4',
                environ_base={'REMOTE_ADDR': '192.168.1.100'}
            )
            assert res_403.status_code == 403

        # 2. Access with see_videos permission -> 200 and correct content
        with patch('auth.load_keys', return_value=mock_keys_with_video):
            client.set_cookie('auth_key', 'test_key')
            res_200 = client.get(
                '/video_file/club100/2026_05_24_whilton/race.mp4',
                environ_base={'REMOTE_ADDR': '192.168.1.100'}
            )
            assert res_200.status_code == 200
            assert res_200.data == b"dummy video content"

            # 3. Nonexistent video -> 404
            res_404 = client.get(
                '/video_file/club100/2026_05_24_whilton/nonexistent.mp4',
                environ_base={'REMOTE_ADDR': '192.168.1.100'}
            )
            assert res_404.status_code == 404


def test_gallery_media_direct_url_photo(populated_db_conn: Any) -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'see_gallery': True,
            'see_leagues': ['*'],
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        client.set_cookie('auth_key', 'test_key')

        # 1. Access meeting with photo query parameter
        response = client.get(
            '/gallery?league=piston_cup&meeting=2026-05-24&photo=photo1.jpg',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'data-filename="photo1.jpg"' in html
        assert 'initMediaFromUrl' in html
        assert '<title>BrBrGallery: photo1.jpg - 2026-05-24 - Radiator Springs</title>' in html


def test_gallery_media_direct_url_video(populated_db_conn: Any) -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'see_gallery': True,
            'see_leagues': ['*'],
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        client.set_cookie('auth_key', 'test_key')

        # 1. Access meeting with video query parameter
        response = client.get(
            '/gallery?league=piston_cup&meeting=2026-05-24&video=video1.mp4',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'data-filename="video1.mp4"' in html
        assert '<title>BrBrGallery: video1.mp4 - 2026-05-24 - Radiator Springs</title>' in html

        # 2. Access meeting with full relative path in video query parameter -> resolves to basename
        response_path = client.get(
            '/gallery?league=piston_cup&meeting=2026-05-24&video=SomeAlbum%2Fvideo1.mp4',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response_path.status_code == 200
        html_path = response_path.get_data(as_text=True)
        assert 'data-filename="video1.mp4"' in html_path
        assert '<title>BrBrGallery: video1.mp4 - 2026-05-24 - Radiator Springs</title>' in html_path


def test_gallery_media_url_wrong_album_auto_resolves(populated_db_conn: Any) -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'see_gallery': True,
            'see_leagues': ['*'],
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        client.set_cookie('auth_key', 'test_key')

        # photo1.jpg belongs to 'album1'. Access with mismatched album query param.
        response = client.get(
            '/gallery?league=piston_cup&meeting=2026-05-24&album=nonexistent_album&photo=photo1.jpg',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        # Should auto-resolve album to album1 so photo1.jpg is present
        assert 'data-filename="photo1.jpg"' in html


def test_gallery_spherical_video_support(populated_db_conn: Any, tmp_path: Any) -> None:
    client = app.app.test_client()
    mock_keys = {
        'test_key': {
            'see_gallery': True,
            'see_videos': True,
            'see_leagues': ['*'],
        }
    }

    # Insert a 360 video into DB
    with database.db.conn.cursor() as cur:
        cur.execute("""
            INSERT INTO videos (meeting_id, filename, start_time, end_time)
            VALUES ('bar/2026_05_24_radiator_springs', 'Practice/CAM_20261107114937_0001_D.mov',
                    '2026-05-24 10:20:00+00', '2026-05-24 10:30:00+00')
        """)
    database.db.conn.commit()

    # Mock video.json in media_dir or meeting_dir
    fake_media = tmp_path / 'media'
    video_dir = fake_media / 'piston_cup' / '2026_05_24_radiator_springs' / 'video' / 'Practice'
    video_dir.mkdir(parents=True)
    with open(video_dir / 'video.json', 'w', encoding='utf-8') as f:
        json.dump({
            'CAM_20261107114937_0001_D.mov': {
                'start': '2026-05-24T10:20:00+00:00',
                'end': '2026-05-24T10:30:00+00:00',
                'spherical': True,
            }
        }, f)

    with patch('auth.load_keys', return_value=mock_keys), \
         patch.dict(database.config, {'media_dir': str(fake_media)}), \
         patch.dict(gallery_handlers.config, {'media_dir': str(fake_media)}):
        client.set_cookie('auth_key', 'test_key')
        response = client.get(
            '/gallery?league=piston_cup&meeting=2026-05-24',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'data-spherical="true"' in html
        assert 'video-360-badge' in html
        assert 'btn-360' in html
