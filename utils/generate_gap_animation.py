#!/usr/bin/env python3

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
generate_gap_animation.py - CLI utility to generate animated gap plot frames for a karting session.

Example usage:
    python utils/generate_gap_animation.py --session \
        fat_regional/cadet/2026-06-13/Rissington+Kart+Club/17_08_race_5_cadet_final \
        --output-dir frames --aspect 16:9 --yrange 40.0
"""

import os
import sys
import argparse
import urllib.parse
import matplotlib.path
import copy
from typing import Any

# Add the parent directory to sys.path so we can import project modules (database, plots, etc.)
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

import pandas as pd  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import MultipleLocator  # noqa: E402

# Import application components
import database  # noqa: E402
from plots import (  # noqa: E402
    _is_hero,
    _formatter,
    apply_penalties,
    get_driver_colors,
    _get_plot_title,
    _add_header,
    _get_left_margin_frac,
    _get_right_margin_frac,
    _get_top_margin_frac,
    _get_bottom_margin_frac,
    _get_figsize
)


# Monkeypatch matplotlib Path deepcopy to avoid Python 3.14 recursion bug
def safe_path_deepcopy(self: matplotlib.path.Path, memo: Any = None) -> matplotlib.path.Path:
    if memo is None:
        memo = {}
    if id(self) in memo:
        return memo[id(self)]
    new_vertices = copy.deepcopy(self._vertices, memo)  # type: ignore
    new_codes = copy.deepcopy(self._codes, memo) if self._codes is not None else None  # type: ignore
    p = matplotlib.path.Path._fast_from_codes_and_verts(  # type: ignore
        new_vertices, new_codes, internals_from=self
    )
    p._readonly = False
    memo[id(self)] = p
    return p


setattr(matplotlib.path.Path, '__deepcopy__', safe_path_deepcopy)
setattr(matplotlib.path.Path, 'deepcopy', safe_path_deepcopy)


def _draw_gap_on_ax_animated(
    ax: Any, df: pd.DataFrame, penalties_df: Any = None, max_y: Any = None, driver_colors: Any = None,
    hero_names: Any = None, N: int = 0, dim_alpha: float = 0.15, bright_alpha: float = 1.0,
    xlim: Any = None, ylim: Any = None, always_bright_labels: bool = False
) -> float:
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

    sorted_drivers = sorted(plot_df['Name'].unique())
    total_segments = max_lap_actual + 1 - min_lap

    # 1. Plot background (dim) lines
    for driver in sorted_drivers:
        driver_df = plot_df[plot_df['Name'] == driver].sort_values('Lap')
        if driver_df.empty:
            continue

        is_hero = _is_hero(driver, hero_names)
        color = driver_colors.get(driver, 'white')

        normal_laps = driver_df[driver_df['Lap'] <= max_lap_actual]
        ax.plot(normal_laps['Lap'], normal_laps['Interval'],
                color=color, linewidth=3.5 if is_hero else 1.8,
                alpha=dim_alpha, zorder=10)

        pen_lap = driver_df[driver_df['LapDisplay'] == 'pen']
        if not pen_lap.empty:
            last_normal = normal_laps.iloc[-1]
            pen_data = pen_lap.iloc[0]
            ax.plot([last_normal['Lap'], pen_data['Lap']], [last_normal['Interval'], pen_data['Interval']],
                    color=color, linewidth=3.5 if is_hero else 1.8,
                    alpha=dim_alpha, linestyle='--', zorder=10)

    # 2. Plot foreground (bright) lines
    if N > 0:
        for driver in sorted_drivers:
            driver_df = plot_df[plot_df['Name'] == driver].sort_values('Lap')
            if driver_df.empty:
                continue

            is_hero = _is_hero(driver, hero_names)
            color = driver_colors.get(driver, 'white')

            normal_laps = driver_df[driver_df['Lap'] <= max_lap_actual]
            fg_normal_laps = normal_laps[normal_laps['Lap'] <= min_lap + N]
            if not fg_normal_laps.empty:
                ax.plot(fg_normal_laps['Lap'], fg_normal_laps['Interval'],
                        color=color, linewidth=3.5 if is_hero else 1.8,
                        alpha=bright_alpha, zorder=100 if is_hero else 50)

            # Highlight penalty segment if N >= total_segments
            if N >= total_segments:
                pen_lap = driver_df[driver_df['LapDisplay'] == 'pen']
                if not pen_lap.empty:
                    last_normal = normal_laps.iloc[-1]
                    pen_data = pen_lap.iloc[0]
                    ax.plot([last_normal['Lap'], pen_data['Lap']], [last_normal['Interval'], pen_data['Interval']],
                            color=color, linewidth=3.5 if is_hero else 1.8,
                            alpha=bright_alpha, linestyle='--', zorder=100 if is_hero else 50)

    ax.invert_yaxis()

    # 3. Set y-axis and x-axis limits (using global pre-calculated ones if available)
    if ylim is not None:
        ax.set_ylim(ylim)
    else:
        target_padding_px = 60
        fig = ax.figure
        fig_h_px = fig.get_figheight() * fig.dpi
        ax_h_px = ax.get_position().height * fig_h_px

        ymin_val, ymax_val = ax.get_ylim()
        target_bottom = max_y if max_y is not None else ymin_val

        if ax_h_px > target_padding_px:
            y_top = (target_padding_px * target_bottom) / (target_padding_px - ax_h_px)
            ax.set_ylim(target_bottom, y_top)
        elif max_y is not None:
            ax.set_ylim(max_y, ymax_val)

    if xlim is not None:
        ax.set_xlim(xlim)
    else:
        ax.set_xlim(min_lap, max_lap_actual + 1)

    # Styling labels/ticks
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

    ax.yaxis.set_major_formatter(_formatter)
    ax.yaxis.set_major_locator(MultipleLocator(5))
    ax.yaxis.set_minor_locator(MultipleLocator(1))
    ax.grid(True, which='major', axis='y', color='gray', linestyle='-', alpha=0.4)
    ax.grid(True, which='minor', axis='y', color='gray', linestyle=':', alpha=0.4)
    ax.grid(True, which='major', axis='x', alpha=0.3, linestyle='--')

    # Calculate labels position and adjust layout
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

    # Calculate padding needed on the right
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

    # Draw labels
    for ld in labels_data:
        if ld['y_display'] > max(y_min_bound, y_max_bound):
            continue

        # Highlight driver name label only if N >= total_segments or always_bright_labels is True
        is_highlighted = always_bright_labels or (N >= total_segments)
        current_alpha = bright_alpha if is_highlighted else dim_alpha

        ax.text(label_x, ld['y_display'], ld['name'],
                color=ld['color'], alpha=current_alpha, ha='left', va='center', fontsize=8,
                fontweight=('bold' if ld['is_hero'] else 'normal'),
                clip_on=False)

        if ld['is_excluded']:
            ax.plot(max_lap_actual + 1, ld['y_actual'], 'x', color=ld['color'],
                    alpha=current_alpha, markersize=8, mew=2, zorder=200, clip_on=False)

    for y_pos, label in lap_offsets.items():
        if y_pos > max(y_min_bound, y_max_bound):
            continue

        # Highlight penalty offsets only if N >= total_segments or always_bright_labels is True
        is_highlighted = always_bright_labels or (N >= total_segments)
        current_alpha = bright_alpha if is_highlighted else dim_alpha

        ax.text(label_x, y_pos, label, color='gray', alpha=current_alpha, fontsize=8,
                ha='left', va='center', fontweight='bold', style='italic',
                bbox=dict(facecolor='black', alpha=0.8 * current_alpha, edgecolor='none', pad=2),
                clip_on=False)

    return right_adjust


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate animated gap plot frames for a session.")
    parser.add_argument(
        "--session", required=True,
        help="Session identifier (e.g. league/class_name/date/track/session_id)"
    )
    parser.add_argument("--output-dir", default="frames", help="Output directory to save images (default: 'frames')")
    parser.add_argument("--aspect", help="Aspect ratio for the generated plots (e.g., '16:9', '4:3', '1:1')")
    parser.add_argument("--yrange", type=float, help="Custom Y-axis range / max gap in seconds (e.g., 30.0)")
    parser.add_argument("--dim-alpha", type=float, default=0.15, help="Background line opacity (default: 0.15)")
    parser.add_argument(
        "--bright-alpha", type=float, default=1.0,
        help="Foreground highlighted line opacity (default: 1.0)"
    )
    parser.add_argument(
        "--always-bright-labels", action="store_true",
        help="Keep driver name labels bright in all frames instead of highlighting them only at the end"
    )
    parser.add_argument(
        "--hero-names",
        help="Comma-separated hero pilot names to highlight (e.g., 'Brogan Marshall')"
    )

    args = parser.parse_args()

    # Parse session parts
    session_str = urllib.parse.unquote_plus(args.session)
    parts = session_str.split('/')
    if len(parts) != 5:
        print(
            f"Error: Invalid session parameter format. Expected 5 parts separated by '/': "
            f"league/class_name/date/track/session_id. Got: {session_str}",
            file=sys.stderr
        )
        sys.exit(1)

    league, class_name, date_str, track, session_id = parts

    print(f"Loading data for league={league!r}, class_name={class_name!r}, date={date_str!r}, track={track!r}...")

    # Load session data
    hero_names_list = [name.strip() for name in args.hero_names.split(',')] if args.hero_names else None
    load_names = hero_names_list if league == 'kartsim' else None

    df = database.db.load(leagues=league, classes=class_name, date=date_str, track=track, driver_names=load_names)
    if df.empty:
        print(f"Error: No meeting data found for {session_str}", file=sys.stderr)
        sys.exit(1)

    session_df = df[df["SessionID"] == session_id]
    if session_df.empty:
        print(f"Error: Session ID {session_id!r} not found in loaded data.", file=sys.stderr)
        sys.exit(1)

    df_pen = database.db.load_penalties(leagues=league, classes=class_name, date=date_str, track=track)
    session_pen = df_pen[df_pen["SessionID"] == session_id] if not df_pen.empty else None

    # Prepare plot data to calculate total segments
    plot_df = session_df[session_df['LapTimeSeconds'].notna()].copy()
    if plot_df.empty:
        print("Error: No valid lap times found in session data.", file=sys.stderr)
        sys.exit(1)

    max_lap_actual = plot_df['Lap'].max()
    min_lap = plot_df['Lap'].min()
    total_segments = max_lap_actual + 1 - min_lap

    print(
        f"Session stats: laps from {min_lap} to {max_lap_actual}. "
        f"Total highlighted frames: {total_segments + 1} (000 to {total_segments:03d})"
    )

    # Generate driver colors CONSISTENTLY
    driver_colors = get_driver_colors([session_df], hero_names=hero_names_list)

    # 1. Run a pre-calculation pass with N=total_segments to get consistent xlim, ylim, right_adjust
    figsize = _get_figsize(10, 10, args.aspect)
    fig_temp, ax_temp = plt.subplots(figsize=figsize, dpi=192)

    right_adjust = _draw_gap_on_ax_animated(
        ax_temp, session_df, penalties_df=session_pen, max_y=args.yrange,
        driver_colors=driver_colors, hero_names=hero_names_list,
        N=total_segments, dim_alpha=args.dim_alpha, bright_alpha=args.bright_alpha
    )
    xlim = ax_temp.get_xlim()
    ylim = ax_temp.get_ylim()
    plt.close(fig_temp)

    print("Applying constant axes limits:")
    print(f"  xlim: {xlim}")
    print(f"  ylim: {ylim}")
    print(f"  right_adjust: {right_adjust}")

    os.makedirs(args.output_dir, exist_ok=True)

    # 2. Render each frame
    for N in range(total_segments + 1):
        fig, ax = plt.subplots(figsize=figsize, dpi=192)

        _draw_gap_on_ax_animated(
            ax, session_df, penalties_df=session_pen, max_y=args.yrange,
            driver_colors=driver_colors, hero_names=hero_names_list,
            N=N, dim_alpha=args.dim_alpha, bright_alpha=args.bright_alpha,
            xlim=xlim, ylim=ylim, always_bright_labels=args.always_bright_labels
        )

        title_suffix = ''
        _add_header(fig, _get_plot_title(session_df.iloc[0], title_suffix))

        fig.subplots_adjust(
            left=_get_left_margin_frac(fig),
            right=right_adjust,
            top=_get_top_margin_frac(fig),
            bottom=_get_bottom_margin_frac(fig, 105)
        )

        filename = os.path.join(args.output_dir, f"{N:03d}.png")
        fig.savefig(filename)
        plt.close(fig)
        print(f"Generated frame {N:03d}/{total_segments:03d}: {filename}")

    print("Success: all frames generated.")


if __name__ == "__main__":
    main()
