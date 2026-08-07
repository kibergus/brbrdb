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

"""Gallery routes and helpers.

All routes under /gallery, /photo_file, /video_file and /photo_crop live here.
The module exposes ``gallery_blueprint`` which is registered in app.py.
"""

from typing import Any
import io
import os

from flask import (
    Blueprint, Response, abort, render_template, request, send_file, url_for
)
from PIL import Image as PILImage

import logging

import aliases
import auth
import race_db
from database import db, config
from race_tools import sanitize

logger = logging.getLogger(__name__)
gallery_blueprint = Blueprint('gallery', __name__)


# ---------------------------------------------------------------------------
# Helper: media-directory safety
# ---------------------------------------------------------------------------

def _require_media_dir() -> str:
    """Return the configured media_dir or abort with 500."""
    media_dir = config.get('media_dir')
    if not media_dir:
        abort(500, description="'media_dir' must be specified in config.json")
    return media_dir


# ---------------------------------------------------------------------------
# Photo / video file-serving routes
# ---------------------------------------------------------------------------

@gallery_blueprint.route('/video_file/<league>/<class_name>/<date>/<track>/<path:filename>')
def serve_video(league: str, class_name: str, date: str, track: str, filename: str) -> Response:
    try:
        race_db.check_for_dir_traversal([league, class_name, date, track, filename])
    except ValueError:
        abort(400, description="Invalid path")

    if not auth.get_current_acl().get('see_videos'):
        abort(403, description='Access to video files is restricted')

    sessions = db.find_sessions(leagues=league, classes=class_name, date=date, track=track)
    if not sessions:
        abort(404)

    meeting_dir = sessions[0].meeting_dir
    try:
        db.check_media_file_permissions(filename, meeting_dir)
    except ValueError:
        abort(404)

    media_dir = _require_media_dir()
    meeting_folder = sessions[0].meeting_folder
    video_dir = os.path.join(media_dir, league, meeting_folder, 'video')
    real_media_dir = os.path.realpath(media_dir)

    if os.path.exists(video_dir):
        media_path = os.path.join(video_dir, filename)
        if os.path.exists(media_path):
            real_media_path = os.path.realpath(media_path)
            if os.path.commonpath([real_media_dir, real_media_path]) == real_media_dir:
                return send_file(media_path)

    abort(404)


def find_photo_file(media_dir: str, league: str, meeting_folder: str, filename: str) -> str | None:
    """Walk the photo directory tree and return the full path for *filename*."""
    try:
        race_db.check_for_dir_traversal([league, meeting_folder, filename])
    except ValueError:
        return None
    photo_dir = os.path.join(media_dir, league, meeting_folder, 'photo')
    if not os.path.exists(photo_dir):
        return None
    for root, _, files in os.walk(photo_dir):
        if filename in files:
            return os.path.join(root, filename)
    return None


@gallery_blueprint.route('/photo_file/<league>/<meeting_folder>/<path:filename>')
def serve_photo(league: str, meeting_folder: str, filename: str) -> Response:
    try:
        race_db.check_for_dir_traversal([league, meeting_folder, filename])
    except ValueError:
        abort(400, description="Invalid path")

    acl = auth.get_current_acl()
    if not acl.get('see_photos') and not acl.get('see_videos'):
        abort(403, description='Access to photos is restricted')

    media_dir = _require_media_dir()
    real_media_dir = os.path.realpath(media_dir)
    photo_path = find_photo_file(real_media_dir, league, meeting_folder, filename)
    if photo_path:
        real_photo_path = os.path.realpath(photo_path)
        if os.path.commonpath([real_media_dir, real_photo_path]) == real_media_dir:
            return send_file(photo_path)

    abort(404)


