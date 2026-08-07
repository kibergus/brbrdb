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
import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import race_db

# Define the real test data path
TESTDATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'testdata', 'race_db')


def test_racedb_walk_and_cache() -> None:
    db = race_db.RaceDB(base_dir=TESTDATA_DIR, db_config={})

    # In testdata, league is 'piston_cup' from JSON
    leagues = db.list_leagues()
    assert 'piston_cup' in leagues

    meetings = db.list_meetings('piston_cup')
    # There should be at least two meetings (2026-05-10 and 2026-05-24)
    assert len(meetings) >= 2
    # Verify meeting format (class, date, track_name)
    dates = [m[1] for m in meetings]
    assert '2026-05-10' in dates
    assert '2026-05-24' in dates

    # Test find_sessions (replaces _walk_data)
    sessions = db.find_sessions(leagues='piston_cup')
    assert len(sessions) >= 2
    assert all(s.league == 'piston_cup' for s in sessions)
    assert any('2026' in s.date for s in sessions)

    # Test loading
    res = db.load(leagues='piston_cup')
    assert not res.empty
    assert 'League' in res.columns
    assert 'piston_cup' in res['League'].values
    assert 'LapTimeSeconds' in res.columns
    assert 'SessionName' in res.columns
    assert 'ChampPoints' in res.columns
    # Check if we got laps
    assert res['Lap'].max() > 0
    # Ensure LapTimeSeconds are parsed
    assert res['LapTimeSeconds'].dtype == float


def test_racedb_multiple_sessions_one_meeting() -> None:
    db = race_db.RaceDB(base_dir=TESTDATA_DIR, db_config={})

    # The folders in testdata contain multiple metadata files
    # _walk_and_cache should only add one entry per folder
    # Verify we have sessions from the same meeting

    # In 'bar/2026_05_24_radiator_springs', there are multiple sessions
    res = db.load(date='2026-05-24')
    assert not res.empty
    assert 'SessionID' in res.columns
    session_ids = res['SessionID'].unique()
    assert len(session_ids) > 1


def test_racemetadata_times() -> None:
    db = race_db.RaceDB(base_dir=TESTDATA_DIR, db_config={})
    sessions = db.find_sessions(date='2026-05-24', leagues='piston_cup')
    assert len(sessions) > 0

    s = sessions[0]
    # Test session_start
    start = s.session_start
    assert start.year == 2026
    assert start.month == 5
    assert start.day == 24

    # Test session_end
    end = s.session_end
    assert end > start
    # Quick check: session should be around 10-15 minutes long based on testdata
    duration = (end - start).total_seconds()
    assert 600 < duration < 1200


def test_to_list() -> None:
    assert race_db._to_list(None) == []
    assert race_db._to_list('single') == ['single']
    assert race_db._to_list(['a', 'b']) == ['a', 'b']


def test_race_metadata_properties() -> None:
    rm = race_db.RaceMetadata(session_start_datetime='2026-05-03 21:18')
    assert rm.date == '2026-05-03'
    assert rm.time == '21_18'

    rm2 = race_db.RaceMetadata(session_start_datetime='corrupt data')
    with pytest.raises(ValueError, match='Invalid session_start_datetime format'):
        _ = rm2.session_start

    rm3 = race_db.RaceMetadata(session_start_datetime='')
    with pytest.raises(ValueError, match='Session start time missing'):
        _ = rm3.session_start


def test_race_db_invalid_base_dir(tmp_path: Path) -> None:
    # Test base_dir not a directory
    with pytest.raises(NotADirectoryError, match='Data directory not found'):
        race_db.RaceDB(base_dir=str(tmp_path / 'nonexistent'), db_config={})


def test_race_db_skips_telemetry(tmp_path: Path) -> None:
    # Test telemetry sub-directories are skipped when looking for sessions.
    tel_dir = tmp_path / 'meeting' / 'telemetry'
    tel_dir.mkdir(parents=True)
    with open(tel_dir / 'session_metadata.json', 'w') as f:
        json.dump({'league': 'test'}, f)

    db = race_db.RaceDB(base_dir=str(tmp_path), db_config={})
    # The telemetry subdir metadata should not produce any sessions in the DB
    assert len(db.find_sessions()) == 0


def test_race_db_invalid_json(tmp_path: Path) -> None:
    # Test invalid JSON
    meeting_dir = tmp_path / 'meeting2'
    meeting_dir.mkdir()
    with open(meeting_dir / 'session_metadata.json', 'w') as f:
        f.write('invalid json')

    with pytest.raises(json.JSONDecodeError):
        race_db.RaceDB(base_dir=str(tmp_path), db_config={})


