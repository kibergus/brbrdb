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

"""Utilities for parsing, converting, and preserving session start datetimes.

Supports:
- Naive track-local formats: 'YYYY-MM-DD HH:MM' and 'YYYY-MM-DD HH:MM:SS[.fff]'
- Timezone-aware ISO 8601 formats: 'YYYY-MM-DDTHH:MM:SS[.fff]+HH:MM' or '...Z'
- Conversion from timezone-aware inputs to track-local naive datetimes
- Preservation of high-resolution start times when minute-resolution updates occur
"""

from __future__ import annotations
import datetime
import logging
import re
from typing import Any
import zoneinfo

logger = logging.getLogger(__name__)

INVALID_FORMAT_MESSAGE = (
    'Invalid session_start_datetime format. Must be YYYY-MM-DD HH:MM, '
    'YYYY-MM-DD HH:MM:SS[.fff], or ISO 8601 with timezone (e.g. '
    'YYYY-MM-DDTHH:MM:SS+00:00 or ...Z).'
)

_TRACK_TIMEZONES: dict[str, str] = {
    # Germany (FAT DE)
    'kartbahn liedolsheim': 'Europe/Berlin',
    'kartbahn walldorf': 'Europe/Berlin',

    # US Texas (FAT US TX)
    'gulf coast karters': 'America/Chicago',
    'north texas karters': 'America/Chicago',
    'north texas kartway': 'America/Chicago',
    'kartmoto': 'America/Chicago',
    'kart moto': 'America/Chicago',

    # US California (FAT US CA, FAT World Finals)
    'willow springs': 'America/Los_Angeles',
    'willow springs reverse': 'America/Los_Angeles',
    'willow springs international raceway': 'America/Los_Angeles',
    'adams motorsports park': 'America/Los_Angeles',
    'apex kart racing': 'America/Los_Angeles',
    'apex racing center': 'America/Los_Angeles',
    'blue max': 'America/Los_Angeles',
    'blue max kart club': 'America/Los_Angeles',
    'buttonwillow raceway park': 'America/Los_Angeles',
    'sonoma raceway': 'America/Los_Angeles',
    'santa maria karting': 'America/Los_Angeles',
    'kinsmen': 'America/Los_Angeles',

    # US Midwest (FAT US MW)
    'new castle motorsports park': 'America/Indiana/Indianapolis',
    'badger': 'America/Chicago',
    'kaminsky park kart circuit': 'America/Chicago',
    'norway motorsports park': 'America/Chicago',
    'autobahn country club': 'America/Chicago',
    '61 kart way': 'America/Chicago',
    'road america': 'America/Chicago',
    'g&j kartway': 'America/New_York',
}

_KNOWN_UK_TRACKS: set[str] = {
    'bayford meadows', 'bayford meadows 2026',
    'brentwood', 'brentwood karting',
    'buckmore park', 'buckmore park 2026',
    'clay pigeon', 'clay pigeon 2026',
    'dunkeswell', 'dunkeswell 2026',
    'ellough park', 'ellough park 2026',
    'forest edge', 'forest edge kart club', 'forest edge kart club 2026',
    'fulbeck', 'fulbeck kart club', 'fulbeck kart club 2026',
    'glan y gors', 'glan-y-gors', 'gyg championship pro circuit', 'gyg championship pro circuit 2026',
    'hooton park', 'hooton park 2026',
    'kimbolton', 'kimbolton circuit 1 2026',
    'larkhall', 'larkhall pro', 'larkhall pro 2026',
    'llandow', 'south wales karting centre', 'south wales karting centre 2026',
    'lydd', 'lydd karting', 'lydd karting 2026',
    'nutts corner', 'nutts corner 2026',
    'paul fletcher international pro', 'paul fletcher international pro 2026', 'pfi', 'pf international',
    'red lodge', 'red lodge karting', 'red lodge karting 2026',
    'rissington', 'rissington 2026',
    'rowrah', 'rowrah lakeland circuit', 'rowrah lakeland circuit 2026',
    'rye house', 'rye house 2026', 'rye house layout 2 2026', 'rye house kart raceway',
    'shenington', 'shennington', 'shenington 2026',
    'silverstone',
    'teesside', 'teesside international circuit', 'teesside international circuit 2026',
    'three sisters', 'three sisters full circuit', 'three sisters full circuit 2026',
    'warden law', 'warden law 2026',
    'whilton mill', 'whilton mill international', 'whilton mill international 2026',
}

_NAIVE_MINUTE_RE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$')
_NAIVE_SECONDS_RE = re.compile(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?$')
_AWARE_ISO_RE = re.compile(
    r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})$'
)


