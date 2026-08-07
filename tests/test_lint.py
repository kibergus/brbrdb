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

import subprocess
import os
import sys


def test_flake8_compliance() -> None:
    """
    Run flake8 on the karting_analysis directory to ensure code style compliance.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    result = subprocess.run(
        [sys.executable, '-m', 'flake8', '.'],
        cwd=project_root,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print('\n--- Flake8 Linter Errors ---\n')
        print(result.stdout)
        print('\n--- Flake8 Stderr ---\n')
        print(result.stderr)
        assert result.returncode == 0, f'Flake8 found lint errors:\n{result.stdout}'
