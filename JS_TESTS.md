# JavaScript Test Coverage Improvements

## Current State

Only `utils.js` has test coverage (`utils.test.js`). The remaining six JS modules
(`kartsim_meeting.js`, `driver.js`, `session.js`, `settings.js`, `drivers_list.js`,
`meeting_socials.js`) have **zero tests**.

The test infrastructure (Vitest) is already configured — the gap is purely authorship.

---

## General Strategy

Most JS in this project is tightly coupled to the DOM and to third-party globals
(`google.maps`, `Plotly`, `Chart`, `window.KART_CONFIG`, etc.). The highest-value
approach is:

1. **Extract pure functions** into modules — test the logic without any DOM/browser
   dependency.
2. **Test DOM-manipulating functions** using Vitest's `jsdom` environment for functions
   that are small enough to be worth it.
3. **Skip or lightly stub** functions that are purely integration glue (e.g., `initMap`,
   `renderExpandablePlots`), noting them in tests as "integration-only".

> Add `"environment": "jsdom"` to `vitest.config.js` (or per-file via
> `// @vitest-environment jsdom`) to enable DOM APIs in tests.

---

## `utils.js` — Already Tested

**Coverage is good.** The following edge cases are currently missing:

| Function | Missing Cases |
|---|---|
| `parseLapTime` | `null` input, `0:45.5` (zero-minute), extra colons, NaN-producing strings |
| `getMedian` | Single-element array, negative numbers |

**Suggested additions in `utils.test.js`:**

```js
// parseLapTime edge cases
it('parses 0:45.5 correctly', () => expect(parseLapTime('0:45.500')).toBe(45.5));
it('handles null input', () => expect(parseLapTime(null)).toBe(999999));

// getMedian edge cases
it('returns single element for 1-item array', () => expect(getMedian([7])).toBe(7));
it('handles negative numbers', () => expect(getMedian([-3, -1, -2])).toBe(-2));
```

---

## `kartsim_meeting.js` — Highest Priority

This is by far the largest file (1942 lines) and contains the most complex business
logic. Several functions are **pure** (no DOM/API dependency) and are ideal test targets.

### Refactoring Required First

The file currently has no `export` statements. To make functions testable:

- **Preferred:** Extract pure logic into a new `kartsim_utils.js` module that
  `kartsim_meeting.js` imports. Test that module.
- **Simpler:** Add named exports for pure functions at the bottom behind an
  `if (typeof module !== 'undefined')` guard.

### Functions to Extract and Test

#### `findSegmentIndex(points, targetDist)` — Binary Search

This is pure and critical — it underlies all distance-based telemetry operations.

```js
// kartsim_utils.test.js
describe('findSegmentIndex', () => {
    const pts = [
        { dist: 0 }, { dist: 10 }, { dist: 20 }, { dist: 30 }
    ];

    it('finds the correct segment for a mid-range value', () =>
        expect(findSegmentIndex(pts, 15)).toBe(1));

    it('finds segment at exact start boundary', () =>
        expect(findSegmentIndex(pts, 0)).toBe(0));

    it('finds segment at exact end boundary', () =>
        expect(findSegmentIndex(pts, 30)).toBe(2));

    it('returns -1 for out-of-range distance', () =>
        expect(findSegmentIndex(pts, 100)).toBe(-1));

    it('returns -1 for empty array', () =>
        expect(findSegmentIndex([], 5)).toBe(-1));

    it('returns -1 for single-element array', () =>
        expect(findSegmentIndex([{ dist: 0 }], 0)).toBe(-1));
});
```

#### `getPointAtDistance(lap, distance)` — Interpolation

Tests linear interpolation of all numeric fields between two telemetry points.

