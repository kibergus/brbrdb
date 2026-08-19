# MCP Server for Karting Telemetry Analysis

## Overview

This document defines the implementation plan for a Model Context Protocol (MCP) server that exposes
karting telemetry data and analysis capabilities as structured tools. The primary design goal is to
enable autonomous **sub-agent workflows** where a coordinating agent can spin up separate sub-agents
to investigate individual turns, generate visual evidence, and synthesize findings into a coaching
report — all without manual scripting.

The MCP server wraps the existing codebase (`race_db.py`, `location_handlers.py`, `parse_telemetry_csv`)
and adds a new image-generation layer using `matplotlib`.

---

## Architecture

```
MCP Client (Claude / Antigravity)
    │
    ├─ Orchestrator agent (builds report, assigns sub-tasks)
    │
    ├─ Sub-agent: Turn 1 investigator
    ├─ Sub-agent: Turn 2 investigator
    └─ ...
         │
         └─ MCP Server (new: mcp_server.py)
              │
              ├─ Session Tools    (reads race_db + CSV files)
              ├─ Track Tools      (reads track JSON)
              ├─ Lap Data Tools   (per-lap + per-turn stats)
              └─ Image Tools      (returns PNG paths / base64)
```

The server should be implemented as a **FastMCP** server using the `mcp` Python SDK. It runs as a
local subprocess (stdio transport) and is accessible from any MCP-compatible client.

---

## Tool Definitions

---

### `list_sessions`

List recorded sessions matching a set of optional filters.

**Input:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `track` | string | no | Track name, e.g. `"Lydd"` |
| `date` | string | no | ISO date `"2026-07-12"` — omit to list all |
| `driver_name` | string | no | Filter by driver name (case-insensitive substring match) |
| `league` | string | no | e.g. `"kartsim"` |
| `class_name` | string | no | e.g. `"iame_waterswift_restricted_cadet_uk"` |

**Returns:** Array of session descriptors:
```json
[
  {
    "session_id": "18_54_practice",
    "date": "2026-07-12",
    "track": "Lydd",
    "league": "kartsim",
    "class_name": "iame_waterswift_restricted_cadet_uk",
    "session_type": "practice",
    "driver_names": ["Alice Driver"],
    "telemetry_channels": ["speed", "throttle", "brake", "steering"]
  }
]
```

---

### `get_track_info`

Returns track metadata including lap length, sector boundaries, and named turn definitions with
distances along the lap. This is the primary reference for interpreting distance-based tool
parameters.

**Input:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `track` | string | yes | Track name |

**Returns:**
```json
{
  "name": "Lydd Karting 2026",
  "lap_length_m": 879.02,
  "sectors": [294.16, 629.16, 879.02],
  "turns": [
    {
      "name": "Turn 1",
      "start_m": 12.0,
      "apexes_m": [65.0],
      "end_m": 120.0
    },
    {
      "name": "Turn 2",
      "start_m": 172.0,
      "apexes_m": [205.0, 236.0],
      "end_m": 249.0
    }
  ]
}
```

---

### `get_stats`

Combined lap and per-turn stats tool. Returns one row per lap for every session matching the
filters. Per-turn stats follow the track config apex definitions: apex-level metrics
(min speed, max steering) are reported per apex; entry/exit speed belong to the turn boundaries;
straight exit speed is measured at the end of the following straight.

**Input:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `track` | string | yes | Track name |
| `date` | string | no | ISO date — if omitted, all dates are returned |
| `session_id` | string | no | Filter to a specific session |
| `driver_name` | string | no | Filter by driver name |
| `turn_index` | int | no | The turn index for which stats should be returned. If not given, whole lap stats are returned. |
| `percentile_center` | float | no | Return laps within ±`percentile_half_width` of this percentile (0–100) where 0 is fastest|
| `percentile_half_width` | float | no | Half-width of the percentile band (default 5) |

> Note that percentile_center=0 effectively returns `percentile_half_width` best results.

**Returns:** Array of lap or turn records, depending on whether `turn_index` is provided:

**When `turn_index` is omitted (returns lap-level stats):**
```json
[
  {
    "session_id": "18_54_practice",
    "date": "2026-07-12",
    "driver_name": "Alice Driver",
    "lap": 23,
    "time_s": 43.430,
    "entry_speed_kmh": 81.1,
    "exit_speed_kmh": 92.4
  }
]
```

