#pragma once

#include <string>
#include <vector>
#include <cstdint>

namespace telemetry {

// Extracts driver name from CSV metadata header.
std::string extract_driver_name(const std::string& csv_path);

// Smooths telemetry values using a 5-sample centered moving average with NaN handling.
std::vector<double> smooth_values(const std::vector<double>& values);

// Processes a single telemetry channel from CSV directly into a Brotli-compressed binary payload.
//
// Parameters:
//   - csv_path: Path to the telemetry CSV file.
//   - channel_name: Name of the channel column to extract (e.g. "Speed", "GForceLat").
//   - should_smooth: Whether to apply 5-sample centered moving average smoothing.
//   - ref_lat, ref_lon: Track geographic reference coordinates (latitude/longitude in degrees).
//   - max_dist_m: Maximum allowable distance (in meters) from the reference coordinates.
//   - has_ref: Indicates whether authoritative track reference coordinates (ref_lat, ref_lon)
//              are provided (e.g., from the track database).
//              NOTE: 'ref' here refers to track location reference coordinates, NOT a reference lap!
//              - If true: Telemetry points exceeding 'max_dist_m' from (ref_lat, ref_lon) are filtered out.
//              - If false: No known track coordinates were supplied. The parser scans the CSV data
//                to compute the median latitude and longitude as an empirical track center with a
//                10 km boundary to filter out GPS initialization anomalies (e.g. (0,0) or satellite lock glitches).
//                If no valid coordinates exist in the file, distance-based filtering is bypassed.
std::string process_telemetry_channel(
    const std::string& csv_path,
    const std::string& channel_name,
    bool should_smooth,
    double ref_lat,
    double ref_lon,
    double max_dist_m,
    bool has_ref
);

// Detects lap crossings by projecting onto the track centerline and monitoring wrap-arounds.
// Returns lap number for each point (1-based, starting at 1, incrementing at each crossing).
std::vector<int> detect_lap_crossings(
    const double* lat_arr,
    const double* lon_arr,
    size_t N,
    const double* cl_lat,
    const double* cl_lon,
    const double* cl_dist,
    size_t M,
    double lap_length
);

// Interpolates distance along track centerline for each telemetry point.
std::vector<double> interpolate_lap_distances(
    const double* lat_arr,
    const double* lon_arr,
    const int* lap_col,
    size_t N,
    const double* cl_lat,
    const double* cl_lon,
    const double* cl_dist,
    size_t M,
    double lap_length
);

} // namespace telemetry