@gallery_blueprint.route('/photo_crop')
def photo_crop() -> Response:
    acl = auth.get_current_acl()
    if not acl.get('see_photos') and not acl.get('see_videos'):
        abort(403, description='Access to photos is restricted')

    league = request.args.get('league')
    meeting_folder = request.args.get('meeting_folder')
    filename = request.args.get('filename')

    try:
        norm_x = float(request.args.get('x', 0))
        norm_y = float(request.args.get('y', 0))
        norm_w = float(request.args.get('w', 1))
        norm_h = float(request.args.get('h', 1))
    except (ValueError, TypeError):
        abort(400, description="Invalid crop coordinates")

    if not league or not meeting_folder or not filename:
        abort(400, description="Missing required parameters")

    try:
        race_db.check_for_dir_traversal([league, meeting_folder, filename])
    except ValueError:
        abort(400, description="Invalid path")

    media_dir = _require_media_dir()
    real_media_dir = os.path.realpath(media_dir)
    photo_path = find_photo_file(real_media_dir, league, meeting_folder, filename)
    if not photo_path:
        abort(404)

    real_photo_path = os.path.realpath(photo_path)
    if os.path.commonpath([real_media_dir, real_photo_path]) != real_media_dir:
        abort(403)

    try:
        img = PILImage.open(photo_path)
        img_w, img_h = img.size

        crop_x = max(0, min(img_w - 1, int(norm_x * img_w)))
        crop_y = max(0, min(img_h - 1, int(norm_y * img_h)))
        crop_w = max(1, min(img_w - crop_x, int(norm_w * img_w)))
        crop_h = max(1, min(img_h - crop_y, int(norm_h * img_h)))

        cropped_img = img.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))
        cropped_img = _apply_watermark(cropped_img)

        out_io = io.BytesIO()
        cropped_img.convert('RGB').save(out_io, 'JPEG', quality=95)
        out_io.seek(0)

        dot_idx = filename.rfind('.')
        base_name = filename[:dot_idx] if dot_idx != -1 else filename
        ext = filename[dot_idx:] if dot_idx != -1 else '.jpg'
        download_name = f"{base_name}_cropped{ext}"

        return send_file(out_io, mimetype='image/jpeg', as_attachment=True,
                         download_name=download_name)
    except Exception as e:
        abort(500, description=f"Image processing error: {e}")


def _apply_watermark(img: Any) -> Any:
    """Overlay the BrBrKitten stamp on the bottom-right corner of *img*."""
    stamp_path = os.path.join(os.path.dirname(__file__), 'static', 'brbrkitten_bw.png')
    if not os.path.exists(stamp_path):
        return img

    stamp = PILImage.open(stamp_path)
    stamp_w = img.width // 6
    if stamp_w <= 0:
        return img

    stamp_aspect = stamp.width / stamp.height
    stamp_h = int(stamp_w / stamp_aspect)

    # Dynamically retrieve Lanczos/Antialias filter to satisfy mypy
    resample_obj = getattr(PILImage, 'Resampling', PILImage)
    resample_filter = getattr(resample_obj, 'LANCZOS', 1)
    stamp_resized = stamp.resize((stamp_w, stamp_h), resample_filter)

    margin = int(img.width * 0.02)
    paste_x = img.width - stamp_w - margin
    paste_y = img.height - stamp_h - margin

    mask = stamp_resized if stamp_resized.mode in ('RGBA', 'LA') else None
    img.paste(stamp_resized, (paste_x, paste_y), mask)
    return img


# ---------------------------------------------------------------------------
# Gallery page routes
# ---------------------------------------------------------------------------

@gallery_blueprint.route('/gallery')
def gallery() -> str:
    """Dispatch to the appropriate gallery sub-page based on query parameters."""
    if not auth.get_current_acl().get('see_gallery'):
        abort(403, description='Access to gallery is restricted')

    league = request.args.get('league')
    meeting_date = request.args.get('meeting')

    if not league:
        return _gallery_root()
    if not meeting_date:
        return _gallery_league(league)
    return _gallery_meeting(league, meeting_date)


# ---------------------------------------------------------------------------
# Gallery sub-pages
# ---------------------------------------------------------------------------

def _gallery_root() -> str:
    """Render the top-level gallery page listing all leagues that have media."""
    with db.conn.cursor() as cur:
        cur.execute('''
            SELECT
                m.league,
                COUNT(DISTINCT m.date) as meetings_count,
                COUNT(DISTINCT p.id) as photos_count,
                COUNT(DISTINCT v.id) as videos_count
            FROM meetings m
            LEFT JOIN photos p ON p.meeting_id = m.id
            LEFT JOIN videos v ON v.meeting_id = m.id
            GROUP BY m.league
            HAVING COUNT(DISTINCT p.id) > 0 OR COUNT(DISTINCT v.id) > 0
        ''')
        rows = cur.fetchall()

    # Filter out kartsim if not allowed
    if not auth.get_current_acl().get('kartsim_data'):
        rows = [r for r in rows if r[0] != 'kartsim']

    leagues_with_media = sorted(
        [
            {
                'id': league_id,
                'name': aliases.get_league_name(league_id),
                'color': aliases.get_league_color(league_id),
                'meetings_count': meetings_count,
                'photos_count': photos_count,
                'videos_count': videos_count,
            }
            for league_id, meetings_count, photos_count, videos_count in rows
        ],
        key=lambda x: x['name'],
    )

    return render_template('gallery_root.html', leagues=leagues_with_media)


