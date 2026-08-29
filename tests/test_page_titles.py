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
import re
from typing import Generator, Any
import pytest
import psycopg2
from flask.testing import FlaskClient
import app
import database
import gallery_handlers
import race_db


@pytest.fixture
def test_client_with_db(ephemeral_postgres: dict[str, Any]) -> Generator[FlaskClient, None, None]:
    try:
        database.db.close()
    except Exception:
        pass

    testdata_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'testdata', 'race_db'))
    new_db = race_db.RaceDB(base_dir=testdata_dir, db_config=ephemeral_postgres)
    database.db = new_db
    app.db = new_db
    gallery_handlers.db = new_db

    conn = psycopg2.connect(**ephemeral_postgres)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO photos (meeting_id, filename, taken_at, album)
            VALUES ('bar/2026_05_24_radiator_springs', 'photo1.jpg', '2026-05-24 10:00:00+00', 'album1')
            ON CONFLICT DO NOTHING
        """)
        cur.execute("""
            INSERT INTO videos (meeting_id, filename, start_time, end_time)
            VALUES ('bar/2026_05_24_radiator_springs', 'video1.mp4', '2026-05-24 10:00:00+00', '2026-05-24 10:10:00+00')
            ON CONFLICT DO NOTHING
        """)
    conn.commit()
    conn.close()

    with app.app.test_client() as client:
        yield client


def _extract_title(response_data: bytes) -> str:
    html = response_data.decode('utf-8')
    match = re.search(r'<title>(.*?)</title>', html, re.DOTALL)
    return match.group(1).strip() if match else ''


def test_league_root_title(test_client_with_db: FlaskClient) -> None:
    resp = test_client_with_db.get('/league')
    assert resp.status_code == 200
    assert _extract_title(resp.data) == 'BrBrDb'


def test_about_title(test_client_with_db: FlaskClient) -> None:
    resp = test_client_with_db.get('/about')
    assert resp.status_code == 200
    assert _extract_title(resp.data) == 'About BrBrDb'


def test_settings_title(test_client_with_db: FlaskClient) -> None:
    resp = test_client_with_db.get('/settings')
    assert resp.status_code == 200
    assert _extract_title(resp.data) == 'Settings - BrBrDb'


def test_auth_title(test_client_with_db: FlaskClient) -> None:
    resp = test_client_with_db.get('/auth')
    assert resp.status_code == 200
    assert _extract_title(resp.data) == 'Authentication - BrBrDb'


def test_auth_builder_title(test_client_with_db: FlaskClient) -> None:
    resp = test_client_with_db.get('/auth_builder')
    assert resp.status_code == 200
    assert _extract_title(resp.data) == 'Auth Builder - BrBrDb'


def test_drivers_and_driver_title(test_client_with_db: FlaskClient) -> None:
    resp = test_client_with_db.get('/drivers')
    assert resp.status_code == 200
    assert _extract_title(resp.data) == 'BrBrPeople'

    resp = test_client_with_db.get('/driver/Driver%201')
    assert resp.status_code == 200
    assert _extract_title(resp.data) == 'BrBrPeople: Driver 1'


def test_league_classes_title(test_client_with_db: FlaskClient) -> None:
    resp = test_client_with_db.get('/league/piston_cup')
    assert resp.status_code == 200
    assert _extract_title(resp.data) == 'Piston Cup - BrBrDb'


def test_league_class_title(test_client_with_db: FlaskClient) -> None:
    resp = test_client_with_db.get('/league/piston_cup/pro')
    assert resp.status_code == 200
    assert _extract_title(resp.data) == 'Piston Cup pro - BrBrDb'


def test_meeting_title(test_client_with_db: FlaskClient) -> None:
    resp = test_client_with_db.get('/meeting/piston_cup/pro/2026-05-24/Radiator%20Springs')
    assert resp.status_code == 200
    assert _extract_title(resp.data) == '2026-05-24 - Radiator Springs - BrBrDb'


def test_gallery_titles(test_client_with_db: FlaskClient) -> None:
    resp = test_client_with_db.get('/gallery')
    assert resp.status_code == 200
    assert _extract_title(resp.data) == 'BrBrGallery'

    resp = test_client_with_db.get('/gallery?league=piston_cup')
    assert resp.status_code == 200
    assert _extract_title(resp.data) == 'BrBrGallery: Piston Cup'

    resp = test_client_with_db.get('/gallery?league=piston_cup&meeting=2026-05-24')
    assert resp.status_code == 200
    assert _extract_title(resp.data) == 'BrBrGallery: 2026-05-24 - Radiator Springs'