```js
describe('getPointAtDistance', () => {
    const lap = {
        points: [
            { dist: 0,  speed: 50, throttle: 100, brake: 0 },
            { dist: 10, speed: 70, throttle: 80,  brake: 0 },
            { dist: 20, speed: 90, throttle: 60,  brake: 10 },
        ]
    };

    it('interpolates speed at midpoint', () => {
        const p = getPointAtDistance(lap, 5);
        expect(p.speed).toBeCloseTo(60);
    });

    it('returns first point when distance is below range', () =>
        expect(getPointAtDistance(lap, -5)).toEqual(lap.points[0]));

    it('returns last point when distance is above range', () =>
        expect(getPointAtDistance(lap, 999)).toEqual(lap.points[2]));

    it('returns null for empty lap', () =>
        expect(getPointAtDistance({ points: [] }, 5)).toBeNull());
});
```

#### `precalculateLapData(lap)` — Braking Detection

```js
describe('precalculateLapData', () => {
    it('detects a braking onset (brake > 5 after brake <= 5)', () => {
        const lap = {
            points: [
                { dist: 10, brake: 0 },
                { dist: 20, brake: 0 },
                { dist: 30, brake: 80 },  // onset here
                { dist: 40, brake: 90 },
            ]
        };
        precalculateLapData(lap);
        expect(lap.brakingPoints).toEqual([30]);
    });

    it('ignores consecutive high-brake points (not a new onset)', () => {
        const lap = {
            points: [
                { dist: 0, brake: 80 },
                { dist: 10, brake: 90 },
            ]
        };
        precalculateLapData(lap);
        expect(lap.brakingPoints).toEqual([]);
    });

    it('handles lap with no points gracefully', () => {
        const lap = {};
        precalculateLapData(lap);
        expect(lap.brakingPoints).toEqual([]);
    });
});
```

#### `getBrakingPointsInRange(lap, centerDist, range)`

```js
describe('getBrakingPointsInRange', () => {
    it('returns braking points within range', ...);
    it('returns empty array when no braking points are near', ...);
    it('handles wrap-around at start/end of track', ...);
});
```

> **Note:** `getBrakingPointsInRange` reads the module-level `trackData` global. Inject
> it as a parameter, or expose a `setTrackData(data)` test helper, before testing.

#### `getSectorTime` and `getTurnTime` — Simple Accessors

```js
describe('getSectorTime', () => {
    it('returns the correct sector time by index', () =>
        expect(getSectorTime({ sector_times: [10.1, 9.8, 11.2] }, 1)).toBe(9.8));

    it('returns null when sector_times is missing', () =>
        expect(getSectorTime({}, 0)).toBeNull());

    it('returns null for an out-of-range index', () =>
        expect(getSectorTime({ sector_times: [10] }, 5)).toBeNull());
});

// Same structure for getTurnTime
```

---

## `driver.js` — Medium Priority

Most of `driver.js` is tightly coupled to `Chart.js` and the DOM. However, the
**data grouping and smoothing logic** (lines 62–95) is complex, re-implements a
rolling median, and has non-obvious cross-class filtering. It should be extracted.

### Suggested Extraction: `groupProgressionData(rawData)`

Extract the grouping, sorting, and smoothing into a pure function in `driver_utils.js`:

```js
export function groupProgressionData(rawData, getMedianFn) { ... }
```

Then test:

```js
describe('groupProgressionData', () => {
    it('groups entries by league_name + class', ...);
    it('sorts each group by date_time', ...);
    it('smooths values using a 5-point window median', ...);
    it('only includes same orig_class/orig_league entries in the smoothing window', ...);
    it('sets isVisible=false for kartsim entries by default', ...);
    it('returns an empty object for empty input', ...);
});
```

### URL Construction in `updateImg`

The `updateImg` closure (lines 469–485) joins base URLs with params and avoids
double `?`. Once extracted:

```js
describe('buildPlotUrl', () => {
    it('appends params with ? when base URL has no query string', ...);
    it('appends params with & when base URL already has a query string', ...);
});
```

---

## `session.js` — Medium Priority

### `formatTime(seconds)` — Easy Win

This function is fully pure and needs only an `export` statement:

```js
// session.test.js
import { formatTime } from './session.js';

describe('formatTime', () => {
    it('formats sub-minute as M:SS', () => expect(formatTime(45)).toBe('0:45'));
    it('formats minutes correctly',   () => expect(formatTime(90)).toBe('1:30'));
    it('formats hours',               () => expect(formatTime(3661)).toBe('1:01:01'));
    it('zero-pads seconds',           () => expect(formatTime(61)).toBe('1:01'));
    it('handles 0',                   () => expect(formatTime(0)).toBe('0:00'));
});
```

### `stepVideo` / `togglePlay` / `stopAllOthers`

These require a jsdom environment with a mock `HTMLVideoElement`:

```js
// @vitest-environment jsdom
describe('stepVideo', () => {
    it('advances video currentTime by the given step', () => {
        document.body.innerHTML = '<video id="v1"></video>';
        const video = document.getElementById('v1');
        video.currentTime = 10;
        stepVideo('v1', 0.5);
        expect(video.currentTime).toBe(10.5);
    });

    it('does nothing for an invalid id', () => {
        expect(() => stepVideo('no-such-id', 1)).not.toThrow();
    });
});
```

---

## `settings.js` — Lower Priority

`togglePilot` has meaningful toggle logic:

```js
// @vitest-environment jsdom
describe('togglePilot', () => {
    it('disables an active pilot', () => {
        document.body.innerHTML = `
            <div>
                <input name="hero_pilot">
                <button class="btn active"></button>
            </div>`;
        const btn = document.querySelector('button');
        togglePilot(btn);
        expect(btn.classList.contains('disabled')).toBe(true);
        expect(document.querySelector('input').name).toBe('disabled_hero');
    });

    it('re-enables a disabled pilot', () => { ... });
});
```

---

## `drivers_list.js` — Lower Priority

The search/filter logic is straightforward to test with jsdom, but requires
extracting the filter callback first:

```js
// @vitest-environment jsdom
describe('driver search filtering', () => {
    it('hides non-matching driver cards', ...);
    it('shows all cards when query is empty', ...);
    it('hides entire letter sections with no visible cards', ...);
    it('is case-insensitive', ...);
});
```

> **Refactoring needed:** The filter logic lives inside a `DOMContentLoaded` callback.
> Extract it into an exported `filterDrivers(query)` function.

---

## `meeting_socials.js` — Lower Priority

`reloadSocialPlot` strips and rebuilds URL params. Once extracted:

```js
describe('reloadSocialPlot URL construction', () => {
    it('strips existing maxy and t params before re-adding', ...);
    it('preserves other query params from original URL', ...);
    it('appends a fresh timestamp t param', ...);
});
```

---

## Summary

| File | Extractable Pure Logic | jsdom DOM Tests | Refactoring Needed |
|---|---|---|---|
| `utils.js` | Already exported | — | Minor edge cases |
| `trajectory_plot.js` | `TrajectoryPlot.render`, SVG builder, cropping, marker interpolation | `trajectory_plot.test.js` (6 tests) | Fully tested module |
| `kartsim_meeting.js` | `findSegmentIndex`, `getPointAtDistance`, `precalculateLapData`, `getSectorTime`, `getTurnTime` | — | Extract to module |
| `driver.js` | Data grouping & smoothing, URL builder | — | Extract to module |
| `session.js` | `formatTime` | `stepVideo`, `togglePlay` | Add exports |
| `settings.js` | — | `togglePilot` | None |
| `drivers_list.js` | — | Search filter | Extract callback |
| `meeting_socials.js` | URL building | — | Extract URL logic |

## Recommended Next Steps (Ordered by ROI)

1. **Immediate (no refactoring):** Add missing edge-case tests to `utils.test.js`.
2. **Completed:** Implemented `static/js/trajectory_plot.js` and full Vitest suite in `static/js/trajectory_plot.test.js`.
3. **Low effort:** Add `export` to `formatTime` in `session.js` → write `session.test.js`.
4. **Medium effort:** Extract the five pure functions from `kartsim_meeting.js` into
   `kartsim_utils.js`. This is the highest-value change given the complexity.
5. **Medium effort:** Extract progression data pipeline from `driver.js` into
   `driver_utils.js`.
6. **Config:** Add `environment: 'jsdom'` as the Vitest default to avoid per-file
   annotations.