def _gallery_league(league: str) -> str:
    """Render the league gallery page listing all meetings that have media."""
    if league not in db.list_leagues():
        abort(404, description="League not found")

    if league == 'kartsim' and not auth.get_current_acl().get('kartsim_data'):
        abort(403, description="Access to KartSim is restricted")

    with db.conn.cursor() as cur:
        cur.execute('''
            SELECT
                m.date,
                MAX(m.meeting_name) as meeting_name,
                MAX(m.track_name) as track_name,
                COUNT(DISTINCT p.id) as photos_count,
                COUNT(DISTINCT v.id) as videos_count
            FROM meetings m
            LEFT JOIN photos p ON p.meeting_id = m.id
            LEFT JOIN videos v ON v.meeting_id = m.id
            WHERE m.league = %s
            GROUP BY m.date
            HAVING COUNT(DISTINCT p.id) > 0 OR COUNT(DISTINCT v.id) > 0
            ORDER BY m.date DESC
        ''', (league,))
        rows = cur.fetchall()

    meetings_with_media = [
        {
            'name': name,
            'date': str(date),
            'track': track,
            'photos_count': photos_count,
            'videos_count': videos_count,
        }
        for date, name, track, photos_count, videos_count in rows
    ]

    return render_template(
        'gallery_league.html',
        league=league,
        league_name=aliases.get_league_name(league),
        meetings=meetings_with_media,
    )


def _gallery_meeting(league: str, meeting_date: str) -> str:
    """Render the per-meeting gallery page with photos and videos."""
    sessions = db.find_sessions(leagues=league, date=meeting_date)

    available_classes = sorted(list(set(c for s in sessions for c in s.class_name)))
    selected_class = request.args.get('class') or request.args.get('class_name')
    selected_album = request.args.get('album')

    photos_rows, videos_rows = _fetch_media_rows(league, meeting_date)
    video_markers_map = _build_video_markers_map(sessions, league, meeting_date)
    photo_subfolder_map = _build_photo_subfolder_map(sessions, league)
    folder_to_session = _build_folder_to_session(sessions)
    session_by_id = {s.session_id: s for s in sessions}

    photos, photo_albums = _process_photos(
        photos_rows, session_by_id, photo_subfolder_map, folder_to_session,
        league, selected_class, selected_album,
    )
    videos, video_albums = _process_videos(
        videos_rows, session_by_id, folder_to_session, sessions,
        league, meeting_date, selected_class, selected_album,
        video_markers_map,
    )

    available_albums = _sort_albums_chronologically(
        photo_albums | video_albums,
        sessions,
    )

    first_session = sessions[0] if sessions else None
    track_name = first_session.track_name if first_session else 'unknown'

    meeting_class = selected_class or (available_classes[0] if available_classes else 'cadet')
    if league == 'kartsim':
        meeting_url = url_for(
            'location.telemetry_view',
            league=league,
            class_name=meeting_class,
            date=meeting_date,
            track=track_name,
        )
    else:
        meeting_url = url_for(
            'meeting_view',
            league=league,
            class_name=meeting_class,
            date=meeting_date,
            track=track_name,
        )

    session_url = None
    session_name = None
    if selected_album:
        matched_session = folder_to_session.get(selected_album.lower())

        if not matched_session:
            for s in sessions:
                clean_name = _clean_session_name(s.session_name)
                stripped_name = sanitize.strip_race_prefix(s.session_name)
                if (
                    s.session_name.lower() == selected_album.lower()
                    or clean_name.lower() == selected_album.lower()
                    or stripped_name.lower() == selected_album.lower()
                ):
                    matched_session = s
                    break

        if not matched_session:
            for p in photos:
                if p.get('sessions'):
                    sess_info = p['sessions'][0]
                    matched_session = session_by_id.get(sess_info['session_id'])
                    if matched_session:
                        break
            if not matched_session:
                for v in videos:
                    if v.get('sessions'):
                        sess_info = v['sessions'][0]
                        matched_session = session_by_id.get(sess_info['session_id'])
                        if matched_session:
                            break

        if matched_session:
            sess_class = selected_class or (
                matched_session.class_name[0] if matched_session.class_name else meeting_class
            )
            session_url = url_for(
                'session_view',
                league=league,
                class_name=sess_class,
                date=meeting_date,
                track=track_name,
                session_id=matched_session.session_id,
            )
            session_name = sanitize.strip_race_prefix(matched_session.session_name)

    return render_template(
        'gallery.html',
        league=league,
        meeting_date=meeting_date,
        track=track_name,
        available_classes=available_classes,
        available_albums=available_albums,
        selected_class=selected_class,
        selected_album=selected_album,
        photos=photos,
        videos=videos,
        meeting_url=meeting_url,
        session_url=session_url,
        session_name=session_name,
    )


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------


