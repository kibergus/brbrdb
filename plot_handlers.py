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

import hashlib
import pandas as pd
from flask import Blueprint, Response, request, abort, jsonify
from typing import TypedDict, Any, Sequence, Iterable, Hashable, Set
import auth
import plots
from database import db
from race_tools import sanitize

plots_blueprint = Blueprint('plots', __name__)


class SessionPlotInfo(TypedDict):
    title: str
    session_id: str
    plots: list[str]


class PlotCache:
    """In-memory plot cache supporting session-level targeted invalidation."""

    def __init__(self, maxsize: int = 128) -> None:
        self.maxsize = maxsize
        self._cache: dict[Hashable, tuple[Any, Set[str]]] = {}

    def get(self, key: Hashable) -> Any | None:
        if key in self._cache:
            val, session_ids = self._cache.pop(key)
            self._cache[key] = (val, session_ids)  # MRU order
            return val
        return None

    def set(self, key: Hashable, val: Any, session_ids: Iterable[str] | None = None) -> None:
        if len(self._cache) >= self.maxsize and key not in self._cache:
            first_key = next(iter(self._cache))
            del self._cache[first_key]
        s_ids = set(session_ids) if session_ids else set()
        self._cache[key] = (val, s_ids)

    def invalidate(self, session_ids: str | Sequence[str] | Set[str] | None = None) -> None:
        """Invalidate cached entries. If session_ids is None, clears all entries.
        Otherwise clears entries whose session_ids set overlaps with target session_ids.
        """
        if session_ids is None:
            self._cache.clear()
            return

        if isinstance(session_ids, str):
            target_ids = {session_ids}
        else:
            target_ids = set(session_ids)

        if not target_ids:
            return

        keys_to_del = [
            k for k, (_, s_ids) in self._cache.items()
            if not s_ids or bool(s_ids & target_ids)
        ]
        for k in keys_to_del:
            del self._cache[k]


plot_cache = PlotCache(maxsize=256)


def invalidate_plot_cache(session_ids: str | Sequence[str] | set[str] | None = None) -> None:
    """Public helper to invalidate plot cache for specific session IDs or all entries."""
    plot_cache.invalidate(session_ids)


def make_plot_response(png_bytes: bytes, filename: str | None = None) -> Response:
    """Build a Flask Response with ETag and Cache-Control headers for plot images."""
    etag = f'"{hashlib.md5(png_bytes).hexdigest()}"'
    headers = {
        'Cache-Control': 'no-cache, must-revalidate',
        'ETag': etag,
    }
    if filename:
        headers['Content-Disposition'] = f'inline; filename={filename}'

    if_none_match = request.headers.get('If-None-Match')
    if if_none_match and if_none_match == etag:
        return Response(status=304, headers=headers)

    return Response(png_bytes, mimetype='image/png', headers=headers)


def get_hero_names() -> list[str]:
    """Extract hero pilot names from cookies as a list."""
    hero_pilot = request.cookies.get('hero_pilot', '')
    return [p.strip().strip('\"\'') for p in hero_pilot.split(',') if p.strip()]


def _generate_plot_filename(dfs: list[pd.DataFrame], default_prefix: str) -> str:
    """Generate a descriptive filename for a plot based on the session data."""
    if not dfs:
        return f'{default_prefix}.png'

    if len(dfs) == 1:
        sample = dfs[0].iloc[0]
        date = sample.get('Date', 'date')
        track = sample.get('TrackName', 'track')
        session = sample.get('SessionID', 'session')
        name = f'{date}_{track}_{session}'
    else:
        meetings = set((df.iloc[0]['Date'], df.iloc[0]['TrackName']) for df in dfs)
        name = dfs[0].iloc[0].get('TrackName', 'meeting') if len(meetings) == 1 else default_prefix

    return f'{name}.png'.replace(' ', '_').replace(':', '_')


