from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext

ext_modules = [
    Pybind11Extension(
        "_telemetry_native",
        [
            "native/telemetry_native.cpp",
            "native/bindings.cpp",
        ],
        extra_compile_args=["-O3", "-std=c++20"],
        extra_link_args=["-lbrotlienc"],
    ),
]

setup(
    name="_telemetry_native",
    version="0.1.0",
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)