def _clean_session_name(session_name: str) -> str:
    """Normalise a session name for album-matching purposes."""
    return ' '.join(sanitize.strip_race_prefix(session_name).replace('/', ' ').split())


# ---------------------------------------------------------------------------
# Database fetch helpers
# ---------------------------------------------------------------------------

def _fetch_media_rows(league: str, meeting_date: str) -> tuple[list, list]:
    """Return (photos_rows, videos_rows) for the given meeting."""
    with db.conn.cursor() as cur:
        cur.execute('''
            SELECT p.filename, p.taken_at, p.meeting_id, m.meeting_dir, p.album, p.session_id
            FROM photos p
            JOIN meetings m ON p.meeting_id = m.id
            WHERE m.league = %s AND m.date = %s
            ORDER BY p.taken_at ASC
        ''', (league, meeting_date))
        photos_rows = cur.fetchall()

        cur.execute('''
            SELECT v.filename, v.start_time, v.end_time, v.meeting_id, m.meeting_dir, v.session_id
            FROM videos v
            JOIN meetings m ON v.meeting_id = m.id
            WHERE m.league = %s AND m.date = %s
            ORDER BY v.start_time ASC
        ''', (league, meeting_date))
        videos_rows = cur.fetchall()

    return photos_rows, videos_rows


# ---------------------------------------------------------------------------
# Lookup-map builders
# ---------------------------------------------------------------------------

def _build_video_markers_map(
    sessions: list, league: str, meeting_date: str
) -> dict[str, list[dict[str, Any]]]:
    """Build a {basename -> [marker, ...]} map for lap markers on videos."""
    video_markers_map: dict[str, list[dict[str, Any]]] = {}
    for s in sessions:
        s_class = s.class_name[0] if s.class_name else 'cadet'
        df = db.load(leagues=league, classes=s_class, date=meeting_date, track=s.track_name)
        if df.empty:
            continue
        session_data = df[df['SessionID'] == s.session_id]
        if session_data.empty:
            continue

        for ov in db.get_overlapping_videos(s, session_data):
            vfilename = ov['filename']
            if vfilename not in video_markers_map:
                video_markers_map[vfilename] = []
            for marker in ov['markers']:
                video_markers_map[vfilename].append({
                    'driver': marker['driver'],
                    'lap': int(marker['lap']),
                    'video_time': marker['video_time'],
                    'lap_time': float(marker['lap_time']),
                    'session_id': s.session_id,
                    'session_name': sanitize.strip_race_prefix(s.session_name),
                })
    return video_markers_map


def _build_photo_subfolder_map(sessions: list, league: str) -> dict[str, str]:
    """Return {filename_lower -> subfolder_name} from the on-disk photo directory."""
    media_dir = config.get('media_dir')
    if not media_dir or not sessions:
        return {}

    meeting_folder = sessions[0].meeting_folder
    photo_dir = os.path.join(media_dir, league, meeting_folder, 'photo')
    if not os.path.exists(photo_dir):
        return {}

    photo_subfolder_map: dict[str, str] = {}
    for entry in os.scandir(photo_dir):
        if entry.is_dir():
            for file_entry in os.scandir(entry.path):
                if file_entry.is_file() and not file_entry.name.startswith('.'):
                    photo_subfolder_map[file_entry.name.lower()] = entry.name
    return photo_subfolder_map


def _build_folder_to_session(sessions: list) -> dict[str, Any]:
    """Return {clean_session_name_lower -> session} for album-to-session lookup."""
    return {_clean_session_name(s.session_name).lower(): s for s in sessions}