def get_meeting_plot_titles(df: pd.DataFrame, df_pen: pd.DataFrame) -> list[SessionPlotInfo]:
    """Return a list of plot info for each session."""
    if df.empty:
        return []

    # Sort and get unique sessions by full identity to preserve ALL sessions
    session_cols = ['League', 'Class', 'Date', 'SessionID']
    sessions_metadata = df.sort_values('DateTime').groupby(session_cols, sort=False).first().reset_index()

    sessions: list[SessionPlotInfo] = []
    for _, row in sessions_metadata.iterrows():
        session_name = row['SessionName']
        session_id = row['SessionID']

        # Add start time if available
        dt = row.get('DateTime', '')
        display_title = sanitize.strip_race_prefix(session_name)
        if dt and isinstance(dt, str) and ' ' in dt:
            time_part = dt.split(' ')[1]
            display_title = f'{time_part} {sanitize.strip_race_prefix(session_name)}'

        session_name_lower = str(session_name).lower()
        if 'final' in session_name_lower or 'heat' in session_name_lower:
            plot_types = ['gap', 'violin']
        else:
            plot_types = ['violin']

        sessions.append({
            'title': display_title,
            'session_id': session_id,
            'plots': plot_types
        })
    return sessions


def _get_violin_plot_bytes(
    league: str, class_name: str, date: str, track: str, session_id: str | None,
    aspect: str | None, drivers_arg: str | None, sessions_arg: str | None,
    max_y: float | None, hero_names: tuple[str, ...] | None
) -> bytes:
    key = ('violin', league, class_name, date, track, session_id, aspect, drivers_arg, sessions_arg, max_y, hero_names)
    cached = plot_cache.get(key)
    if cached is not None:
        return cached

    load_names = hero_names if league == 'kartsim' else None
    df = db.load(leagues=league, classes=class_name, date=date, track=track, driver_names=load_names)
    if df.empty:
        abort(404)

    session_ids = set(df['SessionID'].unique()) if 'SessionID' in df.columns else set()

    # Filtering by driver name list
    if drivers_arg:
        driver_names = [d.strip() for d in drivers_arg.split(',')]
        df = df[df['Name'].isin(driver_names)]

    fig: plots.Figure | None
    if session_id:
        session_df = df[df['SessionID'] == session_id]
        if session_df.empty:
            abort(404)
        fig = plots.plot_split_violins(
            session_df, max_y=max_y, aspect=aspect, split_drivers=None,
            hero_names=list(hero_names) if hero_names else None
        )
    else:
        session_indices = None
        if sessions_arg:
            session_indices = [int(i) for i in sessions_arg.split(',')]

        fig = plots.plot_violins_multisession(
            df, session_indices=session_indices, max_y=max_y, aspect=aspect, split_drivers=None,
            hero_names=list(hero_names) if hero_names else None
        )

    if fig is None:
        abort(404, description='No valid lap times found for this session.')
    png_bytes = plots.fig_to_png_bytes(fig)
    plot_cache.set(key, png_bytes, session_ids)
    return png_bytes


@plots_blueprint.route('/violin_plot/<league>/<class_name>/<date>/<track>.png', defaults={'session_id': None})
@plots_blueprint.route('/violin_plot/<league>/<class_name>/<date>/<track>/<session_id>.png')
def violin_plot(league: str, class_name: str, date: str, track: str, session_id: str | None) -> Response:
    """Serve a violin plot as a PNG image. Single session if session_id is provided, else full meeting.
    Supports 'drivers' query parameter for a comma-separated list of driver names to filter by.
    Supports 'sessions' query parameter for a comma-separated list of session indices (if no session_id).
    """
    acl = auth.get_current_acl()
    if not auth.can_see_league(acl, league):
        abort(403, description='Access to this league is restricted')
    aspect = request.args.get('aspect')
    drivers_arg = request.args.get('drivers')
    sessions_arg = request.args.get('sessions')
    max_y = request.args.get('maxy_violin', type=float) or request.args.get('maxy', type=float)

    hero_names = tuple(get_hero_names())
    try:
        png_bytes = _get_violin_plot_bytes(
            league, class_name, date, track, session_id, aspect, drivers_arg, sessions_arg, max_y, hero_names
        )
        return make_plot_response(png_bytes)
    except ValueError as e:
        abort(404, description=str(e))


