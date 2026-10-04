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

# Stage 1: Build the race_tools wheel
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN pip install --no-cache-dir build
RUN git clone https://github.com/kibergus/race_tools.git

WORKDIR /build/race_tools
RUN python -m build --wheel

# Stage 2: Final runtime image
FROM python:3.12-slim

# Install ffmpeg for transcode operations and g++/libbrotli-dev for native C++ extensions
RUN apt-get update && apt-get install -y ffmpeg g++ libbrotli-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy and install the built race_tools wheel
COPY --from=builder /build/race_tools/dist/*.whl .
RUN pip install *.whl && rm *.whl

# Copy all application files (subject to .dockerignore)
COPY . .
RUN python setup.py build_ext --inplace

# Expose port 5000 internally
EXPOSE 5000

# Run the application with gunicorn for production
# Bind to 0.0.0.0 so it's accessible from outside the container
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "4", "--timeout", "600", "app:app"]
