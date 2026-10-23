"""Separate v3 packed filter with selected original FP32 columns retained.

The source/reference remain unchanged. Mixed reconstruction is used only to
obtain conservative filter intervals; final scores always use original rows.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

import numpy as np

from .index import (
    _FORMAT, _MANIFEST_KEYS, _REFERENCE, _U, _close_arrays, _gamma, _hash_source_block,
    _integer, _manifest, _norm_upper, _outward, _quantize, _sha256_file, _source_batches, _source_shape,
)
from .native import coarse_scores as row_coarse_scores, require_ieee_environment
from .native_mixed import backend, global_bounds
from .packed_index import PackedBoundedIndex, _aligned_source_batches, _packed_shape, _working_chunk_rows, pack_codes

_VERSION = 3
_LAYOUT = "tile16_quad4_fp32_columns"
_SCHEME = "fp32-int8-plus-exact-columns-v1"
_NORM_NAMES = ("residual_upper", "xhat_upper", "x_upper")
_DTYPES = {"packed_codes": "|i1", "scales": "<f4", "sums": "<i4", "exact_values": "<f4"}
_KEYS = _MANIFEST_KEYS | {"layout", "norm_envelope", "exact_columns"}


def _columns(value, dimensions):
    if isinstance(value, (str, bytes)):
        raise ValueError("exact_columns must be a nonempty sequence of distinct integer column IDs")
    try:
        result = [_integer(x, "exact column", 0, dimensions - 1) for x in value]
    except TypeError as error:
        raise ValueError("exact_columns must be a sequence") from error
    if not result or len(set(result)) != len(result):
        raise ValueError("exact_columns must be nonempty and contain no duplicates")
    return tuple(sorted(result))


def _array_shape(name, shape, columns):
    if name == "packed_codes":
        return _packed_shape(shape)
    if name == "exact_values":
        return (shape[0], len(columns))
    return (shape[0],)


def _mixed_quantize(value, columns):
    tail = np.array(value, dtype=np.float32, order="C", copy=True)
    tail[:, columns] = 0
    return _quantize(tail)


def _mixed_metadata(value, codes, scales, columns):
    x = np.asarray(value, dtype=np.float64)
    reconstructed = codes.astype(np.float64) * scales.astype(np.float64)[:, None]
    reconstructed[:, columns] = x[:, columns]
    x_upper, xhat_upper = _norm_upper(x), _norm_upper(reconstructed)
    residual = x - reconstructed
    residual_upper = _outward(_norm_upper(residual) + 2 * _U * (x_upper + xhat_upper))
    return residual_upper, xhat_upper, x_upper


def build_mixed_packed_index(source, path, *, exact_columns, chunk_rows=8192):
    """Build an immutable v3 index with document-selected FP32 columns.

