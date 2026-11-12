# Session Upload API

The server supports two interfaces for uploading race sessions.

## Authentication

All ingestion endpoints require authentication via standard API Key tokens. Clients must provide their API key using the `X-API-Key` HTTP header:

```http
X-API-Key: <your_api_key_here>
```

Upload access is locked on a per-driver basis. If a driver name in the upload payload is not permitted under the corresponding API key, the server rejects the upload with a `403 Forbidden` response.

## Error Handling

All endpoints return errors in JSON format with a `4xx` or `5xx` HTTP status code. The response body contains an `error` key explaining the reason for the failure:

```json
{
    "error": "Detailed error message explaining the failure."
}
```

## Unary Ingestion (`/api/upload/files`)

A convenient endpoint for uploading sessions for which only lap times are known. May contain data for many drivers.

- **HTTP Method**: `POST`
- **Content Type**: `multipart/form-data`
- **Request Parameters**:
  - `metadata` (File, JSON format): The metadata block of the session.
  - `results` (File, CSV format): The leaderboards, laps, and driver session summaries.
  - `penalties` (File, CSV format, Optional): Penalty logs for the session.

- **Example Response (200 OK)**:
  ```json
  {
      "message": "Files uploaded and session imported successfully.",
      "session_id": "2026_05_10_rowrah_14_30_practice"
  }
  ```

### Metadata JSON (`metadata.json`)

The metadata payload defines the session configuration and context.

#### Schema Fields

| Field Name | Type | Required | Description | Example |
| :--- | :--- | :---: | :--- | :--- |
| `league` | String | **Yes** | The league or identifier for the simulation source. | `"kartsim"` |
| `class_name` | String or Array | **Yes** | The engine/kart classification(s) for the session. | `"cadet"` or `["senior"]` |
| `track_name` | String | **Yes** | The name of the track. | `"Rowrah"` |
| `session_name` | String | **Yes** | The category or type of the session. | `"Practice"`, `"Qualifying"`, `"Final"` |
| `session_start_datetime`| String | **Yes** | Start time in `"YYYY-MM-DD HH:MM"`, `"YYYY-MM-DD HH:MM:SS[.fff]"`, or ISO 8601 with timezone (e.g. `"2026-10-10T11:34:25.148+00:00"` or `"...Z"`). Timezone-aware inputs are automatically converted to track-local naive time. | `"2026-05-10 14:30"`, `"2026-10-10 12:34:25"`, or `"2026-10-10T11:34:25.148+00:00"` |
| `driver_name` | String | No | Optional driver name (not required; driver names are validated against ACL permissions directly from results CSV). | `"Driver A"` |
| `short_name` | String | No | Shorthand notation of the session name. | `"Practice"`, `"Q"`, `"F"` |
| `meeting_name` | String | No | The overall race meeting name (defaults to track name).| `"Rowrah Practice"` |
| `track_conditions` | String | No | General condition of the track surfaces. | `"Dry"`, `"Wet"`, `"Damp"` |
| `temperature` | Float/String | No | Temperature of the venue. | `21.5` or `"21.5C"` |
| `weather` | String | No | General atmospheric weather state. | `"Sunny"`, `"Dry"`, `"Rain"` |
| `distance_to_rear_axle` | Float | No | Distance from rear axle center to camera in meters. | `1.5` |
| `alphatiming_url` | String | No | Reference link to Alphatiming or session results page. | `"https://..."` |

##### Accepted Datetime Formats & Timezone Semantics
1. **`YYYY-MM-DD HH:MM`** (minute-resolution, naive, track-local time).
2. **`YYYY-MM-DD HH:MM:SS[.fff]`** (seconds-bearing, naive, track-local time). Fractional seconds are trimmed to whole seconds.
3. **ISO 8601 with timezone offset or `Z`** (e.g. `2026-10-10T11:34:25.148+00:00`, `2026-10-10T11:34:25Z`). The server converts the timestamp to the track's local IANA timezone (using track configuration or track lookup, falling back to `Europe/London`).

##### Persistence of High-Resolution Start Times (Seconds Preservation)
When an upload updates an existing session (e.g. final results uploaded after partial live timing):
- If the incoming value has **minute resolution only** (no seconds in input) and is within 5 minutes of the stored value, the existing high-resolution start time (including seconds) is preserved.
- If the incoming value has **minute resolution only** and differs by 5 minutes or more, it is treated as a genuine change and overwrites.
- If the incoming value contains **seconds**, it always overwrites with the latest high-resolution value.