**When `turn_index` is provided (returns specific turn stats):**
```json
[
  {
    "session_id": "18_54_practice",
    "date": "2026-07-12",
    "driver_name": "Alice Driver",
    "lap": 23,
    "turn_index": 1,
    "turn_name": "Turn 1",
    "time_s": 5.250,
    "entry_speed_kmh": 81.1,
    "exit_speed_kmh": 73.9,
    "straight_exit_speed_kmh": 89.4,
    "apexes": [
      {
        "apex_m": 65.0,
        "min_speed_kmh": 61.2,
        "max_steering_deg": 40.2
      }
    ]
  }
]
```

> - **`entry_speed_kmh`**: speed at the start of the turn (`start_m`) or start of the lap.
> - **`exit_speed_kmh`**: speed at the end of the turn (`end_m`) or end of the lap.
> - **`straight_exit_speed_kmh`**: speed at the end of the following straight (= `start_m` of the next turn). Captures how well the driver converted corner exit into straight-line speed (only present for turns).
> - **`apexes`**: one entry per apex defined in `get_track_info`. `min_speed_kmh` and `max_steering_deg` are extracted from a window around each apex distance (only present for turns).

---

### `get_aggregates`

# FIXME: Need to provide a smart way to filer out outliers e.g. drop out laps. E.g.maybe just take some tpop percentile of laps.

Returns aggregate statistics across all (or filtered) laps for each turn. Useful for reasoning
about driver consistency and identifying turns where performance is unstable.

**Input:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `track` | string | yes | Track name |
| `date` | string | no | ISO date — if omitted, aggregates across all dates |
| `session_id` | string | no | Restrict to one session |
| `driver_name` | string | no | Filter by driver name |
| `exclude_outlier_pct` | float | no | Trim this % from each tail before aggregating (default 0, max 20) |

**Returns:**
```json
{
  "driver_name": "Alice Driver",
  "date": "2026-07-12",
  "lap_count": 31,
  "overall": {
    "lap_time_min_s": 43.430,
    "lap_time_max_s": 46.102,
    "lap_time_avg_s": 44.215,
    "lap_time_std_s": 0.612
  },
  "Turn 1": {
    "turn_time_min_s": 5.080,
    "turn_time_max_s": 5.490,
    "turn_time_avg_s": 5.261,
    "turn_time_std_s": 0.091,
    "min_speed_avg_kmh": 62.3,
    "min_speed_std_kmh": 1.4,
    "max_steering_avg_deg": 39.1,
    "max_steering_std_deg": 2.3
  }
}
```

---

### `get_telemetry_plot`

Generates a stacked multi-channel telemetry comparison plot for one or more laps over a
distance range specified by the caller.

`laps` is the ONLY way to select laps for comparison. Each entry in `laps` MUST be a 4-element tuple/list of `(date, track, session_id, lap_number)`.

**Input:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `laps` | list[tuple] | yes | List of `(date, track, session_id, lap_number)` tuples/lists |
| `start_m` | float | yes | Start distance along the lap in metres |
| `end_m` | float | yes | End distance along the lap in metres |
| `channels` | list[string] | yes | Channels to plot, e.g. `["Speed", "Brake", "delta_time"]` |

**Returns:** `{ "image_base64": "..." }`

---

### `get_trajectory_plot`

Renders GPS (X/Z) trajectories for one or more laps overlaid on the full track outline.

`laps` is the ONLY way to select laps for comparison. Each entry in `laps` MUST be a 4-element tuple/list of `(date, track, session_id, lap_number)`.

**Input:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `laps` | list[tuple] | yes | List of `(date, track, session_id, lap_number)` tuples/lists |
| `start_m` | float | no | Start distance for the crop (omit for full lap) |
| `end_m` | float | no | End distance for the crop (omit for full lap) |
| `color_mode` | str | no | Color mode: `'lap'` (default), `'pedals'`, `'accel'`, or `'speed'` |

**Returns:** `{ "image_base64": "..." }`

---

### `get_pace_summary`

Returns a structured summary of the theoretical vs. actual best lap for a session or day,
including per-turn deltas and prioritized improvement opportunities.

