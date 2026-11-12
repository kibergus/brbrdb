#!/usr/bin/env python3
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

"""CLI utility to recompute stale or forced *.derived.brtc files across the archive."""

from __future__ import annotations

import argparse
import logging
import os
import sys

try:
    from database import config
    import telemetry_brtc
    import telemetry_store
    from utils.compaction_daemon import iter_telemetry_dirs
except ImportError:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from database import config
    import telemetry_brtc
    import telemetry_store
    from utils.compaction_daemon import iter_telemetry_dirs

logger = logging.getLogger(__name__)


def recompute_archive_derived(
    data_dir: str,
    force: bool = False,
    calculator: str | None = None,
    league: str | None = None,
    track: str | None = None,
) -> tuple[int, int]:
    """Scan data_dir for *.primary.brtc and recompute stale (or forced) *.derived.brtc files.

    Returns (inspected_count, recomputed_count).
    """
    inspected = 0
    recomputed = 0

    search_root = os.path.join(data_dir, league) if league else data_dir
    track_filter_lower = track.strip().lower() if track else None

    for tel_dir in iter_telemetry_dirs(search_root):
        try:
            entries = sorted(os.listdir(tel_dir))
        except OSError:
            continue

        for entry in entries:
            if not entry.endswith(telemetry_store.PRIMARY_BRTC_SUFFIX):
                continue
            stem = entry[:-len(telemetry_store.PRIMARY_BRTC_SUFFIX)]
            prim_path = os.path.join(tel_dir, entry)

            if track_filter_lower:
                try:
                    man = telemetry_brtc.read_brtc_manifest(prim_path)
                    t_name = str((man.get('metadata') or {}).get('track_name') or '').lower()
                    if track_filter_lower not in t_name:
                        continue
                except Exception:
                    continue

            inspected += 1
            if telemetry_store.recompute_driver_derived(
                tel_dir,
                stem,
                force=force,
                calculator_name=calculator,
            ):
                recomputed += 1
                logger.info('Recomputed derived channels for %s / %s', tel_dir, stem)

    return inspected, recomputed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Recompute stale or forced *.derived.brtc telemetry channels.')
    parser.add_argument(
        '--data-dir',
        default=config.get('data_dir', '/mnt/data/kibergus/karting/data'),
        help='Root data directory containing league/class/meeting folders.',
    )
    parser.add_argument('--force', action='store_true', help='Recompute even when calculator versions/hashes match.')
    parser.add_argument('--calculator', default=None, help='Only check staleness of a specific calculator name.')
    parser.add_argument('--league', default=None, help='Optional league subdirectory filter.')
    parser.add_argument('--track', default=None, help='Optional track name substring filter.')
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )
    inspected, recomputed = recompute_archive_derived(
        data_dir=args.data_dir,
        force=args.force,
        calculator=args.calculator,
        league=args.league,
        track=args.track,
    )
    logger.info('Derived recomputation finished: inspected=%d, recomputed=%d', inspected, recomputed)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
