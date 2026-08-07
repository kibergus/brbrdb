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
# Path to the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
# Path to the analysis directory
ANALYSIS_DIR="$(dirname "$SCRIPT_DIR")"

# Activate the virtual environment
source "$ANALYSIS_DIR/venv/bin/activate"

# Launch Jupyter Notebook
# We use the venv's python to run jupyter to ensure the environment is correct
"$ANALYSIS_DIR/venv/bin/python" -m jupyter notebook --notebook-dir="$ANALYSIS_DIR" --config="$SCRIPT_DIR/jupyter_notebook_config.py"

