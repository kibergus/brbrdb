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

"""Flask Blueprint providing HTTP & SSE MCP (Model Context Protocol) endpoints for the web app."""
from __future__ import annotations
import asyncio
import json
import queue
import uuid
from typing import Any, Generator
from flask import Blueprint, request, make_response, Response, stream_with_context
from mcp_server import server


mcp_blueprint = Blueprint('mcp', __name__)

_sse_sessions: dict[str, queue.Queue[dict[str, Any]]] = {}


def _make_jsonrpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    """Helper to build a standard JSON-RPC 2.0 error response dict."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message}
    }


def handle_jsonrpc_request(req_data: dict[str, Any]) -> dict[str, Any] | None:
    """Process an incoming MCP JSON-RPC 2.0 message and return JSON-RPC response."""
    req_id = req_data.get('id')
    method = req_data.get('method')
    params = req_data.get('params') or {}

    if not method:
        return _make_jsonrpc_error(req_id, -32600, "Invalid Request: missing method")

    if method == "initialize":
        client_version = params.get("protocolVersion", "2024-11-05") if isinstance(params, dict) else "2024-11-05"
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": client_version,
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "brbrdb",
                    "version": "1.0.0"
                }
            }
        }
    elif method.startswith("notifications/") or method == "cancelled":
        return None
    elif method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {}
        }
    elif method == "tools/list":
        tools = asyncio.run(server.mcp.list_tools())
        tools_out = []
        for t in tools:
            tools_out.append({
                "name": t.name,
                "description": t.description,
                "inputSchema": t.inputSchema
            })
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": tools_out
            }
        }
    elif method == "tools/call":
        tool_name = params.get("name")
        if not isinstance(tool_name, str):
            return _make_jsonrpc_error(req_id, -32602, "Invalid params: 'name' must be a string")
        tool_args = params.get("arguments") or {}
        try:
            call_res = asyncio.run(server.mcp.call_tool(tool_name, tool_args))

            res = call_res[0] if isinstance(call_res, tuple) else call_res
            content_list = []
            if isinstance(res, (list, tuple)):
                for c in res:
                    if hasattr(c, 'text'):
                        content_list.append({"type": "text", "text": getattr(c, 'text')})
                    elif hasattr(c, 'data'):
                        content_list.append({
                            "type": "image",
                            "data": getattr(c, 'data'),
                            "mimeType": getattr(c, 'mimeType', 'image/png')
                        })
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": content_list
                }
            }
        except Exception as e:
            return _make_jsonrpc_error(req_id, -32603, str(e))
    else:
        return _make_jsonrpc_error(req_id, -32601, f"Method not found: {method}")


@mcp_blueprint.route('/api/mcp', methods=['POST'])
def mcp_http_post() -> Response:
    """Direct HTTP POST endpoint for MCP JSON-RPC requests."""
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return make_response(
            json.dumps(_make_jsonrpc_error(None, -32700, "Parse error: Invalid JSON")),
            400,
            {'Content-Type': 'application/json'}
        )

    resp = handle_jsonrpc_request(data)
    if resp is None:
        return make_response('', 204)
    return make_response(json.dumps(resp), 200, {'Content-Type': 'application/json'})


@mcp_blueprint.route('/api/mcp/sse', methods=['GET', 'POST'])
def mcp_sse_connect() -> Response:
    """Establish SSE stream connection or handle direct HTTP POST for MCP network clients."""
    if request.method == 'POST':
        return mcp_http_post()

    session_id = str(uuid.uuid4())
    q: queue.Queue[dict[str, Any]] = queue.Queue()
    _sse_sessions[session_id] = q

    key_param = request.args.get('key')
    msg_uri = f"/api/mcp/messages?session_id={session_id}"
    if key_param:
        msg_uri += f"&key={key_param}"

    def generate() -> Generator[str, None, None]:
        try:
            yield f"event: endpoint\ndata: {msg_uri}\n\n"
            while True:
                try:
                    msg = q.get(timeout=30)
                    yield f"event: message\ndata: {json.dumps(msg)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            _sse_sessions.pop(session_id, None)

    resp = Response(stream_with_context(generate()), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache, no-transform'
    resp.headers['X-Accel-Buffering'] = 'no'
    resp.headers['Connection'] = 'keep-alive'
    return resp


@mcp_blueprint.route('/api/mcp/messages', methods=['POST'])
def mcp_sse_messages() -> Response:
    """Handle incoming message POSTs for SSE or direct sessions."""
    session_id = request.args.get('session_id') or request.form.get('session_id')
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return make_response(
            json.dumps(_make_jsonrpc_error(None, -32700, "Parse error: Invalid JSON")),
            400,
            {'Content-Type': 'application/json'}
        )

    resp = handle_jsonrpc_request(data)

    if session_id and session_id in _sse_sessions:
        if resp is not None:
            _sse_sessions[session_id].put(resp)
        return make_response(json.dumps({"status": "accepted"}), 202, {'Content-Type': 'application/json'})

    if resp is None:
        return make_response('', 204)
    return make_response(json.dumps(resp), 200, {'Content-Type': 'application/json'})
