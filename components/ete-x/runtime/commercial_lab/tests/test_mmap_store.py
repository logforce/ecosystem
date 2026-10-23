"""Lossless bits, partial access, bounded caching and corrupt-file boundaries."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

import numpy as np

from commercial_lab.etex_mmap import MappedMatrix, StoreCorruptionError, StoreError, build_store
from commercial_lab.etex_mmap.store import HEADER_SIZE, INDEX_ENTRY_SIZE, _HEADER, _INDEX, _header_crc


def rewrite_header(blob):
    value = bytearray(blob)
    header = list(_HEADER.unpack_from(value))
    start, size = int(header[13]), int(header[14])
    header[18] = zlib.crc32(value[start:start + size])
    header[19] = 0
    h = _HEADER.pack(*header)
    header[19] = _header_crc(h)
    value[:HEADER_SIZE] = _HEADER.pack(*header)
    return bytes(value)


def replace_single_payload(path, payload):
    blob = bytearray(path.read_bytes())
    h = list(_HEADER.unpack_from(blob)); assert h[12] == 1
    entry = list(_INDEX.unpack_from(blob, HEADER_SIZE))
    entry[1], entry[3] = len(payload), zlib.crc32(payload)
    _INDEX.pack_into(blob, HEADER_SIZE, *entry)
    h[16] = len(payload)
    h[18] = zlib.crc32(blob[HEADER_SIZE:HEADER_SIZE + INDEX_ENTRY_SIZE])
    h[19] = 0
    head = _HEADER.pack(*h); h[19] = _header_crc(head)
    path.write_bytes(_HEADER.pack(*h) + bytes(blob[HEADER_SIZE:HEADER_SIZE + INDEX_ENTRY_SIZE]) + payload)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.rng = np.random.default_rng(20261022)

    def tearDown(self):
        self.temp.cleanup()

    def path(self, name="test.etm"):
        return self.directory / name

    def test_all_fp16_and_bf16_bit_patterns(self):
        bits = np.arange(65536, dtype=np.uint16).reshape(256, 256)
        for logical in ("float16", "bfloat16"):
            with self.subTest(logical=logical):
                p = self.path(logical)
                build_store(bits, p, group_rows=31, logical_dtype=logical, codec="zstd", shuffle="byte")
                with MappedMatrix.open(p, cache_bytes=0) as store:
                    restored = np.concatenate(list(store.scan_batches(37)))
                    np.testing.assert_array_equal(restored.view(np.uint16), bits)
                    self.assertEqual(store.logical_dtype, logical)
                    self.assertEqual(restored.dtype.kind, "u" if logical == "bfloat16" else "f")

    def test_fp32_nan_payload_signed_zero_infinity_and_random_bits(self):
        bits = self.rng.integers(0, 1 << 32, size=(79, 33), dtype=np.uint32)
        bits.flat[:10] = [0, 0x80000000, 1, 0x007FFFFF, 0x00800000, 0x7F800000,
                          0xFF800000, 0x7F800001, 0x7FC01234, 0xFFFFFFFF]
        for codec in ("raw", "zlib", "zstd"):
            p = self.path(codec)
            build_store(bits.view(np.float32), p, codec=codec, shuffle="auto", group_rows=8)
            with MappedMatrix.open(p) as store:
                actual = store.take(np.arange(len(bits)))
                np.testing.assert_array_equal(actual.view(np.uint32), bits)

    def test_big_endian_and_noncontiguous_inputs_preserve_bits(self):
        bits = self.rng.integers(0, 1 << 32, size=(17, 18), dtype=np.uint32)
        bits[0, 0] = 0x7F800001
        source = bits.astype(">u4").view(">f4")[:, ::2]
        p = self.path()
        build_store(source, p, codec="zstd", group_rows=4)
        with MappedMatrix.open(p) as store:
            np.testing.assert_array_equal(store.take(np.arange(17)).view(np.uint32), bits[:, ::2])
        np.testing.assert_array_equal(source.view(">u4"), bits[:, ::2])

    def test_requested_order_duplicates_tails_and_scan_batches(self):
        a = self.rng.normal(size=(257, 7)).astype(np.float32)
        ids = np.array([256, 0, 31, 256, 8, 8, 32, 128])
        for group_rows in (1, 8, 32, 128, 256):
            p = self.path(str(group_rows))
            info = build_store(a, p, codec="zstd", shuffle="auto", group_rows=group_rows)
            self.assertEqual(info["file_bytes"], p.stat().st_size)
            with MappedMatrix.open(p, cache_bytes=1024) as store:
                np.testing.assert_array_equal(store.take(ids).view(np.uint32), a[ids].view(np.uint32))
                batches = list(store.scan_batches(41))
                self.assertTrue(all(len(b) <= 41 and b.flags.owndata for b in batches))
                np.testing.assert_array_equal(np.concatenate(batches).view(np.uint32), a.view(np.uint32))
                self.assertEqual(store.take([]).shape, (0, 7))

    def test_raw_fallback_bounds_and_file_overhead(self):
        bits = self.rng.integers(0, 1 << 32, size=(128, 32), dtype=np.uint32)
        p = self.path()
        info = build_store(bits, p, codec="zstd", shuffle="auto", group_rows=8)
        self.assertEqual(info["mode_counts"], {"raw": 16, "compressed_plain": 0, "compressed_byte": 0})
        self.assertEqual(info["file_bytes"], bits.nbytes + HEADER_SIZE + INDEX_ENTRY_SIZE * 16)
        with MappedMatrix.open(p) as store:
            np.testing.assert_array_equal(store.take([7, 0]), bits[[7, 0]])
            self.assertEqual(store.stats()["groups_decoded"], 0)
            self.assertEqual(store.stats()["cache_bytes"], 0)

    def test_only_requested_groups_decode_and_lru_is_bounded(self):
        a = np.zeros((64, 32), dtype=np.float32)
        p = self.path(); build_store(a, p, group_rows=8, codec="zstd")
        group_bytes = 8 * 32 * 4
        with MappedMatrix.open(p, cache_bytes=2 * group_bytes) as store:
            store.take([0, 0, 7])
            self.assertEqual(store.stats()["groups_decoded"], 1)
            self.assertEqual(store.stats()["decoded_bytes"], group_bytes)
            self.assertEqual(store.stats()["verified_groups"], 1)
            store.take([1])
            self.assertEqual(store.stats()["cache_hits"], 1)
            store.take([8]); store.take([16])  # Evicts the least recent group 0.
            self.assertEqual(store.stats()["cache_bytes"], 2 * group_bytes)
            store.take([0])
            stats = store.stats()
            self.assertEqual(stats["groups_decoded"], 4)
            self.assertEqual(stats["decoded_bytes"], 4 * group_bytes)
            self.assertEqual(stats["cache_evictions"], 2)
            self.assertLess(stats["decoded_bytes"], a.nbytes)
            self.assertLessEqual(stats["cache_bytes"], stats["cache_limit_bytes"])

    def test_disabled_or_undersized_cache_never_retains_decoded_groups(self):
        a = np.zeros((32, 32), dtype=np.float16)
        p = self.path(); build_store(a, p, group_rows=8)
        for budget in (0, 1, 511):
            with MappedMatrix.open(p, cache_bytes=budget) as store:
                store.take([0]); store.take([0])
                stats = store.stats()
                self.assertEqual(stats["cache_bytes"], 0)
                self.assertEqual(stats["cache_hits"], 0)
                self.assertEqual(stats["groups_decoded"], 2)

    def test_returned_arrays_survive_close_and_do_not_mutate_store(self):
        a = np.arange(100, dtype=np.float32).reshape(20, 5)
        for codec in ("raw", "zstd"):
            p = self.path(codec); build_store(a, p, codec=codec, group_rows=4)
            with MappedMatrix.open(p) as store:
                out = store.take([0, 19, 0])
                batch = next(store.scan_batches(3))
                self.assertTrue(out.flags.owndata)
            out[:] = 0; batch[:] = 0
            with MappedMatrix.open(p) as store:
                np.testing.assert_array_equal(store.take([0, 19]), a[[0, 19]])
            store.close()  # Idempotent.
            with self.assertRaises(ValueError): store.take([0])

    def test_lazy_integrity_detects_only_accessed_corrupt_group(self):
        a = np.zeros((24, 32), dtype=np.float32)
        p = self.path(); build_store(a, p, group_rows=8)
        data = bytearray(p.read_bytes()); h = _HEADER.unpack_from(data)
        entry = _INDEX.unpack_from(data, HEADER_SIZE + INDEX_ENTRY_SIZE)
        data[h[15] + entry[0]] ^= 1
        p.write_bytes(data)
        with MappedMatrix.open(p, verify="lazy") as store:
            np.testing.assert_array_equal(store.take([0]), a[[0]])
            with self.assertRaises(StoreCorruptionError): store.take([8])
        with self.assertRaises(StoreCorruptionError): MappedMatrix.open(p, verify="eager")

    def test_header_index_and_reserved_bytes_rejected(self):
        p = self.path(); build_store(np.zeros((20, 16), np.float32), p, group_rows=8)
        original = p.read_bytes()
        for offset in (32, HEADER_SIZE + 8):
            blob = bytearray(original); blob[offset] ^= 1; p.write_bytes(blob)
            with self.assertRaises(StoreError): MappedMatrix.open(p)
        for offset, value in ((HEADER_SIZE + 24, 255), (HEADER_SIZE + 25, 1), (HEADER_SIZE, 1)):
            blob = bytearray(original); blob[offset] = value; p.write_bytes(rewrite_header(blob))
            with self.assertRaises(StoreError): MappedMatrix.open(p)
        p.write_bytes(original[:-1])
        with self.assertRaises(StoreError): MappedMatrix.open(p)
        p.write_bytes(original + b"\0")
        with self.assertRaises(StoreError): MappedMatrix.open(p)

    def test_zstd_declared_size_cannot_override_group_bounds(self):
        import zstandard
        p = self.path(); build_store(np.zeros((8, 16), np.float32), p, group_rows=8, shuffle="none")
        # Valid compressed frame with valid stored CRC, but a 1 MiB decoded
        # size while the matrix/header/index authorizes only 512 bytes.
        replace_single_payload(p, zstandard.ZstdCompressor().compress(b"\0" * (1 << 20)))
        with MappedMatrix.open(p) as store:
            with self.assertRaisesRegex(StoreCorruptionError, "content/window"):
                store.take([0])

    def test_compressed_trailing_data_rejected_even_with_updated_crc(self):
        for codec in ("zlib", "zstd"):
            p = self.path(codec); build_store(np.zeros((8, 16), np.float32), p, group_rows=8, codec=codec, shuffle="none")
            blob = p.read_bytes(); h = _HEADER.unpack_from(blob)
            replace_single_payload(p, blob[h[15]:] + b"garbage")
            with MappedMatrix.open(p) as store:
                with self.assertRaises(StoreCorruptionError): store.take([0])

    def test_decode_limit_and_eager_verification(self):
        a = np.zeros((24, 16), dtype=np.float32)
        p = self.path(); build_store(a, p, group_rows=8)
        with self.assertRaisesRegex(StoreError, "max_group_bytes"):
            MappedMatrix.open(p, max_group_bytes=128)
        with MappedMatrix.open(p, verify="eager") as store:
            self.assertEqual(store.stats()["verified_groups"], 3)
            self.assertEqual(store.stats()["cache_entries"], 0)
            store.reset_stats(clear_cache=True)
            self.assertEqual(store.stats()["groups_decoded"], 0)
            store.take([0])
            self.assertEqual(store.stats()["crc_checks"], 0)  # Already verified immutable file.

    def test_existing_destination_refused_and_input_not_changed(self):
        a = np.arange(100, dtype=np.float16).reshape(20, 5)
        before = a.view(np.uint16).copy()
        p = self.path(); build_store(a, p)
        digest = hashlib.sha256(p.read_bytes()).digest()
        with self.assertRaises(FileExistsError): build_store(a * 2, p)
        self.assertEqual(hashlib.sha256(p.read_bytes()).digest(), digest)
        np.testing.assert_array_equal(a.view(np.uint16), before)

    def test_empty_matrix_and_input_validation(self):
        p = self.path(); build_store(np.empty((0, 384), dtype=np.float32), p)
        with MappedMatrix.open(p) as store:
            self.assertEqual(store.shape, (0, 384))
            self.assertEqual(store.take([]).shape, (0, 384))
            self.assertEqual(list(store.scan_batches()), [])
            self.assertEqual(store.file_bytes, HEADER_SIZE)
        with self.assertRaises(TypeError): build_store(np.ones((2, 3), np.float64), self.path("f64"))
        with self.assertRaises(TypeError): build_store(np.ones((2, 3), np.float16), self.path("bf"), logical_dtype="bfloat16")
        with self.assertRaises(TypeError): build_store(np.ones((2, 3), np.float32), self.path("f16"), logical_dtype="float16")
        with self.assertRaises(ValueError): build_store(np.ones((2, 3), np.float32), self.path("group"), group_rows=0)
        with self.assertRaises(ValueError): MappedMatrix.open(p, cache_bytes=-1)
        with self.assertRaises(ValueError): MappedMatrix.open(p, verify="none")

    def test_bad_row_ids_and_scan_sizes(self):
        p = self.path(); build_store(np.zeros((10, 5), np.float32), p)
        with MappedMatrix.open(p) as store:
            for ids in ([10], [-1], np.array([2**64 - 1], dtype=np.uint64)):
                with self.assertRaises(IndexError): store.take(ids)
            for ids in ([0.0], [True], [False, True]):
                with self.assertRaises(TypeError): store.take(ids)
            with self.assertRaises(ValueError): store.take([[1]])
            with self.assertRaises(ValueError): list(store.scan_batches(0))

    def test_concurrent_readers_have_independent_outputs(self):
        a = np.arange(1024, dtype=np.float32).reshape(64, 16)
        p = self.path(); build_store(a, p, group_rows=8)
        requests = [[i, 63-i, i] for i in range(12)]
        with MappedMatrix.open(p, cache_bytes=2048) as store:
            with ThreadPoolExecutor(max_workers=3) as pool:
                actual = list(pool.map(store.take, requests))
            for ids, out in zip(requests, actual):
                np.testing.assert_array_equal(out, a[ids])
            self.assertLessEqual(store.stats()["cache_bytes"], 2048)


if __name__ == "__main__":
    unittest.main()
