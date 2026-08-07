#!/usr/bin/env python3

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
import sys
import json
import re
import glob
import argparse
import datetime
from typing import Any

import pandas as pd
import psycopg2

from race_tools import sanitize

# Helper functions matching race_db.py to avoid depending on it


def _to_list(x: Any) -> list[str]:
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    return list(x)


def _read_cleaned_csv(csv_path: str) -> pd.DataFrame:
    """Loads a CSV and normalizes column names by replacing empty ones with Col_i."""
    df = pd.read_csv(csv_path)
    df.columns = [c if c else f'Col_{i}' for i, c in enumerate(df.columns)]
    return df


def _normalize_pos(v: Any) -> int | str:
    """Normalize position values, preserving strings like DNS/DNF/DSQ."""
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return str(v) if not pd.isna(v) else '999'


def _check_path_is_safe(base_dir: str, path: str) -> bool:
    """Validate that a path is within the base_dir."""
    try:
        real_base = os.path.realpath(os.path.expanduser(base_dir))
        real_path = os.path.realpath(path)
        return os.path.commonpath([real_base, real_path]) == real_base
    except (ValueError, OSError):
        return False


def _load_penalty_file(csv_path: str) -> pd.DataFrame:
    """Helper to load and parse a single penalty CSV file."""
    if not os.path.exists(csv_path):
        return pd.DataFrame()

    df = _read_cleaned_csv(csv_path)
    if df.empty or 'Penalty' not in df.columns:
        return pd.DataFrame()

    # Positions Added (e.g., +1 Position)
    df['PositionsAdded'] = df['Penalty'].str.extract(
        r'\+([\d\.]+)\s+Position', flags=re.IGNORECASE
    )[0].astype(float).fillna(0.0)

    # Seconds Added (e.g., +5 Seconds)
    df['SecondsAdded'] = df['Penalty'].str.extract(
        r'\+([\d\.]+)\s+Second', flags=re.IGNORECASE
    )[0].astype(float).fillna(0.0)

    # Excluded / Disqualified
    df['Excluded'] = df['Penalty'].str.contains(r'Excluded|Disqualified', case=False, regex=True).fillna(False)

    # Best laptime(s) deleted
    def get_del_count(p: Any) -> int:
        m = re.search(r'Best\s+(\d+)?\s*laptimes?\s+deleted', str(p), re.IGNORECASE)
        if not m:
            return 0
        return int(m.group(1)) if m.group(1) else 1

    df['BestLapDeleted'] = df['Penalty'].apply(get_del_count).astype(int)
    return df


def parse_temperature(t_val: Any) -> float | None:
    if t_val is None or pd.isna(t_val):
        return None
    try:
        return float(t_val)
    except (ValueError, TypeError):
        m = re.search(r'[-+]?\d*\.\d+|\d+', str(t_val))
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                pass
        return None


def parse_champ_points(row: Any) -> int | None:
    for col in ['ChampPoints', 'CPts', 'Points']:
        if col in row and pd.notna(row[col]):
            try:
                return int(float(row[col]))
            except (ValueError, TypeError):
                pass
    return None


