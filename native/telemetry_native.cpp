#include "telemetry_native.hpp"

#include <fstream>
#include <sstream>
#include <cmath>
#include <cstring>
#include <algorithm>
#include <limits>
#include <charconv>
#include <numbers>
#include <brotli/encode.h>

namespace telemetry {

static inline std::string to_lower(std::string_view s) {
    std::string res;
    res.reserve(s.size());
    for (char c : s) {
        res.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(c))));
    }
    return res;
}

static inline std::string_view trim(std::string_view s) {
    while (!s.empty() && std::isspace(static_cast<unsigned char>(s.front()))) {
        s.remove_prefix(1);
    }
    while (!s.empty() && std::isspace(static_cast<unsigned char>(s.back()))) {
        s.remove_suffix(1);
    }
    return s;
}

// Validates GPS coordinates and filters points outside the track boundary.
// 'has_ref' indicates if track reference coordinates (ref_lat, ref_lon) are active.
// Note: 'ref' refers to track geographic reference coordinates (not a reference lap).
// If has_ref is false, spatial distance filtering is skipped and only basic sanity checks apply.
static inline bool is_coordinate_within_bounds(
    double lat, double lon, double ref_lat, double ref_lon, double max_dist_m, bool has_ref
) {
    if (std::isnan(lat) || std::isnan(lon) || (lat == 0.0 && lon == 0.0)) return false;
    if (lat < -90.0 || lat > 90.0 || lon < -180.0 || lon > 180.0) return false;
    if (!has_ref) return true;
    double dlat = (lat - ref_lat) * 111000.0;
    double dlon = (lon - ref_lon) * 111000.0 * std::cos(ref_lat * (std::numbers::pi / 180.0));
    return (dlat * dlat + dlon * dlon) <= (max_dist_m * max_dist_m);
}

