# JavaScript Test Coverage Status & Backlog

This document tracks test coverage across frontend JavaScript modules, highlighting what has been implemented and what remains to be added.

---

## 1. Status Overview

| Module | Status | Notes |
| :--- | :---: | :--- |
| `kartsim_meeting/` | ✅ **Implemented** | Refactored into modular package; 6 Vitest suites covering all pure algorithms and interactions. |
| `trajectory_plot.js` | ✅ **Implemented** | Covered by `static/js/trajectory_plot.test.js`. |
| `meeting_socials.js` | ✅ **Implemented** | Covered by `static/js/meeting_socials.test.js` (`reloadSocialPlot`). |
| `racebox_ble.js` | ✅ **Implemented** | Covered by `static/js/racebox_ble.test.js`. |
| `telemetry_upload.js` | ✅ **Implemented** | Covered by `static/js/telemetry_upload.test.js`. |
| `utils.js` | ⚠️ **Mostly Implemented** | Core cases covered in `utils.test.js`; a few minor edge cases pending. |
| `driver.js` | ❌ **Yet to be Added** | Data grouping, rolling median smoothing, and plot URL construction. |
| `session.js` | ❌ **Yet to be Added** | `formatTime` helper and video playback control handlers. |
| `settings.js` | ❌ **Yet to be Added** | `togglePilot` DOM toggle state logic. |
| `drivers_list.js` | ❌ **Yet to be Added** | Search filtering callback. |

---

## 2. Implemented Modules (Summary)

- **`kartsim_meeting/`**: The original 1900-line monolith was split into modular components under `static/js/kartsim_meeting/`. Pure telemetry operations (`findSegmentIndex`, `getPointAtDistance`, `precalculateLapData`, `getTurnTime`, etc.) and UI interactions are verified in:
  - `telemetry.test.js`
  - `map.test.js`
  - `lap_selection.test.js`
  - `state.test.js`
  - `stats_plots.test.js`
  - `url_sync.test.js`
- **`trajectory_plot.js`**: `TrajectoryPlot.render`, SVG generation, marker interpolation, and cropping are verified in `trajectory_plot.test.js`.
- **`meeting_socials.js`**: Verified in `meeting_socials.test.js`.

---

## 3. Modules Yet to be Added (Backlog)

### `driver.js` — High Priority
The file is currently coupled to Chart.js and the DOM, but contains complex pure logic that should be extracted and tested:

#### 1. Data Grouping and Smoothing (`groupProgressionData`)
Lines 62–95 group entries by league/class, sort by date, apply a 5-point rolling median filter, and filter cross-class entries.
- **Action**: Extract to a pure function `groupProgressionData(rawData, getMedianFn)` in `driver_utils.js`.
- **Test Cases**:
  - Groups entries by `league_name + class`.
  - Sorts groups chronologically by `date_time`.
  - Correctly smooths values with rolling median window.
  - Excludes cross-class/cross-league entries from smoothing window.
  - Returns empty structure on empty input.

#### 2. Plot URL Construction (`buildPlotUrl`)
The `updateImg` logic (lines 469–485) safely merges base URLs with query parameters.
- **Test Cases**:
  - Appends query params with `?` when base URL has no query string.
  - Appends query params with `&` when query string is already present.

---

### `session.js` — Medium Priority

#### 1. `formatTime(seconds)` (Pure Function)
- **Action**: Export `formatTime` from `session.js`.
- **Test Cases**:
  - Sub-minute seconds: `45` $\rightarrow$ `'0:45'`.
  - Minutes: `90` $\rightarrow$ `'1:30'`.
  - Hours: `3661` $\rightarrow$ `'1:01:01'`.
  - Zero-padding: `61` $\rightarrow$ `'1:01'`.
  - Zero: `0` $\rightarrow$ `'0:00'`.

#### 2. Video Controls in jsdom (`stepVideo`, `togglePlay`, `stopAllOthers`)
- **Action**: Test video element currentTime manipulation and playback toggles with mock HTMLVideoElements in jsdom.

---

### `settings.js` — Low Priority

#### `togglePilot(btn)`
- **Action**: Test in jsdom that clicking `togglePilot`:
  - Adds/removes the `.disabled` class on the button.
  - Toggles the input field name between `hero_pilot` and `disabled_hero`.

---

### `drivers_list.js` — Low Priority

#### Search and Filter Callback
- **Action**: Extract filtering logic out of the `DOMContentLoaded` listener into `filterDrivers(query)`.
- **Test Cases**:
  - Hides non-matching driver cards.
  - Shows all cards when query is empty.
  - Hides letter section headers when all children are hidden.
  - Case-insensitive matching.

---

### `utils.js` — Minor Edge Cases Remaining
Existing tests in `utils.test.js` cover standard behavior. Remaining edge cases to add:
- `parseLapTime(null)` $\rightarrow$ returns `999999`.
- `parseLapTime('0:45.500')` $\rightarrow$ returns `45.5`.
- `getMedian([7])` $\rightarrow$ returns `7` (single-item array).
- `getMedian([-3, -1, -2])` $\rightarrow$ returns `-2` (negative values).
