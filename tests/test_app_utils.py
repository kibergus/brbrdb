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

from typing import Any, cast, Dict, List
import pandas as pd
from unittest.mock import patch, MagicMock
from flask import render_template
import aliases
import app
from race_tools import sanitize


def test_parse_time() -> None:
    assert sanitize.parse_time('10.500') == 10.5
    assert sanitize.parse_time('1:10.500') == 70.5
    assert sanitize.parse_time('1:22.24') == 82.24
    assert sanitize.parse_time(None) is None
    assert sanitize.parse_time('') is None
    assert sanitize.parse_time('invalid') is None


def test_calculate_gaps_final() -> None:
    drivers_info: list[dict[str, Any]] = [
        {'name': 'Leader', 'pos': '1', 'gap_raw': None, 'best_lap_raw': 60.0},
        {'name': 'Second', 'pos': '2', 'gap_raw': '0.500', 'best_lap_raw': 60.1},
        {'name': 'Third', 'pos': '3', 'gap_raw': '1.200', 'best_lap_raw': 60.2},
        {'name': 'Slow', 'pos': '4', 'gap_raw': '1:10.000', 'best_lap_raw': 70.0},
        {'name': 'DNF', 'pos': 'DNF', 'gap_raw': None, 'best_lap_raw': 60.5},
    ]
    app.calculate_gaps(drivers_info, is_final=True)

    assert drivers_info[0]['gap_leader'] == '-'
    assert drivers_info[0]['gap_next'] == '-'

    assert drivers_info[1]['gap_leader'] == ' 0.500'
    assert drivers_info[1]['gap_next'] == ' 0.500'

    assert drivers_info[2]['gap_leader'] == ' 1.200'
    assert drivers_info[2]['gap_next'] == ' 0.700'

    assert drivers_info[3]['gap_leader'] == '1:10.000'
    assert drivers_info[3]['gap_next'] == '1:08.800'

    assert drivers_info[4]['gap_leader'] == '-'
    assert drivers_info[4]['gap_next'] == '-'


def test_calculate_gaps_practice() -> None:
    drivers_info: list[dict[str, Any]] = [
        {'name': 'Leader', 'pos': '1', 'best_lap_raw': 60.0},
        {'name': 'Second', 'pos': '2', 'best_lap_raw': 60.5},
        {'name': 'Third', 'pos': '3', 'best_lap_raw': 61.2},
    ]
    app.calculate_gaps(drivers_info, is_final=False)

    assert drivers_info[0]['gap_leader'] == '-'
    assert drivers_info[0]['gap_next'] == '-'

    assert drivers_info[1]['gap_leader'] == ' 0.500'
    assert drivers_info[1]['gap_next'] == ' 0.500'

    assert drivers_info[2]['gap_leader'] == ' 1.200'
    assert drivers_info[2]['gap_next'] == ' 0.700'


def test_get_session_results_champpoints() -> None:
    df = pd.DataFrame([
        {'SessionID': 'sess_1', 'Name': 'Driver A', 'Pos': 1, 'ChampPoints': 25.0},
        {'SessionID': 'sess_1', 'Name': 'Driver B', 'Pos': 2, 'ChampPoints': 18.0},
    ])
    results = app._get_session_results(df, 'sess_1')
    assert results['Driver A']['points'] == '25'
    assert results['Driver A']['pos'] == '1'
    assert results['Driver A']['preliminary'] is True
    assert results['Driver B']['points'] == '18'
    assert results['Driver B']['pos'] == '2'


def test_get_session_results_cpts() -> None:
    df = pd.DataFrame([
        {'SessionID': 'sess_1', 'Name': 'Driver A', 'Pos': 1, 'CPts': 25.0},
        {'SessionID': 'sess_1', 'Name': 'Driver B', 'Pos': 2, 'CPts': 18.0},
    ])
    results = app._get_session_results(df, 'sess_1')
    assert results['Driver A']['points'] == '25'
    assert results['Driver B']['points'] == '18'


