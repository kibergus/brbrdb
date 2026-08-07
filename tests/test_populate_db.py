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
import json
from typing import Generator, Any
import pytest
import psycopg2
import pandas as pd

from utils import populate_db


@pytest.fixture
def db_conn(ephemeral_postgres: dict[str, Any]) -> Generator[Any, None, None]:
    """Provides a fresh database connection with a clean schema for each test."""
    conn = psycopg2.connect(**ephemeral_postgres)
    populate_db.create_schema(conn, drop_first=True)
    yield conn
    conn.close()


def test_populate_database_basic(db_conn: Any, tmp_path: Any) -> None:
    """Test populating the database from files and basic schema correctness."""
    # Set up a mock data folder
    meeting_dir = tmp_path / 'league_a' / '2026_05_03_clay_pigeon'
    meeting_dir.mkdir(parents=True)

    meta = {
        'league': 'club100',
        'class_name': ['Senior'],
        'track_name': 'Clay Pigeon',
        'session_name': 'Senior Heat 1',
        'short_name': 'Heat 1',
        'meeting_name': 'Clay Pigeon Round 1',
        'session_start_datetime': '2026-05-03 10:00',
        'track_conditions': 'Dry',
        'temperature': '18.5',
        'weather': 'Sunny',
        'alphatiming_url': 'http://example.com'
    }
    with open(meeting_dir / 's1_metadata.json', 'w', encoding='utf-8') as f:
        json.dump(meta, f)

    csv_data = {
        'Pos': [1, 2],
        'Name': ['Driver A', 'Driver B'],
        'Positions Gained': [0, 2],
        'Gap': ['', '+1.200s'],
        'ChampPoints': [50, 48],
        'Lap 0': ['0.000', '0.000'],
        'Lap 1': ['1:00.500', '1:01.000'],
        'Lap 2': ['1:00.200', '1:00.800']
    }
    pd.DataFrame(csv_data).to_csv(meeting_dir / 's1.csv', index=False)

    # Run population
    populate_db.populate_database(str(tmp_path), db_conn, clean_orphans=True)

    # Assertions
    with db_conn.cursor() as cur:
        # Meetings
        cur.execute('SELECT league, id, track_name FROM meetings')
        meeting = cur.fetchone()
        assert meeting is not None
        assert meeting[0] == 'club100'
        assert meeting[1] == 'league_a/2026_05_03_clay_pigeon'
        assert meeting[2] == 'Clay Pigeon'

        # Drivers
        cur.execute('SELECT driver_name FROM drivers ORDER BY driver_name')
        drivers = cur.fetchall()
        assert len(drivers) == 2
        assert drivers[0][0] == 'Driver A'
        assert drivers[1][0] == 'Driver B'

        # Sessions
        cur.execute('SELECT session_id, class_name, temperature FROM sessions')
        session = cur.fetchone()
        assert session is not None
        assert session[0] == '2026_05_03_clay_pigeon_s1'
        assert session[1] == ['Senior']
        assert session[2] == 18.5

        # Results
        cur.execute('''
            SELECT d.driver_name, r.pos, r.positions_gained, r.champ_points, r.best_lap_seconds, r.lap_times
            FROM session_results r
            JOIN drivers d ON r.driver_id = d.id
            ORDER BY d.driver_name
        ''')
        results = cur.fetchall()
        assert len(results) == 2

        # Driver A
        assert results[0][0] == 'Driver A'
        assert results[0][1] == '1'
        assert results[0][2] == 0
        assert results[0][3] == 50
        assert results[0][4] == pytest.approx(60.200)
        assert results[0][5] == [0.0, 60.5, 60.2]

        # Driver B
        assert results[1][0] == 'Driver B'
        assert results[1][1] == '2'
        assert results[1][2] == 2
        assert results[1][3] == 48
        assert results[1][4] == pytest.approx(60.800)
        assert results[1][5] == [0.0, 61.0, 60.8]


