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

from pathlib import Path
import json
import uuid
import pandas as pd
import pytest
from mcp.server.fastmcp import Image
from mcp_server import server, image_tools, lap_tools


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
    assert info["track_name"] == "Lydd"
    assert info["lap_length"] > 0
    assert isinstance(info["sector_end"], list)
    assert len(info["sector_end"]) > 0
    assert isinstance(info["turns"], list)
    assert len(info["turns"]) > 0
    assert "center_line" not in info
    assert "geojson" not in info

    t1 = info["turns"][0]
    assert t1["name"] == "1"
    assert isinstance(t1["start"], float)
    assert isinstance(t1["apex"], list)
    assert isinstance(t1["end"], float)
    assert t1["start"] < t1["end"]

    info_with_geom = server.get_track_info("Lydd", include_geometry=True)
    assert "center_line" in info_with_geom
    assert len(info_with_geom["center_line"]) > 0
    assert "geojson" in info_with_geom
    assert len(info_with_geom["geojson"]) > 0


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
        channels=["Speed", "Throttle", "Brake", "Steering Angle", "Delta Time"]
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


def test_extract_raw_lap_data_with_outlap(tmp_path: Path) -> None:
    csv_content = """Format,RaceTools CSV
Driver name,Alexey

Time,Latitude,Longitude,Record,Lap,Lap Distance (m),Speed (km/h)
2026-09-12T11:02:00.000Z,50.934,0.907,1,1,117.2,30.0
2026-09-12T11:02:30.000Z,50.934,0.907,2,1,1040.0,75.0
2026-09-12T11:02:40.000Z,50.934,0.907,3,2,0.0,70.0
2026-09-12T11:03:28.409Z,50.934,0.907,4,2,1040.0,72.0
"""
    test_file = tmp_path / "outlap_extract_test.csv"
    test_file.write_text(csv_content)

    # Request normalized Lap 1 (flying lap 1, which corresponds to raw CSV Lap 2)
    data = image_tools.extract_raw_lap_data(str(test_file), [1], ["Speed (km/h)"])
    assert 1 in data
    assert len(data[1]['dist']) == 2
    assert data[1]['dist'] == [0.0, 1040.0]
    assert data[1]['Speed (km/h)'] == [70.0, 72.0]


VALID_REPORT_HEADER = """<!-- {
  "title": "Turn Analysis Test",
  "league": "kartsim",
  "class_name": "cadet",
  "date": "2026-08-21",
  "track": "Clay Pigeon"
} -->\n"""


def test_save_report_generates_uuid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('mcp_server.report_tools.get_reports_dir', lambda: str(tmp_path))
    content = f"{VALID_REPORT_HEADER}<div>Report Body</div>"

    res = server.save_report(content=content)
    assert res['status'] == 'success'
    report_id = res['report_id']
    # Check valid UUID
    assert str(uuid.UUID(report_id)) == report_id
    assert res['filename'] == f"{report_id}.html"
    assert res['url'] == f"https://brbrdb.brbrkitten.com/telemetry/report/{report_id}"

    # File exists
    saved_file = tmp_path / f"{report_id}.html"
    assert saved_file.exists()
    assert saved_file.read_text() == content


def test_save_report_validation_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('mcp_server.report_tools.get_reports_dir', lambda: str(tmp_path))

    # Missing metadata entirely
    with pytest.raises(ValueError, match="metadata could not be parsed"):
        server.save_report(content="<div>Report without metadata</div>")

    # Missing required metadata fields
    incomplete = '<!-- {"title": "Only Title"} -->\n<div>Body</div>'
    with pytest.raises(ValueError, match="missing required field"):
        server.save_report(content=incomplete)


def test_save_report_non_uuid_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('mcp_server.report_tools.get_reports_dir', lambda: str(tmp_path))
    with pytest.raises(ValueError, match="must be a valid UUID"):
        server.save_report(content=f"{VALID_REPORT_HEADER}<div>Test</div>", report_id="my_cool_report")


def test_read_report_and_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('mcp_server.report_tools.get_reports_dir', lambda: str(tmp_path))
    content = f"{VALID_REPORT_HEADER}Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n"

    res = server.save_report(content=content)
    report_id = res['report_id']

    # Full read
    full = server.read_report(report_id)
    assert full == content

    # Line slice
    sliced = server.read_report(report_id, start_line=1, end_line=2)
    assert "Turn Analysis Test" in sliced


