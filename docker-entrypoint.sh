#!/usr/bin/env bash
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

set -e

python -m utils.compaction_daemon --interval 60 --idle-seconds 600 &
DAEMON_PID=$!

cleanup() {
    kill -TERM "$DAEMON_PID" 2>/dev/null || true
    if [ -n "${APP_PID:-}" ]; then
        kill -TERM "$APP_PID" 2>/dev/null || true
        wait "$APP_PID" 2>/dev/null || true
    fi
    wait "$DAEMON_PID" 2>/dev/null || true
}
trap cleanup SIGTERM SIGINT

"$@" &
APP_PID=$!
wait "$APP_PID"
STATUS=$?
cleanup
exit "$STATUS"

