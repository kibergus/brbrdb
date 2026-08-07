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

import pytest
import os

from race_db import RaceDB, check_for_dir_traversal

TESTDATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'testdata', 'race_db')


def test_check_for_dir_traversal_valid() -> None:
    # Should not raise
    check_for_dir_traversal(['valid_league'])
    check_for_dir_traversal([['league1', 'league2']])
    check_for_dir_traversal([('league1', 'league2')])
    check_for_dir_traversal([None])
    check_for_dir_traversal(['val1', 'val2', ['val3']])


def test_check_for_dir_traversal_invalid() -> None:
    with pytest.raises(ValueError, match='Security error'):
        check_for_dir_traversal(['../hidden'])
    with pytest.raises(ValueError, match='Security error'):
        check_for_dir_traversal(['/etc/passwd'])
    with pytest.raises(ValueError, match='Security error'):
        check_for_dir_traversal([['valid', '../invalid']])
    with pytest.raises(ValueError, match='Security error'):
        check_for_dir_traversal(['val1', 'val2', '../val3'])


def test_check_path_is_safe() -> None:
    db = RaceDB(base_dir=TESTDATA_DIR, db_config={})
    safe_path = os.path.join(db.base_dir, 'foo', 'bar.csv')
    unsafe_path = os.path.join(os.path.dirname(db.base_dir), 'secrets.txt')

    assert db._check_path_is_safe(safe_path) is True
    # Base dir itself is safe
    assert db._check_path_is_safe(db.base_dir) is True
    # Parent of base dir is NOT safe
    assert db._check_path_is_safe(os.path.dirname(db.base_dir)) is False
    assert db._check_path_is_safe(unsafe_path) is False


def test_load_injection_attempts() -> None:
    db = RaceDB(base_dir=TESTDATA_DIR, db_config={})

    # These should all raise ValueError now
    with pytest.raises(ValueError, match='Security error'):
        db.load(leagues='../etc/passwd')

    with pytest.raises(ValueError, match='Security error'):
        db.load(date='/absolute/path')

    with pytest.raises(ValueError, match='Security error'):
        db.find_sessions(track='..\\windows\\style')


def test_realpath_normalization() -> None:
    # If base_dir has '..', it should be normalized
    db = RaceDB(base_dir=os.path.join(TESTDATA_DIR, '..', 'race_db'), db_config={})
    # realpath should have resolved the '..'
    assert '..' not in db.base_dir
    assert db.base_dir == os.path.realpath(TESTDATA_DIR)
