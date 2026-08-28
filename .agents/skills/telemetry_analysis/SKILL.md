---
name: telemetry_analysis
description: Analyzes karting telemetry data using MCP server tools and corner sub-agents, comparing reference benchmark vs target laps (<3 laps per plot) with Delta T inflection point analysis, data-grounded causation chain investigation with explicit confidence levels, and actionable coaching guidance in HTML reports.
---

# Telemetry Analysis Skill

This skill provides an autonomous, turn-by-turn workflow for analyzing karting telemetry data. It combines orchestrator data discovery, corner-focused sub-agent investigations, and interactive HTML report generation in the `reports/` folder using the project's MCP server and paired `TelemetryPlot.js` library.

---

## Workflow Overview

```
Orchestrator Agent
 ├── 1. Data Discovery (list_sessions, get_track_info, get_pace_summary, get_aggregates, get_stats)
 ├── 2. Lap Selection (Keep <3 laps per plot: Benchmark vs Target Comparison Lap)
 ├── 3. Corner Sub-Agent Delegation (Delta T inflection analysis, trajectory, data-grounded causation, confidence level)
 ├── 4. Synthesis (Identify 1 or 2 most important actionable coaching takeaways)
 └── 5. HTML Report Generation (writes reports/<session_id>_telemetry_report.html with TelemetryPlot widgets)
```

---

## Step 1: Session & Data Discovery (Orchestrator)

1. **Mandatory MCP Data Loading with Python Post-Processing**:
   - All session lists, track metadata, pace summaries, lap statistics, and telemetry plots **MUST be loaded through MCP server tools** (`list_sessions`, `get_track_info`, `get_pace_summary`, `get_stats`, `get_telemetry_plot`, `get_trajectory_plot`).
   - Agents **ARE ALLOWED and encouraged to post-process MCP output data using Python scripts** (e.g. executing lightweight inline Python scripts to sort, filter, calculate delta distributions, or identify specific target laps from MCP outputs).

2. **Locate Target Sessions & Audit Available Channels**:
   - Call `list_sessions(track=..., date=..., driver_name=...)` to list **ALL** available sessions for the target date and track (note: multiple sessions e.g. morning and evening practice/race sessions often exist on the same day).
   - **Channel Audit**: Identify which telemetry channels are available in the session (e.g. `Speed`, `accel`, `Latitude`, `Longitude` vs full sensor suites with `Steering Angle`, `Throttle`, `Brake`, `RPM`, `Slip Angle`). Note missing channels up front so conclusions never claim unmeasured data as fact.

3. **Fetch Track Metadata**:
   Call `get_track_info(track=...)` to retrieve lap length, sector boundaries, and named turn definitions (`start_m`, `end_m`, `apexes_m`).

4. **Get Pace & Aggregate Overview Across Sessions**:
   - Call `get_pace_summary(track=..., date=..., driver_name=...)` to identify theoretical best lap vs actual best lap across all sessions on that date, total delta, and per-turn deltas.
   - Call `get_aggregates(track=..., date=..., driver_name=...)` to check per-turn time consistency (`turn_time_std_s`).

5. **Select Reference & Comparison Laps (Strict Plot Readability Rule)**:
   - **Plot Readability Rule**: **Keep the number of laps shown on plots under 3** (typically **2 laps**: 1 Benchmark Lap vs 1 Target Comparison Lap; or at most 3 laps when contrasting a specific distribution like Benchmark vs Median vs 75th percentile) unless specifically asked to do differently. This prevents plot clutter and keeps traces clear and interpretable.
   - Scan **all sessions** for that date via `get_stats(..., turn_index=N)` / `get_pace_summary` to select:
     - **Fastest Corner Benchmark Lap**: When analyzing a specific corner (e.g. Turn 4), the benchmark reference lap **MUST be selected based on the fastest segment time for THAT SPECIFIC CORNER** (`turn_time_s` from `get_stats(..., turn_index=N)`), **NOT overall full lap time**. For example, if Lap 12 has the fastest Turn 4 corner time (9.042s), Lap 12 MUST be chosen as the Turn 4 benchmark reference lap, even if another lap had a faster overall full lap time.
     - **Target Comparison Lap**: A representative median lap or a lap with a specific time loss (~0.3s to 0.5s delta) to diagnose the root cause of the slowdown.

