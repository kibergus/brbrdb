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


def test_mypy_compliance() -> None:
    """
    Run mypy on the karting_analysis directory to ensure type annotations are correct.
    """
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    result = subprocess.run(
        [sys.executable, '-m', 'mypy', '.'],
        cwd=project_root,
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print('\n--- Mypy Type Checking Errors ---\n')
        print(result.stdout)
        print('\n--- Mypy Stderr ---\n')
        print(result.stderr)
        assert result.returncode == 0, f'Mypy found type errors:\n{result.stdout}'
