"""Immutable, indexed, memory-mapped floating-point row-group storage.

Compression is standard zstd/zlib, optionally preceded by standard byte-plane
shuffling. No quantization, model-specific assumption, or novel codec is used.
"""
from __future__ import annotations

from collections import OrderedDict
import contextlib
import mmap
import os
from pathlib import Path
import struct
import tempfile
import threading
from typing import Iterator
import zlib

import numpy as np

MAGIC = b"ETEMMAP\0"
VERSION = 1
HEADER_SIZE = 128
INDEX_ENTRY_SIZE = 32
_HEADER = struct.Struct("<8sHHIHHHHIiQQQQQQQQII24s")
_INDEX = struct.Struct("<QIIIIB7s")
_INDEX_DTYPE = np.dtype({"names": ["offset", "stored_size", "raw_size", "stored_crc", "raw_crc", "mode", "reserved"],
    "formats": ["<u8", "<u4", "<u4", "<u4", "<u4", "u1", "V7"],
    "offsets": [0, 8, 12, 16, 20, 24, 25], "itemsize": INDEX_ENTRY_SIZE})
DTYPES = {"float16": (1, np.dtype("<f2")), "float32": (2, np.dtype("<f4")),
          "bfloat16": (3, np.dtype("<u2")), "uint16": (4, np.dtype("<u2")),
          "uint32": (5, np.dtype("<u4"))}
_DTYPE_BY_CODE = {code: (name, dtype) for name, (code, dtype) in DTYPES.items()}
CODECS = {"raw": 0, "zlib": 1, "zstd": 2}
SHUFFLES = {"none": 0, "byte": 1, "auto": 2}
_DEFAULT_GROUP_LIMIT = 256 * 1024 * 1024
_COUNTER_NAMES = ("take_calls", "rows_requested", "output_bytes", "group_requests", "cache_hits", "cache_misses",
                  "raw_group_reads", "groups_decoded", "decoded_bytes", "stored_bytes_processed",
                  "crc_checks", "crc_bytes", "cache_evictions")


class StoreError(ValueError):
    """Unsupported or malformed row-group store."""


class StoreCorruptionError(StoreError):
    """A checksum, compressed frame, or reconstructed payload is invalid."""


def _zstd():
    try:
        import zstandard
    except ImportError as exc:
        raise ImportError("zstd storage requires the optional 'zstandard' package") from exc
    return zstandard


def _int(value, name, minimum=0, maximum=None):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"{name} must be an integer")
    value = int(value)
    if value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} is outside the supported range")
    return value


def _input_words(array, logical_dtype):
    value = np.asarray(array)
    if value.ndim != 2 or value.shape[1] == 0:
        raise ValueError("array must be a two-dimensional matrix with at least one column")
    if (value.dtype.kind, value.dtype.itemsize) not in (("f", 2), ("f", 4), ("u", 2), ("u", 4)):
        raise TypeError("Only float16, float32, uint16 bits, or uint32 bits are supported")
    if logical_dtype is None:
        logical_dtype = {("f", 2): "float16", ("f", 4): "float32", ("u", 2): "uint16", ("u", 4): "uint32"}[(value.dtype.kind, value.dtype.itemsize)]
    if logical_dtype not in DTYPES:
        raise ValueError(f"Unsupported logical_dtype: {logical_dtype!r}")
    code, dtype = DTYPES[logical_dtype]
    if value.dtype.itemsize != dtype.itemsize:
        raise TypeError("Input and logical dtype widths differ; conversion/quantization must be explicit")
    if logical_dtype == "bfloat16" and value.dtype.kind != "u":
        raise TypeError("bfloat16 requires uint16 bit patterns, not a NumPy float16 array")
    if value.dtype.kind == "f" and logical_dtype != f"float{value.dtype.itemsize * 8}":
        raise TypeError("A floating input must retain its original floating-point format")
    # Normalize byte order via integers; never recast FP NaNs or their payloads.
    source_bits = value.view(np.dtype(f"u{value.dtype.itemsize}").newbyteorder(value.dtype.byteorder))
    bits = np.ascontiguousarray(source_bits, dtype=f"<u{value.dtype.itemsize}")
    return bits, logical_dtype, code, dtype


