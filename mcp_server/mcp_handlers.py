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

"""Flask Blueprint providing Streamable HTTP MCP (Model Context Protocol) endpoints.

Implements stateless Streamable HTTP transport compatible with multi-worker
environments (e.g. Gunicorn with multiple worker processes).
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import threading
import time
import uuid
from typing import Any, Generator
from flask import Blueprint, request, make_response, Response, stream_with_context
from mcp_server import server


mcp_blueprint = Blueprint('mcp', __name__)

SESSION_ID_PATTERN = re.compile(r'^[\x21-\x7E]+$')

# Thread budget protection: limit concurrent SSE streams per worker process
# Default to 4 (which is 1/2 of the default 8 threads per worker process)
MCP_MAX_SSE_PER_WORKER = int(os.environ.get('MCP_MAX_SSE_PER_WORKER', '4'))
MCP_SSE_MAX_DURATION_SECONDS = float(os.environ.get('MCP_SSE_MAX_DURATION_SECONDS', '300.0'))
_sse_semaphore = threading.Semaphore(MCP_MAX_SSE_PER_WORKER)


def _make_jsonrpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    """Helper to build a standard JSON-RPC 2.0 error response dict."""
    return {
        'jsonrpc': '2.0',
        'id': req_id,
        'error': {'code': code, 'message': message}
    }


def _extract_session_id() -> str | None:
    """Extract MCP session ID from request headers or query parameters."""
    return (
        request.headers.get('mcp-session-id')
        or request.headers.get('Mcp-Session-Id')
        or request.args.get('session_id')
        or request.args.get('mcp_session_id')
    )


def _wants_sse(accept_header: str | None) -> bool:
    """Check if client specifically requested text/event-stream and not application/json."""
    if not accept_header:
        return False
    types = [t.strip().split(';')[0].lower() for t in accept_header.split(',')]
    has_sse = any(t == 'text/event-stream' for t in types)
    has_json = any(t in ('application/json', '*/*') for t in types)
    return has_sse and not has_json


def _accepts_supported_type(accept_header: str | None) -> bool:
    """Check if client accepts application/json or text/event-stream."""
    if not accept_header:
        return True
    types = [t.strip().split(';')[0].lower() for t in accept_header.split(',')]
    return any(t in ('application/json', 'text/event-stream', '*/*', 'application/*') for t in types)


def _make_mcp_response(
    data: dict[str, Any] | None,
    session_id: str | None = None,
    accept_header: str | None = None,
    status_code: int = 200,
) -> Response:
    """Format and return an HTTP or SSE response according to content negotiation."""
    headers: dict[str, str] = {}
    if session_id:
        headers['Mcp-Session-Id'] = session_id

    if data is None:
        return make_response('', status_code, headers)

    if _wants_sse(accept_header):
        headers['Content-Type'] = 'text/event-stream'
        headers['Cache-Control'] = 'no-cache, no-transform'
        sse_body = f'event: message\ndata: {json.dumps(data)}\n\n'
        return make_response(sse_body, status_code, headers)
    else:
        headers['Content-Type'] = 'application/json'
        return make_response(json.dumps(data), status_code, headers)


def handle_jsonrpc_request(req_data: dict[str, Any]) -> dict[str, Any] | None:
    """Process an incoming MCP JSON-RPC 2.0 message and return JSON-RPC response."""
    req_id = req_data.get('id')
    method = req_data.get('method')
    params = req_data.get('params') or {}

    if not method:
        return _make_jsonrpc_error(req_id, -32600, 'Invalid Request: missing method')

    if method.startswith('notifications/') or method == 'cancelled' or 'id' not in req_data:
        return None

    if method == 'initialize':
        client_version = params.get('protocolVersion', '2024-11-05') if isinstance(params, dict) else '2024-11-05'
        return {
            'jsonrpc': '2.0',
            'id': req_id,
            'result': {
                'protocolVersion': client_version,
                'capabilities': {
                    'tools': {}
                },
                'serverInfo': {
                    'name': 'brbrdb',
                    'version': '1.0.0'
                }
            }
        }
    elif method == 'ping':
        return {
            'jsonrpc': '2.0',
            'id': req_id,
            'result': {}
        }
    elif method == 'tools/list':
        tools = asyncio.run(server.mcp.list_tools())
        tools_out = []
        for t in tools:
            tools_out.append({
                'name': t.name,
                'description': t.description,
                'inputSchema': t.inputSchema
            })
        return {
            'jsonrpc': '2.0',
            'id': req_id,
            'result': {
                'tools': tools_out
            }
        }
    elif method == 'tools/call':
        tool_name = params.get('name')
        if not isinstance(tool_name, str):
            return _make_jsonrpc_error(req_id, -32602, "Invalid params: 'name' must be a string")
        tool_args = params.get('arguments') or {}
        try:
            call_res = asyncio.run(server.mcp.call_tool(tool_name, tool_args))

            res = call_res[0] if isinstance(call_res, tuple) else call_res
            content_list = []
            if isinstance(res, (list, tuple)):
                for c in res:
                    if hasattr(c, 'text'):
                        content_list.append({'type': 'text', 'text': getattr(c, 'text')})
                    elif hasattr(c, 'data'):
                        content_list.append({
                            'type': 'image',
                            'data': getattr(c, 'data'),
                            'mimeType': getattr(c, 'mimeType', 'image/png')
                        })
            return {
                'jsonrpc': '2.0',
                'id': req_id,
                'result': {
                    'content': content_list
                }
            }
        except Exception as e:
            return _make_jsonrpc_error(req_id, -32603, str(e))
    else:
        return _make_jsonrpc_error(req_id, -32601, f'Method not found: {method}')


@mcp_blueprint.route('/api/mcp', methods=['GET', 'POST', 'DELETE', 'OPTIONS'])
def mcp_endpoint() -> Response:
    """Streamable HTTP transport endpoint for MCP protocol. Stateless and multi-worker safe."""
    if request.method == 'OPTIONS':
        resp = make_response('', 204)
        resp.headers['Allow'] = 'GET, POST, DELETE, OPTIONS'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, DELETE, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = (
            'Content-Type, Accept, Mcp-Session-Id, Mcp-Protocol-Version, X-API-Key, Authorization'
        )
        return resp

    session_id = _extract_session_id()
    if session_id and not SESSION_ID_PATTERN.fullmatch(session_id):
        return make_response(
            json.dumps(_make_jsonrpc_error(None, -32600, 'Bad Request: Invalid session ID format')),
            400,
            {'Content-Type': 'application/json'}
        )

    if request.method == 'DELETE':
        if not session_id:
            return make_response(
                json.dumps(_make_jsonrpc_error(None, -32600, 'Bad Request: Missing session ID')),
                400,
                {'Content-Type': 'application/json'}
            )
        return make_response('', 200, {'Content-Type': 'application/json'})

    if request.method == 'GET':
        get_accept = request.headers.get('Accept', '')
        if 'text/event-stream' not in get_accept and '*/*' not in get_accept:
            return make_response(
                'Not Acceptable: Client must accept text/event-stream',
                406,
                {'Content-Type': 'text/plain'}
            )

        if not session_id:
            return make_response(
                'Bad Request: Missing session ID',
                400,
                {'Content-Type': 'text/plain'}
            )

        acquired = _sse_semaphore.acquire(blocking=False)
        if not acquired:
            return make_response(
                'Too Many Requests: MCP SSE connection limit reached for this worker',
                429,
                {'Content-Type': 'text/plain', 'Retry-After': '15'}
            )

        released = threading.Event()

        def release_slot() -> None:
            if not released.is_set():
                released.set()
                _sse_semaphore.release()

        def generate() -> Generator[str, None, None]:
            try:
                yield ': keepalive\n\n'
                start_time = time.monotonic()
                while time.monotonic() - start_time < MCP_SSE_MAX_DURATION_SECONDS:
                    time.sleep(15.0)
                    yield ': keepalive\n\n'
            except GeneratorExit:
                pass
            finally:
                release_slot()

        resp = Response(stream_with_context(generate()), mimetype='text/event-stream')
        resp.call_on_close(release_slot)
        resp.headers['Mcp-Session-Id'] = session_id
        resp.headers['Cache-Control'] = 'no-cache, no-transform'
        resp.headers['X-Accel-Buffering'] = 'no'
        resp.headers['Connection'] = 'keep-alive'
        return resp

    # POST handling
    post_accept = request.headers.get('Accept')
    if post_accept and not _accepts_supported_type(post_accept):
        return make_response(
            'Not Acceptable: Client must accept application/json or text/event-stream',
            406,
            {'Content-Type': 'text/plain'}
        )

    content_type = request.headers.get('Content-Type', '')
    if content_type and not any(content_type.startswith(t) for t in ('application/json', 'application/json-rpc')):
        return make_response(
            'Unsupported Media Type: Content-Type must be application/json',
            415,
            {'Content-Type': 'text/plain'}
        )

    data = request.get_json(silent=True)
    if data is None or not isinstance(data, dict):
        return make_response(
            json.dumps(_make_jsonrpc_error(None, -32700, 'Parse error: Invalid JSON')),
            400,
            {'Content-Type': 'application/json'}
        )

    # In stateless Streamable HTTP, if no session ID provided on initialize, generate one
    if not session_id and data.get('method') == 'initialize':
        session_id = uuid.uuid4().hex

    resp_data = handle_jsonrpc_request(data)
    if resp_data is None:
        return _make_mcp_response(None, session_id=session_id, status_code=202)

    return _make_mcp_response(
        resp_data,
        session_id=session_id,
        accept_header=post_accept,
        status_code=200
    )