def test_race_db_list_meetings_filters() -> None:
    db = race_db.RaceDB(base_dir=TESTDATA_DIR, db_config={})

    # Test year filter
    meetings = db.list_meetings('piston_cup', year='2026')
    assert len(meetings) > 0
    meetings_bad = db.list_meetings('piston_cup', year='1980')
    assert len(meetings_bad) == 0

    # Test class filter
    all_meetings = db.list_meetings('piston_cup')
    if all_meetings:
        cls = all_meetings[0][0]
        meetings_cls = db.list_meetings('piston_cup', class_name=cls)
        assert len(meetings_cls) > 0
        meetings_cls_bad = db.list_meetings('piston_cup', class_name='nonexistent')
        assert len(meetings_cls_bad) == 0

    # Test driver_names filter
    drivers = db.get_driver_names()
    if drivers:
        meetings_driver = db.list_meetings('piston_cup', driver_names=drivers[0])
        assert len(meetings_driver) <= len(all_meetings)


def test_race_db_list_years() -> None:
    db = race_db.RaceDB(base_dir=TESTDATA_DIR, db_config={})
    years = db.list_years('piston_cup')
    assert '2026' in years


def test_race_db_find_sessions_filters() -> None:
    db = race_db.RaceDB(base_dir=TESTDATA_DIR, db_config={})

    # Test various filters in find_sessions
    s1 = db.find_sessions(leagues='piston_cup')
    assert len(s1) > 0

    s2 = db.find_sessions(leagues='nonexistent')
    assert len(s2) == 0

    s3 = db.find_sessions(leagues='piston_cup', year='2026')
    assert len(s3) > 0

    s4 = db.find_sessions(leagues='piston_cup', track='Radiator Springs')
    assert len(s4) > 0

    # Test session_id filter
    if s1:
        sid = s1[0].session_id
        s_by_id = db.find_sessions(session_id=sid)
        assert len(s_by_id) > 0
        assert any(m.session_id == sid for m in s_by_id)

    # Test consolidated_classes
    # Based on aliases.py, club100 usually has Lightweight/Heavyweight
    _ = db.find_sessions(consolidated_classes='Lightweight')


def test_race_db_load_filters() -> None:
    db = race_db.RaceDB(base_dir=TESTDATA_DIR, db_config={})

    # Test driver filter
    csv_path = os.path.join(TESTDATA_DIR, 'bar', '2026_05_24_radiator_springs', '10_20_s1.csv')
    df_sample = pd.read_csv(csv_path)
    if not df_sample.empty and 'Name' in df_sample.columns:
        driver = df_sample['Name'].iloc[0]
        res = db.load(driver_names=driver)
        assert not res.empty
        assert (res['Name'] == driver).all()

        res_bad = db.load(driver_names='Nonexistent Driver')
        assert res_bad.empty


def test_load_penalties_and_overlapping_videos(tmp_path: Path) -> None:
    # Setup mock meeting with penalties and videos
    meeting_dir = tmp_path / 'meeting'
    meeting_dir.mkdir()

    meta = {
        'league': 'test_league',
        'class_name': ['test_class'],
        'track_name': 'test_track',
        'session_name': 'test_session',
        'session_start_datetime': '2026-05-03 10:00',
        'track_conditions': 'Dry'
    }
    with open(meeting_dir / 's1_metadata.json', 'w') as f:
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

    video_data = {
        'video1.mp4': {
            'start': '2026-05-03T10:00:00+01:00',
            'end': '2026-05-03T10:05:00+01:00'
        }
    }
    with open(meeting_dir / 'video.json', 'w') as f:
        json.dump(video_data, f)

    db = race_db.RaceDB(base_dir=str(tmp_path), db_config={})

    # Test load_penalties
    pens = db.load_penalties(leagues='test_league')
    assert not pens.empty
    assert 'Driver A' in pens['Name'].values
    assert pens.iloc[0]['SecondsAdded'] == 5.0
    assert pens.iloc[1]['BestLapDeleted'] == 1

    # Test load with penalties (lap deletion)
    df = db.load(leagues='test_league')
    # Driver B should have one lap deleted.
    driver_b = df[df['Name'] == 'Driver B']
    assert driver_b['LapTimeDeleted'].any()

    # Test overlapping videos
    sessions = db.find_sessions(leagues='test_league')
    videos = db.get_overlapping_videos(sessions[0], df)
    assert len(videos) > 0
    assert videos[0]['filename'] == 'video1.mp4'
    assert len(videos[0]['markers']) > 0