Column order is canonicalized once at construction; queries cannot change it.
All source columns remain in the original authoritative FP32 matrix.
    """
    require_ieee_environment()
    shape = _source_shape(source)
    columns = _columns(exact_columns, shape[1])
    chunk_rows = _working_chunk_rows(chunk_rows, shape[1])
    path = Path(path).absolute()
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Destination already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{path.name}.", dir=path.parent))
    arrays, published, reserved = {}, [], False
    try:
        for name, dtype in _DTYPES.items():
            arrays[name] = np.lib.format.open_memmap(stage / f"{name}.npy", mode="w+", dtype=dtype,
                shape=_array_shape(name, shape, columns), version=(1, 0))
        source_hash = hashlib.sha256()
        envelope = dict.fromkeys(_NORM_NAMES, 0.0)
        for start, block in _aligned_source_batches(source, shape, chunk_rows):
            stop = start + len(block)
            codes, scales, sums = _mixed_quantize(block, columns)
            for name, upper in zip(_NORM_NAMES, _mixed_metadata(block, codes, scales, columns)):
                envelope[name] = max(envelope[name], float(np.max(upper)))
            arrays["packed_codes"][start // 16:(stop + 15) // 16] = pack_codes(codes)
            arrays["scales"][start:stop] = scales
            arrays["sums"][start:stop] = sums
            arrays["exact_values"][start:stop] = block[:, columns]
            _hash_source_block(source_hash, block)
        for value in arrays.values():
            value.flush()
        _close_arrays(arrays)
        manifest = {"format": _FORMAT, "version": _VERSION, "scheme": _SCHEME, "reference": _REFERENCE,
                    "layout": _LAYOUT, "shape": list(shape), "source_dtype": "<f4",
                    "source_sha256": source_hash.hexdigest(), "chunk_rows": chunk_rows,
                    "exact_columns": list(columns), "norm_envelope": envelope, "files": {}}
        for name, dtype in _DTYPES.items():
            file = stage / f"{name}.npy"
            manifest["files"][file.name] = {"sha256": _sha256_file(file), "bytes": file.stat().st_size,
                "dtype": dtype, "shape": list(_array_shape(name, shape, columns))}
            with file.open("rb") as stream:
                os.fsync(stream.fileno())
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n")
        with (stage / "manifest.json").open("rb") as stream:
            os.fsync(stream.fileno())
        path.mkdir(); reserved = True
        for name in [f"{name}.npy" for name in _DTYPES] + ["manifest.json"]:
            os.link(stage / name, path / name)
            published.append(path / name)
        return manifest
    except BaseException:
        if reserved:
            for file in published:
                file.unlink(missing_ok=True)
            try:
                path.rmdir()
            except OSError:
                pass
        raise
    finally:
        _close_arrays(arrays)
        shutil.rmtree(stage, ignore_errors=True)


class MixedPackedBoundedIndex(PackedBoundedIndex):
    """Independent v3 reader; v1/v2 artifacts and code paths remain unchanged."""
    @classmethod
    def open(cls, path, source, *, verify_source=True, skip_reason=None, chunk_rows=8192):
        require_ieee_environment()
        if not isinstance(verify_source, bool):
            raise ValueError("verify_source must be a boolean")
        if not verify_source and (not isinstance(skip_reason, str) or not skip_reason.strip()):
            raise ValueError("Skipping source verification requires an explicit nonempty skip_reason")
        self = cls()
        self.path, self.source = Path(path).absolute(), source
        self.shape = _source_shape(source)
        self.chunk_rows = _working_chunk_rows(chunk_rows, self.shape[1])
        self.verified_source = False
        self.skip_reason = None if verify_source else skip_reason.strip()
        try:
            if self.path.is_symlink() or not self.path.is_dir():
                raise ValueError("Mixed index must be a real directory")
            manifest_path = self.path / "manifest.json"
            manifest = _manifest(manifest_path)
            if not isinstance(manifest, dict) or set(manifest) != _KEYS:
                raise ValueError("Invalid mixed index manifest fields")
            if (manifest["format"] != _FORMAT or _integer(manifest["version"], "version", 1) != _VERSION
                    or manifest["scheme"] != _SCHEME or manifest["reference"] != _REFERENCE
                    or manifest["source_dtype"] != "<f4" or manifest["layout"] != _LAYOUT):
                raise ValueError("Unsupported mixed format, layout, or scoring contract")
            if (not isinstance(manifest["shape"], list) or len(manifest["shape"]) != 2
                    or tuple(_integer(x, "shape", 1) for x in manifest["shape"]) != self.shape):
                raise ValueError("Mixed index shape does not match source")
            chunk = _integer(manifest["chunk_rows"], "manifest chunk rows", 16)
            if chunk % 16:
                raise ValueError("Mixed chunk size must be tile aligned")
            if not isinstance(manifest["exact_columns"], list):
                raise ValueError("exact_columns must be a canonical JSON list")
            self.exact_columns = _columns(manifest["exact_columns"], self.shape[1])
            if list(self.exact_columns) != manifest["exact_columns"]:
                raise ValueError("exact_columns must be sorted in the manifest")
            source_sha = manifest["source_sha256"]
            if (not isinstance(source_sha, str) or len(source_sha) != 64
                    or any(c not in "0123456789abcdef" for c in source_sha)):
                raise ValueError("Invalid source fingerprint")
            envelope = manifest["norm_envelope"]
            if not isinstance(envelope, dict) or set(envelope) != set(_NORM_NAMES):
                raise ValueError("Invalid mixed norm envelope")
            self._norm_envelope = {}
            for name, value in envelope.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("Mixed norms must be finite nonnegative numbers")
                try:
                    value = float(value)
                except (OverflowError, ValueError) as error:
                    raise ValueError("Invalid mixed norm value") from error
                if not np.isfinite(value) or value < 0:
                    raise ValueError("Mixed norms must be finite and nonnegative")
                self._norm_envelope[name] = value
            files = manifest["files"]
            if not isinstance(files, dict) or set(files) != {f"{name}.npy" for name in _DTYPES}:
                raise ValueError("Unexpected mixed index file set")
            for name, dtype in _DTYPES.items():
                file = self.path / f"{name}.npy"
                meta = files[file.name]
                expected_shape = _array_shape(name, self.shape, self.exact_columns)
                expected_bytes = int(np.prod(expected_shape, dtype=object)) * np.dtype(dtype).itemsize
                if not isinstance(meta, dict) or set(meta) != {"sha256", "bytes", "dtype", "shape"}:
                    raise ValueError("Invalid mixed array metadata")
                array_shape = meta["shape"]
                if (not isinstance(array_shape, list) or len(array_shape) != len(expected_shape)
                        or tuple(_integer(x, "array shape", 1) for x in array_shape) != expected_shape
                        or meta["dtype"] != dtype or file.is_symlink() or not file.is_file()):
                    raise ValueError("Invalid mixed array path, shape, or dtype")
                sha = meta["sha256"]
                if (not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)):
                    raise ValueError("Invalid mixed array SHA-256")
                size = _integer(meta["bytes"], "file bytes", expected_bytes + 1, expected_bytes + 4096)
                if file.stat().st_size != size or _sha256_file(file) != sha:
                    raise ValueError(f"Mixed array size or SHA-256 mismatch: {file.name}")
                with file.open("rb") as stream:
                    if stream.read(8) != b"\x93NUMPY\x01\x00":
                        raise ValueError("Mixed arrays must use canonical NPY version 1")
                value = np.load(file, mmap_mode="r", allow_pickle=False, max_header_size=4096)
                if (value.dtype != np.dtype(dtype) or value.shape != expected_shape
                        or not value.flags.c_contiguous or value.offset + value.nbytes != size):
                    value._mmap.close()
                    raise ValueError("Unexpected mixed NPY representation or trailing bytes")
                self._arrays[name] = value
            self.source_sha256 = source_sha
            self.manifest_sha256 = _sha256_file(manifest_path)
            self.file_bytes = manifest_path.stat().st_size + sum(meta["bytes"] for meta in files.values())
            self.index_bytes = sum(a.nbytes for a in self._arrays.values())
            self._packed_code_bytes = self._arrays["packed_codes"].nbytes
            self.source_bytes = self.shape[0] * self.shape[1] * 4
            self.native_backend = backend()
            self._validate_metadata()
            if verify_source:
                self._verify_source()
                self.verified_source = True
            self._closed = False
            return self
        except BaseException:
            self.close()
            raise

    def _validate_metadata(self):
        super()._validate_metadata()
        for start in range(0, self.shape[0], self.chunk_rows):
            stop = min(self.shape[0], start + self.chunk_rows)
            if np.any(self._unpack_rows(start, stop)[:, self.exact_columns] != 0):
                raise ValueError("Codes of exact columns must be zero")
            if not np.all(np.isfinite(self._arrays["exact_values"][start:stop])):
                raise ValueError("Exact source columns must be finite FP32")

    def _verify_source(self):
        hasher = hashlib.sha256()
        for start, block in _source_batches(self.source, self.shape, self.chunk_rows):
            stop = start + len(block)
            _hash_source_block(hasher, block)
            expected = np.ascontiguousarray(block[:, self.exact_columns], dtype=np.float32)
            stored = self._arrays["exact_values"][start:stop]
            if not np.array_equal(stored.view(np.uint32), expected.view(np.uint32)):
                raise ValueError("Exact columns differ bitwise from the original FP32 source")
            required = _mixed_metadata(block, self._unpack_rows(start, stop), self._arrays["scales"][start:stop], self.exact_columns)
            for name, upper in zip(_NORM_NAMES, required):
                if np.any(upper > self._norm_envelope[name]):
                    raise ValueError("Source changed or stored mixed norm bound is insufficient")
        if hasher.hexdigest() != self.source_sha256:
            raise ValueError("Source FP32 fingerprint does not match mixed index")

    def _query_metadata(self, query):
        codes, scales, _ = _mixed_quantize(query[None, :], self.exact_columns)
        residual_q, qhat, q = _mixed_metadata(query[None, :], codes, scales, self.exact_columns)
        e = self._norm_envelope
        with np.errstate(over="ignore", invalid="ignore", under="ignore"):
            radius = _outward(q[0] * e["residual_upper"])
            radius += _outward(residual_q[0] * e["xhat_upper"])
            radius += _outward(_gamma(self.shape[1] + 8) * q[0] * e["x_upper"])
            # Two rounded scale multiplications and K sequential additions.
            # gamma(K+3) conservatively covers gamma(K+2); the absolute term
            # sum is bounded by the full mixed reconstruction norm product.
            radius += _outward(_gamma(len(self.exact_columns) + 3) * qhat[0] * e["xhat_upper"])
            radius = _outward(radius)
        exact_query = np.ascontiguousarray(query[list(self.exact_columns)], dtype=np.float32)
        return codes[0], scales[0], float(radius), exact_query

    def _filter_global_bounds(self, query, k):
        codes, scale, radius, exact_query = self._query_metadata(query)
        a = self._arrays
        upper, threshold, valid = global_bounds(a["packed_codes"], a["scales"], a["sums"], codes, scale,
            a["exact_values"], exact_query, radius, self.shape[1], k)
        return upper, threshold, valid, radius

    def _bounds(self, query):
        codes, scale, radius, exact_query = self._query_metadata(query)
        coarse = np.empty(self.shape[0], dtype=np.float64)
        for start in range(0, self.shape[0], self.chunk_rows):
            stop = min(self.shape[0], start + self.chunk_rows)
            block = row_coarse_scores(self._unpack_rows(start, stop), self._arrays["scales"][start:stop],
                                     self._arrays["sums"][start:stop], codes, scale)
            for j in range(len(self.exact_columns)):
                block += self._arrays["exact_values"][start:stop, j].astype(np.float64) * float(exact_query[j])
            coarse[start:stop] = block
        with np.errstate(over="ignore", invalid="ignore", under="ignore"):
            lower = np.nextafter(coarse - radius, -np.inf)
            upper = np.nextafter(coarse + radius, np.inf)
        return coarse, lower, upper

    def search_per_row(self, query, k=10):
        raise NotImplementedError("Mixed v3 stores global norm maxima; per-row bounds are not persisted")

    def _finish(self, *args, **kwargs):
        result = super()._finish(*args, **kwargs)
        s = result.stats
        s["search_path"] = ("native_mixed_packed_global_envelope" if s["search_path"] == "native_packed_global_envelope"
                            else "python_mixed_reference_exact_seed")
        return result

    def _layout_stats(self):
        result = super()._layout_stats()
        result.update({"format_version": _VERSION, "index_layout": _LAYOUT,
            "exact_columns": list(self.exact_columns), "exact_column_count": len(self.exact_columns),
            "exact_column_payload_bytes": self.shape[0] * len(self.exact_columns) * 4,
            "coarse_roundoff_gamma_operations": len(self.exact_columns) + 3})
        return result

    def stats(self):
        with self._lock:
            result = super().stats()
            result["notes"] = (
                "Mixed v3 array payload includes padded packed codes, FP32 scales, INT32 row sums, "
                "and the selected original FP32 column values. Norm maxima and selected column IDs are in "
                "the manifest; file bytes include all headers/JSON. Final scores use the unchanged external "
                "FP32 source. No row-major code matrix is retained. Startup/reference chunks, O(N) query "
                "buffers, outputs, source pages/decoded cache and OS page cache are separate. Payload and "
                "logical scan bytes are not RSS or measured memory/disk traffic."
            )
            return result
