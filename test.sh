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
# Combined test runner for Python and JavaScript

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
ANALYSIS_DIR="$DIR"

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}Starting Python tests (pytest)...${NC}"
if [ -d "$ANALYSIS_DIR/venv" ]; then
    source "$ANALYSIS_DIR/venv/bin/activate"
    export PYTHONPATH="$ANALYSIS_DIR:$PYTHONPATH"
    pytest
    PY_EXIT=$?
else
    echo -e "${RED}Warning: Virtual environment not found, running pytest with system python...${NC}"
    pytest
    PY_EXIT=$?
fi

echo -e "\n${GREEN}Starting JavaScript tests (vitest)...${NC}"
npm test
JS_EXIT=$?

echo -e "\n--- Summary ---"
if [ $PY_EXIT -eq 0 ]; then
    echo -e "Python tests: ${GREEN}PASSED${NC}"
else
    echo -e "Python tests: ${RED}FAILED${NC}"
fi

if [ $JS_EXIT -eq 0 ]; then
    echo -e "JavaScript tests: ${GREEN}PASSED${NC}"
else
    echo -e "JavaScript tests: ${RED}FAILED${NC}"
fi

# Exit with combined status
if [ $PY_EXIT -eq 0 ] && [ $JS_EXIT -eq 0 ]; then
    echo -e "\n${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "\n${RED}Some tests failed.${NC}"
    exit 1
fi
