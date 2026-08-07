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
