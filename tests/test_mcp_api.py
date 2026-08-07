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
from app import app


def test_mcp_unauthorized_access() -> None:
    client = app.test_client()
    response = client.post('/api/mcp', json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list"
    }, environ_base={'REMOTE_ADDR': '192.168.1.100'})
    assert response.status_code == 401
    data = json.loads(response.data)
    assert 'error' in data
    assert 'Unauthorized' in data['error']


def test_mcp_invalid_api_key() -> None:
    client = app.test_client()
    response = client.post('/api/mcp', headers={
        'X-API-Key': 'invalid-key-12345'
    }, json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list"
    }, environ_base={'REMOTE_ADDR': '192.168.1.100'})
    assert response.status_code == 401


def test_mcp_valid_api_key_header() -> None:
    client = app.test_client()
    valid_key = 'ba9d82ef-9d53-4a46-81fa-72c6da096ba5'

    response = client.post('/api/mcp', headers={
        'X-API-Key': valid_key
    }, json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize"
    }, environ_base={'REMOTE_ADDR': '192.168.1.100'})
    assert response.status_code == 200
    res_data = json.loads(response.data)
    assert "brbrdb" in res_data["result"]["serverInfo"]["name"]


def test_mcp_valid_api_key_query_param() -> None:

    client = app.test_client()
    valid_key = 'ba9d82ef-9d53-4a46-81fa-72c6da096ba5'

    response = client.post(f'/api/mcp?key={valid_key}', json={
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list"
    }, environ_base={'REMOTE_ADDR': '192.168.1.100'})
    assert response.status_code == 200
    res_data = json.loads(response.data)
    tools = [t["name"] for t in res_data["result"]["tools"]]
    assert "list_sessions" in tools
    assert "get_track_info" in tools


def test_mcp_tool_call_over_http() -> None:
    client = app.test_client()
    valid_key = 'ba9d82ef-9d53-4a46-81fa-72c6da096ba5'

    response = client.post(f'/api/mcp?key={valid_key}', json={
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "get_track_info",
            "arguments": {
                "track": "Lydd"
            }
        }
    }, environ_base={'REMOTE_ADDR': '192.168.1.100'})
    assert response.status_code == 200
    res_data = json.loads(response.data)
    assert "result" in res_data
    content = res_data["result"]["content"]
    assert len(content) > 0
    assert "Lydd Karting 2026" in content[0]["text"]


def test_mcp_sse_endpoint_authentication() -> None:
    client = app.test_client()
    # Unauthenticated
    resp_unauth = client.get('/api/mcp/sse', environ_base={'REMOTE_ADDR': '192.168.1.100'})
    assert resp_unauth.status_code == 401

    # Authenticated via query parameter
    valid_key = 'ba9d82ef-9d53-4a46-81fa-72c6da096ba5'
    resp_auth = client.get(f'/api/mcp/sse?key={valid_key}', environ_base={'REMOTE_ADDR': '192.168.1.100'})
    assert resp_auth.status_code == 200
    assert 'text/event-stream' in resp_auth.content_type
    assert 'X-Accel-Buffering' in resp_auth.headers


def test_mcp_sse_post_http_first() -> None:
    client = app.test_client()
    valid_key = 'ba9d82ef-9d53-4a46-81fa-72c6da096ba5'

    # Test http-first strategy sending POST directly to /api/mcp/sse
    response = client.post(f'/api/mcp/sse?key={valid_key}', json={
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25"
        }
    }, environ_base={'REMOTE_ADDR': '192.168.1.100'})
    assert response.status_code == 200
    res_data = json.loads(response.data)
    assert res_data["result"]["protocolVersion"] == "2025-11-25"
