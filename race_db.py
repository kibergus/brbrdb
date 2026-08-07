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

from __future__ import annotations
import threading
import pandas as pd
import datetime
import os
import json
import re
import aliases
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from race_tools import sanitize
from typing import Iterable, Union, TypeAlias, Any
from dataclasses import dataclass, field


def _to_list(x: str | Iterable[str] | None) -> list[str]:
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


def _match_class(class_name: str, classes_filter: list[str], consolidated_classes_filter: list[str]) -> bool:
    """Checks if a class name matches the provided filters."""
    if classes_filter and class_name not in classes_filter:
        return False
    if consolidated_classes_filter:
        if aliases.get_consolidated_class(class_name) not in consolidated_classes_filter:
            return False
    return True


def _normalize_pos(v: str | int | float) -> int | str:
    """Normalize position values, preserving strings like DNS/DNF/DSQ."""
    try:
        # Try converting to float then int (handles "1.0")
        return int(float(v))
    except (ValueError, TypeError):
        # Helpfully preserve original string for DNS, DNF, etc.
        return str(v) if not pd.isna(v) else 999


TraversalInput: TypeAlias = Union[str, None, Iterable['TraversalInput']]


def check_for_dir_traversal(items: TraversalInput) -> None:
    """Check if any of the provided values contain path traversal characters."""
    if items is None:
        return
    if isinstance(items, str):
        if '..' in items or items.startswith('/') or items.startswith('\\'):
            raise ValueError(f'Security error: Invalid input {items!r} contains directory traversal characters.')
        return
    if isinstance(items, Iterable):
        for val in items:
            check_for_dir_traversal(val)


@dataclass(kw_only=True)
class RaceMetadata:
    """Metadata for a race session, loaded from _metadata.json files."""
    # Club or league name (e.g., 'club100', 'fat')
    league: str = ''
    # Karting class (e.g., ['cadet'], ['junior', 'senior'])
    class_name: list[str] = field(default_factory=list)
    # Name of the track where the race took place
    track_name: str = ''
    # Full name of the session (e.g., 'Cadet Heat 1')
    session_name: str = ''
    # Normalized session type (e.g., 'Heat 1', 'Final')
    short_name: str = ''
    # Full name of the meeting/event
    meeting_name: str = ''
    # Absolute path to the directory containing session data
    meeting_dir: str = ''
    # Basename of the meeting directory
    meeting_folder: str = ''
    # Unique ID for the session within a meeting folder
    session_id: str = ''
    # YYYY-MM-DD HH:MM format
    session_start_datetime: str = ''
    # Source URL on AlphaTiming website
    alphatiming_url: str = ''
    # e.g., 'Dry', 'Wet'
    track_conditions: str = ''
    # Air/Track temperature
    temperature: str = ''
    # Weather description (e.g., 'sunny', 'cloudy')
    weather: str = ''

    @property
    def date(self) -> str:
        """Returns YYYY-MM-DD format extracted from session_start_datetime."""
        if not self.session_start_datetime:
            return ''
        if ' ' not in self.session_start_datetime:
            raise ValueError(f'Invalid session_start_datetime format: {self.session_start_datetime!r}')
        return self.session_start_datetime.split(' ')[0]

    @property
    def time(self) -> str:
        """Returns HH_MM format extracted from session_start_datetime."""
        if not self.session_start_datetime:
            return ''
        if ' ' not in self.session_start_datetime:
            raise ValueError(f'Invalid session_start_datetime format: {self.session_start_datetime!r}')
        return self.session_start_datetime.split(' ')[1].replace(':', '_')

    @property
    def session_start(self) -> datetime.datetime:
        """Returns session_start_datetime as a naive datetime object. Raises if invalid."""
        if not self.session_start_datetime:
            raise ValueError(f'Session start time missing for session {self.session_id}')
        try:
            return datetime.datetime.strptime(self.session_start_datetime, '%Y-%m-%d %H:%M')
        except ValueError:
            raise ValueError(f'Invalid session_start_datetime format: {self.session_start_datetime!r}') from None

    @property
    def session_end(self) -> datetime.datetime:
        """Calculates session end time by adding total lap duration to start time. Raises if data missing."""
        start = self.session_start

        csv_path = os.path.join(self.meeting_dir, f'{self.session_id}.csv')
        if not os.path.exists(csv_path):
            raise FileNotFoundError(f'Session data CSV missing for {self.session_id} at {csv_path}')

        df = _read_cleaned_csv(csv_path)
        lap_cols = [c for c in df.columns if re.match(r'Lap \d+', c)]
        if not lap_cols:
            raise ValueError(f'No lap data found in CSV for {self.session_id}')

        max_dur = 0.0
        for _, row in df.iterrows():
            total = 0.0
            for col in lap_cols:
                t = sanitize.parse_time(row[col])
                if t is not None:
                    total += t
            if total > max_dur:
                max_dur = total

        return start + datetime.timedelta(seconds=max_dur)


