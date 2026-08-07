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
# Script to run test coverage analysis for the karting analysis project

# Get the script's directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Check if venv exists
if [ ! -d "$DIR/venv" ]; then
    echo "Virtual environment not found in $DIR/venv"
    exit 1
fi

# Activate venv
source "$DIR/venv/bin/activate"

# Ensure PYTHONPATH includes the current directory
export PYTHONPATH="$DIR:$PYTHONPATH"

echo "Running tests with coverage..."

# Run pytest with coverage
# --cov=. : measure coverage for the current directory
# --cov-report=term-missing : show missing lines in terminal
# --cov-report=html : generate HTML report in htmlcov/
pytest --cov=. --cov-report=term-missing --cov-report=html

echo ""
echo "Coverage analysis complete."
echo "HTML report available at: $DIR/htmlcov/index.html"