def _shuffle(raw: bytes, itemsize: int) -> bytes:
    return np.frombuffer(raw, dtype=np.uint8).reshape(-1, itemsize).T.copy().tobytes()


def _unshuffle(raw: bytes, dtype: np.dtype, shape: tuple[int, int]) -> np.ndarray:
    # A single owned byte array becomes the final typed array; no float casts.
    return np.frombuffer(raw, dtype=np.uint8).reshape(dtype.itemsize, -1).T.copy().view(dtype).reshape(shape)


def _header_crc(header: bytes) -> int:
    return zlib.crc32(header[:100] + b"\0\0\0\0" + header[104:])


def build_store(array, path, *, group_rows=256, codec="zstd", shuffle="byte", level=3,
                logical_dtype=None) -> dict:
    """Write a new immutable store atomically; existing destinations are refused.

    ``logical_dtype='bfloat16'`` accepts uint16 bit patterns and reads them back
    as uint16. FP32/FP16 inputs retain all bits, including signed zero and NaNs.
    ``shuffle='auto'`` tries plain and byte-shuffled compression per group;
    ``raw`` fallback is selected whenever compression does not reduce payload.
    """
    bits, logical_dtype, dtype_code, dtype = _input_words(array, logical_dtype)
    group_rows = _int(group_rows, "group_rows", 1, 0xFFFFFFFF)
    level = _int(level, "level", -131072, 22)
    if codec not in CODECS or shuffle not in SHUFFLES:
        raise ValueError("codec must be raw/zlib/zstd; shuffle must be none/byte/auto")
    if codec == "zlib" and not 0 <= level <= 9:
        raise ValueError("zlib level must be in 0..9")
    if codec == "raw":
        shuffle = "none"
    rows, cols = map(int, bits.shape)
    if min(group_rows, max(rows, 1)) * cols * dtype.itemsize > 0xFFFFFFFF:
        raise ValueError("A row group exceeds the version-1 uint32 byte-count limit; choose fewer rows")
    groups = (rows + group_rows - 1) // group_rows
    index_bytes = groups * INDEX_ENTRY_SIZE
    raw_bytes = rows * cols * dtype.itemsize
    path = Path(path).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Destination already exists: {path}")
    compressor = _zstd().ZstdCompressor(level=level, write_content_size=True) if codec == "zstd" else None
    compress = compressor.compress if compressor else (lambda data: zlib.compress(data, level))
    records = bytearray(index_bytes)
    counts = {"raw": 0, "compressed_plain": 0, "compressed_byte": 0}
    cursor = 0
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w+b", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.seek(HEADER_SIZE + index_bytes)
            for group in range(groups):
                raw = bits[group * group_rows:(group + 1) * group_rows].tobytes()
                raw_crc = zlib.crc32(raw)
                payload, mode = raw, 0
                if codec != "raw":
                    if shuffle in ("none", "auto"):
                        candidate = compress(raw)
                        if len(candidate) < len(payload):
                            payload, mode = candidate, 1
                    if shuffle in ("byte", "auto"):
                        candidate = compress(_shuffle(raw, dtype.itemsize))
                        if len(candidate) < len(payload):
                            payload, mode = candidate, 2
                output.write(payload)
                _INDEX.pack_into(records, group * INDEX_ENTRY_SIZE, cursor, len(payload), len(raw),
                                 zlib.crc32(payload), raw_crc, mode, b"\0" * 7)
                counts[("raw", "compressed_plain", "compressed_byte")[mode]] += 1
                cursor += len(payload)
            header = _HEADER.pack(MAGIC, VERSION, HEADER_SIZE, 1, dtype_code, CODECS[codec], SHUFFLES[shuffle],
                0, group_rows, level, rows, cols, groups, HEADER_SIZE, index_bytes,
                HEADER_SIZE + index_bytes, cursor, raw_bytes, zlib.crc32(records), 0, b"\0" * 24)
            header = header[:100] + struct.pack("<I", _header_crc(header)) + header[104:]
            output.seek(0); output.write(header); output.write(records)
            output.flush(); os.fsync(output.fileno())
        # Hard-link publication is atomic and cannot overwrite a concurrent file.
        os.link(temporary, path)
        temporary.unlink(); temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    file_bytes = HEADER_SIZE + index_bytes + cursor
    return {"path": str(path), "shape": [rows, cols], "logical_dtype": logical_dtype,
            "numpy_dtype": dtype.str, "codec": codec, "shuffle": shuffle, "level": level,
            "group_rows": group_rows, "groups": groups, "mode_counts": counts,
            "raw_bytes": raw_bytes, "file_bytes": file_bytes, "payload_bytes": cursor,
            "header_bytes": HEADER_SIZE, "index_bytes": index_bytes,
            "ratio": file_bytes / raw_bytes if raw_bytes else None,
            "saving_pct": 100 * (1 - file_bytes / raw_bytes) if raw_bytes else None}


