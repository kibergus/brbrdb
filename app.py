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
from urllib.parse import urlparse, urljoin
from flask import (
    Flask, render_template, render_template_string, abort, url_for, redirect,
    request, make_response, Response, g
)
from werkzeug import wrappers as werkzeug_wrappers

import aliases
from race_tools import sanitize
import pandas as pd
import re
import plot_handlers
import location_handlers
import upload_handlers
import gallery_handlers
from mcp_server import mcp_handlers
import race_db
from database import db, config

import os
import json
import logging

import auth

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = config['secret_key']
app.register_blueprint(plot_handlers.plots_blueprint)
app.register_blueprint(gallery_handlers.gallery_blueprint)
app.register_blueprint(mcp_handlers.mcp_blueprint)


# Cookies max age: 6 months (6 * 30 days)
COOKIE_MAX_AGE = 6 * 30 * 24 * 60 * 60
app.register_blueprint(location_handlers.location_blueprint)
app.register_blueprint(upload_handlers.upload_blueprint)

REPORTS_DIR = os.path.join(config['data_dir'], 'reports')


@app.route('/reports/<path:filename>')
def serve_report(filename: str) -> Response:
    """Serve a generated HTML telemetry report from the reports directory.

    Reports are rendered through Jinja2 so that template variables (e.g.
    {{ google_maps_api_key }}) are substituted at serve time from config,
    keeping credentials out of stored files.
    """
    os.makedirs(REPORTS_DIR, exist_ok=True)
    safe_path = os.path.realpath(os.path.join(REPORTS_DIR, filename))
    if not safe_path.startswith(os.path.realpath(REPORTS_DIR) + os.sep):
        abort(403)
    if not os.path.isfile(safe_path):
        abort(404)
    with open(safe_path, 'r', encoding='utf-8') as f:
        template_str = f.read()
    rendered = render_template_string(
        template_str,
        google_maps_api_key=config.get('google_maps_api_key', ''),
    )
    return Response(rendered, mimetype='text/html')


@app.teardown_request
def teardown_db(exception: Any = None) -> None:
    """Ensure any uncommitted database transactions are rolled back and connection is returned to the pool."""
    db.close()


@app.before_request
def _require_login() -> werkzeug_wrappers.Response | tuple[str, int] | None:
    """Global auth gate — skipped for localhost, static files, and the /auth endpoint itself."""
    if request.endpoint in ('auth', 'static'):
        return None

    acl = auth.get_current_acl()
    if not acl:
        if request.path.startswith('/api/'):
            return make_response(
                json.dumps({'error': 'Unauthorized: Invalid or missing API key.'}),
                401,
                {'Content-Type': 'application/json'}
            )
        return redirect(url_for('auth', next=request.full_path if request.query_string else request.path))

    view_args = request.view_args or {}
    has_kartsim = (
        view_args.get('league') == 'kartsim' or
        request.path.startswith('/api/telemetry') or
        request.path.startswith('/api/track_data') or
        'kartsim' in request.args.getlist('leagues') or
        request.args.get('league') == 'kartsim' or
        any('kartsim' in s for s in request.args.getlist('session'))
    )
    if has_kartsim:
        if not acl.get('kartsim_data'):
            abort(403, description='Access to KartSim data is restricted')

    if request.path == '/gallery' or request.path.startswith('/gallery/'):
        if not acl.get('see_gallery'):
            abort(403, description='Access to gallery is restricted')

    return None


@app.after_request
def refresh_cookies(response: Response) -> Response:
    """Sliding session expiration - refresh the auth and settings cookies if they are valid."""
    if request.endpoint == 'static':
        return response

    key = request.cookies.get(auth.AUTH_COOKIE)
    if key and key in auth.load_keys():
        set_cookie_headers = response.headers.getlist('Set-Cookie')
        response_cookie_names = {h.split('=', 1)[0].strip() for h in set_cookie_headers if '=' in h}

        if auth.AUTH_COOKIE not in response_cookie_names:
            response.set_cookie(
                auth.AUTH_COOKIE,
                key,
                httponly=True,
                samesite='Lax',
                secure=not app.debug,
                max_age=COOKIE_MAX_AGE
            )

        for name in ('hero_pilot', 'disabled_heroes'):
            if name not in response_cookie_names:
                val = request.cookies.get(name)
                if val is not None:
                    response.set_cookie(
                        name,
                        val,
                        max_age=COOKIE_MAX_AGE,
                        samesite='Lax',
                        secure=not app.debug
                    )

    return response


