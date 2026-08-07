#!/bin/bash

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
# =============================================================================
# DEVELOPMENT ONLY — DO NOT USE IN PRODUCTION
#
# This script starts Flask's built-in development server with FLASK_DEBUG=1.
# The Werkzeug interactive debugger it enables allows arbitrary code execution
# on the server when an unhandled exception occurs — never expose it to the
# network or use it in staging / production environments.
#
# Production deployments run via Docker + gunicorn (see docker-compose.yml).
# =============================================================================
#
# Script to run the karting results viewer server locally

# Get the script's directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# ANALYSIS_DIR is the same as DIR now
ANALYSIS_DIR="$DIR"

# Check if venv exists
if [ ! -d "$ANALYSIS_DIR/venv" ]; then
    echo "Virtual environment not found in $ANALYSIS_DIR/venv"
    exit 1
fi

# Activate venv and run flask app
source "$ANALYSIS_DIR/venv/bin/activate"
export PYTHONPATH="$ANALYSIS_DIR:$PYTHONPATH"
export FLASK_APP="$ANALYSIS_DIR/app.py"
export FLASK_DEBUG=1  # Development only — safe here because this script is never used in production

# Stop already running server on port 5000 if one exists
PORT=5000
if command -v lsof >/dev/null 2>&1; then
    PID=$(lsof -t -i:$PORT)
    if [ -n "$PID" ]; then
        echo "Stopping already running server on port $PORT (PID: $PID)..."
        kill $PID
        for i in {1..10}; do
            if ! kill -0 $PID 2>/dev/null; then
                break
            fi
            sleep 0.5
        done
        if kill -0 $PID 2>/dev/null; then
            echo "Force killing server (PID: $PID)..."
            kill -9 $PID
            sleep 0.5
        fi
    fi
elif command -v fuser >/dev/null 2>&1; then
    echo "Stopping already running server on port $PORT using fuser..."
    fuser -k $PORT/tcp
    sleep 1
fi

flask run --port $PORT