def get_track_timezone(track_name: str | None, db: Any = None) -> zoneinfo.ZoneInfo:
    """Resolves the IANA timezone for a given track.

    Checks:
    1. Database / track json info for 'timezone' property.
    2. Static mapping of non-UK and known tracks.
    3. UK known tracks -> 'Europe/London'.
    4. Fallback -> 'Europe/London' with a warning log.
    """
    if db is not None and track_name:
        try:
            track_data = db.get_track(track_name)
            if track_data and track_data.get('timezone'):
                return zoneinfo.ZoneInfo(track_data['timezone'])
        except Exception as e:
            logger.debug('Error looking up track timezone from db: %s', e)

    if not track_name:
        logger.warning('No track name provided for timezone resolution. Defaulting to Europe/London.')
        return zoneinfo.ZoneInfo('Europe/London')

    key = track_name.lower().strip()
    if key in _TRACK_TIMEZONES:
        return zoneinfo.ZoneInfo(_TRACK_TIMEZONES[key])

    if key in _KNOWN_UK_TRACKS:
        return zoneinfo.ZoneInfo('Europe/London')

    logger.warning("Unknown track %r, falling back to Europe/London timezone.", track_name)
    return zoneinfo.ZoneInfo('Europe/London')


def parse_session_start_datetime(
    val: str | datetime.datetime,
    track_name: str | None = None,
    db: Any = None
) -> tuple[datetime.datetime, bool]:
    """Parses session_start_datetime from various formats into a naive track-local datetime.

    Accepted formats:
    - 'YYYY-MM-DD HH:MM' (naive, track-local, minute-resolution)
    - 'YYYY-MM-DD HH:MM:SS[.fff]' (naive, track-local, seconds-bearing)
    - ISO 8601 with 'T' separator and timezone offset or 'Z' (converted to track-local)

    Returns:
        tuple (local_naive_dt, has_seconds)
        where local_naive_dt has microsecond trimmed (second-level precision).

    Raises:
        ValueError if format is invalid.
    """
    if isinstance(val, datetime.datetime):
        if val.tzinfo is not None:
            tz = get_track_timezone(track_name, db=db)
            local_dt = val.astimezone(tz).replace(tzinfo=None)
            has_seconds = bool(val.second or val.microsecond)
            return local_dt.replace(microsecond=0), has_seconds
        else:
            return val.replace(microsecond=0), bool(val.second or val.microsecond)

    if not isinstance(val, str):
        raise ValueError(INVALID_FORMAT_MESSAGE)

    s = val.strip()
    if not s:
        raise ValueError(INVALID_FORMAT_MESSAGE)

    # 1. Naive minute resolution: YYYY-MM-DD HH:MM
    if _NAIVE_MINUTE_RE.match(s):
        dt = datetime.datetime.fromisoformat(s)
        return dt.replace(microsecond=0), False

    # 2. Naive seconds: YYYY-MM-DD HH:MM:SS[.fff]
    if _NAIVE_SECONDS_RE.match(s):
        dt = datetime.datetime.fromisoformat(s)
        return dt.replace(microsecond=0), True

    # 3. Aware ISO 8601: YYYY-MM-DDTHH:MM[:SS[.fff]](Z|offset)
    if _AWARE_ISO_RE.match(s):
        try:
            aware_dt = datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))
        except Exception:
            raise ValueError(INVALID_FORMAT_MESSAGE) from None

        # Determine whether input string specified seconds
        # Find time component after 'T' and before timezone indicator
        time_part = re.split(r'[Z+-]', s.split('T')[1])[0]
        has_seconds = time_part.count(':') >= 2

        tz = get_track_timezone(track_name, db=db)
        local_dt = aware_dt.astimezone(tz).replace(tzinfo=None)
        return local_dt.replace(microsecond=0), has_seconds

    raise ValueError(INVALID_FORMAT_MESSAGE)


def parse_session_start_datetime_tolerant(val: str | datetime.datetime) -> datetime.datetime:
    """Tolerantly parses a session start datetime string into a naive datetime.

    Accepts 'YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DD HH:MM', or ISO formats.
    """
    if isinstance(val, datetime.datetime):
        return val.replace(tzinfo=None)
    if not val or not isinstance(val, str):
        raise ValueError(f'Invalid session_start_datetime format: {val!r}')

    s = val.strip()
    try:
        # Handles YYYY-MM-DD HH:MM, YYYY-MM-DD HH:MM:SS, and ISO strings
        dt = datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))
        return dt.replace(tzinfo=None)
    except Exception:
        raise ValueError(f'Invalid session_start_datetime format: {val!r}') from None


def resolve_start_datetime_overwrite(
    existing_dt: datetime.datetime | None,
    incoming_dt: datetime.datetime,
    incoming_has_seconds: bool
) -> datetime.datetime:
    """Implements the overwrite rule for session start times.

    - If incoming value has minute resolution only (input string had no seconds)
      AND timedelta < 5 minutes: keep the existing value (retains high-resolution seconds).
    - If incoming value has minute resolution only AND timedelta >= 5 minutes:
      it is a genuine change, so overwrite.
    - If incoming value has seconds: overwrite (latest high-resolution value wins).
    """
    if existing_dt is None:
        return incoming_dt

    if not incoming_has_seconds:
        # Minute resolution incoming: check if within 5 minutes of existing
        if abs(existing_dt - incoming_dt) < datetime.timedelta(minutes=5):
            return existing_dt
        else:
            return incoming_dt
    else:
        # High-resolution incoming overwrites existing
        return incoming_dt
