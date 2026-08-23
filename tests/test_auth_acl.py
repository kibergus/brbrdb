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

from typing import Any
from unittest.mock import patch
import app
import auth


def test_can_upload_driver_wildcard() -> None:
    # Test wildcard option
    acl = {
        'upload_sessions': {
            'drivers': ['*']
        }
    }
    assert auth.can_upload_session_for_driver(acl, 'Driver A') is True
    assert auth.can_upload_session_for_driver(acl, 'Any Other Driver') is True


def test_can_upload_driver_list() -> None:
    # Test specific drivers list
    acl = {
        'upload_sessions': {
            'drivers': ['Driver A', 'Driver B']
        }
    }
    assert auth.can_upload_session_for_driver(acl, 'Driver A') is True
    assert auth.can_upload_session_for_driver(acl, 'Driver B') is True
    assert auth.can_upload_session_for_driver(acl, 'Driver C') is False


def test_can_upload_driver_empty() -> None:
    # Test empty or disabled permission
    acl_empty: dict[str, Any] = {}
    assert auth.can_upload_session_for_driver(acl_empty, 'Driver A') is False

    acl_disabled = {
        'upload_sessions': None
    }
    assert auth.can_upload_session_for_driver(acl_disabled, 'Driver A') is False


def test_can_upload_league_wildcard() -> None:
    acl = {
        'upload_sessions': {
            'leagues': ['*']
        }
    }
    assert auth.can_upload_session_for_league(acl, 'kartsim') is True
    assert auth.can_upload_session_for_league(acl, 'other') is True


def test_can_upload_league_list() -> None:
    acl = {
        'upload_sessions': {
            'leagues': ['kartsim', 'rotax']
        }
    }
    assert auth.can_upload_session_for_league(acl, 'kartsim') is True
    assert auth.can_upload_session_for_league(acl, 'rotax') is True
    assert auth.can_upload_session_for_league(acl, 'other') is False


