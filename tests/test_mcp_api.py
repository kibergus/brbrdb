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
import threading
from app import app
from mcp_server import mcp_handlers


def test_mcp_unauthorized_access() -> None:
    client = app.test_client()
    response = client.post('/api/mcp', json={
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'tools/list'
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
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'tools/list'
    }, environ_base={'REMOTE_ADDR': '192.168.1.100'})
    assert response.status_code == 401


def test_mcp_valid_api_key_header() -> None:
    client = app.test_client()
    valid_key = 'ba9d82ef-9d53-4a46-81fa-72c6da096ba5'

    response = client.post('/api/mcp', headers={
        'X-API-Key': valid_key
    }, json={
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'initialize'
    }, environ_base={'REMOTE_ADDR': '192.168.1.100'})
    assert response.status_code == 200
    res_data = json.loads(response.data)
    assert 'brbrdb' in res_data['result']['serverInfo']['name']
    assert 'Mcp-Session-Id' in response.headers
    assert len(response.headers['Mcp-Session-Id']) > 0


def test_mcp_valid_api_key_query_param() -> None:
    client = app.test_client()
    valid_key = 'ba9d82ef-9d53-4a46-81fa-72c6da096ba5'

    response = client.post(f'/api/mcp?key={valid_key}', json={
        'jsonrpc': '2.0',
        'id': 2,
        'method': 'tools/list'
    }, environ_base={'REMOTE_ADDR': '192.168.1.100'})
    assert response.status_code == 200
    res_data = json.loads(response.data)
    tools = [t['name'] for t in res_data['result']['tools']]
    assert 'list_sessions' in tools
    assert 'get_track_info' in tools


def test_mcp_tool_call_over_http() -> None:
    client = app.test_client()
    valid_key = 'ba9d82ef-9d53-4a46-81fa-72c6da096ba5'

    response = client.post(f'/api/mcp?key={valid_key}', json={
        'jsonrpc': '2.0',
        'id': 3,
        'method': 'tools/call',
        'params': {
            'name': 'get_track_info',
            'arguments': {
                'track': 'Lydd'
            }
        }
    }, environ_base={'REMOTE_ADDR': '192.168.1.100'})
    assert response.status_code == 200
    res_data = json.loads(response.data)
    assert 'result' in res_data
    content = res_data['result']['content']
    assert len(content) > 0
    assert 'Lydd Karting 2026' in content[0]['text']
    assert 'center_line' not in content[0]['text']
    assert 'geojson' not in content[0]['text']

    response_geom = client.post(f'/api/mcp?key={valid_key}', json={
        'jsonrpc': '2.0',
        'id': 4,
        'method': 'tools/call',
        'params': {
            'name': 'get_track_info',
            'arguments': {
                'track': 'Lydd',
                'include_geometry': True
            }
        }
    }, environ_base={'REMOTE_ADDR': '192.168.1.100'})
    assert response_geom.status_code == 200
    res_geom_data = json.loads(response_geom.data)
    content_geom = res_geom_data['result']['content']
    assert 'center_line' in content_geom[0]['text']
    assert 'geojson' in content_geom[0]['text']