---

## Step 2: Corner-by-Corner Investigation via Sub-Agents

For **EACH** turn defined in `get_track_info`:
Launch a dedicated sub-agent focusing exclusively on that turn index $N$ ($Turn\ 1, Turn\ 2, \dots$).

### Sub-Agent Scope & Plot Restrictions:
1. Restrict visual and statistical analysis **only** to the distance range `start_m` to `end_m` of the target turn.
2. **Keep plotted laps under 3** (default to 2 laps: Benchmark Reference Lap vs Target Comparison Lap) for all visual inspection and report widgets.
3. Call `get_stats(track=..., date=..., turn_index=N)` for the target driver to compare:
   - Turn time ($s$)
   - Entry speed ($km/h$)
   - Minimum apex speed ($km/h$)
   - Exit speed ($km/h$)
   - Straight exit speed ($km/h$)
   - Apex maximum steering angle ($deg$, *if recorded*)

---

## Data-Grounded Causation Methodology & Confidence Communication

All findings, diagnoses, and coaching takeaways MUST be strictly anchored in the actual telemetry channels recorded. **Never assert unmeasured physical driver actions or vehicle dynamics as factual certainty.**

### Evidence Hierarchy & Confidence Levels

When forming conclusions, categorize each finding into one of three confidence tiers:

1. **Direct Telemetry Evidence (High Confidence)**:
   - Directly measured and visible in available telemetry channels (e.g., speed trace drops 5 km/h earlier, Delta T steepens +0.15s between 40m–60m, GPS trajectory shows turn-in 3m earlier / apex clipped 1.2m wider).
   - **Tone**: State as objective fact (e.g., *"The data shows minimum apex speed was 4.2 km/h lower on Lap 15, accounting for +0.14s of time loss."*).

2. **Derived / Corroborated Findings (Medium Confidence)**:
   - Inferences supported by surrogate or secondary channels when a primary sensor is absent (e.g., `accel` longitudinal acceleration channel showing positive drive begins 6 meters later, indicating delayed throttle pickup even if a direct pedal position sensor is not fitted).
   - **Tone**: State the inference clearly alongside the supporting channel (e.g., *"Based on the longitudinal acceleration (`accel`) trace, positive drive was delayed by ~6m, indicating later throttle commitment."*).

3. **Hypotheses & Plausible Scenarios (Low-to-Medium Confidence)**:
   - Explanations of driver behavior or dynamics that **are not directly measured** (e.g., claiming a speed scrub was caused by "steering wheel snap / excessive steering lock" or "brake locking" when steering angle and brake pressure sensors are **not** present).
   - **Strict Rule**: You **MUST NOT** claim an unmeasured cause as a definitive fact. You may offer it as a reasoned hypothesis, but **you must explicitly communicate the confidence level and state that the channel is not recorded**.
   - **Tone / Phrasing Example**: *"Hypothesis (Medium Confidence — steering angle not recorded): The 4 km/h mid-apex speed drop without deceleration on the accel trace suggests possible tire scrub from an aggressive steering input or line pinch."*

---

## Lap-to-Lap Comparison & Root Cause Causation Workflow

When comparing laps, the core objective is to determine **why time was lost** and formulate **actionable things the driver can do to be faster**:

### 1. Delta T Inflection Point Analysis
- Inspect the **Delta Time ($\Delta T$)** channel across the corner distance.
- Pinpoint the **exact meter marks / locations where $\Delta T$ begins to climb or steepens significantly** (identifying where time is actively lost: entry phase, apex phase, or exit acceleration phase).

### 2. Telemetry Channels Inspection & Data-Grounded Causation Chain
Around each point where time is lost, inspect the available telemetry channels to establish the **causation chain** (Driver Control / Line Choice $\rightarrow$ Kart Dynamics / Attitude $\rightarrow$ Speed Deficit $\rightarrow$ Time Delta):

- **Throttle / Accelerator**:
  - *If Throttle channel is available*: Check for delayed throttle pickup, hesitation, throttle breathing/lifting, or partial application.
  - *Fallback if throttle channel is not available*: Inspect **longitudinal acceleration (`accel` / `acceleration`)** to evaluate when positive drive begins and how aggressively the kart accelerates out of the corner. Clearly attribute observations to `accel` rather than direct pedal position.