# ---------------------------------------------------------------------------
# Media-item builders
# ---------------------------------------------------------------------------


def _process_photos(
    photos_rows: list,
    session_by_id: dict,
    photo_subfolder_map: dict[str, str],
    folder_to_session: dict,
    league: str,
    selected_class: str | None,
    selected_album: str | None,
) -> tuple[list[dict[str, Any]], set[str]]:
    """Build the list of photo dicts for the template, applying filters and deduplication.

    Returns (photos, available_albums_set) where available_albums_set is collected
    before the album filter so the UI can show all albums for switching.
    """
    seen: dict[str, dict[str, Any]] = {}
    available_albums: set[str] = set()

    for row in photos_rows:
        filename, taken_at, meeting_id, meeting_dir, db_album, db_session_id = row
        meeting_folder = os.path.basename(meeting_dir)

        matched_sess = session_by_id.get(db_session_id) if db_session_id else None

        album = db_album
        if not album:
            album = photo_subfolder_map.get(filename.lower())
        if not album:
            album = (_clean_session_name(matched_sess.session_name)
                     if matched_sess else 'saturday')

        if not matched_sess:
            matched_sess = folder_to_session.get(album.lower())

        # Filter by class
        if selected_class and matched_sess:
            if selected_class not in matched_sess.class_name:
                continue

        available_albums.add(album)

        # Filter by album
        if selected_album and album.lower() != selected_album.lower():
            continue

        base_fn = os.path.basename(filename).lower()
        has_prefix = '/' in filename or '\\' in filename

        base_name, _ = os.path.splitext(filename)
        photo_item = {
            'filename': filename,
            'taken_at': taken_at.isoformat() if taken_at else None,
            'meeting_id': meeting_id,
            'meeting_folder': meeting_folder,
            'thumb_url': url_for('gallery.serve_photo', league=league,
                                 meeting_folder=meeting_folder,
                                 filename=base_name + '.thumb.jpg'),
            'small_url': url_for('gallery.serve_photo', league=league,
                                 meeting_folder=meeting_folder,
                                 filename=base_name + '.small.jpg'),
            'full_url': url_for('gallery.serve_photo', league=league,
                                meeting_folder=meeting_folder, filename=filename),
            'album': album,
            'sessions': _session_links([matched_sess] if matched_sess else []),
        }

        # Prefer the entry that has a folder prefix (more specific path)
        if base_fn in seen:
            existing_has_prefix = '/' in seen[base_fn]['filename'] or '\\' in seen[base_fn]['filename']
            if has_prefix and not existing_has_prefix:
                seen[base_fn] = photo_item
        else:
            seen[base_fn] = photo_item

    photos = list(seen.values())
    photos.sort(key=lambda x: x['taken_at'] or '')
    return photos, available_albums


