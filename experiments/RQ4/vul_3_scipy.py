from scipy.special import obl_cv_seq

v_values = obl_cv_seq(1, 3, 2.0)
print(v_values)

"""
$ python -c "import sys, scipy, numpy; print(scipy.__version__, numpy.__version__, sys.version_info); scipy.show_config()"
1.16.2 2.3.4 sys.version_info(major=3, minor=13, micro=9, releaselevel='final', serial=0)
Build Dependencies:
  blas:
    detection method: pkgconfig
    found: true
    include directory: /opt/_internal/cpython-3.13.5/lib/python3.13/site-packages/scipy_openblas32/include
    lib directory: /opt/_internal/cpython-3.13.5/lib/python3.13/site-packages/scipy_openblas32/lib
    name: scipy-openblas
    openblas configuration: OpenBLAS 0.3.29.dev DYNAMIC_ARCH NO_AFFINITY Haswell MAX_THREADS=64
    pc file directory: /project
    version: 0.3.29.dev
  lapack:
    detection method: pkgconfig
    found: true
    include directory: /opt/_internal/cpython-3.13.5/lib/python3.13/site-packages/scipy_openblas32/include
    lib directory: /opt/_internal/cpython-3.13.5/lib/python3.13/site-packages/scipy_openblas32/lib
    name: scipy-openblas
    openblas configuration: OpenBLAS 0.3.29.dev DYNAMIC_ARCH NO_AFFINITY Haswell MAX_THREADS=64
    pc file directory: /project
    version: 0.3.29.dev
  pybind11:
    detection method: config-tool
    include directory: unknown
    name: pybind11
    version: 3.0.1
Compilers:
  c:
    commands: cc
    linker: ld.bfd
    name: gcc
    version: 10.2.1
  c++:
    commands: c++
    linker: ld.bfd
    name: gcc
    version: 10.2.1
  cython:
    commands: cython
    linker: cython
    name: cython
    version: 3.1.3
  fortran:
    commands: gfortran
    linker: ld.bfd
    name: gcc
    version: 10.2.1
  pythran:
    include directory: ../../tmp/build-env-r79e130o/lib/python3.13/site-packages/pythran
    version: 0.18.0
Machine Information:
  build:
    cpu: x86_64
    endian: little
    family: x86_64
    system: linux
  cross-compiled: false
  host:
    cpu: x86_64
    endian: little
    family: x86_64
    system: linux
Python Information:
  path: /tmp/build-env-r79e130o/bin/python
  version: '3.13'
"""
