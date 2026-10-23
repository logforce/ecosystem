"""Separate v2 packed INT8 index with a verified global norm envelope.

Only packed codes, scales and row sums are stored as arrays. Three scalar norm
maxima in the manifest replace the v1 per-row norm arrays. The immutable FP32
source and the exact eight-lane FP64 reference remain the same as in v1.
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
    BoundedIndex, SearchResult, _FORMAT, _MANIFEST_KEYS, _REFERENCE, _SCHEME,
    _close_arrays, _float_block, _gamma, _hash_source_block, _integer, _manifest,
    _outward, _quantize, _sha256_file, _source_batches, _source_shape, _upper_metadata,
)
from .native import coarse_scores as row_coarse_scores, require_ieee_environment
from .native_packed import backend, global_bounds

_VERSION = 2
_LAYOUT = "tile16_quad4"
_NORM_NAMES = ("residual_upper", "xhat_upper", "x_upper")
_DTYPES = {"packed_codes": "|i1", "scales": "<f4", "sums": "<i4"}
_KEYS = _MANIFEST_KEYS | {"layout", "norm_envelope"}
_DEFAULT_CHUNK_ROWS = 8192


def _packed_shape(shape):
    rows, dimensions = shape
    return ((rows + 15) // 16, (dimensions + 3) // 4, 16, 4)


def _working_chunk_rows(value, dimensions):
    """Tile-aligned chunks with a conservative cap on the 2D work arrays.

This limits the row count used by conversion/verification, not total process
memory. Input/source caches, mapped files and outputs remain separate.
    """
    requested = _integer(value, "chunk_rows", 1)
    aligned = ((requested + 15) // 16) * 16
    work_cap = max(16, ((128 * 1024 * 1024) // (64 * dimensions) // 16) * 16)
    return min(aligned, work_cap)


def pack_codes(codes):
    """Copy a nonempty INT8 matrix to tile16/quad4 layout, padding with zeros.

The public helper is also useful for independently checking the native kernel.
The index builder itself quantizes to [-127, 127]; this helper preserves all
INT8 values, including -128.
    """
    codes = np.asarray(codes)
    if codes.dtype != np.int8 or codes.ndim != 2 or min(codes.shape) < 1:
        raise ValueError("codes must be a nonempty two-dimensional INT8 matrix")
    rows, dimensions = codes.shape
    tiles, quads, _, _ = _packed_shape(codes.shape)
    padded = np.zeros((tiles * 16, quads * 4), dtype=np.int8)
    padded[:rows, :dimensions] = codes
    return padded.reshape(tiles, 16, quads, 4).transpose(0, 2, 1, 3).copy()


def _aligned_source_batches(source, shape, chunk_rows):
    # A source scan may choose smaller, unaligned batches. Explicit row ranges
    # keep each packed tile owned by one writer without a full-code temporary.
    rows, dimensions = shape
    for start in range(0, rows, chunk_rows):
        stop = min(rows, start + chunk_rows)
        value = source[start:stop] if isinstance(source, np.ndarray) else source.take(np.arange(start, stop))
        yield start, _float_block(value, (stop - start, dimensions))


def build_packed_index(source, path, *, chunk_rows=_DEFAULT_CHUNK_ROWS) -> dict:
    """Build a separate immutable v2 packed index; refuse an existing path.