def test_populate_database_penalties(db_conn: Any, tmp_path: Any) -> None:
    """Test parsing penalties and applying lap time deletions to session_results."""
    meeting_dir = tmp_path / 'club100' / '2026_05_03_clay_pigeon'
    meeting_dir.mkdir(parents=True)

    meta = {
        'league': 'club100',
        'class_name': ['Senior'],
        'track_name': 'Clay Pigeon',
        'session_name': 'Senior Heat 1',
        'session_start_datetime': '2026-05-03 10:00'
    }
    with open(meeting_dir / 's1_metadata.json', 'w', encoding='utf-8') as f:
        json.dump(meta, f)

    csv_data = {
        'Pos': [1, 2],
        'Name': ['Driver A', 'Driver B'],
        'Lap 1': ['1:00.000', '1:01.000'],
        'Lap 2': ['1:00.500', '1:00.800']
    }
    pd.DataFrame(csv_data).to_csv(meeting_dir / 's1.csv', index=False)

    penalties = {
        'Name': ['Driver A', 'Driver B'],
        'Penalty': ['+5 Seconds Penalty', 'Best laptime deleted']
    }
    pd.DataFrame(penalties).to_csv(meeting_dir / 's1_penalties.csv', index=False)

    # Run population
    populate_db.populate_database(str(tmp_path), db_conn, clean_orphans=True)

    # Assertions
    with db_conn.cursor() as cur:
        # Check penalties table
        cur.execute('''
            SELECT d.driver_name, p.penalty_desc, p.seconds_added, p.best_lap_deleted
            FROM penalties p
            JOIN drivers d ON p.driver_id = d.id
            ORDER BY d.driver_name
        ''')
        pens = cur.fetchall()
        assert len(pens) == 2

        # Driver A: +5 Seconds
        assert pens[0][0] == 'Driver A'
        assert pens[0][1] == '+5 Seconds Penalty'
        assert pens[0][2] == 5.0
        assert pens[0][3] is False

        # Driver B: Best laptime deleted
        assert pens[1][0] == 'Driver B'
        assert pens[1][1] == 'Best laptime deleted'
        assert pens[1][2] == 0.0
        assert pens[1][3] is True

        # Check session_results table to see if lap deletion was applied
        cur.execute('''
            SELECT d.driver_name, r.best_lap_seconds, r.lap_times
            FROM session_results r
            JOIN drivers d ON r.driver_id = d.id
            ORDER BY d.driver_name
        ''')
        results = cur.fetchall()

        # Driver A: Laps [1:00.0, 1:00.5] -> best should be 60.0
        assert results[0][0] == 'Driver A'
        assert results[0][1] == pytest.approx(60.0)
        assert results[0][2] == [None, 60.0, 60.5]

        # Driver B: Laps [1:01.0, 1:00.8] -> best (1:00.8) deleted -> lap_times array contains NULL/None, best is 61.0
        assert results[1][0] == 'Driver B'
        assert results[1][1] == pytest.approx(61.0)
        assert results[1][2] == [None, 61.0, None]


