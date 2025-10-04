from Cython.Build import cythonize
from setuptools import Extension, setup

extentions = [
    Extension(
        name="tracefuzz.mutate",
        sources=["src/tracefuzz/c/mutate.cxx"],
        language="c++",
        extra_compile_args=["-std=c++17"],
    )
]

setup(
    name="tracefuzz",
    version="1.0.0",
    ext_modules=cythonize(extentions),
)