Chunk rows are rounded up to a multiple of 16 and capped for very wide input.
Only a chunk of row-major codes exists during construction. All persisted codes
use the packed representation; original FP32 vectors stay in the external source.
    """
    require_ieee_environment()
    shape = _source_shape(source)
    chunk_rows = _working_chunk_rows(chunk_rows, shape[1])
    path = Path(path).absolute()
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Destination already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{path.name}.", dir=path.parent))
    arrays, published = {}, []
    reserved = False
    try:
        for name, dtype in _DTYPES.items():
            array_shape = _packed_shape(shape) if name == "packed_codes" else (shape[0],)
            arrays[name] = np.lib.format.open_memmap(stage / f"{name}.npy", mode="w+", dtype=dtype,
                                                    shape=array_shape, version=(1, 0))
        source_hash = hashlib.sha256()
        envelope = dict.fromkeys(_NORM_NAMES, 0.0)
        for start, block in _aligned_source_batches(source, shape, chunk_rows):
            stop = start + len(block)
            codes, scales, sums = _quantize(block)
            required = _upper_metadata(block, codes, scales)
            for name, upper in zip(_NORM_NAMES, required):
                envelope[name] = max(envelope[name], float(np.max(upper)))
            arrays["packed_codes"][start // 16:(stop + 15) // 16] = pack_codes(codes)
            arrays["scales"][start:stop] = scales
            arrays["sums"][start:stop] = sums
            _hash_source_block(source_hash, block)
        for value in arrays.values():
            value.flush()
        _close_arrays(arrays)
        manifest = {"format": _FORMAT, "version": _VERSION, "scheme": _SCHEME, "reference": _REFERENCE,
                    "layout": _LAYOUT, "shape": list(shape), "source_dtype": "<f4",
                    "source_sha256": source_hash.hexdigest(), "chunk_rows": chunk_rows,
                    "norm_envelope": envelope, "files": {}}
        for name, dtype in _DTYPES.items():
            file = stage / f"{name}.npy"
            array_shape = _packed_shape(shape) if name == "packed_codes" else (shape[0],)
            manifest["files"][file.name] = {"sha256": _sha256_file(file), "bytes": file.stat().st_size,
                                           "dtype": dtype, "shape": list(array_shape)}
            with file.open("rb") as stream:
                os.fsync(stream.fileno())
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
        (stage / "manifest.json").write_bytes(manifest_bytes)
        with (stage / "manifest.json").open("rb") as stream:
            os.fsync(stream.fileno())
        path.mkdir()
        reserved = True
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


class PackedBoundedIndex(BoundedIndex):
    """Packed global-envelope search with no resident row-major code duplicate.