def create_schema(conn: Any, drop_first: bool = False) -> None:
    with conn.cursor() as cur:
        if drop_first:
            print('Dropping existing tables...')
            cur.execute('DROP TABLE IF EXISTS photo_drivers CASCADE;')
            cur.execute('DROP TABLE IF EXISTS photos CASCADE;')
            cur.execute('DROP TABLE IF EXISTS videos CASCADE;')
            cur.execute('DROP TABLE IF EXISTS penalties CASCADE;')
            cur.execute('DROP TABLE IF EXISTS session_results CASCADE;')
            cur.execute('DROP TABLE IF EXISTS sessions CASCADE;')
            cur.execute('DROP TABLE IF EXISTS drivers CASCADE;')
            cur.execute('DROP TABLE IF EXISTS meetings CASCADE;')

        print('Creating tables and indexes...')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS meetings (
                id TEXT PRIMARY KEY,
                league TEXT NOT NULL,
                meeting_name TEXT NOT NULL,
                meeting_dir TEXT NOT NULL,
                date DATE NOT NULL,
                track_name TEXT NOT NULL
            );
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS drivers (
                id SERIAL PRIMARY KEY,
                driver_name TEXT UNIQUE NOT NULL
            );
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                meeting_id TEXT NOT NULL REFERENCES meetings (id) ON DELETE CASCADE,
                class_name TEXT[] NOT NULL DEFAULT '{}',
                session_name TEXT NOT NULL,
                short_name TEXT NOT NULL,
                session_start_datetime TIMESTAMP WITHOUT TIME ZONE NOT NULL,
                alphatiming_url TEXT,
                track_conditions TEXT,
                temperature REAL,
                weather TEXT
            );
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS session_results (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions (session_id) ON DELETE CASCADE,
                driver_id INTEGER NOT NULL REFERENCES drivers (id) ON DELETE CASCADE,
                pos TEXT,
                positions_gained INTEGER DEFAULT 0,
                gap TEXT,
                champ_points INTEGER,
                best_lap_seconds REAL,
                lap_times REAL[],
                CONSTRAINT unique_driver_session UNIQUE (session_id, driver_id)
            );
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS penalties (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES sessions (session_id) ON DELETE CASCADE,
                driver_id INTEGER NOT NULL REFERENCES drivers (id) ON DELETE CASCADE,
                penalty_desc TEXT,
                positions_added REAL DEFAULT 0.0,
                seconds_added REAL DEFAULT 0.0,
                excluded BOOLEAN DEFAULT FALSE,
                best_lap_deleted BOOLEAN DEFAULT FALSE
            );
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS videos (
                id SERIAL PRIMARY KEY,
                meeting_id TEXT NOT NULL REFERENCES meetings (id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                start_time TIMESTAMP WITH TIME ZONE NOT NULL,
                end_time TIMESTAMP WITH TIME ZONE NOT NULL,
                session_id TEXT,
                CONSTRAINT unique_meeting_video UNIQUE (meeting_id, filename)
            );
        ''')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS photos (
                id SERIAL PRIMARY KEY,
                meeting_id TEXT NOT NULL REFERENCES meetings (id) ON DELETE CASCADE,
                filename TEXT NOT NULL,
                taken_at TIMESTAMP WITH TIME ZONE NOT NULL,
                album TEXT,
                session_id TEXT,
                CONSTRAINT unique_meeting_photo UNIQUE (meeting_id, filename)
            );
        ''')
        cur.execute('ALTER TABLE photos ADD COLUMN IF NOT EXISTS album TEXT;')
        cur.execute('ALTER TABLE videos ADD COLUMN IF NOT EXISTS session_id TEXT;')
        cur.execute('ALTER TABLE photos ADD COLUMN IF NOT EXISTS session_id TEXT;')

        cur.execute('''
            CREATE TABLE IF NOT EXISTS photo_drivers (
                photo_id INTEGER NOT NULL REFERENCES photos (id) ON DELETE CASCADE,
                driver_id INTEGER NOT NULL REFERENCES drivers (id) ON DELETE CASCADE,
                PRIMARY KEY (photo_id, driver_id)
            );
        ''')

        cur.execute('CREATE INDEX IF NOT EXISTS idx_results_driver ON session_results (driver_id, session_id);')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_results_session ON session_results (session_id, best_lap_seconds);')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_meetings_lookup ON meetings (league, date, track_name);')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions (session_start_datetime);')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_drivers_name ON drivers (driver_name);')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_photo_drivers_driver ON photo_drivers (driver_id);')
    conn.commit()


def get_or_create_driver(cur: Any, driver_name: str) -> int:
    cur.execute('SELECT id FROM drivers WHERE driver_name = %s', (driver_name,))
    row = cur.fetchone()
    if row:
        return int(row[0])
    cur.execute('INSERT INTO drivers (driver_name) VALUES (%s) RETURNING id', (driver_name,))
    return int(cur.fetchone()[0])


def get_or_create_meeting(
    cur: Any, league: str, meeting_id: str, meeting_name: str, meeting_dir: str, date_str: str, track_name: str
) -> str:
    cur.execute('SELECT id FROM meetings WHERE id = %s', (meeting_id,))
    row = cur.fetchone()
    if row:
        cur.execute(
            '''
            UPDATE meetings SET
                league = %s, meeting_name = %s, meeting_dir = %s, date = %s, track_name = %s
            WHERE id = %s
            ''',
            (league, meeting_name, meeting_dir, date_str, track_name, meeting_id)
        )
        return meeting_id

    cur.execute(
        '''
        INSERT INTO meetings (id, league, meeting_name, meeting_dir, date, track_name)
        VALUES (%s, %s, %s, %s, %s, %s)
        ''',
        (meeting_id, league, meeting_name, meeting_dir, date_str, track_name)
    )
    return meeting_id