def test_augment_championship_data_filtering() -> None:
    with patch('app.db') as mock_db:
        mock_db.list_meetings.return_value = [
            ('cadet', '2026-04-04', 'Shenington'),  # Round 1 meeting
            ('cadet', '2026-05-02', 'Dunkeswell'),  # Round 2 meeting
            ('cadet', '2026-05-03', 'Dunkeswell'),  # practice only (no finals)
            ('cadet', '2026-05-23', 'Rowrah'),      # Round 3 meeting
        ]

        # Mock find_sessions for each date
        session_pf_shenington = MagicMock(session_id='sess_pf_shenington')
        session_pf_shenington.session_name = 'Pre-Final'
        session_f_shenington = MagicMock(session_id='sess_f_shenington')
        session_f_shenington.session_name = 'Final'

        session_pf_dunkeswell = MagicMock(session_id='sess_pf_dunkeswell')
        session_pf_dunkeswell.session_name = 'Pre-Final'
        session_f_dunkeswell = MagicMock(session_id='sess_f_dunkeswell')
        session_f_dunkeswell.session_name = 'Final'

        session_f_rowrah = MagicMock(session_id='sess_f_rowrah')
        session_f_rowrah.session_name = 'Final'

        def find_sessions_side_effect(leagues: Any, classes: Any, date: str, track: Any) -> list[MagicMock]:
            if date == '2026-04-04':
                return [session_pf_shenington, session_f_shenington]
            elif date == '2026-05-02':
                return [session_pf_dunkeswell, session_f_dunkeswell]
            elif date == '2026-05-03':
                # Practice only, no pf or f
                p_sess = MagicMock(session_id='sess_prac')
                p_sess.session_name = 'Practice 1'
                return [p_sess]
            elif date == '2026-05-23':
                return [session_f_rowrah]
            return []

        mock_db.find_sessions.side_effect = find_sessions_side_effect

        # Mock db.load to return session data
        df_rowrah = pd.DataFrame([
            {'SessionID': 'sess_f_rowrah', 'Name': 'Driver A', 'Pos': 1, 'ChampPoints': 25.0},
            {'SessionID': 'sess_f_rowrah', 'Name': 'Driver B', 'Pos': 2, 'ChampPoints': 18.0},
        ])

        def load_side_effect(leagues: Any, classes: Any, date: str, track: Any) -> pd.DataFrame:
            if date == '2026-05-23':
                return df_rowrah
            return pd.DataFrame()

        mock_db.load.side_effect = load_side_effect

        # Create dummy championship data
        # R1 is filled, R2 is filled, R3 is empty
        championship = {
            'standings': [
                {
                    'name': 'Driver A',
                    'total_all_rounds': '43',
                    'rounds': {
                        'R1': {'PF': {'points': '8', 'pos': '1'}, 'F': {'points': '25', 'pos': '1'}},
                        'R2': {'PF': {'points': '10', 'pos': '1'}},
                        'R3': {'PF': {'points': '', 'pos': ''}, 'F': {'points': '', 'pos': ''}}
                    }
                },
                {
                    'name': 'Driver B',
                    'total_all_rounds': '18',
                    'rounds': {
                        'R1': {'PF': {'points': '0', 'pos': ''}, 'F': {'points': '18', 'pos': '2'}},
                        'R2': {'PF': {'points': '0', 'pos': ''}},
                        'R3': {'PF': {'points': '', 'pos': ''}, 'F': {'points': '', 'pos': ''}}
                    }
                }
            ]
        }

        app.augment_championship_data(championship, 'fat_pro', 'cadet', '2026')

        # Driver A should get 25 points in R3 F
        # Driver B should get 18 points in R3 F
        standings = cast(List[Dict[str, Any]], championship['standings'])
        driver_a_rounds = standings[0]['rounds']
        driver_b_rounds = standings[1]['rounds']

        assert driver_a_rounds['R3']['F']['points'] == '25'
        assert driver_a_rounds['R3']['F']['pos'] == '1'
        assert driver_b_rounds['R3']['F']['points'] == '18'
        assert driver_b_rounds['R3']['F']['pos'] == '2'


