"""Build only the bundled trusted C++ source, with runtime SIMD dispatch."""
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

@lru_cache(maxsize=1)
def library():
    source=Path(__file__).with_name('kernels.cpp')
    flags=['-O3','-std=c++17','-shared','-fPIC','-ffp-contract=off','-fno-fast-math']
    compiler=shutil.which('g++') or shutil.which('clang++')
    if not compiler: raise RuntimeError('A C++17 compiler is required for the native search benchmark')
    identity=subprocess.run([compiler,'--version'],check=True,capture_output=True,text=True).stdout
    build_key=repr((flags,compiler,identity,platform.machine(),platform.system())).encode()
    signature=hashlib.sha256(source.read_bytes()+build_key).hexdigest()[:16]
    directory=source.parent/'.build'; directory.mkdir(exist_ok=True)
    target=directory/f'bounded_{signature}.so'
    if not target.exists():
        # Independent processes may compile simultaneously. Publish only a
        # complete shared object, with a unique temporary name for each build.
        with tempfile.NamedTemporaryFile(prefix=f'.{target.stem}.',suffix='.so',dir=directory,delete=False) as f:
            temp=Path(f.name)
        try:
            subprocess.run([compiler,*flags,str(source),'-o',str(temp)],check=True,capture_output=True,text=True)
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
    lib=ct.CDLL(str(target))
    p=ct.c_void_p; sz=ct.c_size_t
    lib.etex_exact.argtypes=[p,p,sz,sz,p]; lib.etex_exact.restype=None
    lib.etex_exact_portable.argtypes=[p,p,sz,sz,p]; lib.etex_exact_portable.restype=None
    lib.etex_float.argtypes=[p,p,sz,sz,p]; lib.etex_float.restype=None
    lib.etex_coarse.argtypes=[p,p,p,p,ct.c_double,sz,sz,p]; lib.etex_coarse.restype=None
    lib.etex_coarse_portable.argtypes=[p,p,p,p,ct.c_double,sz,sz,p]; lib.etex_coarse_portable.restype=None
    lib.etex_filter_bounds.argtypes=[p,p,p,p,p,p,p,*([ct.c_double]*4),sz,sz,sz,p,p,p]
    lib.etex_filter_bounds.restype=ct.c_int
    lib.etex_filter_global.argtypes=[p,p,p,p,ct.c_double,ct.c_double,sz,sz,sz,p,p,p]
    lib.etex_filter_global.restype=ct.c_int
    lib.etex_search_backend.argtypes=[];lib.etex_search_backend.restype=ct.c_char_p
    lib.etex_round_nearest.argtypes=[];lib.etex_round_nearest.restype=ct.c_int
    lib.etex_ieee_environment.argtypes=[];lib.etex_ieee_environment.restype=ct.c_int
    return lib

def backend(): return library().etex_search_backend().decode()

def require_ieee_environment():
    if not library().etex_ieee_environment():
        raise RuntimeError('A checked x86-64 IEEE environment with round-to-nearest and FTZ/DAZ disabled is required')

def exact_scores(x,q,*,portable=False):
    x=np.asarray(x)
    if x.ndim!=2:raise ValueError('data must be a two-dimensional matrix')
    if x.dtype != np.float32 or not x.flags.c_contiguous:
        x=np.ascontiguousarray(x,dtype=np.float32)
    q=np.ascontiguousarray(q,dtype=np.float64)
    if q.ndim!=1 or q.size!=x.shape[1]:raise ValueError('query dimension mismatch')
    out=np.empty(len(x),dtype=np.float64)
    lib=library()
    require_ieee_environment()
    fn=lib.etex_exact_portable if portable else lib.etex_exact
    fn(x.ctypes.data,q.ctypes.data,*x.shape,out.ctypes.data)
    return out

def float_scores(x,q):
    x=np.ascontiguousarray(x,dtype=np.float32);q=np.ascontiguousarray(q,dtype=np.float32)
    if x.ndim!=2 or q.ndim!=1 or q.size!=x.shape[1]:raise ValueError('shape mismatch')
    require_ieee_environment()
    out=np.empty(len(x),dtype=np.float32)
    library().etex_float(x.ctypes.data,q.ctypes.data,*x.shape,out.ctypes.data)
    return out

