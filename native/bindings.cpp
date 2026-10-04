#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "telemetry_native.hpp"

namespace py = pybind11;

PYBIND11_MODULE(_telemetry_native, m) {
    m.doc() = "Native C++ telemetry preprocessing and binary channel encoder";

    m.def("extract_driver_name", &telemetry::extract_driver_name,
          py::arg("csv_path"),
          "Extract driver name from CSV metadata header");

    m.def("smooth_values", &telemetry::smooth_values,
          py::arg("values"),
          "Smooth values using 5-sample centered moving average with NaN handling");

    m.def("process_telemetry_channel", [](
        const std::string& csv_path,
        const std::string& channel_name,
        bool should_smooth,
        double ref_lat,
        double ref_lon,
        double max_dist_m,
        bool has_ref
    ) {
        // Release GIL during heavy I/O and processing
        std::string result;
        {
            py::gil_scoped_release release;
            result = telemetry::process_telemetry_channel(
                csv_path, channel_name, should_smooth,
                ref_lat, ref_lon, max_dist_m, has_ref
            );
        }
        return py::bytes(result);
    },
    py::arg("csv_path"),
    py::arg("channel_name"),
    py::arg("should_smooth"),
    py::arg("ref_lat") = 0.0,
    py::arg("ref_lon") = 0.0,
    py::arg("max_dist_m") = 5000.0,
    py::arg("has_ref") = false,
    "Process CSV channel directly into encoded/compressed binary payload");
}
