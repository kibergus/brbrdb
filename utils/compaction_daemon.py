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

"""Background daemon compacting .active/ telemetry segment directories after 10 minutes of inactivity."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from types import FrameType

try:
    from database import config
    import telemetry_store
except ImportError:
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from database import config
    import telemetry_store

logger = logging.getLogger(__name__)

DEFAULT_IDLE_SECONDS = 600.0
DEFAULT_POLL_INTERVAL_SECONDS = 60.0


def newest_mtime_in_active_dir(active_dir: str) -> float | None:
    """Return the newest mtime (epoch seconds) across all segment files/subdirs in active_dir."""
    if not os.path.isdir(active_dir):
        return None
    newest: float | None = None
    has_segment = False
    for root, dirs, files in os.walk(active_dir):
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                mt = os.path.getmtime(fp)
            except OSError:
                continue
            if fn.endswith('.brt1'):
                has_segment = True
            if newest is None or mt > newest:
                newest = mt
        for dn in dirs:
            dp = os.path.join(root, dn)
            try:
                mt = os.path.getmtime(dp)
                if newest is None or mt > newest:
                    newest = mt
            except OSError:
                continue
    if not has_segment:
        return None
    return newest


def iter_telemetry_dirs(data_dir: str) -> list[str]:
    """Return sorted paths to all {meeting_dir}/telemetry directories under data_dir."""
    if not os.path.isdir(data_dir):
        return []
    found: list[str] = []
    for root, dirs, _ in os.walk(data_dir):
        if 'telemetry' in dirs:
            found.append(os.path.join(root, 'telemetry'))
            dirs.remove('telemetry')
    return sorted(found)


def scan_and_compact_idle_sessions(
    data_dir: str,
    idle_seconds: float = DEFAULT_IDLE_SECONDS,
    now: float | None = None,
) -> int:
    """Scan data_dir for .active/ directories idle for >= idle_seconds and compact them into .brtc."""
    check_now = time.time() if now is None else now
    compacted_count = 0

    for tel_dir in iter_telemetry_dirs(data_dir):
        try:
            entries = sorted(os.listdir(tel_dir))
        except OSError:
            continue

        for entry in entries:
            if not entry.endswith(telemetry_store.ACTIVE_DIR_SUFFIX):
                continue
            act_path = os.path.join(tel_dir, entry)
            newest_mt = newest_mtime_in_active_dir(act_path)
            if newest_mt is None:
                # Empty .active leftover directory -> clean up quietly
                telemetry_store.compact_driver_session(
                    tel_dir,
                    entry[:-len(telemetry_store.ACTIVE_DIR_SUFFIX)],
                    delete_legacy_csv=False,
                )
                continue

            age_s = check_now - newest_mt
            if age_s < idle_seconds:
                continue

            stem = entry[:-len(telemetry_store.ACTIVE_DIR_SUFFIX)]
            try:
                if telemetry_store.compact_driver_session(tel_dir, stem, delete_legacy_csv=True):
                    compacted_count += 1
                    logger.info('Compacted idle telemetry session %s/%s (idle %.1fs)', tel_dir, stem, age_s)
            except Exception:
                logger.exception('Failed compacting telemetry session %s/%s', tel_dir, stem)

    return compacted_count


def run_daemon(
    data_dir: str,
    interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    idle_seconds: float = DEFAULT_IDLE_SECONDS,
    once: bool = False,
) -> None:
    """Run the compaction daemon loop until SIGTERM/SIGINT (or a single pass when once=True)."""
    stop_event = threading.Event()

    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        logger.info('Compaction daemon received signal %d, shutting down...', signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    logger.info(
        'Starting telemetry compaction daemon on %s (poll=%.1fs, idle=%.1fs)',
        data_dir,
        interval_seconds,
        idle_seconds,
    )

    while not stop_event.is_set():
        scan_and_compact_idle_sessions(data_dir, idle_seconds=idle_seconds)
        if once:
            break
        stop_event.wait(timeout=max(1.0, interval_seconds))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Background telemetry .active -> .brtc compaction daemon.')
    parser.add_argument(
        '--data-dir',
        default=config.get('data_dir', '/mnt/data/kibergus/karting/data'),
        help='Root data directory containing league/class/meeting folders.',
    )
    parser.add_argument(
        '--interval',
        type=float,
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        help='Sweep interval in seconds (default: 60).',
    )
    parser.add_argument(
        '--idle-seconds',
        type=float,
        default=DEFAULT_IDLE_SECONDS,
        help='Required inactivity duration in seconds before compacting (default: 600 = 10 min).',
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run a single sweep and exit.',
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )
    run_daemon(
        data_dir=args.data_dir,
        interval_seconds=args.interval,
        idle_seconds=args.idle_seconds,
        once=args.once,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
