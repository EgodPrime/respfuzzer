from Cython.Build import cythonize
from setuptools import Extension, setup

extentions = [
    Extension(
        name="mplfuzz.mutate",
        sources=["src/mplfuzz/c/mutate.cxx"],
        language="c++",
        extra_compile_args=["-std=c++17"],
    )
]

setup(
    name="mplfuzz",
    version="1.0.0",
    ext_modules=cythonize(extentions),
)