def coarse_scores(codes,scales,sums,qcodes,qscale,*,portable=False):
    codes=np.asarray(codes);scales=np.asarray(scales);sums=np.asarray(sums)
    if codes.dtype!=np.int8 or not codes.flags.c_contiguous or codes.ndim!=2:raise ValueError('contiguous int8 codes required')
    n,d=codes.shape
    if not 1<=d<=65536:raise ValueError('unsupported dimension')
    if scales.dtype!=np.float32 or scales.shape!=(n,) or sums.dtype!=np.int32 or sums.shape!=(n,):raise ValueError('invalid metadata')
    # A strided pointer is not a contiguous C array; normalize both metadata
    # arrays before passing their addresses into the kernel.
    scales=np.ascontiguousarray(scales);sums=np.ascontiguousarray(sums)
    qcodes=np.ascontiguousarray(qcodes,dtype=np.int8)
    if qcodes.shape!=(d,):raise ValueError('query shape mismatch')
    if not np.isfinite(qscale) or qscale<=0:raise ValueError('query scale must be positive and finite')
    require_ieee_environment()
    out=np.empty(n,dtype=np.float64)
    fn=library().etex_coarse_portable if portable else library().etex_coarse
    fn(codes.ctypes.data,scales.ctypes.data,sums.ctypes.data,qcodes.ctypes.data,float(qscale),n,d,out.ctypes.data)
    return out

def fused_bounds(codes,scales,sums,residual_upper,xhat_upper,x_upper,
                 qcodes,qscale,qresidual_upper,qhat_upper,q_upper,k):
    """One native INT8/bound pass, retaining upper[N] and a lower-bound heap[k]."""
    codes=np.asarray(codes)
    if codes.dtype!=np.int8 or codes.ndim!=2 or not codes.flags.c_contiguous:
        raise ValueError('contiguous int8 codes required')
    n,d=codes.shape
    if (isinstance(k,(bool,np.bool_)) or not isinstance(k,(int,np.integer))
            or not 1<=k<=n or not 1<=d<=65536):
        raise ValueError('invalid k or dimension')
    metadata=[]
    for value,dtype in ((scales,np.float32),(sums,np.int32),(residual_upper,np.float64),
                        (xhat_upper,np.float64),(x_upper,np.float64)):
        value=np.asarray(value)
        if value.dtype!=dtype or value.shape!=(n,):raise ValueError('invalid bound metadata')
        metadata.append(np.ascontiguousarray(value))
    qcodes=np.ascontiguousarray(qcodes,dtype=np.int8)
    if qcodes.shape!=(d,):raise ValueError('query shape mismatch')
    parameters=tuple(float(v) for v in (qscale,qresidual_upper,qhat_upper,q_upper))
    if not all(np.isfinite(v) and v>=0 for v in parameters) or parameters[0]==0:
        raise ValueError('invalid query scale/norm parameters')
    require_ieee_environment()
    upper=np.empty(n,dtype=np.float64)
    heap=np.empty(int(k),dtype=np.float64)
    threshold=np.empty(1,dtype=np.float64)
    status=library().etex_filter_bounds(codes.ctypes.data,*(v.ctypes.data for v in metadata),
        qcodes.ctypes.data,*parameters,n,d,int(k),upper.ctypes.data,heap.ctypes.data,threshold.ctypes.data)
    if status<0:raise RuntimeError('Native bound filter rejected its arguments or floating-point environment')
    return upper,float(threshold[0]),bool(status)

def global_bounds(codes,scales,sums,qcodes,qscale,radius,k):
    """Scan only codes/scales/sums with one conservative radius for all rows."""
    codes=np.asarray(codes)
    if codes.dtype!=np.int8 or codes.ndim!=2 or not codes.flags.c_contiguous:
        raise ValueError('contiguous int8 codes required')
    n,d=codes.shape
    if (isinstance(k,(bool,np.bool_)) or not isinstance(k,(int,np.integer))
            or not 1<=k<=n or not 1<=d<=65536):
        raise ValueError('invalid k or dimension')
    metadata=[]
    for value,dtype in ((scales,np.float32),(sums,np.int32)):
        value=np.asarray(value)
        if value.dtype!=dtype or value.shape!=(n,):raise ValueError('invalid coarse metadata')
        metadata.append(np.ascontiguousarray(value))
    qcodes=np.ascontiguousarray(qcodes,dtype=np.int8)
    if qcodes.shape!=(d,):raise ValueError('query shape mismatch')
    if not np.isfinite(qscale) or qscale<=0:raise ValueError('invalid query scale')
    require_ieee_environment()
    if not np.isfinite(radius) or radius<0:
        return np.full(n,np.inf,dtype=np.float64),-np.inf,False
    upper=np.empty(n,dtype=np.float64)
    heap=np.empty(int(k),dtype=np.float64)
    threshold=np.empty(1,dtype=np.float64)
    status=library().etex_filter_global(codes.ctypes.data,*(v.ctypes.data for v in metadata),
        qcodes.ctypes.data,float(qscale),float(radius),n,d,int(k),upper.ctypes.data,heap.ctypes.data,threshold.ctypes.data)
    if status<0:raise RuntimeError('Native global filter rejected its arguments or floating-point environment')
    return upper,float(threshold[0]),bool(status)