- **Steering Angle & Steering Inputs**:
  - *If Steering Angle channel is available*: Check for excessive steering lock (causing front tire scrub and wiping off minimum apex speed), sudden steering spikes, mid-corner steering corrections, or delayed steering unwind on exit.
  - *If Steering Angle is NOT available*: **DO NOT claim steering snapping or excessive wheel lock as a proven fact.** Use GPS trajectory curvature and lateral/longitudinal acceleration to identify line pinching or speed scrub, and present any steering input theory explicitly as a hypothesis.
- **Slip Angles & Lateral Dynamics**:
  - *If available*: Inspect slip angles, lateral acceleration, or yaw rate to check for sliding, snap oversteer, rear instability, or excessive understeer scrub.
  - *If unavailable*: Note lateral acceleration or trajectory widening as surrogate indicators, maintaining appropriate confidence language.
- **Braking Dynamics**:
  - *If Brake pressure/switch is available*: Compare braking initiation point (meters), peak braking force, and trail-braking smoothness vs abrupt brake release.
  - *Fallback if brake channel is not available*: Inspect negative longitudinal acceleration (`accel`) dips to assess braking/deceleration zones and release transitions.
- **Engine RPM & Speed**:
  - Inspect Engine RPM (clutch slip, power band drop, or gearing effects) and speed deltas across the corner.

### 3. Trajectory & Line Analysis (Crucial for GPS-Only & All Telemetry)
- Call `get_trajectory_plot(laps=[...], start_m=..., end_m=..., color_mode="delta_t")` and `color_mode="lap"`.
- **GPS-Only Telemetry Diagnosis**: When advanced sensor channels (throttle, steering, slip angles) are not available and only GPS is recorded, **the spatial trajectory is the primary diagnostic tool**. Analyze:
  - **Entry Width & Turn-in Point**: Did an early turn-in pinch the apex radius? Did a narrow entry prevent carrying roll speed?
  - **Apex Clipping & Geometric Arc**: Was the apex missed? Was the apex clipped too early (forcing a compromised exit)?
  - **Mid-Corner Radius**: Did the driver maintain a smooth continuous radius or have to correct the line?
  - **Exit Width**: Did the driver track out fully to the exit kerb to maximize exit radius, or did they pinch the exit?

---

## Step 3: Synthesis & Key Action Items

1. Collect all corner sub-agent reports.
2. Rank turns by time delta and driver inconsistency.
3. Identify the 1 or 2 highest-priority coaching takeaways across the entire lap.
4. Translate telemetry causation findings into clear, actionable advice the driver can execute on track.
5. **Verify Data Grounding & Confidence Calibration**: Ensure the final synthesis and executive summary strictly distinguish directly measured facts (e.g. apex speeds, braking points, spatial line width) from inferred dynamics or unmeasured hypotheses. Avoid presenting speculative driver inputs (such as unrecorded steering snaps or pedal lifts) as factual certainty.

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
   - **Data Grounding in Coaching**: When giving coaching advice based on inferred or unrecorded dynamics, ground the takeaway in observable targets (e.g., "Keep your hands smooth and focus on a wider entry arc to avoid scrubbing rolling speed" rather than asserting "you snapped the steering wheel at meter 60").

3. **Actionable Track Takeaways**:
   - Keep bullet points punchy, visual, and easy to remember when sitting in the kart on the dummy grid.
   - Give 1-2 simple visual cues for their next session (e.g. "Eye up the exit curb early, keep steering inputs smooth at the apex, and flatten out the wheel as you smash the throttle!").

4. **Speed Units Standard (km/h Only)**:
   - **Always use `km/h`** in all report text, coaching takeaways, coach summary boxes, table headers/cells, and plot annotations.
   - If raw telemetry channels or intermediate calculations are recorded in m/s, convert them to km/h ($v_{\text{km/h}} = v_{\text{m/s}} \times 3.6$).

---

## Step 4: HTML Report Generation in Data Reports Folder

Generate a standalone HTML file in the reports directory at `<DATA_DIR>/reports/<filename>.html` (e.g., `<DATA_DIR>/reports/bayford_meadows_turn2_telemetry_report.html`). The report will be served through the main site under the `/reports/` path (e.g., `/reports/<filename>.html`).