def test_mcp_streamable_http_session_lifecycle() -> None:
    client = app.test_client()
    valid_key = 'ba9d82ef-9d53-4a46-81fa-72c6da096ba5'

    # 1. Initialize session via POST /api/mcp
    resp_init = client.post(f'/api/mcp?key={valid_key}', json={
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'initialize',
        'params': {
            'protocolVersion': '2025-03-26'
        }
    }, environ_base={'REMOTE_ADDR': '192.168.1.100'})
    assert resp_init.status_code == 200
    data_init = json.loads(resp_init.data)
    assert data_init['result']['protocolVersion'] == '2025-03-26'
    session_id = resp_init.headers.get('Mcp-Session-Id')
    assert session_id is not None
    assert len(session_id) > 0

    # 2. Send initialized notification with Mcp-Session-Id -> 202 Accepted
    resp_notif = client.post(
        f'/api/mcp?key={valid_key}',
        headers={'Mcp-Session-Id': session_id},
        json={
            'jsonrpc': '2.0',
            'method': 'notifications/initialized'
        },
        environ_base={'REMOTE_ADDR': '192.168.1.100'}
    )
    assert resp_notif.status_code == 202

    # 3. Ping with Mcp-Session-Id -> 200 OK
    resp_ping = client.post(
        f'/api/mcp?key={valid_key}',
        headers={'Mcp-Session-Id': session_id},
        json={
            'jsonrpc': '2.0',
            'id': 2,
            'method': 'ping'
        },
        environ_base={'REMOTE_ADDR': '192.168.1.100'}
    )
    assert resp_ping.status_code == 200
    assert resp_ping.headers.get('Mcp-Session-Id') == session_id

    # 4. Open GET SSE stream on /api/mcp with Mcp-Session-Id
    resp_get = client.get(
        f'/api/mcp?key={valid_key}',
        headers={
            'Mcp-Session-Id': session_id,
            'Accept': 'text/event-stream'
        },
        environ_base={'REMOTE_ADDR': '192.168.1.100'}
    )
    assert resp_get.status_code == 200
    assert 'text/event-stream' in resp_get.content_type
    assert resp_get.headers.get('Mcp-Session-Id') == session_id
    assert 'X-Accel-Buffering' in resp_get.headers

    # 5. Terminate session via DELETE /api/mcp
    resp_del = client.delete(
        f'/api/mcp?key={valid_key}',
        headers={'Mcp-Session-Id': session_id},
        environ_base={'REMOTE_ADDR': '192.168.1.100'}
    )
    assert resp_del.status_code == 200


def test_mcp_streamable_http_content_negotiation() -> None:
    client = app.test_client()
    valid_key = 'ba9d82ef-9d53-4a46-81fa-72c6da096ba5'

    # POST with Accept: text/event-stream only -> SSE format response
    resp_sse = client.post(
        f'/api/mcp?key={valid_key}',
        headers={'Accept': 'text/event-stream'},
        json={
            'jsonrpc': '2.0',
            'id': 10,
            'method': 'ping'
        },
        environ_base={'REMOTE_ADDR': '192.168.1.100'}
    )
    assert resp_sse.status_code == 200
    assert 'text/event-stream' in resp_sse.content_type
    assert resp_sse.data.startswith(b'event: message\ndata: ')

    # POST with Accept: application/json -> JSON format response
    resp_json = client.post(
        f'/api/mcp?key={valid_key}',
        headers={'Accept': 'application/json'},
        json={
            'jsonrpc': '2.0',
            'id': 11,
            'method': 'ping'
        },
        environ_base={'REMOTE_ADDR': '192.168.1.100'}
    )
    assert resp_json.status_code == 200
    assert 'application/json' in resp_json.content_type

    # POST with unsupported Accept header -> 406 Not Acceptable
    resp_not_acceptable = client.post(
        f'/api/mcp?key={valid_key}',
        headers={'Accept': 'text/csv'},
        json={
            'jsonrpc': '2.0',
            'id': 12,
            'method': 'ping'
        },
        environ_base={'REMOTE_ADDR': '192.168.1.100'}
    )
    assert resp_not_acceptable.status_code == 406

    # POST with unsupported Content-Type -> 415 Unsupported Media Type
    resp_bad_ctype = client.post(
        f'/api/mcp?key={valid_key}',
        headers={'Content-Type': 'text/plain'},
        data='invalid raw string',
        environ_base={'REMOTE_ADDR': '192.168.1.100'}
    )
    assert resp_bad_ctype.status_code == 415

    # GET without Accept: text/event-stream -> 406 Not Acceptable
    resp_get_no_sse = client.get(
        f'/api/mcp?key={valid_key}',
        headers={
            'Mcp-Session-Id': 'dummy-session-id',
            'Accept': 'application/json'
        },
        environ_base={'REMOTE_ADDR': '192.168.1.100'}
    )
    assert resp_get_no_sse.status_code == 406

    # GET without session ID -> 400 Bad Request
    resp_get_no_sess = client.get(
        f'/api/mcp?key={valid_key}',
        headers={'Accept': 'text/event-stream'},
        environ_base={'REMOTE_ADDR': '192.168.1.100'}
    )
    assert resp_get_no_sess.status_code == 400

    # DELETE without session ID -> 400 Bad Request
    resp_del_no_sess = client.delete(
        f'/api/mcp?key={valid_key}',
        environ_base={'REMOTE_ADDR': '192.168.1.100'}
    )
    assert resp_del_no_sess.status_code == 400

    # OPTIONS /api/mcp -> 204 No Content with CORS and Allow headers
    resp_options = client.open(
        '/api/mcp',
        method='OPTIONS',
        environ_base={'REMOTE_ADDR': '192.168.1.100'}
    )
    assert resp_options.status_code == 204
    assert 'GET, POST, DELETE, OPTIONS' in resp_options.headers.get('Allow', '')


