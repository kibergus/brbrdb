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
import functools
from typing import Any
from flask import request, has_request_context

_KEYS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'keys.json')
AUTH_COOKIE = 'auth_key'
_LOCALHOST_ADDRS = {'127.0.0.1', '::1'}


@functools.lru_cache(maxsize=1)
def load_keys() -> dict[str, dict[str, Any]]:
    """Load valid auth keys from keys.json."""
    with open(_KEYS_PATH, 'r') as f:
        return json.load(f)


def get_current_key() -> str | None:
    """Return the raw authentication key provided in the request, or None."""
    # Support multiple key sources to accommodate different clients:
    # 1. 'X-API-Key': Custom header used by existing API routes.
    # 2. 'key' query param: Required for SSE (EventSource) connections without custom header support.
    # 3. AUTH_COOKIE: Web browser session cookie.
    key = request.headers.get('X-API-Key') or request.args.get('key') or request.cookies.get(AUTH_COOKIE)

    # 4. 'Authorization: Bearer <key>': Standard HTTP authorization header used by REST clients & LLM frameworks.
    if not key and request.headers.get('Authorization', '').startswith('Bearer '):
        key = request.headers.get('Authorization', '')[7:].strip()

    return key.strip() if (key and key.strip()) else None


def get_current_acl() -> dict[str, Any]:
    """Return the ACL configuration for the current authenticated key, localhost, or anonymous."""
    key = get_current_key()
    valid_keys = load_keys()

    if key:
        # Explicit key provided: must exist in keys.json
        if key in valid_keys:
            return valid_keys[key]
        # Invalid key supplied -> unauthorized
        return {}

    if request.remote_addr in _LOCALHOST_ADDRS:
        # Default all permissions for local development
        return {
            'see_videos': True,
            'see_gallery': True,
            'see_telemetry': {
                'leagues': ['*']
            },
            'see_drivers': True,
            'see_leagues': ['*'],
            'upload_sessions': {
                'drivers': ['*'],
                'leagues': ['*']
            },
            'manage_reports': True
        }

    # Fallback to anonymous permissions if configured in keys.json under empty key
    if '' in valid_keys:
        return valid_keys['']

    return {}


def can_see_league(acl: dict[str, Any], league: str) -> bool:
    """Check if the ACL allows viewing data for the given league."""
    see_leagues = acl.get('see_leagues')
    if not see_leagues or not isinstance(see_leagues, list):
        return False
    return '*' in see_leagues or league in see_leagues


def can_see_telemetry(
    acl: dict[str, Any],
    league: str | None = None,
    driver: str | None = None
) -> bool:
    """Check if the ACL allows viewing telemetry data.

    `see_telemetry` follows the same format as leagues/upload_sessions:
    with a list of allowed drivers and leagues:
    {
        "drivers": [...],
        "leagues": [...]
    }
    Absence of 'driver' / 'drivers' is treated as '*'.
    """
    see_telemetry = acl.get('see_telemetry')
    if not see_telemetry or see_telemetry is False:
        return False

    if see_telemetry is True:
        allowed_leagues = ['*']
        allowed_drivers = ['*']
    elif isinstance(see_telemetry, list):
        allowed_leagues = see_telemetry
        allowed_drivers = ['*']
    elif isinstance(see_telemetry, dict):
        # Leagues
        raw_leagues = see_telemetry.get('leagues') or see_telemetry.get('league') or []
        if isinstance(raw_leagues, str):
            allowed_leagues = [raw_leagues]
        elif isinstance(raw_leagues, list):
            allowed_leagues = list(raw_leagues)
        else:
            allowed_leagues = []

        # Drivers: treat absence of 'driver' / 'drivers' as '*'
        if 'drivers' in see_telemetry:
            raw_drivers = see_telemetry['drivers']
        elif 'driver' in see_telemetry:
            raw_drivers = see_telemetry['driver']
        else:
            raw_drivers = ['*']

        if isinstance(raw_drivers, str):
            allowed_drivers = [raw_drivers]
        elif isinstance(raw_drivers, list):
            allowed_drivers = list(raw_drivers)
        else:
            allowed_drivers = ['*']
    else:
        return False

    if not allowed_leagues:
        return False

    if league is not None:
        if '*' not in allowed_leagues and league not in allowed_leagues:
            return False

    if driver is not None:
        if '*' not in allowed_drivers and driver not in allowed_drivers:
            return False

    return True


def can_upload_session_for_driver(acl: dict[str, Any], driver_name: str) -> bool:
    """Check if the ACL allows session uploads for the given driver."""
    upload_sessions = acl.get('upload_sessions')
    if not upload_sessions:
        return False
    drivers = upload_sessions.get('drivers', [])
    return '*' in drivers or driver_name in drivers


def can_upload_session_for_league(acl: dict[str, Any], league: str) -> bool:
    """Check if the ACL allows session uploads for the given league."""
    upload_sessions = acl.get('upload_sessions')
    if not upload_sessions:
        return False
    leagues = upload_sessions.get('leagues', [])
    return '*' in leagues or league in leagues


def can_manage_reports(acl: dict[str, Any]) -> bool:
    """Check if the ACL allows managing (creating, editing) reports."""
    return acl.get('manage_reports', False) is True


def get_current_author() -> tuple[str, str]:
    """Return (author_key, author_name) for the current caller."""
    valid_keys = load_keys()
    if has_request_context():
        key = get_current_key()
        if key and key in valid_keys:
            return key, valid_keys[key].get('comment', key)
        if request.remote_addr in _LOCALHOST_ADDRS:
            for k, val in valid_keys.items():
                if val.get('comment') == 'Kibergus':
                    return k, 'Kibergus'
            return 'localhost', 'Localhost'
        if '' in valid_keys:
            return '', valid_keys[''].get('comment', 'Anonymous')
        return 'anonymous', 'Anonymous'

    # Outside request context (CLI / direct tool call / unit test)
    for k, val in valid_keys.items():
        if val.get('comment') == 'Kibergus':
            return k, 'Kibergus'
    return 'localhost', 'Localhost'