def _get_track_plot_bytes(
    league: str,
    class_name: str,
    track: str,
    track_conditions: str | None,
    selected_year: str | None,
    aspect: str | None,
    hero_names: tuple[str, ...] | None
) -> bytes:
    key = ('track', league, class_name, track, track_conditions, selected_year, aspect, hero_names)
    cached = plot_cache.get(key)
    if cached is not None:
        return cached

    cond_tuple = None
    if track_conditions and track_conditions != 'All Conditions':
        cond_tuple = tuple(track_conditions.split(','))

    load_names = hero_names if league == 'kartsim' else None
    df = db.load(
        leagues=league, classes=class_name, track=track,
        year=selected_year, track_conditions=cond_tuple,
        driver_names=load_names
    )
    if df.empty:
        abort(404)
    session_ids = set(df['SessionID'].unique()) if 'SessionID' in df.columns else set()
    fig = plots.plot_split_violins_by_date(df, aspect=aspect, hero_names=list(hero_names) if hero_names else None)
    if fig is None:
        abort(404)
    png_bytes = plots.fig_to_png_bytes(fig)
    plot_cache.set(key, png_bytes, session_ids)
    return png_bytes


@plots_blueprint.route('/track_plot/<league>/<class_name>/<track>.png', defaults={'track_conditions': None})
@plots_blueprint.route('/track_plot/<league>/<class_name>/<track>/<track_conditions>.png')
def track_plot(league: str, class_name: str, track: str, track_conditions: str | None) -> Response:
    """Serve a single combined violin plot for all dates at a track."""
    acl = auth.get_current_acl()
    if not auth.can_see_league(acl, league):
        abort(403, description='Access to this league is restricted')
    selected_year = request.args.get('year')
    aspect = request.args.get('aspect')

    hero_names = tuple(get_hero_names())
    try:
        png_bytes = _get_track_plot_bytes(
            league, class_name, track, track_conditions, selected_year, aspect, hero_names
        )
        return make_plot_response(png_bytes)
    except ValueError as e:
        abort(404, description=str(e))


def _get_gap_plot_bytes(
    session_params: tuple[str, ...], aspect: str | None, max_y: float | None, hero_names: tuple[str, ...] | None
) -> tuple[bytes, str]:
    key = ('gap', session_params, aspect, max_y, hero_names)
    cached = plot_cache.get(key)
    if cached is not None:
        return cached

    dfs = []
    pen_dfs = []
    meeting_dfs = []
    session_ids = set()

    for param in session_params:
        parts = param.split('/')
        if len(parts) != 5:
            abort(400, description=f'Invalid session parameter format: {param!r}')
        league, class_name, date, track, session_id = parts
        session_ids.add(session_id)

        load_names = hero_names if league == 'kartsim' else None
        df = db.load(leagues=league, classes=class_name, date=date, track=track, driver_names=load_names)
        if df.empty:
            abort(404, description=f'Meeting data not found for session request: {param!r}')
        meeting_dfs.append(df)

        session_df = df[df['SessionID'] == session_id]
        if session_df.empty:
            abort(404, description=f'Session ID {session_id!r} not found in meeting for {param!r}')

        dfs.append(session_df)

        df_pen = db.load_penalties(leagues=league, classes=class_name, date=date, track=track)
        session_pen = df_pen[df_pen['SessionID'] == session_id] if not df_pen.empty else pd.DataFrame()
        pen_dfs.append(session_pen)

    filename = _generate_plot_filename(dfs, 'gap')
    driver_colors = plots.get_driver_colors(meeting_dfs, hero_names=list(hero_names) if hero_names else None)

    try:
        fig = plots.gap_plot_multisession(
            dfs, penalties_dfs=pen_dfs, max_y=max_y, aspect=aspect,
            driver_colors=driver_colors, hero_names=list(hero_names) if hero_names else None
        )
        res = (plots.fig_to_png_bytes(fig), filename)
        plot_cache.set(key, res, session_ids)
        return res
    except ValueError as e:
        abort(404, description=str(e))


@plots_blueprint.route('/gap_plot.png')
def gap_plot() -> Response:
    """Route for gap plots, calculating filename for 'Save As' via headers."""
    session_params = request.args.getlist('session')
    aspect = request.args.get('aspect')
    max_y = request.args.get('maxy', type=float)

    hero_names = tuple(get_hero_names())
    png_bytes, filename = _get_gap_plot_bytes(tuple(session_params), aspect, max_y, hero_names)
    return make_plot_response(png_bytes, filename=filename)