#### Example `metadata.json`
```json
{
    "league": "kartsim",
    "class_name": "cadet",
    "track_name": "Rowrah",
    "session_name": "Practice",
    "short_name": "Practice",
    "session_start_datetime": "2026-05-10 14:30:25",
    "driver_name": "Driver A",
    "track_conditions": "Dry",
    "temperature": 18.0,
    "weather": "Dry"
}
```

### Results CSV (`results.csv`)

Holds the summary standings and lap times of all drivers participating in a session.

#### Expected Columns

| Column Name | Required | Description | Format / Example |
| :--- | :---: | :--- | :--- |
| `Name` | **Yes** | The unique name of the driver/pilot. | `"Driver A"` |
| `Pos` | No | Final finished or standing position. Supports integers and codes. | `1` or `"DNS"`, `"DNF"`, `"DSQ"` |
| `Positions Gained`| No | The number of positions gained during the session. | `2` or `-1` |
| `Gap` | No | Time interval behind the leader. | `"+1.234"` or `"+1 Lap"` |
| `ChampPoints` | No | Championship points awarded (also accepts `CPts` or `Points`).| `25` |
| `Lap 0` | No | Out-lap / warmup laptime. | `"1:12.345"` or `"54.123"` |
| `Lap 1` ... `Lap N`| No | High-resolution sector or lap time strings. | `"51.982"` or `"1:02.112"` |

> [!NOTE]
> Lap time columns matching the `Lap <number>` pattern (e.g., `Lap 1`, `Lap 2`) are parsed and converted to seconds. Invalid or missing laps should be left blank.

---

### Penalties (`penalties.csv` - Optional)

#### Expected Columns

| Column Name | Required | Description | Example |
| :--- | :---: | :--- | :--- |
| `Name` | **Yes** | The exact name of the penalized driver (matching the Results CSV). | `"Driver A"` |
| `Penalty` | **Yes** | Description of the penalty. The server parses this string for rules. | `"+5 Seconds"`, `"+1 Position"` |

#### Automatic Penalty Parsing Logic
The server scans the `Penalty` column using regular expressions to apply the following effects:
* **Position Penalties**: e.g., `"+1 Position"` parses as `1.0` positions added.
* **Time Penalties**: e.g., `"+5 Seconds"` parses as `5.0` seconds added.
* **Exclusions**: Any string containing `"Excluded"` or `"Disqualified"` sets the exclusion flag.
* **Lap Deletions**: e.g., `"Best 1 laptimes deleted"` or `"Best laptime deleted"` identifies the driver's fastest valid lap(s) and marks them deleted in the database.

## Telemetry Streaming (`/api/upload/stream`)

Detailed single-driver telemetry (e.g. from a simulator, logger, or auxiliary biometric/eye-tracking sensors) is streamed to `POST /api/upload/stream`. The server auto-detects the payload format from the initial bytes of the decompressed stream, supporting both the **BRT1 binary streaming protocol** (recommended) and **streamed RaceTools CSV** (legacy/text fallback).

- **HTTP Method**: `POST`
- **Content Type**: `application/octet-stream` (BRT1 binary) or `text/csv` (CSV fallback)
- **Request Headers**:
  - `X-API-Key`: Required for authentication. Session metadata is carried inside the stream header rather than HTTP headers.
  - `Content-Encoding`: Optional. Supports `br` (or `brotli`), `gzip`, and `deflate`. Streams can be compressed incrementally on the fly; the server decompresses in a streaming fashion without buffering the full request in RAM.
- **Request Limits & Flush Behavior**:
  - **Maximum Size**: Strict limit of 200 MB (`MAX_METADATA_SIZE`) applied to both raw incoming bytes and decompressed stream data (`400 Bad Request` if exceeded).
  - **Live Flush Interval**: Incoming samples stream directly into append-only `.brt1` segment files on disk (zero intermediate CSV files are written). For primary streams, session leaderboards and database metadata are updated every **15 seconds** and on stream completion.
- **Example Response (`200 OK`)**:
  ```json
  {
      "message": "Session completed and imported successfully.",
      "session_id": "2026_05_10_rowrah_14_30_practice"
  }
  ```
  When uploading an auxiliary stream, the response also echoes `"stream": "<stream_name>"`.

---

### 1. Primary vs. Auxiliary Streams (`stream` Metadata Field)

A single `(Session, Driver)` can receive one **primary** telemetry stream and any number of asynchronous **auxiliary** streams (e.g. EEG headsets, eye trackers, or external sensors running at independent sample rates):

