"""Minimal synchronous ctypes binding to the inspected CogPOSIX ABI 0.2.

This example copies inline U8 buffers. It is not shared memory or zero copy.
See component LICENSE.md.
"""
from __future__ import annotations

import ctypes as ct
from pathlib import Path

from .protocol import (CogStatus, REQUEST_ENVELOPE_BYTES, RESPONSE_ENVELOPE_BYTES,
                       parse_result_envelope, request_envelope)

CAPABILITY = b"etex.rag.search.v1"


class CogError(RuntimeError):
    def __init__(self, status, operation):
        self.status = int(status)
        # operation is always a fixed symbol written in this module, never input.
        self.operation = operation
        super().__init__(f"CogPOSIX {operation} failed with status {self.status}")


class TensorDescriptor(ct.Structure):
    _fields_ = [("struct_size", ct.c_uint32), ("dtype", ct.c_uint32),
                ("rank", ct.c_uint32), ("flags", ct.c_uint32),
                ("offset", ct.c_uint64), ("shape", ct.c_uint64 * 8)]


def tensor_descriptor(size):
    if size not in (REQUEST_ENVELOPE_BYTES, RESPONSE_ENVELOPE_BYTES):
        raise ValueError("Unsupported bridge tensor size")
    result = TensorDescriptor()
    result.struct_size = ct.sizeof(TensorDescriptor)
    result.dtype = 1  # COG_DTYPE_U8
    result.rank = 1
    result.shape[0] = size
    return result


def load_library(path):
    if ct.sizeof(ct.c_void_p) != 8 or ct.sizeof(TensorDescriptor) != 88:
        raise RuntimeError("This example requires the documented 64-bit native CogPOSIX layout")
    lib = ct.CDLL(str(Path(path).resolve(strict=True)))
    u64, u32, i32, u8 = ct.c_uint64, ct.c_uint32, ct.c_int32, ct.c_uint8
    p64, p32, pi32 = ct.POINTER(u64), ct.POINTER(u32), ct.POINTER(i32)
    signatures = {
        "cog_context_create": [ct.c_char_p, p64], "cog_context_destroy": [u64],
        "cog_context_features": [u64, p64],
        "cog_model_open": [u64, ct.c_char_p, p64], "cog_model_close": [u64, u64],
        "cog_buffer_alloc": [u64, u64, p64], "cog_buffer_free": [u64, u64],
        "cog_buffer_write": [u64, u64, ct.POINTER(u8), u64],
        "cog_buffer_read": [u64, u64, ct.POINTER(u8), u64, p64],
        "cog_tensor_create": [u64, u64, ct.POINTER(TensorDescriptor), p64],
        "cog_tensor_release": [u64, u64],
        "cog_infer_submit": [u64, u64, u64, u64, u32, p64],
        "cog_job_status": [u64, u64, p32, pi32],
        "cog_job_wait": [u64, u64, u64], "cog_job_cancel": [u64, u64],
        "cog_job_release": [u64, u64],
    }
    for name, arguments in signatures.items():
        function = getattr(lib, name)
        function.argtypes = arguments
        function.restype = i32
    return lib


def _check(status, operation):
    if status != 0:
        raise CogError(status, operation)


def search(library_path, socket_path, request, *, timeout_ms=120000):
    """Submit one envelope as U8[4096], accept only COMPLETED U8[65536].

    cog_job_wait timeout does not itself cancel. This example explicitly requests
    cancellation before destroying the context on a wait timeout. Individual
    CogPOSIX IPC operations may themselves block; this is not a hard deadline API.
    Do not use a context after fork or concurrently destroy it.
    """
    payload = request_envelope(request)  # validate before loading the C library
    if type(timeout_ms) is not int or not 0 < timeout_ms <= 3600000:
        raise ValueError("Invalid example timeout")
    socket = str(socket_path).encode("utf-8", "strict")
    if not socket or b"\0" in socket:
        raise ValueError("Invalid socket path")
    lib = load_library(library_path)
    context = ct.c_uint64()
    _check(lib.cog_context_create(socket, ct.byref(context)), "context_create")
    model = ct.c_uint64()
    buffers, tensors = [], []
    job = ct.c_uint64()
    terminal = False
    try:
        features = ct.c_uint64()
        _check(lib.cog_context_features(context, ct.byref(features)), "context_features")
        if features.value & 1 == 0:
            raise CogError(CogStatus.UNSUPPORTED, "inline_io_feature")
        _check(lib.cog_model_open(context, CAPABILITY, ct.byref(model)), "model_open")
        for size in (REQUEST_ENVELOPE_BYTES, RESPONSE_ENVELOPE_BYTES):
            buffer = ct.c_uint64()
            _check(lib.cog_buffer_alloc(context, size, ct.byref(buffer)), "buffer_alloc")
            buffers.append(buffer)
        input_bytes = (ct.c_uint8 * len(payload)).from_buffer_copy(payload)
        _check(lib.cog_buffer_write(context, buffers[0], input_bytes, len(payload)), "buffer_write")
        for buffer, size in zip(buffers, (REQUEST_ENVELOPE_BYTES, RESPONSE_ENVELOPE_BYTES)):
            tensor = ct.c_uint64()
            descriptor = tensor_descriptor(size)
            _check(lib.cog_tensor_create(context, buffer, ct.byref(descriptor), ct.byref(tensor)), "tensor_create")
            tensors.append(tensor)
        _check(lib.cog_infer_submit(context, model, tensors[0], tensors[1], 0, ct.byref(job)), "infer_submit")
        wait_status = lib.cog_job_wait(context, job, timeout_ms)
        if wait_status == int(CogStatus.TIMEOUT):
            lib.cog_job_cancel(context, job)
        _check(wait_status, "job_wait")
        state, execution_error = ct.c_uint32(), ct.c_int32()
        _check(lib.cog_job_status(context, job, ct.byref(state), ct.byref(execution_error)), "job_status")
        terminal = state.value in (3, 4, 5)
        if state.value != 3 or execution_error.value != 0:
            raise CogError(execution_error.value or CogStatus.BACKEND, "job_execution")
        output, written = (ct.c_uint8 * RESPONSE_ENVELOPE_BYTES)(), ct.c_uint64()
        _check(lib.cog_buffer_read(context, buffers[1], output, len(output), ct.byref(written)), "buffer_read")
        if written.value != RESPONSE_ENVELOPE_BYTES:
            raise CogError(CogStatus.BACKEND, "output_length")
        result = parse_result_envelope(bytes(output), expected_status=0)
        if result["request_id"] != request["request_id"]:
            raise CogError(CogStatus.BACKEND, "request_id_match")
        return result
    finally:
        # Resource calls can themselves fail after a transport failure. Context
        # teardown is the final owner cleanup; no result is accepted on failure.
        if job.value:
            if not terminal:
                lib.cog_job_cancel(context, job)
            lib.cog_job_release(context, job)
        for tensor in reversed(tensors):
            lib.cog_tensor_release(context, tensor)
        for buffer in reversed(buffers):
            lib.cog_buffer_free(context, buffer)
        if model.value:
            lib.cog_model_close(context, model)
        lib.cog_context_destroy(context)
