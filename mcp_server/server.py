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
import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP, Image
from database import db
import telemetry_store
from mcp_server import image_tools, lap_tools, report_tools, track_tools

mcp = FastMCP("brbrdb")


def read_telemetry_header(csv_path: str) -> tuple[list[str], str | None]:
    """Fast header parser to extract available columns and driver name without reading data rows."""
    tel_dir = os.path.dirname(csv_path)
    stem = telemetry_store.strip_telemetry_suffix(os.path.basename(csv_path))
    return telemetry_store.read_session_header(tel_dir, stem)


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
    - driver_name: Filter by full driver name
    - league: e.g. "kartsim"
    - class_name: e.g. "iame_waterswift_restricted_cadet_uk"

    Returns JSON array string of session descriptors.
    """
    raw_sessions = db.find_sessions(
        leagues=league,
        classes=class_name,
        date=date,
        track=track
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

        if driver_name and not any(d.strip().lower() == driver_name.strip().lower() for d in drivers):
            continue

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
def get_track_info(track: str, include_geometry: bool = False) -> dict[str, Any]:
    """
    Returns track metadata including lap length, sector boundaries, and named turn definitions
    with distances along the lap. This is the primary reference for interpreting distance-based
    tool parameters.

    Parameters:
    - track: Track name (e.g. "Lydd", "Lydd Karting 2026", "Buckmore Park")
    - include_geometry: Whether to include track geometry (centerline coordinates and boundary GeoJSON).
      Defaults to False.

    Returns track information dictionary.
    """
    return track_tools.get_track_info_impl(track, include_geometry=include_geometry)


@mcp.tool()
def get_telemetry_plot(
    laps: list[tuple[str, str, str, int] | tuple[str, str, str, int, str] | dict[str, Any]],
    start_m: float,
    end_m: float,
    channels: list[str]
) -> Image:
    """
    Generates a stacked multi-channel telemetry comparison plot for one or more laps over [start_m, end_m].

    Each entry in `laps` can be:
    - Tuple: (date, track, session_id, lap) or (date, track, session_id, lap, driver_name)
    - Dict: {"date": str, "track": str, "session_id": str, "lap": int, "driver_name": str (optional)}
    `driver_name` is a case-insensitive full driver name match (required when a session has multiple drivers).
    Example: [
        ("2026-11-05", "Lydd", "18_43", 25, "KartSim Coach RS"),
        {"date": "2026-11-05", "track": "Lydd", "session_id": "18_43", "lap": 12, "driver_name": "Katia Guseinova"}
    ]

    Parameters:
    - laps: List of lap selector tuples or dicts.
    - start_m: Start distance along the lap in metres
    - end_m: End distance along the lap in metres
    - channels: Channels to plot (e.g. ["Speed", "Throttle", "Brake", "Steering Angle", "Delta Time"])

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
    resolved_drivers: list[str | None] = []

    for idx, (cs_date, cs_track, cs_sid, cs_lap, cs_driver) in enumerate(parsed_laps):
        cs_key = f"{cs_sid}_L{cs_lap}" if f"{cs_sid}_L{cs_lap}" not in raw_lap_data else f"{cs_sid}_L{cs_lap}_{idx}"
        cs_csv = image_tools.find_telemetry_csv(cs_sid, cs_track, cs_date, driver_name=cs_driver)
        cs_data = image_tools.extract_raw_lap_data(cs_csv, [cs_lap], channels)
        raw_lap_data[cs_key] = cs_data[cs_lap]
        all_lap_keys.append(cs_key)
        resolved_drivers.append(image_tools._read_csv_driver(cs_csv) or cs_driver)

    distinct_drivers = {d for d in resolved_drivers if d}
    show_driver = len(distinct_drivers) > 1 or any(p[4] is not None for p in parsed_laps)
    for idx, lap_key in enumerate(all_lap_keys):
        _, _, cs_sid, cs_lap, _ = parsed_laps[idx]
        drv = resolved_drivers[idx]
        labels[lap_key] = f"{drv} - Lap {cs_lap} ({cs_sid})" if (show_driver and drv) else f"Lap {cs_lap} ({cs_sid})"

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
    driver_name: str | None = None,
    reference_driver_name: str | None = None,
    reference_lap: int | None = None
) -> dict[str, Any]:
    """
    Returns a structured summary of the theoretical vs. actual best lap for a session or day,
    including per-turn deltas and prioritized improvement opportunities.

    When `reference_driver_name` (or `reference_lap`) is provided, computes a per-turn cross-driver
    benchmark (`turn_comparison`) between `driver_name` and `reference_driver_name`.
    When `driver_name` is omitted on a multi-driver session, returns per-driver summaries (`drivers`)
    and automatic cross-driver comparisons against the fastest driver (`comparisons`).

    Parameters:
    - track: Track name
    - date: ISO date
    - session_id: Restrict to one session
    - driver_name: Filter by full driver name (case-insensitive)
    - reference_driver_name: Optional benchmark full driver name (case-insensitive)
    - reference_lap: Optional specific lap number for the reference driver (defaults to their best lap)

    Returns summary dictionary with actual vs theoretical best lap, prioritized turn deltas, and
    cross-driver comparison when applicable.
    """
    return lap_tools.get_pace_summary_impl(
        track=track,
        date=date,
        session_id=session_id,
        driver_name=driver_name,
        reference_driver_name=reference_driver_name,
        reference_lap=reference_lap
    )