class RaceDB:
    def __init__(self, base_dir: str, db_config: dict):
        self.base_dir = os.path.realpath(os.path.expanduser(base_dir))
        self._track_mappings: dict[str, str] | None = None
        self._local = threading.local()

        # Initialize a threaded connection pool
        self._pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=20,
            host=db_config['host'],
            port=db_config['port'],
            dbname=db_config['dbname'],
            user=db_config['user'],
            password=db_config['password']
        )

    @property
    def conn(self) -> psycopg2.extensions.connection:
        conn = getattr(self._local, 'conn', None)
        if conn is None or conn.closed:
            conn = self._pool.getconn()
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, 'conn', None)
        if conn:
            # Ensure any lingering transactions on this thread are rolled back
            if not conn.closed:
                conn.rollback()
            # Release the connection back to the pool
            self._pool.putconn(conn)
            self._local.conn = None

    def _check_path_is_safe(self, path: str) -> bool:
        """Validate that a path is within the base_dir."""
        try:
            # Resolve symbolic links and normalize paths
            real_path = os.path.realpath(path)
            # Check if self.base_dir is a common prefix for the given path
            return os.path.commonpath([self.base_dir, real_path]) == self.base_dir
        except (ValueError, OSError):
            return False

    def _resolve_meeting_dir(self, meeting_dir: str) -> str:
        """Resolve database-stored relative meeting_dir to the currently configured base_dir."""
        if not meeting_dir:
            return ''
        return os.path.abspath(os.path.join(self.base_dir, meeting_dir))

    def list_leagues(self) -> list[str]:
        with self.conn.cursor() as cur:
            cur.execute('SELECT DISTINCT league FROM meetings ORDER BY league')
            return [r[0] for r in cur.fetchall()]

    def list_meetings(
        self,
        league: str,
        year: str | None = None,
        class_name: str | None = None,
        driver_names: str | Iterable[str] | None = None
    ) -> list[tuple[str, str, str]]:
        """Returns list of (class, date, track_name) for a league, optionally filtered."""
        check_for_dir_traversal([league, year, class_name, driver_names])

        query = '''
            SELECT DISTINCT s.class_name, m.date, m.track_name
            FROM sessions s
            JOIN meetings m ON s.meeting_id = m.id
        '''
        where_clauses = ['m.league = %s']
        params: list[Any] = [league]

        if driver_names:
            query += ' JOIN session_results sr ON s.session_id = sr.session_id'
            query += ' JOIN drivers d ON sr.driver_id = d.id'
            drivers_list = _to_list(driver_names)
            where_clauses.append('d.driver_name = ANY(%s)')
            params.append(drivers_list)

        if year:
            where_clauses.append('m.date::text LIKE %s')
            params.append(year + '%')
        if class_name:
            where_clauses.append('s.class_name && %s')
            params.append([class_name])

        query += ' WHERE ' + ' AND '.join(where_clauses)

        meetings = []
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            for class_arr, date_val, track_name in rows:
                for c in (class_arr or []):
                    c = c.strip()
                    if not c:
                        continue
                    meetings.append((c, str(date_val), track_name))
        return sorted(list(set(meetings)))

    def get_championship(self, league: str, class_name: str, year: str) -> dict | None:
        """Loads championship_{year}.json from the league/class directory if it exists."""
        check_for_dir_traversal([league, class_name, year])
        path = os.path.join(self.base_dir, league, class_name, f'championship_{year}.json')
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return None
        return None

    def list_years(self, league: str, class_name: str | None = None) -> list[str]:
        """Returns sorted list of years (descending) that have data for a league/class."""
        meetings = self.list_meetings(league, class_name=class_name)
        return sorted(set(m[1].split('-')[0] for m in meetings), reverse=True)

    def get_driver_names(self) -> list[str]:
        """Returns a sorted list of unique driver names across all sessions."""
        with self.conn.cursor() as cur:
            cur.execute('SELECT driver_name FROM drivers ORDER BY driver_name')
            return [r[0] for r in cur.fetchall()]

    def get_session_drivers(self, session_id: str, meeting_folder: str | None = None) -> list[str]:
        """Returns a sorted list of unique driver names for a specific session."""
        if meeting_folder and not session_id.startswith(meeting_folder):
            full_sid = f"{meeting_folder}_{session_id}"
        else:
            full_sid = session_id
        with self.conn.cursor() as cur:
            cur.execute('''
                SELECT DISTINCT d.driver_name
                FROM session_results sr
                JOIN drivers d ON sr.driver_id = d.id
                WHERE sr.session_id = %s OR sr.session_id = %s
                ORDER BY d.driver_name
            ''', (full_sid, session_id))
            return [r[0] for r in cur.fetchall()]

    def _load_penalty_file(self, csv_path: str) -> pd.DataFrame:
        """Helper to load and parse a single penalty CSV file."""
        if not os.path.exists(csv_path):
            return pd.DataFrame()

        df = _read_cleaned_csv(csv_path)
        if df.empty:
            return pd.DataFrame()

        if 'Penalty' not in df.columns:
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
        def get_del_count(p: str | float) -> int:
            m = re.search(r'Best\s+(\d+)?\s*laptimes?\s+deleted', str(p), re.IGNORECASE)
            if not m:
                return 0
            return int(m.group(1)) if m.group(1) else 1

        df['BestLapDeleted'] = df['Penalty'].apply(get_del_count).astype(int)

        return df

    def find_sessions(self,
                      leagues: str | Iterable[str] | None = None,
                      classes: str | Iterable[str] | None = None,
                      date: str | None = None,
                      track: str | None = None,
                      year: str | None = None,
                      track_conditions: str | Iterable[str] | None = None,
                      consolidated_classes: str | Iterable[str] | None = None,
                      driver_names: str | Iterable[str] | None = None,
                      session_id: str | Iterable[str] | None = None) -> list[RaceMetadata]:
        """Return a list of RaceMetadata objects matching the given filters."""
        check_for_dir_traversal([
            leagues, classes, date, track, year, track_conditions, consolidated_classes, driver_names, session_id
        ])

        leagues_filter = _to_list(leagues)
        classes_filter = _to_list(classes)
        track_conditions_filter = _to_list(track_conditions)
        consolidated_classes_filter = _to_list(consolidated_classes)
        drivers_filter = _to_list(driver_names)
        session_id_filter = _to_list(session_id)

        query = '''
            SELECT
                m.league,
                s.class_name,
                m.track_name,
                s.session_name,
                s.short_name,
                m.meeting_name,
                m.meeting_dir,
                m.id AS meeting_folder,
                s.session_id,
                s.session_start_datetime,
                s.alphatiming_url,
                s.track_conditions,
                s.temperature,
                s.weather,
                m.date
            FROM sessions s
            JOIN meetings m ON s.meeting_id = m.id
        '''
        where_clauses = []
        params: list[Any] = []

        if leagues_filter:
            where_clauses.append('m.league = ANY(%s)')
            params.append(leagues_filter)

        if date:
            where_clauses.append('m.date = %s')
            params.append(date)

        if track:
            where_clauses.append('m.track_name = %s')
            params.append(track)

        if year:
            where_clauses.append('m.date::text LIKE %s')
            params.append(year + '%')

        if track_conditions_filter:
            where_clauses.append('s.track_conditions = ANY(%s)')
            params.append(track_conditions_filter)

        if drivers_filter:
            where_clauses.append('''
                s.session_id IN (
                    SELECT DISTINCT r.session_id
                    FROM session_results r
                    JOIN drivers d ON r.driver_id = d.id
                    WHERE d.driver_name = ANY(%s)
                )
            ''')
            params.append(drivers_filter)

        if classes_filter:
            where_clauses.append('s.class_name && %s')
            params.append(classes_filter)

        if session_id_filter:
            session_patterns = []
            for sid in session_id_filter:
                session_patterns.append(sid)
                if not sid.startswith('%'):
                    session_patterns.append(f'%_{sid}')
            where_clauses.append('s.session_id LIKE ANY(%s)')
            params.append(session_patterns)

        if where_clauses:
            query += ' WHERE ' + ' AND '.join(where_clauses)

        sessions = []
        with self.conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
            for row in rows:
                (
                    league, class_name_arr, track_name, session_name, short_name,
                    meeting_name, meeting_dir, meeting_folder, row_session_id,
                    session_start_datetime, alphatiming_url, track_conditions,
                    temperature, weather, db_date
                ) = row

                meta_classes = [c.strip() for c in (class_name_arr or []) if c.strip()]

                if consolidated_classes_filter:
                    if not any(_match_class(c, [], consolidated_classes_filter) for c in meta_classes):
                        continue

                meeting_folder_name = os.path.basename(meeting_dir) if meeting_dir else ''
                session_filename = row_session_id
                if meeting_folder_name and row_session_id.startswith(meeting_folder_name + '_'):
                    session_filename = row_session_id[len(meeting_folder_name) + 1:]

                if session_id_filter:
                    if row_session_id not in session_id_filter and session_filename not in session_id_filter:
                        continue

                m_start_dt = session_start_datetime.strftime('%Y-%m-%d %H:%M') if session_start_datetime else ''

                meta = RaceMetadata(
                    league=league,
                    class_name=meta_classes,
                    track_name=track_name,
                    session_name=session_name,
                    short_name=short_name,
                    meeting_name=meeting_name,
                    meeting_dir=self._resolve_meeting_dir(meeting_dir),
                    meeting_folder=meeting_folder_name,
                    session_id=session_filename,
                    session_start_datetime=m_start_dt,
                    alphatiming_url=alphatiming_url or '',
                    track_conditions=str(track_conditions) if track_conditions else '',
                    temperature=str(temperature) if temperature is not None else '',
                    weather=weather or ''
                )
                sessions.append(meta)

        sessions.sort(key=lambda x: (
            x.league, x.class_name[0] if x.class_name else '', x.date, x.session_start_datetime
        ))
        return sessions

    def load(self,
             leagues: str | Iterable[str] | None = None,
             classes: str | Iterable[str] | None = None,
             date: str | None = None,
             track: str | None = None,
             year: str | None = None,
             track_conditions: str | Iterable[str] | None = None,
             driver_names: str | Iterable[str] | None = None,
             consolidated_classes: str | Iterable[str] | None = None) -> pd.DataFrame:
        """
        Loads laptimes from the database and returns a melted long-format DataFrame.

        Returned Columns:
            - Lap (int): Lap number (Lap 0 is the starting/grid offset).
            - LapTime (str): Formatted lap time string.
            - LapTimeSeconds (float): Parsed lap time in seconds (0.0 for deleted laps).
            - LapTimeDeleted (bool): True if the lap was voided by a penalty.
            - Name (str): Driver name.
            - League, Class, Date, TrackName, SessionID: Standard identifiers.
            - Pos (str/int), PositionsGained (int), Gap (str): Race result metadata.
            - SessionName, MeetingName, DateTime, AlphaTimingURL: Session metadata.
            - TrackConditions, Temperature, Weather: Environment info.
        """
        check_for_dir_traversal([
            leagues, classes, date, track, year, track_conditions, driver_names, consolidated_classes
        ])
        query = '''
            SELECT
                r.session_id,
                d.driver_name AS "Name",
                r.pos AS "Pos",
                r.positions_gained AS "PositionsGained",
                r.gap AS "Gap",
                r.champ_points AS "ChampPoints",
                r.best_lap_seconds,
                r.lap_times,
                m.league AS "League",
                s.class_name AS "Class",
                m.date AS "Date",
                m.track_name AS "TrackName",
                s.session_name AS "SessionName",
                m.meeting_name AS "MeetingName",
                s.session_start_datetime AS "DateTime",
                s.track_conditions AS "TrackConditions",
                s.temperature AS "Temperature",
                s.weather AS "Weather",
                s.alphatiming_url AS "AlphaTimingURL",
                m.meeting_dir
            FROM session_results r
            JOIN sessions s ON r.session_id = s.session_id
            JOIN meetings m ON s.meeting_id = m.id
            JOIN drivers d ON r.driver_id = d.id
        '''

        where_clauses = []
        params: list[Any] = []

        if leagues:
            leagues_list = _to_list(leagues)
            where_clauses.append('m.league = ANY(%s)')
            params.append(leagues_list)

        if date:
            where_clauses.append('m.date::text LIKE %s')
            params.append(date.replace('_', '-') + '%')

        if track:
            where_clauses.append('m.track_name = %s')
            params.append(track)

        if year:
            where_clauses.append('m.date::text LIKE %s')
            params.append(year + '%')

        if track_conditions:
            cond_list = _to_list(track_conditions)
            where_clauses.append('s.track_conditions = ANY(%s)')
            params.append(cond_list)

        if driver_names:
            drivers_list = _to_list(driver_names)
            where_clauses.append('d.driver_name = ANY(%s)')
            params.append(drivers_list)

        classes_filter = _to_list(classes)
        if classes_filter:
            where_clauses.append('s.class_name && %s')
            params.append(classes_filter)

        consolidated_classes_filter = _to_list(consolidated_classes)

        if where_clauses:
            query += ' WHERE ' + ' AND '.join(where_clauses)

        with self.conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

        df_rows = []
        for r_row in rows:
            (
                session_id, driver_name, pos, pos_gained, gap, champ_points,
                best_lap_sec, lap_times, league, db_class_arr, meeting_date,
                track_name, session_name, meeting_name, dt, cond, temp, weather, url, meeting_dir
            ) = r_row

            meta_classes = [c.strip() for c in (db_class_arr or []) if c.strip()]

            meeting_folder = os.path.basename(meeting_dir)
            session_filename = session_id
            if session_id.startswith(meeting_folder + '_'):
                session_filename = session_id[len(meeting_folder) + 1:]

            matched_class = None
            for c in meta_classes:
                if _match_class(c, classes_filter, consolidated_classes_filter):
                    matched_class = c
                    break

            if matched_class is None:
                continue

            if not lap_times:
                continue

            for lap_num, lap_time_sec in enumerate(lap_times):
                is_deleted = lap_time_sec is None
                # Emit both deleted and non-deleted laps so consumers can filter
                # (deleted laps have LapTimeDeleted=True and LapTimeSeconds=0.0)
                if not is_deleted or lap_num > 0:
                    lap_time_str = sanitize.format_time(lap_time_sec) if lap_time_sec is not None else ''
                    df_rows.append({
                        'Lap': lap_num,
                        'LapTime': lap_time_str,
                        'LapTimeSeconds': float(lap_time_sec) if lap_time_sec is not None else 0.0,
                        'LapTimeDeleted': is_deleted,
                        'Name': driver_name,
                        'League': league,
                        'Class': matched_class,
                        'Date': str(meeting_date),
                        'TrackName': track_name,
                        'SessionID': session_filename,
                        'Pos': _normalize_pos(pos),
                        'PositionsGained': int(pos_gained or 0),
                        'Gap': gap or '',
                        'ChampPoints': int(champ_points) if champ_points is not None else None,
                        'SessionName': session_name or '',
                        'MeetingName': meeting_name or '',
                        'DateTime': dt.strftime('%Y-%m-%d %H:%M') if dt else '',
                        'TrackConditions': cond or '',
                        'Temperature': str(temp) if temp is not None else '',
                        'Weather': weather or '',
                        'AlphaTimingURL': url or ''
                    })
        if df_rows:
            return pd.DataFrame(df_rows)
        else:
            return pd.DataFrame(columns=[
                'Lap', 'LapTime', 'LapTimeSeconds', 'LapTimeDeleted', 'Name',
                'League', 'Class', 'Date', 'TrackName', 'SessionID',
                'Pos', 'PositionsGained', 'Gap', 'ChampPoints', 'SessionName', 'MeetingName', 'DateTime',
                'TrackConditions', 'Temperature', 'Weather', 'AlphaTimingURL'
            ])

    def load_penalties(self,
                       leagues: str | Iterable[str] | None = None,
                       classes: str | Iterable[str] | None = None,
                       date: str | None = None,
                       track: str | None = None,
                       year: str | None = None,
                       track_conditions: str | Iterable[str] | None = None,
                       driver_names: str | Iterable[str] | None = None,
                       consolidated_classes: str | Iterable[str] | None = None) -> pd.DataFrame:
        """
        Loads penalties from the database and returns a DataFrame.

        Returned Columns:
            - Name: str (Driver name)
            - Penalty: str (Raw penalty description)
            - PositionsAdded (float): Number of positions the driver was demoted.
            - SecondsAdded (float): Seconds added to the total race time.
            - Excluded (bool): True if the driver is disqualified.
            - BestLapDeleted (int): Number of best laptimes to be deleted.
            - League, Class, Date, TrackName, SessionID: Standard identifiers.
            - SessionName, MeetingName, DateTime: Metadata from JSON.
        """
        check_for_dir_traversal([
            leagues, classes, date, track, year, track_conditions, driver_names, consolidated_classes
        ])
        query = '''
            SELECT
                d.driver_name AS "Name",
                p.penalty_desc AS "Penalty",
                p.positions_added AS "PositionsAdded",
                p.seconds_added AS "SecondsAdded",
                p.excluded AS "Excluded",
                p.best_lap_deleted AS "BestLapDeleted",
                m.league AS "League",
                s.class_name AS "Class",
                m.date AS "Date",
                m.track_name AS "TrackName",
                p.session_id AS "SessionID",
                s.session_name AS "SessionName",
                m.meeting_name AS "MeetingName",
                s.session_start_datetime AS "DateTime",
                m.meeting_dir
            FROM penalties p
            JOIN sessions s ON p.session_id = s.session_id
            JOIN meetings m ON s.meeting_id = m.id
            JOIN drivers d ON p.driver_id = d.id
        '''

        where_clauses = []
        params: list[Any] = []

        if leagues:
            leagues_list = _to_list(leagues)
            where_clauses.append('m.league = ANY(%s)')
            params.append(leagues_list)

        if date:
            where_clauses.append('m.date::text LIKE %s')
            params.append(date.replace('_', '-') + '%')

        if track:
            where_clauses.append('m.track_name = %s')
            params.append(track)

        if year:
            where_clauses.append('m.date::text LIKE %s')
            params.append(year + '%')

        if track_conditions:
            cond_list = _to_list(track_conditions)
            where_clauses.append('s.track_conditions = ANY(%s)')
            params.append(cond_list)

        if driver_names:
            drivers_list = _to_list(driver_names)
            where_clauses.append('d.driver_name = ANY(%s)')
            params.append(drivers_list)

        classes_filter = _to_list(classes)
        if classes_filter:
            where_clauses.append('s.class_name && %s')
            params.append(classes_filter)

        consolidated_classes_filter = _to_list(consolidated_classes)

        if where_clauses:
            query += ' WHERE ' + ' AND '.join(where_clauses)

        with self.conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

        df_rows = []
        for row in rows:
            (
                driver_name, penalty_desc, pos_added, sec_added, excluded,
                best_lap_del, league, db_class_arr, meeting_date, track_name,
                session_id, session_name, meeting_name, dt, meeting_dir
            ) = row

            meta_classes = [c.strip() for c in (db_class_arr or []) if c.strip()]

            meeting_folder = os.path.basename(meeting_dir)
            session_filename = session_id
            if session_id.startswith(meeting_folder + '_'):
                session_filename = session_id[len(meeting_folder) + 1:]

            for c in meta_classes:
                if not _match_class(c, classes_filter, consolidated_classes_filter):
                    continue

                df_rows.append({
                    'Name': driver_name,
                    'Penalty': penalty_desc or '',
                    'PositionsAdded': float(pos_added or 0.0),
                    'SecondsAdded': float(sec_added or 0.0),
                    'Excluded': bool(excluded),
                    'BestLapDeleted': int(best_lap_del or 0),
                    'League': league,
                    'Class': c,
                    'Date': str(meeting_date),
                    'TrackName': track_name,
                    'SessionID': session_filename,
                    'SessionName': session_name or '',
                    'MeetingName': meeting_name or '',
                    'DateTime': dt.strftime('%Y-%m-%d %H:%M') if dt else ''
                })
        if df_rows:
            return pd.DataFrame(df_rows)
        else:
            return pd.DataFrame(columns=[
                'Name', 'Penalty', 'PositionsAdded', 'SecondsAdded', 'Excluded',
                'BestLapDeleted', 'League', 'Class', 'Date', 'TrackName', 'SessionID',
                'SessionName', 'MeetingName', 'DateTime'
            ])

    def get_track_mappings(self) -> dict[str, str]:
        """
        Scans the tracks directory and returns a dictionary mapping lowercased track names,
        aliases, and base filenames to the canonical JSON base filename (without extension).
        """
        if self._track_mappings is not None:
            return self._track_mappings

        tracks_dir = os.path.join(self.base_dir, 'tracks')
        mappings: dict[str, str] = {}
        if not os.path.exists(tracks_dir):
            self._track_mappings = mappings
            return mappings

        for filename in os.listdir(tracks_dir):
            if filename.endswith('.json') and not filename.startswith('.'):
                base_name = filename[:-5]

                # Map the base filename itself case-insensitively
                mappings[base_name.lower().strip()] = base_name

                # Read track_name and aliases from the JSON file
                filepath = os.path.join(tracks_dir, filename)
                with open(filepath, 'r', encoding='utf-8') as f:
                    track_data = json.load(f)

                    t_name = track_data.get('track_name')
                    if t_name:
                        mappings[t_name.lower().strip()] = base_name

                    aliases_list = track_data.get('aliases', [])
                    if isinstance(aliases_list, list):
                        for alias in aliases_list:
                            if isinstance(alias, str):
                                mappings[alias.lower().strip()] = base_name

        self._track_mappings = mappings
        return mappings

    def _resolve_track_filename(self, track_name: str) -> str | None:
        """
        Resolves a track name to the base filename of the track JSON/GeoJSON.
        Uses a case-insensitive match against track names, aliases, and filenames.
        Returns None if no match is found.
        """
        if not track_name:
            return None

        mappings = self.get_track_mappings()
        return mappings.get(track_name.lower().strip())

    def get_track(self, track_name: str) -> dict | None:
        """
        Loads the track JSON data and its associated GeoJSON data (if it exists)
        by resolving the track name case-insensitively.
        Returns None if no track matches the name.
        """
        base_name = self._resolve_track_filename(track_name)
        if not base_name:
            return None

        tracks_dir = os.path.join(self.base_dir, 'tracks')
        json_path = os.path.join(tracks_dir, f'{base_name}.json')
        if not os.path.exists(json_path):
            return None

        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Try to load GeoJSON for boundaries and other features
        geojson_path = os.path.join(tracks_dir, f'{base_name}.geojson')
        if os.path.exists(geojson_path):
            with open(geojson_path, 'r', encoding='utf-8') as f:
                data['geojson'] = json.load(f)

        return data

    def check_media_file_permissions(self, filename: str, meeting_dir: str) -> None:
        """
        Validate that a media file is safe to serve.
        Checks for directory traversal.
        Raises ValueError if validation fails.
        """
        check_for_dir_traversal(filename)

    def has_telemetry(self, meeting_dir: str, session_id: str | None = None) -> bool:
        """
        Check if telemetry CSV files exist for a meeting directory and optional session_id.
        """
        telemetry_dir = os.path.join(meeting_dir, 'telemetry')
        if not os.path.exists(telemetry_dir):
            return False

        csv_files = [f for f in os.listdir(telemetry_dir) if f.endswith('.csv')]
        if not csv_files:
            return False

        if not session_id:
            return True

        for csv_file in csv_files:
            if (
                csv_file == session_id
                or csv_file.startswith(f'{session_id}_')
                or csv_file.startswith(f'{session_id}.')
            ):
                return True

        return False

    def get_video_data(self, meeting_dir: str) -> dict:
        """Loads and returns video metadata from the database for the specified meeting directory."""
        meeting_id = os.path.relpath(os.path.abspath(meeting_dir), os.path.abspath(self.base_dir))
        vdata = {}
        with self.conn.cursor() as cur:
            cur.execute(
                'SELECT filename, start_time, end_time FROM videos WHERE meeting_id = %s',
                (meeting_id,)
            )
            rows = cur.fetchall()
            for r in rows:
                filename, start_time, end_time = r
                vdata[filename] = {
                    'start': start_time.isoformat(),
                    'end': end_time.isoformat()
                }

        if not vdata:
            videos_file = os.path.join(meeting_dir, 'video.json')
            if os.path.exists(videos_file):
                with open(videos_file, 'r', encoding='utf-8') as f:
                    vdata = json.load(f)
        return vdata

    def get_overlapping_videos(self, session_meta: RaceMetadata, session_data: pd.DataFrame) -> list[dict]:
        """Calculates overlapping videos and lap markers for a given session."""
        vdata = self.get_video_data(session_meta.meeting_dir)
        if not vdata:
            return []

        # TODO: make timezone configurable, currently hardcoded to BST (UTC+1)
        bst_offset = datetime.timezone(datetime.timedelta(hours=1))
        session_start = datetime.datetime.strptime(
            session_meta.session_start_datetime, '%Y-%m-%d %H:%M'
        ).replace(tzinfo=bst_offset)

        # Load time corrections from time_correction.json
        time_correction_map = {}
        tc_file = os.path.join(session_meta.meeting_dir, 'time_correction.json')
        if os.path.exists(tc_file):
            with open(tc_file, 'r') as f:
                time_correction_map = json.load(f)

        time_correction = time_correction_map.get(session_meta.session_id, 0.0)
        session_start += datetime.timedelta(seconds=time_correction)

        max_duration = 0.0
        for _, group in session_data.groupby('Name'):
            dur = group['LapTimeSeconds'].sum()
            if pd.notna(dur) and dur > max_duration:
                max_duration = dur
        session_end = session_start + datetime.timedelta(seconds=max_duration)

        overlapping_videos = []
        for vfilename, vinfo in vdata.items():
            if vfilename.startswith('_'):  # Skip _folder and _comment
                continue
            v_start = datetime.datetime.fromisoformat(vinfo['start'])
            v_end = datetime.datetime.fromisoformat(vinfo['end'])

            if v_start <= session_end and v_end >= session_start:
                markers = []
                for driver_name, group in session_data.groupby('Name'):
                    cumul = 0.0
                    for _, lap_row in group.sort_values('Lap').iterrows():
                        lap_time = lap_row['LapTimeSeconds']
                        if pd.isna(lap_time):
                            continue

                        lap_start = session_start + datetime.timedelta(seconds=cumul)
                        lap_end = lap_start + datetime.timedelta(seconds=lap_time)
                        cumul += lap_time

                        # Overlap check: video [v_start, v_end] and lap [lap_start, lap_end]
                        if lap_start <= v_end and lap_end >= v_start:
                            video_time = max(0.0, (lap_start - v_start).total_seconds())
                            markers.append({
                                'driver': driver_name,
                                'lap': lap_row['Lap'],
                                'video_time': video_time,
                                'lap_time': lap_time
                            })

                markers.sort(key=lambda m: m['video_time'])
                overlapping_videos.append({
                    'filename': vfilename,
                    'markers': markers,
                    'v_start': v_start,
                    'duration': (v_end - v_start).total_seconds()
                })
        return overlapping_videos

    def get_photos(self, driver_name: str | None = None, meeting_dir: str | None = None) -> list[dict]:
        """
        Fetch photo metadata from the database, optionally filtered by driver name and/or meeting directory.
        """
        query = '''
            SELECT DISTINCT p.filename, p.taken_at, p.meeting_id
            FROM photos p
        '''
        params = []
        joins = []
        wheres = []

        if driver_name:
            joins.append('JOIN photo_drivers pd ON p.id = pd.photo_id')
            joins.append('JOIN drivers d ON pd.driver_id = d.id')
            wheres.append('d.driver_name = %s')
            params.append(driver_name)

        if meeting_dir:
            meeting_id = os.path.relpath(os.path.abspath(meeting_dir), os.path.abspath(self.base_dir))
            wheres.append('p.meeting_id = %s')
            params.append(meeting_id)

        if joins:
            query += ' ' + ' '.join(joins)
        if wheres:
            query += ' WHERE ' + ' AND '.join(wheres)

        query += ' ORDER BY p.taken_at DESC'

        with self.conn.cursor() as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()
            return [
                {
                    'filename': r[0],
                    'taken_at': r[1].isoformat() if r[1] else None,
                    'meeting_id': r[2]
                }
                for r in rows
            ]
