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

"""Columnar telemetry container (.brtc) reader, writer, and zero-copy channel server.

Binary layout of a .brtc file:
  - Header (8 bytes): b"BRTC\\x01\\x00\\x00\\x00"
  - Column Payloads: Concatenated Brotli-compressed multi-block <offset, scale, count, deltas[]>
    payloads, identical to the /api/telemetry/channel wire format.
  - Manifest Payload: Brotli-compressed UTF-8 JSON manifest describing metadata, column byte
    offsets/lengths, optional lap index, and optional derived calculator provenance.
  - Footer (16 bytes): <manifest_offset: uint64_le, manifest_length: uint32_le, magic: b"BRTC">
"""

from __future__ import annotations

import json
import os
import struct
import threading
import uuid
from typing import Any

import brotli  # type: ignore[import-untyped]
import numpy as np

import _telemetry_native

BRTC_MAGIC = b'BRTC'
BRTC_VERSION = 1
BRTC_HEADER = b'BRTC\x01\x00\x00\x00'
BRTC_HEADER_SIZE = 8
BRTC_FOOTER_STRUCT = struct.Struct('<QI4s')
BRTC_FOOTER_SIZE = BRTC_FOOTER_STRUCT.size  # 16 bytes

DEFAULT_TIME_ENCODE_SCALE = 0.0001
CORE_ALWAYS_ACTIVE_COLUMNS = frozenset({'Record', 'Time', 'Latitude', 'Longitude', 'Lap'})

_MANIFEST_CACHE_LOCK = threading.Lock()
_MANIFEST_CACHE: dict[str, tuple[int, int, dict[str, Any]]] = {}
_MANIFEST_CACHE_MAX_ENTRIES = 512


def _column_has_active_data(col_name: str, values: np.ndarray) -> bool:
    """Return True if column is a core column or has at least one finite non-zero sample."""
    if col_name in CORE_ALWAYS_ACTIVE_COLUMNS:
        return True
    if values.size == 0:
        return False
    finite_mask = np.isfinite(values)
    if not np.any(finite_mask):
        return False
    return bool(np.any(values[finite_mask] != 0.0))


def resolve_column_name(manifest: dict[str, Any], requested_name: str) -> str | None:
    """Resolve exact or case-insensitive column name inside a .brtc manifest."""
    cols = manifest.get('columns', {})
    if requested_name in cols:
        return requested_name
    req_lower = requested_name.lower()
    for name in manifest.get('column_order', list(cols.keys())):
        if str(name).lower() == req_lower and name in cols:
            return str(name)
    for name in cols:
        if str(name).lower() == req_lower:
            return str(name)
    return None