def test_edit_report_success_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('mcp_server.report_tools.get_reports_dir', lambda: str(tmp_path))
    content = f"{VALID_REPORT_HEADER}Line 1\nTarget line here\nLine 3\n"

    res = server.save_report(content=content)
    report_id = res['report_id']

    # Edit target content
    edit_msg = server.edit_report(
        report_id=report_id,
        target_content="Target line here",
        replacement_content="Replaced line here"
    )
    assert "updated successfully" in edit_msg

    updated = server.read_report(report_id)
    assert "Replaced line here" in updated
    assert "Target line here" not in updated

    # Non-UUID error
    with pytest.raises(ValueError, match="must be a valid UUID"):
        server.edit_report(
            report_id="bad_name",
            target_content="foo",
            replacement_content="bar"
        )

    # Missing target content error
    with pytest.raises(ValueError, match="Target content not found"):
        server.edit_report(
            report_id=report_id,
            target_content="nonexistent text",
            replacement_content="new text"
        )

    # Edit that breaks required metadata
    with pytest.raises(ValueError, match="missing required field"):
        server.edit_report(
            report_id=report_id,
            target_content='"Clay Pigeon"',
            replacement_content='""'
        )


def test_report_author_permissions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('mcp_server.report_tools.get_reports_dir', lambda: str(tmp_path))

    # User A creates report
    monkeypatch.setattr('auth.get_current_author', lambda: ("user-a-key", "User A"))
    res = server.save_report(content=f"{VALID_REPORT_HEADER}<div>Author A Report</div>")
    report_id = res['report_id']

    # User A can edit it
    server.edit_report(
        report_id=report_id,
        target_content="Author A Report",
        replacement_content="Author A Report (v2)"
    )

    # User B attempts to edit User A's report
    monkeypatch.setattr('auth.get_current_author', lambda: ("user-b-key", "User B"))
    with pytest.raises(PermissionError, match="only the report author can edit"):
        server.edit_report(
            report_id=report_id,
            target_content="Author A Report (v2)",
            replacement_content="Hijacked"
        )

    # User B also cannot overwrite it with save_report
    with pytest.raises(PermissionError, match="only the report author can edit"):
        server.save_report(
            content=f"{VALID_REPORT_HEADER}<div>Overwritten</div>",
            report_id=report_id
        )


def test_report_manage_permission_guidance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('mcp_server.report_tools.get_reports_dir', lambda: str(tmp_path))
    monkeypatch.setattr('mcp_server.report_tools.has_request_context', lambda: True)
    monkeypatch.setattr('auth.get_current_acl', lambda: {'permissions': []})

    with pytest.raises(PermissionError, match="does not have permission to manage reports") as exc_info:
        server.save_report(content=f"{VALID_REPORT_HEADER}<div>Denied Report</div>")

    assert "Guidance for agent:" in str(exc_info.value)
    assert "Antigravity UI" in str(exc_info.value)


def test_parse_lap_spec_formats() -> None:
    assert image_tools.parse_lap_spec(("2026-07-12", "Lydd", "18_54_practice", 23)) == (
        "2026-07-12", "Lydd", "18_54_practice", 23, None
    )
    assert image_tools.parse_lap_spec(("2026-11-05", "Whilton Mill", "18_43_practice", 25, "Coach")) == (
        "2026-11-05", "Whilton Mill", "18_43_practice", 25, "Coach"
    )
    assert image_tools.parse_lap_spec({
        "date": "2026-11-05",
        "track": "Whilton Mill",
        "session_id": "18_43_practice",
        "lap": 12,
        "driver_name": "Katia"
    }) == ("2026-11-05", "Whilton Mill", "18_43_practice", 12, "Katia")
    assert image_tools.parse_lap_spec({
        "date": "2026-11-05",
        "track": "Whilton Mill",
        "session_id": "18_43_practice",
        "lap_number": 15,
        "driver": "Coach"
    }) == ("2026-11-05", "Whilton Mill", "18_43_practice", 15, "Coach")

    with pytest.raises(ValueError, match="missing 'lap'"):
        image_tools.parse_lap_spec({"date": "2026-11-05", "track": "Lydd", "session_id": "s1"})

    with pytest.raises(ValueError, match="missing required key"):
        image_tools.parse_lap_spec({"date": "2026-11-05", "lap": 5})


