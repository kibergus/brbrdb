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
import os
from typing import Any
from database import db
import telemetry_store


def read_telemetry_header(csv_path: str) -> tuple[list[str], str | None]:
    """Fast header parser to extract available columns and driver name without reading data rows."""
    tel_dir = os.path.dirname(csv_path)
    stem = telemetry_store.strip_telemetry_suffix(os.path.basename(csv_path))
    return telemetry_store.read_session_header(tel_dir, stem)


def list_sessions_impl(
    track: str | None = None,
    date: str | None = None,
    driver_name: str | None = None,
    league: str | None = None,
    class_name: str | None = None
) -> list[dict[str, Any]]:
    """
    List recorded sessions matching a set of optional filters.

    Parameters:
    - track: Track name, e.g. "Lydd" or "Lydd Karting 2026"
    - date: ISO date "2026-07-12" — omit to list all
    - driver_name: Filter by driver name
    - league: e.g. "kartsim"
    - class_name: e.g. "iame_waterswift_restricted_cadet_uk"

    Returns array of session descriptors.
    """
    raw_sessions = db.find_sessions(
        leagues=league,
        classes=class_name,
        date=date,
        track=track,
        driver_names=driver_name
    )

    results = []
    for s in raw_sessions:
        drivers = db.get_session_drivers(s.session_id, s.meeting_folder)

        telemetry_channels: list[str] = []
        tel_dir = os.path.join(s.meeting_dir, 'telemetry') if s.meeting_dir else ''
        if tel_dir and os.path.exists(tel_dir):
            for stem in telemetry_store.list_driver_stems(tel_dir):
                if stem.startswith(s.session_id) or s.session_id in stem:
                    cols, tel_driver = telemetry_store.read_session_header(tel_dir, stem)
                    if cols and not telemetry_channels:
                        skip = {'record', 'time', 'latitude', 'longitude', 'altitude'}
                        telemetry_channels = [c for c in cols if c.lower() not in skip]

                    if tel_driver and tel_driver not in drivers:
                        drivers.append(tel_driver)

        results.append({
            "session_id": s.session_id,
            "date": s.date,
            "track": s.track_name,
            "league": s.league,
            "class_name": s.class_name[0] if s.class_name else "",
            "session_type": s.short_name or s.session_name,
            "driver_names": drivers,
            "telemetry_channels": telemetry_channels
        })

    return results