def populate_single_session(meta_path: str, conn: Any, base_dir: str) -> str:
    """Populates or updates a single session in the database from its metadata file."""
    if not _check_path_is_safe(base_dir, meta_path):
        raise ValueError(f'Unsafe path: {meta_path}')

    # Load Metadata
    with open(meta_path, 'r', encoding='utf-8') as f:
        meta = json.load(f)

    meeting_dir_abs = os.path.dirname(meta_path)
    meeting_dir = os.path.relpath(meeting_dir_abs, base_dir)
    meeting_folder = os.path.basename(meeting_dir_abs)
    session_filename = os.path.basename(meta_path).replace('_metadata.json', '')

    # Combine meeting folder and session filename to form a globally unique DB session_id
    db_session_id = f'{meeting_folder}_{session_filename}'

    league = meta.get('league', '')
    class_name_list = _to_list(meta.get('class_name', ''))
    track_name = meta.get('track_name', '')
    session_name = meta.get('session_name', '')
    short_name = meta.get('short_name', '')
    meeting_name = meta.get('meeting_name', '')
    session_start_datetime = meta.get('session_start_datetime', '')
    alphatiming_url = meta.get('alphatiming_url', '')
    track_conditions = meta.get('track_conditions', '')
    temperature = parse_temperature(meta.get('temperature'))
    weather = meta.get('weather', '')

    # Standardize date
    if not session_start_datetime:
        session_start_datetime = '1970-01-01 00:00'
    try:
        dt = datetime.datetime.strptime(session_start_datetime, '%Y-%m-%d %H:%M')
        date_str = dt.strftime('%Y-%m-%d')
    except ValueError:
        dt = datetime.datetime(1970, 1, 1, 0, 0)
        date_str = '1970-01-01'

    # Start database transaction for this session
    with conn.cursor() as cur:
        # Delete any existing session/results/penalties to allow overwriting
        cur.execute('DELETE FROM sessions WHERE session_id = %s', (db_session_id,))

        # Get or create meeting
        meeting_id = get_or_create_meeting(
            cur, league, meeting_dir, meeting_name, meeting_dir, date_str, track_name
        )

        # Insert Session
        cur.execute(
            '''
            INSERT INTO sessions (
                session_id, meeting_id, class_name, session_name, short_name,
                session_start_datetime, alphatiming_url, track_conditions, temperature, weather
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''',
            (
                db_session_id, meeting_id, class_name_list, session_name, short_name,
                dt, alphatiming_url, track_conditions, temperature, weather
            )
        )

        # Load penalties if present
        pen_path = os.path.join(meeting_dir_abs, f'{session_filename}_penalties.csv')
        pen_df = _load_penalty_file(pen_path)

        csv_path = os.path.join(meeting_dir_abs, f'{session_filename}.csv')
        if os.path.exists(csv_path):
            # Load results CSV
            try:
                df = _read_cleaned_csv(csv_path)
            except Exception as e:
                print(f'Failed to load CSV {csv_path}: {e}')
                df = pd.DataFrame()

            if not df.empty:
                # Identify lap columns: Lap 0, Lap 1, Lap 2...
                lap_cols = [c for c in df.columns if re.match(r'Lap \d+', c)]

                # For penalty best lap deletion
                best_laps_deleted_counts = {}
                if not pen_df.empty:
                    best_laps_deleted_counts = (
                        pen_df.groupby('Name')['BestLapDeleted'].sum().astype(int).to_dict()
                    )

                inserted_drivers = set()
                for _, row in df.iterrows():
                    driver_name = row.get('Name')
                    if not driver_name or pd.isna(driver_name):
                        continue

                    driver_id = get_or_create_driver(cur, str(driver_name).strip())
                    if driver_id in inserted_drivers:
                        print(
                            f'Warning: Duplicate entry for driver {driver_name!r} '
                            f'(ID: {driver_id}) in session {db_session_id}. '
                            f'Keeping first/higher position and skipping duplicate.'
                        )
                        continue
                    inserted_drivers.add(driver_id)

                    # Parse laps by actual lap number from column name
                    lap_nums = []
                    for c in lap_cols:
                        m = re.search(r'\d+', c)
                        if m:
                            lap_nums.append(int(m.group(0)))

                    max_lap = max(lap_nums) if lap_nums else 0

                    laps: list[float | None] = [None] * (max_lap + 1)
                    lap_deleted = [False] * (max_lap + 1)

                    for col in lap_cols:
                        m = re.search(r'\d+', col)
                        if m:
                            t_num = int(m.group(0))
                            t_sec = sanitize.parse_time(row[col])
                            laps[t_num] = t_sec

                    # Strip trailing None values to represent only completed/attempted laps
                    while len(laps) > 0 and laps[-1] is None:
                        laps.pop()
                        lap_deleted.pop()

                    # Apply lap deletion penalties if any
                    del_count = best_laps_deleted_counts.get(driver_name, 0)
                    for _ in range(del_count):
                        # Find index of minimum non-deleted lap time (excluding Lap 0, i.e. index > 0)
                        best_idx = -1
                        min_val = float('inf')
                        for idx in range(1, len(laps)):
                            val = laps[idx]
                            if val is not None and not lap_deleted[idx]:
                                if val < min_val:
                                    min_val = val
                                    best_idx = idx
                        if best_idx != -1:
                            lap_deleted[best_idx] = True

                    # Build lap_times list for DB (NULL/None for deleted laps)
                    db_laps = [None if lap_deleted[i] else laps[i] for i in range(len(laps))]

                    # Pre-compute best lap time (excluding Lap 0)
                    non_deleted_laps: list[float] = [
                        t for i, t in enumerate(db_laps) if i > 0 and t is not None
                    ]
                    best_lap_seconds = min(non_deleted_laps) if non_deleted_laps else None

                    # Normalize session results fields
                    pos = _normalize_pos(row.get('Pos'))
                    positions_gained = 0
                    if 'Positions Gained' in row:
                        try:
                            positions_gained = int(float(row['Positions Gained']))
                        except (ValueError, TypeError):
                            pass

                    gap = str(row['Gap']) if 'Gap' in row and pd.notna(row['Gap']) else None
                    champ_points = parse_champ_points(row)

                    # Insert into session_results
                    cur.execute(
                        '''
                        INSERT INTO session_results (
                            session_id, driver_id, pos, positions_gained, gap,
                            champ_points, best_lap_seconds, lap_times
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ''',
                        (
                            db_session_id, driver_id, str(pos), positions_gained, gap,
                            champ_points, best_lap_seconds, db_laps
                        )
                    )

        # Insert penalties
        if not pen_df.empty:
            for _, row in pen_df.iterrows():
                d_name = row.get('Name')
                if not d_name or pd.isna(d_name):
                    continue

                driver_id = get_or_create_driver(cur, str(d_name).strip())
                penalty_desc = row.get('Penalty')
                pos_added = float(row.get('PositionsAdded', 0.0))
                sec_added = float(row.get('SecondsAdded', 0.0))
                excluded = bool(row.get('Excluded', False))
                best_lap_del = bool(row.get('BestLapDeleted', 0) > 0)

                cur.execute(
                    '''
                    INSERT INTO penalties (
                        session_id, driver_id, penalty_desc, positions_added,
                        seconds_added, excluded, best_lap_deleted
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ''',
                    (
                        db_session_id, driver_id, penalty_desc, pos_added,
                        sec_added, excluded, best_lap_del
                    )
                )

    conn.commit()
    return db_session_id


