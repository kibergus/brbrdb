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

"""Implementation functions for report management MCP tools (save, read, edit)."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from flask import has_request_context

from database import config
import auth
import report_parser


def get_reports_dir() -> str:
    """Return the absolute path to the reports storage directory."""
    data_dir = config.get('data_dir', '')
    if data_dir:
        return os.path.join(data_dir, 'reports')
    return report_parser.WORKSPACE_REPORTS_DIR


def _get_authors_file() -> str:
    """Return the path to the report authors metadata JSON file."""
    return os.path.join(get_reports_dir(), '.report_authors.json')


def _load_authors() -> dict[str, dict[str, Any]]:
    """Load the mapping of report UUIDs to author metadata."""
    path = _get_authors_file()
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _save_authors(authors: dict[str, dict[str, Any]]) -> None:
    """Save the authors mapping to disk atomically."""
    reports_dir = get_reports_dir()
    os.makedirs(reports_dir, exist_ok=True)
    path = _get_authors_file()
    temp_path = f"{path}.tmp.{uuid.uuid4().hex}"
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(authors, f, indent=2)
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise


def validate_uuid(report_id: str) -> str:
    """Normalize and validate that report_id is a valid UUID.

    Strips any trailing .html or .htm extension. Raises ValueError if invalid.
    """
    clean = report_id.strip()
    if clean.endswith('.html'):
        clean = clean[:-5]
    elif clean.endswith('.htm'):
        clean = clean[:-4]

    try:
        u = uuid.UUID(clean)
        return str(u)
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"Invalid report_id '{report_id}': must be a valid UUID")


def _check_manage_permission() -> tuple[str, str]:
    """Check if the caller has manage_reports permission in their ACL.

    Returns (author_key, author_name).
    """
    if has_request_context():
        acl = auth.get_current_acl()
        if not auth.can_manage_reports(acl):
            raise PermissionError("Permission denied: current user does not have permission to manage reports.")
    return auth.get_current_author()


def _check_author_permission(clean_id: str, author_key: str) -> None:
    """Ensure that the caller is the author of the specified report."""
    authors = _load_authors()
    record = authors.get(clean_id)
    if record:
        owner_key = record.get('author_key')
        is_admin = author_key in ('localhost', 'ba9d82ef-9d53-4a46-81fa-72c6da096ba5')
        if owner_key and owner_key != author_key and not is_admin:
            raise PermissionError(f"Permission denied: only the report author can edit report '{clean_id}'.")


def save_report_impl(content: str, report_id: str | None = None) -> dict[str, Any]:
    """Save or create a report file.

    If report_id is empty, a new UUID is generated.
    If report_id is provided, it must be a valid UUID; existing reports can only be edited by their author.
    """
    author_key, author_name = _check_manage_permission()

    if not report_id or not report_id.strip():
        clean_id = str(uuid.uuid4())
        is_new = True
    else:
        clean_id = validate_uuid(report_id)
        report_file = os.path.join(get_reports_dir(), f"{clean_id}.html")
        is_new = not os.path.exists(report_file)

    if not is_new:
        _check_author_permission(clean_id, author_key)

    # Validate report content before saving to ensure it can be parsed and rendered
    report_parser.validate_report_content(content)

    reports_dir = get_reports_dir()
    os.makedirs(reports_dir, exist_ok=True)
    report_file = os.path.join(reports_dir, f"{clean_id}.html")

    temp_file = os.path.join(reports_dir, f".{clean_id}.html.tmp.{uuid.uuid4().hex}")
    with open(temp_file, 'w', encoding='utf-8') as f:
        f.write(content)
    os.replace(temp_file, report_file)

    # Record author metadata
    authors = _load_authors()
    now_iso = datetime.now(timezone.utc).isoformat()
    if clean_id not in authors:
        authors[clean_id] = {
            'author_key': author_key,
            'author_name': author_name,
            'created_at': now_iso,
            'updated_at': now_iso
        }
    else:
        authors[clean_id]['updated_at'] = now_iso
    _save_authors(authors)

    return {
        'status': 'success',
        'report_id': clean_id,
        'filename': f"{clean_id}.html",
        'url': f"https://brbrdb.brbrkitten.com/telemetry/report/{clean_id}"
    }


def read_report_impl(report_id: str, start_line: int | None = None, end_line: int | None = None) -> str:
    """Read content from an existing report, optionally filtered by line range (1-indexed)."""
    clean_input = report_id.strip()
    if '..' in clean_input or '/' in clean_input or '\\' in clean_input:
        raise ValueError(f"Invalid report_id '{report_id}': path traversal characters not permitted")

    try:
        clean_id = validate_uuid(clean_input)
        file_path = os.path.join(get_reports_dir(), f"{clean_id}.html")
    except ValueError:
        # Fall back to resolving via report_parser for legacy named reports
        resolved = report_parser.resolve_report_path(clean_input, reports_dir=get_reports_dir())
        if not resolved:
            raise FileNotFoundError(f"Report '{report_id}' not found")
        file_path = resolved

    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Report '{report_id}' not found")

    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    total_lines = len(lines)
    s = 1 if start_line is None else max(1, start_line)
    e = total_lines if end_line is None else min(total_lines, end_line)

    if s > e or s > total_lines:
        return ""

    return "".join(lines[s - 1:e])


def edit_report_impl(
    report_id: str,
    target_content: str,
    replacement_content: str,
    start_line: int | None = None,
    end_line: int | None = None,
    allow_multiple: bool = False
) -> str:
    """Perform targeted replacement on an existing report file.

    Requires manage_reports permission and author ownership.
    """
    author_key, author_name = _check_manage_permission()
    clean_id = validate_uuid(report_id)
    _check_author_permission(clean_id, author_key)

    file_path = os.path.join(get_reports_dir(), f"{clean_id}.html")
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Report '{clean_id}' not found")

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    if start_line is not None or end_line is not None:
        lines = content.splitlines(keepends=True)
        total_lines = len(lines)
        s = 1 if start_line is None else max(1, start_line)
        e = total_lines if end_line is None else min(total_lines, end_line)

        prefix = "".join(lines[:s - 1])
        target_range = "".join(lines[s - 1:e])
        suffix = "".join(lines[e:])

        count = target_range.count(target_content)
        if count == 0:
            raise ValueError(f"Target content not found between lines {s} and {e}")
        if count > 1 and not allow_multiple:
            raise ValueError(
                f"Target content found {count} times between lines {s} and {e}; "
                "use allow_multiple=True to replace all occurrences"
            )

        new_target_range = target_range.replace(target_content, replacement_content, -1 if allow_multiple else 1)
        new_content = prefix + new_target_range + suffix
    else:
        count = content.count(target_content)
        if count == 0:
            raise ValueError("Target content not found in report")
        if count > 1 and not allow_multiple:
            raise ValueError(
                f"Target content found {count} times in report; "
                "use allow_multiple=True to replace all occurrences"
            )

        new_content = content.replace(target_content, replacement_content, -1 if allow_multiple else 1)

    # Validate that edited content still has parseable and valid metadata
    report_parser.validate_report_content(new_content)

    temp_file = os.path.join(get_reports_dir(), f".{clean_id}.html.tmp.{uuid.uuid4().hex}")
    with open(temp_file, 'w', encoding='utf-8') as f:
        f.write(new_content)
    os.replace(temp_file, file_path)

    authors = _load_authors()
    if clean_id in authors:
        authors[clean_id]['updated_at'] = datetime.now(timezone.utc).isoformat()
        _save_authors(authors)

    return f"Report '{clean_id}' updated successfully ({count} replacement{'s' if count != 1 else ''})."
