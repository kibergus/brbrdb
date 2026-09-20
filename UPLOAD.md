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
| `session_start_datetime`| String | **Yes** | Start time in `"YYYY-MM-DD HH:MM"` format. | `"2026-05-10 14:30"` |
| `driver_name` | String | No | Optional driver name (not required; driver names are validated against ACL permissions directly from results CSV). | `"Driver A"` |
| `short_name` | String | No | Shorthand notation of the session name. | `"Practice"`, `"Q"`, `"F"` |
| `meeting_name` | String | No | The overall race meeting name (defaults to track name).| `"Rowrah Practice"` |
| `track_conditions` | String | No | General condition of the track surfaces. | `"Dry"`, `"Wet"`, `"Damp"` |
| `temperature` | Float/String | No | Temperature of the venue. | `21.5` or `"21.5C"` |
| `weather` | String | No | General atmospheric weather state. | `"Sunny"`, `"Dry"`, `"Rain"` |
| `distance_to_rear_axle` | Float | No | Distance from rear axle center to camera in meters. | `1.5` |
| `alphatiming_url` | String | No | Reference link to Alphatiming or session results page. | `"https://..."` |


#### Example `metadata.json`
```json
{
    "league": "kartsim",
    "class_name": "cadet",
    "track_name": "Rowrah",
    "session_name": "Practice",
    "short_name": "Practice",
    "session_start_datetime": "2026-05-10 14:30",
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

Alternatively, detailed telemetry (e.g. from a simulator) can be streamed to this endpoint. It supports only single driver sessions. The server processes the stream in real-time, writing the accumulated data to files and the database every N seconds or when the stream ends.

- **HTTP Method**: `POST`
- **Content Type**: `text/csv` or `application/octet-stream`
- **Request Headers**:
  - `X-API-Key`: Required for authentication. No other request headers are required for session metadata.

- **Request Limits & Constraints**:
  - **Maximum Size**: Strict limit of 200 MB (`MAX_METADATA_SIZE`). Uploads exceeding this size are rejected with a `400 Bad Request` error.
  - **Flush Interval**: Telemetry is buffered in memory and flushed to the database and files every 15 seconds, as well as on completion.

- **Request Body**:
  The request body should contain the telemetry in CSV format with a header containing session metadata. The file can be streamed (e.g. during the driving session) and data will be saved periodically. 

- **Embedded CSV Metadata Header Block**:
  The streamed CSV file must start with a metadata header block, separated from the telemetry rows by a single blank line. The metadata fields are:

  | CSV Header Key | Required | Description | Example |
  | :--- | :---: | :--- | :--- |
  | `Track name` | **Yes** | The name of the track. | `Track name,Rowrah` |
  | `Date` | **Yes** | The start date of the session (`YYYY-MM-DD`). | `Date,2026-05-10` |
  | `Time` | **Yes** | The start time of the session (`HH:MM` or `HH:MM:SS`). | `Time,14:30:00` |
  | `Driver name` | **Yes** | The driver name (validated against ACL list permissions). | `Driver name,Driver A` |
  | `League` | **Yes** | Source system or league. | `League,kartsim` |
  | `Class` | **Yes** | Classification of the kart. | `Class,cadet` |
  | `Session` | **Yes** | The category or type of the session. | `Session,Practice` |
  | `Kart number` | No | Optional kart number. | `Kart number,12` |
  | `Conditions` | No | General track condition (e.g. Dry, Wet, Damp). | `Conditions,Dry` |
  | `Temperature` | No | Temperature of the venue. | `Temperature,21.5` |
  | `Weather` | No | General weather state (e.g. Sunny, Dry, Rain). | `Weather,Sunny` |
| `Distance to rear axle` | No | Distance from rear axle center to camera in meters. | `Distance to rear axle,1.5` |

  Example embedded header at the start of the stream:
  ```csv
  Track name,Rowrah
  Date,2026-05-10
  Time,14:30:00
  Driver name,Driver A
  League,kartsim
  Class,cadet
  Session,Practice

  Time,Latitude,Longitude,Speed,Lap
  2026-05-10T14:30:00.000Z,54.123,-3.123,10.0,1
  ...
  ```

- **Idempotency, Reconnections, & Partial Updates**:
  - The session is uniquely identified by the combination of metadata extracted from the CSV file header block (`Date`/`Time`, `Track name`, `Session`, `Driver name`, `League`, `Class`).
  - If a connection drops, is interrupted, or the client starts a new session segment, the client can reconnect and stream from the last known or a newer timestamp.
  - The server preserves all pre-existing telemetry data points with timestamps strictly less than the minimal timestamp of the newly uploaded segment. Pre-existing data points with timestamps greater than or equal to this minimal timestamp are discarded and replaced by the new incoming data.
  - This non-destructive merge ensures robust resume-on-failure and allows clients to stream only newer telemetry segments without having to keep or re-send the entire session's telemetry from the very beginning.

- **Example Response (200 OK)**:
  ```json
  {
      "message": "Session completed and imported successfully.",
      "session_id": "2026_05_10_rowrah_14_30_practice"
  }
  ```

#### 2. Expected Columns
Following the metadata block, the CSV must contain a header row containing `Record` or `Time` followed by high-frequency telemetry parameters:

| Column Name | Alternative Headers | Required | Description |
| :--- | :--- | :---: | :--- |
| `Time` | `time` | **Yes** | Timestamp of the point in ISO-8601 or similar format. |
| `Record` | - | No | Index of the recorded point. |
| `Latitude` | `lat` | No | GPS Latitude in decimal degrees. |
| `Longitude` | `lon` | No | GPS Longitude in decimal degrees. |
| `x` | `pos_x`, `GPS Longitude` | No* | Local Cartesian coordinate X (if GPS is not present). |
| `z` | `pos_y`, `pos_z`, `y`, `GPS Latitude` | No* | Local Cartesian coordinate Z (if GPS is not present). |
| `Speed` | `speed`, `Ground Speed`| **Yes** | Velocity of the kart/vehicle in m/s. |
| `GForceLat` | `gforcelat` | **Yes** | Lateral G-force acceleration (positive to the right). |
| `GForceLon` | `gforcelon` | **Yes** | Longitudinal G-force acceleration (positive forward). |
| `GForceVert` | `gforcevert` | **Yes** | Vertical G-force acceleration. |
| `Throttle` | `throttle`, `Throttle Pos` | **Yes** | Throttle application percentage (`0.0` to `100.0`). |
| `Brake` | `brake`, `Brake Pos` | **Yes** | Brake application percentage (`0.0` to `100.0`). |
| `Steering` | `steering` | **Yes** | Steering wheel rotation angle in degrees. |
| `Yaw Rate` | - | No | Kart yaw rotation rate (angular velocity around vertical axis). |
| `Pitch Rate` | - | No | Kart pitch rotation rate (angular velocity around lateral axis). |
| `Roll Rate` | - | No | Kart roll rotation rate (angular velocity around longitudinal axis). |
| `Lap` | `lap` | No | The current lap number of the point. |
| `LapDistance`| `lap_distance` | No | Distance traveled along the track centerline (meters). |
| `Toe FL` | `toe_fl` | No | Front-left wheel toe angle in radians. |
| `Toe FR` | `toe_fr` | No | Front-right wheel toe angle in radians. |
| `Ori Quat X` | `q_x` | No | Orientation quaternion X. |
| `Ori Quat Y` | `q_y` | No | Orientation quaternion Y. |
| `Ori Quat Z` | `q_z` | No | Orientation quaternion Z. |
| `Ori Quat W` | `q_w` | No | Orientation quaternion W. |
| `RPS FL` | `rps_fl` | No | Wheel speed in Rotations Per Second (Front Left). |
| `RPS FR` | `rps_fr` | No | Wheel speed in Rotations Per Second (Front Right). |
| `RPS RL` | `rps_rl` | No | Wheel speed in Rotations Per Second (Rear Left). |
| `RPS RR` | `rps_rr` | No | Wheel speed in Rotations Per Second (Rear Right). |
| `Lat Patch Vel FL` | `Lat Patch Vel FR`, `Lat Patch Vel RL`, `Lat Patch Vel RR` | No | Lateral patch velocity per wheel. |
| `Long Patch Vel FL` | `Long Patch Vel FR`, `Long Patch Vel RL`, `Long Patch Vel RR` | No | Longitudinal patch velocity per wheel. |
| `Tyre Load FL` | `Tyre Load FR`, `Tyre Load RL`, `Tyre Load RR` | No | Normal load on the tyre per wheel. |
| `Lat Force FL` | `Lat Force FR`, `Lat Force RL`, `Lat Force RR` | No | Lateral force exerted by the tyre per wheel. |
| `Long Force FL` | `Long Force FR`, `Long Force RL`, `Long Force RR` | No | Longitudinal force exerted by the tyre per wheel. |
| `Slide Pct FL` | `Slide Pct FR`, `Slide Pct RL`, `Slide Pct RR` | No | Slip ratio / slide percentage per wheel. |

*\* To successfully compute geo-spatial trajectories and derivative fields, either (`Latitude` & `Longitude`) OR (`x` & `z`) coordinates are required.*

> [!TIP]
> The server automatically handles all derivative computations at finalize-time, converting local `x`/`z` coordinates into geo-spatial latitude and longitude using registered track origins, detecting exact lap crossings, and calculating slip angles.