def test_race_metadata_session_end_errors(tmp_path: Path) -> None:
    rm = race_db.RaceMetadata(meeting_dir=str(tmp_path), session_id='s1', session_start_datetime='2026-05-03 10:00')

    # Missing CSV
    with pytest.raises(FileNotFoundError):
        _ = rm.session_end

    # No lap data
    pd.DataFrame({'Name': ['A']}).to_csv(tmp_path / 's1.csv', index=False)
    with pytest.raises(ValueError, match='No lap data found'):
        _ = rm.session_end


def test_check_path_is_safe_errors(tmp_path: Path) -> None:
    db = race_db.RaceDB(base_dir=str(tmp_path), db_config={})
    with patch('os.path.realpath', side_effect=OSError('Mocked error')):
        assert db._check_path_is_safe('/any/path') is False


def test_dir_traversal_empty_iterable() -> None:
    # Coverage for line 56 in race_db.py
    race_db.check_for_dir_traversal([])


def test_race_db_walk_unsafe_path(tmp_path: Path) -> None:
    # The conftest patched __init__ calls glob.glob to scan for metadata files.
    # If glob returns a path outside base_dir, a ValueError should be raised.
    with patch('glob.glob', return_value=['/etc/passwd_metadata.json']):
        with pytest.raises(ValueError, match='Found metadata file outside base directory'):
            race_db.RaceDB(base_dir=str(tmp_path), db_config={})


def test_load_penalties_no_metadata(tmp_path: Path) -> None:
    meeting_dir = tmp_path / 'meeting'
    meeting_dir.mkdir()

    # Need at least one metadata file for the meeting to be 'found'
    meta = {'league': 'test', 'class_name': ['test']}
    with open(meeting_dir / 'other_metadata.json', 'w') as f:
        json.dump(meta, f)

    # Penalty file WITH matching metadata file
    penalties = {
        'Name': ['Driver A'],
        'Penalty': ['+5 Seconds Penalty']
    }
    pd.DataFrame(penalties).to_csv(meeting_dir / 's1_penalties.csv', index=False)
    meta_s1 = {'league': 'test', 'class_name': ['test']}
    with open(meeting_dir / 's1_metadata.json', 'w') as f:
        json.dump(meta_s1, f)

    db = race_db.RaceDB(base_dir=str(tmp_path), db_config={})
    # Should load now
    pens = db.load_penalties()
    assert not pens.empty


def test_overlapping_videos_with_correction(tmp_path: Path) -> None:
    meeting_dir = tmp_path / 'meeting'
    meeting_dir.mkdir()

    meta = {
        'league': 'test',
        'class_name': ['test'],
        'session_start_datetime': '2026-05-03 10:00',
        'session_id': 's1'
    }
    with open(meeting_dir / 's1_metadata.json', 'w') as f:
        json.dump(meta, f)

    pd.DataFrame({'Name': ['A'], 'Lap 1': ['1:00.000'], 'Pos': [1]}).to_csv(meeting_dir / 's1.csv', index=False)

    with open(meeting_dir / 'time_correction.json', 'w') as f:
        json.dump({'s1': 10.0}, f)

    video_data = {
        'v1.mp4': {
            'start': '2026-05-03T10:00:10+01:00',
            'end': '2026-05-03T10:01:00+01:00'
        }
    }
    with open(meeting_dir / 'video.json', 'w') as f:
        json.dump(video_data, f)

    db = race_db.RaceDB(base_dir=str(tmp_path), db_config={})
    sessions = db.find_sessions()
    df = db.load()
    videos = db.get_overlapping_videos(sessions[0], df)
    assert len(videos) > 0


