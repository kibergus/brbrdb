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

"""
Configuration for plot aesthetics and axis ranges.
"""
import pandas as pd

# Default y-axis range in seconds for violin plots.
DEFAULT_YRANGE = 15.0

# Overrides for specific leagues or classes (as prefixes).
# Use '*' as a wildcard for league or class.
# Structure: { 'league_name': { 'class_prefix': yrange_value } }
YRANGE_OVERRIDES: dict[str, dict[str, float | str]] = {
    'fat_pro': {
        # I want comparable plots between junior and junior lights.
        # So while 5 would be a better value for juniors, it is too small for lites.
        'junior': 7.0,
    },
    'fat': {
        # Ready2race sessiosn go here, with wide variance of skill.
        # So it is hard to find a universal yrange.
        '': 'auto',
    },
    'fekc': {
        '': 7.0,
    },
}


def get_ymin(laps: pd.Series) -> float:
    """
    Return a robust minimum y-axis value, filtering out likely timing glitches.
    Glitches are typically very short laps (e.g., < 40% of the median).
    """
    if laps.empty:
        return 0.0
    median = laps.median()
    # Filter out likely glitches: laps less than 40% of the median.
    # This is safe for karting as even a 'fast' lap won't be < 40% of the median lap.
    filtered_laps = laps[laps > 0.4 * median]
    if filtered_laps.empty:
        return max(0.0, laps.min() - 1.0)
    return max(0.0, filtered_laps.min() - 1.0)


def get_yrange(league: str, class_name: str, laps: pd.Series | None = None) -> float:
    """
    Return the y-range in seconds for a given league and class.
    Checks for exact league match first, then falls back to wildcard '*'.
    Within each, checks for class prefix match.

    If the value configured is 'auto', it calculates a range that fits 90%
    of the lap times (90th percentile - (min - 1)), capped at 2x DEFAULT_YRANGE.
    """
    val: float | str = DEFAULT_YRANGE

    if league in YRANGE_OVERRIDES:
        cfg = YRANGE_OVERRIDES[league]
        # Check specific class prefixes in this league
        found = False
        for prefix, override_val in cfg.items():
            if prefix != '*' and class_name.startswith(prefix):
                val = override_val
                found = True
                break

        if not found and '*' in cfg:
            val = cfg['*']

    if val == 'auto':
        if laps is None or laps.empty:
            return DEFAULT_YRANGE

        # Calculate a range that fits the 90th percentile.
        ymin = get_ymin(laps)
        p90 = laps.quantile(0.90)
        auto_range = p90 - ymin

        return min(auto_range, DEFAULT_YRANGE * 2)

    return float(val)