def _get_combined_plot_bytes(
    session_params: tuple[str, ...], aspect: str | None, max_y: float | None,
    max_y_violin: float | None, hero_names: tuple[str, ...] | None
) -> tuple[bytes, str]:
    key = ('combined', session_params, aspect, max_y, max_y_violin, hero_names)
    cached = plot_cache.get(key)
    if cached is not None:
        return cached

    gap_dfs = []
    pen_dfs = []
    violin_dfs = []
    meeting_dfs = []
    session_ids = set()

    for param in session_params:
        parts = param.split('/')
        if len(parts) != 5:
            continue
        league, class_name, date, track, session_id = parts
        session_ids.add(session_id)

        load_names = hero_names if league == 'kartsim' else None
        df = db.load(leagues=league, classes=class_name, date=date, track=track, driver_names=load_names)
        session_df = df[df['SessionID'] == session_id]
        if session_df.empty:
            continue
        meeting_dfs.append(df)

        gap_dfs.append(session_df)
        violin_dfs.append(session_df)

        df_pen = db.load_penalties(leagues=league, classes=class_name, date=date, track=track)
        session_pen = df_pen[df_pen['SessionID'] == session_id] if not df_pen.empty else pd.DataFrame()
        pen_dfs.append(session_pen)

    if not violin_dfs and not gap_dfs:
        abort(404, description='No data found for the requested sessions or plotting failed.')

    filename = _generate_plot_filename(gap_dfs or violin_dfs, 'combined')
    driver_colors = plots.get_driver_colors(meeting_dfs, hero_names=list(hero_names) if hero_names else None)

    try:
        fig = plots.plot_violin_gap_combined(
            violin_dfs=violin_dfs,
            gap_dfs=gap_dfs,
            penalties_dfs=pen_dfs,
            max_y=max_y,
            max_y_violin=max_y_violin,
            aspect=aspect,
            driver_colors=driver_colors,
            hero_names=list(hero_names) if hero_names else None,
        )
        res = (plots.fig_to_png_bytes(fig), filename)
        plot_cache.set(key, res, session_ids)
        return res
    except ValueError as e:
        abort(404, description=str(e))


@plots_blueprint.route('/combined_plot.png')
def combined_plot() -> Response:
    """Combine violin and gap plots on one diagram for given sessions."""
    session_params = request.args.getlist('session')
    aspect = request.args.get('aspect')
    max_y = request.args.get('maxy', type=float)
    max_y_violin = request.args.get('maxy_violin', type=float)

    hero_names = tuple(get_hero_names())
    png_bytes, filename = _get_combined_plot_bytes(tuple(session_params), aspect, max_y, max_y_violin, hero_names)
    return make_plot_response(png_bytes, filename=filename)


def _get_driver_plot_bytes(
    name: str, leagues: tuple[str, ...], classes: tuple[str, ...], track: str | None,
    year: str | None, aspect: str | None, max_y: float | None, hero_names: tuple[str, ...] | None
) -> bytes:
    key = ('driver', name, leagues, classes, track, year, aspect, max_y, hero_names)
    cached = plot_cache.get(key)
    if cached is not None:
        return cached

    df = db.load(leagues=leagues, track=track, driver_names=name, consolidated_classes=classes, year=year)
    if df.empty:
        abort(404)

    session_ids = set(df['SessionID'].unique()) if 'SessionID' in df.columns else set()
    try:
        fig = plots.plot_driver_violins(
            df, name, max_y=max_y, aspect=aspect,
            title_suffix=' - Session Progression',
            hero_names=list(hero_names) if hero_names else None
        )
        png_bytes = plots.fig_to_png_bytes(fig)
        plot_cache.set(key, png_bytes, session_ids)
        return png_bytes
    except ValueError as e:
        abort(404, description=str(e))


@plots_blueprint.route('/driver_plot/<name>.png')
def driver_plot(name: str) -> Response:
    """Serve a chronological violin plot of for a specific driver.
    Supports 'leagues', 'classes', 'track' and 'year' query parameters for filtering.
    """
    acl = auth.get_current_acl()
    if not acl.get('see_drivers'):
        abort(403, description='Access to drivers is restricted')
    leagues = tuple(request.args.getlist('leagues'))
    classes = tuple(request.args.getlist('classes'))
    track = request.args.get('track')
    year = request.args.get('year')
    aspect = request.args.get('aspect')
    max_y = request.args.get('maxy', type=float)

    hero_names = tuple(get_hero_names())
    png_bytes = _get_driver_plot_bytes(name, leagues, classes, track, year, aspect, max_y, hero_names)
    return make_plot_response(png_bytes)


