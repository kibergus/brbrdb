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

import base64
from collections.abc import Iterator
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.request

import pytest

SIDECAR_DIR = (
    Path(__file__).resolve().parent.parent
    / ".agents"
    / "plugins"
    / "brbrtelemetry"
    / "sidecars"
    / "telemetry-viewer"
)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _http_request(
    url: str,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    req_headers: dict[str, str] = dict(headers or {})
    data_bytes: bytes | None = None
    if body is not None:
        data_bytes = json.dumps(body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data_bytes, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return int(resp.status), resp_headers, resp.read()
    except urllib.error.HTTPError as exc:
        err_headers = {k.lower(): v for k, v in exc.headers.items()}
        return int(exc.code), err_headers, exc.read()


class _SimpleWsClient:
    """Minimal stdlib RFC 6455 client for testing sidecar WebSocket broadcasts."""

    def __init__(self, port: int, path: str = "/ws") -> None:
        self._sock = socket.create_connection(("127.0.0.1", port), timeout=5.0)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(handshake.encode("ascii"))
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket handshake closed prematurely")
            buf += chunk
        header_part, remainder = buf.split(b"\r\n\r\n", 1)
        if b"101" not in header_part.splitlines()[0]:
            raise RuntimeError(f"Unexpected WS status: {header_part!r}")
        self._buf = remainder

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise RuntimeError("Socket closed while reading WS frame")
            self._buf += chunk
        out = self._buf[:n]
        self._buf = self._buf[n:]
        return out

    def recv_json(self, timeout: float = 4.0) -> dict[str, Any]:
        self._sock.settimeout(timeout)
        while True:
            hdr = self._read_exact(2)
            opcode = hdr[0] & 0x0F
            length = hdr[1] & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read_exact(8))[0]
            payload = self._read_exact(length)
            if opcode == 0x1:
                parsed: dict[str, Any] = json.loads(payload.decode("utf-8"))
                return parsed

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.fixture(params=["python", "node"])
def sidecar_instance(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Iterator[tuple[str, int, Path]]:
    runtime = str(request.param)
    if runtime == "node" and shutil.which("node") is None:
        pytest.skip("Node.js not installed")

    port = _find_free_port()
    workspace_dir = tmp_path / "workspace"
    studies_dir = workspace_dir / "studies"
    studies_dir.mkdir(parents=True)
    state_file = tmp_path / "companion_state.json"

    env = os.environ.copy()
    env["ANTIGRAVITY_SIDECAR_WEB_PORT"] = str(port)
    env["BRBR_COMPANION_WS_PORT"] = str(port)
    env["BRBR_WORKSPACE_DIR"] = str(workspace_dir)
    env["BRBR_STUDIES_DIR"] = str(studies_dir)
    env["BRBR_COMPANION_STATE_FILE"] = str(state_file)
    env["BRBR_COMPANION_LOG_FILE"] = str(tmp_path / "companion.log")

    cmd = (
        [sys.executable, str(SIDECAR_DIR / "server.py")]
        if runtime == "python"
        else ["node", str(SIDECAR_DIR / "server.mjs")]
    )
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for /health
    deadline = time.monotonic() + 8.0
    healthy = False
    while time.monotonic() < deadline:
        try:
            status, _, raw = _http_request(f"http://127.0.0.1:{port}/health")
            if status == 200 and json.loads(raw.decode("utf-8")).get("ok") is True:
                healthy = True
                break
        except Exception:
            time.sleep(0.05)

    if not healthy:
        proc.terminate()
        out, err = proc.communicate(timeout=3)
        raise RuntimeError(f"Sidecar ({runtime}) failed to start: {out!r} {err!r}")

    try:
        yield runtime, port, workspace_dir
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_sidecar_studies_manifest_cors_and_path_jailing(
    sidecar_instance: tuple[str, int, Path], tmp_path: Path
) -> None:
    _, port, workspace_dir = sidecar_instance
    base_url = f"http://127.0.0.1:{port}"
    studies_dir = workspace_dir / "studies"

    # Populate two separate studies
    study_a = studies_dir / "2026-04-12_buckmore_braking"
    (study_a / "overlays").mkdir(parents=True)
    (study_a / "channels").mkdir(parents=True)
    (study_a / "overlays" / "brake_markers.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "metadata": {"id": "brake_markers", "label": "Braking Markers", "render": "vector"},
                "features": [],
            }
        ),
        encoding="utf-8",
    )
    (study_a / "channels" / "trail_brake.json").write_text(
        json.dumps(
            {
                "schema": "brbr.custom_channel.v1",
                "channel_id": "trail_brake",
                "label": "Trail Brake",
                "unit": "%",
                "group": "braking",
                "series": [],
            }
        ),
        encoding="utf-8",
    )

    study_b = studies_dir / "2026-04-12_buckmore_eeg"
    (study_b / "overlays").mkdir(parents=True)
    (study_b / "overlays" / "theta_heat.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "metadata": {"id": "theta_heat", "label": "Theta Heatmap", "render": "heatmap"},
                "features": [],
            }
        ),
        encoding="utf-8",
    )

    # Create a secret file outside studies/ and a symlink inside studies/ pointing to it
    outside_secret = tmp_path / "outside_secret.geojson"
    outside_secret.write_text('{"secret": true}', encoding="utf-8")
    escape_link = study_a / "overlays" / "escape.geojson"
    escape_link.symlink_to(outside_secret)

    # 1. Preflight CORS + Private Network Access headers
    opt_status, opt_headers, _ = _http_request(
        f"{base_url}/api/workspace/manifest",
        method="OPTIONS",
        headers={"Access-Control-Request-Private-Network": "true"},
    )
    assert opt_status == 204
    assert opt_headers.get("access-control-allow-origin") == "*"
    assert opt_headers.get("access-control-allow-private-network") == "true"

    # 2. Study discovery
    st_status, st_headers, st_raw = _http_request(f"{base_url}/api/workspace/studies")
    assert st_status == 200
    assert st_headers.get("access-control-allow-private-network") == "true"
    studies_payload = json.loads(st_raw.decode("utf-8"))
    study_ids = {s["id"] for s in studies_payload["studies"]}
    assert "2026-04-12_buckmore_braking" in study_ids
    assert "2026-04-12_buckmore_eeg" in study_ids

    # 3. Scoped manifest + cross-study overlay reference
    man_url = (
        f"{base_url}/api/workspace/manifest"
        f"?study=2026-04-12_buckmore_braking"
        f"&overlays=brake_markers,2026-04-12_buckmore_eeg/overlays/theta_heat"
    )
    m_status, _, m_raw = _http_request(man_url)
    assert m_status == 200
    manifest = json.loads(m_raw.decode("utf-8"))
    assert manifest["study"] == "2026-04-12_buckmore_braking"
    ov_ids = {o["id"] for o in manifest["overlays"]}
    assert "brake_markers" in ov_ids
    assert "2026-04-12_buckmore_eeg/overlays/theta_heat" in ov_ids
    # Symlink escaping studies_dir must not appear in manifest
    assert "escape" not in ov_ids
    assert [c["channel_id"] for c in manifest["channels"]] == ["trail_brake"]
    assert [g["id"] for g in manifest["plot_groups"]] == ["braking"]

    # 4. Path jailing on /files/<subpath>: valid file 200, .. traversal 403, symlink escape 403
    ok_status, _, ok_bytes = _http_request(
        f"{base_url}/files/2026-04-12_buckmore_braking/overlays/brake_markers.geojson"
    )
    assert ok_status == 200
    assert json.loads(ok_bytes.decode("utf-8"))["metadata"]["id"] == "brake_markers"

    trav_status, _, _ = _http_request(f"{base_url}/files/%2e%2e/outside_secret.geojson")
    assert trav_status == 403

    sym_status, _, _ = _http_request(
        f"{base_url}/files/2026-04-12_buckmore_braking/overlays/escape.geojson"
    )
    assert sym_status == 403


