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
plots.py – Karting race visualisation library.

All public functions return a matplotlib Figure object instead of calling
plt.show(), so they can be used both interactively (call fig.show() / plt.show()
yourself) and programmatically (e.g. saved to a buffer for a web response).
"""

import os
import re
import io
from typing import Callable, Sequence

import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
import seaborn as sns
from matplotlib.ticker import FuncFormatter, MultipleLocator
from matplotlib.transforms import ScaledTranslation
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from matplotlib.axes import Axes
from matplotlib.figure import Figure, SubFigure
import plot_config
from race_tools import sanitize
import aliases

matplotlib.use('Agg')   # non-interactive backend; safe to call before plt usage

# ---------------------------------------------------------------------------
# Global style
# ---------------------------------------------------------------------------

plt.style.use('dark_background')
plt.rcParams['figure.figsize'] = [10, 10]
plt.rcParams['font.family'] = 'sans-serif'
plt.rcParams['axes.grid'] = True
plt.rcParams['grid.alpha'] = 0.2
plt.rcParams['grid.linestyle'] = '--'
plt.rcParams['savefig.bbox'] = 'standard'
plt.rcParams['savefig.pad_inches'] = 0
plt.rcParams['figure.autolayout'] = False

# ---------------------------------------------------------------------------
# Constants (override via module attributes if needed)
# ---------------------------------------------------------------------------

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'brbrkitten.png')

# Design tokens for consistent spacing across different aspect ratios
TITLE_TOP_PX = 22        # Top edge of the header title (pixels from top)
AXES_TOP_PX = 90         # Top of the axes area (pixels from top)
AXES_TOP_SPLIT_PX = 100  # Top of the axes area for multi-row plots
AXES_BOTTOM_PX = 135     # Bottom margin for rotated labels
AXES_LEFT_PX = 140       # Left margin for y-axis labels

AXES_RIGHT_PX = 38       # Right margin

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_hero(driver_name: str, hero_names: list[str] | None) -> bool:
    """Helper to check if a driver is a hero, supporting a list of strings."""
    if not hero_names:
        return False
    return any(h in driver_name for h in hero_names)


def _format_time(x: float, pos: int | None = None) -> str:
    """Format seconds as M:SS (no hundredths) for y-axis labels."""
    del pos  # Unused: signature required by matplotlib.
    minutes = int(x // 60)
    seconds = int(x % 60)
    return f'{minutes}:{seconds:02d}'


def _get_top_margin_frac(fig: Figure, margin_px: int = AXES_TOP_PX) -> float:
    """Calculate the relative 'top' parameter for subplots_adjust to hit a fixed pixel margin."""
    fig_height_px = fig.get_figheight() * fig.dpi
    return 1.0 - (margin_px / fig_height_px)


def _get_bottom_margin_frac(fig: Figure, margin_px: int = AXES_BOTTOM_PX) -> float:
    fig_height_px = fig.get_figheight() * fig.dpi
    return margin_px / fig_height_px


def _get_left_margin_frac(fig: Figure | SubFigure, margin_px: int = AXES_LEFT_PX) -> float:
    # get_size_inches is present on both Figure and SubFigure in Matplotlib 3.4+
    fig_width_px = fig.get_size_inches()[0] * fig.dpi  # type: ignore[union-attr]
    return margin_px / fig_width_px


def _get_right_margin_frac(fig: Figure | SubFigure, margin_px: int = AXES_RIGHT_PX) -> float:
    # get_size_inches is present on both Figure and SubFigure in Matplotlib 3.4+
    fig_width_px = fig.get_size_inches()[0] * fig.dpi  # type: ignore[union-attr]
    return 1.0 - (margin_px / fig_width_px)


def _apply_categorical_padding(
    ax: Axes, num_items: int, pad_px: int = 40, margin_right_px: int = AXES_RIGHT_PX
) -> None:
    """Adjust x-limits to maintain a fixed pixel padding on the sides of categories."""
    if num_items <= 0:
        return

    fig = ax.figure
    # Figure width in pixels. get_size_inches is present on both Figure and SubFigure in Matplotlib 3.4+
    w_px = fig.get_size_inches()[0] * fig.dpi  # type: ignore[union-attr]

    # Use the actual axis width if it has been positioned, otherwise fallback to estimate.
    # get_position() returns a Bbox in figure coordinates (0 to 1).
    ax_pos = ax.get_position()
    if ax_pos.width > 0.05:
        ax_w_px = ax_pos.width * w_px
    else:
        ax_w_px = w_px - (AXES_LEFT_PX + margin_right_px)

    # Content width (from edge to edge of violins) is (N-1) spaces + 0.8 units.
    px_per_unit = (ax_w_px - 2 * pad_px) / max(0.8, num_items - 0.2)

    pad_units = pad_px / px_per_unit
    half_width = (num_items - 1) / 2.0
    half_range_units = (num_items - 1) / 2.0 + 0.4 + pad_units

    ax.set_xlim(half_width - half_range_units, half_width + half_range_units)


def _format_time_full(x: float, pos: int | None = None) -> str:
    """Format seconds as M:SS.ss (with hundredths) for display elsewhere."""
    del pos  # Unused: signature required by matplotlib.
    minutes = int(x // 60)
    seconds = x % 60
    return f'{minutes}:{seconds:05.2f}        '


_formatter = FuncFormatter(_format_time)


def _add_logo(fig: Figure) -> None:
    """Add logo at a fixed pixel offset from the top-left corner."""
    if not os.path.exists(LOGO_PATH):
        return
    img = plt.imread(LOGO_PATH)
    imagebox = OffsetImage(img, zoom=0.12)
    # Place at (0, 1) in figure fraction (top-left) with a fixed pixel offset.
    # xybox specifies the offset in pixels; box_alignment=(0, 1) means the
    # top-left of the logo box is at the xybox position.
    ab = AnnotationBbox(imagebox, (0, 1), xycoords='figure fraction',
                        xybox=(25, -25), boxcoords='offset pixels',
                        frameon=False, box_alignment=(0, 1), pad=0)
    fig.add_artist(ab)


def _add_lap_colorbar(
    fig: Figure | SubFigure,
    plot_df: pd.DataFrame,
    ax: Axes | None = None,
    lap_col: str = 'Lap',
    color: str = 'white'
) -> None:
    """Add a small Lap colorbar. If ax is provided, it is positioned relative to that Axes."""
    laps = plot_df[lap_col].dropna()
    if laps.empty:
        return
    first_lap = int(laps.min())
    last_lap = int(laps.max())
    if first_lap >= last_lap:
        return

    norm = Normalize(vmin=first_lap, vmax=last_lap)
    sm = ScalarMappable(norm=norm, cmap='spring')

    # get_size_inches is present on both Figure and SubFigure in Matplotlib 3.4+
    fig_w_px = fig.get_size_inches()[0] * fig.dpi  # type: ignore[union-attr]
    fig_h_px = fig.get_size_inches()[1] * fig.dpi  # type: ignore[union-attr]
    cbar_width_px = 80
    cbar_height_px = 12

    if ax is None:
        # Target Figure: Bottom-right at fixed pixel offset
        cbar_right_pad_px = 70
        cbar_x = (fig_w_px - cbar_right_pad_px - cbar_width_px) / fig_w_px
        cbar_y = 155 / fig_h_px
        cbar_ax = fig.add_axes((cbar_x, cbar_y, cbar_width_px / fig_w_px, cbar_height_px / fig_h_px))
    else:
        # Target Axes: Same pixel size, but positioned relative to this axis.
        bbox = ax.get_position()
        ax_w_px = bbox.width * fig_w_px
        ax_h_px = bbox.height * fig_h_px

        # Calculate inset fraction to achieve exactly cbar_width_px
        width_frac = cbar_width_px / ax_w_px
        height_frac = cbar_height_px / ax_h_px

        # Stay inside the frame: 32px from right axis edge, 20px above bottom axis edge
        x_frac = 1.0 - width_frac - (32 / ax_w_px)
        y_frac = 20 / ax_h_px
        cbar_ax = ax.inset_axes((x_frac, y_frac, width_frac, height_frac))

    cbar = fig.colorbar(sm, cax=cbar_ax, orientation='horizontal')

    mid_lap = (first_lap + last_lap) // 2
    cbar.set_ticks([first_lap, mid_lap, last_lap])
    cbar.ax.set_xticklabels([str(first_lap), str(mid_lap), str(last_lap)], fontsize=9, color=color)
    # Put labels on top of the bar for better visibility above x-labels
    cbar.ax.xaxis.set_ticks_position('top')
    cbar.ax.tick_params(size=4, color=color, pad=1, labelsize=9)

    # Position "Lap:" text at fixed pixel relative to the colorbar
    if ax is None:
        cbar_x_val = (fig_w_px - 70 - cbar_width_px) / fig_w_px
        cbar_y_val = 155 / fig_h_px
        fig.text(cbar_x_val - (12 / fig_w_px), cbar_y_val + (6 / fig_h_px), 'Lap:',
                 fontsize=10, va='center', ha='right', fontweight='bold', color=color)
    else:
        # Re-use the calculated x_frac for the inset axis
        ax_w_px_val = ax.get_position().width * fig_w_px
        ax.text(x_frac - (12 / ax_w_px_val), y_frac + (height_frac / 2.0), 'Lap:',
                transform=ax.transAxes, fontsize=9, va='center', ha='right',
                fontweight='bold', color=color)


def _add_header(fig: Figure, title: str, is_social: bool = False) -> None:
    """Add a consistent header with logo and title at absolute pixel offsets."""
    _add_logo(fig)

    fig_height_px = fig.get_figheight() * fig.dpi
    y_frac = 1.0 - (TITLE_TOP_PX / fig_height_px)

    # Place title in the top-right (or top-center for social)
    if is_social:
        x, ha = 0.5, 'center'
    else:
        x, ha = 0.98, 'right'

    # Using suptitle ensures its handled as a standard figure title
    # Using top alignment and a fixed offset for robustness
    fig.suptitle(title, fontsize=16, x=x, y=y_frac, ha=ha, va='top', color='white')


def _prepare_plot_df(df: pd.DataFrame) -> pd.DataFrame:
    """Filter for valid laptimes and handle numeric positions."""
    plot_df = df[(df.get('LapTimeSeconds', pd.Series(dtype=float)) > 0) &
                 (df.get('Lap', pd.Series(dtype=int)) > 0)].copy()
    plot_df['Pos_Numeric'] = pd.to_numeric(plot_df.get('Pos', pd.Series(dtype=int)), errors='coerce').fillna(999)
    if 'Name' in plot_df.columns:
        plot_df['Name'] = plot_df['Name'].fillna('').astype(str)
    return plot_df


def get_driver_colors(
    dfs: list[pd.DataFrame], hero_names: list[str] | None = None
) -> dict[str, str | tuple[float, ...]]:
    """Generate a consistent driver color mapping based on alphabetical order of all drivers in dfs."""
    all_names: set[str] = set()
    for df in dfs:
        if 'Name' in df.columns:
            all_names.update(df['Name'].fillna('').astype(str).unique())
    sorted_all_names = sorted(all_names)
    palette = sns.color_palette('husl', len(sorted_all_names))
    return {d: ('yellow' if _is_hero(d, hero_names) else palette[i])
            for i, d in enumerate(sorted_all_names)}


def _get_driver_stats(plot_df: pd.DataFrame) -> tuple[list[str], pd.Series]:
    """Get finishing position and best lap time sorted by position."""
    stats = plot_df.sort_values('Lap').groupby('Name').agg({
        'Pos_Numeric': 'last',
        'LapTimeSeconds': 'min'
    }).sort_values('Pos_Numeric')
    return stats.index.tolist(), stats['LapTimeSeconds']


def _style_racing_ax(ax: Axes) -> None:
    """Apply standard racing grid and formatters to an axis."""
    ax.set_ylabel('Lap Time', fontsize=12)
    ax.set_xlabel('')
    ax.yaxis.set_major_formatter(_formatter)
    # Ticks every 1 second as requested
    ax.yaxis.set_major_locator(MultipleLocator(1))

    # General grid lines for every second
    # Changed to solid and higher alpha to be more visible
    ax.grid(True, which='major', axis='y', color='gray', linestyle='-', alpha=0.5)

    # Highlight 5-second intervals.
    # Now slightly lighter (alpha 0.2 instead of 0.45) as requested.
    for val in range(0, 3605, 5):
        ax.axhline(val, color='gray', linestyle='-', alpha=0.5, linewidth=1.5, zorder=0)


def _style_gap_p5_ax(ax: Axes) -> None:
    """Apply standard racing grid and formatters to an axis for Gap to 5th Percentile."""
    ax.set_ylabel('Gap to 5th Percentile (s)', fontsize=12)
    ax.set_xlabel('')
    ax.yaxis.set_major_formatter(plt.FormatStrFormatter('%.2f'))
    ax.yaxis.set_major_locator(MultipleLocator(0.5))
    ax.grid(True, which='major', axis='y', color='gray', linestyle='-', alpha=0.5)
    ax.axhline(0, color='white', linestyle='--', alpha=0.6, linewidth=1.5, zorder=10)


def _apply_driver_labels(
    ax: Axes, sorted_drivers: list[str], best_lap_times: pd.Series, hero_names: list[str] | None = None
) -> None:
    """Format and color driver labels on the x-axis."""
    if len(sorted_drivers) > 12:
        labels = sorted_drivers
    else:
        labels = [f'{_format_time_full(best_lap_times[d])}\n{d}' for d in sorted_drivers]

    ax.set_xticks(range(len(sorted_drivers)))
    rot = 15 if len(sorted_drivers) <= 8 else 25
    tick_labels = ax.set_xticklabels(
        labels, rotation=rot, ha='right', multialignment='right', fontsize=10
    )

    for tick, driver in zip(tick_labels, sorted_drivers):
        tick.set_color('yellow' if _is_hero(driver, hero_names) else 'white')

    dx, dy = 18, 8
    offset = ScaledTranslation(dx / 72, dy / 72, ax.figure.dpi_scale_trans)
    for label in ax.get_xticklabels():
        label.set_transform(label.get_transform() + offset)


def _get_plot_title(sample_row: pd.Series, suffix: str = '', session_names: list[str] | None = None) -> str:
    """Generate and format the plot title from sample data."""
    league = str(sample_row.get('League', ''))
    track = str(sample_row.get('TrackName', ''))
    date = str(sample_row.get('Date', ''))
    class_name = str(sample_row.get('Class', ''))

    if session_names:
        # Unique session names while preserving order
        unique_names = []
        for n in session_names:
            if n not in unique_names:
                unique_names.append(n)

        if len(unique_names) > 1:
            shortened = [unique_names[0]]
            for i in range(1, len(unique_names)):
                prev = unique_names[i - 1]
                curr = unique_names[i]
                # Find common prefix using word-terminating space
                cp = os.path.commonprefix([prev, curr])
                last_space = cp.rfind(' ')
                if last_space != -1:
                    cp = cp[:last_space + 1]
                    shortened.append(curr[len(cp):])
                else:
                    shortened.append(curr)
            if len(shortened) > 2:
                race_name = ', '.join(shortened[:-1]) + ' & ' + shortened[-1]
            else:
                race_name = ' & '.join(shortened)
        else:
            race_name = unique_names[0] if unique_names else ''
    else:
        race_name = str(sample_row.get('SessionName', ''))

    league = aliases.get_league_name(league)

    # Resolve class display name using canonical names from aliases.py
    class_name = aliases.get_class_name(class_name)
    # Ensure title uses standard abbreviation for lightweight classes
    class_name = (
        class_name.replace(' light', ' LW')
        .replace(' super light', ' SLW')
        .replace(' Lightweight', ' LW')
        .replace(' Super Lightweight', ' SLW')
    )

    for old, new in [('Super Lightweight', 'SLW'), ('Lightweight', 'LW')]:
        league = league.replace(old, new)
        race_name = race_name.replace(old, new)

    race_name = sanitize.strip_race_prefix(race_name)
    race_name = re.sub(r' Group ', ' ', race_name).strip()

    # Clean up redundant multi-class session names as they are too long.
    for pattern in [
        r'Cadet R2R / Cadet( Light)?( / Cadet( Light)?)?',
        r'Junior R2R / Junior( Light)?( / Junior( Light)?)?',
        r'Junior / Junior Light',
        r'Cadet / Cadet Light',
        r'Bambino( R2R)? / Bambino( R2R)?',
    ]:
        race_name = re.sub(pattern, '', race_name, flags=re.IGNORECASE).strip()

    # Avoid duplication like "Cadet Cadet Final".
    # For the purpose of this comparison ignore the LW and SLW suffixes in the class name.
    # In race names, these are encoded in various ways; matching base class is fine.
    match_class = class_name.replace(' SLW', '').replace(' LW', '')
    if race_name.lower().startswith(match_class.lower()):
        race_name = race_name[len(match_class):].strip()

        # If the subclass is duplicated in race_name (possibly with a synonym like 'Light'),
        # we strip it from class_name to allow the race_name version to take precedence.
        sub_synonyms = {
            'SLW': ['slw', 'super lightweight', 'super light', 'superlight'],
            'LW': ['lw', 'lightweight', 'light']
        }
        for sub, synonyms in sub_synonyms.items():
            if f' {sub}' in class_name:
                for syn in synonyms:
                    if race_name.lower().startswith(syn):
                        # Ensure word boundary to avoid partial matches
                        if len(race_name) == len(syn) or not race_name[len(syn)].isalnum():
                            class_name = class_name.replace(f' {sub}', '')
                            break

    if 'MeetingName' in sample_row.index and suffix.startswith('(Combined'):
        return f'{league} {track} {date} {class_name} - Progression {suffix}'.strip()

    # Add AM/PM from meeting name if present
    meeting_name = str(sample_row.get('MeetingName', ''))
    if re.search(r'\bAM\b', meeting_name):
        race_name = f'AM {race_name}'.strip()
    elif re.search(r'\bPM\b', meeting_name):
        race_name = f'PM {race_name}'.strip()

    return f'{league} {track} {date} {class_name} {race_name} {suffix}'.strip()


def _get_figsize(width: float, height: float, aspect: str | None) -> tuple[float, float]:
    """Determine figure size from aspect ratio (e.g. "4:5")."""
    if not aspect:
        return (width, height)

    # Match two numbers separated by :
    m = re.match(r'^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$', str(aspect).strip())
    if not m:
        raise ValueError(f"Invalid aspect ratio syntax: '{aspect}'. Expected 'W:H' (e.g. '4:5')")

    w_ratio, h_ratio = float(m.group(1)), float(m.group(2))
    if w_ratio == 0:
        raise ZeroDivisionError('Aspect ratio width cannot be zero')

    return (width, (width * h_ratio) / w_ratio)


def _draw_pace_on_ax(
    ax: Axes, plot_df: pd.DataFrame, sorted_labels: list[str],
    label_col: str, ymax: float, linewidth: float = 1.0
) -> None:
    """Draw horizontal pace lines, handling overflow above ymax.
    Pace is defined as mean lap time, excluding the first lap for non-final sessions.
    """
    if plot_df.empty:
        return

    # Ensure SessionName is string and Lap is numeric for masking
    plot_df = plot_df.copy()
    plot_df['SessionName'] = plot_df['SessionName'].fillna('').astype(str)
    plot_df['Lap'] = pd.to_numeric(plot_df['Lap'], errors='coerce').fillna(0)

    # Pace rule: exclude first lap for non-final sessions
    is_race_mask = plot_df['SessionName'].str.contains('final|heat', case=False, na=False, regex=True)
    valid_laps_mask = is_race_mask | (plot_df['Lap'] > 1)
    pace_df = plot_df[valid_laps_mask]

    if pace_df.empty:
        return

    pace_stats = pace_df.groupby(label_col)['LapTimeSeconds'].mean()

    ymin, _ = ax.get_ylim()
    # Coordinate just above the graph for overflow pace
    overflow_y = ymax + (ymax - ymin) * 0.02

    for i, label in enumerate(sorted_labels):
        if label not in pace_stats:
            continue
        pace = pace_stats[label]

        is_overflow = pace > ymax
        y = overflow_y if is_overflow else pace

        ax.hlines(
            y=y, xmin=i - 0.2, xmax=i + 0.2, color='white',
            linewidth=linewidth, zorder=50, clip_on=not is_overflow
        )


def _draw_overflow_dots(
    ax: Axes, plot_df: pd.DataFrame, sorted_drivers: list[str], ymax: float,
    lap_col: str = 'Lap', hue_norm: Normalize | None = None, dot_size: int = 4
) -> None:
    """Draw markers just above ymax for any LapTimeSeconds values that exceed it."""
    overflow = plot_df[plot_df['LapTimeSeconds'] > ymax].copy()
    if overflow.empty:
        return

    ymin_cur, _ = ax.get_ylim()
    overflow_y = ymax + (ymax - ymin_cur) * 0.02

    overflow = overflow.copy()
    overflow['LapTimeSeconds'] = overflow_y

    ax.set_ylim(ymin_cur, overflow_y + (ymax - ymin_cur) * 0.02)

    existing_collections = set(id(c) for c in ax.collections)

    sns.swarmplot(
        data=overflow, x='Name', y='LapTimeSeconds', order=sorted_drivers,
        hue=lap_col, palette='spring', hue_norm=hue_norm,
        size=dot_size, ax=ax, legend=False,
        warn_thresh=1.0,
    )

    ax.set_ylim(ymin_cur, ymax)

    for collection in ax.collections:
        if id(collection) not in existing_collections:
            collection.set_clip_on(False)

    ax.set_xlabel('')


def _draw_violin_on_ax(
    ax: Axes, plot_df: pd.DataFrame, sorted_drivers: list[str],
    best_lap_times: pd.Series, lap_col: str, hue_norm: Normalize | None = None, dot_size: int = 4,
    axis_style: Callable[[Axes], None] | None = None, hero_names: list[str] | None = None
) -> None:
    """Shared logic for drawing violin + swarm plots on a specific axis."""
    subset_df = plot_df[plot_df['Name'].isin(sorted_drivers)].copy()

    sns.violinplot(
        data=subset_df, x='Name', y='LapTimeSeconds', order=sorted_drivers,
        color='#007acc', linewidth=0, width=0.8, inner=None, ax=ax,
        density_norm='width'
    )

    sns.swarmplot(
        data=subset_df, x='Name', y='LapTimeSeconds', order=sorted_drivers,
        hue=lap_col, palette='spring', hue_norm=hue_norm,
        size=dot_size, ax=ax, legend=False,
        warn_thresh=1.0,
    )

    if axis_style:
        axis_style(ax)
    else:
        _style_racing_ax(ax)
    _apply_driver_labels(ax, sorted_drivers, best_lap_times, hero_names=hero_names)


# ---------------------------------------------------------------------------
# Public plot functions – each returns a matplotlib Figure
# ---------------------------------------------------------------------------

def plot_violin_times(
    df: pd.DataFrame, title_suffix: str = '', max_y: float | None = None,
    lap_col: str = 'Lap', dot_size: int = 4, aspect: str | None = None,
    hero_names: list[str] | None = None
) -> Figure | None:
    """Violin + swarm plot for a single session. Returns a Figure."""
    plot_df = _prepare_plot_df(df)
    if plot_df.empty:
        return None

    sorted_drivers, best_lap_times = _get_driver_stats(plot_df)

    figsize = _get_figsize(10, 10, aspect)
    fig, ax = plt.subplots(figsize=figsize, dpi=192)

    norm = Normalize(vmin=plot_df[lap_col].min(), vmax=plot_df[lap_col].max())
    _draw_violin_on_ax(ax, plot_df, sorted_drivers, best_lap_times, lap_col, hue_norm=norm, dot_size=dot_size,
                       hero_names=hero_names)

    title = _get_plot_title(df.iloc[0], title_suffix)
    _add_header(fig, title)

    ymin = plot_config.get_ymin(plot_df['LapTimeSeconds'])
    yrange = max_y if max_y is not None else plot_config.get_yrange(
        df.iloc[0].get('League'), df.iloc[0].get('Class'), plot_df['LapTimeSeconds']
    )
    ymax = ymin + yrange
    ax.set_ylim(ymin, ymax)
    _draw_overflow_dots(ax, plot_df, sorted_drivers, ymax, lap_col=lap_col, hue_norm=norm, dot_size=dot_size)
    _draw_pace_on_ax(ax, plot_df, sorted_drivers, 'Name', ymax)

    fig.subplots_adjust(
        left=_get_left_margin_frac(fig),
        right=_get_right_margin_frac(fig),
        top=_get_top_margin_frac(fig),
        bottom=_get_bottom_margin_frac(fig)
    )

    _apply_categorical_padding(ax, len(sorted_drivers))
    _add_lap_colorbar(fig, plot_df, lap_col=lap_col)

    return fig


def plot_split_violins(
    df: pd.DataFrame, base_title: str = '', max_y: float | None = None, lap_col: str = 'Lap', aspect: str | None = None,
    split_drivers: bool | None = None, hero_names: list[str] | None = None
) -> Figure:
    """Two-panel violin plot (top half / bottom half of drivers). Returns a Figure."""
    plot_df = _prepare_plot_df(df)
    if plot_df.empty:
        raise ValueError('Cannot plot violins: no valid lap times found for this session.')

    sorted_drivers, best_lap_times = _get_driver_stats(plot_df)

    ymin = plot_config.get_ymin(plot_df['LapTimeSeconds'])
    yrange = max_y if max_y is not None else plot_config.get_yrange(
        df.iloc[0].get('League'), df.iloc[0].get('Class'), plot_df['LapTimeSeconds']
    )
    ymax = ymin + yrange

    num_drivers = len(sorted_drivers)
    if split_drivers is None:
        do_split = num_drivers > 12
    else:
        do_split = split_drivers

    if not do_split:
        top_names = sorted_drivers
        bot_names = []
        figsize = _get_figsize(10, 6, aspect)
        fig, ax_top = plt.subplots(figsize=figsize, dpi=192)
        ax_bot = None
    else:
        mid = num_drivers // 2 + num_drivers % 2
        top_names = sorted_drivers[:mid]
        bot_names = sorted_drivers[mid:]
        figsize = _get_figsize(10, 10, aspect)
        fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=figsize, dpi=192)
    _add_header(fig, _get_plot_title(df.iloc[0], base_title))

    norm = Normalize(vmin=plot_df[lap_col].min(), vmax=plot_df[lap_col].max())

    _draw_violin_on_ax(ax_top, plot_df, top_names, best_lap_times, lap_col, hue_norm=norm, hero_names=hero_names)
    ax_top.set_ylim(ymin, ymax)
    _draw_overflow_dots(
        ax_top, plot_df[plot_df['Name'].isin(top_names)], top_names, ymax,
        lap_col=lap_col, hue_norm=norm
    )
    _draw_pace_on_ax(ax_top, plot_df[plot_df['Name'].isin(top_names)], top_names, 'Name', ymax)

    if bot_names and ax_bot is not None:
        _draw_violin_on_ax(ax_bot, plot_df, bot_names, best_lap_times, lap_col, hue_norm=norm, hero_names=hero_names)
        ax_bot.set_ylim(ymin, ymax)
        _draw_overflow_dots(
            ax_bot, plot_df[plot_df['Name'].isin(bot_names)], bot_names, ymax,
            lap_col=lap_col, hue_norm=norm
        )
        _draw_pace_on_ax(ax_bot, plot_df[plot_df['Name'].isin(bot_names)], bot_names, 'Name', ymax)
    elif ax_bot is not None:
        ax_bot.set_visible(False)

    if ax_bot is None:
        fig.subplots_adjust(
            left=_get_left_margin_frac(fig),
            right=_get_right_margin_frac(fig),
            top=_get_top_margin_frac(fig),
            bottom=_get_bottom_margin_frac(fig)
        )
        _apply_categorical_padding(ax_top, len(top_names))
    else:
        fig.subplots_adjust(
            left=_get_left_margin_frac(fig),
            right=_get_right_margin_frac(fig),
            top=_get_top_margin_frac(fig, AXES_TOP_SPLIT_PX),
            bottom=_get_bottom_margin_frac(fig, 120),
            hspace=0.35
        )
        _apply_categorical_padding(ax_top, len(top_names))
        _apply_categorical_padding(ax_bot, len(bot_names))

    _add_lap_colorbar(fig, plot_df, lap_col=lap_col)
    _add_logo(fig)
    return fig


def plot_combined_progression(
    df: pd.DataFrame, lap_col: str = 'ModifiedLap', aspect: str | None = None
) -> list[Figure]:
    """Plot combined progression split into groups of 10 drivers per plot.
    Returns a list of Figures."""
    if df.empty:
        return []

    plot_df = df[(df['LapTimeSeconds'] > 0) & (df['Lap'] > 0)].copy()
    if plot_df.empty:
        return []

    plot_df['Pos_Numeric'] = pd.to_numeric(plot_df['Pos'], errors='coerce').fillna(999)

    sorted_drivers, _ = _get_driver_stats(plot_df)

    chunk_size = 10
    chunks = [sorted_drivers[i:i + chunk_size] for i in range(0, len(sorted_drivers), chunk_size)]

    figs = []
    for idx, chunk in enumerate(chunks, start=1):
        chunk_df = plot_df[plot_df['Name'].isin(chunk)]
        title_suffix = f'(Combined Progression {idx}/{len(chunks)})'
        fig = plot_violin_times(
            chunk_df,
            title_suffix=title_suffix,
            lap_col=lap_col,
            dot_size=3,
            max_y=95,
            aspect=aspect
        )
        if fig is not None:
            figs.append(fig)
    return figs


def apply_penalties(df: pd.DataFrame, penalties_df: pd.DataFrame) -> tuple[pd.DataFrame, dict[float, str]]:
    """
    Applies penalties to race data.
    Returns a tuple (DataFrame with an additional 'pen' lap, lap_offsets dict).
    """
    if df.empty:
        return df, {}

    max_lap_race = df['Lap'].max()
    finish_df = df.sort_values('Lap').groupby('Name').last().reset_index()
    finish_df['LapsDown'] = max_lap_race - finish_df['Lap']

    finish_df = finish_df.sort_values(['LapsDown', 'Interval'])
    finish_df['FinishPos'] = range(1, len(finish_df) + 1)

    if not penalties_df.empty:
        p_agg = penalties_df.groupby('Name').agg({
            'SecondsAdded': 'sum',
            'PositionsAdded': 'sum',
            'Excluded': 'any'
        }).reset_index()

        finish_df = finish_df.merge(p_agg, on='Name', how='left')

        finish_df['SecondsAdded'] = finish_df['SecondsAdded'].fillna(0.0)
        finish_df['PositionsAdded'] = finish_df['PositionsAdded'].fillna(0.0)
        finish_df['Excluded'] = finish_df['Excluded'].astype('boolean').fillna(False).astype(bool)
    else:
        finish_df['SecondsAdded'] = 0.0
        finish_df['PositionsAdded'] = 0.0
        finish_df['Excluded'] = False

    finish_df['IntermediateInterval'] = finish_df['Interval'] + finish_df['SecondsAdded']

    finish_df = finish_df.sort_values(['LapsDown', 'IntermediateInterval'])

    for idx, row in finish_df[finish_df['PositionsAdded'] > 0].iterrows():
        current_idx = finish_df.index.get_loc(idx)
        target_idx = int(current_idx + row['PositionsAdded'])

        if target_idx >= len(finish_df) - 1:
            finish_df.at[idx, 'IntermediateInterval'] = finish_df['IntermediateInterval'].max() + 1.0
        else:
            time_above = finish_df.iloc[target_idx]['IntermediateInterval']
            time_below = finish_df.iloc[target_idx + 1]['IntermediateInterval']
            finish_df.at[idx, 'IntermediateInterval'] = (time_above + time_below) / 2.0

    finish_df = finish_df.sort_values(['LapsDown', 'IntermediateInterval'])
    finish_df['FinalInterval'] = 0.0
    lap_offsets = {}

    prev_max = None
    for laps, group in finish_df.groupby('LapsDown'):
        if prev_max is None:
            finish_df.loc[group.index, 'FinalInterval'] = group['IntermediateInterval']
        else:
            # Process each driver in the group sequentially to ensure they finish after the
            # previous group and after each other, but without forcing a common large shift.
            # This prevents "phantom penalties" where a slow driver is shifted by the large
            # amount needed by the fastest driver in their group to clear the previous lap.
            first_in_group = True
            last_f_interval = prev_max

            for idx, row in group.iterrows():
                min_gap = 10.0 if first_in_group else 2.0
                actual_f_interval = max(last_f_interval + min_gap, row['IntermediateInterval'])

                finish_df.at[idx, 'FinalInterval'] = actual_f_interval

                if first_in_group:
                    # Place the lap-down label in the transition before the first driver of the group
                    lap_offsets[(prev_max + actual_f_interval) / 2.0] = f"+{int(laps)} lap{'s' if laps > 1 else ''}"
                    first_in_group = False

                last_f_interval = actual_f_interval

        prev_max = finish_df.loc[group.index, 'FinalInterval'].max()

    if finish_df['Excluded'].any():
        max_not_excl = finish_df.loc[~finish_df['Excluded'], 'FinalInterval'].max()
        if pd.isna(max_not_excl):
            max_not_excl = 1.0
        excl_indices = finish_df[finish_df['Excluded']].index
        for i, idx in enumerate(excl_indices):
            finish_df.at[idx, 'FinalInterval'] = max_not_excl + 5.0 + (i * 2.0)

    pen_rows = finish_df.copy()
    pen_rows['Lap'] = max_lap_race + 1
    pen_rows['Interval'] = pen_rows['FinalInterval']
    pen_rows['LapDisplay'] = 'pen'

    return pd.concat([df, pen_rows], ignore_index=True), lap_offsets


def _draw_gap_on_ax(
    ax: Axes, df: pd.DataFrame, penalties_df: pd.DataFrame | None = None,
    max_y: float | None = None, driver_colors: dict[str, str | tuple[float, ...]] | None = None,
    hero_names: list[str] | None = None
) -> float:
    """Internal helper to draw a gap plot on a specific axis."""
    if driver_colors is None:
        driver_colors = {}

    if penalties_df is None:
        penalties_df = pd.DataFrame()

    plot_df = df[df['LapTimeSeconds'].notna()].copy()
    if plot_df.empty:
        raise ValueError('No valid lap times found in session data to draw the gap plot.')

    if 'Name' in plot_df.columns:
        plot_df['Name'] = plot_df['Name'].fillna('').astype(str)

    plot_df = plot_df.sort_values(['Name', 'Lap'])
    plot_df['CumTime'] = plot_df.groupby('Name')['LapTimeSeconds'].cumsum()

    leader_times = plot_df.groupby('Lap')['CumTime'].min().reset_index().rename(columns={'CumTime': 'LeaderCumTime'})
    plot_df = plot_df.merge(leader_times, on='Lap')
    plot_df['Interval'] = plot_df['CumTime'] - plot_df['LeaderCumTime']

    max_lap_actual = plot_df['Lap'].max()
    min_lap = plot_df['Lap'].min()
    plot_df, lap_offsets = apply_penalties(plot_df, penalties_df)

    # Ensure we only iterate over drivers present in the current dataframe.
    # We use alphabetical order for consistent z-order and palette index usage.
    sorted_drivers = sorted(plot_df['Name'].unique())

    for driver in sorted_drivers:
        driver_df = plot_df[plot_df['Name'] == driver].sort_values('Lap')
        if driver_df.empty:
            continue

        is_hero = _is_hero(driver, hero_names)
        color = driver_colors.get(driver, 'white')  # Fallback just in case

        normal_laps = driver_df[driver_df['Lap'] <= max_lap_actual]
        ax.plot(normal_laps['Lap'], normal_laps['Interval'],
                color=color, linewidth=3.5 if is_hero else 1.8,
                alpha=1.0 if is_hero else 0.8, zorder=100 if is_hero else 10)

        pen_lap = driver_df[driver_df['LapDisplay'] == 'pen']
        if not pen_lap.empty:
            last_normal = normal_laps.iloc[-1]
            pen_data = pen_lap.iloc[0]
            ax.plot([last_normal['Lap'], pen_data['Lap']], [last_normal['Interval'], pen_data['Interval']],
                    color=color, linewidth=3.5 if is_hero else 1.8,
                    alpha=1.0 if is_hero else 0.8, linestyle='--', zorder=100 if is_hero else 10)

    ax.invert_yaxis()

    # Ensure zero point is at a fixed distance in pixels from the top.
    # When max_y is specified or changed, the pixel distance can shift if we
    # only keep the absolute 'seconds' of padding.
    target_padding_px = 60
    fig = ax.figure
    # get_figheight is present on both Figure and SubFigure in Matplotlib 3.4+
    fig_h_px = fig.get_figheight() * fig.dpi  # type: ignore[union-attr]
    ax_h_px = ax.get_position().height * fig_h_px

    ymin, ymax = ax.get_ylim()
    target_bottom = max_y if max_y is not None else ymin

    if ax_h_px > target_padding_px:
        # y_top such that (0 - y_top) / (target_bottom - y_top) = target_padding_px / ax_h_px
        y_top = (target_padding_px * target_bottom) / (target_padding_px - ax_h_px)
        ax.set_ylim(target_bottom, y_top)
    elif max_y is not None:
        ax.set_ylim(max_y, ymax)

    ax.set_xlabel('')
    ax.text(-0.02, -0.0198, 'Lap:', transform=ax.transAxes,
            ha='right', va='center', fontsize=12, fontweight='bold')
    ax.set_ylabel('Gap from the Leader (seconds)', fontsize=12)

    ticks = list(range(int(min_lap), int(max_lap_actual) + 1))
    labels = [str(t) for t in ticks]
    ticks.append(max_lap_actual + 1)
    labels.append('pen')
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels)

    ax.set_xlim(min_lap, max_lap_actual + 1)
    ax.yaxis.set_major_formatter(_formatter)
    ax.yaxis.set_major_locator(MultipleLocator(5))
    ax.yaxis.set_minor_locator(MultipleLocator(1))
    ax.grid(True, which='major', axis='y', color='gray', linestyle='-', alpha=0.4)
    ax.grid(True, which='minor', axis='y', color='gray', linestyle=':', alpha=0.4)
    ax.grid(True, which='major', axis='x', alpha=0.3, linestyle='--')

    label_margin = 0.12
    label_x = max_lap_actual + 1 + label_margin
    labels_data = []

    for driver in sorted_drivers:
        driver_df = plot_df[plot_df['Name'] == driver]
        pen_rows = driver_df[driver_df['LapDisplay'] == 'pen']
        if pen_rows.empty:
            continue

        row = pen_rows.iloc[0]
        labels_data.append({
            'y_actual': row['Interval'],
            'y_display': row['Interval'],
            'name': driver,
            'color': driver_colors[driver],
            'is_hero': _is_hero(driver, hero_names),
            'is_excluded': row.get('Excluded', False)
        })

    if labels_data:
        labels_data.sort(key=lambda x: x['y_actual'])
        min_dist = 0.6
        for i in range(1, len(labels_data)):
            if labels_data[i]['y_display'] < labels_data[i - 1]['y_display'] + min_dist:
                labels_data[i]['y_display'] = labels_data[i - 1]['y_display'] + min_dist

    # Calculate how much space names on the right need
    max_label_len = 0
    y_min_bound, y_max_bound = ax.get_ylim()
    # Inverted y-axis, so y_min_bound is the larger value (bottom)
    for ld in labels_data:
        if ld['y_display'] > max(y_min_bound, y_max_bound):
            continue
        max_label_len = max(max_label_len, len(ld['name']))

    for y_pos, label in lap_offsets.items():
        if y_pos > max(y_min_bound, y_max_bound):
            continue
        max_label_len = max(max_label_len, len(label))

    right_margin_needed_px = int(25 + (max_label_len * 7.5))
    right_adjust = _get_right_margin_frac(ax.figure, right_margin_needed_px)

    # ACTUAL DRAWING of the labels
    for ld in labels_data:
        if ld['y_display'] > max(y_min_bound, y_max_bound):
            continue

        ax.text(label_x, ld['y_display'], ld['name'],
                color=ld['color'], ha='left', va='center', fontsize=8,
                fontweight=('bold' if ld['is_hero'] else 'normal'),
                clip_on=False)

        if ld['is_excluded']:
            ax.plot(max_lap_actual + 1, ld['y_actual'], 'x', color=ld['color'],
                    markersize=8, mew=2, zorder=200, clip_on=False)

    for y_pos, label in lap_offsets.items():
        if y_pos > max(y_min_bound, y_max_bound):
            continue

        ax.text(label_x, y_pos, label, color='gray', fontsize=8,
                ha='left', va='center', fontweight='bold', style='italic',
                bbox=dict(facecolor='black', alpha=0.8, edgecolor='none', pad=2),
                clip_on=False)

    return right_adjust


def gap_plot(
    df: pd.DataFrame, penalties_df: pd.DataFrame | None = None, title_suffix: str = '',
    max_y: float | None = None, aspect: str | None = None,
    driver_colors: dict[str, str | tuple[float, ...]] | None = None,
    hero_names: list[str] | None = None
) -> Figure:
    """Plot interval from leader over laps for a race. Returns a Figure."""
    figsize = _get_figsize(10, 10, aspect)
    fig, ax = plt.subplots(figsize=figsize, dpi=192)

    if driver_colors is None:
        driver_colors = get_driver_colors([df], hero_names=hero_names)

    right_adjust = _draw_gap_on_ax(ax, df, penalties_df=penalties_df, max_y=max_y, driver_colors=driver_colors,
                                   hero_names=hero_names)

    _add_header(fig, _get_plot_title(df.iloc[0], title_suffix))
    fig.subplots_adjust(
        left=_get_left_margin_frac(fig),
        right=right_adjust,
        top=_get_top_margin_frac(fig),
        bottom=_get_bottom_margin_frac(fig, 105)
    )

    return fig


def plot_split_violins_by_date(
    df: pd.DataFrame, base_title: str = '', max_y: float | None = None, lap_col: str = 'Lap', aspect: str | None = None,
    hero_names: list[str] | None = None
) -> Figure:
    """Violin + swarm plot for sessions at a track, grouped by date on the x-axis.

    Instead of showing each driver as a separate violin, this function aggregates
    all lap times from one date into a single violin. The swarm dots are colored
    by lap number to visualise progression. Returns a Figure.
    """
    plot_df = _prepare_plot_df(df)
    if plot_df.empty:
        raise ValueError('Cannot plot violins by date: no valid lap times found in provided data.')

    # Get sorted unique dates
    sorted_dates = sorted(plot_df['Date'].unique())
    if not sorted_dates:
        raise ValueError('Cannot plot violins by date: no unique dates found in dataset.')

    # Best lap time per date (for label on x-axis)
    best_per_date = plot_df.groupby('Date')['LapTimeSeconds'].min()

    # Global range for consistency
    sample = plot_df.sort_values('Date').iloc[0]
    ymin = plot_config.get_ymin(plot_df['LapTimeSeconds'])
    yrange = max_y if max_y is not None else plot_config.get_yrange(
        sample.get('League'), sample.get('Class'), plot_df['LapTimeSeconds']
    )
    ymax = ymin + yrange

    num_dates = len(sorted_dates)
    if num_dates < 11:
        top_dates = sorted_dates
        bot_dates = []
        figsize = _get_figsize(10, 6, aspect)
        fig, ax_top = plt.subplots(figsize=figsize, dpi=192)
        ax_bot = None
    else:
        mid = num_dates // 2 + num_dates % 2
        top_dates = sorted_dates[:mid]
        bot_dates = sorted_dates[mid:]
        figsize = _get_figsize(10, 10, aspect)
        fig, (ax_top, ax_bot) = plt.subplots(2, 1, figsize=figsize, dpi=192)

    title = _get_plot_title(sample, f'(by Date {base_title})')
    _add_header(fig, title)

    norm = Normalize(vmin=plot_df[lap_col].min(), vmax=plot_df[lap_col].max())

    def _draw_date_violins(
        ax: Axes, dates: list[str], plot_df: pd.DataFrame, lap_col: str,
        hue_norm: Normalize | None = None
    ) -> None:
        subset = plot_df[plot_df['Date'].isin(dates)].copy()

        sns.violinplot(
            data=subset, x='Date', y='LapTimeSeconds', order=dates,
            color='#007acc', linewidth=0, width=0.8, inner=None, ax=ax,
            density_norm='width'
        )
        sns.swarmplot(
            data=subset, x='Date', y='LapTimeSeconds', order=dates,
            hue=lap_col, palette='spring', hue_norm=hue_norm,
            size=4, ax=ax, legend=False,
            warn_thresh=1.0,
        )

        _style_racing_ax(ax)
        # Format x-axis labels with best lap time
        labels = [f'{_format_time_full(best_per_date[d])}\n{d}' for d in dates]
        ax.set_xticks(range(len(dates)))
        rot = 15 if len(dates) <= 8 else 25
        ax.set_xticklabels(labels, rotation=rot, ha='right', fontsize=10)
        ax.set_xlabel('')

        dx, dy = 18, 8
        offset = ScaledTranslation(dx / 72, dy / 72, ax.figure.dpi_scale_trans)
        for label in ax.get_xticklabels():
            label.set_transform(label.get_transform() + offset)

    _draw_date_violins(ax_top, top_dates, plot_df, lap_col, hue_norm=norm)
    ax_top.set_ylim(ymin, ymax)
    _draw_overflow_dots_by_date(
        ax_top, plot_df[plot_df['Date'].isin(top_dates)], top_dates, ymax,
        lap_col=lap_col, hue_norm=norm
    )
    _draw_pace_on_ax(ax_top, plot_df[plot_df['Date'].isin(top_dates)], top_dates, 'Date', ymax)

    if bot_dates and ax_bot is not None:
        _draw_date_violins(ax_bot, bot_dates, plot_df, lap_col, hue_norm=norm)
        ax_bot.set_ylim(ymin, ymax)
        _draw_overflow_dots_by_date(
            ax_bot, plot_df[plot_df['Date'].isin(bot_dates)], bot_dates, ymax,
            lap_col=lap_col, hue_norm=norm
        )
        _draw_pace_on_ax(ax_bot, plot_df[plot_df['Date'].isin(bot_dates)], bot_dates, 'Date', ymax)
    elif ax_bot is not None:
        ax_bot.set_visible(False)

    if ax_bot is None:
        fig.subplots_adjust(
            left=_get_left_margin_frac(fig),
            right=_get_right_margin_frac(fig),
            top=_get_top_margin_frac(fig),
            bottom=_get_bottom_margin_frac(fig)
        )
        _apply_categorical_padding(ax_top, len(top_dates))
    else:
        fig.subplots_adjust(
            left=_get_left_margin_frac(fig),
            right=_get_right_margin_frac(fig),
            top=_get_top_margin_frac(fig, AXES_TOP_SPLIT_PX),
            bottom=_get_bottom_margin_frac(fig, 160),
            hspace=0.35
        )
        _apply_categorical_padding(ax_top, len(top_dates))
        _apply_categorical_padding(ax_bot, len(bot_dates))

    _add_lap_colorbar(fig, plot_df, lap_col=lap_col)

    return fig


def _draw_overflow_dots_by_date(
    ax: Axes, plot_df: pd.DataFrame, sorted_dates: list[str], ymax: float,
    lap_col: str = 'Lap', hue_norm: Normalize | None = None, dot_size: int = 4
) -> None:
    """Draw markers just above ymax for any LapTimeSeconds values that exceed it, grouped by Date."""
    overflow = plot_df[plot_df['LapTimeSeconds'] > ymax].copy()
    if overflow.empty:
        return

    ymin_cur, _ = ax.get_ylim()
    overflow_y = ymax + (ymax - ymin_cur) * 0.02

    overflow = overflow.copy()
    overflow['LapTimeSeconds'] = overflow_y

    ax.set_ylim(ymin_cur, overflow_y + (ymax - ymin_cur) * 0.02)

    existing_collections = set(id(c) for c in ax.collections)

    sns.swarmplot(
        data=overflow, x='Date', y='LapTimeSeconds', order=sorted_dates,
        hue=lap_col, palette='spring', hue_norm=hue_norm,
        size=dot_size, ax=ax, legend=False,
        warn_thresh=1.0,
    )

    ax.set_ylim(ymin_cur, ymax)

    for collection in ax.collections:
        if id(collection) not in existing_collections:
            collection.set_clip_on(False)

    ax.set_xlabel('')


def analyze_meeting(
    df_meeting: pd.DataFrame, df_penalties: pd.DataFrame | None = None, aspect: str | None = None
) -> list[tuple[str, Figure]]:
    """Run the full meeting analysis and return a list of (title, Figure) tuples."""
    if df_penalties is None:
        df_penalties = pd.DataFrame()

    if df_meeting.empty:
        return []

    results = []

    races = df_meeting.sort_values('DateTime')['SessionName'].unique()

    for race_name in races:
        race_df = df_meeting[df_meeting['SessionName'] == race_name]

        fig = plot_split_violins(race_df, aspect=aspect)
        if fig is not None:
            results.append((str(race_name), fig))

        race_name_lower = str(race_name).lower()
        if 'final' in race_name_lower or 'heat' in race_name_lower:
            race_penalties = (
                df_penalties[df_penalties['SessionName'] == race_name]
                if not df_penalties.empty else pd.DataFrame()
            )
            fig = gap_plot(race_df, penalties_df=race_penalties, aspect=aspect)
            if fig is not None:
                results.append((f'{race_name} – Gap', fig))

    return results


def gap_plot_multisession(
    dfs: list[pd.DataFrame], penalties_dfs: Sequence[pd.DataFrame | None] | None = None,
    max_y: float | None = None, aspect: str | None = None,
    driver_colors: dict[str, str | tuple[float, ...]] | None = None,
    hero_names: list[str] | None = None
) -> Figure:
    """Multiple rows of gap plots, one per session.
    dfs: list of DataFrames, one per session.
    penalties_dfs: list of DataFrames or None, one per session.
    """
    if not dfs:
        raise ValueError('No session dataframes provided to generate a multi-session gap plot.')

    num_sessions = len(dfs)
    if penalties_dfs is None:
        penalties_dfs = [None] * num_sessions

    # Calculate figure size: 10 width, ~6 height per session
    fig_height = 6 * num_sessions + 1
    figsize = _get_figsize(10, fig_height, aspect)
    fig, axes = plt.subplots(num_sessions, 1, figsize=figsize, dpi=192, squeeze=False)

    if driver_colors is None:
        driver_colors = get_driver_colors(dfs, hero_names=hero_names)

    min_right_adjust = 0.96

    session_names = []
    for i, (df, p_df) in enumerate(zip(dfs, penalties_dfs)):
        ax = axes[i, 0]
        right_adjust = _draw_gap_on_ax(ax, df, penalties_df=p_df, max_y=max_y, driver_colors=driver_colors,
                                       hero_names=hero_names)
        min_right_adjust = min(min_right_adjust, right_adjust)

        sample = df.iloc[0]
        row_title = sample.get('SessionName', 'Session')
        session_names.append(row_title)

    # Calculate appropriate titles and spacing
    sample = dfs[0].iloc[0]
    header_title = _get_plot_title(sample, session_names=session_names)
    _add_header(fig, header_title, is_social=False)
    top_margin_v = _get_top_margin_frac(fig, AXES_TOP_PX if num_sessions == 1 else AXES_TOP_SPLIT_PX)
    fig.subplots_adjust(
        left=_get_left_margin_frac(fig),
        right=min_right_adjust,
        top=top_margin_v,
        bottom=_get_bottom_margin_frac(fig, 75),
        hspace=0.2
    )

    return fig


def plot_violins_multisession(
    df: pd.DataFrame, session_indices: list[int] | None = None, max_y: float | None = None, aspect: str | None = None,
    split_drivers: bool | None = None, hero_names: list[str] | None = None,
) -> Figure | None:
    """Multiple rows of violin plots, one per session. Each row has ALL drivers.
    If session_indices is a list of integers, only those sessions (0-indexed) are plotted.
    """
    if df.empty:
        raise ValueError('Cannot generate multi-session violin plot: provided dataframe is empty.')

    plot_df = _prepare_plot_df(df)
    if plot_df.empty:
        return None

    # Get unique sessions sorted by DateTime
    sessions_metadata = plot_df.sort_values('DateTime').groupby('SessionID', sort=False).first().reset_index()

    if session_indices is not None:
        # Filter sessions by index, ensuring they are within bounds
        valid_indices = [i for i in session_indices if 0 <= i < len(sessions_metadata)]
        if not valid_indices:
            raise ValueError(f'None of the provided session indices {session_indices} were found in this meeting.')
        sessions_metadata = sessions_metadata.iloc[valid_indices]

    session_ids = sessions_metadata['SessionID'].tolist()
    num_sessions = len(session_ids)

    relevant_df = plot_df[plot_df['SessionID'].isin(session_ids)]
    if split_drivers is None:
        # Auto-split if field is large, but only for single/double session plots
        # to avoid cluttering larger summaries.
        split_drivers = (relevant_df['Name'].nunique() > 12) and (num_sessions <= 2)

    # Calculate figure size: 10 width, ~4 height per session (or more if split)
    rows_per_session = 2 if split_drivers else 1
    fig_height = 4 * num_sessions * rows_per_session + 1
    figsize = _get_figsize(10, fig_height, aspect)
    fig, axes = plt.subplots(num_sessions * rows_per_session, 1, figsize=figsize, dpi=192, squeeze=False)

    # Global range for consistency across sessions
    sample = sessions_metadata.iloc[0]
    ymin = plot_config.get_ymin(relevant_df['LapTimeSeconds'])
    yrange = max_y if max_y is not None else plot_config.get_yrange(
        sample.get('League'), sample.get('Class'), relevant_df['LapTimeSeconds']
    )
    ymax = ymin + yrange

    for i, session_id in enumerate(session_ids):
        session_df = plot_df[plot_df['SessionID'] == session_id]

        # Get sorted drivers for this session
        sorted_drivers, best_lap_times = _get_driver_stats(session_df)

        if split_drivers:
            mid = len(sorted_drivers) // 2 + len(sorted_drivers) % 2
            driver_groups = [sorted_drivers[:mid], sorted_drivers[mid:]]
            ax_indices = [i * 2, i * 2 + 1]
        else:
            driver_groups = [sorted_drivers]
            ax_indices = [i]

        for group_idx, drivers in enumerate(driver_groups):
            ax = axes[ax_indices[group_idx], 0]
            if not drivers:
                ax.set_visible(False)
                continue

            # Draw on axis
            norm = Normalize(vmin=session_df['Lap'].min(), vmax=session_df['Lap'].max())
            _draw_violin_on_ax(ax, session_df, drivers, best_lap_times, 'Lap', hue_norm=norm, hero_names=hero_names)
            _apply_categorical_padding(ax, len(drivers))

            ax.set_ylim(ymin, ymax)
            _draw_overflow_dots(
                ax, session_df[session_df['Name'].isin(drivers)], drivers, ymax,
                lap_col='Lap', hue_norm=norm
            )
            _draw_pace_on_ax(
                ax, session_df[session_df['Name'].isin(drivers)], drivers, 'Name', ymax
            )

            # Session Label (Session Name) - only on top axis if split
            if group_idx == 0:
                row_title = sessions_metadata.iloc[i]['SessionName']
                dt = sessions_metadata.iloc[i].get('DateTime', '')
                if dt and isinstance(dt, str) and ' ' in dt:
                    time_part = dt.split(' ')[1]
                    row_title = f'{time_part} {row_title}'

                ax.text(0.01, 0.98, row_title, transform=ax.transAxes,
                        va='top', ha='left', fontsize=12, color='white',
                        fontweight='bold', bbox=dict(facecolor='black', alpha=0.4, edgecolor='none', pad=2))

            # Add separate "laps" legend for this subplot
            if not split_drivers or group_idx == 1:
                _add_lap_colorbar(fig, session_df, ax=ax, color='white')

        _apply_categorical_padding(ax, len(drivers))

    # Main title & Header
    session_names = sessions_metadata['SessionName'].tolist()
    header_title = _get_plot_title(sample, session_names=session_names)
    _add_header(fig, header_title, is_social=False)

    top_margin_v = _get_top_margin_frac(fig, AXES_TOP_PX if num_sessions == 1 else AXES_TOP_SPLIT_PX)
    fig.subplots_adjust(
        left=_get_left_margin_frac(fig),
        right=_get_right_margin_frac(fig),
        top=top_margin_v,
        bottom=_get_bottom_margin_frac(fig, 75),
        hspace=0.25
    )

    return fig


def plot_violin_gap_combined(
    violin_dfs: list[pd.DataFrame],
    gap_dfs: list[pd.DataFrame],
    penalties_dfs: Sequence[pd.DataFrame | None] | None = None,
    max_y: float | None = None,
    aspect: str | None = None,
    driver_colors: dict[str, str | tuple[float, ...]] | None = None,
    hero_names: list[str] | None = None
) -> Figure:
    """Combines violin(s) and gap plot(s) into one vertical stack.
    violin_dfs: list of DataFrames for violins.
    gap_dfs: list of DataFrames for gap plots.
    penalties_dfs: list of DataFrames or None, one per gap_df.
    """
    num_v = len(violin_dfs)
    num_g = len(gap_dfs)
    num_total = num_v + num_g

    if num_total == 0:
        raise ValueError('No data provided for combined plot.')

    if penalties_dfs is None:
        penalties_dfs = [None] * num_g

    # Height ratios: Gap plot on top and uses more space than violin plot, but less than before.
    hratios = [2] * num_g + [1.2] * num_v
    # Height: ~8 per gap, ~5 per violin.
    fig_height = 8 * num_g + 5 * num_v + 1
    figsize = _get_figsize(10, fig_height, aspect)
    fig, axes = plt.subplots(num_total, 1, figsize=figsize, dpi=192, squeeze=False,
                             gridspec_kw={'height_ratios': hratios})

    # Pre-calculate consistent colors for all gap plots if not provided
    if driver_colors is None:
        driver_colors = get_driver_colors(gap_dfs, hero_names=hero_names)

    min_right_adjust = 0.97

    # 1. Draw Gaps (TOP)
    for i in range(num_g):
        ax = axes[i, 0]
        right_adjust = _draw_gap_on_ax(
            ax, gap_dfs[i], penalties_df=penalties_dfs[i],
            max_y=max_y, driver_colors=driver_colors,
            hero_names=hero_names
        )
        min_right_adjust = min(min_right_adjust, right_adjust)

        # Session Label for gap (only if more than one unique session exists)
        unique_sessions = set(df.iloc[0].get('SessionID') for df in gap_dfs + violin_dfs if not df.empty)
        if len(unique_sessions) > 1:
            sample = gap_dfs[i].iloc[0]
            row_title = sample.get('SessionName', 'Session')
            dt = sample.get('DateTime', '')
            if dt and isinstance(dt, str) and ' ' in dt:
                time_part = dt.split(' ')[1]
                row_title = f'{time_part} {row_title}'

            ax.text(0.01, 0.98, row_title, transform=ax.transAxes,
                    va='top', ha='left', fontsize=12, color='white',
                    fontweight='bold', bbox=dict(facecolor='black', alpha=0.4, edgecolor='none', pad=2))

    # 2. Draw Violins (BOTTOM)
    # Global range for consistency across ALL violins
    all_violin_data = pd.concat(violin_dfs) if violin_dfs else pd.DataFrame()
    if not all_violin_data.empty:
        plot_df_all = _prepare_plot_df(all_violin_data)
        ymin = plot_config.get_ymin(plot_df_all['LapTimeSeconds'])
        yrange = plot_config.get_yrange(
            all_violin_data.iloc[0].get('League'),
            all_violin_data.iloc[0].get('Class'),
            plot_df_all['LapTimeSeconds']
        )
        ymax = ymin + yrange
    else:
        ymin, ymax = 0, 100

    violin_axes = []
    for i in range(num_v):
        ax = axes[num_g + i, 0]
        violin_axes.append(ax)
        v_df = _prepare_plot_df(violin_dfs[i])
        sorted_drivers, best_lap_times = _get_driver_stats(v_df)

        norm = Normalize(vmin=plot_df_all['Lap'].min(), vmax=plot_df_all['Lap'].max())
        _draw_violin_on_ax(ax, v_df, sorted_drivers, best_lap_times, 'Lap', hue_norm=norm, hero_names=hero_names)

        ax.set_ylim(ymin, ymax)
        _draw_overflow_dots(ax, v_df, sorted_drivers, ymax, lap_col='Lap', hue_norm=norm)
        _draw_pace_on_ax(ax, v_df, sorted_drivers, 'Name', ymax)

        # Session Label (only if more than one unique session exists)
        unique_sessions = set(df.iloc[0].get('SessionID') for df in gap_dfs + violin_dfs if not df.empty)
        if len(unique_sessions) > 1:
            sample = violin_dfs[i].iloc[0]
            row_title = sample.get('SessionName', 'Session')
            dt = sample.get('DateTime', '')
            if dt and isinstance(dt, str) and ' ' in dt:
                time_part = dt.split(' ')[1]
                row_title = f'{time_part} {row_title}'

            ax.text(0.01, 0.98, row_title, transform=ax.transAxes,
                    va='top', ha='left', fontsize=12, color='white',
                    fontweight='bold', bbox=dict(facecolor='black', alpha=0.4, edgecolor='none', pad=2))

    # Header & Spacing
    session_names = [df.iloc[0].get('SessionName', 'Session') for df in gap_dfs + violin_dfs]
    sample = (gap_dfs[0] if gap_dfs else violin_dfs[0]).iloc[0]
    header_title = _get_plot_title(sample, session_names=session_names)
    _add_header(fig, header_title, is_social=False)

    top_margin_v = _get_top_margin_frac(fig, AXES_TOP_PX if num_total == 1 else AXES_TOP_SPLIT_PX)
    standard_right = _get_right_margin_frac(fig)
    fig.subplots_adjust(
        left=_get_left_margin_frac(fig),
        right=standard_right,
        top=top_margin_v,
        bottom=_get_bottom_margin_frac(fig, 100),
        hspace=0.1
    )

    # Narrow ONLY the gap axes to make room for names on the right.
    # Violins use the full standard width as they don't have labels on the right.
    if num_g > 0:
        for i in range(num_g):
            ax = axes[i, 0]
            pos = ax.get_position()
            ax.set_position([pos.x0, pos.y0, min_right_adjust - pos.x0, pos.height])

    for ax in violin_axes:
        idx = violin_axes.index(ax)
        v_df = _prepare_plot_df(violin_dfs[idx])
        _apply_categorical_padding(
            ax, len(v_df['Name'].unique()),
            margin_right_px=AXES_RIGHT_PX
        )

    # Add Lap colorbar at the bottom if we have violins
    if violin_axes and not all_violin_data.empty:
        _add_lap_colorbar(fig, plot_df_all, lap_col='Lap', color='white')

    return fig


def plot_driver_violins(
    df: pd.DataFrame, driver_name: str, max_y: float | None = None, aspect: str | None = None,
    title_suffix: str = '', axis_style: Callable[[Axes], None] | None = None,
    hero_names: list[str] | None = None
) -> Figure:
    """Violin plot of lap times for a specific driver, with sessions on the x-axis."""
    plot_df = _prepare_plot_df(df)
    plot_df = plot_df[plot_df['Name'] == driver_name].copy()
    if plot_df.empty:
        raise ValueError(f'No valid lap times found for driver {driver_name}')

    # Sort by date/time to ensure chronological order
    plot_df['DateTime'] = pd.to_datetime(plot_df['DateTime'])
    plot_df = plot_df.sort_values('DateTime')

    # Create session labels and find unique ones in order
    plot_df['SessionLabel'] = plot_df['Date'] + '\n' + plot_df['SessionName']
    session_labels = []
    for label in plot_df['SessionLabel']:
        if label not in session_labels:
            session_labels.append(label)

    # Prepare stats for best lap per session (as labels/colors)
    best_lap_times = plot_df.groupby('SessionLabel')['LapTimeSeconds'].min()

    # Re-map columns so we can reuse _draw_violin_on_ax
    plot_df['Name'] = plot_df['SessionLabel']

    # Figure height is fixed, width grows with number of sessions
    num_sessions = len(session_labels)
    # Give it a bit more space per session if we want it to be wide
    width = max(10, num_sessions * 1.5)
    figsize = _get_figsize(width, 6, aspect)
    fig, ax = plt.subplots(figsize=figsize, dpi=192)

    norm = Normalize(vmin=plot_df['Lap'].min(), vmax=plot_df['Lap'].max())
    _draw_violin_on_ax(ax, plot_df, session_labels, best_lap_times, 'Lap',
                       hue_norm=norm, axis_style=axis_style, hero_names=hero_names)

    # Title and header
    _add_header(fig, f'{driver_name}{title_suffix}')

    # Y-axis range
    ymin = (plot_df['LapTimeSeconds'].min() - 0.5) if axis_style else plot_config.get_ymin(plot_df['LapTimeSeconds'])
    yrange = max_y if max_y is not None else plot_config.get_yrange(
        df.iloc[0].get('League'), df.iloc[0].get('Class'), plot_df['LapTimeSeconds']
    )
    ymax = ymin + yrange
    ax.set_ylim(ymin, ymax)
    _draw_overflow_dots(ax, plot_df, session_labels, ymax, hue_norm=norm)
    _draw_pace_on_ax(ax, plot_df, session_labels, 'Name', ymax)

    fig.subplots_adjust(
        left=_get_left_margin_frac(fig),
        right=_get_right_margin_frac(fig),
        top=_get_top_margin_frac(fig),
        bottom=_get_bottom_margin_frac(fig, 100)
    )

    _apply_categorical_padding(ax, num_sessions)
    _add_lap_colorbar(fig, plot_df)

    return fig


def fig_to_png_bytes(fig: Figure) -> bytes:
    """Render a Figure to a PNG byte string and close the figure."""
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=fig.dpi)
    plt.close(fig)
    buf.seek(0)
    return buf.read()
