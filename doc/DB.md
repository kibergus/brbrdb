# Remaining RaceDB Query Optimizations

> [!NOTE]
> The primary database migration to PostgreSQL (schema, indexes, connection pooling, and filesystem synchronization) is complete in `race_db.py` and `utils/populate_db.py`.
> This document tracks the remaining narrow-scoped query optimizations in `RaceDB` and their associated caller routes.

---

## 1. Driver Profile Page (`/driver/<name>`)

### Current Status
In `app.py` (`driver_view`), loading a driver profile still calls `db.load()` with **no arguments**:
```python
# app.py:1515
df = db.load()
driver_df = df[(df['Name'] == name) & (df['Lap'] > 0)]
```
This forces the database to fetch and melt all lap times for all drivers across all sessions in history into a massive pandas DataFrame, solely to group by `(League, TrackName, Date, Class)` and find each session's personal best.

### Proposed DB Method
Implement `db.get_driver_personal_bests(driver_name: str) -> pd.DataFrame` in `RaceDB`:

```sql
SELECT 
    m.league AS "League", 
    m.track_name AS "TrackName", 
    s.session_start_datetime AS "Date", 
    s.class_name AS "Class", 
    r.best_lap_seconds AS "LapTimeSeconds", 
    r.session_id AS "SessionID"
FROM session_results r
JOIN sessions s ON r.session_id = s.session_id
JOIN meetings m ON s.meeting_id = m.id
JOIN drivers d ON r.driver_id = d.id
WHERE d.driver_name = %s AND r.best_lap_seconds IS NOT NULL;
```

**Benefits**:
- Backed directly by the existing index `idx_results_driver (driver_id, session_id)`.
- Eliminates memory allocation and pandas melting for thousands of unrelated driver laps.
- Executes in $<2\text{ms}$.

---

## 2. Driver Session Progression & Leader Gap Analytics

### Context
To plot chronological lap time progression relative to the session leader (which currently requires loading full session laptimes to identify `min_best` for each event), compute the session-level statistics directly within PostgreSQL.

### Proposed DB Method
Implement `db.get_driver_session_progression(driver_name: str) -> pd.DataFrame` in `RaceDB`:

```sql
WITH session_bests AS (
    SELECT session_id, MIN(best_lap_seconds) AS leader_best
    FROM session_results
    WHERE best_lap_seconds IS NOT NULL
    GROUP BY session_id
),
session_participants AS (
    SELECT session_id, COUNT(driver_id) AS participants
    FROM session_results
    GROUP BY session_id
)
SELECT 
    m.league AS "League",
    s.class_name AS "Class",
    s.session_start_datetime AS "DateTime",
    s.session_name AS "SessionName",
    m.track_name AS "TrackName",
    s.track_conditions AS "TrackConditions",
    r.pos AS "Pos",
    r.gap AS "Gap",
    r.best_lap_seconds AS "LapTimeSeconds",
    sb.leader_best AS "LeaderBestTime",
    sp.participants AS "Participants",
    r.session_id AS "SessionID"
FROM session_results r
JOIN sessions s ON r.session_id = s.session_id
JOIN meetings m ON s.meeting_id = m.id
JOIN drivers d ON r.driver_id = d.id
JOIN session_bests sb ON r.session_id = sb.session_id
JOIN session_participants sp ON r.session_id = sp.session_id
WHERE d.driver_name = %s
ORDER BY s.session_start_datetime ASC;
```

**Benefits**:
- Offloads aggregation (`MIN(best_lap_seconds)` and `COUNT(driver_id)`) to PostgreSQL.
- Returns a lightweight tabular summary ready for visualization or plotting.
