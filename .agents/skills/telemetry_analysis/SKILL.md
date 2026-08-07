---
name: telemetry_analysis
description: Analyzes karting telemetry data using MCP server tools and corner sub-agents, comparing fastest vs median and 75th percentile laps to produce an actionable interactive HTML coaching report in reports folder.
---

# Telemetry Analysis Skill

This skill provides an autonomous, turn-by-turn workflow for analyzing karting telemetry data. It combines orchestrator data discovery, corner-focused sub-agent investigations, and interactive HTML report generation in the `reports/` folder using the project's MCP server and paired `TelemetryPlot.js` library.

---

## Workflow Overview

```
Orchestrator Agent
 ├── 1. Data Discovery (list_sessions, get_track_info, get_pace_summary, get_aggregates, get_stats)
 ├── 2. Corner Sub-Agent Delegation (1 sub-agent per turn: Turn 1, Turn 2, ...)
 ├── 3. Synthesis (Identify 1 or 2 most important actionable coaching takeaways)
 └── 4. HTML Report Generation (writes reports/<session_id>_telemetry_report.html with TelemetryPlot widgets)
```

---

## Step 1: Session & Data Discovery (Orchestrator)

1. **Mandatory MCP Data Loading with Python Post-Processing**:
   - All session lists, track metadata, pace summaries, lap statistics, and telemetry plots **MUST be loaded through MCP server tools** (`list_sessions`, `get_track_info`, `get_pace_summary`, `get_stats`, `get_telemetry_plot`, `get_trajectory_plot`).
   - Agents **ARE ALLOWED and encouraged to post-process MCP output data using Python scripts** (e.g. executing lightweight inline Python scripts to sort, filter, calculate delta distributions, or identify specific target laps from MCP outputs).

2. **Locate Target Sessions**:
   Call `list_sessions(track=..., date=..., driver_name=...)` to list **ALL** available sessions for the target date and track (note: multiple sessions e.g. morning and evening practice/race sessions often exist on the same day).

3. **Fetch Track Metadata**:
   Call `get_track_info(track=...)` to retrieve lap length, sector boundaries, and named turn definitions (`start_m`, `end_m`, `apexes_m`).

4. **Get Pace & Aggregate Overview Across Sessions**:
   - Call `get_pace_summary(track=..., date=..., driver_name=...)` to identify theoretical best lap vs actual best lap across all sessions on that date, total delta, and per-turn deltas.
   - Call `get_aggregates(track=..., date=..., driver_name=...)` to check per-turn time consistency (`turn_time_std_s`).

5. **Select Cross-Session Reference Laps**:
   Scan **all sessions** for that date via `get_stats(..., turn_index=N)` / `get_pace_summary` to select:
   - **Fastest Corner Benchmark Lap**: When analyzing a specific corner (e.g. Turn 4), the benchmark reference lap **MUST be selected based on the fastest segment time for THAT SPECIFIC CORNER** (`turn_time_s` from `get_stats(..., turn_index=N)`), **NOT overall full lap time**. For example, if Lap 12 has the fastest Turn 4 corner time (9.042s), Lap 12 MUST be chosen as the Turn 4 benchmark reference lap, even if another lap had a faster overall full lap time.
   - **2nd Fastest / Comparison Laps**: The next fastest corner laps or representative clean laps.
   - **Time Loss Laps (~0.4s Lost)**: Target laps where significant time is lost in that specific corner (e.g., +0.35s to +0.45s corner delta) due to driver mistakes or line degradation.

---

## Step 2: Corner-by-Corner Investigation via Sub-Agents

For **EACH** turn defined in `get_track_info`:
Launch a dedicated sub-agent focusing exclusively on that turn index $N$ ($Turn\ 1, Turn\ 2, \dots$).

### Sub-Agent Instructions & Scope:
1. Restrict visual and statistical analysis **only** to the distance range `start_m` to `end_m` of the target turn.
2. Call `get_stats(track=..., date=..., turn_index=N)` for the target driver to compare:
   - Turn time ($s$)
   - Entry speed ($km/h$)
   - Minimum apex speed ($km/h$)
   - Exit speed ($km/h$)
   - Straight exit speed ($km/h$)
   - Apex maximum steering angle ($deg$)
