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

"""Parser and utility functions for Telemetry Reports.

Report Format Specification:
============================
Telemetry reports are saved as HTML files (e.g. in ``data/reports/<report_name>.html``
or ``reports/<report_name>.html``). Each report file consists of two main parts:
1. A structured metadata/state header block (formatted as a leading HTML comment
   ``<!-- { ... } -->`` or frontmatter ``--- { ... } ---``).
2. The HTML body content rendered inside the dedicated "Report" tab in the right panel.

Header Fields:
--------------
- ``title`` (str): Human-readable title of the report (shown in breadcrumbs).
- ``league`` (str): League identifier (e.g. ``"kartsim"``, ``"club100_south"``).
- ``class_name`` (str): Class identifier (e.g. ``"iame_waterswift_restricted_cadet_uk"``).
- ``date`` (str): Meeting date (e.g. ``"2026-08-21"``).
- ``track`` (str): Track name (e.g. ``"Clay Pigeon"``).
- ``session_id`` (str, optional): Default session ID to filter by.
- ``state`` (dict, optional): Initial UI dashboard state:
    - ``tab`` (str): Active main tab (``"map"`` or ``"stats"``; defaults to ``"map"`` in report mode).
    - ``rtab`` (str): Active right-panel tab (defaults to ``"report"``; options: ``"report"``,
      ``"cornering"``, ``"acceleration"``, ``"slip_angle"``).
    - ``sort`` (str): Sort mode in sidebar (``"time"``, ``"num"``, or ``"turn"``).
    - ``turn`` (int): Turn index (0-indexed) when sorting by turn.
    - ``lapsA`` (list[str] | str): Lap IDs selected for Group A (e.g. ``["session_id-6"]``).
    - ``lapsB`` (list[str] | str): Lap IDs selected for Group B (e.g. ``["session_id-5"]``).
    - ``visA`` (bool): Visibility of Group A traces (defaults to True).
    - ``visB`` (bool): Visibility of Group B traces (defaults to True).
    - ``xlim`` (list[float]): X-axis distance range in meters (e.g. ``[280.0, 420.0]``).
    - ``dist`` (float): Initial playback distance cursor in meters.
    - ``delta`` (bool): Whether to show the bottom Delta T plot.
    - ``speed`` (bool): Whether to show the bottom Speed plot.
    - ``plot`` (list[str] | str): Active expandable bottom channel plots (e.g. ``["steering", "gforce"]``).
    - ``tcol`` (str): Trajectory color mode (``"pedals"``, ``"speed"``, ``"accel"``, ``"delta_t"``, etc.).
    - ``sacol`` (str): Slip angle color mode (``"brake_throttle"``, ``"longitudinal_accel"``, etc.).
    - ``map`` (list[float] | dict): Initial map position (``[zoom, lat, lng]``
      or ``{"zoom": 18, "lat": ..., "lng": ...}``).
    - ``sidePanelWidth`` (int): Initial width of the right side panel in pixels.

Interactive Triggers in HTML Body:
----------------------------------
Elements in the report HTML can include data attributes (or use the ``window.Telemetry`` JS helper)
for instant client-side interactivity:
- ``data-dist="<meters>"``: Seeks the distance cursor and updates map/plot markers.
- ``data-xlim="<min>,<max>"`` / ``data-range="<min>,<max>"``:
  Sets the visible range in the bottom plot (meters).
- ``data-map-range="<min>,<max>"`` / ``data-center-map="<min>,<max>"``:
  Centers/fits the map over the track segment (meters).
- ``data-focus-range="<min>,<max>"``:
  Sets bottom plot visible range AND centers the map over that track segment.
- ``data-turn="<index>"``: Selects turn sorting and switches to that turn.
- ``data-laps="<lap_ids>"``: Changes active lap selection.
- ``data-laps-a="<lap_ids>"``: Changes Group A lap selection.
- ``data-laps-b="<lap_ids>"``: Changes Group B lap selection.
- ``data-toggle-plot="<plot_name>"``: Toggles visibility of a single plot
  (e.g. ``"steering"``, ``"speed"``, ``"delta"``).
- ``data-plots="<comma_separated_plots>"``: Sets the exact visible plots and hides others
  (e.g. ``"speed,steering,gforce"`` or ``"none"``).
- ``data-tab="map|stats"``: Switches between map and stats views.
- ``data-rtab="report|cornering|acceleration|slip_angle"``: Switches right panel tabs.
- Class ``telemetry-jump-btn``: Pre-styled button for interactive triggers and jumps.
- JS Helpers available in iframe:
  - ``window.Telemetry.setRange(startM, endM)``
  - ``window.Telemetry.focusMap(startM, endM)``
  - ``window.Telemetry.focusRange(startM, endM)``
  - ``window.Telemetry.togglePlot('<name>')``
  - ``window.Telemetry.setPlots(['speed', 'steering'])``
  - ``window.Telemetry.selectLaps(['6'], ['5'])``
  - ``window.Telemetry.jump({ dist: 316.5, xlim: '290,380', ... })``

Example Report File:
--------------------
<!-- {
  "title": "Turn 3 Hairpin Coaching Analysis",
  "league": "kartsim",
  "class_name": "iame_waterswift_restricted_cadet_uk",
  "date": "2026-08-21",
  "track": "Clay Pigeon",
  "session_id": "18_09_practice",
  "state": {
    "tab": "map",
    "sort": "turn",
    "turn": 2,
    "lapsA": ["18_09_practice_katia_guseinova.csv-6"],
    "lapsB": ["18_09_practice_katia_guseinova.csv-5"],
    "visA": true,
    "visB": true,
    "xlim": [280.0, 420.0],
    "dist": 340.0,
    "delta": true,
    "speed": true,
    "plot": ["steering", "gforce"],
    "tcol": "pedals",
    "rtab": "report",
    "sidePanelWidth": 420
  }
} -->

<div class="report-content">
  <div class="report-badge">Coaching Deep-Dive</div>
  <h2>Turn 3 Hairpin Analysis</h2>
  <p>
    Braking begins at <button class="telemetry-jump-btn" data-dist="316.5" data-xlim="290,380">316.5m</button>.
  </p>
</div>
"""

