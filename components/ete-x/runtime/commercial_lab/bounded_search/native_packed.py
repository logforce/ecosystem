"""Independent native bindings for the tile16/quad4 code layout.

Importing this module does not compile or execute native code. Compilation is
deferred until an explicit backend/coarse/filter call.
"""
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

from .native import require_ieee_environment


@lru_cache(maxsize=1)
def library():
    source = Path(__file__).with_name("kernels_packed.cpp")
    flags = ["-O3", "-std=c++17", "-shared", "-fPIC", "-ffp-contract=off", "-fno-fast-math"]
    compiler = shutil.which("g++") or shutil.which("clang++")
    if not compiler:
        raise RuntimeError("A C++17 compiler is required for the packed search variant")
    identity = subprocess.run([compiler, "--version"], check=True, capture_output=True, text=True).stdout
    build_key = repr((flags, compiler, identity, platform.machine(), platform.system())).encode()
    digest = hashlib.sha256(source.read_bytes() + build_key).hexdigest()[:16]
    directory = source.parent / ".build"
    directory.mkdir(exist_ok=True)
    target = directory / f"packed_{digest}.so"
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
    for name in ("etex_packed_coarse", "etex_packed_coarse_portable"):
        fn = getattr(lib, name)
        fn.argtypes = [p, p, p, p, ct.c_double, size, size, p]
        fn.restype = ct.c_int
    for name in ("etex_packed_global", "etex_packed_global_portable"):
        fn = getattr(lib, name)
        fn.argtypes = [p, p, p, p, ct.c_double, ct.c_double, size, size, size, p, p, p]
        fn.restype = ct.c_int
    lib.etex_packed_backend.argtypes = []
    lib.etex_packed_backend.restype = ct.c_char_p
    return lib


def backend():
    return library().etex_packed_backend().decode()


def _inputs(packed, scales, sums, qcodes, qscale, dimensions):
    if (isinstance(dimensions, (bool, np.bool_)) or not isinstance(dimensions, (int, np.integer))
            or not 1 <= dimensions <= 65536):
        raise ValueError("dimensions must be an integer in 1..65536")
    d = int(dimensions)
    scales, sums, packed = np.asarray(scales), np.asarray(sums), np.asarray(packed)
    if scales.dtype != np.float32 or scales.ndim != 1 or len(scales) == 0:
        raise ValueError("scales must be a nonempty FP32 vector")
    n = len(scales)
    if sums.dtype != np.int32 or sums.shape != (n,):
        raise ValueError("sums must be a matching int32 vector")
    if (packed.dtype != np.int8 or not packed.flags.c_contiguous
            or packed.shape != ((n + 15) // 16, (d + 3) // 4, 16, 4)):
        raise ValueError("invalid contiguous tile16/quad4 code array")
    scales, sums = np.ascontiguousarray(scales), np.ascontiguousarray(sums)
    qcodes = np.asarray(qcodes)
    if qcodes.dtype != np.int8 or qcodes.shape != (d,):
        raise ValueError("query codes must be a matching int8 vector")
    if not np.isfinite(qscale) or qscale <= 0:
        raise ValueError("query scale must be positive and finite")
    # Past-D bytes are zero unsigned values; valid packed dimension padding is
    # also zero. The correction uses the sum over original logical coordinates.
    unsigned = np.zeros(((d + 3) // 4) * 4, dtype=np.uint8)
    unsigned[:d] = np.ascontiguousarray(qcodes).view(np.uint8) ^ np.uint8(0x80)
    qwords = unsigned.view("<u4")
    require_ieee_environment()
    return packed, scales, sums, qwords, n, d


def coarse_scores(packed, scales, sums, qcodes, qscale, dimensions, *, portable=False):
    packed, scales, sums, qwords, n, d = _inputs(packed, scales, sums, qcodes, qscale, dimensions)
    out = np.empty(n, dtype=np.float64)
    fn = library().etex_packed_coarse_portable if portable else library().etex_packed_coarse
    status = fn(packed.ctypes.data, scales.ctypes.data, sums.ctypes.data, qwords.ctypes.data,
                float(qscale), n, d, out.ctypes.data)
    if status < 0:
        raise RuntimeError("Packed coarse scorer rejected its arguments or floating-point environment")
    return out


def global_bounds(packed, scales, sums, qcodes, qscale, radius, dimensions, k, *, portable=False):
    packed, scales, sums, qwords, n, d = _inputs(packed, scales, sums, qcodes, qscale, dimensions)
    if (isinstance(k, (bool, np.bool_)) or not isinstance(k, (int, np.integer)) or not 1 <= k <= n):
        raise ValueError("k must be an integer in 1..rows")
    if not np.isfinite(radius) or radius < 0:
        return np.full(n, np.inf, dtype=np.float64), -np.inf, False
    upper = np.empty(n, dtype=np.float64)
    heap = np.empty(int(k), dtype=np.float64)
    threshold = np.empty(1, dtype=np.float64)
    fn = library().etex_packed_global_portable if portable else library().etex_packed_global
    status = fn(packed.ctypes.data, scales.ctypes.data, sums.ctypes.data, qwords.ctypes.data,
                float(qscale), float(radius), n, d, int(k), upper.ctypes.data, heap.ctypes.data,
                threshold.ctypes.data)
    if status < 0:
        raise RuntimeError("Packed bound filter rejected its arguments or floating-point environment")
    return upper, float(threshold[0]), bool(status)
