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


def test_plot_cache_prefix_suffix_invalidation() -> None:
    cache = PlotCache(maxsize=10)

    # 1. Entry tagged with short session ID, invalidated with full db_session_id
    cache.set('short_id_plot', b'plot_data_1', session_ids=['11_44_bambino_r2r_bambino_practice_4'])
    cache.set('other_plot', b'plot_data_2', session_ids=['12_00_cadet_heat_1'])

    cache.invalidate(['2026_10_11_Shenington_11_44_bambino_r2r_bambino_practice_4'])
    assert cache.get('short_id_plot') is None
    assert cache.get('other_plot') == b'plot_data_2'

    # 2. Entry tagged with full db_session_id, invalidated with short session ID
    cache.set(
        'full_id_plot', b'plot_data_3',
        session_ids=['2026_10_11_Shenington_11_44_bambino_r2r_bambino_practice_4']
    )
    cache.invalidate(['11_44_bambino_r2r_bambino_practice_4'])
    assert cache.get('full_id_plot') is None
    assert cache.get('other_plot') == b'plot_data_2'


def test_plot_cache_cross_worker_shared_db() -> None:
    with tempfile.NamedTemporaryFile(suffix='.sqlite3', delete=False) as tf:
        shared_db_path = tf.name

    try:
        worker1_cache = PlotCache(maxsize=10, cache_path=shared_db_path)
        worker2_cache = PlotCache(maxsize=10, cache_path=shared_db_path)

        # Worker 1 caches a generated plot
        worker1_cache.set('violin_plot_bambino', b'violin_png_bytes', session_ids=['11_44_practice_4'])

        # Worker 2 reads from the shared cache (cache hit)
        assert worker2_cache.get('violin_plot_bambino') == b'violin_png_bytes'

        # Worker 2 receives an upload and invalidates the session
        worker2_cache.invalidate(['2026_10_11_Shenington_11_44_practice_4'])

        # Both Worker 1 and Worker 2 now see the cache entry invalidated
        assert worker1_cache.get('violin_plot_bambino') is None
        assert worker2_cache.get('violin_plot_bambino') is None

        worker1_cache.close()
        worker2_cache.close()
    finally:
        if os.path.exists(shared_db_path):
            os.remove(shared_db_path)
        for ext in ('-wal', '-shm'):
            if os.path.exists(shared_db_path + ext):
                os.remove(shared_db_path + ext)


def test_plot_cache_lru_eviction() -> None:
    cache = PlotCache(maxsize=3)

    cache.set('item1', b'val1')
    cache.set('item2', b'val2')
    cache.set('item3', b'val3')

    # Access item1 to make item2 the least recently used
    assert cache.get('item1') == b'val1'

    # Insert item4 -> should trigger LRU eviction of item2
    cache.set('item4', b'val4')

    assert cache.get('item1') == b'val1'
    assert cache.get('item2') is None
    assert cache.get('item3') == b'val3'
    assert cache.get('item4') == b'val4'


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