def _get_driver_percentile_plot_bytes(
    name: str, leagues: tuple[str, ...], classes: tuple[str, ...], track: str | None,
    year: str | None, aspect: str | None, max_y: float | None, hero_names: tuple[str, ...] | None
) -> bytes:
    key = ('driver_percentile', name, leagues, classes, track, year, aspect, max_y, hero_names)
    cached = plot_cache.get(key)
    if cached is not None:
        return cached

    hero_df = db.load(leagues=leagues, track=track, driver_names=name, consolidated_classes=classes, year=year)
    if hero_df.empty:
        abort(404)

    session_ids = tuple(hero_df['SessionID'].unique())

    full_df = db.load(leagues=leagues, track=track, consolidated_classes=classes, year=year)
    full_df = full_df[full_df['SessionID'].isin(session_ids)]

    if full_df.empty:
        abort(404)

    session_cols = ['League', 'Class', 'Date', 'SessionID']
    plot_df = plots._prepare_plot_df(full_df)
    percentiles = plot_df.groupby(session_cols)['LapTimeSeconds'].quantile(0.05)

    full_df = full_df.merge(percentiles.rename('P5'), on=session_cols)
    full_df['LapTimeSeconds'] = full_df['LapTimeSeconds'] - full_df['P5']

    try:
        fig = plots.plot_driver_violins(
            full_df, name, max_y=max_y, aspect=aspect,
            title_suffix=' - Session Progression (Gap to 5th Percentile)',
            axis_style=plots._style_gap_p5_ax,
            hero_names=list(hero_names) if hero_names else None
        )
        png_bytes = plots.fig_to_png_bytes(fig)
        plot_cache.set(key, png_bytes, set(session_ids))
        return png_bytes
    except ValueError as e:
        abort(404, description=str(e))


@plots_blueprint.route('/driver_percentile_plot/<name>.png')
def driver_percentile_plot(name: str) -> Response:
    """Serve a chronological violin plot of gaps to 5th percentile for a specific driver."""
    acl = auth.get_current_acl()
    if not acl.get('see_drivers'):
        abort(403, description='Access to drivers is restricted')
    leagues = tuple(request.args.getlist('leagues'))
    classes = tuple(request.args.getlist('classes'))
    track = request.args.get('track')
    year = request.args.get('year')
    aspect = request.args.get('aspect')
    max_y = request.args.get('maxy', type=float)

    hero_names = tuple(get_hero_names())
    png_bytes = _get_driver_percentile_plot_bytes(name, leagues, classes, track, year, aspect, max_y, hero_names)
    return make_plot_response(png_bytes)


@plots_blueprint.route('/api/training_data/<league>/<class_name>')
def training_data_api(league: str, class_name: str) -> Response | tuple[Response, int]:
    """Return training time data as JSON for hero drivers."""
    acl = auth.get_current_acl()
    if not auth.can_see_league(acl, league):
        return jsonify({'error': 'Access to this league is restricted'}), 403
    selected_year = request.args.get('year')
    hero_names = tuple(get_hero_names())
    if not hero_names:
        return jsonify({'error': 'No hero drivers selected'}), 400

    df = db.load(leagues=league, classes=class_name, year=selected_year, driver_names=hero_names)
    if df.empty:
        return jsonify({'daily': [], 'weekly': []})

    df['Date'] = pd.to_datetime(df['Date'])

    def get_aggregated_data_by_driver(dataframe: pd.DataFrame, group_col: str) -> list[dict[str, object]]:
        agg = dataframe.groupby([group_col, 'Name'])['LapTimeSeconds'].sum().reset_index()
        agg = agg.sort_values(group_col)
        periods = sorted(agg[group_col].unique())

        period_data = []
        for period in periods:
            period_df = agg[agg[group_col] == period]
            drivers_dict = {}
            total = 0.0
            for _, row in period_df.iterrows():
                name = str(row['Name'])
                seconds = float(row['LapTimeSeconds'])
                drivers_dict[name] = seconds
                total += seconds

            period_str = period.strftime('%Y-%m-%d') if hasattr(period, 'strftime') else str(period)

            period_data.append({
                group_col.lower(): period_str,
                'total': total,
                'drivers': drivers_dict
            })
        return period_data

    daily_data = get_aggregated_data_by_driver(df, 'Date')

    df['Week'] = df['Date'].dt.to_period('W').dt.start_time
    weekly_data = get_aggregated_data_by_driver(df, 'Week')

    return jsonify({
        'daily': daily_data,
        'weekly': weekly_data,
        'hero_names': hero_names
    })
