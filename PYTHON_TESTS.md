# Python Test Coverage Improvement Plan

This document identifies functions across the project that can be tested using standard unit tests without the need for mocking external dependencies (I/O, databases, or network).

## `karting/analysis` Application

### `aliases.py`
- `get_consolidated_class(raw_class_name: str) -> str`: Tests class name simplification.
- `get_league_name(league_id: str) -> str`: Tests league ID to display name mapping.
- `get_league_color(league_id: str) -> str`: Tests color mapping.
- `get_league_group(league_id: str) -> str`: Tests league categorization.
- `group_leagues(leagues: list[str]) -> list[tuple[str, list[str]]]`: Tests grouping and sorting logic.
- `get_class_name(class_id: str) -> str`: Tests class ID to display name mapping.
- `get_class_info(class_id: str) -> ClassInfo`: Tests class metadata retrieval.
- `get_video_source_name(source_id: str) -> str | None`: Tests video source mapping.

### `plot_config.py`
- `get_ymin(laps: pd.Series) -> float`: Tests robust minimum calculation with glitch filtering.
- `get_yrange(league: str, class_name: str, laps: pd.Series | None = None) -> float`: Tests y-axis range selection logic including overrides and 'auto' mode.

### `plots.py`
- `_is_hero(driver_name: str, hero_names: list[str] | None) -> bool`: Tests hero driver identification.
- `_format_time(x: float, pos: int | None = None) -> str`: Tests time formatting for plot axes.
- `_format_time_full(x: float, pos: int | None = None) -> str`: Tests precise time formatting.
- `_get_figsize(width: float, height: float, aspect: str | None) -> tuple[float, float]`: Tests aspect ratio calculations.
- `_get_plot_title(sample_row: pd.Series, suffix: str = '', session_names: list[str] | None = None) -> str`: Tests complex title generation logic (highly valuable for testing).

### `video_transcoder.py`
- `_get_cache_path(filename: str, video_cache_dir: str) -> str`: Tests unique cache path generation based on filename hash.

### `race_db.py`
- `_to_list(x: str | Iterable[str] | None) -> list[str]`: Tests input normalization.
- `_match_class(class_name: str, classes_filter: list[str], consolidated_classes_filter: list[str]) -> bool`: Tests class filtering logic.
- `_normalize_pos(v: str | int | float) -> int | str`: Tests position normalization (handling DNS/DNF).
- `_check_for_dir_traversal(items: TraversalInput) -> None`: Tests security validation for path traversal.
- `RaceMetadata.date` (property): Tests date extraction from datetime string.
- `RaceMetadata.time` (property): Tests time extraction and formatting.