### Mandated HTML Requirements:
- **Same-Origin External Stylesheet & JavaScript Libraries**: HTML reports MUST link:
  - Main stylesheet: `<link rel="stylesheet" href="/static/css/telemetry_report.css">`
  - Plotly: `<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>`
  - Stats & Minimap Library: `<script type="module" src="/static/js/stats_plots.js"></script>`
  - TelemetryPlot Library: `<script src="/static/js/telemetry_plot.js"></script>`
  - TrajectoryPlot Module: `<script type="module" src="/static/js/trajectory_plot.js"></script>`

- **Plot Lap Limit**: Always keep the number of laps displayed on `TelemetryPlot` and `TrajectoryPlot` widgets under 3 (default: 2 laps - Benchmark Reference Lap and Target Comparison Lap).

- **No Hardcoded Plot Data (Anti-Hallucination & Clean Architecture)**:
  - Reports MUST NEVER hardcode raw arrays of lap times or plot data in scripts.
  - All distribution plots and minimaps MUST be rendered using the reusable building bricks in `stats_plots.js` (`StatsPlot` and `TrackMinimap`), which fetch live, authenticated data directly from `/api/telemetry` and `/api/track_data`.

- **Distribution Chart Building Brick (`StatsPlot.renderTurnViolin`)**:
  ```html
  <script>
    StatsPlot.renderTurnViolin('#distribution-chart', {
      league: "club100_south",
      class_name: "cadet_lw",
      track: "Llandow",
      date: "2026-08-02",
      turn_name: "The Dell", // or turn_index: 5
      highlight_laps: [
        { session_id: "12_30_race_5_qualifying", lap: 11, label: "Benchmark (14.075s)", color: "#10b981", size: 11 },
        { session_id: "12_30_race_5_qualifying", lap: 5, label: "Median (14.409s)", color: "#f59e0b", size: 10 }
      ]
    });
  </script>
  ```

- **Track Minimap Building Brick with Time Loss Color Coding (`TrackMinimap.render`)**:
  Placed directly in the report header alongside the distribution chart to visually display the track layout, the highlighted target corner, and color-code all corners based on time lost across laps (green/yellow/red gradient scale):
  ```html
  <script>
    TrackMinimap.render('#track-minimap-header', {
      league: "club100_south",
      class_name: "cadet_lw",
      track: "Llandow",
      date: "2026-08-02",
      highlight_turn: "The Dell",
      padding: 12
    });
  </script>
  ```

- **Multi-Channel Telemetry Widget (`TelemetryPlot.render`)**:
  ```html
  <script>
    TelemetryPlot.render('#telemetry-widget-turn-1', {
      session_id: "...",
      track: "...",
      date: "...",
      league: "...",
      class_name: "...",
      laps: [23, 15], // Keep under 3 laps (Benchmark vs Target Lap)
      start_m: 12.0,
      end_m: 120.0,
      channels: ["Speed", "Throttle", "Brake", "Steering Angle", "Delta Time"],
      vertical_lines: [{ distance_m: 65.0, label: "Apex T1", color: "#38bdf8", style: "dashed" }],
      highlight_ranges: [{ start_m: 45.0, end_m: 65.0, label: "Braking Zone", color: "rgba(244, 63, 94, 0.12)" }],
      lap_styles: {
        "23": { color: "#10b981", width: 2.5, label: "Lap 23 (Fastest - 43.43s)" },
        "15": { color: "#f59e0b", width: 1.5, label: "Lap 15 (Target - 44.10s)" }
      },
      annotations: [
        { distance_m: 65.0, channel: "Speed", text: "Min Speed: 61.2 vs 58.4 km/h" }
      ]
    });
  </script>
  ```

- **Interactive GPS Trajectory Widget (`TrajectoryPlot.render`)**:
  ```html
  <script type="module">
    import { TrajectoryPlot } from '/static/js/trajectory_plot.js';
    TrajectoryPlot.render('#trajectory-widget-turn-1', {
      session_id: "...",
      track: "...",
      date: "...",
      league: "...",
      class_name: "...",
      laps: [23, 15], // Keep under 3 laps
      reference_lap: 23, // Explicit benchmark reference lap (drawn in white in delta_t mode)
      start_m: 12.0,
      end_m: 120.0,
      color_mode: "delta_t", // "delta_t" (reference lap in white, other laps green/yellow/red pace delta), "speed", "accel", "pedals", or "lap"
      markers: [{ distance_m: 65.0, label: "Apex T1", color: "#38bdf8" }]
    });
  </script>
  ```