| Stream Role | `stream` Metadata Value | Requirements & Server Behavior |
| :--- | :--- | :--- |
| **Primary Stream** | Omitted **or** `""` (empty string) | **Default (100% backward-compatible with existing clients).** Carries core vehicle/kart telemetry (and any extra channels if the client sends everything in one stream). Updates session leaderboards and lap summaries. Requires geo-spatial coordinates (`Latitude` & `Longitude`, or local `x` & `z`) for lap/track projection. |
| **Auxiliary Stream** | Lowercase slug matching `^[a-z0-9][a-z0-9_-]{0,63}$` (e.g. `"eeg_muse"`, `"eye_tobii"`) | Carries `Time` plus one or more arbitrary sensor channels at any cadence. **Must not** be `"primary"` (rejected with `400 Bad Request`). Does **not** require GPS/Speed/Lap channels and may start before, during, or after the primary stream. |

> [!IMPORTANT]
> **Convention**: Primary streams always omit `stream` or set `"stream": ""`. Setting `"stream": "primary"` is explicitly rejected so there is never ambiguity between primary and named auxiliary streams.

All streams coming from the same host share the session clock (`Time`), without requiring client-side sample-rate synchronization or server-side handler mutexes.

---

### 2. Wire Format A: BRT1 Binary Stream (Recommended)

If the first 7 decompressed bytes match the magic chunk `04 00 01 42 52 54 31`, the server parses the upload as a **BRT1 (Binary Race Telemetry v1)** stream. Every chunk uses a 3-byte little-endian header:

```text
+-------------------------------------------------------------------------------+
| chunk_len (2B, uint16_le) | chunk_type (1B, uint8) | payload (chunk_len B)    |
+-------------------------------------------------------------------------------+
```

#### Chunk Types

| Type | Name | Payload Layout | Description |
| :---: | :--- | :--- | :--- |
| `0x01` | **Magic** | `4` bytes: ASCII `"BRT1"` (`42 52 54 31`) | Must be the very first chunk in the stream. |
| `0x02` | **Metadata JSON** | UTF-8 JSON (`{"metadata": {...}, "channels": [...]}`) | Must immediately follow `0x01`. Declares session metadata and the ordered list of $M$ channels with positive quantization scales. |
| `0x03` | **Baseline / Keyframe** | $M \times 8$ bytes (`float64_le[M]`) | Sets exact IEEE-754 `float64` values for all $M$ channels (use IEEE `NaN` for missing values). Sent on initial sample, when any channel transitions from `NaN` to a finite value, or whenever a step exceeds `±32767 * scale`. |
| `0x04` | **Telemetry Deltas** | $N \times M \times 2$ bytes (`int16_le[N * M]`, $N \ge 1$) | One or more samples of 16-bit signed quantized deltas relative to the running channel values: $\Delta q = \mathrm{round}((v_i - v_{i-1}) / \mathrm{scale})$. `-32768` (`0x8000`) encodes `NaN`; finite deltas lie in `[-32767, +32767]`. |
| `0xFF` | **End of Stream (EOS)** | `0` bytes (empty) | Signals clean completion of the stream segment. |

#### `0x02` Metadata JSON Structure & Channel Scales

- **`Time` Channel Semantics in BRT1**: Values of the `Time` channel are floating-point **seconds elapsed relative to the session start (`Date` + `Time`)** declared in `metadata` (can be negative if an auxiliary sensor starts recording slightly before session start).
- **Arbitrary Positive `scale`**: Each channel entry `{"name": "<Channel>", "scale": <float>}` must specify a positive finite `scale`. Decoders strictly read `scale` from headers and never assume hard-coded constants.

##### Example: Primary Stream `0x02` Payload (`"stream"` omitted or `""`)
```json
{
  "metadata": {
    "Track name": "Rowrah",
    "Date": "2026-05-10",
    "Time": "14:30:00",
    "Driver name": "Driver A",
    "League": "kartsim",
    "Class": "cadet",
    "Session": "Practice",
    "Distance to rear axle": 1.5,
    "Conditions": "Dry"
  },
  "channels": [
    {"name": "Time", "scale": 0.001},
    {"name": "Latitude", "scale": 1e-7},
    {"name": "Longitude", "scale": 1e-7},
    {"name": "Speed", "scale": 0.01},
    {"name": "Throttle", "scale": 0.1},
    {"name": "Brake", "scale": 0.1},
    {"name": "Steering", "scale": 0.05},
    {"name": "GForceLat", "scale": 0.001},
    {"name": "GForceLon", "scale": 0.001},
    {"name": "GForceVert", "scale": 0.001},
    {"name": "Yaw Rate", "scale": 0.001},
    {"name": "Lap", "scale": 1.0}
  ]
}
```