**Input:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `track` | string | yes | Track name |
| `date` | string | yes | ISO date |
| `session_id` | string | no | Restrict to one session (otherwise aggregates across the day) |
| `driver_name` | string | no | Filter by driver name |

**Returns:**
```json
{
  "driver_name": "Alice Driver",
  "actual_best_lap": 23,
  "actual_best_session": "18_54_practice",
  "actual_best_s": 43.430,
  "theoretical_best_s": 42.936,
  "total_delta_s": 0.494,
  "turns": [
    {
      "name": "Turn 1",
      "actual_s": 5.250,
      "best_s": 5.080,
      "delta_s": 0.170,
      "best_lap": 25,
      "best_session": "18_54_practice",
      "priority": 1
    }
  ]
}
```

---

### `get_session_consistency_image`

Generates a lap time evolution chart over the session: scatter of lap time per lap with per-turn
colouring and a rolling trend line. Useful for assessing whether the driver improved or degraded
and in which turn.

**Input:**
| Parameter | Type | Required | Description |
|---|---|---|---|
| `track` | string | yes | Track name |
| `date` | string | yes | ISO date |
| `session_id` | string | yes | Session identifier |
| `driver_name` | string | no | Filter by driver name |

**Returns:** `{ "image_base64": "..." }`

---

## Proposed Workflow: Autonomous Turn Investigation

```
Orchestrator Agent
│
├── list_sessions(track="Lydd", date="2026-07-12", driver_name="Alice")
├── get_track_info(track="Lydd")
│     → learns turn names, apex distances, start/end metres
│
├── get_pace_summary(track="Lydd", date="2026-07-12")
│     → identifies priority turns by delta_s
│
├── get_aggregates(track="Lydd", date="2026-07-12")
│     → identifies which turns have high std_dev (inconsistency)
│
├── For each priority turn:
│    └── Spawn Sub-Agent: Investigate "Turn N"
│         │
│         ├── get_stats(track, date, turn_index=N)
│         │     → pick reference laps from returned turn stats
│         │
│         ├── get_trajectory_plot(laps=[best_lap, typical_lap],
│         │       start_m=turn.start_m, end_m=turn.end_m)
│         │
│         ├── get_telemetry_plot(laps=[best_lap, typical_lap],
│         │       start_m=turn.start_m, end_m=turn.end_m,
│         │       channels=["speed", "brake", "steering", "delta_time"])
│         │
│         └── Returns coaching note + TelemetryPlot.js spec (with vertical apex lines & lap highlights)
│
└── Synthesize all turn findings → Interactive HTML Coaching Report
      (renders responsive JS TelemetryPlot widgets with annotations for the driver)
```

---

## Implementation Details

### File Structure

```
/mnt/data/kibergus/karting/analysis/
├── mcp_server.py          # FastMCP entry point
├── mcp_tools/
│   ├── __init__.py
│   ├── session_tools.py   # list_sessions
│   ├── track_tools.py     # get_track_info
│   ├── lap_tools.py       # get_stats, get_aggregates, get_pace_summary
│   └── image_tools.py     # get_telemetry_plot, get_trajectory_plot,
│                          # get_session_consistency_image
├── static/js/
│   └── telemetry_plot.js  # Client-side interactive JS plot component for HTML reports
├── race_db.py             # Existing
├── location_handlers.py   # Existing
└── ...
```

### Dependencies

- `mcp` Python SDK (`pip install mcp`)
- `matplotlib` Agg backend (already in venv)
- `pandas`, `numpy` (already present)

### Matplotlib Recursion Bug Workaround

> [!WARNING]
> Matplotlib has a bug with Python 3.14's `copy.deepcopy` causing a `RecursionError` during
> `plt.style.use('dark_background')`. **Workaround**: apply dark styles manually via `rcParams`
> rather than the built-in style system.

### Image & Plot Output

Image tools (`get_telemetry_plot`, `get_trajectory_plot`, `get_session_consistency_image`) return a `base64`-encoded PNG so the sub-agent LLM can inspect visual curves directly during investigation without needing local file path management.

For user-facing coaching reports, the orchestrator agent generates HTML pages configured with `TelemetryPlot.js` widgets. This provides full client-side interactivity (hover tooltips, line highlights, vertical marker annotations) without requiring server image rendering for report viewing.

### Transport

Initial implementation: `stdio` subprocess. Future: HTTP/SSE for multi-client use.
