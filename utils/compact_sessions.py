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

"""CLI utility to migrate legacy telemetry/*.csv archives and .active/ segments into .brtc format."""

from __future__ import annotations

import argparse
import logging
import os
import sys

try:
    from database import config
    import telemetry_store
    from utils.compaction_daemon import iter_telemetry_dirs
except ImportError:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from database import config
    import telemetry_store
    from utils.compaction_daemon import iter_telemetry_dirs

logger = logging.getLogger(__name__)


def compact_archive(
    data_dir: str,
    dry_run: bool = False,
    keep_csv: bool = False,
    league: str | None = None,
) -> tuple[int, int]:
    """Scan data_dir and compact all legacy .csv and .active/ sessions into .brtc.

    Returns (matched_sessions, compacted_sessions).
    """
    matched = 0
    compacted = 0

    search_root = os.path.join(data_dir, league) if league else data_dir
    for tel_dir in iter_telemetry_dirs(search_root):
        try:
            entries = sorted(os.listdir(tel_dir))
        except OSError:
            continue

        candidate_stems: set[str] = set()
        for entry in entries:
            if entry.endswith('.csv'):
                candidate_stems.add(entry[:-4])
            elif entry.endswith(telemetry_store.ACTIVE_DIR_SUFFIX):
                candidate_stems.add(entry[:-len(telemetry_store.ACTIVE_DIR_SUFFIX)])

        for stem in sorted(candidate_stems):
            matched += 1
            if dry_run:
                logger.info('[DRY-RUN] Would compact %s / %s (delete_csv=%s)', tel_dir, stem, not keep_csv)
                continue
            if telemetry_store.compact_driver_session(
                tel_dir,
                stem,
                delete_legacy_csv=(not keep_csv),
            ):
                compacted += 1
                logger.info('Compacted %s / %s', tel_dir, stem)

    return matched, compacted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Compact legacy telemetry CSVs and .active segments into .brtc.')
    parser.add_argument(
        '--data-dir',
        default=config.get('data_dir', '/mnt/data/kibergus/karting/data'),
        help='Root data directory containing league/class/meeting folders.',
    )
    parser.add_argument('--league', default=None, help='Optional league subdirectory filter.')
    parser.add_argument('--dry-run', action='store_true', help='Report candidates without modifying files.')
    parser.add_argument(
        '--keep-csv',
        action='store_true',
        help='Keep original legacy .csv files after creating .brtc (default: delete .csv).',
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )
    matched, compacted = compact_archive(
        data_dir=args.data_dir,
        dry_run=args.dry_run,
        keep_csv=args.keep_csv,
        league=args.league,
    )
    logger.info('Archive compaction finished: matched=%d, compacted=%d', matched, compacted)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
