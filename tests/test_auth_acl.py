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
from unittest.mock import MagicMock, patch
import pandas as pd
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


def test_can_see_league_wildcard() -> None:
    acl = {
        'see_leagues': ['*']
    }
    assert auth.can_see_league(acl, 'kartsim') is True
    assert auth.can_see_league(acl, 'rotax') is True
    assert auth.can_see_league(acl, 'any_other') is True


def test_can_see_league_list() -> None:
    acl = {
        'see_leagues': ['kartsim', 'rotax']
    }
    assert auth.can_see_league(acl, 'kartsim') is True
    assert auth.can_see_league(acl, 'rotax') is True
    assert auth.can_see_league(acl, 'fkl') is False


def test_can_see_league_empty() -> None:
    acl_empty: dict[str, Any] = {}
    assert auth.can_see_league(acl_empty, 'rotax') is False

    acl_none: dict[str, Any] = {'see_leagues': None}
    assert auth.can_see_league(acl_none, 'rotax') is False

    acl_empty_list: dict[str, Any] = {'see_leagues': []}
    assert auth.can_see_league(acl_empty_list, 'rotax') is False


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


def test_settings_and_auth_builder_bypass_auth() -> None:
    # Test that /settings, /save_settings, and /auth_builder can be accessed without a key
    client = app.app.test_client()
    with patch('auth.load_keys', return_value={}):
        resp_settings = client.get('/settings', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_settings.status_code == 200

        resp_save = client.post(
            '/save_settings',
            data={'hero_pilot': 'Test Pilot'},
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_save.status_code == 302

        resp_builder = client.get('/auth_builder', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_builder.status_code == 200


def test_anonymous_user_acl() -> None:
    client = app.app.test_client()
    mock_keys = {
        '': {
            'comment': 'Anonymous',
            'see_videos': False,
            'see_gallery': False,
            'see_telemetry': False,
            'see_drivers': False,
            'see_leagues': ['*'],
            'upload_sessions': {}
        },
        'user_key': {
            'see_videos': True,
            'see_gallery': True,
            'see_telemetry': True,
            'see_drivers': True,
            'see_leagues': ['*'],
            'upload_sessions': {}
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        # 1. Anonymous user (no key, remote IP) gets anonymous ACL
        resp_league = client.get('/league', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_league.status_code == 200

        # Drivers is forbidden for anonymous
        resp_drivers = client.get('/drivers', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_drivers.status_code == 403

        # Telemetry is forbidden for anonymous
        resp_telemetry = client.get(
            '/telemetry/rotax/cadet/2026-03-15/Llandow',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_telemetry.status_code == 403

        # 2. Authenticated user with user_key can access drivers
        client.set_cookie('auth_key', 'user_key')
        resp_drivers_auth = client.get('/drivers', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_drivers_auth.status_code == 200


def test_see_drivers_permission() -> None:
    client = app.app.test_client()
    mock_keys = {
        'no_drivers_key': {
            'see_drivers': False,
            'see_leagues': ['*'],
        },
        'has_drivers_key': {
            'see_drivers': True,
            'see_leagues': ['*'],
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        # 1. Without see_drivers -> 403 Forbidden for drivers endpoints
        client.set_cookie('auth_key', 'no_drivers_key')
        resp_drivers = client.get('/drivers', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_drivers.status_code == 403

        resp_driver = client.get('/driver/Alexey', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_driver.status_code == 403

        resp_plot = client.get('/driver_plot/Alexey.png', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_plot.status_code == 403

        resp_p5_plot = client.get('/driver_percentile_plot/Alexey.png', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_p5_plot.status_code == 403

        # Navbar should NOT include drivers link
        resp_about = client.get('/about', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_about.status_code == 200
        assert 'Drivers' not in resp_about.get_data(as_text=True)

        # 2. With see_drivers -> allowed
        client.set_cookie('auth_key', 'has_drivers_key')
        resp_drivers_ok = client.get('/drivers', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_drivers_ok.status_code == 200

        resp_about_ok = client.get('/about', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_about_ok.status_code == 200
        assert 'Drivers' in resp_about_ok.get_data(as_text=True)


def test_see_leagues_permission() -> None:
    client = app.app.test_client()
    mock_keys = {
        'rotax_only_key': {
            'see_leagues': ['rotax'],
            'see_drivers': True,
        },
        'all_leagues_key': {
            'see_leagues': ['*'],
            'see_drivers': True,
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        # 1. Rotax only key
        client.set_cookie('auth_key', 'rotax_only_key')
        # Accessing rotax should not be 403
        resp_rotax = client.get('/league/rotax', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_rotax.status_code != 403

        # Accessing kartsim should be 403
        resp_kartsim = client.get('/league/kartsim/last', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_kartsim.status_code == 403

        # Meeting for restricted league should be 403
        resp_meeting = client.get(
            '/meeting/kartsim/cadet/2026-05-10/Rowrah',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_meeting.status_code == 403

        # League list should only list rotax, not kartsim
        resp_league_list = client.get('/league', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_league_list.status_code == 200
        html = resp_league_list.get_data(as_text=True)
        assert 'KartSim' not in html

        # 2. Wildcard all leagues key
        client.set_cookie('auth_key', 'all_leagues_key')
        resp_all = client.get('/league/kartsim/last', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_all.status_code != 403


def test_see_telemetry_restricted() -> None:
    client = app.app.test_client()
    mock_keys = {
        'no_telemetry_key': {
            'see_telemetry': False,
            'see_leagues': ['*'],
        },
        'has_telemetry_key': {
            'see_telemetry': True,
            'see_leagues': ['*'],
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        client.set_cookie('auth_key', 'no_telemetry_key')

        # 1. Telemetry API endpoint should be 403 for any league
        resp_api = client.get('/api/telemetry', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_api.status_code == 403

        # 2. Telemetry view page should be 403 for non-kartsim league as well
        resp_view_rotax = client.get(
            '/telemetry/rotax/cadet/2026-03-15/Llandow',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_view_rotax.status_code == 403

        # 3. Telemetry view page for kartsim should be 403
        resp_view_ks = client.get(
            '/telemetry/kartsim/cadet/2026-05-10/Rowrah',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_view_ks.status_code == 403

        # 4. League list with see_telemetry: False hides KartSim
        resp_league_no_tel = client.get('/league', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_league_no_tel.status_code == 200
        assert 'KartSim' not in resp_league_no_tel.get_data(as_text=True)

        # 5. League list with see_telemetry: True shows KartSim
        client.set_cookie('auth_key', 'has_telemetry_key')
        resp_league_has_tel = client.get('/league', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert resp_league_has_tel.status_code == 200
        assert 'KartSim' in resp_league_has_tel.get_data(as_text=True)

        # 6. Key with see_telemetry: True but see_leagues restricting kartsim can still view telemetry
        mock_keys_tel_only = {
            'telemetry_key': {
                'see_telemetry': True,
                'see_leagues': ['rotax'],
            }
        }
        with patch('auth.load_keys', return_value=mock_keys_tel_only):
            client.set_cookie('auth_key', 'telemetry_key')
            resp_ks_telemetry = client.get(
                '/telemetry/kartsim/cadet/2026-05-10/Rowrah',
                environ_base={'REMOTE_ADDR': '192.168.1.100'}
            )
            # Should not be 403 Forbidden because telemetry is guided by see_telemetry only
            assert resp_ks_telemetry.status_code != 403


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
            'see_leagues': ['*'],
        },
        'has_gallery_key': {
            'see_gallery': True,
            'see_leagues': ['*'],
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


def test_about_telemetry_prints_api_key() -> None:
    mock_keys = {
        'telemetry_user_key_123': {
            'see_telemetry': True,
        }
    }
    client = app.app.test_client()
    with patch('auth.load_keys', return_value=mock_keys):
        client.set_cookie('auth_key', 'telemetry_user_key_123')
        response = client.get('/about', environ_base={'REMOTE_ADDR': '192.168.1.100'})
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert 'The installer will ask for an API key; your key is <code>telemetry_user_key_123</code>' in html


def test_can_see_telemetry_unit() -> None:
    # 1. Disabled or missing telemetry
    assert auth.can_see_telemetry({}) is False
    assert auth.can_see_telemetry({'see_telemetry': False}) is False
    assert auth.can_see_telemetry({'see_telemetry': None}) is False
    assert auth.can_see_telemetry({'see_telemetry': {}}) is False

    # 2. Boolean True
    acl_bool_true = {'see_telemetry': True}
    assert auth.can_see_telemetry(acl_bool_true) is True
    assert auth.can_see_telemetry(acl_bool_true, league='kartsim', driver='Alicia') is True

    # 3. Absence of driver treated as wildcard '*'
    acl_no_driver = {
        'see_telemetry': {
            'leagues': ['kartsim']
        }
    }
    assert auth.can_see_telemetry(acl_no_driver) is True
    assert auth.can_see_telemetry(acl_no_driver, league='kartsim') is True
    assert auth.can_see_telemetry(acl_no_driver, league='kartsim', driver='Alicia') is True
    assert auth.can_see_telemetry(acl_no_driver, league='kartsim', driver='Harrison') is True
    assert auth.can_see_telemetry(acl_no_driver, league='rotax', driver='Alicia') is False

    # 4. List format for leagues (absence of driver treated as '*')
    acl_list = {
        'see_telemetry': ['kartsim']
    }
    assert auth.can_see_telemetry(acl_list, league='kartsim') is True
    assert auth.can_see_telemetry(acl_list, league='kartsim', driver='Any') is True
    assert auth.can_see_telemetry(acl_list, league='rotax') is False

    # 5. Specific drivers list
    acl_driver_scoped = {
        'see_telemetry': {
            'drivers': ['Alicia Waterhouse', 'Harrison Outram'],
            'leagues': ['kartsim']
        }
    }
    assert auth.can_see_telemetry(acl_driver_scoped, league='kartsim', driver='Alicia Waterhouse') is True
    assert auth.can_see_telemetry(acl_driver_scoped, league='kartsim', driver='Harrison Outram') is True
    assert auth.can_see_telemetry(acl_driver_scoped, league='kartsim', driver='Other Driver') is False
    assert auth.can_see_telemetry(acl_driver_scoped, league='rotax', driver='Alicia Waterhouse') is False

    # 6. Singular 'driver' and 'league' keys supported
    acl_singular = {
        'see_telemetry': {
            'driver': 'Alicia Waterhouse',
            'league': 'kartsim'
        }
    }
    assert auth.can_see_telemetry(acl_singular, league='kartsim', driver='Alicia Waterhouse') is True
    assert auth.can_see_telemetry(acl_singular, league='kartsim', driver='Other') is False


def test_structured_see_telemetry_routes() -> None:
    client = app.app.test_client()
    mock_keys = {
        'kartsim_only_key': {
            'see_telemetry': {
                'leagues': ['kartsim']
            },
            'see_leagues': ['*']
        },
        'rotax_only_key': {
            'see_telemetry': {
                'leagues': ['rotax']
            },
            'see_leagues': ['*']
        }
    }
    with patch('auth.load_keys', return_value=mock_keys):
        # 1. KartSim only key accessing kartsim telemetry is allowed
        client.set_cookie('auth_key', 'kartsim_only_key')
        resp_ks = client.get(
            '/telemetry/kartsim/cadet/2026-05-10/Rowrah',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_ks.status_code != 403

        # Accessing rotax telemetry with kartsim only key is 403 Forbidden
        resp_rotax = client.get(
            '/telemetry/rotax/cadet/2026-03-15/Llandow',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_rotax.status_code == 403

        # 2. Rotax only key accessing rotax telemetry is allowed
        client.set_cookie('auth_key', 'rotax_only_key')
        resp_rotax_ok = client.get(
            '/telemetry/rotax/cadet/2026-03-15/Llandow',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_rotax_ok.status_code != 403

        # Accessing kartsim telemetry with rotax only key is 403 Forbidden
        resp_ks_forbidden = client.get(
            '/telemetry/kartsim/cadet/2026-05-10/Rowrah',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_ks_forbidden.status_code == 403


def test_session_view_telemetry_button_acl() -> None:
    client = app.app.test_client()
    mock_keys = {
        'no_tel_key': {
            'see_telemetry': False,
            'see_leagues': ['*']
        },
        'has_tel_key': {
            'see_telemetry': {
                'leagues': ['club100_south']
            },
            'see_leagues': ['*']
        }
    }
    mock_df = pd.DataFrame([{
        'SessionID': 'practice_1',
        'Name': 'Driver 1',
        'Lap': 1,
        'LapTime': '1:00.000',
        'LapTimeSeconds': 60.0,
        'LapTimeDeleted': False,
        'Pos': 1,
        'PositionsGained': 0,
        'SessionName': 'Practice 1',
        'DateTime': '2026-08-02 11:00:00'
    }])
    with patch('auth.load_keys', return_value=mock_keys), \
         patch('app.db.find_sessions') as mock_find, \
         patch('app.db.has_telemetry', return_value=True), \
         patch('app.db.load', return_value=mock_df), \
         patch('app.db.load_penalties', return_value=pd.DataFrame()), \
         patch('app.db.get_overlapping_videos', return_value=[]):
        mock_session = MagicMock()
        mock_session.meeting_dir = '/dummy'
        mock_session.session_id = 'practice_1'
        mock_session.session_start_datetime = '2026-08-02 11:00'
        mock_find.return_value = [mock_session]

        # 1. User with no telemetry access should NOT see the Telemetry button
        client.set_cookie('auth_key', 'no_tel_key')
        resp_no_tel = client.get(
            '/session/club100_south/cadet_lw/2026-08-02/Llandow/practice_1',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_no_tel.status_code == 200
        assert 'Telemetry' not in resp_no_tel.get_data(as_text=True)

        # 2. User with telemetry access for club100_south should see the Telemetry button
        client.set_cookie('auth_key', 'has_tel_key')
        resp_has_tel = client.get(
            '/session/club100_south/cadet_lw/2026-08-02/Llandow/practice_1',
            environ_base={'REMOTE_ADDR': '192.168.1.100'}
        )
        assert resp_has_tel.status_code == 200
        assert 'Telemetry' in resp_has_tel.get_data(as_text=True)