def test_require_login_redirect() -> None:
    # Test that requests are redirected to auth when not logged in (from non-localhost)
    client = app.app.test_client()
    with patch('auth.load_keys', return_value={}):
        # Mocking remote_addr to be a remote address
        response = client.get('/drivers', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert response.status_code == 302
        assert '/auth' in response.headers['Location']


def test_static_files_bypass_auth() -> None:
    # Test that static files can be accessed without redirection even when not logged in
    client = app.app.test_client()
    with patch('auth.load_keys', return_value={}):
        response = client.get('/static/style.css', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert response.status_code == 200


def test_kartsim_data_restricted() -> None:
    # Test that accessing kartsim resources is blocked if kartsim_data permission is false
    client = app.app.test_client()
    mock_keys = {
        'no_kartsim_key': {
            'see_videos': True,
            'kartsim_data': False,
            'upload_sessions': None
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        client.set_cookie('auth_key', 'no_kartsim_key')

        # Accessing kartsim meeting list or kartsim route should be forbidden
        # Let's mock a remote IP to bypass the default localhost bypass in order to test the key
        response = client.get('/league/kartsim/last', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert response.status_code == 403

        # Accessing non-kartsim league should be allowed (e.g. redirecting or loading)
        response_non = client.get('/league/other/last', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        # Should not be a 403 forbidden
        assert response_non.status_code != 403


def test_api_endpoints_require_login_401() -> None:
    # Test that any API endpoint returns 401 instead of redirecting if returned ACL is empty
    client = app.app.test_client()
    with patch('auth.load_keys', return_value={}):
        # Mocking remote_addr to be a remote address to trigger non-localhost auth flow
        response = client.get('/api/telemetry', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert response.status_code == 401
        assert response.get_json() == {'error': 'Unauthorized: Invalid or missing API key.'}


def test_gallery_restricted() -> None:
    # Test that accessing /gallery returns 403 error page if see_gallery permission is false
    client = app.app.test_client()
    mock_keys = {
        'no_gallery_key': {
            'see_gallery': False,
        },
        'has_gallery_key': {
            'see_gallery': True,
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        # 1. No gallery permission -> 403 Forbidden with friendly error page
        client.set_cookie('auth_key', 'no_gallery_key')
        response = client.get(
            '/gallery?league=fat_regional&meeting=2026-06-13',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response.status_code == 403
        html = response.get_data(as_text=True)
        assert 'athletes' in html.lower()
        assert 'parents' in html.lower()
        assert 'brbrdb@kibergus.com' in html

        # 2. Menu should still contain gallery link even without ACL
        resp_about = client.get('/about', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_about.status_code == 200
        about_html = resp_about.get_data(as_text=True)
        assert 'Gallery' in about_html
        assert 'href="/gallery"' in about_html

        # 3. Anonymous user (no key, remote IP) -> /gallery returns 403 error page (not 302 redirect)
        client_anon = app.app.test_client()
        response_anon_gallery = client_anon.get('/gallery', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert response_anon_gallery.status_code == 403
        anon_gallery_html = response_anon_gallery.get_data(as_text=True)
        assert 'athletes' in anon_gallery_html.lower()

        # 4. Anonymous user -> /about returns 200 OK
        response_anon_about = client_anon.get('/about', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert response_anon_about.status_code == 200

        # 5. Has gallery permission -> not 403 (should render or find sessions, but won't be blocked)
        client.set_cookie('auth_key', 'has_gallery_key')
        response2 = client.get(
            '/gallery?league=fat_regional&meeting=2026-06-13',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert response2.status_code != 403


def test_cookie_secure_flags_in_debug_and_non_debug() -> None:
    # Test that secure=not app.debug and max_age=COOKIE_MAX_AGE are correctly set on response cookies
    mock_keys = {
        'test_key': {
            'see_videos': True,
        }
    }
    client = app.app.test_client()
    original_debug = app.app.debug

    try:
        # Test debug mode: True (cookies should NOT be secure)
        app.app.debug = True
        with patch('auth.load_keys', return_value=mock_keys):
            # Test auth GET
            response = client.get('/auth?key=test_key')
            cookies = response.headers.getlist('Set-Cookie')
            auth_cookie = next((c for c in cookies if auth.AUTH_COOKIE in c), None)
            assert auth_cookie is not None
            assert 'Secure' not in auth_cookie and 'secure' not in auth_cookie
            assert f'Max-Age={app.COOKIE_MAX_AGE}' in auth_cookie

            # Test settings POST
            response = client.post('/save_settings', data={'hero_pilot': 'Pilot A', 'disabled_hero': 'Pilot B'})
            cookies = response.headers.getlist('Set-Cookie')
            hero_cookie = next((c for c in cookies if 'hero_pilot' in c), None)
            disabled_cookie = next((c for c in cookies if 'disabled_heroes' in c), None)
            assert hero_cookie is not None
            assert disabled_cookie is not None
            assert 'Secure' not in hero_cookie and 'secure' not in hero_cookie
            assert 'Secure' not in disabled_cookie and 'secure' not in disabled_cookie
            assert f'Max-Age={app.COOKIE_MAX_AGE}' in hero_cookie
            assert f'Max-Age={app.COOKIE_MAX_AGE}' in disabled_cookie

        # Test debug mode: False (cookies MUST be secure)
        app.app.debug = False
        with patch('auth.load_keys', return_value=mock_keys):
            # Test auth GET
            response = client.get('/auth?key=test_key')
            cookies = response.headers.getlist('Set-Cookie')
            auth_cookie = next((c for c in cookies if auth.AUTH_COOKIE in c), None)
            assert auth_cookie is not None
            # Check case-insensitively for Secure/secure attribute in cookie header
            assert 'secure' in auth_cookie.lower()
            assert f'Max-Age={app.COOKIE_MAX_AGE}' in auth_cookie

            # Test settings POST
            response = client.post('/save_settings', data={'hero_pilot': 'Pilot A', 'disabled_hero': 'Pilot B'})
            cookies = response.headers.getlist('Set-Cookie')
            hero_cookie = next((c for c in cookies if 'hero_pilot' in c), None)
            disabled_cookie = next((c for c in cookies if 'disabled_heroes' in c), None)
            assert hero_cookie is not None
            assert disabled_cookie is not None
            assert 'secure' in hero_cookie.lower()
            assert 'secure' in disabled_cookie.lower()
            assert f'Max-Age={app.COOKIE_MAX_AGE}' in hero_cookie
            assert f'Max-Age={app.COOKIE_MAX_AGE}' in disabled_cookie
    finally:
        app.app.debug = original_debug


def test_auth_redirects() -> None:
    mock_keys = {
        'test_key': {
            'see_videos': True,
        }
    }
    client = app.app.test_client()
    with patch('auth.load_keys', return_value=mock_keys):
        # 1. No next parameter -> index page
        response = client.get('/auth?key=test_key')
        assert response.status_code == 302
        assert response.headers['Location'] == '/'

        # 2. next=/auth -> index page (to avoid cyclical redirect)
        response = client.get('/auth?key=test_key&next=/auth')
        assert response.status_code == 302
        assert response.headers['Location'] == '/'

        # 3. next=/some-other-safe-path -> /some-other-safe-path
        response = client.get('/auth?key=test_key&next=/some-other-safe-path')
        assert response.status_code == 302
        assert response.headers['Location'] == '/some-other-safe-path'

        # 4. next=http://evil.com -> index page (open redirect protection)
        response = client.get('/auth?key=test_key&next=http://evil.com')
        assert response.status_code == 302
        assert response.headers['Location'] == '/'


def test_sliding_cookie_refresh() -> None:
    mock_keys = {
        'test_key': {
            'see_videos': True,
        }
    }
    client = app.app.test_client()
    with patch('auth.load_keys', return_value=mock_keys):
        # 1. Access with cookie set should refresh the auth_key and settings cookies if present
        client.set_cookie('auth_key', 'test_key')
        client.set_cookie('hero_pilot', 'Test Pilot')

        # Request a page that requires auth but allows normal rendering
        # Mock remote_addr to trigger full auth check
        response = client.get('/about', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        cookies = response.headers.getlist('Set-Cookie')

        auth_cookie = next((c for c in cookies if auth.AUTH_COOKIE in c), None)
        hero_cookie = next((c for c in cookies if 'hero_pilot' in c), None)
        disabled_cookie = next((c for c in cookies if 'disabled_heroes' in c), None)

        assert auth_cookie is not None
        assert f'Max-Age={app.COOKIE_MAX_AGE}' in auth_cookie
        assert hero_cookie is not None
        assert f'Max-Age={app.COOKIE_MAX_AGE}' in hero_cookie
        assert disabled_cookie is None

        # 2. Access to static files should NOT refresh the cookies
        response_static = client.get('/static/style.css', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        cookies_static = response_static.headers.getlist('Set-Cookie')
        auth_cookie_static = next((c for c in cookies_static if auth.AUTH_COOKIE in c), None)
        assert auth_cookie_static is None


def test_save_settings_visibility_change_persists() -> None:
    mock_keys = {
        'test_key': {
            'see_videos': True,
        }
    }
    client = app.app.test_client()
    with patch('auth.load_keys', return_value=mock_keys):
        client.set_cookie('auth_key', 'test_key')
        client.set_cookie('hero_pilot', 'Driver A')
        client.set_cookie('disabled_heroes', '')

        # Move Driver A from active to disabled
        response = client.post('/save_settings', data={'disabled_hero': 'Driver A'})
        cookies = response.headers.getlist('Set-Cookie')

        hero_cookie = next((c for c in cookies if 'hero_pilot=' in c), None)
        disabled_cookie = next((c for c in cookies if 'disabled_heroes=' in c), None)

        assert hero_cookie is not None
        assert 'Driver A' not in hero_cookie
        assert disabled_cookie is not None
        assert 'Driver' in disabled_cookie and 'A' in disabled_cookie


def test_secret_key_is_set() -> None:
    assert app.app.secret_key is not None
    assert len(app.app.secret_key) > 0


def test_security_headers() -> None:
    client = app.app.test_client()
    response = client.get('/static/style.css')
    assert response.headers.get('X-Content-Type-Options') == 'nosniff'
    assert response.headers.get('X-Frame-Options') == 'SAMEORIGIN'
    assert response.headers.get('Referrer-Policy') == 'strict-origin-when-cross-origin'
