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

import sys
import os
import shutil
import subprocess
import socket
import time
import psycopg2
from typing import Any, Generator
import pytest
import webbrowser
import json
import glob

# Add the project root to sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

import race_db  # noqa: E402
from utils import populate_db  # noqa: E402
from tests.goldens_diff.golden_app import app as golden_app  # noqa: E402


def find_free_port() -> int:
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return int(port)


@pytest.fixture(scope='session')
def ephemeral_postgres() -> Generator[dict[str, Any], None, None]:
    """Starts an ephemeral Postgres server in user space for testing."""
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    temp_dir = os.path.join(tests_dir, 'tmp_postgres')
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)

    data_dir = os.path.join(temp_dir, 'data')
    port = find_free_port()

    initdb_bin = '/usr/lib/postgresql/18/bin/initdb'
    pg_ctl_bin = '/usr/lib/postgresql/18/bin/pg_ctl'

    # Run initdb
    subprocess.run(
        [initdb_bin, '-D', data_dir, '-U', 'postgres', '--auth-local=trust', '--auth-host=trust'],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # Start Postgres using data_dir as socket path to avoid /var/run/postgresql permission issues
    subprocess.run(
        [pg_ctl_bin, '-D', data_dir, '-o', f'-p {port} -h 127.0.0.1 -k {data_dir}', 'start'],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # Wait for ready
    ready = False
    for _ in range(40):
        res = subprocess.run(['pg_isready', '-h', '127.0.0.1', '-p', str(port)], capture_output=True)
        if res.returncode == 0:
            ready = True
            break
        time.sleep(0.2)

    if not ready:
        subprocess.run(
            [pg_ctl_bin, '-D', data_dir, 'stop', '-m', 'immediate'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        shutil.rmtree(temp_dir)
        raise RuntimeError('Failed to start ephemeral Postgres server.')

    db_config = {
        'host': '127.0.0.1',
        'port': port,
        'dbname': 'postgres',
        'user': 'postgres',
        'password': ''
    }

    yield db_config

    # Stop server and clean up
    subprocess.run(
        [pg_ctl_bin, '-D', data_dir, 'stop', '-m', 'immediate'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(0.5)
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)


@pytest.fixture(autouse=True)
def mock_race_db_connection(ephemeral_postgres: dict[str, Any], monkeypatch: Any) -> None:
    """Automatically redirects all RaceDB constructor database connectivity to the ephemeral postgres instance."""
    original_init = race_db.RaceDB.__init__

    def patched_init(self: Any, base_dir: str, db_config: dict[str, Any]) -> None:

        # Verify base_dir exists
        real_base = os.path.realpath(os.path.expanduser(base_dir))
        if not os.path.isdir(real_base):
            raise NotADirectoryError(f'Data directory not found: {base_dir}')

        # Check for safety / directory traversal on metadata files and validate JSON decoding
        pattern = os.path.join(real_base, '**', '*_metadata.json')
        for path in glob.glob(pattern, recursive=True):
            real_path = os.path.realpath(path)
            if os.path.commonpath([real_base, real_path]) != real_base:
                raise ValueError(f'Found metadata file outside base directory: {path}')
            with open(path, 'r', encoding='utf-8') as f:
                json.load(f)

        db_config_override = ephemeral_postgres

        # Terminate any other active connections to avoid lock-induced hangs
        conn = psycopg2.connect(**db_config_override)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND pid <> pg_backend_pid();
            """)
        conn.close()

        # Populate the ephemeral database with data in base_dir
        conn = psycopg2.connect(**db_config_override)
        populate_db.create_schema(conn, drop_first=True)
        if os.path.isdir(base_dir):
            populate_db.populate_database(base_dir, conn)
        conn.close()

        # Call the original constructor with the overridden ephemeral db_config
        original_init(self, base_dir, db_config_override)

    monkeypatch.setattr(race_db.RaceDB, '__init__', patched_init)


# Global list to collect failed golden tests
failed_goldens: list[str] = []


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: Any, call: Any) -> Generator[None, Any, None]:
    """Collect test execution reports to check for golden test failures."""
    outcome = yield
    rep = outcome.get_result()

    # We only care about test execution failures (call phase) in test_goldens.py
    if rep.when == 'call' and rep.failed:
        if 'test_goldens.py' in str(item.fspath):
            failed_goldens.append(item.nodeid)


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    """Trigger the visual regression dashboard if any golden tests failed."""
    if failed_goldens:
        print('\n' + '=' * 80)
        print('                 VISUAL REGRESSION FAILURE DETECTED')
        print('=' * 80)
        print('The following golden plot verification tests failed:')
        for nodeid in failed_goldens:
            print(f'  - {nodeid}')
        print('\nLaunching the Golden Plot Verification Dashboard on http://127.0.0.1:5002/ ...')
        print('Please visit the page to review the 3-panel diff and accept changes.')
        print('Press Ctrl+C in this terminal to stop the server once you are done.')
        print('=' * 80 + '\n')

        # Open the default web browser to the dashboard
        try:
            webbrowser.open('http://127.0.0.1:5002/')
        except Exception as e:
            print(f'Note: Could not open browser automatically: {e}')

        # Start the standalone golden tests server in blocking mode
        try:
            # Disable Werkzeug reloader/debugger to keep it clean in the pytest terminal
            golden_app.run(port=5002, debug=False, use_reloader=False)
        except KeyboardInterrupt:
            print('\nStopping Golden Plots Verification server. Exiting.')
        except Exception as e:
            print(f'Error starting the verification server: {e}')
