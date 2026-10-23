"""Native v3 mixed packed scan; importing this module performs no compilation."""
from __future__ import annotations

import ctypes as ct
from functools import lru_cache
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile

import numpy as np

from .native_packed import _inputs


@lru_cache(maxsize=1)
def library():
    source = Path(__file__).with_name("kernels_mixed.cpp")
    included = source.with_name("kernels_packed.cpp")
    flags = ["-O3", "-std=c++17", "-shared", "-fPIC", "-ffp-contract=off", "-fno-fast-math"]
    compiler = shutil.which("g++") or shutil.which("clang++")
    if not compiler:
        raise RuntimeError("A C++17 compiler is required for mixed packed search")
    identity = subprocess.run([compiler, "--version"], check=True, capture_output=True, text=True).stdout
    key = repr((flags, compiler, identity, platform.machine(), platform.system())).encode()
    digest = hashlib.sha256(source.read_bytes() + b"\0" + included.read_bytes() + key).hexdigest()[:16]
    directory = source.parent / ".build"
    directory.mkdir(exist_ok=True)
    target = directory / f"mixed_{digest}.so"
    if not target.exists():
        with tempfile.NamedTemporaryFile(prefix=f".{target.stem}.", suffix=".so", dir=directory, delete=False) as stream:
            temporary = Path(stream.name)
        try:
            subprocess.run([compiler, *flags, str(source), "-o", str(temporary)], check=True,
                           capture_output=True, text=True)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    lib = ct.CDLL(str(target))
    p, size = ct.c_void_p, ct.c_size_t
    for name in ("etex_mixed_coarse", "etex_mixed_coarse_portable"):
        fn = getattr(lib, name)
        fn.argtypes = [p, p, p, p, p, p, ct.c_double, size, size, size, p]
        fn.restype = ct.c_int
    for name in ("etex_mixed_global", "etex_mixed_global_portable"):
        fn = getattr(lib, name)
        fn.argtypes = [p, p, p, p, p, p, ct.c_double, ct.c_double, size, size, size, size, p, p, p]
        fn.restype = ct.c_int
    lib.etex_mixed_backend.argtypes = []
    lib.etex_mixed_backend.restype = ct.c_char_p
    return lib


def backend():
    return library().etex_mixed_backend().decode()


def _prepare(packed, scales, sums, qcodes, qscale, exact_values, exact_query, dimensions):
    p, s, sums, qwords, n, d = _inputs(packed, scales, sums, qcodes, qscale, dimensions)
    values, query = np.asarray(exact_values), np.asarray(exact_query)
    if (values.dtype != np.float32 or values.ndim != 2 or not values.flags.c_contiguous
            or values.shape[0] != n or not 1 <= values.shape[1] <= d):
        raise ValueError("exact_values must be a contiguous FP32 rows-by-columns matrix")
    columns = values.shape[1]
    if query.dtype != np.float32 or query.shape != (columns,):
        raise ValueError("exact_query must be a matching FP32 vector")
    return p, s, sums, qwords, values, np.ascontiguousarray(query), n, d, columns


def coarse_scores(packed, scales, sums, qcodes, qscale, exact_values, exact_query, dimensions, *, portable=False):
    p, s, sums, qwords, values, query, n, d, columns = _prepare(
        packed, scales, sums, qcodes, qscale, exact_values, exact_query, dimensions)
    out = np.empty(n, dtype=np.float64)
    fn = library().etex_mixed_coarse_portable if portable else library().etex_mixed_coarse
    status = fn(p.ctypes.data, s.ctypes.data, sums.ctypes.data, qwords.ctypes.data,
                values.ctypes.data, query.ctypes.data, float(qscale), n, d, columns, out.ctypes.data)
    if status < 0:
        raise RuntimeError("Mixed coarse scorer rejected arguments or floating-point environment")
    return out


def global_bounds(packed, scales, sums, qcodes, qscale, exact_values, exact_query, radius, dimensions, k, *, portable=False):
    p, s, sums, qwords, values, query, n, d, columns = _prepare(
        packed, scales, sums, qcodes, qscale, exact_values, exact_query, dimensions)
    if isinstance(k, (bool, np.bool_)) or not isinstance(k, (int, np.integer)) or not 1 <= k <= n:
        raise ValueError("k must be an integer in 1..rows")
    if not np.isfinite(radius) or radius < 0:
        return np.full(n, np.inf, dtype=np.float64), -np.inf, False
    upper, heap, threshold = np.empty(n, np.float64), np.empty(int(k), np.float64), np.empty(1, np.float64)
    fn = library().etex_mixed_global_portable if portable else library().etex_mixed_global
    status = fn(p.ctypes.data, s.ctypes.data, sums.ctypes.data, qwords.ctypes.data,
                values.ctypes.data, query.ctypes.data, float(qscale), float(radius),
                n, d, columns, int(k), upper.ctypes.data, heap.ctypes.data, threshold.ctypes.data)
    if status < 0:
        raise RuntimeError("Mixed filter rejected arguments or floating-point environment")
    return upper, float(threshold[0]), bool(status)