def test_augment_championship_data_all_drivers_and_provisional() -> None:
    with patch('app.db') as mock_db:
        mock_db.list_meetings.return_value = [
            ('cadet', '2026-04-10', 'Track A'),
            ('cadet', '2026-05-23', 'Track B')
        ]

        def find_sessions_side_effect(leagues: Any, classes: Any, date: str, track: Any) -> list:
            if date in ('2026-04-10', '2026-05-23'):
                session_f = MagicMock(session_id=f'sess_f_{date}')
                session_f.session_name = 'Final'
                session_f.track_conditions = 'Dry'
                return [session_f]
            return []
        mock_db.find_sessions.side_effect = find_sessions_side_effect

        def load_side_effect(leagues: Any, classes: Any, date: str, track: Any) -> pd.DataFrame:
            if date == '2026-05-23':
                return pd.DataFrame([
                    {'SessionID': 'sess_f_2026-05-23', 'Name': 'Driver A', 'Pos': 1, 'ChampPoints': 25.0},
                ])
            return pd.DataFrame()
        mock_db.load.side_effect = load_side_effect

        championship = {
            'standings': [
                {
                    'name': 'Driver A',
                    'pts': '33',
                    'total': '33',
                    'rounds': {
                        'R1': {'PF': {'points': '8', 'pos': '1'}, 'F': {'points': '25', 'pos': '1'}},
                        'R2': {'PF': {'points': '', 'pos': ''}, 'F': {'points': '', 'pos': ''}}
                    }
                },
                {
                    'name': 'Driver B',
                    'pts': '18',
                    'total': '18',
                    'rounds': {
                        'R1': {'PF': {'points': '0', 'pos': ''}, 'F': {'points': '18', 'pos': '2'}},
                        'R2': {'PF': {'points': '', 'pos': ''}, 'F': {'points': '', 'pos': ''}}
                    }
                }
            ]
        }

        app.augment_championship_data(championship, 'fat_pro', 'cadet', '2026')

        standings = cast(List[Dict[str, Any]], championship['standings'])

        assert standings[0]['name'] == 'Driver A'
        assert standings[0]['total_all_rounds'] == '58'
        assert standings[0]['total_all_rounds_official'] == '33'
        assert standings[0]['total_to_count'] == '33'

        assert standings[1]['name'] == 'Driver B'
        assert standings[1]['total_all_rounds'] == '18'
        assert standings[1]['total_all_rounds_official'] == '18'
        assert standings[1]['total_to_count'] == '18'


def test_session_breadcrumbs() -> None:
    with app.app.test_request_context():
        # Case 1: Session name has class name prefix to be stripped
        metadata_dup = {
            'league': 'fat_pro',
            'class': 'cadet',
            'date': '2026-05-24',
            'track': 'Rowrah',
            'session_name': 'Cadet / Cadet Light Practice 2',
            'session_id': '09_55_practice_2',
            'is_final': False,
        }

        html_dup = render_template(
            'session.html',
            metadata=metadata_dup,
            drivers=[],
            lap_data={},
            penalties=[],
            overlapping_videos=[],
            prev_session=None,
            next_session=None
        )

        # Verify "Cadet Light Practice 2" is in the output and there's no duplicate "Cadet"
        assert 'Cadet Light Practice 2' in html_dup
        assert 'Rowrah</a> / Cadet' not in html_dup

        # Case 2: Session name does not have class name prefix
        metadata_no_dup = {
            'league': 'fat_pro',
            'class': 'cadet',
            'date': '2026-05-24',
            'track': 'Rowrah',
            'session_name': 'Practice 2',
            'session_id': '09_55_practice_2',
            'is_final': False,
        }

        html_no_dup = render_template(
            'session.html',
            metadata=metadata_no_dup,
            drivers=[],
            lap_data={},
            penalties=[],
            overlapping_videos=[],
            prev_session=None,
            next_session=None
        )
        assert 'Practice 2' in html_no_dup

        # Case 3: Session name has a non-matching class prefix
        metadata_other = {
            'league': 'fat_pro',
            'class': 'cadet',
            'date': '2026-05-24',
            'track': 'Rowrah',
            'session_name': 'Senior / Practice 2',
            'session_id': '09_55_practice_2',
            'is_final': False,
        }

        html_other = render_template(
            'session.html',
            metadata=metadata_other,
            drivers=[],
            lap_data={},
            penalties=[],
            overlapping_videos=[],
            prev_session=None,
            next_session=None
        )
        assert 'Senior' in html_other
        assert 'Practice 2' in html_other


