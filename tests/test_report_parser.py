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
import tempfile
import pytest
import report_parser


def test_parse_report_content_json_comment() -> None:
    raw = """<!-- {
  "title": "Turn 4 Hairpin Analysis",
  "league": "kartsim",
  "class_name": "cadet",
  "date": "2026-08-21",
  "track": "Clay Pigeon",
  "session_id": "18_09_practice",
  "state": {
    "tab": "map",
    "sort": "turn",
    "turn": 2,
    "lapsA": ["lap-1"],
    "lapsB": ["lap-2"],
    "xlim": [100.0, 200.0],
    "delta": true,
    "speed": true
  }
} -->
<div class="report-body">
  <h2>Turn 4 Analysis</h2>
  <p>Analysis text here.</p>
</div>
"""
    meta, state, body = report_parser.parse_report_content(raw)
    assert meta['title'] == "Turn 4 Hairpin Analysis"
    assert meta['league'] == "kartsim"
    assert meta['class_name'] == "cadet"
    assert meta['date'] == "2026-08-21"
    assert meta['track'] == "Clay Pigeon"
    assert meta['session_id'] == "18_09_practice"
    assert state['tab'] == "map"
    assert state['sort'] == "turn"
    assert state['turn'] == 2
    assert state['lapsA'] == ["lap-1"]
    assert state['lapsB'] == ["lap-2"]
    assert state['xlim'] == [100.0, 200.0]
    assert state['delta'] is True
    assert '<div class="report-body">' in body
    assert '<h2>Turn 4 Analysis</h2>' in body


def test_parse_report_content_telemetry_comment_prefix() -> None:
    raw = """<!--telemetry_report {
  "title": "Hook Turn Analysis",
  "league": "club100",
  "class_name": "cadet_lw",
  "date": "2026-08-02",
  "track": "Llandow"
} -->
<p>Report content</p>
"""
    meta, state, body = report_parser.parse_report_content(raw)
    assert meta['title'] == "Hook Turn Analysis"
    assert meta['league'] == "club100"
    assert meta['class_name'] == "cadet_lw"
    assert '<p>Report content</p>' in body


def test_parse_report_content_frontmatter_json() -> None:
    raw = """---
{
  "title": "Frontmatter Report",
  "league": "kartsim",
  "class_name": "x30",
  "date": "2026-06-22",
  "track": "Whilton Mill"
}
---
<div>Body</div>
"""
    meta, state, body = report_parser.parse_report_content(raw)
    assert meta['title'] == "Frontmatter Report"
    assert meta['track'] == "Whilton Mill"
    assert '<div>Body</div>' in body


def test_parse_report_content_script_tag() -> None:
    raw = """<script type="application/json" id="telemetry-report-data">
{
  "title": "Embedded Script Report",
  "league": "kartsim",
  "class_name": "x30",
  "date": "2026-06-22",
  "track": "Whilton Mill"
}
</script>
<div>Body content</div>
"""
    meta, state, body = report_parser.parse_report_content(raw)
    assert meta['title'] == "Embedded Script Report"
    assert '<div>Body content</div>' in body


def test_parse_report_content_no_metadata() -> None:
    raw = "<div>Plain HTML report without header</div>"
    meta, state, body = report_parser.parse_report_content(raw)
    assert meta == {}
    assert state == {}
    assert body == "<div>Plain HTML report without header</div>"


def test_resolve_and_load_report() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        report_file = os.path.join(tmpdir, "test_turn_report.html")
        with open(report_file, "w", encoding="utf-8") as f:
            f.write(
                '<!-- {"title": "Test Report", "league": "kartsim", '
                '"class_name": "x30", "date": "2026-05-10", "track": "Rowrah"} -->\n'
                '<p>Test content</p>'
            )

        resolved = report_parser.resolve_report_path("test_turn_report", reports_dir=tmpdir)
        assert resolved == report_file

        resolved_with_ext = report_parser.resolve_report_path("test_turn_report.html", reports_dir=tmpdir)
        assert resolved_with_ext == report_file

        # Non-existent
        assert report_parser.resolve_report_path("non_existent", reports_dir=tmpdir) is None

        # Directory traversal prevention
        assert report_parser.resolve_report_path("../../../etc/passwd", reports_dir=tmpdir) is None

        # Load report
        loaded = report_parser.load_report("test_turn_report", reports_dir=tmpdir)
        assert loaded is not None
        meta, state, body = loaded
        assert meta['title'] == "Test Report"
        assert meta['track'] == "Rowrah"
        assert "<p>Test content</p>" in body


def test_parse_report_content_in_head_after_doctype() -> None:
    raw = """<!DOCTYPE html>
<html>
<head>
  <!-- {
    "title": "Turn Analysis",
    "league": "kartsim",
    "class_name": "cadet",
    "date": "2026-10-03",
    "track": "Whilton Mill",
    "session_id": "10_17_practice"
  } -->
</head>
<body><p>Content</p></body>
</html>"""
    meta, state, body = report_parser.parse_report_content(raw)
    assert meta['track'] == "Whilton Mill"
    assert meta['session_id'] == "10_17_practice"
    assert "<p>Content</p>" in body


def test_validate_report_content() -> None:
    # Empty content
    with pytest.raises(ValueError, match="cannot be empty"):
        report_parser.validate_report_content("")

    # No metadata
    with pytest.raises(ValueError, match="metadata could not be parsed"):
        report_parser.validate_report_content("<div>No metadata</div>")

    # Missing required fields
    incomplete = '<!-- {"title": "Only Title"} -->\n<p>Content</p>'
    with pytest.raises(ValueError, match="missing required field"):
        report_parser.validate_report_content(incomplete)

    # Valid single-session report
    valid_single = (
        '<!-- {"title": "Test", "league": "kartsim", "class_name": "cadet", '
        '"date": "2026-10-03", "track": "Whilton Mill"} -->\n<p>Content</p>'
    )
    meta = report_parser.validate_report_content(valid_single)
    assert meta['track'] == "Whilton Mill"

    # Valid multi-session report
    valid_multi = (
        '<!-- {"title": "Multi", "track": "Lydd", "sessions": ["kartsim/cadet/2026-08-21/Lydd/1"]} -->\n'
        '<p>Content</p>'
    )
    meta_multi = report_parser.validate_report_content(valid_multi)
    assert meta_multi['title'] == "Multi"
