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

import json
import pytest
from mcp.server.fastmcp import Image
from mcp_server import server


def test_list_sessions_all() -> None:
    sessions = json.loads(server.list_sessions())
    assert isinstance(sessions, list)
    if sessions:
        s = sessions[0]
        required_keys = {
            "session_id", "date", "track", "league",
            "class_name", "session_type", "driver_names", "telemetry_channels"
        }
        assert required_keys.issubset(s.keys())
        assert isinstance(s["driver_names"], list)
        assert isinstance(s["telemetry_channels"], list)


def test_list_sessions_filtered_by_track() -> None:
    sessions = json.loads(server.list_sessions(track="Lydd"))
    assert isinstance(sessions, list)
    assert len(sessions) > 0
    for s in sessions:
        assert "lydd" in s["track"].lower()


def test_list_sessions_filtered_by_driver() -> None:
    sessions = json.loads(server.list_sessions(driver_name="Alice"))
    assert isinstance(sessions, list)
    for s in sessions:
        assert any("alice" in d.lower() for d in s["driver_names"])


def test_get_track_info_success() -> None:
    info = server.get_track_info("Lydd")
    assert isinstance(info, dict)
    assert info["track_name"] == "Lydd Karting 2026"
    assert info["lap_length"] > 0
    assert isinstance(info["sector_end"], list)
    assert len(info["sector_end"]) > 0
    assert isinstance(info["turns"], list)
    assert len(info["turns"]) > 0

    t1 = info["turns"][0]
    assert "name" in t1
    assert "start" in t1
    assert "apex" in t1
    assert "end" in t1
    assert t1["name"] == "1"


def test_get_track_info_invalid_track() -> None:
    with pytest.raises(ValueError, match="Track not found"):
        server.get_track_info("NonExistentTrack12345")


def test_get_telemetry_plot_success() -> None:
    result = server.get_telemetry_plot(
        laps=[
            ("2026-07-12", "Lydd", "18_54_practice", 23),
            ("2026-07-12", "Lydd", "18_54_practice", 25)
        ],
        start_m=12.0,
        end_m=120.0,
        channels=["Speed", "Brake", "delta_time"]
    )
    assert isinstance(result, Image)
    img_bytes = result.data
    assert img_bytes is not None and img_bytes.startswith(b'\x89PNG\r\n\x1a\n')


def test_get_telemetry_plot_invalid_session() -> None:
    with pytest.raises(ValueError, match="Telemetry file not found"):
        server.get_telemetry_plot(
            laps=[("2026-07-12", "Lydd", "non_existent_session", 1)],
            start_m=0.0,
            end_m=100.0,
            channels=["Speed"]
        )


def test_get_telemetry_plot_invalid_channel() -> None:
    with pytest.raises(ValueError, match="Channel 'NonExistentChannel' not found"):
        server.get_telemetry_plot(
            laps=[("2026-07-12", "Lydd", "18_54_practice", 23)],
            start_m=12.0,
            end_m=120.0,
            channels=["NonExistentChannel"]
        )


def test_get_telemetry_plot_invalid_dist_range() -> None:
    with pytest.raises(ValueError, match="start_m must be strictly less than end_m"):
        server.get_telemetry_plot(
            laps=[("2026-07-12", "Lydd", "18_54_practice", 23)],
            start_m=120.0,
            end_m=12.0,
            channels=["Speed"]
        )


@pytest.mark.anyio
async def test_fastmcp_tool_registration() -> None:
    tools = await server.mcp.list_tools()
    tool_names = [t.name for t in tools]
    assert "list_sessions" in tool_names
    assert "get_track_info" in tool_names
    assert "get_telemetry_plot" in tool_names
    assert "get_stats" in tool_names
    assert "get_aggregates" in tool_names
    assert "get_pace_summary" in tool_names
    assert "get_trajectory_plot" in tool_names
    assert "get_session_consistency_image" in tool_names


def test_get_stats_whole_lap() -> None:
    stats = json.loads(server.get_stats(track="Lydd", date="2026-07-12"))
    assert isinstance(stats, list)
    if stats:
        st = stats[0]
        assert "session_id" in st
        assert "lap" in st
        assert "time_s" in st
        assert "entry_speed_kmh" in st
        assert "exit_speed_kmh" in st


def test_get_stats_turn_index() -> None:
    stats = json.loads(server.get_stats(track="Lydd", date="2026-07-12", turn_index=1))
    assert isinstance(stats, list)
    if stats:
        st = stats[0]
        assert st["turn_index"] == 1
        assert "turn_name" in st
        assert "time_s" in st
        assert "apexes" in st
        assert isinstance(st["apexes"], list)


def test_get_aggregates() -> None:
    aggs = server.get_aggregates(track="Lydd", date="2026-07-12")
    assert isinstance(aggs, dict)
    assert "overall" in aggs
    assert "lap_time_min_s" in aggs["overall"]


def test_get_pace_summary() -> None:
    pace = server.get_pace_summary(track="Lydd", date="2026-07-12")
    assert isinstance(pace, dict)
    assert "actual_best_s" in pace
    assert "theoretical_best_s" in pace
    assert "total_delta_s" in pace
    assert "turns" in pace
    assert isinstance(pace["turns"], list)


def test_get_trajectory_plot() -> None:
    for mode in ["lap", "pedals", "accel", "speed", "delta_t"]:
        res = server.get_trajectory_plot(
            laps=[
                ("2026-07-12", "Lydd", "18_54_practice", 23),
                ("2026-07-12", "Lydd", "18_54_practice", 15)
            ],
            color_mode=mode
        )
        assert isinstance(res, Image)
        img_bytes = res.data
        assert img_bytes is not None and img_bytes.startswith(b'\x89PNG\r\n\x1a\n')


def test_get_session_consistency_image() -> None:
    res = server.get_session_consistency_image(session_id="18_54_practice", track="Lydd", date="2026-07-12")
    assert isinstance(res, Image)
    img_bytes = res.data
    assert img_bytes is not None and img_bytes.startswith(b'\x89PNG\r\n\x1a\n')
