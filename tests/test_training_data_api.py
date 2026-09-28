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

from typing import Any
import pandas as pd
from unittest.mock import patch
from flask import Flask
from plot_handlers import plots_blueprint


def test_training_data_api_no_heroes() -> None:
    app = Flask(__name__)
    app.register_blueprint(plots_blueprint)
    client = app.test_client()

    with patch('plot_handlers.get_hero_names', return_value=[]):
        response = client.get('/api/training_data/kartsim/cadet')
        assert response.status_code == 400
        assert response.get_json() == {'error': 'No hero drivers selected'}


def test_training_data_api_empty_db() -> None:
    app = Flask(__name__)
    app.register_blueprint(plots_blueprint)
    client = app.test_client()

    with patch('plot_handlers.get_hero_names', return_value=['Hero A']):
        with patch('plot_handlers.db.load', return_value=pd.DataFrame()):
            response = client.get('/api/training_data/kartsim/cadet')
            assert response.status_code == 200
            assert response.get_json() == {'daily': [], 'weekly': []}


def test_training_data_api_success() -> None:
    app = Flask(__name__)
    app.register_blueprint(plots_blueprint)
    client = app.test_client()

    # Create mock dataframe
    df = pd.DataFrame([
        {'Date': '2026-06-18', 'Name': 'Hero A', 'LapTimeSeconds': 1200.0},
        {'Date': '2026-06-18', 'Name': 'Hero B', 'LapTimeSeconds': 2400.0},
        {'Date': '2026-06-19', 'Name': 'Hero A', 'LapTimeSeconds': 1500.0},
    ])

    with patch('plot_handlers.get_hero_names', return_value=['Hero A', 'Hero B']):
        with patch('plot_handlers.db.load', return_value=df):
            response = client.get('/api/training_data/kartsim/cadet?year=2026')
            assert response.status_code == 200
            data: Any = response.get_json()

            # Check daily
            daily = data['daily']
            assert len(daily) == 2

            # 2026-06-18
            assert daily[0]['date'] == '2026-06-18'
            assert daily[0]['total'] == 3600.0
            assert daily[0]['drivers'] == {'Hero A': 1200.0, 'Hero B': 2400.0}

            # 2026-06-19
            assert daily[1]['date'] == '2026-06-19'
            assert daily[1]['total'] == 1500.0
            assert daily[1]['drivers'] == {'Hero A': 1500.0}

            # Check weekly
            weekly = data['weekly']
            assert len(weekly) == 1
            # June 18/19 2026 is Thursday/Friday, so the week starts on June 15, 2026 (Monday)
            assert weekly[0]['week'] == '2026-06-15'
            assert weekly[0]['total'] == 5100.0
            assert weekly[0]['drivers'] == {'Hero A': 2700.0, 'Hero B': 2400.0}


def test_training_data_api_all_classes() -> None:
    app = Flask(__name__)
    app.register_blueprint(plots_blueprint)
    client = app.test_client()

    df = pd.DataFrame([
        {'Date': '2026-06-18', 'Name': 'Hero A', 'LapTimeSeconds': 1000.0},
    ])

    with patch('plot_handlers.get_hero_names', return_value=['Hero A']):
        with patch('plot_handlers.db.load', return_value=df) as mock_load:
            response = client.get('/api/training_data/kartsim?year=2026')
            assert response.status_code == 200
            data: Any = response.get_json()
            assert len(data['daily']) == 1
            mock_load.assert_called_once_with(leagues='kartsim', classes=None, year='2026', driver_names=('Hero A',))