def populate_database(base_dir: str, conn: Any, clean_orphans: bool = True) -> None:
    base_dir = os.path.realpath(os.path.expanduser(base_dir))
    if not os.path.isdir(base_dir):
        raise NotADirectoryError(f'Data directory not found: {base_dir}')

    # Find metadata files
    pattern = os.path.join(base_dir, '**', '*_metadata.json')
    metadata_files = glob.glob(pattern, recursive=True)

    # Fetch existing session IDs from DB to avoid re-uploading them
    existing_session_ids = set()
    with conn.cursor() as cur:
        cur.execute('SELECT session_id FROM sessions')
        existing_session_ids = {row[0] for row in cur.fetchall()}

    active_session_ids = set()
    active_meeting_folders = set()

    for meta_path in sorted(metadata_files):
        # Ignore telemetry folders
        rel_path = os.path.relpath(meta_path, base_dir)
        if 'telemetry' in rel_path.split(os.sep):
            continue

        if not _check_path_is_safe(base_dir, meta_path):
            print(f'Skipping unsafe path: {meta_path}')
            continue

        meeting_dir = os.path.dirname(meta_path)
        meeting_dir_rel = os.path.relpath(meeting_dir, base_dir)
        meeting_folder = os.path.basename(meeting_dir)
        session_filename = os.path.basename(meta_path).replace('_metadata.json', '')

        # Combine meeting folder and session filename to form a globally unique DB session_id
        db_session_id = f'{meeting_folder}_{session_filename}'

        active_session_ids.add(db_session_id)
        active_meeting_folders.add(meeting_dir_rel)

        if db_session_id in existing_session_ids:
            continue

        try:
            populate_single_session(meta_path, conn, base_dir)
            print(f'Successfully processed session: {db_session_id}')
        except Exception as e:
            print(f'Failed to process session {db_session_id} from {meta_path}: {e}')

    # Synchronize orphans
    if clean_orphans:
        with conn.cursor() as cur:
            # 1. Delete orphaned sessions (not on disk)
            if active_session_ids:
                cur.execute('DELETE FROM sessions WHERE session_id NOT IN %s', (tuple(active_session_ids),))
            else:
                cur.execute('DELETE FROM sessions')

            # 2. Fetch list of meetings from the DB and drop those which are not in files
            cur.execute('SELECT id FROM meetings')
            db_meetings = {row[0] for row in cur.fetchall()}
            orphaned_meetings = db_meetings - active_meeting_folders
            if orphaned_meetings:
                cur.execute('DELETE FROM meetings WHERE id IN %s', (tuple(orphaned_meetings),))
                print(f'Purged {len(orphaned_meetings)} orphaned meetings from the database.')

            # 3. Clean up orphaned drivers
            cur.execute('''
                DELETE FROM drivers
                WHERE id NOT IN (SELECT DISTINCT driver_id FROM session_results)
                  AND id NOT IN (SELECT DISTINCT driver_id FROM penalties)
            ''')
        conn.commit()
        print('Successfully synchronized database: purged orphaned sessions, meetings, and drivers.')