def write_brtc(
    path: str,
    columns: dict[str, np.ndarray],
    metadata: dict[str, Any] | None = None,
    column_order: list[str] | None = None,
    preferred_scales: dict[str, float] | None = None,
    laps: list[dict[str, Any]] | None = None,
    calculators: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Atomically write a .brtc columnar file and return its manifest dictionary."""
    parent_dir = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent_dir, exist_ok=True)

    ordered_names = list(column_order) if column_order is not None else list(columns.keys())
    for k in columns:
        if k not in ordered_names:
            ordered_names.append(k)

    row_count = 0
    for name in ordered_names:
        if name in columns:
            arr_len = int(columns[name].shape[0])
            if row_count == 0:
                row_count = arr_len
            elif arr_len != row_count:
                raise ValueError(
                    f'Column {name!r} has length {arr_len}, expected {row_count} in {path}'
                )

    scales = preferred_scales or {}
    encoded_blobs: list[tuple[str, bytes, bool]] = []
    for name in ordered_names:
        if name not in columns:
            continue
        arr = np.ascontiguousarray(columns[name], dtype=np.float64)
        pref_scale = float(scales.get(name, 0.0))
        if pref_scale <= 0.0 and name == 'Time':
            pref_scale = DEFAULT_TIME_ENCODE_SCALE
        blob: bytes = _telemetry_native.encode_channel_blocks(arr, pref_scale)
        has_data = _column_has_active_data(name, arr)
        encoded_blobs.append((name, blob, has_data))

    col_entries: dict[str, dict[str, Any]] = {}
    current_offset = BRTC_HEADER_SIZE
    final_order: list[str] = []
    for name, blob, has_data in encoded_blobs:
        blob_len = len(blob)
        col_entries[name] = {
            'offset': current_offset,
            'length': blob_len,
            'has_data': has_data,
        }
        current_offset += blob_len
        final_order.append(name)

    manifest: dict[str, Any] = {
        'version': BRTC_VERSION,
        'row_count': row_count,
        'metadata': dict(metadata) if metadata else {},
        'column_order': final_order,
        'columns': col_entries,
    }
    if laps is not None:
        manifest['laps'] = laps
    if calculators is not None:
        manifest['calculators'] = calculators

    manifest_json_bytes = json.dumps(manifest, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    manifest_compressed: bytes = brotli.compress(manifest_json_bytes, quality=4)
    manifest_offset = current_offset
    manifest_length = len(manifest_compressed)
    footer = BRTC_FOOTER_STRUCT.pack(manifest_offset, manifest_length, BRTC_MAGIC)

    tmp_path = f'{path}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}'
    try:
        with open(tmp_path, 'wb') as f:
            f.write(BRTC_HEADER)
            for _, blob, _ in encoded_blobs:
                f.write(blob)
            f.write(manifest_compressed)
            f.write(footer)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    invalidate_manifest_cache(path)
    return manifest


def invalidate_manifest_cache(path: str | None = None) -> None:
    """Invalidate cached .brtc manifest(s)."""
    with _MANIFEST_CACHE_LOCK:
        if path is None:
            _MANIFEST_CACHE.clear()
        else:
            real = os.path.realpath(path)
            _MANIFEST_CACHE.pop(real, None)


def read_brtc_manifest(path: str) -> dict[str, Any]:
    """Read and cache the JSON manifest from the footer of a .brtc file."""
    real = os.path.realpath(path)
    st = os.stat(real)
    file_size = st.st_size
    mtime_ns = st.st_mtime_ns

    if file_size < BRTC_HEADER_SIZE + BRTC_FOOTER_SIZE:
        raise ValueError(f'Invalid .brtc file (too small): {path}')

    with _MANIFEST_CACHE_LOCK:
        cached = _MANIFEST_CACHE.get(real)
        if cached is not None and cached[0] == mtime_ns and cached[1] == file_size:
            return cached[2]

    with open(real, 'rb') as f:
        header = f.read(BRTC_HEADER_SIZE)
        if header[:4] != BRTC_MAGIC:
            raise ValueError(f'Invalid .brtc header magic in {path}')
        version = header[4]
        if version != BRTC_VERSION:
            raise ValueError(f'Unsupported .brtc version {version} in {path}')

        f.seek(file_size - BRTC_FOOTER_SIZE)
        footer_bytes = f.read(BRTC_FOOTER_SIZE)
        manifest_offset, manifest_length, footer_magic = BRTC_FOOTER_STRUCT.unpack(footer_bytes)
        if footer_magic != BRTC_MAGIC:
            raise ValueError(f'Invalid .brtc footer magic in {path}')
        if manifest_offset < BRTC_HEADER_SIZE or manifest_offset + manifest_length + BRTC_FOOTER_SIZE > file_size:
            raise ValueError(f'Corrupt .brtc manifest bounds in {path}')

        f.seek(manifest_offset)
        manifest_compressed = f.read(manifest_length)

    manifest_raw = brotli.decompress(manifest_compressed)
    manifest: dict[str, Any] = json.loads(manifest_raw.decode('utf-8'))

    with _MANIFEST_CACHE_LOCK:
        if len(_MANIFEST_CACHE) >= _MANIFEST_CACHE_MAX_ENTRIES:
            _MANIFEST_CACHE.clear()
        _MANIFEST_CACHE[real] = (mtime_ns, file_size, manifest)

    return manifest


def read_brtc_column(
    path: str,
    channel_name: str,
    manifest: dict[str, Any] | None = None,
) -> np.ndarray | None:
    """Decode a single column from a .brtc file into a float64 numpy array, or None if absent."""
    if manifest is None:
        manifest = read_brtc_manifest(path)
    resolved = resolve_column_name(manifest, channel_name)
    if resolved is None:
        return None
    col_info = manifest['columns'][resolved]
    offset = int(col_info['offset'])
    length = int(col_info['length'])
    if length == 0:
        return np.zeros(0, dtype=np.float64)
    with open(path, 'rb') as f:
        f.seek(offset)
        payload = f.read(length)
    arr: np.ndarray = _telemetry_native.decode_channel_blocks(payload)
    return arr


def read_brtc_columns(
    path: str,
    channel_names: list[str] | None = None,
    manifest: dict[str, Any] | None = None,
) -> dict[str, np.ndarray]:
    """Decode multiple (or all) columns from a .brtc file in a single file open."""
    if manifest is None:
        manifest = read_brtc_manifest(path)
    cols_meta = manifest.get('columns', {})
    if channel_names is None:
        targets = [(name, name) for name in manifest.get('column_order', list(cols_meta.keys())) if name in cols_meta]
    else:
        targets = []
        for req in channel_names:
            res = resolve_column_name(manifest, req)
            if res is not None:
                targets.append((req, res))

    out: dict[str, np.ndarray] = {}
    if not targets:
        return out

    with open(path, 'rb') as f:
        for out_key, col_key in targets:
            col_info = cols_meta[col_key]
            offset = int(col_info['offset'])
            length = int(col_info['length'])
            if length == 0:
                out[out_key] = np.zeros(0, dtype=np.float64)
            else:
                f.seek(offset)
                payload = f.read(length)
                out[out_key] = _telemetry_native.decode_channel_blocks(payload)
    return out


def serve_brtc_column(
    path: str,
    channel_name: str,
    is_speed: bool = False,
    should_smooth: bool = False,
    manifest: dict[str, Any] | None = None,
) -> bytes | None:
    """Return Brotli-compressed multi-block wire bytes for channel_name from path, or None if absent."""
    if manifest is None:
        manifest = read_brtc_manifest(path)
    resolved = resolve_column_name(manifest, channel_name)
    if resolved is None:
        return None
    col_info = manifest['columns'][resolved]
    offset = int(col_info['offset'])
    length = int(col_info['length'])
    payload: bytes = _telemetry_native.serve_brtc_channel(
        path, offset, length, bool(is_speed), bool(should_smooth)
    )
    return payload
