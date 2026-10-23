"""INT8 full-scan filtering with exact FP32-source/FP64-reference top-k.

This is an implementation of established filter-and-refine ideas (VA-File,
FEXIPRO, EAHR/PVS), not a new search algorithm. Original rows remain external.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading

import numpy as np

from .native import backend, coarse_scores, exact_scores, fused_bounds, global_bounds, require_ieee_environment

_FORMAT = "etex-bounded-index"
_VERSION = 1
_SCHEME = "fp32-int8-residual-upper-v1"
_REFERENCE = "fp32-products-fp64-eight-lanes-no-fma-v1"
_U = 2.0 ** -53
_CHUNK_ROWS = 8192
_ARRAY_DTYPES = {"codes": "|i1", "scales": "<f4", "sums": "<i4",
                 "residual_upper": "<f8", "xhat_upper": "<f8", "x_upper": "<f8"}
_MANIFEST_KEYS = {"format", "version", "scheme", "reference", "shape", "source_dtype",
                  "source_sha256", "chunk_rows", "files"}


def _integer(value, name, minimum=0, maximum=None):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer")
    value = int(value)
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} is outside the supported range")
    return value


def _gamma(n):
    return (n * _U) / (1.0 - n * _U)


def _outward(value):
    """Inflate a nonnegative expression by more than its local roundoff."""
    return np.nextafter(value * (1.0 + _gamma(16)), np.inf)


def _norm_upper(value):
    """Conservative Euclidean norm for finite FP32-derived FP64 vectors."""
    value = np.asarray(value, dtype=np.float64)
    d = value.shape[-1]
    squared = np.sum(value * value, axis=-1, dtype=np.float64)
    upper = np.sqrt(squared / (1.0 - _gamma(2 * d + 4))) * (1.0 + 8 * _U)
    return np.nextafter(upper, np.inf)


def _upper_metadata(value, codes, scales):
    x = np.asarray(value, dtype=np.float64)
    # An int8 times an FP32 scale has at most 31 significand bits: exact in FP64.
    xhat = codes.astype(np.float64) * scales.astype(np.float64)[:, None]
    x_upper, xhat_upper = _norm_upper(x), _norm_upper(xhat)
    residual = x - xhat
    # The stored residual norm must also cover subtraction rounding.
    residual_upper = _outward(_norm_upper(residual) + 2 * _U * (x_upper + xhat_upper))
    return residual_upper, xhat_upper, x_upper


def _quantize(value):
    maxima = np.max(np.abs(value), axis=1)
    with np.errstate(under="ignore"):
        scales = np.asarray(maxima / np.float32(127), dtype=np.float32)
    # Includes underflow of maxabs/127 on very small FP32 subnormals.
    scales[scales == 0] = np.float32(1)
    codes = np.clip(np.rint(value.astype(np.float64) / scales.astype(np.float64)[:, None]),
                    -127, 127).astype(np.int8)
    sums = np.sum(codes, axis=1, dtype=np.int32)
    return codes, scales, sums


def _source_shape(source):
    shape = getattr(source, "shape", None)
    dtype = np.dtype(getattr(source, "dtype", object))
    if shape is None or len(shape) != 2:
        raise ValueError("source must be a two-dimensional FP32 matrix")
    rows = _integer(shape[0], "rows", 1)
    cols = _integer(shape[1], "columns", 1, 65536)
    if dtype.kind != "f" or dtype.itemsize != 4:
        raise TypeError("source must contain original FP32 values; conversion must be explicit")
    if not isinstance(source, np.ndarray) and not callable(getattr(source, "take", None)):
        raise TypeError("source must be an ndarray/memmap or expose row-wise take(ids)")
    return rows, cols


def _float_block(value, expected_shape):
    value = np.asarray(value)
    if value.shape != expected_shape or value.dtype.kind != "f" or value.dtype.itemsize != 4:
        raise ValueError("source returned the wrong matrix shape or FP32 dtype")
    # Finite FP32 values preserve their numerical representation when normalizing
    # byte order. No FP16, INT8, or other numerical conversion is performed here.
    value = np.ascontiguousarray(value, dtype=np.float32)
    if not np.all(np.isfinite(value)):
        raise ValueError("source contains a nonfinite FP32 value")
    return value


def _source_batches(source, shape, chunk_rows):
    rows, cols = shape
    if isinstance(source, np.ndarray):
        for start in range(0, rows, chunk_rows):
            stop = min(rows, start + chunk_rows)
            yield start, _float_block(source[start:stop], (stop - start, cols))
        return
    scan = getattr(source, "scan_batches", None)
    if callable(scan):
        start = 0
        for block in scan(chunk_rows):
            block = np.asarray(block)
            if block.ndim != 2 or not 1 <= len(block) <= chunk_rows or start + len(block) > rows:
                raise ValueError("source scan returned invalid batch boundaries")
            yield start, _float_block(block, (len(block), cols))
            start += len(block)
        if start != rows:
            raise ValueError("source scan omitted rows")
        return
    for start in range(0, rows, chunk_rows):
        stop = min(rows, start + chunk_rows)
        ids = np.arange(start, stop, dtype=np.intp)
        yield start, _float_block(source.take(ids), (stop - start, cols))


def _hash_source_block(hasher, value):
    canonical = np.ascontiguousarray(value, dtype="<f4")
    hasher.update(memoryview(canonical).cast("B"))


def _sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _json_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError(f"Duplicate manifest field: {name}")
        result[name] = value
    return result


def _bad_constant(value):
    raise ValueError(f"Nonfinite JSON value: {value}")


def _finite_float(value):
    result = float(value)
    if not np.isfinite(result):
        raise ValueError("Nonfinite JSON number")
    return result


def _manifest(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Missing, symlinked, or oversized index manifest")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_json_object,
                      parse_constant=_bad_constant, parse_float=_finite_float)


def _close_arrays(arrays):
    for value in arrays.values():
        mapping = getattr(value, "_mmap", None)
        if mapping is not None:
            mapping.close()
    arrays.clear()


def build_index(source, path, *, chunk_rows=_CHUNK_ROWS) -> dict:
    """Build an immutable INT8 filter index bound to canonical source FP32 bytes.

    Files contain no original vectors. The source must remain available and
    immutable for subsequent search. A manifest is published last; an existing
    destination is refused. The return value is the complete manifest.
    """
    require_ieee_environment()
    shape = _source_shape(source)
    chunk_rows = _integer(chunk_rows, "chunk_rows", 1)
    path = Path(path).absolute()
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Destination already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{path.name}.", dir=path.parent))
    arrays = {}
    published = []
    reserved = False
    try:
        for name, dtype in _ARRAY_DTYPES.items():
            array_shape = shape if name == "codes" else (shape[0],)
            arrays[name] = np.lib.format.open_memmap(stage / f"{name}.npy", mode="w+", dtype=dtype,
                                                    shape=array_shape, version=(1, 0))
        source_hash = hashlib.sha256()
        for start, block in _source_batches(source, shape, chunk_rows):
            stop = start + len(block)
            codes, scales, sums = _quantize(block)
            residual, xhat, x = _upper_metadata(block, codes, scales)
            for name, values in zip(_ARRAY_DTYPES, (codes, scales, sums, residual, xhat, x)):
                arrays[name][start:stop] = values
            _hash_source_block(source_hash, block)
        for value in arrays.values():
            value.flush()
        _close_arrays(arrays)
        manifest = {"format": _FORMAT, "version": _VERSION, "scheme": _SCHEME, "reference": _REFERENCE,
                    "shape": list(shape), "source_dtype": "<f4", "source_sha256": source_hash.hexdigest(),
                    "chunk_rows": chunk_rows, "files": {}}
        for name, dtype in _ARRAY_DTYPES.items():
            file = stage / f"{name}.npy"
            manifest["files"][file.name] = {"sha256": _sha256_file(file), "bytes": file.stat().st_size,
                "dtype": dtype, "shape": list(shape if name == "codes" else (shape[0],))}
            with file.open("rb") as stream:
                os.fsync(stream.fileno())
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
        (stage / "manifest.json").write_bytes(manifest_bytes)
        with (stage / "manifest.json").open("rb") as stream:
            os.fsync(stream.fileno())
        # Exclusive reservation cannot overwrite an existing directory, including
        # one concurrently published by another writer. Manifest is the commit.
        path.mkdir()
        reserved = True
        for name in [f"{name}.npy" for name in _ARRAY_DTYPES] + ["manifest.json"]:
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


def _topk_positions(scores, k, row_ids):
    if k == 0:
        return np.empty(0, dtype=np.intp)
    if k == len(scores):
        candidates = np.arange(len(scores), dtype=np.intp)
    else:
        cutoff = np.partition(scores, len(scores) - k)[len(scores) - k]
        above = np.flatnonzero(scores > cutoff)
        tied = np.flatnonzero(scores == cutoff)
        needed = k - len(above)
        if len(tied) > needed:
            tied = tied[np.argpartition(row_ids[tied], needed - 1)[:needed]]
        candidates = np.concatenate((above, tied))
    order = np.lexsort((row_ids[candidates], -scores[candidates]))
    return candidates[order]


def stable_topk(scores, k, row_ids=None):
    """Return row IDs sorted by descending score, then ascending ID for ties."""
    scores = np.asarray(scores)
    if scores.ndim != 1 or scores.dtype.kind != "f" or not np.all(np.isfinite(scores)):
        raise ValueError("scores must be a finite one-dimensional floating array")
    k = _integer(k, "k", 0, len(scores))
    if row_ids is None:
        ids = np.arange(len(scores), dtype=np.int64)
    else:
        ids = np.asarray(row_ids)
        if ids.shape != scores.shape or ids.dtype.kind not in ("i", "u"):
            raise ValueError("row_ids must be a matching integer vector")
        if len(np.unique(ids)) != len(ids):
            raise ValueError("row_ids must be unique")
    return ids[_topk_positions(scores, k, ids)].copy()


@dataclass(frozen=True)
class SearchResult:
    row_ids: np.ndarray
    scores: np.ndarray
    stats: dict

    @property
    def ids(self):
        return self.row_ids


class BoundedIndex:
    """Read-only filter index plus an externally owned immutable FP32 source."""
    def __init__(self):
        self._arrays = {}
        self._closed = True
        self._lock = threading.RLock()
        self._searches = self._candidate_total = self._fallbacks = 0

    @classmethod
    def open(cls, path, source, *, verify_source=True, skip_reason=None, chunk_rows=_CHUNK_ROWS):
        require_ieee_environment()
        if not isinstance(verify_source, bool):
            raise ValueError("verify_source must be a boolean")
        if not verify_source and (not isinstance(skip_reason, str) or not skip_reason.strip()):
            raise ValueError("Skipping source verification requires an explicit nonempty skip_reason")
        self = cls()
        self.path = Path(path).absolute()
        self.chunk_rows = _integer(chunk_rows, "chunk_rows", 1)
        self.source = source
        self.shape = _source_shape(source)
        self.verified_source = False
        self.skip_reason = None if verify_source else skip_reason.strip()
        try:
            if self.path.is_symlink() or not self.path.is_dir():
                raise ValueError("Index must be a real directory")
            manifest_path = self.path / "manifest.json"
            manifest = _manifest(manifest_path)
            if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
                raise ValueError("Invalid index manifest fields")
            if (manifest["format"] != _FORMAT or manifest["version"] != _VERSION
                    or isinstance(manifest["version"], bool) or manifest["scheme"] != _SCHEME
                    or manifest["reference"] != _REFERENCE or manifest["source_dtype"] != "<f4"):
                raise ValueError("Unsupported index format, version, or scoring contract")
            if (not isinstance(manifest["shape"], list) or len(manifest["shape"]) != 2
                    or tuple(_integer(x, "shape", 1) for x in manifest["shape"]) != self.shape):
                raise ValueError("Index shape does not match the source")
            _integer(manifest["chunk_rows"], "manifest chunk_rows", 1)
            source_sha = manifest["source_sha256"]
            if (not isinstance(source_sha, str) or len(source_sha) != 64
                    or any(c not in "0123456789abcdef" for c in source_sha)):
                raise ValueError("Invalid source fingerprint")
            files = manifest["files"]
            expected_files = {f"{name}.npy" for name in _ARRAY_DTYPES}
            if not isinstance(files, dict) or set(files) != expected_files:
                raise ValueError("Unexpected index file set")
            for name, dtype in _ARRAY_DTYPES.items():
                file = self.path / f"{name}.npy"
                meta = files[file.name]
                expected_shape = self.shape if name == "codes" else (self.shape[0],)
                expected_bytes = int(np.prod(expected_shape, dtype=object)) * np.dtype(dtype).itemsize
                if not isinstance(meta, dict) or set(meta) != {"sha256", "bytes", "dtype", "shape"}:
                    raise ValueError("Invalid array metadata")
                if (meta["dtype"] != dtype or meta["shape"] != list(expected_shape)
                        or file.is_symlink() or not file.is_file()):
                    raise ValueError("Invalid array path, shape, or dtype")
                size = _integer(meta["bytes"], "file byte count", expected_bytes + 1, expected_bytes + 4096)
                if file.stat().st_size != size or _sha256_file(file) != meta["sha256"]:
                    raise ValueError(f"Index array size or SHA-256 mismatch: {file.name}")
                with file.open("rb") as stream:
                    if stream.read(8) != b"\x93NUMPY\x01\x00":
                        raise ValueError("Index arrays must use the canonical NPY version-1 representation")
                value = np.load(file, mmap_mode="r", allow_pickle=False, max_header_size=4096)
                if (value.dtype != np.dtype(dtype) or value.shape != expected_shape
                        or not value.flags.c_contiguous or value.offset + value.nbytes != size):
                    value._mmap.close()
                    raise ValueError("Unexpected NPY array representation or trailing bytes")
                self._arrays[name] = value
            self.source_sha256 = source_sha
            self.manifest_sha256 = _sha256_file(manifest_path)
            self.file_bytes = manifest_path.stat().st_size + sum(meta["bytes"] for meta in files.values())
            self.index_bytes = sum(value.nbytes for value in self._arrays.values())
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
        self._norm_envelope = dict.fromkeys(("residual_upper", "xhat_upper", "x_upper"), 0.0)
        for start in range(0, self.shape[0], self.chunk_rows):
            stop = min(self.shape[0], start + self.chunk_rows)
            a = {name: value[start:stop] for name, value in self._arrays.items()}
            if np.any(a["codes"] == -128):
                raise ValueError("Index code is outside [-127, 127]")
            if not np.array_equal(np.sum(a["codes"], axis=1, dtype=np.int32), a["sums"]):
                raise ValueError("INT8 row sums do not match codes")
            for name in ("scales", "residual_upper", "xhat_upper", "x_upper"):
                if not np.all(np.isfinite(a[name])) or np.any(a[name] < 0):
                    raise ValueError("Nonfinite or negative index metadata")
            if np.any(a["scales"] == 0):
                raise ValueError("Index scales must be positive")
            for name in self._norm_envelope:
                self._norm_envelope[name] = max(self._norm_envelope[name], float(np.max(a[name])))

    def _verify_source(self):
        hasher = hashlib.sha256()
        for start, block in _source_batches(self.source, self.shape, self.chunk_rows):
            stop = start + len(block)
            _hash_source_block(hasher, block)
            expected = _upper_metadata(block, self._arrays["codes"][start:stop],
                                        self._arrays["scales"][start:stop])
            for name, required in zip(("residual_upper", "xhat_upper", "x_upper"), expected):
                if np.any(self._arrays[name][start:stop] < required):
                    raise ValueError("Source changed or stored norm bound is insufficient")
        if hasher.hexdigest() != self.source_sha256:
            raise ValueError("Source FP32 fingerprint does not match the index")

    def _ensure_open(self):
        if self._closed:
            raise ValueError("BoundedIndex is closed")

    def _query(self, query):
        require_ieee_environment()
        query = np.asarray(query)
        if query.shape != (self.shape[1],) or query.dtype.kind != "f" or query.dtype.itemsize != 4:
            raise ValueError("query must be a one-dimensional FP32 vector of matching dimension")
        query = np.ascontiguousarray(query, dtype=np.float32)
        if not np.all(np.isfinite(query)):
            raise ValueError("query contains a nonfinite value")
        return query

    def _bounds(self, query):
        codes, scales, _ = _quantize(query[None, :])
        residual_q, qhat, q = _upper_metadata(query[None, :], codes, scales)
        coarse = coarse_scores(self._arrays["codes"], self._arrays["scales"], self._arrays["sums"],
                               codes[0], scales[0])
        a = self._arrays
        # Triangle inequality for quantization residuals, plus FP64 reference
        # accumulation and the two scaled-integer coarse multiplications.
        with np.errstate(over="ignore", invalid="ignore", under="ignore"):
            radius = _outward(q[0] * a["residual_upper"])
            radius += _outward(residual_q[0] * a["xhat_upper"])
            radius += _outward(_gamma(self.shape[1] + 8) * q[0] * a["x_upper"])
            radius += _outward(_gamma(3) * qhat[0] * a["xhat_upper"])
            radius = _outward(radius)
            lower = np.nextafter(coarse - radius, -np.inf)
            upper = np.nextafter(coarse + radius, np.inf)
        return coarse, lower, upper

    def bounds(self, query):
        """Return owned coarse/lower/upper arrays for independent inspection."""
        with self._lock:
            self._ensure_open()
            return self._bounds(self._query(query))

    def _filter_bounds(self, query, k):
        codes, scales, _ = _quantize(query[None, :])
        residual_q, qhat, q = _upper_metadata(query[None, :], codes, scales)
        a = self._arrays
        return fused_bounds(a["codes"], a["scales"], a["sums"], a["residual_upper"],
                            a["xhat_upper"], a["x_upper"], codes[0], scales[0],
                            residual_q[0], qhat[0], q[0], k)

    def _filter_global_bounds(self, query, k):
        codes, scales, _ = _quantize(query[None, :])
        residual_q, qhat, q = _upper_metadata(query[None, :], codes, scales)
        envelope = self._norm_envelope
        # The same monotone outward arithmetic as the per-row formula, with
        # maxima of all nonnegative row-dependent terms. One radius per query.
        with np.errstate(over="ignore", invalid="ignore", under="ignore"):
            radius = _outward(q[0] * envelope["residual_upper"])
            radius += _outward(residual_q[0] * envelope["xhat_upper"])
            radius += _outward(_gamma(self.shape[1] + 8) * q[0] * envelope["x_upper"])
            radius += _outward(_gamma(3) * qhat[0] * envelope["xhat_upper"])
            radius = _outward(radius)
        a = self._arrays
        upper, threshold, valid = global_bounds(a["codes"], a["scales"], a["sums"],
                                               codes[0], scales[0], radius, k)
        return upper, threshold, valid, float(radius)

    def _score_ids(self, query, ids):
        scores = np.empty(len(ids), dtype=np.float64)
        for start in range(0, len(ids), self.chunk_rows):
            stop = min(len(ids), start + self.chunk_rows)
            selected = ids[start:stop]
            value = self.source[selected] if isinstance(self.source, np.ndarray) else self.source.take(selected)
            value = _float_block(value, (len(selected), self.shape[1]))
            scores[start:stop] = exact_scores(value, query)
        if not np.all(np.isfinite(scores)):
            raise ValueError("Nonfinite exact scores; source or floating-point environment changed")
        return scores

    def search(self, query, k=10):
        """Global-envelope INT8 scan followed by exact scores, ties by row ID."""
        return self._search_native(query, k, per_row=False)

    def search_per_row(self, query, k=10):
        """Fused row-specific norm bounds; retained as an independent alternative."""
        return self._search_native(query, k, per_row=True)

    def _search_native(self, query, k, *, per_row):
        with self._lock:
            self._ensure_open()
            k = _integer(k, "k", 1, self.shape[0])
            query = self._query(query)
            n = self.shape[0]
            if per_row:
                upper, threshold, valid = self._filter_bounds(query, k)
                radius, heap_kind = None, "lower"
                scan_bytes = self.index_bytes
                search_path = "native_fused_lower_heap"
            else:
                upper, threshold, valid, radius = self._filter_global_bounds(query, k)
                heap_kind = "coarse"
                scan_bytes = n * (self.shape[1] + 8) if np.isfinite(radius) and radius >= 0 else 0
                search_path = "native_global_envelope"
            fallback = None
            if not valid or not np.isfinite(threshold):
                fallback = "nonfinite_or_invalid_error_bounds"
                candidates = np.arange(n, dtype=np.int64)
                max_excluded = None
                threshold = None
            else:
                # At least k true reference scores are >= the kth largest lower
                # bound. Retain equality to preserve all score/row-ID ties.
                keep = upper >= threshold
                candidates = np.flatnonzero(keep)
                max_excluded = (float(np.max(upper, where=~keep, initial=-np.inf))
                                if len(candidates) < n else None)
                if len(candidates) < k:
                    fallback = "fewer_than_k_interval_candidates"
                    candidates = np.arange(n, dtype=np.int64)
                    max_excluded, threshold = None, None
            scores = self._score_ids(query, candidates)
            return self._finish(query, k, candidates, scores, upper, max_excluded, threshold,
                fallback, 0, {"coarse": 0, "lower": 0, "upper": upper.nbytes}, k * 8,
                search_path, scan_input_bytes=scan_bytes, global_radius=radius, heap_kind=heap_kind)

    def search_reference(self, query, k=10):
        """Independent Python-array interval path with exact coarse-top-k seeds."""
        with self._lock:
            self._ensure_open()
            k = _integer(k, "k", 1, self.shape[0])
            query = self._query(query)
            coarse, lower, upper = self._bounds(query)
            n = self.shape[0]
            all_ids = np.arange(n, dtype=np.int64)
            fallback = None
            if not (np.all(np.isfinite(coarse)) and np.all(np.isfinite(lower))
                    and np.all(np.isfinite(upper)) and np.all(lower <= upper)):
                fallback = "nonfinite_or_invalid_error_bounds"
                candidates = all_ids
                scores = self._score_ids(query, candidates)
                seed_count = 0
                max_excluded, tau = None, None
            else:
                seeds = _topk_positions(coarse, k, all_ids)
                seed_scores = self._score_ids(query, seeds)
                tau = float(np.min(seed_scores))
                # Keep equality: exact score ties are resolved by row ID.
                keep = upper >= tau
                keep[seeds] = False
                remaining = np.flatnonzero(keep)
                candidates = np.concatenate((seeds, remaining))
                scores = np.concatenate((seed_scores, self._score_ids(query, remaining)))
                keep[seeds] = True
                max_excluded = (float(np.max(upper, where=~keep, initial=-np.inf))
                                if len(candidates) < n else None)
                seed_count = len(seeds)
                if np.any(scores < lower[candidates]):
                    fallback = "candidate_below_lower_bound"
            return self._finish(query, k, candidates, scores, upper, max_excluded, tau,
                fallback, seed_count, {"coarse": coarse.nbytes, "lower": lower.nbytes, "upper": upper.nbytes},
                0, "python_reference_exact_seed")

    def _finish(self, query, k, candidates, scores, upper, max_excluded, threshold,
                fallback, seed_count, scratch, heap_bytes, search_path, *, scan_input_bytes=None,
                global_radius=None, heap_kind="lower"):
        n = self.shape[0]
        refined_rows = len(scores)
        selected = _topk_positions(scores, k, candidates)
        final_ids, final_scores = candidates[selected].copy(), scores[selected].copy()
        kth = float(final_scores[-1])
        # These are consistency diagnostics, not an independent proof that all
        # intervals enclose their true scores. That follows from the bound and
        # validated immutable metadata, and is checked independently in tests.
        if fallback is None:
            if np.any(scores > upper[candidates]):
                fallback = "candidate_above_upper_bound"
            elif threshold is not None and kth < threshold:
                fallback = "final_score_below_filter_threshold"
            elif max_excluded is not None and max_excluded > kth:
                fallback = "certificate_check_failed"
        if fallback is not None and len(candidates) < n:
            candidates = np.arange(n, dtype=np.int64)
            scores = self._score_ids(query, candidates)
            refined_rows += n
            selected = _topk_positions(scores, k, candidates)
            final_ids, final_scores = candidates[selected].copy(), scores[selected].copy()
        if fallback is not None:
            max_excluded, threshold, seed_count = None, None, 0
            kth = float(final_scores[-1])
        candidate_count = len(candidates)
        certificate = max_excluded is None or max_excluded <= kth
        self._searches += 1
        self._candidate_total += candidate_count
        self._fallbacks += int(fallback is not None)
        stats = {"rows": n, "dimensions": self.shape[1], "k": k,
            "candidate_count": candidate_count, "seed_count": seed_count,
            "additional_candidates": candidate_count - seed_count,
            "pruned_count": n - candidate_count, "pruned_fraction": 1 - candidate_count / n,
            "candidate_fraction": candidate_count / n,
            "candidate_source_bytes": candidate_count * self.shape[1] * 4,
            "refinement_rows_scored": refined_rows,
            "refinement_source_bytes": refined_rows * self.shape[1] * 4,
            "index_bytes": self.index_bytes, "index_file_bytes": self.file_bytes,
            "scanned_index_payload_bytes": self.index_bytes if scan_input_bytes is None else scan_input_bytes,
            "bounds_array_bytes": scratch, "bounds_arrays_total_bytes": sum(scratch.values()),
            "lower_heap_bytes": heap_bytes if heap_kind == "lower" else 0,
            "coarse_heap_bytes": heap_bytes if heap_kind == "coarse" else 0,
            "filter_heap_bytes": heap_bytes,
            "filter_working_buffer_bytes": sum(scratch.values()) + heap_bytes,
            "source_bytes": self.source_bytes, "source_verified": self.verified_source,
            "source_verification_skip_reason": self.skip_reason, "fallback_reason": fallback,
            "native_backend": self.native_backend, "search_path": search_path,
            "global_radius": global_radius if global_radius is None or np.isfinite(global_radius) else None,
            "certificate": {"valid": certificate, "excluded_count": n - candidate_count,
                "max_excluded_upper": max_excluded, "final_kth_score": kth,
                "filter_threshold": threshold, "reference": _REFERENCE,
                "source_binding_verified": self.verified_source,
                "kind": "interval_separation_with_conditional_source_binding"}}
        return SearchResult(final_ids, final_scores, stats)

    def stats(self):
        with self._lock:
            self._ensure_open()
            return {"shape": list(self.shape), "index_bytes": self.index_bytes, "file_bytes": self.file_bytes,
                "source_bytes": self.source_bytes, "index_to_source_ratio": self.index_bytes / self.source_bytes,
                "query_bounds_arrays_bytes": self.shape[0] * 8,
                "reference_query_bounds_arrays_bytes": self.shape[0] * 3 * 8,
                "global_scan_payload_bytes": self.shape[0] * (self.shape[1] + 8),
                "per_row_scan_payload_bytes": self.index_bytes,
                "global_norm_envelope": dict(self._norm_envelope),
                "source_sha256": self.source_sha256, "manifest_sha256": self.manifest_sha256,
                "source_verified": self.verified_source, "source_verification_skip_reason": self.skip_reason,
                "native_backend": self.native_backend, "searches": self._searches,
                "candidates_total": self._candidate_total, "fallbacks": self._fallbacks,
                "notes": "Index bytes are mapped array payloads, not resident RAM. Query O(N) temporaries, source mappings, codec/cache state, returned vectors, and OS page cache are separate. Startup verification touches all source and index pages."}

    def close(self):
        with self._lock:
            _close_arrays(self._arrays)
            self._closed = True

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
