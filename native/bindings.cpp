#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>
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

    m.def("detect_lap_crossings", [](
        py::array_t<double, py::array::c_style | py::array::forcecast> lat_arr,
        py::array_t<double, py::array::c_style | py::array::forcecast> lon_arr,
        py::array_t<double, py::array::c_style | py::array::forcecast> cl_lat,
        py::array_t<double, py::array::c_style | py::array::forcecast> cl_lon,
        py::array_t<double, py::array::c_style | py::array::forcecast> cl_dist,
        double lap_length
    ) {
        py::ssize_t N = lat_arr.size();
        py::ssize_t M = cl_lat.size();
        if (lon_arr.size() != N || cl_lon.size() != M || cl_dist.size() != M) {
            throw std::invalid_argument("Array size mismatch");
        }
        std::vector<int> res;
        {
            py::gil_scoped_release release;
            res = telemetry::detect_lap_crossings(
                lat_arr.data(), lon_arr.data(), static_cast<size_t>(N),
                cl_lat.data(), cl_lon.data(), cl_dist.data(), static_cast<size_t>(M),
                lap_length
            );
        }
        py::array_t<int> py_res(res.size());
        std::memcpy(py_res.mutable_data(), res.data(), res.size() * sizeof(int));
        return py_res;
    },
    py::arg("lat_arr"), py::arg("lon_arr"),
    py::arg("cl_lat"), py::arg("cl_lon"), py::arg("cl_dist"),
    py::arg("lap_length"),
    "Detect lap crossings and assign lap numbers to telemetry points");

    m.def("interpolate_lap_distances", [](
        py::array_t<double, py::array::c_style | py::array::forcecast> lat_arr,
        py::array_t<double, py::array::c_style | py::array::forcecast> lon_arr,
        py::array_t<int, py::array::c_style | py::array::forcecast> lap_col,
        py::array_t<double, py::array::c_style | py::array::forcecast> cl_lat,
        py::array_t<double, py::array::c_style | py::array::forcecast> cl_lon,
        py::array_t<double, py::array::c_style | py::array::forcecast> cl_dist,
        double lap_length
    ) {
        py::ssize_t N = lat_arr.size();
        py::ssize_t M = cl_lat.size();
        if (lon_arr.size() != N || lap_col.size() != N || cl_lon.size() != M || cl_dist.size() != M) {
            throw std::invalid_argument("Array size mismatch");
        }
        std::vector<double> res;
        {
            py::gil_scoped_release release;
            res = telemetry::interpolate_lap_distances(
                lat_arr.data(), lon_arr.data(), lap_col.data(), static_cast<size_t>(N),
                cl_lat.data(), cl_lon.data(), cl_dist.data(), static_cast<size_t>(M),
                lap_length
            );
        }
        py::array_t<double> py_res(res.size());
        std::memcpy(py_res.mutable_data(), res.data(), res.size() * sizeof(double));
        return py_res;
    },
    py::arg("lat_arr"), py::arg("lon_arr"), py::arg("lap_col"),
    py::arg("cl_lat"), py::arg("cl_lon"), py::arg("cl_dist"),
    py::arg("lap_length"),
    "Interpolate lap distance along track centerline for telemetry points");
}