def test_sidecar_mcp_tools_and_websocket_live_sync(
    sidecar_instance: tuple[str, int, Path]
) -> None:
    _, port, workspace_dir = sidecar_instance
    base_url = f"http://127.0.0.1:{port}"
    studies_dir = workspace_dir / "studies"

    ws = _SimpleWsClient(port)
    try:
        # Initial greeting frame
        ready_msg = ws.recv_json(timeout=4.0)
        assert ready_msg["type"] == "COMPANION_READY"

        # 1. MCP initialize & tools/list
        _, _, init_raw = _http_request(
            f"{base_url}/mcp",
            method="POST",
            body={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        init_res = json.loads(init_raw.decode("utf-8"))
        assert init_res["result"]["serverInfo"]["name"] == "brbrtelemetry-companion"

        _, _, list_raw = _http_request(
            f"{base_url}/mcp",
            method="POST",
            body={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        tool_names = {t["name"] for t in json.loads(list_raw.decode("utf-8"))["result"]["tools"]}
        assert tool_names == {
            "set_telemetry_view",
            "list_workspace_studies",
            "register_plot_group",
            "sync_workspace_data",
            "get_telemetry_view_status",
        }

        # 2. register_plot_group writes plot_groups.json and broadcasts workspace_sync + openPlot
        _, _, reg_raw = _http_request(
            f"{base_url}/mcp",
            method="POST",
            body={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "register_plot_group",
                    "arguments": {
                        "study": "2026-04-12_eeg",
                        "id": "eeg",
                        "label": "EEG",
                        "y_label": "Band Power (uV^2)",
                        "channels": [
                            {"channel_id": "eeg_alpha", "label": "Alpha", "color": "#a855f7"},
                            {"channel_id": "eeg_theta", "label": "Theta", "color": "#06b6d4"},
                        ],
                        "open_plot": True,
                    },
                },
            },
        )
        reg_res = json.loads(reg_raw.decode("utf-8"))
        assert reg_res["result"].get("isError") is not True
        pg_file = studies_dir / "2026-04-12_eeg" / "plot_groups.json"
        assert pg_file.is_file()

        received_types: set[str] = set()
        for _ in range(2):
            frame = ws.recv_json(timeout=4.0)
            received_types.add(str(frame.get("type", "")))
        assert "workspace_sync" in received_types
        assert "telemetry_jump" in received_types

        # 3. set_telemetry_view broadcasts 1-indexed turnNumber, study, overlays, and plots
        _, _, view_raw = _http_request(
            f"{base_url}/mcp",
            method="POST",
            body={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "set_telemetry_view",
                    "arguments": {
                        "turn": 3,
                        "dist": 316.5,
                        "plots": ["speed", "eeg"],
                        "study": "2026-04-12_eeg",
                        "overlays": ["theta_heat"],
                        "trajectory_color": "delta_t",
                    },
                },
            },
        )
        assert json.loads(view_raw.decode("utf-8"))["result"].get("isError") is not True

        jump_frame = ws.recv_json(timeout=4.0)
        while jump_frame.get("type") == "workspace_sync":
            jump_frame = ws.recv_json(timeout=4.0)
        assert jump_frame["turnNumber"] == 3
        assert jump_frame["dist"] == 316.5
        assert jump_frame["study"] == "2026-04-12_eeg"
        assert jump_frame["overlays"] == ["theta_heat"]
        assert jump_frame["tcol"] == "delta_t"

        # 4. Writing a new overlay file triggers automatic 1s watcher workspace_sync broadcast
        ov_dir = studies_dir / "2026-04-12_eeg" / "overlays"
        ov_dir.mkdir(parents=True, exist_ok=True)
        (ov_dir / "theta_heat.geojson").write_text(
            '{"type": "FeatureCollection", "features": []}',
            encoding="utf-8",
        )
        watch_frame = ws.recv_json(timeout=4.0)
        assert watch_frame["type"] == "workspace_sync"

        # 5. get_telemetry_view_status reports 1 connected client
        _, _, status_raw = _http_request(
            f"{base_url}/mcp",
            method="POST",
            body={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "get_telemetry_view_status", "arguments": {}},
            },
        )
        status_text = json.loads(status_raw.decode("utf-8"))["result"]["content"][0]["text"]
        status_data = json.loads(status_text)
        assert status_data["clients_connected"] >= 1
        assert status_data["active_study"] == "2026-04-12_eeg"
    finally:
        ws.close()

    # 6. Outer wrapper socket (/ws?role=wrapper) does not inflate clients_connected,
    #    and receives wrapper_navigate when no inner viewer is connected
    wrapper_ws = _SimpleWsClient(port, path="/ws?role=wrapper")
    try:
        assert wrapper_ws.recv_json(timeout=4.0)["type"] == "COMPANION_READY"
        _, _, st_raw2 = _http_request(f"{base_url}/health")
        assert json.loads(st_raw2.decode("utf-8"))["clientsConnected"] == 0

        _, _, nav_raw = _http_request(
            f"{base_url}/mcp",
            method="POST",
            body={
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "set_telemetry_view",
                    "arguments": {
                        "session_url": "https://brbrdb.brbrkitten.com/telemetry/kartsim/senior/2026-04-12/buckmore",
                    },
                },
            },
        )
        nav_res = json.loads(nav_raw.decode("utf-8"))
        assert nav_res["result"].get("isError") is not True
        nav_frame = wrapper_ws.recv_json(timeout=4.0)
        assert nav_frame["type"] == "wrapper_navigate"
        assert nav_frame["sessionUrl"].startswith("/telemetry/kartsim/senior/2026-04-12/buckmore")
        assert "companion=1" in nav_frame["sessionUrl"]
        assert "https://brbrdb.brbrkitten.com/auth?" in nav_frame["authTarget"]
        assert "next=%2Ftelemetry%2Fkartsim%2Fsenior%2F2026-04-12%2Fbuckmore" in nav_frame["authTarget"]
    finally:
        wrapper_ws.close()


