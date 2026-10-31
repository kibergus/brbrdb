# Python Test Coverage Status & Backlog

This document tracks unit test coverage for pure helper functions across Python modules that do not require external I/O, database, or network mocks.

---

## 1. Status Overview

| Module | Status | Notes |
| :--- | :---: | :--- |
| `aliases.py` | ❌ **Yet to be Added** | 8 pure dictionary/mapping functions currently have 0% test coverage. |
| `plot_config.py` | ❌ **Yet to be Added** | `get_ymin` and `get_yrange` have 0% test coverage. |
| `plots.py` (helpers) | ❌ **Yet to be Added** | Axis formatting, aspect ratio, and title generation helpers unverified. |
| `video_transcoder.py` | ❌ **Yet to be Added** | Cache path hash resolver unverified. |
| `race_db.py` (helpers) | ⚠️ **Partially Implemented** | `_to_list`, path traversal, and `RaceMetadata` dates tested; class/pos helpers remain. |

---

## 2. Implemented Items (Summary)

- **`race_db.py`**:
  - `_to_list(x)`: Tested in `tests/test_race_db.py`.
  - `check_for_dir_traversal(items)`: Tested across paths in `tests/test_race_db_security.py`.
  - `RaceMetadata.date` & `RaceMetadata.time`: Tested in `tests/test_race_db.py`.

---

## 3. Items Yet to be Added (Backlog)

### `aliases.py` — High Priority (No Current Coverage)
All functions are pure lookup/string transformations ideal for parameterized tests:
- `get_consolidated_class(raw_class_name: str) -> str`: Class name alias mapping.
- `get_league_name(league_id: str) -> str`: League identifier to display label.
- `get_league_color(league_id: str) -> str`: Hex color mapping with fallbacks.
- `get_league_group(league_id: str) -> str`: League classification grouping.
- `group_leagues(leagues: list[str]) -> list[tuple[str, list[str]]]`: Grouping & ordering logic.
- `get_class_name(class_id: str) -> str`: Class ID to display label.
- `get_class_info(class_id: str) -> ClassInfo`: Class dataclass metadata retrieval.
- `get_video_source_name(source_id: str) -> str | None`: Video provider mapping.

---

### `plot_config.py` — High Priority (No Current Coverage)
- `get_ymin(laps: pd.Series) -> float`: Robust minimum calculation with glitch filtering (ignoring aberrant sub-lap spikes).
- `get_yrange(league: str, class_name: str, laps: pd.Series | None = None) -> float`: Dynamic and configured Y-axis range selection, testing `'auto'` mode and league/class overrides.

---

### `plots.py` Helper Functions — Medium Priority
While major plot renderers are smoke-tested in `test_plots.py`, these internal pure helpers lack direct unit testing:
- `_is_hero(driver_name: str, hero_names: list[str] | None) -> bool`: Hero driver check with case normalization and null handling.
- `_format_time(x: float, pos: int | None = None) -> str`: Axis tick time formatting.
- `_format_time_full(x: float, pos: int | None = None) -> str`: Millisecond-precision lap time formatting.
- `_get_figsize(width: float, height: float, aspect: str | None) -> tuple[float, float]`: Aspect ratio sizing calculations.
- `_get_plot_title(sample_row: pd.Series, suffix: str = '', session_names: list[str] | None = None) -> str`: Complex multi-session plot title generator.

---

### `video_transcoder.py` — Low Priority
- `_get_cache_path(filename: str, video_cache_dir: str) -> str`: Deterministic cache path hashing based on source filename.

---

### `race_db.py` Pure Helpers — Low Priority
- `_match_class(class_name: str, classes_filter: list[str], consolidated_classes_filter: list[str]) -> bool`: Multi-tier class filtering with alias consolidation.
- `_normalize_pos(v: str | int | float) -> int | str`: String preservation for `DNS`/`DNF`/`DSQ` and numeric parsing.