def test_populate_database_sync_and_orphans(db_conn: Any, tmp_path: Any) -> None:
    """Test that populate_database deletes DB data that is no longer present on disk."""
    meeting_dir_1 = tmp_path / 'club100' / '2026_05_03_clay_pigeon'
    meeting_dir_2 = tmp_path / 'club100' / '2026_05_10_buckmore_park'
    meeting_dir_1.mkdir(parents=True)
    meeting_dir_2.mkdir(parents=True)

    # 1. Write metadata for Clay Pigeon
    meta_1 = {
        'league': 'club100',
        'class_name': ['Senior'],
        'track_name': 'Clay Pigeon',
        'session_name': 'Clay Heat 1',
        'session_start_datetime': '2026-05-03 10:00'
    }
    with open(meeting_dir_1 / 's1_metadata.json', 'w', encoding='utf-8') as f:
        json.dump(meta_1, f)
    pd.DataFrame({'Pos': [1], 'Name': ['Driver A'], 'Lap 1': ['1:00.0']}).to_csv(meeting_dir_1 / 's1.csv', index=False)

    # 2. Write metadata for Buckmore Park
    meta_2 = {
        'league': 'club100',
        'class_name': ['Senior'],
        'track_name': 'Buckmore Park',
        'session_name': 'Buckmore Heat 1',
        'session_start_datetime': '2026-05-10 11:00'
    }
    with open(meeting_dir_2 / 's2_metadata.json', 'w', encoding='utf-8') as f:
        json.dump(meta_2, f)
    pd.DataFrame({'Pos': [1], 'Name': ['Driver B'], 'Lap 1': ['1:01.0']}).to_csv(meeting_dir_2 / 's2.csv', index=False)

    # Populate first time (both present)
    populate_db.populate_database(str(tmp_path), db_conn, clean_orphans=True)

    with db_conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM meetings')
        assert cur.fetchone()[0] == 2
        cur.execute('SELECT COUNT(*) FROM sessions')
        assert cur.fetchone()[0] == 2
        cur.execute('SELECT COUNT(*) FROM drivers')
        assert cur.fetchone()[0] == 2

    # 3. Now delete Buckmore Park metadata file from disk
    os.remove(meeting_dir_2 / 's2_metadata.json')

    # Re-run population
    populate_db.populate_database(str(tmp_path), db_conn, clean_orphans=True)

    # Buckmore Park should be purged from DB, leaving only Clay Pigeon
    with db_conn.cursor() as cur:
        cur.execute('SELECT id FROM meetings')
        meetings = cur.fetchall()
        assert len(meetings) == 1
        assert meetings[0][0] == 'club100/2026_05_03_clay_pigeon'

        cur.execute('SELECT session_id FROM sessions')
        sessions = cur.fetchall()
        assert len(sessions) == 1
        assert sessions[0][0] == '2026_05_03_clay_pigeon_s1'

        cur.execute('SELECT driver_name FROM drivers')
        drivers = cur.fetchall()
        assert len(drivers) == 1
        assert drivers[0][0] == 'Driver A'


def test_populate_database_duplicate_driver(db_conn: Any, tmp_path: Any) -> None:
    """Test populating the database when a driver has duplicate entries (e.g. DNS) in the same session CSV."""
    meeting_dir = tmp_path / 'club100' / '2026_05_03_clay_pigeon'
    meeting_dir.mkdir(parents=True)

    meta = {
        'league': 'club100',
        'class_name': ['Senior'],
        'track_name': 'Clay Pigeon',
        'session_name': 'Senior Heat 1',
        'session_start_datetime': '2026-05-03 10:00'
    }
    with open(meeting_dir / 's1_metadata.json', 'w', encoding='utf-8') as f:
        json.dump(meta, f)

    # Driver A is listed twice: first with a valid result, then as DNS
    csv_data = {
        'Pos': ['1', 'DNS'],
        'Name': ['Driver A', 'Driver A'],
        'Lap 1': ['1:00.000', ''],
        'Lap 2': ['1:00.500', '']
    }
    pd.DataFrame(csv_data).to_csv(meeting_dir / 's1.csv', index=False)

    # Run population - this should not crash with a UniqueViolation
    populate_db.populate_database(str(tmp_path), db_conn, clean_orphans=True)

    # Verify that only the first entry was saved
    with db_conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM session_results')
        assert cur.fetchone()[0] == 1

        cur.execute('''
            SELECT d.driver_name, r.pos, r.best_lap_seconds
            FROM session_results r
            JOIN drivers d ON r.driver_id = d.id
        ''')
        res = cur.fetchone()
        assert res is not None
        assert res[0] == 'Driver A'
        assert res[1] == '1'
        assert res[2] == pytest.approx(60.0)