def test_resolve_car_name() -> None:
    assert aliases.resolve_car_name('25_KSP_e10_mini') == 'rotax_e10_mini'
    assert aliases.get_class_name('rotax_e10_mini') == 'Rotax E10 Mini'
    assert aliases.get_class_info('rotax_e10_mini').class_type == 'cadet'

    assert aliases.resolve_car_name('25_KSP_Mini_Max') == 'rotax_mini_max'
    assert aliases.get_class_name('rotax_mini_max') == 'Rotax Mini Max'
    assert aliases.get_class_info('rotax_mini_max').class_type == 'cadet'

    assert aliases.resolve_car_name('25_KSP_e10_Bambini') == 'rotax_e10_bambino'
    assert aliases.get_class_name('rotax_e10_bambino') == 'Rotax E10 Bambino'
    assert aliases.get_class_info('rotax_e10_bambino').class_type == 'bambino'

    assert aliases.resolve_car_name('25_KSP_e20') == 'rotax_e20'
    assert aliases.get_class_name('rotax_e20') == 'Rotax E20'
    assert aliases.get_class_info('rotax_e20').class_type is None

    assert aliases.resolve_car_name('25_Ksp_Junior_Max') == 'rotax_junior_max'
    assert aliases.get_class_name('rotax_junior_max') == 'Rotax Junior Max'
    assert aliases.get_class_info('rotax_junior_max').class_type == 'junior'

    assert aliases.resolve_car_name('25_Ksp_Senior_Max') == 'rotax_senior_max'
    assert aliases.get_class_name('rotax_senior_max') == 'Rotax Senior Max'
    assert aliases.get_class_info('rotax_senior_max').class_type is None

    assert aliases.resolve_car_name('25_ksp_Iame_Bambino') == 'iame_bambino'
    assert aliases.get_class_name('iame_bambino') == 'IAME Bambino'
    assert aliases.get_class_info('iame_bambino').class_type == 'bambino'

    assert aliases.resolve_car_name('KSP_GX200') == 'honda_gx200'
    assert aliases.get_class_name('honda_gx200') == 'Honda GX200'
    assert aliases.get_class_info('honda_gx200').class_type == 'cadet'

    assert aliases.resolve_car_name('KSP_Iame_Junior_X30') == 'x30_junior'
    assert aliases.get_class_name('x30_junior') == 'X30 Junior'
    assert aliases.get_class_info('x30_junior').class_type == 'junior'

    assert aliases.resolve_car_name('KSP_Iame_Senior_X30') == 'x30_senior'
    assert aliases.get_class_name('x30_senior') == 'X30 Senior'
    assert aliases.get_class_info('x30_senior').class_type is None

    assert aliases.get_class_name('sprint_lw') == 'Sprint Lightweight'
    assert aliases.get_class_info('sprint_lw').class_type is None

    assert aliases.get_class_name('sprint_middleweight') == 'Sprint Middleweight'
    assert aliases.get_class_info('sprint_middleweight').class_type is None

    assert aliases.get_class_name('experience_lw') == 'Experience Lightweight'
    assert aliases.get_class_info('experience_lw').class_type is None

    assert aliases.get_class_name('experience_junior_lw') == 'Experience Junior Lightweight'
    assert aliases.get_class_info('experience_junior_lw').class_type == 'junior'

    assert aliases.get_class_name('experience_junior_slw') == 'Experience Junior Super Lightweight'
    assert aliases.get_class_info('experience_junior_slw').class_type == 'junior'


def test_resolve_track_name() -> None:
    assert aliases.resolve_track_name('Dunkeswell 2026') == 'Dunkeswell'
    assert aliases.resolve_track_name('Bayford Meadows 2026') == 'Bayford Meadows'
    assert aliases.resolve_track_name('Fulbeck Kart Club 2026') == 'Fulbeck'
    assert aliases.resolve_track_name('Lydd Karting 2026') == 'Lydd'
    assert aliases.resolve_track_name('South Wales Karting Centre 2026') == 'Llandow'
    assert aliases.resolve_track_name('Larkhall Pro 2026') == 'Larkhall'
    assert aliases.resolve_track_name('Kimbolton Circuit 1 2026') == 'Kimbolton'

    # Generic cases (different years)
    assert aliases.resolve_track_name('Dunkeswell 2023') == 'Dunkeswell'
    assert aliases.resolve_track_name('Fulbeck Kart Club 2024') == 'Fulbeck'

    # Without year
    assert aliases.resolve_track_name('Rissington Kart Club') == 'Rissington'
    assert aliases.resolve_track_name('Larkhall Pro') == 'Larkhall'