3. Inspect internal plot images:
   - Call `get_telemetry_plot(laps=[(date, track, session_id, lap_num), ...], start_m=..., end_m=..., channels=["Speed", "Brake", "Throttle", "Steering Angle", "delta_time"])`.
   - Call `get_trajectory_plot(laps=[(date, track, session_id, lap_num), ...], start_m=..., end_m=...)`.
4. Diagnose driver root cause actions (linking driver control inputs directly to chosen trajectory line geometry):
   - **Line Geometry & Trajectory**: Wide vs tight entry approach, apex clipping distance, mid-corner trajectory arc, and exit line width.
   - **Throttle Pickup & Lift Offs**: How entry line angle and apex trajectory dictate when full throttle can be picked up, or force mid-corner throttle chops/lifting.
   - **Steering Inputs & Corrections**: How trajectory pinch or over-shooting forces over-steering, scrubbed minimum speed, or emergency counter-steering.
   - **Braking & Entry Dynamics**: Braking point distance and trail-braking pressure shaped by the entry trajectory angle into the turn.
   - **Time Delta Reference**: ALWAYS calculate time deltas relative to the fastest lap present on the plot/report.
5. Generate `TelemetryPlot.js` JSON spec:
   - Define `vertical_lines` for apex distance and braking initiation.
   - Define `highlight_ranges` for braking zone and corner exit acceleration zone.
   - Define `lap_styles` for fastest, median, and 75% laps.
   - Define `annotations` pointing out exact telemetry deltas (e.g. "Apex Min Speed: 61.2 vs 58.4 km/h").

---

## Step 3: Synthesis & Key Action Items

1. Collect all corner sub-agent reports.
2. Rank turns by time delta and driver inconsistency.
4. Keep the coaching advice clear, encouraging, and directly grounded in the telemetry evidence.

---

## Communication Style & Audience Guidelines (Junior Drivers 10–18 Years Old)

ALL coaching text, explanations, and report summaries MUST be tailored for **youth karting drivers aged 10 to 18 years old**:

1. **Tone & Persona**:
   - **Supportive, Energetic, and Empowering**: Act as an encouraging racing coach who builds confidence. Always highlight what the driver did well (e.g. "Awesome entry speed into Turn 4!") before diving into areas for improvement.
   - **Positive Framing**: Frame mistakes as exciting opportunities to unlock lap time (e.g. "Unlocking +0.2s by opening up your exit line!" instead of "Driver error lost time").

2. **Language & Terminology**:
   - **Accessible & Clear**: Avoid dense engineering jargon or heavy mathematical formulas. 
   - **Karting Terminology**: Use natural karting terms that young drivers use at the track:
     - Use *"getting back on the gas"* or *"throttle pickup"* instead of "accelerator displacement".
     - Use *"carrying rolling speed through the apex"* instead of "apex minimum velocity optimization".
     - Use *"scrubbing speed with too much steering lock"* instead of "tire scrub friction loss".
     - Use *"opening up the wheel on exit"* instead of "unwinding steering input angle".
     - Use *"straight-line braking"* and *"smooth trail braking"* instead of "deceleration phase transition".

3. **Actionable Track Takeaways**:
   - Keep bullet points punchy, visual, and easy to remember when sitting in the kart on the dummy grid.
   - Give 1-2 simple visual cues for their next session (e.g. "Eye up the exit curb early, keep steering inputs smooth at the apex, and flatten out the wheel as you smash the throttle!").

---

## Step 4: HTML Report Generation in Data Reports Folder

Generate a standalone HTML file in the reports directory at `<DATA_DIR>/reports/<filename>.html` (e.g., `<DATA_DIR>/reports/bayford_meadows_turn2_telemetry_report.html`). The report will be served through the main site under the `/reports/` path (e.g., `/reports/<filename>.html`).

### Mandated HTML Requirements:
- **Same-Origin External Stylesheet**: HTML reports MUST link the main site stylesheet (`<link rel="stylesheet" href="/static/css/telemetry_report.css">`).
- **Actual Telemetry Display**: ALL telemetry plots in the report MUST be rendered using the project's `TelemetryPlot.js` library (`<script src="/static/js/telemetry_plot.js"></script>`) paired with Plotly (`<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>`). Because reports are served on the main site under `/reports/`, API requests automatically execute same-origin with standard authenticated session cookies.