def test_multi_driver_lap_selection_and_plots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tel_dir = tmp_path / "telemetry"
    tel_dir.mkdir()

    coach_csv = tel_dir / "18_43_practice_coach.csv"
    coach_csv.write_text(
        "Format,RaceTools CSV\n"
        "Driver name,KartSim Coach RS\n\n"
        "Time,Latitude,Longitude,Record,Lap,Lap Distance (m),Speed,Throttle,Brake\n"
        "10.0,52.275,-1.095,1,1,0.0,20.0,100.0,0.0\n"
        "15.0,52.276,-1.094,2,1,100.0,25.0,100.0,0.0\n"
    )

    student_csv = tel_dir / "18_43_practice_katia.csv"
    student_csv.write_text(
        "Format,RaceTools CSV\n"
        "Driver name,Katia Guseinova\n\n"
        "Time,Latitude,Longitude,Record,Lap,Lap Distance (m),Speed,Throttle,Brake\n"
        "10.0,52.275,-1.095,1,1,0.0,18.0,80.0,0.0\n"
        "16.0,52.276,-1.094,2,1,100.0,22.0,90.0,10.0\n"
    )

    class DummySession:
        meeting_dir = str(tmp_path)
        session_id = "18_43_practice"
        league = "kartsim"
        class_name = ["cadet"]
        date = "2026-11-05"
        track_name = "Whilton Mill"

    monkeypatch.setattr("race_db.RaceDB.find_sessions", lambda self, **kwargs: [DummySession()])

    resolved_coach = image_tools.find_telemetry_csv(
        "18_43_practice", "Whilton Mill", "2026-11-05", driver_name="kartsim coach rs"
    )
    resolved_katia = image_tools.find_telemetry_csv(
        "18_43_practice", "Whilton Mill", "2026-11-05", driver_name="Katia Guseinova"
    )
    assert resolved_coach == str(coach_csv)
    assert resolved_katia == str(student_csv)

    # Partial/substring names must NOT match (full driver name required)
    with pytest.raises(ValueError, match="Available drivers: \\['KartSim Coach RS', 'Katia Guseinova'\\]"):
        image_tools.find_telemetry_csv(
            "18_43_practice", "Whilton Mill", "2026-11-05", driver_name="Coach"
        )

    captured_labels: dict[int | str, str] = {}
    orig_render = image_tools.render_stacked_telemetry_plot

    def spy_render(*args: object, **kwargs: object) -> str:
        lbls = kwargs.get("labels")
        if isinstance(lbls, dict):
            captured_labels.update(lbls)
        return orig_render(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(image_tools, "render_stacked_telemetry_plot", spy_render)

    tel_img = server.get_telemetry_plot(
        laps=[
            ("2026-11-05", "Whilton Mill", "18_43_practice", 1, "KartSim Coach RS"),
            {
                "date": "2026-11-05",
                "track": "Whilton Mill",
                "session_id": "18_43_practice",
                "lap": 1,
                "driver_name": "Katia Guseinova"
            }
        ],
        start_m=0.0,
        end_m=100.0,
        channels=["Speed", "Delta Time"]
    )
    assert isinstance(tel_img, Image)
    assert tel_img.data is not None and tel_img.data.startswith(b'\x89PNG\r\n\x1a\n')
    label_vals = list(captured_labels.values())
    assert "KartSim Coach RS - Lap 1 (18_43_practice)" in label_vals
    assert "Katia Guseinova - Lap 1 (18_43_practice)" in label_vals

    traj_img = server.get_trajectory_plot(
        laps=[
            ("2026-11-05", "Whilton Mill", "18_43_practice", 1, "KartSim Coach RS"),
            {
                "date": "2026-11-05",
                "track": "Whilton Mill",
                "session_id": "18_43_practice",
                "lap": 1,
                "driver_name": "Katia Guseinova"
            }
        ],
        start_m=0.0,
        end_m=100.0,
        color_mode="delta_t"
    )
    assert isinstance(traj_img, Image)
    assert traj_img.data is not None and traj_img.data.startswith(b'\x89PNG\r\n\x1a\n')


def test_get_pace_summary_multi_driver_and_reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tel_dir = tmp_path / "telemetry"
    tel_dir.mkdir()

    coach_csv = tel_dir / "18_43_practice_coach.csv"
    coach_csv.write_text(
        "Format,RaceTools CSV\n"
        "Driver name,KartSim Coach RS\n\n"
        "Time,Latitude,Longitude,Record,Lap,Lap Distance (m),Speed\n"
        "2026-11-05T18:43:00.000Z,52.275,-1.095,1,1,0.0,22.0\n"
        "2026-11-05T18:43:02.500Z,52.275,-1.095,2,1,50.0,18.0\n"
        "2026-11-05T18:43:05.000Z,52.275,-1.095,3,1,100.0,24.0\n"
        "2026-11-05T18:43:10.000Z,52.275,-1.095,4,1,200.0,25.0\n"
        "2026-11-05T18:43:10.000Z,52.275,-1.095,5,2,0.0,22.0\n"
        "2026-11-05T18:43:12.400Z,52.275,-1.095,6,2,50.0,19.0\n"
        "2026-11-05T18:43:14.800Z,52.275,-1.095,7,2,100.0,24.5\n"
        "2026-11-05T18:43:19.500Z,52.275,-1.095,8,2,200.0,25.5\n"
    )

    student_csv = tel_dir / "18_43_practice_katia.csv"
    student_csv.write_text(
        "Format,RaceTools CSV\n"
        "Driver name,Katia Guseinova\n\n"
        "Time,Latitude,Longitude,Record,Lap,Lap Distance (m),Speed\n"
        "2026-11-05T18:43:00.000Z,52.275,-1.095,1,1,0.0,20.0\n"
        "2026-11-05T18:43:02.800Z,52.275,-1.095,2,1,50.0,15.0\n"
        "2026-11-05T18:43:05.600Z,52.275,-1.095,3,1,100.0,21.0\n"
        "2026-11-05T18:43:11.200Z,52.275,-1.095,4,1,200.0,23.0\n"
        "2026-11-05T18:43:11.200Z,52.275,-1.095,5,2,0.0,20.5\n"
        "2026-11-05T18:43:13.900Z,52.275,-1.095,6,2,50.0,15.5\n"
        "2026-11-05T18:43:16.600Z,52.275,-1.095,7,2,100.0,21.5\n"
        "2026-11-05T18:43:22.000Z,52.275,-1.095,8,2,200.0,23.5\n"
    )

    class DummySession:
        meeting_dir = str(tmp_path)
        meeting_folder = "2026-11-05_whilton"
        session_id = "18_43_practice"
        league = "kartsim"
        class_name = ["cadet"]
        date = "2026-11-05"
        track_name = "Whilton Mill"

    mock_track = {
        "lap_length": 200.0,
        "sector_end": [100.0, 200.0],
        "turns": [
            {"name": "Christmas", "start": 0.0, "apex": [50.0], "end": 100.0},
            {"name": "The Boot", "start": 100.0, "apex": [150.0], "end": 200.0},
        ]
    }

    monkeypatch.setattr("race_db.RaceDB.find_sessions", lambda self, **kwargs: [DummySession()])
    monkeypatch.setattr("race_db.RaceDB.get_track", lambda self, track: mock_track)
    monkeypatch.setattr(
        "race_db.RaceDB.get_session_drivers",
        lambda self, sid, mf=None: ["KartSim Coach RS", "Katia Guseinova"]
    )
    monkeypatch.setattr("race_db.RaceDB.load", lambda self, **kwargs: pd.DataFrame())
    monkeypatch.setattr(lap_tools, "is_lap_valid", lambda dists, turns, length: True)

    # 1. Explicit cross-driver comparison using case-insensitive full driver names
    cross = server.get_pace_summary(
        track="Whilton Mill",
        date="2026-11-05",
        session_id="18_43_practice",
        driver_name="katia guseinova",
        reference_driver_name="kartsim coach rs"
    )
    assert cross["driver_name"] == "Katia Guseinova"
    assert cross["reference_driver_name"] == "KartSim Coach RS"
    assert cross["driver_best_lap"]["lap_number"] == 2
    assert cross["driver_best_lap"]["lap_time_s"] == pytest.approx(10.8, abs=0.01)
    assert cross["reference_best_lap"]["lap_number"] == 2
    assert cross["reference_best_lap"]["lap_time_s"] == pytest.approx(9.5, abs=0.01)
    assert cross["total_best_lap_gap_s"] == pytest.approx(1.3, abs=0.01)
    assert len(cross["turn_comparison"]) == 2
    for tc in cross["turn_comparison"]:
        assert tc["best_lap_delta_s"] > 0
        assert tc["median_delta_s"] > 0
        assert tc["vmin_delta_kmh"] < 0

    # Partial/substring driver_name must not match
    with pytest.raises(ValueError, match="No valid telemetry data found"):
        server.get_pace_summary(
            track="Whilton Mill",
            date="2026-11-05",
            session_id="18_43_practice",
            driver_name="Katia"
        )

    # 2. Explicit reference_lap selection
    cross_lap1 = server.get_pace_summary(
        track="Whilton Mill",
        date="2026-11-05",
        session_id="18_43_practice",
        driver_name="Katia Guseinova",
        reference_driver_name="KartSim Coach RS",
        reference_lap=1
    )
    assert cross_lap1["reference_best_lap"]["lap_number"] == 1
    assert cross_lap1["reference_best_lap"]["lap_time_s"] == pytest.approx(10.0, abs=0.01)

    # 3. Omitting driver_name on a multi-driver session returns per-driver breakdown + comparisons
    multi = server.get_pace_summary(
        track="Whilton Mill",
        date="2026-11-05",
        session_id="18_43_practice"
    )
    assert "drivers" in multi
    assert set(multi["drivers"].keys()) == {"KartSim Coach RS", "Katia Guseinova"}
    assert multi["fastest_driver"] == "KartSim Coach RS"
    assert "Katia Guseinova" in multi["comparisons"]
    assert multi["comparisons"]["Katia Guseinova"]["total_best_lap_gap_s"] == pytest.approx(1.3, abs=0.01)


def test_long_pit_wait_lap_marked_invalid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tel_dir = tmp_path / "telemetry"
    tel_dir.mkdir()

    # Lap 1: 10s, Lap 2: 11s, Lap 3: 500s (> 10x median of ~11s)
    csv_file = tel_dir / "18_43_practice_coach.csv"
    csv_file.write_text(
        "Format,RaceTools CSV\n"
        "Driver name,KartSim Coach RS\n\n"
        "Time,Latitude,Longitude,Record,Lap,Lap Distance (m),Speed\n"
        "2026-11-05T18:43:00.000Z,52.275,-1.095,1,1,0.0,20.0\n"
        "2026-11-05T18:43:10.000Z,52.275,-1.095,2,1,200.0,22.0\n"
        "2026-11-05T18:43:10.000Z,52.275,-1.095,3,2,0.0,20.0\n"
        "2026-11-05T18:43:21.000Z,52.275,-1.095,4,2,200.0,22.0\n"
        "2026-11-05T18:43:21.000Z,52.275,-1.095,5,3,0.0,20.0\n"
        "2026-11-05T18:51:41.000Z,52.275,-1.095,6,3,200.0,22.0\n"
    )

    class DummySession:
        meeting_dir = str(tmp_path)
        meeting_folder = "2026-11-05_whilton"
        session_id = "18_43_practice"
        league = "kartsim"
        class_name = ["cadet"]
        date = "2026-11-05"
        track_name = "Whilton Mill"

    mock_track = {
        "lap_length": 200.0,
        "sector_end": [100.0, 200.0],
        "turns": [{"name": "Christmas", "start": 0.0, "apex": [50.0], "end": 100.0}]
    }

    monkeypatch.setattr("race_db.RaceDB.find_sessions", lambda self, **kwargs: [DummySession()])
    monkeypatch.setattr("race_db.RaceDB.get_track", lambda self, track: mock_track)
    monkeypatch.setattr("race_db.RaceDB.get_session_drivers", lambda self, sid, mf=None: ["KartSim Coach RS"])
    monkeypatch.setattr("race_db.RaceDB.load", lambda self, **kwargs: pd.DataFrame())
    monkeypatch.setattr(lap_tools, "is_lap_valid", lambda dists, turns, length: True)

    stats = json.loads(server.get_stats(track="Whilton Mill", date="2026-11-05", session_id="18_43_practice"))
    lap_nums = [s["lap"] for s in stats]
    assert lap_nums == [1, 2]
    assert 3 not in lap_nums