import json
import os
import re
from typing import Any, Tuple

from database import config

# Primary and fallback reports directories
REPORTS_DIR = os.path.join(config.get('data_dir', ''), 'reports')
WORKSPACE_REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reports')


def resolve_report_path(report_name: str, reports_dir: str | None = None) -> str | None:
    """Safely resolve a report filename or relative path inside reports directories.

    Prevents directory traversal attacks.
    """
    search_dirs = []
    if reports_dir:
        search_dirs.append(reports_dir)
    else:
        if REPORTS_DIR and os.path.isdir(REPORTS_DIR):
            search_dirs.append(REPORTS_DIR)
        if WORKSPACE_REPORTS_DIR and os.path.isdir(WORKSPACE_REPORTS_DIR):
            search_dirs.append(WORKSPACE_REPORTS_DIR)

    candidates = [report_name]
    if not report_name.endswith('.html') and not report_name.endswith('.htm'):
        candidates.append(f"{report_name}.html")

    for base_dir in search_dirs:
        real_base = os.path.realpath(base_dir)
        for cand in candidates:
            target_path = os.path.realpath(os.path.join(base_dir, cand))
            if target_path == real_base or target_path.startswith(real_base + os.sep):
                if os.path.isfile(target_path):
                    return target_path

    return None