The original source is externally owned and must remain immutable. Default
opening validates every asset, canonical source bytes, and all global norm
bounds. Explicitly skipping source verification weakens that source binding.
    """
    @classmethod
    def open(cls, path, source, *, verify_source=True, skip_reason=None, chunk_rows=_DEFAULT_CHUNK_ROWS):
        require_ieee_environment()
        if not isinstance(verify_source, bool):
            raise ValueError("verify_source must be a boolean")
        if not verify_source and (not isinstance(skip_reason, str) or not skip_reason.strip()):
            raise ValueError("Skipping source verification requires an explicit nonempty skip_reason")
        self = cls()
        self.path = Path(path).absolute()
        self.source = source
        self.shape = _source_shape(source)
        self.chunk_rows = _working_chunk_rows(chunk_rows, self.shape[1])
        self.verified_source = False
        self.skip_reason = None if verify_source else skip_reason.strip()
        try:
            if self.path.is_symlink() or not self.path.is_dir():
                raise ValueError("Packed index must be a real directory")
            manifest_path = self.path / "manifest.json"
            manifest = _manifest(manifest_path)
            if not isinstance(manifest, dict) or set(manifest) != _KEYS:
                raise ValueError("Invalid packed index manifest fields")
            if (manifest["format"] != _FORMAT or _integer(manifest["version"], "version", 1) != _VERSION
                    or manifest["scheme"] != _SCHEME
                    or manifest["reference"] != _REFERENCE or manifest["source_dtype"] != "<f4"
                    or manifest["layout"] != _LAYOUT):
                raise ValueError("Unsupported packed format, layout, or scoring contract")
            if (not isinstance(manifest["shape"], list) or len(manifest["shape"]) != 2
                    or tuple(_integer(x, "shape", 1) for x in manifest["shape"]) != self.shape):
                raise ValueError("Packed index shape does not match the source")
            recorded_chunk = _integer(manifest["chunk_rows"], "manifest chunk_rows", 16)
            if recorded_chunk % 16:
                raise ValueError("Packed builder chunk size must be tile aligned")
            source_sha = manifest["source_sha256"]
            if (not isinstance(source_sha, str) or len(source_sha) != 64
                    or any(c not in "0123456789abcdef" for c in source_sha)):
                raise ValueError("Invalid source fingerprint")
            envelope = manifest["norm_envelope"]
            if not isinstance(envelope, dict) or set(envelope) != set(_NORM_NAMES):
                raise ValueError("Invalid global norm envelope fields")
            self._norm_envelope = {}
            for name, value in envelope.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("Global norm envelope must contain finite nonnegative numbers")
                try:
                    value = float(value)
                except (OverflowError, ValueError) as error:
                    raise ValueError("Invalid global norm envelope number") from error
                if not np.isfinite(value) or value < 0:
                    raise ValueError("Global norm envelope must be finite and nonnegative")
                self._norm_envelope[name] = value
            files = manifest["files"]
            if not isinstance(files, dict) or set(files) != {f"{name}.npy" for name in _DTYPES}:
                raise ValueError("Unexpected packed index file set")
            for name, dtype in _DTYPES.items():
                file = self.path / f"{name}.npy"
                meta = files[file.name]
                expected_shape = _packed_shape(self.shape) if name == "packed_codes" else (self.shape[0],)
                expected_bytes = int(np.prod(expected_shape, dtype=object)) * np.dtype(dtype).itemsize
                if not isinstance(meta, dict) or set(meta) != {"sha256", "bytes", "dtype", "shape"}:
                    raise ValueError("Invalid packed array metadata")
                array_shape = meta["shape"]
                if (not isinstance(array_shape, list) or len(array_shape) != len(expected_shape)
                        or tuple(_integer(x, "array shape", 1) for x in array_shape) != expected_shape
                        or meta["dtype"] != dtype or file.is_symlink() or not file.is_file()):
                    raise ValueError("Invalid packed array path, shape, or dtype")
                file_sha = meta["sha256"]
                if (not isinstance(file_sha, str) or len(file_sha) != 64
                        or any(c not in "0123456789abcdef" for c in file_sha)):
                    raise ValueError("Invalid packed array SHA-256")
                size = _integer(meta["bytes"], "file byte count", expected_bytes + 1, expected_bytes + 4096)
                if file.stat().st_size != size or _sha256_file(file) != file_sha:
                    raise ValueError(f"Packed array size or SHA-256 mismatch: {file.name}")
                with file.open("rb") as stream:
                    if stream.read(8) != b"\x93NUMPY\x01\x00":
                        raise ValueError("Packed arrays must use the canonical NPY version-1 representation")
                value = np.load(file, mmap_mode="r", allow_pickle=False, max_header_size=4096)
                if (value.dtype != np.dtype(dtype) or value.shape != expected_shape
                        or not value.flags.c_contiguous or value.offset + value.nbytes != size):
                    value._mmap.close()
                    raise ValueError("Unexpected packed NPY representation or trailing bytes")
                self._arrays[name] = value
            self.source_sha256 = source_sha
            self.manifest_sha256 = _sha256_file(manifest_path)
            self.file_bytes = manifest_path.stat().st_size + sum(meta["bytes"] for meta in files.values())
            self.index_bytes = sum(value.nbytes for value in self._arrays.values())
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

    def _unpack_rows(self, start, stop):
        """Owned row-major codes for one bounded startup/reference chunk only."""
        tiles = self._arrays["packed_codes"][start // 16:(stop + 15) // 16]
        padded = tiles.transpose(0, 2, 1, 3).copy().reshape(len(tiles) * 16, -1)
        return np.ascontiguousarray(padded[start % 16:start % 16 + stop - start, :self.shape[1]])

    def _validate_metadata(self):
        rows, dimensions = self.shape
        for start in range(0, rows, self.chunk_rows):
            stop = min(rows, start + self.chunk_rows)
            codes = self._unpack_rows(start, stop)
            scales = self._arrays["scales"][start:stop]
            if np.any(codes == -128):
                raise ValueError("Packed index code is outside [-127, 127]")
            if not np.array_equal(np.sum(codes, axis=1, dtype=np.int32), self._arrays["sums"][start:stop]):
                raise ValueError("Packed INT8 row sums do not match codes")
            if not np.all(np.isfinite(scales)) or np.any(scales <= 0):
                raise ValueError("Packed index scales must be finite and positive")
            if dimensions % 4:
                tail = self._arrays["packed_codes"][start // 16:(stop + 15) // 16, -1, :, dimensions % 4:]
                if np.any(tail != 0):
                    raise ValueError("Packed dimension padding must be zero")
        if rows % 16 and np.any(self._arrays["packed_codes"][-1, :, rows % 16:, :] != 0):
            raise ValueError("Packed row padding must be zero")

    def _verify_source(self):
        hasher = hashlib.sha256()
        for start, block in _source_batches(self.source, self.shape, self.chunk_rows):
            stop = start + len(block)
            _hash_source_block(hasher, block)
            required = _upper_metadata(block, self._unpack_rows(start, stop), self._arrays["scales"][start:stop])
            for name, upper in zip(_NORM_NAMES, required):
                if np.any(upper > self._norm_envelope[name]):
                    raise ValueError("Source changed or stored global norm bound is insufficient")
        if hasher.hexdigest() != self.source_sha256:
            raise ValueError("Source FP32 fingerprint does not match the packed index")

    def _query_metadata(self, query):
        codes, scales, _ = _quantize(query[None, :])
        residual_q, qhat, q = _upper_metadata(query[None, :], codes, scales)
        envelope = self._norm_envelope
        with np.errstate(over="ignore", invalid="ignore", under="ignore"):
            radius = _outward(q[0] * envelope["residual_upper"])
            radius += _outward(residual_q[0] * envelope["xhat_upper"])
            radius += _outward(_gamma(self.shape[1] + 8) * q[0] * envelope["x_upper"])
            radius += _outward(_gamma(3) * qhat[0] * envelope["xhat_upper"])
            radius = _outward(radius)
        return codes[0], scales[0], float(radius)

    def _filter_global_bounds(self, query, k):
        codes, scale, radius = self._query_metadata(query)
        a = self._arrays
        upper, threshold, valid = global_bounds(a["packed_codes"], a["scales"], a["sums"],
                                               codes, scale, radius, self.shape[1], k)
        return upper, threshold, valid, radius

    def _bounds(self, query):
        # Deliberately independent from the tiled native scorer: bounded chunks
        # are unpacked and passed through the established row-major primitive.
        codes, scale, radius = self._query_metadata(query)
        coarse = np.empty(self.shape[0], dtype=np.float64)
        for start in range(0, self.shape[0], self.chunk_rows):
            stop = min(self.shape[0], start + self.chunk_rows)
            coarse[start:stop] = row_coarse_scores(self._unpack_rows(start, stop), self._arrays["scales"][start:stop],
                                                  self._arrays["sums"][start:stop], codes, scale)
        with np.errstate(over="ignore", invalid="ignore", under="ignore"):
            lower = np.nextafter(coarse - radius, -np.inf)
            upper = np.nextafter(coarse + radius, np.inf)
        return coarse, lower, upper

    def search(self, query, k=10) -> SearchResult:
        """Packed INT8 scan and exact FP64 refinement of all possible winners."""
        return self._search_native(query, k, per_row=False)

    def search_per_row(self, query, k=10):
        raise NotImplementedError("Packed v2 stores global norm maxima; use BoundedIndex v1 for per-row bounds")

    def _finish(self, *args, **kwargs):
        result = super()._finish(*args, **kwargs)
        s = result.stats
        if s["search_path"] == "native_global_envelope":
            s["search_path"] = "native_packed_global_envelope"
            if s["scanned_index_payload_bytes"]:
                s["scanned_index_payload_bytes"] = self.index_bytes
        else:
            s["search_path"] = "python_packed_reference_exact_seed"
        s.update(self._layout_stats())
        return result

    def _layout_stats(self):
        logical = self.shape[0] * self.shape[1]
        return {"format_version": _VERSION, "index_layout": _LAYOUT,
                "packed_code_bytes": self._packed_code_bytes, "logical_code_bytes": logical,
                "code_padding_bytes": self._packed_code_bytes - logical,
                "per_row_norm_array_bytes": 0, "global_norm_scalar_bytes": 24,
                "persistent_row_major_code_bytes": 0}

    def stats(self):
        with self._lock:
            result = super().stats()
            result.update(self._layout_stats())
            result["global_scan_payload_bytes"] = self.index_bytes
            result["per_row_scan_payload_bytes"] = None
            result["working_chunk_rows"] = self.chunk_rows
            result["notes"] = (
                "Index bytes are packed codes including zero padding, FP32 scales and INT32 row sums. "
                "Three FP64 norm maxima are JSON manifest values; full file bytes include all headers and JSON. "
                "No row-major code matrix is retained. Startup and the reference path unpack bounded chunks. "
                "Mapped payload is not resident RAM: O(N) query buffers, source mappings/decoded cache, "
                "outputs, temporary conversion arrays and OS page cache are separate. Scan bytes are logical "
                "input payload, not measured DRAM or SSD traffic. Startup verifies every source/index page."
            )
            return result