@app.after_request
def set_security_headers(response: Response) -> Response:
    """Set standard HTTP security headers on all responses."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return response


def _is_safe_url(target: str) -> bool:
    """Return True only if *target* resolves to a path on the same host.

    Prevents open-redirect attacks where an attacker supplies an absolute URL
    (e.g. ``https://evil.com``) as the ``next`` parameter.
    """
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


@app.route('/auth', methods=['GET', 'POST'], endpoint='auth')
def auth_route() -> werkzeug_wrappers.Response | str:
    """Authenticate via ?key=... query parameter or a POST form."""
    valid_keys = auth.load_keys()
    raw_next = request.args.get('next') or request.form.get('next') or ''
    if raw_next and _is_safe_url(raw_next) and urlparse(urljoin(request.host_url, raw_next)).path != url_for('auth'):
        next_url = raw_next
    else:
        next_url = url_for('index')

    if request.method == 'POST':
        key = request.form.get('key', '')
        if key in valid_keys:
            resp = make_response(redirect(next_url))
            resp.set_cookie(
                auth.AUTH_COOKIE,
                key,
                httponly=True,
                samesite='Lax',
                secure=not app.debug,
                max_age=COOKIE_MAX_AGE
            )
            return resp
        return render_template('auth.html', error='Invalid access key', next_url=next_url)

    # Handle GET
    key = request.args.get('key', '')
    if key and key in valid_keys:
        resp = make_response(redirect(next_url))
        resp.set_cookie(
            auth.AUTH_COOKIE,
            key,
            httponly=True,
            samesite='Lax',
            secure=not app.debug,
            max_age=COOKIE_MAX_AGE
        )
        return resp

    # If already logged in, just go to next_url
    current_key = request.cookies.get(auth.AUTH_COOKIE)
    if current_key in valid_keys:
        return redirect(next_url)

    return render_template('auth.html', next_url=next_url)


@app.route('/auth_builder', endpoint='auth_builder')
def auth_builder() -> str:
    """Page to construct shareable authentication redirect URLs."""
    return render_template('auth_builder.html')


VIDEO_CACHE_DIR: str = config.get('video_cache_dir') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'video_cache'
)
os.makedirs(VIDEO_CACHE_DIR, exist_ok=True)


def format_time(s: str | float | None) -> str:
    """Formats seconds into MM:SS.sss or SS.sss string.

    Returns '-' for zero and 'N/A' for None/NaN."""
    if s is None or pd.isna(s):
        return 'N/A'
    val = float(s)
    if val == 0:
        return '-'
    mins = int(val // 60)
    secs = val % 60
    if mins > 0:
        return f'{mins}:{secs:06.3f}'
    return f'{secs:6.3f}'


def calculate_gaps(drivers_info: list[dict], is_final: bool) -> None:
    """
    Calculates and adds 'gap_leader' and 'gap_next' strings to each driver dict.
    Drivers must be pre-sorted by position.
    """
    min_best = min((d.get('best_lap_raw', float('nan')) for d in drivers_info
                    if pd.notna(d.get('best_lap_raw'))), default=float('nan'))

    prev_gap_to_leader = 0.0
    for i, d in enumerate(drivers_info):
        if is_final:
            current_gap_to_leader = sanitize.parse_time(d.get('gap_raw'))
        else:
            if pd.notna(d.get('best_lap_raw')) and pd.notna(min_best):
                current_gap_to_leader = d['best_lap_raw'] - min_best
            else:
                current_gap_to_leader = None

        if i == 0 or d.get('pos') in ['DNS', 'DNF', 'DSQ', 'EXCL'] or current_gap_to_leader is None:
            d['gap_leader'] = '-'
            d['gap_next'] = '-'
            if current_gap_to_leader is not None:
                prev_gap_to_leader = current_gap_to_leader
        else:
            gap_to_next = current_gap_to_leader - prev_gap_to_leader
            if gap_to_next < 0:
                gap_to_next = 0.0
            d['gap_leader'] = format_time(current_gap_to_leader)
            d['gap_next'] = format_time(gap_to_next)
            prev_gap_to_leader = current_gap_to_leader


def _get_session_results(df: pd.DataFrame, session_id: str | None) -> dict[str, dict[str, Any]]:
    """Extract results map (Name -> {points, pos, preliminary}) for a specific session."""
    if not session_id:
        return {}
    s_df = df[df['SessionID'] == session_id].drop_duplicates('Name')

    results = {}
    for _, row in s_df.iterrows():
        # Check both 'ChampPoints' and 'CPts' columns which may be available in the data
        pts = '0'
        for col in ['ChampPoints', 'CPts']:
            if col in row and pd.notna(row[col]):
                try:
                    pts = str(int(float(row[col])))
                    break
                except (ValueError, TypeError):
                    pass

        results[row['Name']] = {
            'points': pts,
            'pos': str(row['Pos']),
            'preliminary': True
        }
    return results


def augment_championship_data(championship: dict, league: str, class_name: str, year: str | None) -> None:
    """Adds preliminary results from local data to the championship standings if rounds are missing."""
    if not championship or 'standings' not in championship:
        return

    all_meetings = db.list_meetings(league, year=year, class_name=class_name)
    unique_meetings = sorted(list(set((m[1], m[2]) for m in all_meetings)))

    # Filter unique meetings to only include those that actually have a pre-final or final session,
    # so they correspond correctly to actual championship rounds.
    valid_meetings = []
    for m_date, m_track in unique_meetings:
        sessions = db.find_sessions(leagues=league, classes=class_name, date=m_date, track=m_track)
        pf_s = next((s for s in sessions if 'pre' in s.session_name.lower() and 'final' in s.session_name.lower()),
                    None)
        f_s = next((s for s in sessions
                    if 'pre' not in s.session_name.lower() and 'final' in s.session_name.lower()),
                   None)
        if pf_s or f_s:
            valid_meetings.append((m_date, m_track))

    rounds_list = sorted(championship['standings'][0]['rounds'].keys(), key=lambda x: (len(x), x))

    # Identify rounds already containing official data
    filled_rounds = set()
    for entry in championship['standings']:
        for r_id, r_data in entry['rounds'].items():
            if any(s.get('points') or s.get('pos') for s in r_data.values() if isinstance(s, dict)):
                filled_rounds.add(r_id)

    # Process meetings chronologically and map to available rounds
    for i, (m_date, m_track) in enumerate(valid_meetings):
        if i >= len(rounds_list):
            break

        round_id = rounds_list[i]
        if round_id not in filled_rounds:
            sessions = db.find_sessions(leagues=league, classes=class_name, date=m_date, track=m_track)
            pf_s = next((s for s in sessions if 'pre' in s.session_name.lower() and 'final' in s.session_name.lower()),
                        None)
            f_s = next((s for s in sessions
                        if 'pre' not in s.session_name.lower() and 'final' in s.session_name.lower()),
                       None)

            if not pf_s and not f_s:
                continue

            df_all = db.load(leagues=league, classes=class_name, date=m_date, track=m_track)
            pf_results = _get_session_results(df_all, pf_s.session_id if pf_s else None)
            f_results = _get_session_results(df_all, f_s.session_id if f_s else None)

            for entry in championship['standings']:
                name = entry['name']
                r_data = entry['rounds'].get(round_id, {})

                updated = False
                if not r_data.get('PF', {}).get('points') and name in pf_results:
                    r_data['PF'] = pf_results[name]
                    updated = True
                if not r_data.get('F', {}).get('points') and name in f_results:
                    r_data['F'] = f_results[name]
                    updated = True

                if updated:
                    entry['rounds'][round_id] = r_data

    # Identify the past rounds (rounds that have already occurred)
    past_rounds = set(rounds_list[:len(valid_meetings)]).union(filled_rounds)
    championship['past_rounds'] = list(past_rounds)

    # Get the configured number of drop rounds per championship (default to 2)
    drop_rounds_count = championship.get('drop_rounds', 2)

    # Normalize keys and compute totals for all drivers
    for entry in championship['standings']:
        if 'total_to_count' not in entry and 'pts' in entry:
            entry['total_to_count'] = entry['pts']
        if 'total_all_rounds' not in entry and 'total' in entry:
            entry['total_all_rounds'] = entry['total']

        total_all = 0
        total_official = 0
        round_totals = {}
        past_round_scores = []
        missed_rounds = []

        for r_name in rounds_list:
            r_content = entry.get('rounds', {}).get(r_name, {})
            round_score = 0
            has_round_results = False
            for _, s_content in r_content.items():
                if s_content is not None:
                    pts = s_content.get('points')
                    pos = s_content.get('pos')
                    if pts or pos:
                        has_round_results = True
                    if pts:
                        try:
                            pts_val = int(float(pts))
                        except ValueError:
                            continue
                        round_score += pts_val
                        total_all += pts_val
                        if not s_content.get('preliminary'):
                            total_official += pts_val

            if not has_round_results:
                missed_rounds.append(r_name)

            round_totals[r_name] = round_score
            if r_name in past_rounds:
                past_round_scores.append((r_name, round_score))

        # Sort past rounds by score ascending to identify drop rounds
        sorted_past_rounds = sorted(past_round_scores, key=lambda x: x[1])
        num_drops = min(drop_rounds_count, len(past_round_scores))
        drop_rounds = [r_name for r_name, _ in sorted_past_rounds[:num_drops]]

        entry['total_all_rounds'] = str(total_all)
        entry['total_all_rounds_official'] = str(total_official)
        entry['round_totals'] = round_totals
        entry['drop_rounds'] = drop_rounds
        entry['missed_rounds'] = missed_rounds

    # Re-sort based on updated totals and re-assign positions
    def get_sort_val(e: dict) -> float:
        try:
            return float(e.get('total_all_rounds', '0'))
        except ValueError:
            return 0.0

    championship['standings'].sort(key=get_sort_val, reverse=True)
    for i, entry in enumerate(championship['standings']):
        entry['pos'] = str(i + 1)


def get_gallery_url() -> str:
    if 'gallery_url' in g:
        return g.gallery_url

    view_args = request.view_args or {}
    league = view_args.get('league') or request.args.get('league')
    meeting_date = view_args.get('date') or request.args.get('meeting')

    url = url_for('gallery.gallery')
    if league and meeting_date and gallery_handlers.has_meeting_gallery(league, meeting_date):
        url = url_for('gallery.gallery', league=league, meeting=meeting_date)
    elif league and gallery_handlers.has_league_gallery(league):
        url = url_for('gallery.gallery', league=league)

    g.gallery_url = url
    return url


@app.context_processor
def utility_processor() -> dict[str, Any]:
    hero_pilot = request.cookies.get('hero_pilot', '')
    hero_pilots = [p.strip().strip('\"\'') for p in hero_pilot.split(',') if p.strip()]
    return dict(
        get_league_name=aliases.get_league_name,
        get_league_color=aliases.get_league_color,
        get_class_info=aliases.get_class_info,
        format_time=format_time,
        HERO_NAMES=hero_pilots,
        get_current_acl=auth.get_current_acl,
        get_gallery_url=get_gallery_url,
        has_meeting_gallery=gallery_handlers.has_meeting_gallery,
    )


@app.route('/about')
def about() -> str:
    return render_template('about.html')


@app.route('/settings')
def settings() -> str:
    hero_pilot = request.cookies.get('hero_pilot', '')
    disabled_heroes = request.cookies.get('disabled_heroes', '')
    # Get the URL to return to after saving.
    # Use 'next' parameter if present, otherwise use Referer header.
    raw_next: str = request.args.get('next') or request.referrer or ''

    # Reject unsafe (off-host) redirects and disallow looping back to settings.
    if raw_next and _is_safe_url(raw_next) and url_for('settings') not in raw_next and '/save_settings' not in raw_next:
        next_url: str | None = raw_next
    else:
        next_url = None

    return render_template(
        'settings.html',
        hero_pilot=hero_pilot,
        disabled_heroes=disabled_heroes,
        next_url=next_url
    )


@app.route('/save_settings', methods=['POST'])
def save_settings() -> Response:
    # Handle both single input and potentially multiple inputs with the same name
    hero_pilots = request.form.getlist('hero_pilot')
    if not hero_pilots:
        hero_pilots = [request.form.get('hero_pilot', '')]

    # Clean up and join back into comma-separated string
    cleaned_pilots = []
    for p in hero_pilots:
        cleaned_pilots.extend([part.strip() for part in p.split(',') if part.strip()])

    # Deduplicate while preserving order
    seen = set()
    final_pilots = []
    for p in cleaned_pilots:
        if p not in seen:
            final_pilots.append(p)
            seen.add(p)

    hero_pilot_str = ', '.join(final_pilots)

    # Handle disabled heroes
    disabled_list = request.form.getlist('disabled_hero')
    cleaned_disabled = []
    for d in disabled_list:
        cleaned_disabled.extend([part.strip() for part in d.split(',') if part.strip()])

    seen_all = set(final_pilots)
    final_disabled = []
    for d in cleaned_disabled:
        if d not in seen_all and d not in final_disabled:
            final_disabled.append(d)

    disabled_str = ', '.join(final_disabled)

    raw_next_url = request.form.get('next_url') or ''
    next_url = raw_next_url if _is_safe_url(raw_next_url) else url_for('settings')
    resp = make_response(redirect(next_url))
    resp.set_cookie(
        'hero_pilot',
        hero_pilot_str,
        max_age=COOKIE_MAX_AGE,
        samesite='Lax',
        secure=not app.debug
    )
    resp.set_cookie(
        'disabled_heroes',
        disabled_str,
        max_age=COOKIE_MAX_AGE,
        samesite='Lax',
        secure=not app.debug
    )
    return resp


@app.route('/')
def index() -> werkzeug_wrappers.Response:
    return redirect(url_for('league_list'))


@app.route('/league')
def league_list() -> str:
    leagues = db.list_leagues()
    if not auth.get_current_acl().get('kartsim_data'):
        leagues = [lg for lg in leagues if lg != 'kartsim']
    grouped_leagues = aliases.group_leagues(leagues)
    return render_template('league_list.html', grouped_leagues=grouped_leagues)


@app.route('/league/<league>/last')
def last_meeting_redirect(league: str) -> werkzeug_wrappers.Response | str:
    if league == 'kartsim':
        hero_names = plot_handlers.get_hero_names()
        if not hero_names:
            return render_template('suggest_hero.html')

        # Find all sessions in KartSim matching these drivers
        sessions = db.find_sessions(leagues='kartsim', driver_names=hero_names)
        if not sessions:
            return render_template('suggest_hero.html')

        # Find the last session by start datetime
        last_session = max(sessions, key=lambda s: s.session_start_datetime)

        # Redirect to the meeting page
        selected_class = last_session.class_name[0] if last_session.class_name else 'cadet'
        return redirect(url_for(
            'location.telemetry_view',
            league='kartsim',
            class_name=selected_class,
            date=last_session.date,
            track=last_session.track_name
        ))

    # For other leagues
    meetings = db.list_meetings(league)
    if not meetings:
        abort(404, description=f'No meetings found for league {league}')

    # meetings is a list of (class_name, date, track_name)
    # Find latest meeting date
    latest_date = max(m[1] for m in meetings)
    latest_meetings = [m for m in meetings if m[1] == latest_date]
    track_name = latest_meetings[0][2]

    # If a hero driver has participated, choose the first class they participated in.
    # Otherwise default to cadet.
    hero_names = plot_handlers.get_hero_names()
    participated_class = None
    if hero_names:
        for m in latest_meetings:
            c_name = m[0]
            sessions = db.find_sessions(
                leagues=league,
                classes=c_name,
                date=latest_date,
                track=track_name,
                driver_names=hero_names
            )
            if sessions:
                participated_class = c_name
                break

    selected_class = participated_class if participated_class else 'cadet'
    return redirect(url_for(
        'meeting_view',
        league=league,
        class_name=selected_class,
        date=latest_date,
        track=track_name
    ))


@app.route('/league/<league>')
def league_view(league: str) -> str:
    hero_names = plot_handlers.get_hero_names() if league == 'kartsim' else None
    if hero_names:
        meetings = db.list_meetings(league, driver_names=hero_names)
    else:
        meetings = db.list_meetings(league)

    # meetings is a list of (class_name, date, track_name)
    classes = sorted(list(set(m[0] for m in meetings)))
    return render_template('league_classes.html', league=league, classes=classes)


@app.route('/league/<league>/<class_name>')
def class_view(league: str, class_name: str) -> str:
    years = db.list_years(league, class_name)

    if league == 'kartsim':
        hero_names = plot_handlers.get_hero_names()
        if hero_names:
            # We filter years by checking which years have sessions for the hero driver
            df_hero_all = db.load(leagues=league, classes=class_name, driver_names=tuple(hero_names))
            years = sorted(set(row['Date'].split('-')[0] for _, row in df_hero_all.iterrows()), reverse=True)

    # Get selected year from query param, default to latest year
    selected_year = request.args.get('year')
    if not selected_year and years:
        selected_year = years[0]

    # Use RaceDB's year filter to get meetings for selected year
    class_meetings = db.list_meetings(league, year=selected_year, class_name=class_name)

    if league == 'kartsim':
        hero_names = plot_handlers.get_hero_names()
        if hero_names:
            df_hero = db.load(leagues=league, classes=class_name, year=selected_year, driver_names=tuple(hero_names))
            # Use meeting keys (date, track) to filter the meeting list
            hero_meeting_keys = set((row['Date'], row['TrackName']) for _, row in df_hero.iterrows())
            class_meetings = [m for m in class_meetings if (m[1], m[2]) in hero_meeting_keys]

    # View mode: 'by_date' (default) or 'by_track' or 'activity'
    is_kartsim = (league == 'kartsim')
    view_mode = request.args.get('view', 'by_date')

    if view_mode == 'by_track':
        # Group by track using RaceDB's cached metadata
        track_to_dates: dict[str, set[str]] = {}
        track_to_conditions: dict[str, set[str]] = {}

        for c_name, date, track in class_meetings:
            if track not in track_to_dates:
                track_to_dates[track] = set()
                track_to_conditions[track] = set()

            track_to_dates[track].add(date)

            sessions = db.find_sessions(leagues=league, classes=class_name, date=date, track=track)
            for s in sessions:
                if s.track_conditions:
                    track_to_conditions[track].add(s.track_conditions)

        # Sort tracks alphabetically
        sorted_track_names = sorted(track_to_dates.keys())

        # Prepare track_groups for the template: list of (track, cond_str)
        # and track_groups lookup: track -> list of dates
        track_groups = {t: sorted(dates) for t, dates in track_to_dates.items()}
        sorted_track_keys = []
        for t in sorted_track_names:
            cond_str = ','.join(sorted(track_to_conditions[t])) if track_to_conditions[t] else 'Unknown'
            sorted_track_keys.append((t, cond_str))

        return render_template(
            'league.html',
            league=league,
            class_name=class_name,
            meeting_keys=[],
            meetings={},
            years=years,
            selected_year=selected_year,
            view_mode=view_mode,
            is_kartsim=is_kartsim,
            track_groups=track_groups,
            sorted_track_keys=sorted_track_keys,
            meeting_conditions={},
            championship=None
        )
    elif view_mode == 'activity':
        return render_template(
            'league.html',
            league=league,
            class_name=class_name,
            meeting_keys=[],
            meetings={},
            years=years,
            selected_year=selected_year,
            view_mode=view_mode,
            is_kartsim=is_kartsim,
            track_groups={},
            sorted_track_keys=[],
            meeting_conditions={},
            championship=None
        )
    else:
        # Default "by_date" view
        # Grouping by meeting (date + track)
        grouped_meetings: dict[tuple[str, str], list[str]] = {}
        meeting_conditions: dict[tuple[str, str], str] = {}
        for c_name, date, track in class_meetings:
            key = (date, track)
            if key not in grouped_meetings:
                grouped_meetings[key] = []
            grouped_meetings[key].append(c_name)

        for (date, track) in grouped_meetings.keys():
            conditions = set()
            sessions = db.find_sessions(leagues=league, classes=class_name, date=date, track=track)
            for s in sessions:
                if s.track_conditions:
                    conditions.add(s.track_conditions)
            cond_str = ','.join(sorted(conditions)) if conditions else 'Unknown'
            meeting_conditions[(date, track)] = cond_str

        # Sort by date descending
        sorted_meeting_keys = sorted(grouped_meetings.keys(), key=lambda x: x[0], reverse=True)

        championship = db.get_championship(league, class_name, selected_year) if selected_year else None
        if championship:
            augment_championship_data(championship, league, class_name, selected_year)

        return render_template(
            'league.html',
            league=league,
            class_name=class_name,
            meeting_keys=sorted_meeting_keys,
            meetings=grouped_meetings,
            years=years,
            selected_year=selected_year,
            view_mode=view_mode,
            is_kartsim=is_kartsim,
            track_groups={},
            sorted_track_keys=[],
            meeting_conditions=meeting_conditions,
            championship=championship
        )


@app.route('/meeting/<league>/<class_name>/<path:date>/<track>')
def meeting_view(league: str, class_name: str, date: str, track: str) -> str:
    sessions = db.find_sessions(leagues=league, classes=class_name, date=date, track=track)

    # track = sessions[0]['track_name'] if sessions else "" # Already got it from URL

    df = db.load(leagues=league, classes=class_name, date=date, track=track)
    df_pen = db.load_penalties(leagues=league, classes=class_name, date=date, track=track)
    plot_titles = plot_handlers.get_meeting_plot_titles(df, df_pen)

    # Get other classes for the same meeting to allow switching
    all_meetings = db.list_meetings(league)
    other_classes = sorted(list(set(m[0] for m in all_meetings if m[1] == date and m[2] == track)))

    # Find previous/next chronological meetings for the current class in this league
    meetings_for_class = db.list_meetings(league, class_name=class_name)
    meetings_for_class.sort(key=lambda m: m[1])

    current_idx = -1
    for i, m in enumerate(meetings_for_class):
        if m[1] == date and m[2] == track:
            current_idx = i
            break

    prev_meeting = meetings_for_class[current_idx - 1] if current_idx > 0 else None
    next_meeting = meetings_for_class[current_idx + 1] if current_idx < len(meetings_for_class) - 1 else None

    prev_meeting_url = url_for(
        'meeting_view',
        league=league,
        class_name=class_name,
        date=prev_meeting[1],
        track=prev_meeting[2]
    ) if prev_meeting else ''

    next_meeting_url = url_for(
        'meeting_view',
        league=league,
        class_name=class_name,
        date=next_meeting[1],
        track=next_meeting[2]
    ) if next_meeting else ''

    prev_meeting_title = f'{prev_meeting[1]} - {prev_meeting[2]}' if prev_meeting else ''
    next_meeting_title = f'{next_meeting[1]} - {next_meeting[2]}' if next_meeting else ''

    return render_template(
        'meeting.html',
        league=league,
        class_name=class_name,
        date=date,
        track=track,
        sessions=sessions,
        plot_titles=plot_titles,
        available_classes=other_classes,
        prev_meeting_url=prev_meeting_url,
        next_meeting_url=next_meeting_url,
        prev_meeting_title=prev_meeting_title,
        next_meeting_title=next_meeting_title
    )


@app.route('/meeting/<league>/<class_name>/<path:date>/<track>/socials')
def meeting_socials(league: str, class_name: str, date: str, track: str) -> str:
    hero_names = plot_handlers.get_hero_names()
    load_names = tuple(hero_names) if league == 'kartsim' else None
    sessions = db.find_sessions(leagues=league, classes=class_name, date=date, track=track)
    df = db.load(leagues=league, classes=class_name, date=date, track=track, driver_names=load_names)

    # Filter to only sessions that have actual lap data (matching plots._prepare_plot_df logic).
    # This ensures indices used in the sessions query param match plots.plot_violins_multisession.
    sessions_with_laps = set()
    if not df.empty:
        sessions_with_laps = set(df[(df['LapTimeSeconds'] > 0) & (df['Lap'] > 0)]['SessionID'].unique())

    all_social_sessions = sorted([s for s in sessions if s.session_id in sessions_with_laps],
                                 key=lambda s: s.session_start_datetime)

    # Group sessions by meeting folder and name to handle multiple meetings on the same day
    # (e.g. Practice vs Race meetings can be in the same directory but have different names)
    meetings_groups: dict[tuple[str, str], list[tuple[int, race_db.RaceMetadata]]] = {}
    for i, s in enumerate(all_social_sessions):
        key = (s.meeting_folder, s.meeting_name)
        if key not in meetings_groups:
            meetings_groups[key] = []
        meetings_groups[key].append((i, s))

    social_plots = []
    # Add summary plots per meeting (excluding finals)
    for key in sorted(meetings_groups.keys()):
        group = meetings_groups[key]
        summary_indices = [idx for idx, s in group if not sanitize.is_final_session(s.session_name)]

        if summary_indices:
            sample_session = group[0][1]
            title = 'Meeting Summary'
            if len(meetings_groups) > 1:
                title = f'{sample_session.meeting_name} Summary'

            # Determine session types for the summary filename
            has_practice = any(
                'practice' in s.session_name.lower() or
                'academy' in s.session_name.lower() or
                'testing' in s.session_name.lower()
                for _, s in group if not sanitize.is_final_session(s.session_name)
            )
            has_qualifying = any(re.search(r'quali(fying)?', s.session_name.lower())
                                 for _, s in group if not sanitize.is_final_session(s.session_name))

            if has_practice and has_qualifying:
                type_str = '2_practice_qualifying'
            elif has_practice:
                type_str = '3_practice'
            elif has_qualifying:
                type_str = '4_qualifying'
            else:
                type_str = '5_summary'

            type_slug = type_str
            if re.search(r'\bAM\b', sample_session.meeting_name):
                type_slug += '_am'
            elif re.search(r'\bPM\b', sample_session.meeting_name):
                type_slug += '_pm'

            download_name = (f'{sanitize.clean_slug(date)}_{sanitize.clean_slug(league)}_'
                             f'{sanitize.clean_slug(track)}_{sanitize.clean_slug(class_name)}_{type_slug}')

            social_plots.append({
                'title': title,
                'url': url_for('plots.violin_plot', league=league, class_name=class_name,
                               date=date, track=track, sessions=','.join(map(str, summary_indices)), aspect='4:5'),
                'download_name': download_name,
                'session_links': [
                    url_for(
                        'session_view', league=league, class_name=class_name,
                        date=date, track=track, session_id=all_social_sessions[idx].session_id
                    )
                    for idx in summary_indices
                ]
            })

    # Individual Social plots (Pre-Finals/Finals)
    for s in sorted(sessions, key=lambda s: s.session_start_datetime):
        name = sanitize.strip_race_prefix(s.session_name)
        low_name = name.lower()
        if sanitize.is_final_session(low_name):
            session_path = f'{league}/{class_name}/{date}/{track}/{s.session_id}'

            if 'pre' in low_name:
                type_slug = '1_pre_final'
            elif 'heat' in low_name:
                type_slug = sanitize.clean_slug(name)
            else:
                type_slug = '0_final'
            if re.search(r'\bAM\b', s.meeting_name):
                type_slug += '_am'
            elif re.search(r'\bPM\b', s.meeting_name):
                type_slug += '_pm'

            download_name = (f'{sanitize.clean_slug(date)}_{sanitize.clean_slug(league)}_'
                             f'{sanitize.clean_slug(track)}_{sanitize.clean_slug(class_name)}_{type_slug}')
            social_plots.append({
                'title': f'{name} Social',
                'url': url_for('plots.combined_plot', session=session_path, aspect='4:5'),
                'download_name': download_name,
                'main_link': url_for(
                    'session_view', league=league, class_name=class_name,
                    date=date, track=track, session_id=s.session_id
                )
            })

    # Get other classes for the same meeting to allow switching
    all_meetings = db.list_meetings(league)
    other_classes = sorted(list(set(m[0] for m in all_meetings if m[1] == date and m[2] == track)))

    # Find previous/next chronological meetings for the current class in this league
    meetings_for_class = db.list_meetings(league, class_name=class_name)
    meetings_for_class.sort(key=lambda m: m[1])

    current_idx = -1
    for i, m in enumerate(meetings_for_class):
        if m[1] == date and m[2] == track:
            current_idx = i
            break

    prev_meeting = meetings_for_class[current_idx - 1] if current_idx > 0 else None
    next_meeting = meetings_for_class[current_idx + 1] if current_idx < len(meetings_for_class) - 1 else None

    prev_meeting_url = url_for(
        'meeting_socials',
        league=league,
        class_name=class_name,
        date=prev_meeting[1],
        track=prev_meeting[2]
    ) if prev_meeting else ''

    next_meeting_url = url_for(
        'meeting_socials',
        league=league,
        class_name=class_name,
        date=next_meeting[1],
        track=next_meeting[2]
    ) if next_meeting else ''

    prev_meeting_title = f'{prev_meeting[1]} - {prev_meeting[2]}' if prev_meeting else ''
    next_meeting_title = f'{next_meeting[1]} - {next_meeting[2]}' if next_meeting else ''

    return render_template(
        'meeting_socials.html',
        league=league,
        class_name=class_name,
        date=date,
        track=track,
        social_plots=social_plots,
        available_classes=other_classes,
        prev_meeting_url=prev_meeting_url,
        next_meeting_url=next_meeting_url,
        prev_meeting_title=prev_meeting_title,
        next_meeting_title=next_meeting_title
    )


@app.route('/session/<league>/<class_name>/<date>/<track>/<session_id>')
def session_view(league: str, class_name: str, date: str, track: str, session_id: str) -> str:
    # Load session data
    hero_names = plot_handlers.get_hero_names()
    load_names = tuple(hero_names) if league == 'kartsim' else None
    df = db.load(leagues=league, classes=class_name, date=date, track=track, driver_names=load_names)
    if df.empty:
        abort(404)

    session_data = df[df['SessionID'] == session_id]
    if session_data.empty:
        abort(404)

    # Load penalties
    penalties_df = db.load_penalties(leagues=league, classes=class_name, date=date, track=track)
    session_pen = penalties_df[penalties_df['SessionID'] == session_id] if not penalties_df.empty else pd.DataFrame()
    penalties_list = session_pen.to_dict('records')

    # session_data is in long format: Lap, LapTime, LapTimeSeconds, League, Class, Date,
    # TrackName, SessionID, DriverName, ...
    session_name = sanitize.strip_race_prefix(session_data.iloc[0].get('SessionName', session_id))
    # 1. Collect initial stats and ranking metrics for all drivers
    drivers_info = []
    for name, group in session_data.groupby('Name', sort=False):
        # Best lap (excluding Lap 0 and Deleted laps)
        valid_laps = group[(group['Lap'] > 0) & (~group['LapTimeDeleted'])]
        best_lap_raw = valid_laps['LapTimeSeconds'].min() if not valid_laps.empty else float('nan')

        # Original race laps (excluding Lap 0)
        all_laps = group[group['Lap'] > 0]

        # Penalty summary
        d_pens = [p for p in penalties_list if p['Name'] == name]
        excluded = any(p.get('Excluded', False) for p in d_pens)

        pos = group['Pos'].iloc[0]
        pos_gained = group['PositionsGained'].iloc[0]

        # Calculate consistency (Standard Deviation)
        # We only look at 'flying' laps (Lap 2+) because Lap 1 is typically a slower,
        # start-dependent lap that would skew the consistency metric.
        actual_laps = all_laps[all_laps['Lap'] > 1]['LapTimeSeconds']
        consistency = actual_laps.std() if len(actual_laps) > 1 else 0.0

        drivers_info.append({
            'name': name,
            'best_lap': format_time(best_lap_raw) if not pd.isna(best_lap_raw) else 'N/A',
            'best_lap_raw': best_lap_raw,
            'consistency': f'{consistency:.3f}' if consistency > 0 else 'N/A',
            'count': len(all_laps),
            'excluded': bool(excluded),
            'pos': str(pos),  # pos can be 'DNS', so keep as string
            'pos_gained': int(pos_gained) if not pd.isna(pos_gained) else 0,
            'gap_raw': group['Gap'].iloc[0] if 'Gap' in group.columns else None
        })

    # 2. Final Ranking
    # Handle mixed types (int vs string like 'DNS') for sorting
    def sort_pos(d: dict[str, Any]) -> tuple[int, int | str]:
        try:
            return (0, int(d['pos']))
        except (ValueError, TypeError):
            return (1, str(d['pos']))

    drivers_info.sort(key=sort_pos)
    calculate_gaps(drivers_info, sanitize.is_final_session(session_name))

    min_best = min((d['best_lap_raw'] for d in drivers_info if pd.notna(d['best_lap_raw'])), default=float('nan'))
    drivers = []
    for d in drivers_info:
        drivers.append({
            **d,
            'pos': d['pos'] if not d['excluded'] else 'EXCL',
            'is_fastest': bool(d['best_lap_raw'] == min_best) if not pd.isna(min_best) else False
        })

    # Prepare charts data (using alphabetical or original best lap order for colors but consistent labels)
    lap_data = {}
    for driver in drivers:
        name = driver['name']
        d_laps = session_data[(session_data['Name'] == name) & (session_data['Lap'] > 0)].sort_values('Lap')
        lap_data[name] = {
            'laps': d_laps['Lap'].tolist(),
            'times': d_laps['LapTimeSeconds'].tolist()
        }

    dt = session_data.iloc[0].get('DateTime', '')
    time_part = dt.split(' ')[1] if dt and isinstance(dt, str) and ' ' in dt else ''

    session_metadata = {
        'session_id': session_id,
        'session_name': session_name,
        'league': league,
        'class': class_name,
        'date': date,
        'time': time_part,
        'track': session_data.iloc[0].get('TrackName', ''),
        'track_conditions': session_data.iloc[0].get('TrackConditions', ''),
        'temperature': session_data.iloc[0].get('Temperature', ''),
        'weather': session_data.iloc[0].get('Weather', '').replace('_', ' ').capitalize(),
        'alphatiming_url': session_data.iloc[0].get('AlphaTimingURL', ''),
        'is_final': sanitize.is_final_session(session_name)
    }

    # Find overlapping videos and navigation
    overlapping_videos = []
    if auth.get_current_acl().get('see_videos'):
        s_meta_list = db.find_sessions(leagues=league, classes=class_name, date=date, track=track)
        s_meta_list.sort(key=lambda s: s.session_start_datetime)

        current_idx = next((i for i, s in enumerate(s_meta_list) if s.session_id == session_id), -1)
        prev_session = s_meta_list[current_idx - 1] if current_idx > 0 else None
        next_session = s_meta_list[current_idx + 1] if current_idx < len(s_meta_list) - 1 else None

        s_meta = s_meta_list[current_idx] if current_idx != -1 else None
        if s_meta:
            raw_videos = db.get_overlapping_videos(s_meta, session_data)
            for ov in raw_videos:
                ov['url'] = url_for(
                    'gallery.serve_video', league=league, class_name=class_name,
                    date=date, track=track, filename=ov['filename']
                )
                ov['basename'] = os.path.basename(ov['filename'])
                video_base, _ = os.path.splitext(ov['filename'])
                ov['thumbnail_url'] = url_for(
                    'gallery.serve_video', league=league, class_name=class_name,
                    date=date, track=track, filename=video_base + '.small.jpg'
                )

            # Group videos by source
            grouped_videos: dict[str, dict[str, Any]] = {}
            non_grouped_videos: list[dict[str, Any]] = []

            for v in raw_videos:
                basename = os.path.basename(v['filename'])
                # Expected format: 260502_170943_r5_0003C898.mp4 (4 parts)
                parts = basename.split('_')
                source_id = parts[2].lower() if len(parts) == 4 else None
                source_name = aliases.get_video_source_name(source_id) if source_id else None

                if source_id and source_name:
                    if source_id not in grouped_videos:
                        grouped_videos[source_id] = {
                            'source_id': source_id,
                            'source_name': source_name,
                            'videos': []
                        }
                    grouped_videos[source_id]['videos'].append(v)
                else:
                    non_grouped_videos.append(v)

            # Sort groups by the order in aliases.VIDEO_SOURCE_NAMES
            sorted_groups = []
            for sid in aliases.VIDEO_SOURCE_NAMES:
                if sid in grouped_videos:
                    sorted_groups.append(grouped_videos[sid])

            overlapping_videos = sorted_groups + non_grouped_videos
    else:
        s_meta_list = db.find_sessions(leagues=league, classes=class_name, date=date, track=track)
        s_meta_list.sort(key=lambda s: s.session_start_datetime)

        current_idx = next((i for i, s in enumerate(s_meta_list) if s.session_id == session_id), -1)
        prev_session = s_meta_list[current_idx - 1] if current_idx > 0 else None
        next_session = s_meta_list[current_idx + 1] if current_idx < len(s_meta_list) - 1 else None

    has_telemetry = False
    if s_meta_list:
        has_telemetry = db.has_telemetry(s_meta_list[0].meeting_dir, session_id)

    return render_template(
        'session.html',
        metadata=session_metadata,
        drivers=drivers,
        lap_data=lap_data,
        penalties=penalties_list,
        overlapping_videos=overlapping_videos,
        prev_session=prev_session,
        next_session=next_session,
        has_telemetry=has_telemetry
    )


@app.route('/track_sessions/<league>/<class_name>/<track>')
@app.route('/track_sessions/<league>/<class_name>/<track>/<track_conditions>')
def track_sessions_view(league: str, class_name: str, track: str, track_conditions: str | None = None) -> str:
    """Show a single combined violin plot for a track, with dates on x-axis."""
    selected_year = request.args.get('year')

    cond_query = None
    if track_conditions:
        cond_query = tuple(track_conditions.split(','))

    all_sessions = db.find_sessions(
        leagues=league, classes=class_name, track=track,
        year=selected_year, track_conditions=cond_query
    )

    if league == 'kartsim':
        hero_names = plot_handlers.get_hero_names()
        if hero_names:
            df_hero = db.load(
                leagues=league, classes=class_name, track=track,
                year=selected_year, track_conditions=cond_query,
                driver_names=tuple(hero_names)
            )
            hero_session_ids = set(df_hero['SessionID'].unique())
            all_sessions = [s for s in all_sessions if s.session_id in hero_session_ids]

    # Group sessions by date
    dates = sorted(set(s.date for s in all_sessions))

    return render_template(
        'track_sessions.html',
        league=league,
        class_name=class_name,
        track=track,
        track_conditions=track_conditions,  # Pass the actual value (could be None)
        display_conditions=track_conditions or 'All Conditions',
        dates=dates,
        selected_year=selected_year
    )


@app.route('/driver/<name>')
def driver_view(name: str) -> str:
    # Load all data to find this driver across all sessions
    # This will be fast after the first load due to lru_cache in RaceDB
    df = db.load()
    if not auth.get_current_acl().get('kartsim_data'):
        df = df[df['League'] != 'kartsim']
    if df.empty:
        abort(404)

    driver_df = df[(df['Name'] == name) & (df['Lap'] > 0)]
    if driver_df.empty:
        abort(404)

    # Process for the UI
    # We want: League -> Track -> Date -> Best Time
    # Group by (League, Track, Date) and take minimum LapTimeSeconds
    best_per_day = driver_df.groupby(['League', 'TrackName', 'Date', 'Class']).agg({
        'LapTimeSeconds': 'min',
        'SessionID': 'first'  # For linking (but maybe meeting link is better)
    }).reset_index()

    # Sort by date (descending)
    best_per_day = best_per_day.sort_values('Date', ascending=False)

    # Structure for template: {League: {Track: {GroupedClass: [entries]}}}
    leagues: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = {}
    for _, row in best_per_day.iterrows():
        # Grouping logic
        original_league_id = row['League']
        league_id = aliases.get_league_group(original_league_id)

        track = row['TrackName']
        if league_id not in leagues:
            leagues[league_id] = {}
        if track not in leagues[league_id]:
            leagues[league_id][track] = {}

        # Class grouping logic
        # For non-KartSim leagues, we squash heavy/lightweight categories that use the same kart.
        # Mixed practice sessions are common, so we group them together.
        # For KartSim, we don't aggregate because the karts/classes are different models.
        class_id = row['Class']
        class_info = aliases.get_class_info(class_id)
        group_name = class_info.name

        # Remove weighting suffixes to group similar karts (e.g. "Cadet Lightweight" -> "Cadet")
        group_name = re.sub(
            r'\s+(super\s+light(?:weight)?|light(?:weight)?|heavyweight)$', '', group_name, flags=re.IGNORECASE
        )

        if group_name not in leagues[league_id][track]:
            leagues[league_id][track][group_name] = []

        leagues[league_id][track][group_name].append({
            'date': row['Date'],
            'best_time': row['LapTimeSeconds'],
            'class': row['Class'],
            'session_id': row['SessionID'],
            'league': original_league_id  # Keep original for links
        })

    # Sort leagues alphabetically by their display name
    leagues = dict(sorted(leagues.items(), key=lambda x: aliases.get_league_name(x[0])))

    # Also sort tracks and groups alphabetically
    for league_id in leagues:
        leagues[league_id] = dict(sorted(leagues[league_id].items()))
        for track in leagues[league_id]:
            leagues[league_id][track] = dict(sorted(leagues[league_id][track].items()))

    # === Progression Data ===
    session_cols = ['League', 'Class', 'Date', 'SessionID']
    valid_laps = df[(df['Lap'] > 0) & (~df['LapTimeDeleted'])]
    session_best = valid_laps.groupby(session_cols)['LapTimeSeconds'].min()

    driver_valid_laps = driver_df[~driver_df['LapTimeDeleted']]
    driver_best_per_session = driver_valid_laps.groupby(session_cols).agg({
        'DateTime': 'first',
        'SessionName': 'first',
        'LapTimeSeconds': 'min',
        'TrackName': 'first',
        'TrackConditions': 'first',
        'Pos': 'first',
        'Gap': 'first'
    }).reset_index()

    # Calculate participants per session for normalization
    session_participants = df.groupby(session_cols)['Name'].nunique()

    progression_data = []
    for _, row in driver_best_per_session.iterrows():
        session_id = row['SessionID']
        session_key = (row['League'], row['Class'], row['Date'], session_id)
        session_name = row['SessionName']
        is_final = sanitize.is_final_session(session_name)

        gap = None
        if is_final:
            gap = sanitize.parse_time(row.get('Gap'))

        # Fallback to best lap gap if it's not a final or if gap is still None
        if gap is None:
            leader_time = session_best.get(session_key)
            if pd.notna(leader_time) and pd.notna(row['LapTimeSeconds']):
                gap = row['LapTimeSeconds'] - leader_time

        if gap is None:
            continue

        league_id = aliases.get_league_group(row['League'])
        group_name = aliases.get_consolidated_class(row['Class'])

        dt = row['DateTime']
        if pd.isna(dt) or not dt:
            dt = row['Date']

        progression_data.append({
            'date_time': dt,
            'date': row['Date'],
            'session_name': sanitize.strip_race_prefix(row['SessionName']),
            'track': row['TrackName'],
            'league': row['League'],  # Original ID (e.g. kartsim)
            'league_name': aliases.get_league_name(row['League']),
            'league_group': league_id,  # Group (e.g. other)
            'class': group_name,
            'orig_class': row['Class'],
            'orig_league': row['League'],
            'session_id': session_id,
            'gap': round(gap, 3),
            'track_conditions': str(row.get('TrackConditions', '') or ''),
            'pos': row['Pos'],
            'participants': int(session_participants.get(session_key, 1))
        })

    progression_data.sort(key=lambda x: str(x['date_time']))

    # Get unique tracks for filtering
    tracks_list = sorted(list(set(d['track'] for d in progression_data)))

    # Get unique years for filtering
    years_list = sorted(
        list(set(
            d['date'].split('-')[0]
            for d in progression_data
            if d.get('date') and '-' in d['date']
        )),
        reverse=True
    )

    return render_template(
        'driver.html',
        name=name,
        leagues=leagues,
        progression=progression_data,
        tracks_list=tracks_list,
        years_list=years_list
    )


@app.route('/drivers')
def drivers_list() -> str:
    driver_names = db.get_driver_names()

    # Group by first letter
    drivers_grouped: dict[str, list[str]] = {}
    for name in driver_names:
        if not name:
            continue
        first_letter = name[0].upper()
        if first_letter not in drivers_grouped:
            drivers_grouped[first_letter] = []
        drivers_grouped[first_letter].append(name)

    # Sort groups by letter
    drivers_grouped = dict(sorted(drivers_grouped.items()))

    return render_template('drivers_list.html', drivers_grouped=drivers_grouped)


if __name__ == '__main__':
    app.run(debug=True, port=5000)