def parse_report_content(raw_content: str) -> Tuple[dict[str, Any], dict[str, Any], str]:
    """Parse raw report file text into (metadata, state, body_html).

    Supports:
    1. Leading HTML comment with JSON: <!-- { ... } -->
    2. Leading frontmatter: --- { ... } ---
    3. <script type="application/json" id="telemetry-report-data"> tag
    4. Plain HTML body fallback
    """
    metadata: dict[str, Any] = {}
    body_html = raw_content

    # 1. Check for leading HTML comment <!-- { ... } -->
    comment_match = re.match(r'^\s*<!--\s*(?:telemetry_report\s*)?(\{.*?\})\s*-->', raw_content, re.DOTALL)
    if comment_match:
        try:
            parsed = json.loads(comment_match.group(1))
            if isinstance(parsed, dict):
                metadata = parsed
                body_html = raw_content[comment_match.end():].strip()
        except Exception:
            pass

    # 2. Check for frontmatter --- ... --- if not already parsed
    if not metadata:
        fm_match = re.match(r'^\s*---\s*\n(.*?)\n---\s*\n', raw_content, re.DOTALL)
        if fm_match:
            try:
                parsed = json.loads(fm_match.group(1).strip())
                if isinstance(parsed, dict):
                    metadata = parsed
                    body_html = raw_content[fm_match.end():].strip()
            except Exception:
                pass

    # 4. Check for embedded script tag
    if not metadata:
        script_match = re.search(
            r'<script\s+type=["\']application/json["\']\s+id=["\']telemetry-report-data["\']\s*>(.*?)</script>',
            raw_content,
            re.DOTALL
        )
        if script_match:
            try:
                parsed = json.loads(script_match.group(1).strip())
                if isinstance(parsed, dict):
                    metadata = parsed
            except Exception:
                pass

    # 5. Check for any HTML comment with JSON (e.g. inside <head> or preceded by <!DOCTYPE html>)
    if not metadata:
        comment_search = re.search(r'<!--\s*(?:telemetry_report\s*)?(\{.*?\})\s*-->', raw_content, re.DOTALL)
        if comment_search:
            try:
                parsed = json.loads(comment_search.group(1))
                if isinstance(parsed, dict):
                    metadata = parsed
                    body_html = (
                        raw_content[:comment_search.start()] +
                        raw_content[comment_search.end():]
                    ).strip()
            except Exception:
                pass

    # Normalize state dict
    state_val = metadata.get('state')
    state = state_val if isinstance(state_val, dict) else {}

    return metadata, state, body_html


def validate_report_content(content: str) -> dict[str, Any]:
    """Validate report content before saving or writing.

    Ensures the report contains a valid metadata header with all required fields
    necessary for rendering inside the Telemetry Viewer.

    Raises:
        ValueError: If metadata cannot be parsed or required fields are missing.
    """
    if not content or not content.strip():
        raise ValueError("Report content cannot be empty.")

    meta, state, body = parse_report_content(content)
    if not meta:
        raise ValueError(
            "Report metadata could not be parsed. Telemetry reports must include a JSON metadata "
            "header block at the top (<!-- { ... } -->) specifying meeting parameters and viewer state.\n"
            "Example header:\n"
            "<!-- {\n"
            '  "title": "Turn Analysis Coaching Guide",\n'
            '  "league": "kartsim",\n'
            '  "class_name": "iame_waterswift_restricted_cadet_uk",\n'
            '  "date": "2026-10-03",\n'
            '  "track": "Whilton Mill",\n'
            '  "session_id": "10_17_practice",\n'
            '  "state": {\n'
            '    "tab": "map",\n'
            '    "rtab": "report",\n'
            '    "sort": "turn",\n'
            '    "turn": 5\n'
            "  }\n"
            "} -->"
        )

    sessions_val = meta.get('sessions')
    has_sessions = isinstance(sessions_val, list) and len(sessions_val) > 0
    league = meta.get('league')
    class_name = meta.get('class_name') or meta.get('class')
    date = meta.get('date')
    track = meta.get('track')

    if not has_sessions:
        missing = []
        if not league or not isinstance(league, str) or not league.strip():
            missing.append("'league'")
        if not class_name or not isinstance(class_name, str) or not class_name.strip():
            missing.append("'class_name'")
        if not date or not isinstance(date, str) or not date.strip():
            missing.append("'date'")
        if not track or not isinstance(track, str) or not track.strip():
            missing.append("'track'")

        if missing:
            raise ValueError(
                f"Report metadata is missing required field(s): {', '.join(missing)}. "
                "Single-session reports must specify 'league', 'class_name', 'date', and 'track' "
                "(or 'sessions' for multi-session reports) so they can be loaded in the Telemetry Viewer."
            )

    return meta


def load_report(report_name: str, reports_dir: str | None = None) -> Tuple[dict[str, Any], dict[str, Any], str] | None:
    """Load and parse a report file by name.

    Returns (metadata, state, body_html) or None if file not found.
    """
    file_path = resolve_report_path(report_name, reports_dir=reports_dir)
    if not file_path:
        return None

    with open(file_path, 'r', encoding='utf-8') as f:
        raw_content = f.read()

    return parse_report_content(raw_content)
