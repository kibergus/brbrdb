---
name: telemetry_analysis
description: Analyzes karting telemetry data using MCP server tools and corner sub-agents, comparing reference benchmark vs target laps with Delta T inflection analysis and data-grounded causation chains, producing interactive commentary reports that synchronize directly with the Telemetry Viewer.
---

# Telemetry Analysis Skill

This skill provides an autonomous, turn-by-turn workflow for analyzing karting telemetry data. It combines orchestrator session inspection, corner-focused sub-agent investigations, and interactive HTML commentary report generation published via the `save_report` MCP tool.

> [!IMPORTANT]
> **Remote Telemetry Data & Reports (No Local Files or Database)**:
> All telemetry datasets, track metadata, and reports are hosted remotely and accessed **exclusively** via the `telemetry` MCP tools (`list_sessions`, `get_stats`, `save_report`, etc.).
> - Do NOT search the local workspace or filesystem for telemetry logs, CSVs, or database files.
> - Do NOT attempt to connect to any external or local database.
> - Do NOT attempt to read or write report files directly via local filesystem tools; all report publishing, reading, and editing is handled through the MCP tools (`save_report`, `read_report`, `edit_report`).

---

## Core Concept: Interactive Telemetry Viewer Commentary

Rather than creating isolated, heavyweight pages that reinvent plotting widgets from scratch, reports serve as an **interactive coach commentary companion** integrated directly inside the main **Telemetry Viewer** (displayed in the right panel's **Report** tab).

```
+-----------------------------------------------------------------------------------+
| Telemetry Viewer Dashboard                                                        |
| +-----------------------------------------------+ +-----------------------------+ |
| | Satellite Map & Trajectory Overlays           | | Right Panel: [Report Tab]   | |
| | (Synchronized kart markers & GPS lines)       | |                             | |
| |                                               | |  Coach Commentary & Insights| |
| +-----------------------------------------------+ |                             | |
| | Bottom Telemetry Channel Plots                | |  [316.5m: Braking Point]    | |
| | (Speed, Delta T, Steering, Throttle, G-Force) | |  [338.0m: Apex Scrub (70°)] | |
| |                                               | |  [Compare Lap 6 vs Lap 5]   | |
| +-----------------------------------------------+ +-----------------------------+ |
+-----------------------------------------------------------------------------------+
```

As the driver or coach reads the report commentary, clicking on interactive text triggers or buttons seamlessly manipulates the main Telemetry Viewer in real time:
- **Jumping Distance**: Moves the kart marker along the track map and positions the synchronized vertical cursor across all telemetry plots.
- **Plot Zoom / Visible Range**: Sets the visible X-axis range (in meters) on the bottom charts.
- **Map Centering**: Automatically zooms and centers the satellite map over the exact track segment or corner.
- **Lap Selection**: Switches active comparison laps in Group A (all compared laps are placed in Group A; Group B is not used).
- **Plot Visibility & Focus**: Toggles individual plots or focuses the bottom bar on specific channels (e.g. Speed + Steering + G-Force).

The reader can read the analysis, click any observation to instantly see and verify the evidence on the synchronized map and plots, and then continue exploring the telemetry data freely.

---

## Workflow Overview

```
Orchestrator Agent
 ├── 1. Data Discovery (list_sessions, get_track_info, get_pace_summary, get_aggregates, get_stats)
 ├── 2. Lap Selection (Keep <3 laps: Benchmark Reference Lap vs Target Comparison Lap)
 ├── 3. Corner Sub-Agent Delegation (Delta T inflection, causation chain with calibrated confidence)
 ├── 4. Synthesis (Identify top 1–2 actionable coaching takeaways for youth drivers)
 └── 5. Report Publishing & Iteration (save_report, read_report, edit_report MCP tools)
```

---

## Step 1: Session & Data Discovery (Orchestrator)

1. **MCP Data Loading & Python Analysis Scripts**:
   - All session lists, track metadata, pace summaries, lap statistics, and telemetry plots **MUST be loaded through MCP server tools** (`list_sessions`, `get_track_info`, `get_pace_summary`, `get_stats`, `get_telemetry_plot`, `get_trajectory_plot`).
   - Agents **are encouraged to write Python scripts to analyze or post-process data returned by the MCP tools** (e.g. filtering laps, computing delta distributions, or selecting representative target laps). Intermediate calculations or analysis scratch files may be saved if helpful, but the input data itself originates from the MCP tools rather than existing workspace files or databases.

2. **Locate Target Sessions & Audit Available Channels**:
   - Call `list_sessions(track=..., date=..., driver_name=...)` to list all sessions for the target date and track.
   - **Channel Audit**: Identify which telemetry channels are physically available (e.g. `Speed` + `accel` + `Latitude`/`Longitude` vs full sensor suites with `Steering Angle`, `Throttle`, `Brake`, `RPM`, `Slip Angle`). Note missing channels up front so conclusions never claim unmeasured data as fact.

3. **Fetch Track Metadata**:
   - Call `get_track_info(track=...)` to retrieve lap length, sector boundaries, and named turn definitions (`start_m`, `end_m`, `apexes_m`).

4. **Pace Overview & Lap Selection**:
   - Call `get_pace_summary(...)` and `get_aggregates(...)` to evaluate theoretical best lap vs actual best lap and per-turn consistency.
   - **Plot Readability Rule**: **Keep the number of laps under 3** (typically **2 laps**: 1 Benchmark Reference Lap vs 1 Target Comparison Lap) to ensure clarity.
   - **Corner Benchmark Rule**: When analyzing a specific corner (e.g. Turn 3), the benchmark lap **MUST be selected based on the fastest segment time for THAT SPECIFIC CORNER** (`turn_time_s` from `get_stats(..., turn_index=N)`), **NOT overall full lap time**.
   - **Group A Only Rule**: **All laps being compared must be placed in Group A (`lapsA`)**. Do not use Group B (`lapsB`).
   - **Opened Plots Limit Rule**: **Keep the number of simultaneously opened plots under 3** (e.g. `plot: ["steering"]` alongside Speed/Delta, or presets like `data-plots="speed,steering,delta"` or `data-plots="speed,pedals,delta"`). Opening 3 or more bottom channel plots pushes the track map off-screen and hides the kart trajectory.
   - **Purpose-Driven Triggers Rule**: **Do NOT create generic disconnected plot button bars hanging in the air** (e.g., generic standalone "Show Steering" or "Toggle Pedals" toolbars). Every button or preset must serve a clear narrative purpose tied directly to conveying a specific coaching idea (e.g., inspecting braking points with pedals, checking apex steering scrub, or verifying exit throttle commitment).
   - **Target Comparison Lap**: Select a representative median lap or a lap with a specific time loss (~0.2s–0.5s delta) to diagnose the root cause of the slowdown.
   - **Prompt Comment Rule**: **Always include the exact prompt and generation instructions** inside an HTML comment section at the top of the report (immediately following the JSON header comment). This makes the prompt generally invisible to users in the browser/viewer, while keeping reports fully auditable and reproducible.

---

## Step 2: Corner-by-Corner Investigation via Sub-Agents

For each corner being analyzed, launch a dedicated sub-agent focused on that turn index $N$:
1. Restrict analysis to the distance range `start_m` to `end_m`.
2. Inspect `get_stats(track=..., date=..., turn_index=N)` to compare:
   - Turn time ($s$)
   - Entry speed ($km/h$)
   - Minimum apex speed ($km/h$)
   - Exit speed ($km/h$)
   - Straight exit speed ($km/h$)
   - Apex maximum steering angle ($deg$, *if recorded*)

---

## Data-Grounded Causation Methodology & Confidence Tiers

All findings, diagnoses, and coaching takeaways MUST be strictly anchored in the actual telemetry channels recorded. **Never assert unmeasured physical driver actions or vehicle dynamics as factual certainty.**

### Evidence Hierarchy & Confidence Levels

1. **Direct Telemetry Evidence (High Confidence)**:
   - Directly measured and visible in available telemetry channels (e.g., speed trace drops 5 km/h earlier, Delta T steepens +0.15s between 40m–60m, GPS trajectory shows turn-in 3m earlier / apex clipped 1.2m wider).
   - **Tone**: State as objective fact (*"The data shows minimum apex speed was 4.2 km/h lower on Lap 5, accounting for +0.14s of time loss."*).

2. **Derived / Corroborated Findings (Medium Confidence)**:
   - Inferences supported by surrogate or secondary channels when a primary sensor is absent (e.g., `accel` longitudinal acceleration channel showing positive drive begins 6 meters later, indicating delayed throttle pickup even if a direct pedal position sensor is not fitted).
   - **Tone**: State the inference clearly alongside the supporting channel (*"Based on the longitudinal acceleration (`accel`) trace, positive drive was delayed by ~6m, indicating later throttle commitment."*).

3. **Hypotheses & Plausible Scenarios (Low-to-Medium Confidence)**:
   - Explanations of driver behavior or dynamics that **are not directly measured** (e.g., claiming a speed scrub was caused by "steering wheel snap / excessive steering lock" or "brake locking" when steering angle and brake pressure sensors are **not** present).
   - **Strict Rule**: You **MUST NOT** claim an unmeasured cause as a definitive fact. You may offer it as a reasoned hypothesis, but **you must explicitly communicate the confidence level and state that the channel is not recorded**.
   - **Tone / Phrasing Example**: *"Hypothesis (Medium Confidence — steering angle not recorded): The 4 km/h mid-apex speed drop without deceleration on the accel trace suggests possible tire scrub from an aggressive steering input or line pinch."*

---

## Causation Chain & Delta T Analysis Workflow

1. **Delta T Inflection Point Analysis**:
   - Inspect the **Delta Time ($\Delta T$)** channel across the corner distance.
   - Pinpoint the **exact meter marks** where $\Delta T$ begins to climb or steepens significantly (entry phase, apex phase, or exit acceleration phase).

2. **Establish the Causation Chain**:
   - Connect: Driver Control / Line Choice $\rightarrow$ Kart Dynamics / Attitude $\rightarrow$ Speed Deficit $\rightarrow$ Time Delta.
   - **Braking**: Compare braking initiation meter mark, trail-braking smoothness vs abrupt release. (Fallback: negative `accel` dips).
   - **Apex Speed & Steering**: Compare minimum apex speed (V-min) and steering lock. (Fallback: GPS arc curvature and lateral accel).
   - **Throttle Pickup**: Compare full throttle commitment point and acceleration drive off the corner. (Fallback: positive `accel` rise).
   - **Line Geometry**: Compare entry width, apex clipping distance, and exit track-out width from GPS trajectory.

---

## Communication Style for Junior Drivers (~11 Years Old)

*(Applies to commentary text written inside the generated HTML report)*

- **Friendly, Encouraging, and Enthusiastic**: Speak like an approachable, positive racing coach chatting with an 11-year-old kart racer. Keep explanations simple, vivid, and fun.
- **No Corporate Jargon**: Avoid stiff corporate phrases like "Executive Summary" (use *"The Big Picture"*, *"Pace Check"*, or *"How Much Time Can We Find?"*).
- **Positive Framing**: Frame mistakes as exciting secrets to unlocking faster laps (e.g. *"Unlocking +0.2s by letting the kart roll freely on exit!"*).
- **Relatable Karting Physics**: Explain what the kart actually feels like (*"getting back on the gas"*, *"carrying rolling speed"*, *"scrubbing speed with too much wheel angle"*, *"opening up the wheel on exit"*).
- **Speed Units Standard**: **Always use `km/h` exclusively** in all text, tables, and buttons ($v_{\text{km/h}} = v_{\text{m/s}} \times 3.6$).
- **Punchy Track Cues**: Provide 1–2 simple, visual cues easy to picture in the helmet while driving.

---

## Report Format Specification

Reports are HTML commentary companion snippets published to the viewer service via the `save_report` MCP tool.

### 1. Structure
A report file consists of three parts:
1. **Header**: A top comment block containing a JSON configuration object specifying the meeting parameters and default UI viewer state.
2. **Invisible Prompt / Generation Context Comment**: An HTML comment block (`<!-- Prompt / Generation Context: ... -->`) containing the exact prompt/instructions given to build the report. Because it is enclosed in an HTML comment, it is generally invisible to users in the browser/viewer, but remains fully accessible in the source for auditing and reproduction.
3. **Body**: Clean HTML markup containing the commentary text, stat cards, evidence items, and clickable interactive trigger buttons.

```html
<!-- {
  "title": "Turn 3 Hairpin Coaching Guide",
  "league": "kartsim",
  "class_name": "iame_waterswift_restricted_cadet_uk",
  "date": "2026-08-21",
  "track": "Clay Pigeon",
  "session_id": "18_09_practice",
  "state": {
    "tab": "map",
    "sort": "turn",
    "turn": 2,
    "lapsA": ["18_09_practice-6", "18_09_practice-5"],
    "visA": true,
    "xlim": [280.0, 420.0],
    "dist": 340.0,
    "delta": true,
    "speed": true,
    "plot": ["steering"],
    "tcol": "pedals",
    "rtab": "report",
    "sidePanelWidth": 420
  }
} -->

<!--
Prompt / Generation Context:
Analyze Turn 3 (Hairpin) at Clay Pigeon for 2026-08-21 session 18_09_practice.
Compare Benchmark Lap 6 vs Target Comparison Lap 5.
Investigate braking point, apex steering scrub, and throttle commitment on exit.
Keep plots under 3 channels and write friendly coaching advice for an 11-year-old junior driver.
-->

<div class="report-content">
  <div>
    <h2>Turn 3 Hairpin Coaching Guide</h2>
    <p>
      Comparing Benchmark <button class="telemetry-jump-btn" data-laps="18_09_practice-6,18_09_practice-5" data-dist="316.5" data-plots="speed,pedals,delta">Lap 6 (6.243s)</button> 
      vs Target Comparison <button class="telemetry-jump-btn" data-laps="18_09_practice-6,18_09_practice-5" data-dist="319.5" data-plots="speed,pedals,delta">Lap 5 (6.426s)</button>.
    </p>
  </div>

  <div>
    <h3>1. The Big Picture & Pace Check</h3>
    <p>
      There is an awesome <strong>+0.183s</strong> to gain through the Hairpin by braking just a little earlier so the front tyres don't scrub, letting you get back on full throttle sooner on exit!
    </p>
    <div class="report-stats-grid">
      <div class="report-stat-card">
        <span class="label">Benchmark Turn Time</span>
        <span class="value">6.243s</span>
      </div>
      <div class="report-stat-card">
        <span class="label">Lap 5 Turn Time</span>
        <span class="value">6.426s</span>
      </div>
      <div class="report-stat-card">
        <span class="label">Corner Delta</span>
        <span class="value delta-negative">+0.183s</span>
      </div>
      <div class="report-stat-card">
        <span class="label">Apex V-Min</span>
        <span class="value">41.2 km/h</span>
      </div>
    </div>
  </div>

  <div>
    <h3>2. Causation Chain & Evidence</h3>
    <ul>
      <li>
        <span class="evidence-tag evidence-high">Direct Evidence</span>
        <strong>Braking Point:</strong> On Benchmark Lap 6, braking begins smoothly at 
        <button class="telemetry-jump-btn" data-dist="316.5" data-xlim="290,380">316.5m</button>. On Lap 5, the driver brakes 4m later and rushes the entry.
      </li>
      <li>
        <span class="evidence-tag evidence-high">Direct Evidence</span>
        <strong>Steering Lock:</strong> Maximum steering angle reached 70.2° on Lap 5 vs only 48.6° on Lap 6 at 
        <button class="telemetry-jump-btn" data-dist="338.0" data-xlim="320,370" data-plots="speed,steering">338.0m (Apex - View Steering)</button>, causing heavy understeer scrub.
      </li>
      <li>
        <span class="evidence-tag evidence-medium">Derived Evidence</span>
        <strong>Throttle Commitment:</strong> Full throttle pickup was delayed by 8 meters to 
        <button class="telemetry-jump-btn" data-dist="357.0" data-xlim="340,410">357.0m</button> while waiting for the front axle to grip.
      </li>
    </ul>
  </div>

  <div>
    <h3>3. Actionable Coaching Takeaways</h3>
    <ol>
      <li><strong>Earlier Brake Initiation:</strong> Begin braking at the 316m marker to avoid overspeeding into the apex.</li>
      <li><strong>Smooth Steering Release:</strong> Limit maximum steering angle to ~50° to prevent washing out the front end.</li>
      <li><strong>Straight-Line Drive:</strong> Open the wheel earlier on exit to commit to 100% throttle by 349m.</li>
    </ol>
  </div>
</div>
```

---

## Report Publishing & Management via MCP Tools

External analysis agents publish and iterate on reports using the following server-provided MCP tools:

### 1. `save_report(content: str, report_id: str | None = None)`
- **Creating a new report**: Call `save_report(content="...")` with `report_id=None` (or omitting `report_id`). The server generates a random unguessable UUIDv4, persists the report on the server, and records your author identity. Returns `{ "status": "success", "report_id": "<uuid>", "filename": "<uuid>.html", "url": "https://brbrdb.brbrkitten.com/telemetry/report/<uuid>" }`.
- **Overwriting an existing report**: Call `save_report(content="...", report_id="<uuid>")`. `report_id` **must** be a valid UUID and can only be updated by the original author.

> [!IMPORTANT]
> **Mandatory User Output & Link Rule**:
> - After calling `save_report` to generate or update a report, you **MUST ALWAYS** give the user the full absolute URL: `https://brbrdb.brbrkitten.com/telemetry/report/<uuid>` as a clickable markdown link. Never use a relative path or omit the domain; always include the full `https://brbrdb.brbrkitten.com` URL!
> - **Do NOT duplicate the report in your chat response**: All detailed breakdowns, evidence items, data tables, and in-depth commentary belong strictly inside the published HTML report. Duplicating the report in chat output distracts the user.
> - **Keep the chat response minimal**: Limit your final response to the clickable report URL accompanied by at most a concise 1–2 sentence high-level takeaway. All details must remain in the report.

### 2. `read_report(report_id: str, start_line: int | None = None, end_line: int | None = None)`
- Reads back an existing report by UUID, optionally slicing line ranges (1-indexed) mirroring the `view_file` API.

### 3. `edit_report(report_id: str, target_content: str, replacement_content: str, start_line: int | None = None, end_line: int | None = None, allow_multiple: bool = False)`
- Surgically replaces sections of text in an existing report by UUID without rewriting the entire file, mirroring `replace_file_content`. Restricted to the report author. Always provide the updated report URL to the user after editing.

---

## Interactive Data Attributes & JavaScript API

### Data Attributes for HTML Elements
Any `<button>`, `<a>`, or text `<span>` in the report body can trigger viewer updates:

| Attribute | Value Example | Action in Telemetry Viewer |
|:---|:---|:---|
| `data-dist` | `"316.5"` | Seeks the distance cursor and updates map/chart markers |
| `data-range` / `data-xlim` | `"280,420"` | Sets the visible meter range in the bottom plot |
| `data-map-range` / `data-center-map` | `"280,420"` | Centers/fits the satellite map over that track segment (meters) |
| `data-focus-range` | `"280,420"` | Sets plot visible range **AND** centers the map over that segment |
| `data-turn` | `"2"` | Switches turn sorting and focuses on that turn index |
| `data-laps` / `data-laps-a` | `"18_09_practice-6,18_09_practice-5"` | Sets Group A lap selection (all compared laps in Group A) |
| `data-toggle-plot` | `"steering"` | Toggles visibility of a bottom plot channel (`steering`, `speed`, `delta`, `pedals`, `gforce`, etc.) |
| `data-plots` | `"speed,steering,gforce"` | Sets exact open bottom plots and hides all others (`"none"` hides all) |
| `data-tab` | `"map"` or `"stats"` | Switches main dashboard view |
| `data-rtab` | `"report"`, `"cornering"` | Switches right panel tab |

### JavaScript API (`window.Telemetry`)
From scripts inside the report iframe, programmatic triggers are available:
```javascript
// Seek distance cursor
Telemetry.jump({ dist: 316.5, xlim: '290,380', plots: 'speed,steering' });

// Set visible range in bottom plot
Telemetry.setRange(280, 420);

// Center map over track segment
Telemetry.focusMap(280, 420);

// Set plot range AND center map
Telemetry.focusRange(280, 420);

// Toggle a single bottom plot channel
Telemetry.togglePlot('steering');

// Set visible bottom plots
Telemetry.setPlots(['speed', 'steering', 'gforce']);

// Select comparison laps in Group A
Telemetry.selectLaps(['18_09_practice-6', '18_09_practice-5']);
```

---

## Styling Classes (`telemetry_report.css`)

All commentary reports are styled with `/static/css/telemetry_report.css`:
- `.report-content`: Outer container with standard padding and font styling.
- `.report-badge`: Pill badge for report category (e.g. `Coaching Deep-Dive`).
- `.report-stats-grid`: Grid layout for key corner metrics.
- `.report-stat-card`: Individual stat box with `.label` and `.value` (`.delta-negative` / `.delta-positive`).
- `.evidence-tag`: Evidence tag badge (`.evidence-high` for direct data, `.evidence-medium` for inferred, `.evidence-low` for hypotheses).
- `.telemetry-jump-btn`: Clean interactive pill button for meter jumps, plot toggles, and lap switches.

---

## References

For templates and prompt structures:
- [Corner Sub-Agent Prompt Template](references/subagent_prompt.md)
- [HTML Report Template](references/report_template.html)
- [MCP Tools Reference](references/mcp_tools_reference.md)