class MappedMatrix:
    """Thread-safe read-only row access; returned arrays own their memory.

    The source file is immutable by contract: publish a new filename for a new
    version and never modify an open store in place. ``verify='lazy'`` checks
    each group's CRC the first time it is read; ``always`` checks on every cache
    miss; ``eager`` verifies all groups at open without populating decode cache.
    Compressed frame lengths/bounds are checked on every decompression.
    """
    def __init__(self):
        self._file = self._mmap = self._index = None
        self._closed = True
        self._lock = threading.RLock()
        self._cache = OrderedDict()
        self._cache_size = 0
        self._counters = dict.fromkeys(_COUNTER_NAMES, 0)

    @classmethod
    def open(cls, path, *, cache_bytes=64 * 1024 * 1024, verify="lazy",
             max_group_bytes=_DEFAULT_GROUP_LIMIT):
        self = cls()
        self.cache_limit = _int(cache_bytes, "cache_bytes", 0)
        self._max_group_bytes = _int(max_group_bytes, "max_group_bytes", 1, 0xFFFFFFFF)
        if verify not in ("lazy", "always", "eager"):
            raise ValueError("verify must be lazy, always, or eager")
        self.verify_policy = verify
        self.path = Path(path).absolute()
        try:
            self._file = self.path.open("rb")
            size = os.fstat(self._file.fileno()).st_size
            if size < HEADER_SIZE:
                raise StoreError("Truncated store header")
            header = self._file.read(HEADER_SIZE)
            values = _HEADER.unpack(header)
            (magic, version, hsize, flags, dtype_code, codec_code, shuffle_code, reserved,
             group_rows, level, rows, cols, groups, index_offset, index_bytes, payload_offset,
             payload_bytes, raw_bytes, index_crc, header_crc, tail) = values
            if magic != MAGIC or version != VERSION or hsize != HEADER_SIZE or flags != 1 or reserved or any(tail):
                raise StoreError("Unsupported store header, version, flags, or reserved data")
            if _header_crc(header) != header_crc:
                raise StoreCorruptionError("Header CRC32 mismatch")
            if dtype_code not in _DTYPE_BY_CODE or codec_code not in CODECS.values() or shuffle_code not in SHUFFLES.values():
                raise StoreError("Unknown dtype, codec, or shuffle")
            logical_dtype, dtype = _DTYPE_BY_CODE[dtype_code]
            if not cols or not group_rows or rows * cols * dtype.itemsize != raw_bytes:
                raise StoreError("Invalid matrix shape or raw byte count")
            if groups != (rows + group_rows - 1) // group_rows or index_offset != HEADER_SIZE or index_bytes != groups * INDEX_ENTRY_SIZE:
                raise StoreError("Invalid row-group directory dimensions")
            if payload_offset != HEADER_SIZE + index_bytes or payload_offset > size or payload_bytes != size - payload_offset:
                raise StoreError("File, index, or payload length mismatch")
            if codec_code == 0 and shuffle_code != 0:
                raise StoreError("Raw stores cannot declare a shuffle")
            if (codec_code == 1 and not 0 <= level <= 9) or (codec_code == 2 and not -131072 <= level <= 22):
                raise StoreError("Invalid compression level")
            # Own the small index bytes, so no directory view pins the mmap open.
            index_data = self._file.read(index_bytes)
            if len(index_data) != index_bytes or zlib.crc32(index_data) != index_crc:
                raise StoreCorruptionError("Index CRC32 mismatch")
            index = np.frombuffer(index_data, dtype=_INDEX_DTYPE)
            if groups:
                sizes = index["stored_size"].astype(np.uint64)
                offsets = index["offset"]
                if np.any(sizes == 0) or np.any(offsets > payload_bytes) or np.any(sizes > payload_bytes - offsets):
                    raise StoreError("Group offset or stored size is out of bounds")
                if offsets[0] != 0 or offsets[-1] + sizes[-1] != payload_bytes or np.any(offsets[1:] != offsets[:-1] + sizes[:-1]):
                    raise StoreError("Group payloads must be contiguous and cover the file exactly")
                expected_full = group_rows * cols * dtype.itemsize
                last_raw = (rows - (groups - 1) * group_rows) * cols * dtype.itemsize
                if (groups > 1 and np.any(index["raw_size"][:-1] != expected_full)) or index["raw_size"][-1] != last_raw:
                    raise StoreError("Group raw size disagrees with shape")
                if np.any(index["raw_size"] > self._max_group_bytes):
                    raise StoreError("A row group exceeds max_group_bytes; reopen with an explicit larger limit")
                mode = index["mode"]
                allowed = {0} if codec_code == 0 else ({0, 1} if shuffle_code == 0 else {0, 2} if shuffle_code == 1 else {0, 1, 2})
                if np.any(~np.isin(mode, list(allowed))) or np.any(index.view(np.uint8).reshape(-1, INDEX_ENTRY_SIZE)[:, 25:]):
                    raise StoreError("Invalid group mode or reserved directory bytes")
                is_raw = mode == 0
                if (np.any(index["stored_size"][is_raw] != index["raw_size"][is_raw])
                        or np.any(index["stored_crc"][is_raw] != index["raw_crc"][is_raw])
                        or np.any(index["stored_size"][~is_raw] >= index["raw_size"][~is_raw])):
                    raise StoreError("Invalid raw fallback or compressed group size")
            elif payload_bytes:
                raise StoreError("An empty matrix cannot have payload")
            self.shape, self.dtype, self.logical_dtype = (int(rows), int(cols)), dtype, logical_dtype
            self.group_rows, self.group_count = int(group_rows), int(groups)
            self.codec = next(k for k, v in CODECS.items() if v == codec_code)
            self.shuffle = next(k for k, v in SHUFFLES.items() if v == shuffle_code)
            self.level, self.file_bytes, self.raw_bytes = level, size, raw_bytes
            self._payload_offset, self._payload_bytes = payload_offset, payload_bytes
            self._index = index
            self._verified = np.zeros(groups, dtype=np.bool_)
            self._decompressor = _zstd().ZstdDecompressor() if codec_code == 2 else None
            self._mmap = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
            self._closed = False
            if verify == "eager":
                self.verify_all()
            return self
        except BaseException:
            self.close()
            raise

    def _ensure_open(self):
        if self._closed:
            raise ValueError("MappedMatrix is closed")

    def _crc(self, data, expected, label):
        self._counters["crc_checks"] += 1
        self._counters["crc_bytes"] += len(data)
        if zlib.crc32(data) != expected:
            raise StoreCorruptionError(f"{label} CRC32 mismatch")

    def _decode(self, view, raw_size, group):
        try:
            if self.codec == "zlib":
                decoder = zlib.decompressobj()
                output = decoder.decompress(view, raw_size + 1)
                if len(output) != raw_size or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
                    raise StoreCorruptionError(f"Invalid zlib frame size or trailing data in group {group}")
            else:
                zstd = _zstd()
                declared = zstd.frame_content_size(view)
                parameters = zstd.get_frame_parameters(view)
                if declared not in (raw_size, zstd.CONTENTSIZE_UNKNOWN) or parameters.window_size > self._max_group_bytes:
                    raise StoreCorruptionError(f"Invalid zstd content/window size in group {group}")
                output = self._decompressor.decompress(view, max_output_size=raw_size,
                                                       allow_extra_data=False)
                if len(output) != raw_size:
                    raise StoreCorruptionError(f"Invalid decoded size in group {group}")
            return output
        except StoreCorruptionError:
            raise
        except Exception as exc:
            raise StoreCorruptionError(f"Compressed payload failed in group {group}: {exc}") from exc

    def _group(self, group, *, populate_cache=True):
        self._counters["group_requests"] += 1
        if group in self._cache:
            self._counters["cache_hits"] += 1
            self._cache.move_to_end(group)
            return self._cache[group]
        record = self._index[group]
        mode, raw_size, stored_size = int(record["mode"]), int(record["raw_size"]), int(record["stored_size"])
        offset = self._payload_offset + int(record["offset"])
        shape = (raw_size // (self.shape[1] * self.dtype.itemsize), self.shape[1])
        check_crc = self.verify_policy == "always" or not self._verified[group]
        self._counters["stored_bytes_processed"] += stored_size
        if mode == 0:
            self._counters["raw_group_reads"] += 1
            if check_crc:
                view = memoryview(self._mmap)[offset:offset + stored_size]
                try: self._crc(view, int(record["stored_crc"]), f"Raw group {group}")
                finally: view.release()
                self._verified[group] = True
            return np.frombuffer(self._mmap, dtype=self.dtype, count=raw_size // self.dtype.itemsize, offset=offset).reshape(shape)
        self._counters["cache_misses"] += 1
        view = memoryview(self._mmap)[offset:offset + stored_size]
        try:
            if check_crc:
                self._crc(view, int(record["stored_crc"]), f"Stored group {group}")
            decoded = self._decode(view, raw_size, group)
        finally:
            view.release()
        value = _unshuffle(decoded, self.dtype, shape) if mode == 2 else np.frombuffer(decoded, dtype=self.dtype).reshape(shape)
        if check_crc:
            self._crc(memoryview(value).cast("B"), int(record["raw_crc"]), f"Decoded group {group}")
            self._verified[group] = True
        value.flags.writeable = False
        self._counters["groups_decoded"] += 1
        self._counters["decoded_bytes"] += raw_size
        if populate_cache and raw_size <= self.cache_limit and self.cache_limit:
            while self._cache and self._cache_size + raw_size > self.cache_limit:
                _, removed = self._cache.popitem(last=False)
                self._cache_size -= removed.nbytes
                self._counters["cache_evictions"] += 1
            self._cache[group] = value
            self._cache_size += raw_size
        return value

    def take(self, row_ids) -> np.ndarray:
        """Return owned rows in requested order, retaining duplicate row IDs."""
        with self._lock:
            self._ensure_open()
            ids = np.asarray(row_ids)
            if ids.ndim != 1:
                raise ValueError("row_ids must be one-dimensional")
            if ids.size == 0:
                # An empty Python list has float64 dtype; it is unambiguous here.
                if ids.dtype.kind == "b":
                    raise TypeError("Boolean row IDs are not supported")
                ids = np.empty(0, dtype=np.intp)
            elif ids.dtype.kind not in ("i", "u"):
                raise TypeError("row_ids must contain integer indices, not floats or booleans")
            if ids.size and (np.any(ids < 0) or np.any(ids >= self.shape[0])):
                raise IndexError("row ID is outside the matrix")
            ids = ids.astype(np.intp, copy=False)
            out = np.empty((ids.size, self.shape[1]), dtype=self.dtype)
            self._counters["take_calls"] += 1
            self._counters["rows_requested"] += ids.size
            self._counters["output_bytes"] += out.nbytes
            if not ids.size:
                return out
            groups = ids // self.group_rows
            order = np.argsort(groups, kind="stable")
            sorted_groups = groups[order]
            boundaries = np.r_[0, np.flatnonzero(sorted_groups[1:] != sorted_groups[:-1]) + 1, len(ids)]
            for left, right in zip(boundaries[:-1], boundaries[1:]):
                positions = order[left:right]
                group = int(sorted_groups[left])
                values = self._group(group)
                out[positions] = values[ids[positions] - group * self.group_rows]
            return out

    def scan_batches(self, batch_rows=None) -> Iterator[np.ndarray]:
        """Yield owned contiguous batches, bounded by ``batch_rows`` output rows."""
        if batch_rows is None:
            batch_rows = self.group_rows
        batch_rows = _int(batch_rows, "batch_rows", 1)
        start = 0
        while start < self.shape[0]:
            stop = min(self.shape[0], start + batch_rows)
            yield self.take(np.arange(start, stop, dtype=np.intp))
            start = stop
        with self._lock:
            self._ensure_open()

    def verify_all(self):
        """Validate every group without filling the decode cache."""
        with self._lock:
            self._ensure_open()
            for group in range(self.group_count):
                value = self._group(group, populate_cache=False)
                del value

    def clear_cache(self):
        with self._lock:
            self._cache.clear()
            self._cache_size = 0

    def reset_stats(self, *, clear_cache=False):
        with self._lock:
            self._ensure_open()
            self._counters = dict.fromkeys(_COUNTER_NAMES, 0)
            if clear_cache:
                self.clear_cache()

    def stats(self) -> dict:
        """Logical bytes/counters, not OS page faults, RSS, or physical disk I/O."""
        with self._lock:
            self._ensure_open()
            mode = self._index["mode"]
            return {"path": str(self.path), "shape": list(self.shape), "logical_dtype": self.logical_dtype,
                    "numpy_dtype": self.dtype.str, "codec": self.codec, "shuffle": self.shuffle,
                    "level": self.level, "group_rows": self.group_rows, "groups": self.group_count,
                    "file_bytes": self.file_bytes, "raw_bytes": self.raw_bytes,
                    "payload_bytes": self._payload_bytes, "header_bytes": HEADER_SIZE,
                    "index_bytes": self._index.nbytes,
                    "resident_index_and_validation_bytes": self._index.nbytes + self._verified.nbytes,
                    "mode_counts": {"raw": int(np.count_nonzero(mode == 0)),
                                    "compressed_plain": int(np.count_nonzero(mode == 1)),
                                    "compressed_byte": int(np.count_nonzero(mode == 2))},
                    **{name: int(value) for name, value in self._counters.items()},
                    "cache_bytes": self._cache_size, "cache_limit_bytes": self.cache_limit,
                    "cache_entries": len(self._cache), "verified_groups": int(self._verified.sum()),
                    "verify_policy": self.verify_policy,
                    "notes": "Stored bytes processed are logical spans accessed on cache misses/raw requests, not physical reads. Decoded bytes count complete compressed groups only. Cache bytes exclude index, Python/codec objects and transient output/decode buffers. mmap pages depend on OS page cache."}

    def close(self):
        with self._lock:
            self.clear_cache()
            self._index = None
            if self._mmap is not None:
                self._mmap.close(); self._mmap = None
            if self._file is not None:
                self._file.close(); self._file = None
            self._closed = True

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __del__(self):
        with contextlib.suppress(Exception):
            self.close()