##### Example: Auxiliary Sensor Stream `0x02` Payload (`"stream": "eeg_muse"`)
```json
{
  "metadata": {
    "Track name": "Rowrah",
    "Date": "2026-05-10",
    "Time": "14:30:00",
    "Driver name": "Driver A",
    "League": "kartsim",
    "Class": "cadet",
    "Session": "Practice",
    "stream": "eeg_muse"
  },
  "channels": [
    {"name": "Time", "scale": 0.0001},
    {"name": "AlphaPower", "scale": 0.001},
    {"name": "BetaPower", "scale": 0.001},
    {"name": "ThetaBetaRatio", "scale": 0.001}
  ]
}
```

See [BINARY_UPLOAD_PROTOCOL.md](./BINARY_UPLOAD_PROTOCOL.md) for byte-level framing details and typical quantization scales.

---

### 3. Wire Format B: Streamed CSV (Legacy / Text Fallback)

If the stream does not start with the BRT1 magic bytes, the server parses it as UTF-8 CSV and transcodes rows on the fly into `.brt1` binary segments on disk.

The CSV stream must begin with a `Key,Value` metadata header block, followed by a **single blank line**, followed by the column header row and telemetry data rows:

```csv
Track name,Rowrah
Date,2026-05-10
Time,14:30:00
Driver name,Driver A
League,kartsim
Class,cadet
Session,Practice

Time,Latitude,Longitude,Speed,Throttle,Brake,Steering,GForceLat,GForceLon,GForceVert,Lap
2026-05-10T14:30:00.000Z,54.55100,-3.44200,10.0,85.0,0.0,1.5,0.12,0.35,1.01,1
```

In CSV streams, the `Time` column may be formatted either as an ISO-8601 timestamp (`2026-05-10T14:30:00.000Z`) or as numeric seconds elapsed since `Date` + `Time`.

---

### 4. Stream Metadata Fields

The following metadata keys apply identically to the `"metadata"` object in BRT1 `0x02` chunks and to the `Key,Value` header block in streamed CSVs (key matching is case-insensitive):

| Metadata Key | Required | Description | Example |
| :--- | :---: | :--- | :--- |
| `Track name` | **Yes** | Name of the circuit/track. | `"Rowrah"` |
| `Date` | **Yes** | Session start date (`YYYY-MM-DD`, or full start datetime). | `"2026-05-10"` |
| `Time` | **Yes** | Session start time (`HH:MM` or `HH:MM:SS`). | `"14:30:00"` |
| `Driver name` | **Yes** | Driver name (validated against API key ACL permissions). | `"Driver A"` |
| `League` | **Yes** | League or simulator identifier. | `"kartsim"` |
| `Class` | **Yes** | Kart/vehicle class identifier. | `"cadet"` |
| `Session` | **Yes** | Session type/category. | `"Practice"` |
| `stream` | No | `""` or omitted for the primary stream; lowercase slug (`[a-z0-9][a-z0-9_-]{0,63}`, never `"primary"`) for an auxiliary stream. | `""` or `"eeg_muse"` |
| `Session ID` | No | Optional explicit sanitized session stem (`session_id` also accepted), overriding `{HH_MM}_{session}`. | `"14_30_practice"` |
| `kart_number` | No | Optional kart/car number. | `"12"` |
| `Conditions` | No | Track surface condition (`Dry`, `Wet`, `Damp`). | `"Dry"` |
| `Temperature` | No | Ambient/venue temperature. | `"21.5"` |
| `Weather` | No | Atmospheric weather state. | `"Sunny"` |
| `Distance to rear axle` | No | Distance from rear axle center to sensor/camera in meters (used for gyro rear slip calculation). | `1.5` |
| `Track configuration` | No | Specific track layout/configuration suffix. | `"GP"` |
| `Track Origin Latitude` | No | Origin latitude for local Cartesian `(x, z)` translation. | `45.61389` |
| `Track Origin Longitude` | No | Origin longitude for local Cartesian `(x, z)` translation. | `9.28624` |
| `Track Origin Heading` | No | Track origin heading in degrees clockwise from North. | `180.0` |

---

### 5. Standard Primary Stream Channels

Auxiliary streams can define arbitrary numeric channel names alongside `Time`. Primary vehicle streams recognize the following standard channels:

| Channel Name | Alternative Headers | Required (Primary) | Description |
| :--- | :--- | :---: | :--- |
| `Time` | `time` | **Yes** | Sample timestamp (seconds from session start in BRT1; ISO-8601 or seconds in CSV). |
| `Latitude` | `lat` | No* | GPS Latitude in decimal degrees. |
| `Longitude` | `lon` | No* | GPS Longitude in decimal degrees. |
| `x` | `pos_x`, `GPS Longitude` | No* | Local Cartesian X coordinate (translated to GPS when `Latitude`/`Longitude` are absent). |
| `z` | `pos_y`, `pos_z`, `y`, `GPS Latitude` | No* | Local Cartesian Z coordinate (translated to GPS when `Latitude`/`Longitude` are absent). |
| `Speed` | `speed`, `Ground Speed` | **Yes** | Vehicle ground speed in **m/s** (stored raw in m/s; converted to km/h when served to the UI). |
| `GForceLat` | `gforcelat` | No | Lateral G-force acceleration (positive right). |
| `GForceLon` | `gforcelon` | No | Longitudinal G-force acceleration (positive forward). |
| `GForceVert` | `gforcevert` | No | Vertical G-force acceleration. |
| `Throttle` | `throttle`, `Throttle Pos` | No | Throttle pedal/application percentage (`0.0` to `100.0`). |
| `Brake` | `brake`, `Brake Pos` | No | Brake pedal/application percentage (`0.0` to `100.0`). |
| `Steering` | `steering`, `Steering Angle` | No | Steering wheel angle in degrees. |
| `Yaw Rate` | - | No | Yaw angular velocity around vertical axis (used for rear slip angle calculation). |
| `Pitch Rate` | - | No | Pitch angular velocity around lateral axis. |
| `Roll Rate` | - | No | Roll angular velocity around longitudinal axis. |
| `Lap` | `lap` | No | Current lap number (auto-detected from track start/finish line if omitted). |
| `Lap Distance` | `LapDistance`, `lap_distance` | No | Distance along track centerline in meters (computed from centerline projection). |
| `Toe FL` / `Toe FR` | `toe_fl`, `toe_fr` | No | Front wheel toe angles in radians. |
| `Ori Quat X` / `Y` / `Z` / `W` | `q_x`, `q_y`, `q_z`, `q_w` | No | Chassis orientation quaternion components. |
| `RPS FL` / `FR` / `RL` / `RR` | `rps_fl`, `rps_fr`, `rps_rl`, `rps_rr` | No | Wheel rotational speeds in rotations per second. |
| `Lat Patch Vel FL`..`RR` | - | No | Per-wheel lateral contact patch velocity. |
| `Long Patch Vel FL`..`RR` | - | No | Per-wheel longitudinal contact patch velocity. |
| `Tyre Load FL`..`RR` | - | No | Per-wheel normal load. |
| `Lat Force FL`..`RR` | - | No | Per-wheel lateral tyre force. |
| `Long Force FL`..`RR` | - | No | Per-wheel longitudinal tyre force. |
| `Slide Pct FL`..`RR` | - | No | Per-wheel slip/slide percentage. |

*\* Primary streams require either (`Latitude` & `Longitude`) OR (`x` & `z` with a known track origin) so the server can filter off-track coordinates, project centerline distances, and split laps.*

---

### 6. Reconnections, Overlap Pruning, & Compaction Lifecycle

- **Session Identity**: A driver's telemetry within a meeting is keyed by `Date`/`Time` (or `Session ID`), `Track name`, `Session`, `Driver name`, `League`, and `Class`.
- **Reconnect-Safe Segment Ingestion**: Clients may routinely drop and re-establish HTTP stream connections (e.g. every minute) or re-upload later. Each connection writes an independent segment file under `{driver_stem}.active/{_primary|aux_<stream>}/`.
- **Timestamp Overlap Pruning (Per Stream)**: When segments for a given stream (`_primary` or `aux_<stream>`) are merged, any pre-existing samples in that stream with `Time >= min(new_segment.Time)` are discarded and replaced by the newer segment, while earlier samples (`Time < min(new_segment.Time)`) are preserved.
- **On-Demand Live Viewing & 10-Minute Inactivity Compaction**:
  - Viewing an active session before compaction materializes an ephemeral `.brtc` cache on demand without writing CSV files.
  - Once `{driver_stem}.active/` receives no writes for **10 minutes (`600s`)**, the background compaction daemon merges and compresses the session into columnar **Brotli Telemetry Columnar (`.brtc`)** files:
    - `{driver_stem}.primary.brtc`: Immutable raw primary channels.
    - `{driver_stem}.aux.<stream>.brtc`: Immutable native-cadence auxiliary stream channels.
    - `{driver_stem}.derived.brtc`: Regenerable computed channels (centerline `Lap Distance`, slip angles, wheel kinematics) plus auxiliary channels linearly resampled onto the primary `Time` grid (with sensor disconnect gaps masked to `NaN`).

