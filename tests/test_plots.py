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

import pandas as pd
import numpy as np
import plots


def test_get_driver_colors_with_nan() -> None:
    # Create mock dataframes with NaN driver names
    df1 = pd.DataFrame({
        'Name': ['Alice', np.nan, 'Bob'],
        'LapTimeSeconds': [50.1, 52.3, 51.2],
        'Lap': [1, 2, 3]
    })
    df2 = pd.DataFrame({
        'Name': ['Charlie', None, 'Alice'],
        'LapTimeSeconds': [53.1, 54.2, 50.8],
        'Lap': [1, 2, 3]
    })

    # Call get_driver_colors
    colors = plots.get_driver_colors([df1, df2])

    # Check that colors dictionary has string keys
    assert 'Alice' in colors
    assert 'Bob' in colors
    assert 'Charlie' in colors
    assert '' in colors  # the NaN should be converted to ''

    # Check that all keys are strings
    for k in colors.keys():
        assert isinstance(k, str)


def test_prepare_plot_df_with_nan() -> None:
    df = pd.DataFrame({
        'Name': ['Alice', np.nan, 'Bob'],
        'LapTimeSeconds': [50.1, 52.3, 51.2],
        'Lap': [1, 2, 3],
        'Pos': [1, 2, 3]
    })

    plot_df = plots._prepare_plot_df(df)
    assert not plot_df.empty
    # The NaN in the Name column should have been converted to ''
    assert plot_df['Name'].isna().sum() == 0
    assert '' in plot_df['Name'].values


def test_plot_violin_gap_combined_max_y_violin() -> None:
    df_violin = pd.DataFrame({
        'SessionID': ['s1', 's1', 's1'],
        'SessionName': ['Final', 'Final', 'Final'],
        'DateTime': ['2026-08-08 10:00', '2026-08-08 10:00', '2026-08-08 10:00'],
        'League': ['fat_pro', 'fat_pro', 'fat_pro'],
        'Class': ['cadet', 'cadet', 'cadet'],
        'Name': ['Alice', 'Bob', 'Alice'],
        'LapTimeSeconds': [50.1, 52.3, 51.2],
        'Lap': [1, 1, 2],
        'Pos': [1, 2, 1],
    })
    df_gap = df_violin.copy()

    fig = plots.plot_violin_gap_combined(
        violin_dfs=[df_violin],
        gap_dfs=[df_gap],
        max_y=10.0,
        max_y_violin=3.5,
    )
    assert fig is not None


def test_gap_plot_truncated_labels() -> None:
    df_gap = pd.DataFrame({
        'SessionID': ['s1', 's1', 's1', 's1'],
        'SessionName': ['Final', 'Final', 'Final', 'Final'],
        'DateTime': ['2026-08-08 10:00', '2026-08-08 10:00', '2026-08-08 10:00', '2026-08-08 10:00'],
        'League': ['fat_pro', 'fat_pro', 'fat_pro', 'fat_pro'],
        'Class': ['cadet', 'cadet', 'cadet', 'cadet'],
        'Name': ['Alice', 'Bob', 'Charlie', 'David'],
        'LapTimeSeconds': [50.0, 52.0, 58.0, 65.0],  # Charlie & David will exceed max_y=5.0
        'Lap': [1, 1, 1, 1],
        'Pos': [1, 2, 3, 4],
    })

    fig, ax = plots.plt.subplots(figsize=(10, 5))
    plots._draw_gap_on_ax(ax, df_gap, max_y=5.0)

    # All 4 driver labels should be drawn in the text objects
    texts = [t.get_text() for t in ax.texts]
    for driver in ['Alice', 'Bob', 'Charlie', 'David']:
        assert driver in texts


def test_violin_plot_with_deleted_laps() -> None:
    df = pd.DataFrame({
        'SessionID': ['s1'] * 5,
        'SessionName': ['Qualifying'] * 5,
        'DateTime': ['2026-08-08 10:00'] * 5,
        'League': ['fat_pro'] * 5,
        'Class': ['cadet'] * 5,
        'Name': ['Alice', 'Alice', 'Alice', 'Bob', 'Bob'],
        'LapTimeSeconds': [52.0, 50.0, 53.0, 54.0, 55.0],
        'Lap': [1, 2, 3, 1, 2],
        'Pos': [1, 1, 1, 2, 2],
        'LapTimeDeleted': [False, True, False, False, False]
    })

    # Test _get_driver_stats: Alice's best lap should be 52.0, not the deleted 50.0
    plot_df = plots._prepare_plot_df(df)
    sorted_drivers, best_laps = plots._get_driver_stats(plot_df)
    assert sorted_drivers == ['Alice', 'Bob']
    assert best_laps['Alice'] == 52.0
    assert best_laps['Bob'] == 54.0

    # Test plot_split_violins
    fig = plots.plot_split_violins(df, base_title='Test Session')
    assert fig is not None

    # Verify that ax has collections for both regular dots and the deleted 'x' marker
    ax = fig.axes[0]
    # In matplotlib, swarmplot with marker='x' creates a PathCollection
    # Check that at least one collection has offsets near y=50.0
    has_deleted_point = False
    for col in ax.collections:
        offsets = np.asarray(col.get_offsets())
        if offsets.size > 0:
            y_vals = offsets[:, 1]
            if np.any(np.isclose(y_vals, 50.0, atol=0.1)):
                has_deleted_point = True
                break
    assert has_deleted_point, "Deleted lap at 50.0 should be plotted"

    plots.plt.close(fig)
