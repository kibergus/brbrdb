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

import re
from dataclasses import dataclass


@dataclass
class ClassInfo:
    name: str
    class_type: str | None = None


def get_consolidated_class(raw_class_name: str) -> str:
    """Returns a consolidated class name, e.g. 'Cadet Light' -> 'Cadet'."""
    info = get_class_info(raw_class_name)
    return re.sub(
        r'\s+(super\s+light(?:weight)?|light(?:weight)?|heavyweight)$',
        '', info.name, flags=re.IGNORECASE
    )


_LEAGUE_NAMES = {
    'club100': 'Club 100',
    'club100_north': 'Club 100 North',
    'club100_south': 'Club 100 South',
    'kartsim': 'KartSim',
    'fat': 'FAT',
    'fat_pro': 'FAT Pro',
    'fat_regional': 'FAT Regional',
    'fat_world_finals': 'FAT World Finals',
    'fat_us_ca': 'FAT US CA',
    'fat_us_mw': 'FAT US MW',
    'fekc': 'Forest Edge',
    'drs': 'DRS',
    'skrc': 'Shenington KC',
    'rkc': 'Rissington KC',
}

_LEAGUE_COLORS = {
    'club100': '#E31E24',  # Brand Red
    'club100_north': '#E31E24',
    'club100_south': '#E31E24',
    'fat': '#1D4296',  # Brand Blue
    'fat_pro': '#1D4296',
    'fat_regional': '#1D4296',
    'fat_world_finals': '#1D4296',
    'fat_us_ca': '#1D4296',
    'fat_us_mw': '#1D4296',
    'kartsim': '#3498db',  # blue
    'fekc': '#9b59b6',  # purple
    'drs': '#55b0cb',   # DRS Cyan
}

_CLASS_NAMES = {
    # FKL and Club 100 classes.
    'bambino': ClassInfo('Bambino', 'bambino'),
    'cadet': ClassInfo('Cadet', 'cadet'),
    'cadet_lw': ClassInfo('Cadet light', 'cadet'),
    'cadet_slw': ClassInfo('Cadet super light', 'cadet'),
    'junior': ClassInfo('Junior', 'junior'),
    'junior_lw': ClassInfo('Junior light', 'junior'),
    'junior_slw': ClassInfo('Junior super light', 'junior'),
    'senior': ClassInfo('Senior', None),
    'sp40': ClassInfo('Sprint 40', None),
    'sp40_lw': ClassInfo('Sprint 40 Lightweight', None),
    'sp60': ClassInfo('Sprint 60', None),
    'sp60_lw': ClassInfo('Sprint 60 Lightweight', None),
    'middleweight': ClassInfo('Middleweight', None),
    'heavyweight': ClassInfo('Heavyweight', None),
    'experience_heavyweight': ClassInfo('Experience Heavyweight', None),
    'experience': ClassInfo('Experience', None),
    'experience_lw': ClassInfo('Experience Lightweight', None),
    'experience_junior_lw': ClassInfo('Experience Junior Lightweight', 'junior'),
    'experience_junior_slw': ClassInfo('Experience Junior Super Lightweight', 'junior'),
    'sprint': ClassInfo('Sprint', None),
    'sprint_lw': ClassInfo('Sprint Lightweight', None),
    'sprint_middleweight': ClassInfo('Sprint Middleweight', None),
    'testing': ClassInfo('Testing', None),
    # KartSim and generic classes.
    'formula_210': ClassInfo('Formula 210', None),
    '210_national': ClassInfo('210 National', None),
    'formula_libre': ClassInfo('Formula Libre', None),
    'fp4': ClassInfo('FP4', None),
    'honda_gx200': ClassInfo('Honda GX200', 'cadet'),
    'iame_bambino': ClassInfo('IAME Bambino', 'bambino'),
    'iame_waterswift_restricted_cadet_uk': ClassInfo('IAME Waterswift Restricted Cadet UK', 'cadet'),
    'iame_waterswift_cadet_uk': ClassInfo('IAME Waterswift Cadet UK', 'cadet'),
    'kz': ClassInfo('KZ', None),
    'mighte_bambino': ClassInfo('MightE Bambino', 'bambino'),
    'mighte_cadet': ClassInfo('MightE Cadet', 'cadet'),
    'rotax_e10_mini': ClassInfo('Rotax E10 Mini', 'cadet'),
    'rotax_inter': ClassInfo('Rotax Inter', None),
    'rotax_max': ClassInfo('Rotax Max', None),
    'rotax_mini_max': ClassInfo('Rotax Mini Max', 'cadet'),
    'rotax_e10_bambino': ClassInfo('Rotax E10 Bambino', 'bambino'),
    'rotax_e20': ClassInfo('Rotax E20', None),
    'rotax_junior_max': ClassInfo('Rotax Junior Max', 'junior'),
    'rotax_senior_max': ClassInfo('Rotax Senior Max', None),
    'superkart': ClassInfo('Superkart', None),
    'tillotson': ClassInfo('Tillotson T4', None),
    'tillotson_t4': ClassInfo('Tillotson T4', None),
    'tillotson_t4_junior': ClassInfo('Tillotson T4 Junior', 'junior'),
    'x30_junior': ClassInfo('X30 Junior', 'junior'),
    'x30_senior': ClassInfo('X30 Senior', None),
    # DRS classes.
    'drs40': ClassInfo('DRS40', None),
    'drs50': ClassInfo('DRS50', None),
    'drs100': ClassInfo('DRS100', None),
    'drs125': ClassInfo('DRS125', None),
}


