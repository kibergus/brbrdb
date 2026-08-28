# Telemetry MCP Server Tools Reference

This reference outlines the available MCP server tools for fetching telemetry data, lap statistics, and rendering inspection plots.

## Tools Summary

### 1. `list_sessions(track=None, date=None, driver_name=None, league=None, class_name=None)`
- Lists recorded sessions with metadata (session_id, date, track, driver_names, telemetry_channels).

### 2. `get_track_info(track)`
- Returns track length (`lap_length`), sector boundaries (`sector_end`), and named turn definitions (`turns`).
- Each turn object contains: `name` (e.g. "1"), `start` (meters), `apex` (list of meters), `end` (meters).

### 3. `get_pace_summary(track, date, session_id=None, driver_name=None)`
- Returns actual best lap time, theoretical best lap time, total time delta, and per-turn breakdown of best times and priority rankings.

### 4. `get_aggregates(track, date=None, session_id=None, driver_name=None, exclude_outlier_pct=0.0)`
- Returns mean, min, max, and standard deviation for overall lap time and per-turn metrics (`turn_time`, `min_speed`, `max_steering`).

### 5. `get_stats(track, date=None, session_id=None, driver_name=None, turn_index=None, percentile_center=None, percentile_half_width=5.0)`
- Lap or per-turn statistics across matching laps.
- When `turn_index` is omitted: whole lap stats (`time_s`, `entry_speed_kmh`, `exit_speed_kmh`).
- When `turn_index` is provided: specific turn stats (`time_s`, `entry_speed_kmh`, `exit_speed_kmh`, `straight_exit_speed_kmh`, `apexes` array with `min_speed_kmh`, `max_steering_deg`).
- Use `percentile_center=0` for fastest laps, `percentile_center=50` for median laps, `percentile_center=75` for 75th percentile laps.

### 6. `get_telemetry_plot(laps, start_m, end_m, channels)`
- **Agent inspection tool**: Renders stacked multi-channel telemetry plot and returns base64 PNG.
- **Plot Readability Guideline**: Keep `laps` under 3 (default: 2 laps - Benchmark Reference Lap vs Target Comparison Lap).
- **`laps` is the ONLY way to select laps**: Pass a list of 4-element tuples `(date, track, session_id, lap_number)`.
- Channels: `["Speed", "Throttle", "Brake", "Steering Angle", "Delta Time"]`. (If `Throttle` or `Steering Angle` are not recorded on the kart, use `accel`/`acceleration` to analyze engine drive / braking transitions, and spatial GPS trajectory to analyze line geometry; treat any unmeasured steering/throttle actions as explicit hypotheses).

- **Agent inspection tool**: Renders X/Z GPS line trajectories for comparison over distance crop.
- **Plot Readability Guideline**: Keep `laps` under 3 (default: 2 laps - Benchmark Reference Lap vs Target Comparison Lap).
- **`laps` is the ONLY way to select laps**: Pass a list of 4-element tuples `(date, track, session_id, lap_number)`.
  Example: `laps=[("2026-06-25", "Llandow", "16_20_practice", 15), ("2026-07-28", "Llandow", "18_32_practice", 12)]`
- **`color_mode`**: Color scheme mode: `"lap"` (default, solid color per lap), `"pedals"` (green throttle / red brake / white coasting), `"accel"` (green accel / red decel), `"speed"` (multi-stop velocity heatmap), or `"delta_t"` (reference lap in white, other laps colored by rate of time gained/lost: green=gaining time, yellow=equal pace, red=losing time).
- **GPS-Only Telemetry**: Crucial tool when advanced channels (throttle/steering) are missing to diagnose line geometry, turn-in points, apex clipping, and exit tracking.

### 8. `get_session_consistency_image(session_id, track, date, driver_name=None)`
- Renders lap time evolution scatter plot over the session.