@pytest.mark.parametrize("server_runtime", ["python", "node"])
@pytest.mark.parametrize("bridge_runtime", ["python", "node"])
def test_mcp_stdio_bridge_forwards_workspace_cwd(
    server_runtime: str,
    bridge_runtime: str,
    tmp_path: Path,
) -> None:
    if (server_runtime == "node" or bridge_runtime == "node") and shutil.which("node") is None:
        pytest.skip("Node.js not installed")

    port = _find_free_port()
    state_file = tmp_path / "unbound_state.json"
    adopted_workspace = tmp_path / "agent_cwd_workspace"
    adopted_workspace.mkdir(parents=True)

    srv_env = os.environ.copy()
    srv_env.pop("BRBR_WORKSPACE_DIR", None)
    srv_env.pop("BRBR_STUDIES_DIR", None)
    srv_env["ANTIGRAVITY_SIDECAR_WEB_PORT"] = str(port)
    srv_env["BRBR_COMPANION_WS_PORT"] = str(port)
    srv_env["BRBR_COMPANION_STATE_FILE"] = str(state_file)
    srv_env["BRBR_COMPANION_LOG_FILE"] = str(tmp_path / "unbound_companion.log")

    srv_cmd = (
        [sys.executable, str(SIDECAR_DIR / "server.py")]
        if server_runtime == "python"
        else ["node", str(SIDECAR_DIR / "server.mjs")]
    )
    # Start from a simulated ~/.gemini/plugins/... path so default workspace isn't bound
    fake_global_dir = tmp_path / ".gemini" / "plugins" / "brbrtelemetry"
    fake_global_dir.mkdir(parents=True)

    srv_proc = subprocess.Popen(
        srv_cmd,
        cwd=str(fake_global_dir),
        env=srv_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 8.0
        healthy = False
        while time.monotonic() < deadline:
            try:
                st, _, raw = _http_request(f"http://127.0.0.1:{port}/health")
                if st == 200 and json.loads(raw.decode("utf-8")).get("ok") is True:
                    healthy = True
                    break
            except Exception:
                time.sleep(0.05)
        assert healthy, f"Sidecar ({server_runtime}) failed health check on port {port}"

        bridge_env = os.environ.copy()
        bridge_env["ANTIGRAVITY_SIDECAR_WEB_PORT"] = str(port)
        bridge_env["BRBR_COMPANION_WS_PORT"] = str(port)

        cmd = (
            [sys.executable, str(SIDECAR_DIR / "mcp_stdio.py")]
            if bridge_runtime == "python"
            else ["node", str(SIDECAR_DIR / "mcp_stdio.mjs")]
        )

        stdin_lines = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 10, "method": "initialize", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 11,
                        "method": "tools/call",
                        "params": {"name": "get_telemetry_view_status", "arguments": {}},
                    }
                ),
            ]
        ) + "\n"

        completed = subprocess.run(
            cmd,
            input=stdin_lines.encode("utf-8"),
            cwd=str(adopted_workspace),
            env=bridge_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10.0,
            check=True,
        )

        out_lines = [
            json.loads(line)
            for line in completed.stdout.decode("utf-8").splitlines()
            if line.strip()
        ]
        assert len(out_lines) == 2, out_lines
        assert out_lines[0]["id"] == 10, out_lines
        assert out_lines[1]["id"] == 11, out_lines
        assert "result" in out_lines[1], out_lines
        status_obj = json.loads(out_lines[1]["result"]["content"][0]["text"])
        assert Path(status_obj["workspace_dir"]).resolve() == adopted_workspace.resolve()
    finally:
        srv_proc.terminate()
        try:
            srv_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            srv_proc.kill()
