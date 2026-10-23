"""Independent checks of packed layout, bounds, persistence and exact ranking."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from commercial_lab.bounded_search import BoundedIndex, build_index, exact_scores, stable_topk
from commercial_lab.bounded_search.native import coarse_scores as row_coarse_scores, global_bounds as row_global_bounds
from commercial_lab.bounded_search.native_packed import coarse_scores, global_bounds
from commercial_lab.bounded_search.packed_index import PackedBoundedIndex, build_packed_index, pack_codes
from commercial_lab.etex_mmap import MappedMatrix, build_store


class PackedSearchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.rng = np.random.default_rng(416803)

    def tearDown(self):
        self.temporary.cleanup()

    def index(self, x, name="packed", **kwargs):
        path = self.root / name
        build_packed_index(x, path, chunk_rows=31)
        return PackedBoundedIndex.open(path, x, chunk_rows=17, **kwargs)

    def assert_exact(self, index, x, q, k, method="search"):
        reference = exact_scores(x, q, portable=True)
        expected = stable_topk(reference, k)
        result = getattr(index, method)(q, k)
        np.testing.assert_array_equal(result.row_ids, expected)
        np.testing.assert_array_equal(result.scores.view(np.uint64), reference[expected].view(np.uint64))
        self.assertTrue(result.stats["certificate"]["valid"])
        self.assertIsNone(result.stats["fallback_reason"])
        return result

    def edit_array(self, path, name, edit):
        file = path / f"{name}.npy"
        value = np.load(file, mmap_mode="r+", allow_pickle=False)
        edit(value)
        value.flush(); value._mmap.close()
        manifest = json.loads((path / "manifest.json").read_text())
        manifest["files"][file.name]["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
        manifest["files"][file.name]["bytes"] = file.stat().st_size
        (path / "manifest.json").write_text(json.dumps(manifest))

    def test_layout_direct_offset_and_zero_padding(self):
        # Check the specification coordinate by coordinate, rather than by
        # inversely applying the same reshape/transposition as the encoder.
        for rows in (1, 15, 16, 17, 31, 32, 33):
            for dimensions in (1, 3, 4, 5, 63, 64, 65, 100):
                codes = self.rng.integers(-128, 128, size=(rows, dimensions), dtype=np.int8)
                packed = pack_codes(codes)
                quads = (dimensions + 3) // 4
                self.assertEqual(packed.shape, ((rows + 15) // 16, quads, 16, 4))
                flat = packed.ravel()
                for row in range(((rows + 15) // 16) * 16):
                    for column in range(quads * 4):
                        offset = ((row // 16) * quads + column // 4) * 64 + (row % 16) * 4 + column % 4
                        wanted = codes[row, column] if row < rows and column < dimensions else 0
                        self.assertEqual(flat[offset], wanted)

    def test_integer_coarse_matches_row_major_and_portable_bits(self):
        for dimensions in (1, 3, 4, 5, 63, 64, 65, 100, 384):
            codes = self.rng.integers(-128, 128, size=(35, dimensions), dtype=np.int8)
            scales = self.rng.uniform(0.01, 2, size=len(codes)).astype(np.float32)
            sums = np.sum(codes, axis=1, dtype=np.int32)
            query = self.rng.integers(-128, 128, size=dimensions, dtype=np.int8)
            scale = np.float32(0.0371)
            expected = np.sum(codes.astype(np.int64) * query.astype(np.int64), axis=1)
            expected = expected.astype(np.float64) * scales.astype(np.float64) * float(scale)
            packed = pack_codes(codes)
            for portable in (False, True):
                actual = coarse_scores(packed, scales, sums, query, scale, dimensions, portable=portable)
                row = row_coarse_scores(codes, scales, sums, query, scale, portable=portable)
                np.testing.assert_array_equal(actual.view(np.uint64), expected.view(np.uint64))
                np.testing.assert_array_equal(actual.view(np.uint64), row.view(np.uint64))

    def test_maximum_dimension_unsigned_accumulation_does_not_overflow(self):
        dimensions = 65536
        codes = np.vstack((np.full(dimensions, -128, np.int8), np.full(dimensions, 127, np.int8),
                           self.rng.integers(-128, 128, size=dimensions, dtype=np.int8)))
        scales = np.array([0.125, 2, 0.31], np.float32)
        sums = np.sum(codes, axis=1, dtype=np.int32)
        packed = pack_codes(codes)
        for sign in (-128, 127):
            query = np.full(dimensions, sign, np.int8)
            expected = np.sum(codes.astype(np.int64) * query.astype(np.int64), axis=1)
            expected = expected.astype(np.float64) * scales.astype(np.float64) * 0.25
            for portable in (False, True):
                actual = coarse_scores(packed, scales, sums, query, 0.25, dimensions, portable=portable)
                np.testing.assert_array_equal(actual.view(np.uint64), expected.view(np.uint64))

    def test_native_global_filter_identical_to_v1_including_threshold(self):
        for dimensions in (1, 5, 65, 100, 384):
            codes = self.rng.integers(-127, 128, size=(51, dimensions), dtype=np.int8)
            scales = self.rng.uniform(0.01, 1, len(codes)).astype(np.float32)
            sums = np.sum(codes, axis=1, dtype=np.int32)
            query = self.rng.integers(-127, 128, size=dimensions, dtype=np.int8)
            packed = pack_codes(codes)
            for k in (1, 10, len(codes)):
                for radius in (0.0, 0.001, 1e30):
                    expected = row_global_bounds(codes, scales, sums, query, 0.027, radius, k)
                    for portable in (False, True):
                        actual = global_bounds(packed, scales, sums, query, 0.027, radius, dimensions, k,
                                               portable=portable)
                        np.testing.assert_array_equal(actual[0].view(np.uint64), expected[0].view(np.uint64))
                        self.assertEqual(np.float64(actual[1]).view(np.uint64), np.float64(expected[1]).view(np.uint64))
                        self.assertEqual(actual[2], expected[2])

    def test_strided_native_metadata_and_input_guards(self):
        codes = self.rng.integers(-127, 128, size=(29, 17), dtype=np.int8)
        scales = self.rng.uniform(0.1, 1, len(codes)).astype(np.float32)
        sums = np.sum(codes, axis=1, dtype=np.int32)
        query = codes[3]
        packed = pack_codes(codes[::-1])
        actual = coarse_scores(packed, scales[::-1], sums[::-1], query, 0.1, 17)
        wanted = row_coarse_scores(np.ascontiguousarray(codes[::-1]), scales[::-1], sums[::-1], query, 0.1)
        np.testing.assert_array_equal(actual.view(np.uint64), wanted.view(np.uint64))
        for bad in (0, 65537, True, 17.0):
            with self.assertRaises(ValueError): coarse_scores(packed, scales, sums, query, 0.1, bad)
        with self.assertRaises(ValueError): coarse_scores(packed[..., ::-1], scales, sums, query, 0.1, 17)
        with self.assertRaises(ValueError): coarse_scores(packed, scales, sums, query.astype(np.int32), 0.1, 17)
        with self.assertRaises(ValueError): global_bounds(packed, scales, sums, query, 0.1, 1, 17, True)

    def test_random_rankings_and_global_intervals_match_verified_v1(self):
        for dimensions in (1, 5, 17, 65, 100, 384):
            x = self.rng.normal(size=(83, dimensions)).astype(np.float32)
            x *= self.rng.uniform(0.1, 30, size=(len(x), 1)).astype(np.float32)
            path = self.root / f"row{dimensions}"
            row_manifest = build_index(x, path, chunk_rows=23)
            with BoundedIndex.open(path, x) as row, self.index(x, f"packed{dimensions}") as packed:
                self.assertEqual(packed.source_sha256, row_manifest["source_sha256"])
                self.assertEqual(packed._norm_envelope, row._norm_envelope)
                for q in (x[2], -x[2], np.zeros(dimensions, np.float32)):
                    coarse, lower, upper = packed.bounds(q)
                    reference = exact_scores(x, q, portable=True)
                    self.assertTrue(np.all(lower <= reference))
                    self.assertTrue(np.all(reference <= upper))
                    _, row_lower, row_upper = row.bounds(q)
                    self.assertTrue(np.all(lower <= row_lower))
                    self.assertTrue(np.all(upper >= row_upper))
                    for k in (1, 10, len(x)):
                        native = packed._filter_global_bounds(q, k)
                        row_native = row._filter_global_bounds(q, k)
                        np.testing.assert_array_equal(native[0].view(np.uint64), upper.view(np.uint64))
                        np.testing.assert_array_equal(native[0].view(np.uint64), row_native[0].view(np.uint64))
                        self.assertEqual(native[1:], row_native[1:])
                        self.assertEqual(native[1], np.partition(lower, len(x) - k)[len(x) - k])
                        fast = self.assert_exact(packed, x, q, k)
                        slow = self.assert_exact(packed, x, q, k, method="search_reference")
                        np.testing.assert_array_equal(fast.row_ids, slow.row_ids)
                        np.testing.assert_array_equal(fast.scores.view(np.uint64), slow.scores.view(np.uint64))
                        self.assertEqual(fast.stats["scanned_index_payload_bytes"], packed.index_bytes)
                        self.assertEqual(fast.stats["bounds_arrays_total_bytes"], len(x) * 8)
                        self.assertEqual(fast.stats["coarse_heap_bytes"], k * 8)
                        self.assertEqual(fast.stats["search_path"], "native_packed_global_envelope")

    def test_ties_near_ties_negative_and_extreme_fp32(self):
        near = np.ones((39, 17), np.float32)
        near[:, 6] += np.arange(len(near), dtype=np.float32) * np.finfo(np.float32).eps
        near = near[self.rng.permutation(len(near))]
        tiny, maximum = np.nextafter(np.float32(0), np.float32(1)), np.finfo(np.float32).max
        extremes = np.array([[tiny, -tiny, 0, tiny, tiny], [maximum, -maximum, maximum, maximum, 0],
                             [1, -1, 1e-30, -1e-30, tiny]], np.float32)
        cases = [
            (np.zeros((33, 5), np.float32), np.zeros(5, np.float32)),
            (np.ones((33, 5), np.float32), np.ones(5, np.float32)),
            (-np.arange(1, 34, dtype=np.float32)[:, None] * np.ones((1, 5), np.float32), np.ones(5, np.float32)),
            (near, np.ones(17, np.float32)),
            (extremes, np.full(5, tiny, np.float32)),
            (extremes, np.array([maximum, maximum, -maximum, maximum, 0], np.float32)),
        ]
        for number, (x, q) in enumerate(cases):
            with self.index(x, f"edge{number}") as index:
                self.assert_exact(index, x, q, min(7, len(x)))
                _, lower, upper = index.bounds(q)
                reference = exact_scores(x, q, portable=True)
                self.assertTrue(np.all(lower <= reference))
                self.assertTrue(np.all(reference <= upper))

    def test_only_one_code_layout_is_persisted_and_padding_is_counted(self):
        x = self.rng.normal(size=(17, 5)).astype(np.float32)
        with self.index(x) as index:
            self.assertEqual(set(p.name for p in index.path.iterdir()),
                             {"manifest.json", "packed_codes.npy", "scales.npy", "sums.npy"})
            self.assertEqual(set(index._arrays), {"packed_codes", "scales", "sums"})
            stats = index.stats()
            self.assertEqual(stats["packed_code_bytes"], 2 * 2 * 64)
            self.assertEqual(stats["code_padding_bytes"], 256 - 17 * 5)
            self.assertEqual(stats["index_bytes"], 256 + 17 * 8)
            self.assertEqual(stats["global_scan_payload_bytes"], stats["index_bytes"])
            self.assertEqual(stats["per_row_norm_array_bytes"], 0)
            self.assertEqual(stats["persistent_row_major_code_bytes"], 0)
            self.assertEqual(stats["file_bytes"], sum(p.stat().st_size for p in index.path.iterdir()))
            np.testing.assert_array_equal(index._unpack_rows(7, 17), index._unpack_rows(0, 17)[7:])
            result = self.assert_exact(index, x, x[0], 3)
            with self.assertRaises(NotImplementedError): index.search_per_row(x[0], 3)
        # Returned score/ID arrays own their data after mappings have closed.
        np.testing.assert_array_equal(result.row_ids, stable_topk(exact_scores(x, x[0]), 3))
        with self.assertRaises(ValueError): index.search(x[0], 3)

    def test_mmap_source_only_selected_rows_are_decoded_after_startup(self):
        x = np.zeros((257, 33), np.float32)
        x[:, 1] = 1
        x[0] = 0; x[0, 0] = 1
        store_path, index_path = self.root / "source.etm", self.root / "packed"
        build_store(x, store_path, group_rows=8)
        build_packed_index(x, index_path)
        query = np.zeros(33, np.float32); query[0] = 1
        with MappedMatrix.open(store_path, cache_bytes=0) as source:
            with PackedBoundedIndex.open(index_path, source, chunk_rows=17) as index:
                source.reset_stats(clear_cache=True)
                result = self.assert_exact(index, x, query, 1)
                self.assertEqual(result.stats["candidate_count"], 1)
                self.assertEqual(source.stats()["groups_decoded"], 1)
                self.assertEqual(source.stats()["rows_requested"], 1)
            np.testing.assert_array_equal(source.take([0]), x[[0]])

    def test_unaligned_custom_source_scan_and_canonical_byte_order(self):
        x = self.rng.normal(size=(47, 13)).astype(np.float32)

        class OddSource:
            shape, dtype = x.shape, np.dtype(np.float32)
            def take(self, ids): return x[ids]
            def scan_batches(self, batch_rows):
                for start in range(0, len(x), 7): yield x[start:start + 7]

        first = build_packed_index(x.astype(">f4"), self.root / "big", chunk_rows=1)
        second = build_packed_index(OddSource(), self.root / "odd", chunk_rows=19)
        self.assertEqual(first["source_sha256"], second["source_sha256"])
        self.assertEqual(first["source_sha256"], hashlib.sha256(x.astype("<f4").tobytes()).hexdigest())
        with PackedBoundedIndex.open(self.root / "odd", OddSource(), chunk_rows=19) as index:
            self.assert_exact(index, x, x[5], 5)

    def test_source_changed_and_undersized_global_certificate_are_rejected(self):
        x = np.ones((33, 17), np.float32)
        path = self.root / "packed"; build_packed_index(x, path)
        changed = x.copy(); changed[4, 3] = np.nextafter(changed[4, 3], np.float32(0))
        with self.assertRaises(ValueError): PackedBoundedIndex.open(path, changed)
        with self.assertRaises(ValueError): PackedBoundedIndex.open(path, x, verify_source=False)
        with PackedBoundedIndex.open(path, x, verify_source=False, skip_reason="Immutable source already checked in setup") as index:
            self.assertFalse(index.stats()["source_verified"])
            result = self.assert_exact(index, x, x[0], 3)
            self.assertFalse(result.stats["certificate"]["source_binding_verified"])
        manifest = json.loads((path / "manifest.json").read_text())
        manifest["norm_envelope"]["x_upper"] = 0
        (path / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "global norm bound is insufficient"):
            PackedBoundedIndex.open(path, x)

    def test_updated_hash_cannot_hide_nonzero_padding_or_bad_row_sums(self):
        x = self.rng.normal(size=(17, 5)).astype(np.float32)
        edits = (
            ("dim", "packed_codes", lambda v: v.__setitem__((0, -1, 0, 1), 1), "dimension padding"),
            ("row", "packed_codes", lambda v: v.__setitem__((-1, 0, 1, 0), 1), "row padding"),
            ("sum", "sums", lambda v: v.__setitem__(0, v[0] + 1), "row sums"),
            ("scale", "scales", lambda v: v.__setitem__(0, np.nan), "finite and positive"),
        )
        for name, array, edit, expected in edits:
            path = self.root / name; build_packed_index(x, path)
            self.edit_array(path, array, edit)
            with self.assertRaisesRegex(ValueError, expected): PackedBoundedIndex.open(path, x)

    def test_hash_paths_json_and_npy_representation_are_validated(self):
        x = np.ones((17, 5), np.float32)
        path = self.root / "packed"; build_packed_index(x, path)
        manifest_path = path / "manifest.json"
        original = manifest_path.read_bytes()
        manifest = json.loads(original)
        manifest["files"]["../packed_codes.npy"] = manifest["files"].pop("packed_codes.npy")
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError): PackedBoundedIndex.open(path, x)
        manifest_path.write_text(original.decode().replace('"norm_envelope": {', '"norm_envelope": {"x_upper": 1e10000,'))
        with self.assertRaises(ValueError): PackedBoundedIndex.open(path, x)
        manifest_path.write_bytes(original)
        file = path / "packed_codes.npy"
        original_codes = file.read_bytes()
        altered = bytearray(original_codes); altered[-1] ^= 1; file.write_bytes(altered)
        with self.assertRaisesRegex(ValueError, "SHA-256"): PackedBoundedIndex.open(path, x)
        file.write_bytes(original_codes)
        external = self.root / "external.npy"; file.rename(external); file.symlink_to(external)
        with self.assertRaises(ValueError): PackedBoundedIndex.open(path, x)
        file.unlink(); external.rename(file)
        # Even with a matching asset hash, trailing bytes are not canonical NPY.
        file.write_bytes(original_codes + b"extra")
        manifest = json.loads(original)
        manifest["files"][file.name]["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
        manifest["files"][file.name]["bytes"] = file.stat().st_size
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "representation or trailing bytes"):
            PackedBoundedIndex.open(path, x)

    def test_existing_destination_nonfinite_source_and_invalid_query(self):
        x = np.ones((17, 5), np.float32)
        path = self.root / "packed"; build_packed_index(x, path)
        with self.assertRaises(FileExistsError): build_packed_index(x, path)
        bad = x.copy(); bad[-1, -1] = np.inf
        with self.assertRaises(ValueError): build_packed_index(bad, self.root / "bad", chunk_rows=16)
        self.assertFalse((self.root / "bad").exists())
        with PackedBoundedIndex.open(path, x) as index:
            for query in (np.ones(5, np.float64), np.ones(4, np.float32), np.full(5, np.nan, np.float32)):
                with self.assertRaises(ValueError): index.search(query, 1)
            for k in (0, 18, True, 2.0):
                with self.assertRaises(ValueError): index.search(x[0], k)
        with self.assertRaises(ValueError): BoundedIndex.open(path, x)

    def test_nonfinite_radius_falls_back_to_full_exact_scan(self):
        x = self.rng.normal(size=(37, 19)).astype(np.float32)
        query = x[3]
        with self.index(x) as index:
            with patch.object(index, "_filter_global_bounds", return_value=(np.full(len(x), np.inf), -np.inf, False, np.inf)):
                result = index.search(query, 7)
            expected_scores = exact_scores(x, query, portable=True)
            expected_ids = stable_topk(expected_scores, 7)
            np.testing.assert_array_equal(result.row_ids, expected_ids)
            np.testing.assert_array_equal(result.scores.view(np.uint64), expected_scores[expected_ids].view(np.uint64))
            self.assertEqual(result.stats["candidate_count"], len(x))
            self.assertEqual(result.stats["scanned_index_payload_bytes"], 0)
            self.assertEqual(result.stats["fallback_reason"], "nonfinite_or_invalid_error_bounds")


if __name__ == "__main__":
    unittest.main()