def _process_videos(
    videos_rows: list,
    session_by_id: dict,
    folder_to_session: dict,
    sessions: list,
    league: str,
    meeting_date: str,
    selected_class: str | None,
    selected_album: str | None,
    video_markers_map: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Build the list of video dicts for the template, applying filters and deduplication.

    Returns (videos, available_albums_set).
    """
    first_session = sessions[0] if sessions else None
    default_track = first_session.track_name if first_session else 'unknown'

    seen: dict[str, dict[str, Any]] = {}
    available_albums: set[str] = set()

    for filename, start_time, end_time, meeting_id, meeting_dir, db_session_id in videos_rows:
        meeting_folder = os.path.basename(meeting_dir)

        video_sessions = ([session_by_id[db_session_id]]
                          if db_session_id and db_session_id in session_by_id else [])

        album = _determine_video_album(filename, video_sessions)
        matched_sess = video_sessions[0] if video_sessions else folder_to_session.get(album.lower())

        # Filter by class (session-less albums are kept)
        if selected_class and matched_sess:
            if selected_class not in matched_sess.class_name:
                continue

        available_albums.add(album)

        # Filter by album
        if selected_album and album.lower() != selected_album.lower():
            continue

        base_fn = os.path.basename(filename).lower()
        has_prefix = '/' in filename or '\\' in filename

        primary_session = video_sessions[0] if video_sessions else first_session
        v_class = (primary_session.class_name[0]
                   if primary_session and primary_session.class_name else 'cadet')
        v_track = primary_session.track_name if primary_session else default_track

        dir_name = os.path.dirname(filename)
        base_name, _ = os.path.splitext(os.path.basename(filename))
        thumb_filename = (os.path.join(dir_name, base_name + '.small.jpg')
                          if dir_name else base_name + '.small.jpg')

        v_markers = sorted(
            video_markers_map.get(os.path.basename(filename), []),
            key=lambda m: m['video_time'],
        )

        video_item = {
            'filename': filename,
            'start_time': start_time.isoformat() if start_time else None,
            'end_time': end_time.isoformat() if end_time else None,
            'meeting_id': meeting_id,
            'meeting_folder': meeting_folder,
            'thumb_url': url_for('gallery.serve_video', league=league, class_name=v_class,
                                 date=meeting_date, track=v_track, filename=thumb_filename),
            'url': url_for('gallery.serve_video', league=league, class_name=v_class,
                           date=meeting_date, track=v_track, filename=filename),
            'markers': v_markers,
            'sessions': _session_links(video_sessions),
        }

        if base_fn in seen:
            existing_has_prefix = '/' in seen[base_fn]['filename'] or '\\' in seen[base_fn]['filename']
            if has_prefix and not existing_has_prefix:
                seen[base_fn] = video_item
        else:
            seen[base_fn] = video_item

    videos = list(seen.values())
    videos.sort(key=lambda x: x['start_time'] or '')
    return videos, available_albums


def _determine_video_album(filename: str, video_sessions: list) -> str:
    """Resolve the album name for a video based on its path or associated session."""
    if '/' in filename or '\\' in filename:
        return filename.replace('\\', '/').split('/')[0]
    if video_sessions:
        return _clean_session_name(video_sessions[0].session_name)
    return 'saturday'


def _session_links(sessions: list) -> list[dict[str, Any]]:
    """Serialize a list of session objects to lightweight dicts for the template."""
    return [
        {
            'session_id': s.session_id,
            'session_name': sanitize.strip_race_prefix(s.session_name),
            'class_name': s.class_name[0] if s.class_name else 'cadet',
        }
        for s in sessions
    ]


def _sort_albums_chronologically(albums: set[str], sessions: list) -> list[str]:
    """Sort a set of album names chronologically based on their order in the sessions list."""
    session_order: dict[str, int] = {}
    for s in sessions:
        if s.session_name:
            clean_name = _clean_session_name(s.session_name).lower()
            if clean_name not in session_order:
                session_order[clean_name] = len(session_order)

    def sort_key(alb: str) -> tuple[int, str]:
        alb_lower = alb.lower()
        return session_order.get(alb_lower, 999999), alb.lower()

    return sorted(albums, key=sort_key)


def has_meeting_gallery(league: str, meeting_date: str) -> bool:
    """Return True if photos or videos exist in DB for the given league and meeting date."""
    if not auth.get_current_acl().get('see_gallery'):
        return False
    if league == 'kartsim' and not auth.get_current_acl().get('kartsim_data'):
        return False
    if not db or not db.conn:
        return False
    with db.conn.cursor() as cur:
        cur.execute('''
            SELECT 1
            FROM meetings m
            LEFT JOIN photos p ON p.meeting_id = m.id
            LEFT JOIN videos v ON v.meeting_id = m.id
            WHERE m.league = %s AND m.date = %s
            GROUP BY m.id
            HAVING COUNT(DISTINCT p.id) > 0 OR COUNT(DISTINCT v.id) > 0
            LIMIT 1
        ''', (league, meeting_date))
        return cur.fetchone() is not None


def has_league_gallery(league: str) -> bool:
    """Return True if photos or videos exist in DB for the given league."""
    if not auth.get_current_acl().get('see_gallery'):
        return False
    if league == 'kartsim' and not auth.get_current_acl().get('kartsim_data'):
        return False
    if not db or not db.conn:
        return False
    with db.conn.cursor() as cur:
        cur.execute('''
            SELECT 1
            FROM meetings m
            LEFT JOIN photos p ON p.meeting_id = m.id
            LEFT JOIN videos v ON v.meeting_id = m.id
            WHERE m.league = %s
            GROUP BY m.id
            HAVING COUNT(DISTINCT p.id) > 0 OR COUNT(DISTINCT v.id) > 0
            LIMIT 1
        ''', (league,))
        return cur.fetchone() is not None
