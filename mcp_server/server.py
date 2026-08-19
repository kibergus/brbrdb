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

"""FastMCP server entry point for Telemetry Analysis."""
from __future__ import annotations
import base64
import csv
import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP, Image
from database import db
from mcp_server import image_tools, lap_tools

mcp = FastMCP("brbrdb")


# FIXME: This should live in race_db.py
def read_telemetry_header(csv_path: str) -> tuple[list[str], str | None]:
    """Fast header parser to extract available columns and driver name without reading data rows."""
    columns: list[str] = []
    driver_name: str | None = None
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            metadata_rows = []
            header = None
            found_blank = False
            for row in reader:
                if not found_blank:
                    if not row or all(cell.strip() == '' for cell in row):
                        found_blank = True
                    else:
                        metadata_rows.append(row)
                else:
                    header = [col.strip() for col in row]
                    break

            for row in metadata_rows:
                if row and len(row) >= 2 and row[0] in ('Driver name', 'Configuration'):
                    driver_name = row[1]
                    break

            if header:
                columns = header
    except (OSError, UnicodeDecodeError):
        pass

    return columns, driver_name


@mcp.tool()
def list_sessions(
    track: str | None = None,
    date: str | None = None,
    driver_name: str | None = None,
    league: str | None = None,
    class_name: str | None = None
) -> str:
    """
    Lists recorded sessions matching the given filters.

    Parameters:
    - track: Filter by track name (substring match)
    - date: Filter by ISO date string (YYYY-MM-DD)
    - driver_name: Filter by driver name (substring match)
    - league: e.g. "kartsim"
    - class_name: e.g. "iame_waterswift_restricted_cadet_uk"

    Returns JSON array string of session descriptors.
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
            for csv_file in sorted(os.listdir(tel_dir)):
                if csv_file.endswith('.csv') and (csv_file.startswith(s.session_id) or s.session_id in csv_file):
                    csv_path = os.path.join(tel_dir, csv_file)
                    cols, tel_driver = read_telemetry_header(csv_path)
                    if cols and not telemetry_channels:
                        skip = {'record', 'time', 'latitude', 'longitude', 'altitude'}
                        telemetry_channels = [c for c in cols if c not in skip]

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

    return json.dumps(results, indent=2)


@mcp.tool()
def get_track_info(track: str) -> dict[str, Any]:
    """
    Returns track metadata including lap length, sector boundaries, and named turn definitions
    with distances along the lap. This is the primary reference for interpreting distance-based
    tool parameters.

    Parameters:
    - track: Track name (e.g. "Lydd", "Lydd Karting 2026", "Buckmore Park")

    Returns track information dictionary.
    """
    track_data = db.get_track(track)
    if not track_data:
        raise ValueError(f"Track not found: {track!r}")
    return track_data


@mcp.tool()
def get_telemetry_plot(
    laps: list[tuple[str, str, str, int] | list[Any] | dict[str, Any]],
    start_m: float,
    end_m: float,
    channels: list[str]
) -> Image:
    """
    Generates a stacked multi-channel telemetry comparison plot for one or more laps over a
    distance range specified by the caller.

    `laps` selects laps for comparison. Each entry in `laps` MUST be a tuple
    or list specifying: (date, track, session_id, lap_number).
    Example:
    [
        ("2026-06-25", "Llandow", "16_20_practice", 15),
        ("2026-07-28", "Llandow", "18_32_practice", 12),
        ("2026-07-28", "Llandow", "18_32_practice", 15),
        ("2026-07-28", "Llandow", "18_32_practice", 21)
    ]

    Parameters:
    - laps: List of (date, track, session_id, lap_number) tuples or lists.
    - start_m: Start distance along the lap in metres
    - end_m: End distance along the lap in metres
    - channels: Channels to plot (e.g. ["Speed", "Brake", "Throttle", "Steering Angle", "delta_time"])

    Returns Image object containing PNG plot data.
    """
    if not laps:
        raise ValueError("laps parameter cannot be empty")
    if not channels:
        raise ValueError("channels parameter cannot be empty")
    if start_m >= end_m:
        raise ValueError("start_m must be strictly less than end_m")

    parsed_laps = [image_tools.parse_lap_spec(item) for item in laps]
    raw_lap_data: dict[int | str, dict[str, list[float]]] = {}
    all_lap_keys: list[int | str] = []
    labels: dict[int | str, str] = {}

    for idx, (cs_date, cs_track, cs_sid, cs_lap) in enumerate(parsed_laps):
        cs_key = f"{cs_sid}_L{cs_lap}" if f"{cs_sid}_L{cs_lap}" not in raw_lap_data else f"{cs_sid}_L{cs_lap}_{idx}"
        cs_csv = image_tools.find_telemetry_csv(cs_sid, cs_track, cs_date)
        cs_data = image_tools.extract_raw_lap_data(cs_csv, [cs_lap], channels)
        raw_lap_data[cs_key] = cs_data[cs_lap]
        all_lap_keys.append(cs_key)
        labels[cs_key] = f"Lap {cs_lap} ({cs_sid})"

    dists_grid, interp_lap_data = image_tools.interpolate_lap_channels(
        all_lap_keys, channels, raw_lap_data, start_m, end_m
    )
    first_sid = parsed_laps[0][2]
    b64_image = image_tools.render_stacked_telemetry_plot(
        first_sid, all_lap_keys, channels, dists_grid, interp_lap_data, labels=labels
    )
    return Image(data=base64.b64decode(b64_image), format="png")


@mcp.tool()
def get_stats(
    track: str,
    date: str | None = None,
    session_id: str | None = None,
    driver_name: str | None = None,
    turn_index: int | None = None,
    percentile_center: float | None = None,
    percentile_half_width: float = 5.0
) -> str:
    """
    Combined lap and per-turn stats tool. Returns one row per lap for every session matching the
    filters. Per-turn stats follow the track config apex definitions.

    Parameters:
    - track: Track name (e.g. "Lydd")
    - date: ISO date filter
    - session_id: Restrict to specific session
    - driver_name: Filter by driver name
    - turn_index: The turn index for which stats should be returned (1-indexed). Omit for whole lap.
    - percentile_center: Return laps within ±percentile_half_width of this percentile (0-100)
    - percentile_half_width: Half-width of percentile band (default 5.0)

    Returns JSON array string of lap or turn records.
    """
    stats = lap_tools.get_stats_impl(
        track=track,
        date=date,
        session_id=session_id,
        driver_name=driver_name,
        turn_index=turn_index,
        percentile_center=percentile_center,
        percentile_half_width=percentile_half_width
    )
    return json.dumps(stats, indent=2)


@mcp.tool()
def get_aggregates(
    track: str,
    date: str | None = None,
    session_id: str | None = None,
    driver_name: str | None = None,
    exclude_outlier_pct: float = 0.0
) -> dict[str, Any]:
    """
    Returns aggregate statistics across laps for each turn. Useful for reasoning about driver
    consistency and identifying turns where performance is unstable.

    Parameters:
    - track: Track name
    - date: ISO date filter
    - session_id: Restrict to one session
    - driver_name: Filter by driver name
    - exclude_outlier_pct: Trim this % from each tail before aggregating (0-20)

    Returns dictionary with overall and per-turn consistency metrics.
    """
    return lap_tools.get_aggregates_impl(
        track=track,
        date=date,
        session_id=session_id,
        driver_name=driver_name,
        exclude_outlier_pct=exclude_outlier_pct
    )


@mcp.tool()
def get_pace_summary(
    track: str,
    date: str,
    session_id: str | None = None,
    driver_name: str | None = None
) -> dict[str, Any]:
    """
    Returns a structured summary of the theoretical vs. actual best lap for a session or day,
    including per-turn deltas and prioritized improvement opportunities.

    Parameters:
    - track: Track name
    - date: ISO date
    - session_id: Restrict to one session
    - driver_name: Filter by driver name

    Returns summary dictionary with actual vs theoretical best lap and prioritized turn deltas.
    """
    return lap_tools.get_pace_summary_impl(
        track=track,
        date=date,
        session_id=session_id,
        driver_name=driver_name
    )


@mcp.tool()
def get_trajectory_plot(
    laps: list[tuple[str, str, str, int] | list[Any] | dict[str, Any]],
    start_m: float | None = None,
    end_m: float | None = None,
    color_mode: str = "lap"
) -> Image:
    """
    Renders GPS (X/Z) trajectories for one or more laps overlaid on the full track outline.

    `laps` selects laps for comparison. Each entry in `laps` MUST be a tuple
    or list specifying: (date, track, session_id, lap_number).
    Example:
    [
        ("2026-06-25", "Llandow", "16_20_practice", 15),
        ("2026-07-28", "Llandow", "18_32_practice", 12)
    ]

    Parameters:
    - laps: List of (date, track, session_id, lap_number) tuples or lists.
    - start_m: Start distance for crop (omit for full lap)
    - end_m: End distance for crop (omit for full lap)
    - color_mode: Color scheme mode: 'lap' (default, solid color per lap), 'pedals' (green throttle /
      red brake), 'accel' (green accel / red decel), or 'speed' (multi-stop speed heatmap).

    Returns Image object containing PNG trajectory plot data.
    """
    if not laps:
        raise ValueError("laps parameter cannot be empty")

    b64_image = image_tools.render_trajectory_plot(
        laps=laps,
        start_m=start_m,
        end_m=end_m,
        color_mode=color_mode
    )
    return Image(data=base64.b64decode(b64_image), format="png")


@mcp.tool()
def get_session_consistency_image(
    session_id: str,
    track: str,
    date: str,
    driver_name: str | None = None
) -> Image:
    """
    Generates a lap time evolution chart over the session: scatter of lap time per lap with
    rolling trend line.

    Parameters:
    - session_id: Session identifier
    - track: Track name
    - date: ISO date
    - driver_name: Filter by driver name

    Returns Image object containing PNG plot data.
    """
    b64_image = image_tools.render_session_consistency_image(
        session_id=session_id,
        track=track,
        date=date,
        driver_name=driver_name
    )
    return Image(data=base64.b64decode(b64_image), format="png")


if __name__ == "__main__":
    mcp.run()
