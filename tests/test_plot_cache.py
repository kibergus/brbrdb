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

from flask import Flask
from plot_handlers import PlotCache, make_plot_response


def test_plot_cache_targeted_invalidation() -> None:
    cache = PlotCache(maxsize=10)

    # Set entries with session IDs
    cache.set('key1', b'data1', session_ids=['sess_A', 'sess_B'])
    cache.set('key2', b'data2', session_ids=['sess_B', 'sess_C'])
    cache.set('key3', b'data3', session_ids=['sess_D'])

    assert cache.get('key1') == b'data1'
    assert cache.get('key2') == b'data2'
    assert cache.get('key3') == b'data3'

    # Invalidate only sess_A -> should remove key1, keep key2 and key3
    cache.invalidate(['sess_A'])
    assert cache.get('key1') is None
    assert cache.get('key2') == b'data2'
    assert cache.get('key3') == b'data3'

    # Invalidate sess_D -> should remove key3, keep key2
    cache.invalidate('sess_D')
    assert cache.get('key3') is None
    assert cache.get('key2') == b'data2'

    # Global clear
    cache.invalidate(None)
    assert cache.get('key2') is None


def test_make_plot_response_headers_and_etag() -> None:
    app = Flask(__name__)
    png_bytes = b'fake_png_data_12345'

    with app.test_request_context('/plots/test.png'):
        resp = make_plot_response(png_bytes)
        assert resp.status_code == 200
        assert resp.headers.get('Cache-Control') == 'no-cache, must-revalidate'
        etag = resp.headers.get('ETag')
        assert etag is not None
        assert etag.startswith('"') and etag.endswith('"')

    # Test 304 Not Modified with If-None-Match header
    with app.test_request_context('/plots/test.png', headers={'If-None-Match': etag}):
        resp_304 = make_plot_response(png_bytes)
        assert resp_304.status_code == 304
        assert resp_304.headers.get('Cache-Control') == 'no-cache, must-revalidate'
        assert resp_304.headers.get('ETag') == etag
        assert len(resp_304.get_data()) == 0