std::string extract_driver_name(const std::string& csv_path) {
    std::ifstream file(csv_path, std::ios::binary);
    if (!file.is_open()) return "";

    std::string line;
    while (std::getline(file, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        std::string_view sv = trim(line);
        if (sv.empty()) break; // End of metadata header

        size_t comma = line.find(',');
        if (comma != std::string::npos) {
            std::string_view key = trim(std::string_view(line.data(), comma));
            std::string_view val = trim(std::string_view(line.data() + comma + 1, line.size() - comma - 1));
            if (key == "Driver name" || key == "Configuration") {
                return std::string(val);
            }
        }
    }
    return "";
}

std::vector<double> smooth_values(const std::vector<double>& values) {
    size_t n = values.size();
    std::vector<double> smoothed(n);
    for (size_t i = 0; i < n; ++i) {
        if (std::isnan(values[i])) {
            smoothed[i] = std::numeric_limits<double>::quiet_NaN();
            continue;
        }
        double sum = 0.0;
        int count = 0;
        for (int k = -2; k <= 2; ++k) {
            long idx = static_cast<long>(i) + k;
            if (idx >= 0 && idx < static_cast<long>(n)) {
                double v = values[idx];
                if (!std::isnan(v)) {
                    sum += v;
                    count++;
                }
            }
        }
        smoothed[i] = count > 0 ? (sum / count) : std::numeric_limits<double>::quiet_NaN();
    }
    return smoothed;
}

static std::string compress_brotli(const std::string& data) {
    size_t max_size = BrotliEncoderMaxCompressedSize(data.size());
    std::string out;
    out.resize(max_size);
    size_t encoded_size = max_size;
    BROTLI_BOOL res = BrotliEncoderCompress(
        4, // quality 4 for speed & compression
        BROTLI_DEFAULT_WINDOW,
        BROTLI_MODE_GENERIC,
        data.size(),
        reinterpret_cast<const uint8_t*>(data.data()),
        &encoded_size,
        reinterpret_cast<uint8_t*>(&out[0])
    );
    if (!res) {
        return "";
    }
    out.resize(encoded_size);
    return out;
}

static inline double parse_iso_time_to_seconds(std::string_view sv) {
    // Formats: "YYYY-MM-DDTHH:MM:SS.sssZ" or "HH:MM:SS.sss"
    size_t t_pos = sv.find('T');
    if (t_pos != std::string_view::npos) {
        sv.remove_prefix(t_pos + 1);
    }
    if (!sv.empty() && sv.back() == 'Z') {
        sv.remove_suffix(1);
    }

    size_t c1 = sv.find(':');
    if (c1 == std::string_view::npos) return std::numeric_limits<double>::quiet_NaN();
    size_t c2 = sv.find(':', c1 + 1);
    if (c2 == std::string_view::npos) return std::numeric_limits<double>::quiet_NaN();

    std::string_view h_sv = sv.substr(0, c1);
    std::string_view m_sv = sv.substr(c1 + 1, c2 - c1 - 1);
    std::string_view s_sv = sv.substr(c2 + 1);

    int h = 0, m = 0;
    auto res_h = std::from_chars(h_sv.data(), h_sv.data() + h_sv.size(), h);
    auto res_m = std::from_chars(m_sv.data(), m_sv.data() + m_sv.size(), m);
    if (res_h.ec != std::errc() || res_m.ec != std::errc()) {
        return std::numeric_limits<double>::quiet_NaN();
    }

    double s = 0.0;
    auto res_s = std::from_chars(s_sv.data(), s_sv.data() + s_sv.size(), s);
    if (res_s.ec != std::errc()) {
        char* endptr = nullptr;
        std::string s_str(s_sv);
        s = std::strtod(s_str.c_str(), &endptr);
        if (endptr == s_str.c_str()) {
            return std::numeric_limits<double>::quiet_NaN();
        }
    }

    return static_cast<double>(h) * 3600.0 + static_cast<double>(m) * 60.0 + s;
}

std::string process_telemetry_channel(
    const std::string& csv_path,
    const std::string& channel_name,
    bool should_smooth,
    double ref_lat,
    double ref_lon,
    double max_dist_m,
    bool has_ref
) {
    std::string empty_header(16, '\0');
    double zero_val = 0.0;
    double one_val = 1.0;
    std::memcpy(&empty_header[0], &zero_val, sizeof(double));
    std::memcpy(&empty_header[8], &one_val, sizeof(double));
    std::string empty_payload = compress_brotli(empty_header);

    std::ifstream file(csv_path, std::ios::binary);
    if (!file.is_open()) {
        return empty_payload;
    }

    std::string line;
    bool found_blank = false;
    // Skip metadata rows up to blank line
    while (std::getline(file, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        std::string_view sv = trim(line);
        if (sv.empty()) {
            found_blank = true;
            break;
        }
    }

    if (!found_blank) {
        return empty_payload;
    }

    // Next non-empty line is header
    if (!std::getline(file, line)) {
        return empty_payload;
    }
    if (!line.empty() && line.back() == '\r') line.pop_back();

    std::vector<std::string> headers;
    size_t start = 0;
    for (size_t i = 0; i <= line.size(); ++i) {
        if (i == line.size() || line[i] == ',') {
            std::string_view h = trim(std::string_view(line.data() + start, i - start));
            headers.emplace_back(h);
            start = i + 1;
        }
    }

    int time_idx = -1, lat_idx = -1, lon_idx = -1, lap_idx = -1, col_idx = -1;
    for (size_t i = 0; i < headers.size(); ++i) {
        if (headers[i] == "Time") time_idx = static_cast<int>(i);
        else if (headers[i] == "Latitude") lat_idx = static_cast<int>(i);
        else if (headers[i] == "Longitude") lon_idx = static_cast<int>(i);
        else if (headers[i] == "Lap") lap_idx = static_cast<int>(i);

        if (headers[i] == channel_name) col_idx = static_cast<int>(i);
    }

    // If track reference is not provided, compute median coordinates from the data rows
    if (!has_ref && lat_idx >= 0 && lon_idx >= 0) {
        std::vector<double> sample_lats;
        std::vector<double> sample_lons;
        sample_lats.reserve(5000);
        sample_lons.reserve(5000);

        std::string sample_line;
        std::streampos data_pos = file.tellg();
        while (std::getline(file, sample_line)) {
            if (!sample_line.empty() && sample_line.back() == '\r') sample_line.pop_back();
            if (sample_line.empty()) continue;

            size_t curr = 0;
            int col = 0;
            std::string_view s_lat, s_lon;
            for (size_t i = 0; i <= sample_line.size(); ++i) {
                if (i == sample_line.size() || sample_line[i] == ',') {
                    if (col == lat_idx) s_lat = trim(std::string_view(sample_line.data() + curr, i - curr));
                    else if (col == lon_idx) s_lon = trim(std::string_view(sample_line.data() + curr, i - curr));
                    curr = i + 1;
                    col++;
                    if (col > std::max(lat_idx, lon_idx)) break;
                }
            }
            if (!s_lat.empty() && !s_lon.empty()) {
                double l1 = 0.0, l2 = 0.0;
                auto r1 = std::from_chars(s_lat.data(), s_lat.data() + s_lat.size(), l1);
                auto r2 = std::from_chars(s_lon.data(), s_lon.data() + s_lon.size(), l2);
                if (r1.ec == std::errc() && r2.ec == std::errc()) {
                    if (!std::isnan(l1) && !std::isnan(l2) && !(l1 == 0.0 && l2 == 0.0) &&
                        l1 >= -90.0 && l1 <= 90.0 && l2 >= -180.0 && l2 <= 180.0) {
                        sample_lats.push_back(l1);
                        sample_lons.push_back(l2);
                    }
                }
            }
        }
        if (!sample_lats.empty()) {
            size_t mid = sample_lats.size() / 2;
            std::nth_element(sample_lats.begin(), sample_lats.begin() + mid, sample_lats.end());
            std::nth_element(sample_lons.begin(), sample_lons.begin() + mid, sample_lons.end());
            ref_lat = sample_lats[mid];
            ref_lon = sample_lons[mid];
            max_dist_m = 10000.0;
            has_ref = true;
        }
        // Rewind back to start of data rows
        file.clear();
        file.seekg(data_pos);
    }

    bool is_time = (channel_name == "Time");
    std::string ch_lower = to_lower(channel_name);
    bool is_speed = (ch_lower == "speed" || ch_lower == "speed (m/s)");

    std::vector<double> values;
    values.reserve(200000);

    std::vector<std::string_view> row_tokens;
    row_tokens.reserve(headers.size());

    while (std::getline(file, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;

        row_tokens.clear();
        const char* p = line.data();
        const char* end = p + line.size();
        const char* token_start = p;
        while (p <= end) {
            if (p == end || *p == ',') {
                row_tokens.emplace_back(token_start, p - token_start);
                token_start = p + 1;
            }
            ++p;
        }

        if (row_tokens.size() < headers.size()) continue;

        // Row validation exactly matching parse_telemetry_csv
        std::string_view time_sv = (time_idx >= 0) ? trim(row_tokens[time_idx]) : std::string_view();
        std::string_view lat_sv = (lat_idx >= 0) ? trim(row_tokens[lat_idx]) : std::string_view();
        std::string_view lon_sv = (lon_idx >= 0) ? trim(row_tokens[lon_idx]) : std::string_view();
        std::string_view lap_sv = (lap_idx >= 0) ? trim(row_tokens[lap_idx]) : std::string_view();

        if (time_sv.empty() || lat_sv.empty() || lon_sv.empty() || lap_sv.empty()) continue;

        double f_lat = 0.0, f_lon = 0.0;
        auto res_lat = std::from_chars(lat_sv.data(), lat_sv.data() + lat_sv.size(), f_lat);
        if (res_lat.ec != std::errc()) {
            char* endptr = nullptr;
            f_lat = std::strtod(lat_sv.data(), &endptr);
            if (endptr == lat_sv.data()) continue;
        }

        auto res_lon = std::from_chars(lon_sv.data(), lon_sv.data() + lon_sv.size(), f_lon);
        if (res_lon.ec != std::errc()) {
            char* endptr = nullptr;
            f_lon = std::strtod(lon_sv.data(), &endptr);
            if (endptr == lon_sv.data()) continue;
        }

        if (!is_coordinate_within_bounds(f_lat, f_lon, ref_lat, ref_lon, max_dist_m, has_ref)) continue;

        int lap_val = 0;
        auto res_lap = std::from_chars(lap_sv.data(), lap_sv.data() + lap_sv.size(), lap_val);
        if (res_lap.ec != std::errc()) {
            char* endptr = nullptr;
            lap_val = static_cast<int>(std::strtol(lap_sv.data(), &endptr, 10));
            if (endptr == lap_sv.data()) continue;
        }

        if (is_time) {
            values.push_back(parse_iso_time_to_seconds(time_sv));
        } else if (col_idx >= 0) {
            std::string_view val_sv = trim(row_tokens[col_idx]);
            if (val_sv.empty()) {
                values.push_back(std::numeric_limits<double>::quiet_NaN());
            } else {
                double val = 0.0;
                auto res_val = std::from_chars(val_sv.data(), val_sv.data() + val_sv.size(), val);
                if (res_val.ec != std::errc()) {
                    char* endptr = nullptr;
                    val = std::strtod(val_sv.data(), &endptr);
                    if (endptr == val_sv.data()) {
                        values.push_back(std::numeric_limits<double>::quiet_NaN());
                        continue;
                    }
                }
                if (is_speed) val *= 3.6;
                values.push_back(val);
            }
        } else {
            values.push_back(std::numeric_limits<double>::quiet_NaN());
        }
    }

    if (should_smooth) {
        values = smooth_values(values);
    }

    // Compute mean and scale
    double sum = 0.0;
    size_t count = 0;
    for (double v : values) {
        if (!std::isnan(v)) {
            sum += v;
            count++;
        }
    }

    double mean_val = 0.0;
    double scale = 1.0;
    if (count > 0) {
        mean_val = sum / static_cast<double>(count);
        double max_dev = 0.0;
        double max_diff = 0.0;
        double prev_val = 0.0;
        bool has_prev = false;
        for (double v : values) {
            if (!std::isnan(v)) {
                double dev = std::abs(v - mean_val);
                if (dev > max_dev) max_dev = dev;
                if (has_prev) {
                    double diff = std::abs(v - prev_val);
                    if (diff > max_diff) max_diff = diff;
                }
                prev_val = v;
                has_prev = true;
            }
        }
        double scale_base = max_dev / 32760.0;
        double scale_delta = max_diff / 32760.0;
        scale = std::max(scale_base, scale_delta);
        if (scale == 0.0) scale = 1.0;
    }

    // Delta encode
    size_t n = values.size();
    std::vector<int16_t> deltas(n);
    int64_t last_valid_q = 0;
    bool has_seen_valid = false;
    for (size_t i = 0; i < n; ++i) {
        if (std::isnan(values[i])) {
            deltas[i] = -32768;
        } else {
            int64_t q_val = static_cast<int64_t>(std::round((values[i] - mean_val) / scale));
            if (!has_seen_valid) {
                int64_t clipped = std::clamp(q_val, static_cast<int64_t>(-32767), static_cast<int64_t>(32767));
                deltas[i] = static_cast<int16_t>(clipped);
                has_seen_valid = true;
            } else {
                int64_t d = q_val - last_valid_q;
                int64_t clipped = std::clamp(d, static_cast<int64_t>(-32767), static_cast<int64_t>(32767));
                deltas[i] = static_cast<int16_t>(clipped);
            }
            last_valid_q = q_val;
        }
    }

    std::string raw_payload;
    raw_payload.resize(16 + deltas.size() * sizeof(int16_t));
    std::memcpy(&raw_payload[0], &mean_val, sizeof(double));
    std::memcpy(&raw_payload[8], &scale, sizeof(double));
    if (!deltas.empty()) {
        std::memcpy(&raw_payload[16], deltas.data(), deltas.size() * sizeof(int16_t));
    }

    return compress_brotli(raw_payload);
}

} // namespace telemetry