- **Reference Lap Disambiguation**:
  - `TrajectoryPlot.render` accepts `reference_lap` in three formats:
    1. **Tuple/Array (Multi-session disambiguation)**: `reference_lap: ["2026-08-02", "Llandow", "12_30_race_5_qualifying", 11]`
    2. **Object**: `reference_lap: { session_id: "12_30_race_5_qualifying", lap: 11 }`
    3. **Number** (single session): `reference_lap: 11`
  - **Default**: If omitted, it automatically defaults to the **first lap in `options.laps`** (which is always the Benchmark Reference Lap in coaching reports).
- **Time Delta Calculation**: Time deltas in report tables and annotations MUST always be calculated relative to the fastest lap present on the plot/report (the reference lap with delta = 0.000s).
- **Cross-Session Benchmark Laps**: When selecting the fastest reference lap for a corner, ALWAYS scan across ALL sessions available for that date (e.g. morning practice, afternoon practice, heat/race sessions) so the benchmark represents the driver's absolute best performance of the day.
- **GPS Coordinates for Trajectory Plots**: Spatial trajectory plots (`TrajectoryPlot.js` / `get_trajectory_plot`) MUST ALWAYS use `Longitude` and `Latitude` GPS coordinates (in degrees) as the primary spatial channels. Supports `color_mode: "delta_t"` to visually display time gained (green) or lost (red) relative to the reference lap.
- **Trajectory-Driven Input Analysis & Grounding**: When analyzing driver behavior (throttle pickup, lifts, steering inputs, oversteer corrections, braking transitions), anchor all statements in observable telemetry and line geometry (entry width, turn-in angle, apex proximity/clipping, mid-corner arc, exit positioning). If steering or pedal sensors are not recorded, treat input mechanisms as hypotheses derived from trajectory and acceleration, not direct measurements.
- **Dual Visualizations**: Reports MUST include both channel telemetry (`TelemetryPlot.js`) and spatial GPS trajectory visuals (`TrajectoryPlot.js`) for the distance crop.
- **Color-Coded Lap Numbers in Text**: Whenever lap numbers are mentioned in text (body paragraphs, executive summary, coaching takeaways, coach summary boxes, or table cells), they MUST be styled using CSS classes matching their telemetry plot trace colors: `.lap_ref` for the benchmark reference lap (e.g. `<span class="lap_ref">Lap X</span>`), and `.lap_1`, `.lap_2`, ..., `.lap_10` for target laps in sequential plot order (e.g. `<span class="lap_1">Lap Y</span>`, `<span class="lap_2">Lap Z</span>`).
- **Report Structure & Layout Requirements**:
  1. **Header & Executive Summary Grid**:
     - **Left Side**: Executive summary text. Driver name MUST be highlighted as a clickable link to their brbrdb driver page (`https://brbrdb.brbrkitten.com/drivers/<driver_name>`). Track and turn names MUST be highlighted with `.highlight-badge`. Session names MUST be highlighted as clickable links to session pages (`https://brbrdb.brbrkitten.com/telemetry/<league>/<class_name>/<date>/<track>/`).
     - **Right Side**: Interactive turn/lap time distribution chart rendered via `StatsPlot.renderTurnViolin`.
  2. **Selected Analysis Laps Table**:
     - Placed immediately below the summary header grid. Contains columns: Lap Role (pill badge), Session & Date (with session link), Lap #, Turn/Lap Time, Time Delta, and Selection Rationale & Focus. Highlights *why* each lap was selected for analysis. No telemetry plot widgets in this section.
  3. **Coaching Actions & Detailed Turn Breakdown**:
     - Detailed turn breakdown cards containing track minimap (`TrackMinimap.render`), interactive multi-channel `TelemetryPlot.js` widgets, spatial `TrajectoryPlot.js` line overlays, telemetry metrics tables, and coach's action items for the driver.

---

## References

For detailed reference files, refer to:
- [MCP Tools Reference](references/mcp_tools_reference.md)
- [Corner Sub-Agent Prompt Template](references/subagent_prompt.md)
- [HTML Report Template](references/report_template.html)