def get_league_name(league_id: str) -> str:
    return _LEAGUE_NAMES.get(league_id, league_id.replace('_', ' ').title())


def get_league_color(league_id: str) -> str:
    return _LEAGUE_COLORS.get(league_id, '#94a3b8')  # default to text-secondary


def get_league_group(league_id: str) -> str:
    if league_id == 'kartsim':
        return 'kartsim'
    if league_id.startswith('club100'):
        return 'club100'
    if league_id in ('fat_us_ca', 'fat_us_mw'):
        return 'fat_us'
    if league_id.startswith('fat'):
        return 'fat'
    return 'other'


def group_leagues(leagues: list[str]) -> list[tuple[str, list[str]]]:
    """Group leagues into club100, fat, and other buckets, sorted by name."""
    groups: dict[str, list[str]] = {
        'kartsim': [],
        'club100': [],
        'fat': [],
        'fat_us': [],
        'other': []
    }
    for league_id in leagues:
        groups[get_league_group(league_id)].append(league_id)

    # Sort each group by display name
    for g in groups:
        groups[g].sort(key=get_league_name)

    # Convert to list of (group_name, list_of_leagues) for stable iteration
    # Ordered as: kartsim, club100, fat, other
    grouped_list = [
        ('kartsim', groups['kartsim']),
        ('club100', groups['club100']),
        ('fat', groups['fat']),
        ('fat_us', groups['fat_us']),
        ('other', groups['other'])
    ]
    # Filter empty groups
    return [g for g in grouped_list if g[1]]


def get_class_name(class_id: str) -> str:
    info = _CLASS_NAMES.get(class_id)
    return info.name if info else class_id


def get_class_info(class_id: str) -> ClassInfo:
    info = _CLASS_NAMES.get(class_id)
    return info if info else ClassInfo(name=class_id, class_type=None)


VIDEO_SOURCE_NAMES = {
    'r5': 'R5',
}


def get_video_source_name(source_id: str) -> str | None:
    return VIDEO_SOURCE_NAMES.get(source_id.lower())


# Maps raw car/class names as they may appear in uploaded CSVs to the canonical
# class ID used for directory layout and DB lookups.  Keys are matched
# case-insensitively after stripping whitespace.
CAR_NAME_ALIASES: dict[str, str] = {
    'ksp_iwc_re_uk': 'iame_waterswift_restricted_cadet_uk',
    'ksp_iwc_uk': 'iame_waterswift_cadet_uk',
    'iame waterswift (inter)': 'iame_waterswift_cadet_uk',
    'iame water swift (restricted)': 'iame_waterswift_restricted_cadet_uk',
    'iame waterswift (restricted)': 'iame_waterswift_restricted_cadet_uk',
    'tillotson': 'tillotson_t4',
    'tillotson t4': 'tillotson_t4',
    't4': 'tillotson_t4',
    't4j': 'tillotson_t4_junior',
    'tillotson t4 junior': 'tillotson_t4_junior',
    'formula 210': 'formula_210',
    'formula210': 'formula_210',
    '210 national': '210_national',
    '210national': '210_national',
    'formula libre': 'formula_libre',
    'x30 junior': 'x30_junior',
    '25_ksp_e10_mini': 'rotax_e10_mini',
    '25_ksp_mini_max': 'rotax_mini_max',
    '25_ksp_e10_bambini': 'rotax_e10_bambino',
    '25_ksp_e20': 'rotax_e20',
    '25_ksp_junior_max': 'rotax_junior_max',
    '25_ksp_senior_max': 'rotax_senior_max',
    '25_ksp_iame_bambino': 'iame_bambino',
    'ksp_gx200': 'honda_gx200',
    'ksp_iame_junior_x30': 'x30_junior',
    'ksp_iame_senior_x30': 'x30_senior',
}


def resolve_car_name(raw_name: str) -> str:
    """Returns the canonical class ID for *raw_name*, falling back to the
    original value (lowercased and stripped) if no alias is defined."""
    key = raw_name.strip().lower()
    return CAR_NAME_ALIASES.get(key, raw_name.strip())


# Maps raw track/venue names as they may appear in imports/uploads to the
# canonical track name used in directory layout and DB lookups.
TRACK_NAME_ALIASES: dict[str, str] = {
    'rissington': 'Rissington',
    'south wales karting centre': 'Llandow',
    'larkhall pro': 'Larkhall',
    'kimbolton circuit 1': 'Kimbolton',
}


def resolve_track_name(raw_name: str | None) -> str:
    """Returns the canonical track name for *raw_name*, falling back to the
    original value (stripped) if no alias is defined."""
    if not raw_name:
        return ''

    # 1. Strip whitespace
    name = raw_name.strip()
    # 2. Remove year at the end (e.g. " 2026")
    name = re.sub(r'\s+20\d{2}$', '', name)
    # 3. Drop " Kart Club" and " Karting" at the end
    name = re.sub(r'\s+(?:Kart\s+Club|Karting)$', '', name, flags=re.IGNORECASE)

    # 4. Check aliases
    key = name.lower()
    return TRACK_NAME_ALIASES.get(key, name)