def test_mcp_streamable_http_stateless_multi_worker() -> None:
    client = app.test_client()
    valid_key = 'ba9d82ef-9d53-4a46-81fa-72c6da096ba5'

    # Any valid session ID from another worker is accepted in stateless mode
    worker_session_id = 'worker-session-from-another-process-98765'
    resp_post = client.post(
        f'/api/mcp?key={valid_key}',
        headers={'Mcp-Session-Id': worker_session_id},
        json={
            'jsonrpc': '2.0',
            'id': 20,
            'method': 'ping'
        },
        environ_base={'REMOTE_ADDR': '192.168.1.100'}
    )
    assert resp_post.status_code == 200
    assert resp_post.headers.get('Mcp-Session-Id') == worker_session_id

    # Invalid session ID format (non-ASCII / control chars) -> 400 Bad Request
    resp_invalid_id = client.post(
        f'/api/mcp?key={valid_key}',
        headers={'Mcp-Session-Id': 'invalid session with spaces\x00'},
        json={
            'jsonrpc': '2.0',
            'id': 21,
            'method': 'ping'
        },
        environ_base={'REMOTE_ADDR': '192.168.1.100'}
    )
    assert resp_invalid_id.status_code == 400


def test_mcp_sse_thread_budget_limit() -> None:
    client = app.test_client()
    valid_key = 'ba9d82ef-9d53-4a46-81fa-72c6da096ba5'

    orig_sem = mcp_handlers._sse_semaphore
    test_sem = threading.Semaphore(1)
    mcp_handlers._sse_semaphore = test_sem

    try:
        # Simulate all permits in budget currently in use by background streams
        test_sem.acquire()

        # Attempt to open a new stream when budget is exhausted -> 429
        resp = client.get(
            f'/api/mcp?key={valid_key}',
            headers={'Mcp-Session-Id': 'sess-busy', 'Accept': 'text/event-stream'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp.status_code == 429
        assert 'Too Many Requests' in resp.get_data(as_text=True)

        # Release the permit (simulating an existing stream closing)
        test_sem.release()

        # Now opening a new stream succeeds
        resp_ok = client.get(
            f'/api/mcp?key={valid_key}',
            headers={'Mcp-Session-Id': 'sess-ok', 'Accept': 'text/event-stream'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_ok.status_code == 200
        # The stream acquired the permit
        assert not test_sem.acquire(blocking=False)

        # Closing the response releases the permit back to the budget
        resp_ok.close()
        assert test_sem.acquire(blocking=False)
        test_sem.release()
    finally:
        mcp_handlers._sse_semaphore = orig_sem