def test_race_db_multi_class_filtering(tmp_path: Path) -> None:
    # Setup mock meeting with multiple classes in one session
    meeting_dir = tmp_path / 'meeting'
    meeting_dir.mkdir()

    meta = {
        'league': 'test_league',
        'class_name': ['class_a', 'class_b'],
        'track_name': 'test_track',
        'session_name': 'test_session',
        'session_start_datetime': '2026-05-02 11:00',
        'short_name': 'Practice 1'
    }
    with open(meeting_dir / 's1_metadata.json', 'w') as f:
        json.dump(meta, f)

    csv_data = {
        'Pos': [1, 2],
        'Name': ['Driver A', 'Driver B'],
        'Lap 1': ['1:00.000', '1:01.000']
    }
    pd.DataFrame(csv_data).to_csv(meeting_dir / 's1.csv', index=False)

    db = race_db.RaceDB(base_dir=str(tmp_path), db_config={})

    # 1. Test filtering by class_a
    df_a = db.load(classes='class_a')
    assert not df_a.empty
    assert (df_a['Class'] == 'class_a').all()
    assert 'class_b' not in df_a['Class'].values

    # 2. Test filtering by class_b
    df_b = db.load(classes='class_b')
    assert not df_b.empty
    assert (df_b['Class'] == 'class_b').all()
    assert 'class_a' not in df_b['Class'].values

    # 3. Test filtering by both (should yield unique laps, choosing the first matching class)
    df_both = db.load(classes=('class_a', 'class_b'))
    assert len(df_both) == 2
    assert (df_both['Class'] == 'class_a').all()

    # 4. Test no filter (should yield unique laps, choosing the first matching class)
    df_none = db.load()
    assert len(df_none) == 2
    assert (df_none['Class'] == 'class_a').all()

    # 5. Test find_sessions (should only return one object per session metadata file)
    sessions = db.find_sessions(classes=('class_a', 'class_b'))
    assert len(sessions) == 1
    assert set(sessions[0].class_name) == {'class_a', 'class_b'}


def test_check_file_metadata_logic(tmp_path: Path) -> None:
    # _check_file_metadata was an internal method that has been removed.
    # The equivalent filtering is now done at the DB query level via SQL WHERE clauses.
    # This test verifies that the DB-backed find_sessions correctly filters by class.
    meeting_dir = tmp_path / 'meeting'
    meeting_dir.mkdir()
    meta = {
        'class_name': ['Senior', 'Junior'],
        'track_conditions': 'Wet',
        'league': 'test'
    }
    with open(meeting_dir / 's1_metadata.json', 'w') as f:
        json.dump(meta, f)

    # Need a CSV for the session to be loadable
    pd.DataFrame({'Pos': [1], 'Name': ['A'], 'Lap 1': ['1:00.000']}).to_csv(
        meeting_dir / 's1.csv', index=False
    )

    db = race_db.RaceDB(base_dir=str(tmp_path), db_config={})

    # 1. Matches by class
    sessions = db.find_sessions(classes='Senior')
    assert len(sessions) == 1

    # 2. Fails if class not in metadata
    sessions = db.find_sessions(classes='Cadet')
    assert len(sessions) == 0

    # 3. Matches with track conditions filter
    sessions = db.find_sessions(track_conditions='Wet')
    assert len(sessions) == 1

    # 4. Fails if track conditions don't match
    sessions = db.find_sessions(track_conditions='Dry')
    assert len(sessions) == 0


def test_overlapping_videos_robust_assignment(tmp_path: Path) -> None:
    # Setup mock meeting
    meeting_dir = tmp_path / 'meeting'
    meeting_dir.mkdir()

    meta = {
        'league': 'test_league',
        'class_name': ['test_class'],
        'track_name': 'test_track',
        'session_name': 'test_session',
        'session_start_datetime': '2026-05-03 10:00',
        'session_id': 's1'
    }
    with open(meeting_dir / 's1_metadata.json', 'w') as f:
        json.dump(meta, f)

    # Two laps of 60 seconds
    csv_data = {
        'Pos': [1],
        'Name': ['Driver A'],
        'Lap 1': ['1:00.000'],
        'Lap 2': ['1:00.000']
    }
    pd.DataFrame(csv_data).to_csv(meeting_dir / 's1.csv', index=False)

    # Case 1: Video entirely within Lap 1
    # Lap 1: [10:00:00, 10:01:00]
    # Video 1: [10:00:10, 10:00:20]
    # Case 2: Video crossing Lap 1 and Lap 2
    # Video 2: [10:00:50, 10:01:10]
    video_data = {
        'video_within.mp4': {
            'start': '2026-05-03T10:00:10+01:00',
            'end': '2026-05-03T10:00:20+01:00'
        },
        'video_crossing.mp4': {
            'start': '2026-05-03T10:00:50+01:00',
            'end': '2026-05-03T10:01:10+01:00'
        }
    }
    with open(meeting_dir / 'video.json', 'w') as f:
        json.dump(video_data, f)

    db = race_db.RaceDB(base_dir=str(tmp_path), db_config={})
    sessions = db.find_sessions(leagues='test_league')
    df = db.load(leagues='test_league')

    videos = db.get_overlapping_videos(sessions[0], df)
    videos.sort(key=lambda v: v['filename'])

    assert len(videos) == 2

    # Check video_within.mp4
    v_within = next(v for v in videos if v['filename'] == 'video_within.mp4')
    assert len(v_within['markers']) == 1
    assert v_within['markers'][0]['lap'] == 1
    assert v_within['markers'][0]['video_time'] == 0.0

    # Check video_crossing.mp4
    v_cross = next(v for v in videos if v['filename'] == 'video_crossing.mp4')
    assert len(v_cross['markers']) == 2
    v_cross['markers'].sort(key=lambda m: m['lap'])
    assert v_cross['markers'][0]['lap'] == 1
    assert v_cross['markers'][0]['video_time'] == 0.0
    assert v_cross['markers'][1]['lap'] == 2
    assert v_cross['markers'][1]['video_time'] == 10.0