def test_augment_championship_data_drop_rounds() -> None:
    with patch('app.db') as mock_db:
        # Mock list_meetings to indicate that R1, R2, R3 have occurred (past rounds)
        # R4 is in the future.
        mock_db.list_meetings.return_value = [
            ('cadet', '2026-04-10', 'Track A'),
            ('cadet', '2026-05-23', 'Track B'),
            ('cadet', '2026-06-15', 'Track C'),
        ]

        def find_sessions_side_effect(leagues: Any, classes: Any, date: str, track: Any) -> list:
            session_f = MagicMock(session_id=f'sess_f_{date}')
            session_f.session_name = 'Final'
            return [session_f]
        mock_db.find_sessions.side_effect = find_sessions_side_effect
        mock_db.load.return_value = pd.DataFrame(columns=['SessionID', 'Name', 'Pos', 'ChampPoints'])

        championship = {
            'drop_rounds': 2,
            'standings': [
                {
                    'name': 'Driver A',
                    'pts': '60',
                    'total': '60',
                    'rounds': {
                        # R1: 8 + 25 + 1 = 34
                        'R1': {'PF': {'points': '8'}, 'F': {'points': '25'}, 'BL': {'points': '1'}},
                        # R2: 6 + 18 = 24
                        'R2': {'PF': {'points': '6'}, 'F': {'points': '18'}},
                        # R3: Missed (past round, score 0)
                        'R3': {'PF': {'points': ''}, 'F': {'points': ''}},
                        # R4: Future round, score 0. Should NOT be dropped because it's in the future.
                        'R4': {'PF': {'points': ''}, 'F': {'points': ''}},
                    }
                }
            ]
        }

        app.augment_championship_data(championship, 'fat_pro', 'cadet', '2026')

        standings = cast(List[Dict[str, Any]], championship['standings'])
        driver_a = standings[0]

        # Verify past rounds list
        past_rounds_list = championship.get('past_rounds')
        assert isinstance(past_rounds_list, list)
        assert set(past_rounds_list) == {'R1', 'R2', 'R3'}

        # Verify round totals
        assert driver_a['round_totals']['R1'] == 34
        assert driver_a['round_totals']['R2'] == 24
        assert driver_a['round_totals']['R3'] == 0
        assert driver_a['round_totals']['R4'] == 0

        # Verify drop rounds: least 2 from past rounds {R1: 34, R2: 24, R3: 0}
        # The least 2 are R3 (0) and R2 (24).
        drop_rounds_list = driver_a.get('drop_rounds')
        assert isinstance(drop_rounds_list, list)
        assert set(drop_rounds_list) == {'R3', 'R2'}


def test_kartsim_class_view_no_hero() -> None:
    client = app.app.test_client()
    with patch('plot_handlers.get_hero_names', return_value=[]):
        resp = client.get('/league/kartsim/iame_waterswift_restricted_cadet_uk')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Driver Selection Required' in html
        assert 'Back to Leagues' in html


def test_kartsim_class_view_hero_no_sessions() -> None:
    client = app.app.test_client()
    with patch('plot_handlers.get_hero_names', return_value=['Test Driver']):
        with patch('app.db.load', return_value=pd.DataFrame()):
            resp = client.get('/league/kartsim/iame_waterswift_restricted_cadet_uk')
            assert resp.status_code == 200
            html = resp.get_data(as_text=True)
            assert 'Driver Selection Required' in html


def test_kartsim_league_view_no_hero() -> None:
    client = app.app.test_client()
    with patch('plot_handlers.get_hero_names', return_value=[]):
        resp = client.get('/league/kartsim')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Driver Selection Required' in html


def test_kartsim_track_sessions_view_no_hero() -> None:
    client = app.app.test_client()
    with patch('plot_handlers.get_hero_names', return_value=[]):
        resp = client.get('/track_sessions/kartsim/iame_waterswift_restricted_cadet_uk/Llandow/Dry')
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert 'Driver Selection Required' in html


def test_league_helpers() -> None:
    assert aliases.get_league_name('fat_us_tx') == 'FAT US TX'
    assert aliases.get_league_name('fat_de') == 'FAT DE'
    assert aliases.get_league_name('fat_us_ca') == 'FAT US CA'
    assert aliases.get_league_name('fat_us_mw') == 'FAT US MW'
    assert aliases.get_league_name('club100') == 'Club 100'

    assert aliases.get_league_color('fat_us_tx') == '#1D4296'
    assert aliases.get_league_color('fat_de') == '#1D4296'
    assert aliases.get_league_color('club100') == '#E31E24'

    assert aliases.get_league_group('fat_us_tx') == 'fat_us'
    assert aliases.get_league_group('fat_us_ca') == 'fat_us'
    assert aliases.get_league_group('fat_us_mw') == 'fat_us'
    assert aliases.get_league_group('fat_de') == 'fat_other'
    assert aliases.get_league_group('fat_pro') == 'fat'
    assert aliases.get_league_group('fat_world_finals') == 'fat_other'
    assert aliases.get_league_group('club100_north') == 'club100'
    assert aliases.get_league_group('kartsim') == 'kartsim'
    assert aliases.get_league_group('fekc') == 'other'

    grouped = aliases.group_leagues(['fat_us_tx', 'fat_de', 'club100', 'fekc'])
    group_map = dict(grouped)
    assert 'fat_us' in group_map
    assert 'fat_us_tx' in group_map['fat_us']
    assert 'fat_other' in group_map
    assert 'fat_de' in group_map['fat_other']
    assert 'club100' in group_map
    assert 'other' in group_map