@mcp.tool()
def get_trajectory_plot(
    laps: list[tuple[str, str, str, int] | tuple[str, str, str, int, str] | dict[str, Any]],
    start_m: float | None = None,
    end_m: float | None = None,
    color_mode: str = "lap"
) -> Image:
    """
    Renders GPS (X/Z) trajectories for one or more laps overlaid on the full track outline.

    Each entry in `laps` can be:
    - Tuple: (date, track, session_id, lap) or (date, track, session_id, lap, driver_name)
    - Dict: {"date": str, "track": str, "session_id": str, "lap": int, "driver_name": str (optional)}
    `driver_name` is a case-insensitive full driver name match (required when a session has multiple drivers).
    Example: [
        ("2026-11-05", "Lydd", "18_43", 25, "KartSim Coach RS"),
        {"date": "2026-11-05", "track": "Lydd", "session_id": "18_43", "lap": 12, "driver_name": "Katia Guseinova"}
    ]

    Parameters:
    - laps: List of lap selector tuples or dicts.
    - start_m: Start distance for crop (omit for full lap)
    - end_m: End distance for crop (omit for full lap)
    - color_mode: Color scheme mode: 'lap' (default, solid color per lap), 'pedals' (green throttle /
      red brake), 'accel' (green accel / red decel), 'speed' (multi-stop speed heatmap), or 'delta_t'
      (white for reference lap, green/yellow/red derivative rate of time gained/lost vs reference lap).

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


@mcp.tool()
def save_report(content: str, report_id: str | None = None) -> dict[str, Any]:
    """
    Advanced capability: Saves or updates a permanent telemetry analysis HTML report on the server.

    IMPORTANT: Only use this tool when the user explicitly requests to save or publish the report to the website.
    Do NOT call this automatically at the end of an analysis. Most interactions should be delivered directly
    as conversational commentary and interactive pills inside the Antigravity chat.

    Note on permissions: Saving reports requires manage_reports permission on the server. If this tool errors out
    with a permission denied error, inform the user that remote report saving is not available for their account/token,
    but reassure them that they can still interact with the analysis and explore telemetry directly within the
    Antigravity UI.

    If `report_id` is empty or None, generates a new random unguessable UUIDv4 for the report.
    If `report_id` is specified, it MUST be a valid UUID; otherwise an error is raised.
    Existing reports can only be edited by their original author.

    Parameters:
    - content: Full HTML report content including metadata frontmatter/comment.
    - report_id: Optional UUID of the report. Must be a valid UUID if provided.

    Returns dictionary with report_id, filename, and viewer URL.
    """
    return report_tools.save_report_impl(content, report_id=report_id)


@mcp.tool()
def read_report(report_id: str, start_line: int | None = None, end_line: int | None = None) -> str:
    """
    Reads report content from the reports directory, optionally sliced by line range.

    Parameters:
    - report_id: Report UUID or name.
    - start_line: Optional 1-indexed starting line number.
    - end_line: Optional 1-indexed ending line number.

    Returns the text content of the report.
    """
    return report_tools.read_report_impl(report_id, start_line=start_line, end_line=end_line)


@mcp.tool()
def edit_report(
    report_id: str,
    target_content: str,
    replacement_content: str,
    start_line: int | None = None,
    end_line: int | None = None,
    allow_multiple: bool = False
) -> str:
    """
    Performs surgical replacement of target text in an existing report.
    `report_id` must be a valid UUID. Only the author of the report is permitted to edit it.

    Parameters:
    - report_id: Report UUID (must be a valid UUID).
    - target_content: Exact substring to find and replace.
    - replacement_content: Text to replace target_content with.
    - start_line: Optional 1-indexed start line boundary.
    - end_line: Optional 1-indexed end line boundary.
    - allow_multiple: Whether to allow replacing multiple occurrences (default: False).

    Returns confirmation message.
    """
    return report_tools.edit_report_impl(
        report_id=report_id,
        target_content=target_content,
        replacement_content=replacement_content,
        start_line=start_line,
        end_line=end_line,
        allow_multiple=allow_multiple
    )


if __name__ == "__main__":
    import sys
    from typing import Literal, cast
    t_arg = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    transport = cast(Literal["stdio", "sse", "streamable-http"], t_arg)
    mcp.run(transport=transport)