def main() -> None:
    parser = argparse.ArgumentParser(description='Populate and synchronize the race relational database.')
    parser.add_argument(
        '--config', default='../config.json',
        help='Path to config.json containing database credentials.'
    )
    parser.add_argument('--data-dir', help='Override the data directory path from config.')
    parser.add_argument('--reset', action='store_true', help='Drop all tables and recreate the schema from scratch.')
    args = parser.parse_args()

    # Load Config
    if not os.path.exists(args.config):
        print(f'Error: Config file not found: {args.config}')
        sys.exit(1)

    try:
        with open(args.config, 'r', encoding='utf-8') as f:
            config_dict = json.load(f)
    except Exception as e:
        print(f'Error loading config file {args.config}: {e}')
        sys.exit(1)

    data_dir = args.data_dir or config_dict.get('data_dir')
    if not data_dir:
        print('Error: data_dir is not specified in config or arguments.')
        sys.exit(1)

    data_dir = os.path.abspath(os.path.expanduser(data_dir))
    db_conf = config_dict.get('database', {})

    print(f"Connecting to database {db_conf.get('dbname')} on {db_conf.get('host')}:{db_conf.get('port')}...")
    try:
        conn = psycopg2.connect(
            host=db_conf.get('host', 'localhost'),
            port=db_conf.get('port', 5432),
            dbname=db_conf.get('dbname', 'race_db'),
            user=db_conf.get('user', 'race_user'),
            password=db_conf.get('password', '')
        )
    except Exception as e:
        print(f'Database connection failed: {e}')
        sys.exit(1)

    try:
        create_schema(conn, drop_first=args.reset)
        populate_database(data_dir, conn, clean_orphans=True)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