def test_get_driver_names() -> None:
    db = race_db.RaceDB(base_dir=TESTDATA_DIR, db_config={})
    names = db.get_driver_names()
    assert len(names) > 0
    # verify some driver name exists
    assert any(name != '' for name in names)


def test_find_sessions_driver_filter() -> None:
    db = race_db.RaceDB(base_dir=TESTDATA_DIR, db_config={})

    # 1. Find sessions with a specific driver
    names = db.get_driver_names()
    assert len(names) > 0
    driver = names[0]

    # Find sessions for this driver
    sessions = db.find_sessions(driver_names=driver)
    assert len(sessions) > 0

    # Ensure the driver is indeed in those sessions
    for s in sessions:
        csv_path = os.path.join(s.meeting_dir, f'{s.session_id}.csv')
        df = pd.read_csv(csv_path, usecols=['Name'])
        assert driver in df['Name'].values


def test_race_db_track_mappings(tmp_path: Path) -> None:
    # Set up subdirectories
    tracks_dir = tmp_path / 'tracks'
    tracks_dir.mkdir()

    # Create dummy track JSON files
    t1 = {
        'track_name': 'Rowrah Lakeland Circuit 2026',
        'aliases': ['Rowrah', 'Rowrah Lakeland']
    }
    with open(tracks_dir / 'rowrah_lakeland_circuit_2026.json', 'w') as f:
        json.dump(t1, f)

    t2 = {
        'track_name': 'Buckmore Park',
        'aliases': []
    }
    with open(tracks_dir / 'buckmore_park_2026.json', 'w') as f:
        json.dump(t2, f)

    # Create dummy GeoJSON file for Buckmore Park
    geojson_data = {
        'type': 'FeatureCollection',
        'features': []
    }
    with open(tracks_dir / 'buckmore_park_2026.geojson', 'w') as f:
        json.dump(geojson_data, f)

    # Initialize RaceDB
    db = race_db.RaceDB(base_dir=str(tmp_path), db_config={})

    # Test get_track_mappings
    mappings = db.get_track_mappings()
    assert mappings['rowrah'] == 'rowrah_lakeland_circuit_2026'
    assert mappings['rowrah lakeland'] == 'rowrah_lakeland_circuit_2026'
    assert mappings['buckmore park'] == 'buckmore_park_2026'

    # Test get_track with different aliases and names
    track1_by_alias = db.get_track('Rowrah')
    assert track1_by_alias is not None
    assert track1_by_alias['track_name'] == 'Rowrah Lakeland Circuit 2026'
    assert 'geojson' not in track1_by_alias

    track1_by_full_name = db.get_track('Rowrah Lakeland Circuit 2026')
    assert track1_by_full_name is not None
    assert track1_by_full_name['track_name'] == 'Rowrah Lakeland Circuit 2026'

    # Test get_track with GeoJSON loading
    track2 = db.get_track('Buckmore Park')
    assert track2 is not None
    assert track2['track_name'] == 'Buckmore Park'
    assert track2['geojson'] == geojson_data

    # Test get_track returning None for non-existent track
    assert db.get_track('Non-existent Track') is None


@patch('race_db.psycopg2.connect')
def test_has_telemetry_helper(mock_connect: MagicMock, tmp_path: Path) -> None:
    db = race_db.RaceDB(base_dir=str(tmp_path), db_config={})
    meeting_dir = tmp_path / "meeting"
    meeting_dir.mkdir()

    assert not db.has_telemetry(str(meeting_dir))

    telemetry_dir = meeting_dir / "telemetry"
    telemetry_dir.mkdir()
    assert not db.has_telemetry(str(meeting_dir))

    (telemetry_dir / "10_00_session_driver.csv").write_text("data")
    assert db.has_telemetry(str(meeting_dir))
    assert db.has_telemetry(str(meeting_dir), "10_00_session")
    assert not db.has_telemetry(str(meeting_dir), "11_00_session")
