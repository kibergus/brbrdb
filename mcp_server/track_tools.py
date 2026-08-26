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
from typing import Any
from database import db


def get_track_info_impl(track: str) -> dict[str, Any]:
    """
    Returns track metadata including lap length, sector boundaries, and named turn definitions
    with distances along the lap.

    Parameters:
    - track: Track name (e.g. "Lydd", "Lydd Karting 2026", "Buckmore Park")

    Returns track information dictionary with canonical field names:
    - lap_length (meters)
    - sector_end (list of meter boundaries)
    - turns: list of objects with { name, start, apex, end } (distances in meters)
    """
    raw_track_data = db.get_track(track)
    if not raw_track_data:
        raise ValueError(f"Track not found: {track!r}")

    track_data: dict[str, Any] = dict(raw_track_data)

    lap_len_raw = track_data.get('lap_length', 0.0)
    track_data['lap_length'] = float(lap_len_raw)

    sec_ends = track_data.get('sector_end', [])
    track_data['sector_end'] = [float(s) for s in sec_ends] if isinstance(sec_ends, list) else []

    turns = track_data.get('turns', [])
    normalized_turns: list[dict[str, Any]] = []
    if isinstance(turns, list):
        for turn in turns:
            if isinstance(turn, dict):
                t = dict(turn)
                start_val = float(t.get('start', 0.0))
                end_val = float(t.get('end', 0.0))

                raw_apex = t.get('apex', [])
                if isinstance(raw_apex, (int, float)):
                    apex_list = [float(raw_apex)]
                elif isinstance(raw_apex, list):
                    apex_list = [float(a) for a in raw_apex if a is not None]
                else:
                    apex_list = []

                normalized_turns.append({
                    'name': str(t.get('name', '')),
                    'start': start_val,
                    'apex': apex_list,
                    'end': end_val
                })

    track_data['turns'] = normalized_turns
    return track_data