- **Interactive Widgets**: Each turn section MUST contain a `<div class="telemetry-widget-container" id="telemetry-widget-turn-N"></div>` element populated via:
  ```html
  <script>
    TelemetryPlot.render('#telemetry-widget-turn-1', {
      session_id: "...",
      track: "...",
      date: "...",
      laps: [23, 15, 8],
      start_m: 12.0,
      end_m: 120.0,
      channels: ["speed", "brake", "throttle", "steering", "delta_time"],
      vertical_lines: [{ distance_m: 65.0, label: "Apex T1", color: "#38bdf8", style: "dashed" }],
      highlight_ranges: [{ start_m: 45.0, end_m: 65.0, label: "Braking Zone", color: "rgba(244, 63, 94, 0.12)" }],
      lap_styles: {
        "23": { color: "#10b981", width: 2.5, label: "Lap 23 (Fastest - 43.43s)" },
        "15": { color: "#f59e0b", width: 1.5, label: "Lap 15 (Median - 44.10s)" },
        "8": { color: "#f43f5e", width: 1.5, label: "Lap 8 (75% Lap - 44.58s)", style: "dashed" }
      },
      annotations: [
        { distance_m: 65.0, channel: "speed", text: "Min Speed: 61.2 vs 58.4 km/h" }
      ]
    });
  </script>
  ```
- **Time Delta Calculation**: Time deltas in report tables and annotations MUST always be calculated relative to the fastest lap present on the plot/report (the reference lap with delta = 0.000s).
- **Cross-Session Benchmark Laps**: When selecting the fastest reference lap for a corner, ALWAYS scan across ALL sessions available for that date (e.g. morning practice, afternoon practice, heat/race sessions) so the benchmark represents the driver's absolute best performance of the day.
- **GPS Coordinates for Trajectory Plots**: Spatial trajectory plots (`TrajectoryPlot.js` / `get_trajectory_plot`) MUST ALWAYS use `Longitude` and `Latitude` GPS coordinates (in degrees) as the primary spatial channels.
- **Trajectory-Driven Input Analysis**: Driver inputs (throttle modulation, throttle chops/lifting, steering snaps, oversteer corrections, and brake duration) MUST be analyzed as direct consequences of the driver's chosen racing line geometry (entry width, turn-in angle, apex proximity/clipping, mid-corner arc, and exit positioning).
- **Dual Visualizations**: Reports MUST include both channel telemetry (`TelemetryPlot.js`) and spatial GPS trajectory visuals (`TrajectoryPlot.js`) for the distance crop.
- **Color-Coded Lap Numbers in Text**: Whenever lap numbers are mentioned in text (body paragraphs, executive summary, coaching takeaways, coach summary boxes, or table cells), they MUST be styled using CSS classes matching their telemetry plot trace colors: `.lap_ref` for the benchmark reference lap (e.g. `<span class="lap_ref">Lap X</span>`), and `.lap_1`, `.lap_2`, ..., `.lap_10` for target laps in sequential plot order (e.g. `<span class="lap_1">Lap Y</span>`, `<span class="lap_2">Lap Z</span>`).
- **Report Structure & Layout Requirements**:
  1. **Header & Executive Summary Grid**:
     - **Left Side**: Executive summary text. Driver name MUST be highlighted as a clickable link to their brbrdb driver page (`https://brbrdb.brbrkitten.com/drivers/<driver_name>`). Track and turn names MUST be highlighted with `.highlight-badge`. Session names MUST be highlighted as clickable links to session pages (`https://brbrdb.brbrkitten.com/telemetry/<league>/<class_name>/<date>/<track>/`).
     - **Right Side**: Interactive turn/lap time distribution chart (Plotly violin/scatter plot) showing time spread across laps and highlighting the selected analysis laps on the curve.
  2. **Selected Analysis Laps Table**:
     - Placed immediately below the summary header grid. Contains columns: Lap Role (pill badge), Session & Date (with session link), Lap #, Turn/Lap Time, Time Delta, and Selection Rationale & Focus. Highlights *why* each lap was selected for analysis. No telemetry plot widgets in this section.
  3. **Coaching Actions & Detailed Turn Breakdown**:
     - Detailed turn breakdown cards containing interactive multi-channel `TelemetryPlot.js` widgets, spatial `TrajectoryPlot.js` line overlays, telemetry metrics tables, and coach's action items for the driver.

---

## References

For detailed reference files, refer to:
- [MCP Tools Reference](references/mcp_tools_reference.md)
- [Corner Sub-Agent Prompt Template](references/subagent_prompt.md)
- [HTML Report Template](references/report_template.html)
