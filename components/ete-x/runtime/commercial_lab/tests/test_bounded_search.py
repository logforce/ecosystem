"""Exact reference ranking, conservative bounds, immutable source binding."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from commercial_lab.bounded_search import BoundedIndex, build_index, exact_scores, stable_topk
from commercial_lab.bounded_search.index import _norm_upper
from commercial_lab.bounded_search.native import coarse_scores
from commercial_lab.etex_mmap import MappedMatrix, build_store


class BoundedSearchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.rng = np.random.default_rng(901731)

    def tearDown(self):
        self.temporary.cleanup()

    def index(self, x, name="index", **kwargs):
        path = self.root / name
        build_index(x, path, chunk_rows=31)
        return BoundedIndex.open(path, x, **kwargs)

    def assert_exact(self, index, x, q, k, method="search"):
        all_scores = exact_scores(x, q, portable=True)
        expected = stable_topk(all_scores, k)
        result = getattr(index, method)(q, k)
        np.testing.assert_array_equal(result.row_ids, expected)
        np.testing.assert_array_equal(result.scores.view(np.uint64), all_scores[expected].view(np.uint64))
        self.assertTrue(result.stats["certificate"]["valid"])
        certificate = result.stats["certificate"]
        if certificate["max_excluded_upper"] is not None:
            self.assertLessEqual(certificate["max_excluded_upper"], certificate["final_kth_score"])
        return result

    def test_random_rankings_and_bounds_multiple_dimensions(self):
        for d in (1, 7, 8, 17, 65, 100, 384):
            x = self.rng.normal(size=(91, d)).astype(np.float32)
            x *= self.rng.uniform(0.01, 100, size=(91, 1)).astype(np.float32)
            with self.index(x, f"d{d}") as index:
                for _ in range(3):
                    q = self.rng.normal(size=d).astype(np.float32)
                    coarse, lower, upper = index.bounds(q)
                    reference = exact_scores(x, q, portable=True)
                    self.assertTrue(np.all(lower <= reference))
                    self.assertTrue(np.all(reference <= upper))
                    self.assertTrue(np.all(lower <= coarse))
                    for k in (1, 10, len(x)):
                        self.assert_exact(index, x, q, k)

    def test_zero_negative_and_all_tied_scores(self):
        for name, x, q in (
            ("zeros", np.zeros((45, 17), np.float32), np.zeros(17, np.float32)),
            ("ties", np.ones((45, 17), np.float32), np.ones(17, np.float32)),
            ("negative", -np.arange(1, 46, dtype=np.float32)[:, None] * np.ones((1, 17), np.float32),
             np.ones(17, np.float32)),
        ):
            with self.index(x, name) as index:
                result = self.assert_exact(index, x, q, 7)
                np.testing.assert_array_equal(result.row_ids, np.arange(7))
                if name != "negative":
                    self.assertEqual(result.stats["candidate_count"], len(x))

    def test_near_ties_that_quantization_cannot_distinguish(self):
        x = np.ones((120, 33), dtype=np.float32)
        x[:, 7] = np.float32(1) + np.arange(120, dtype=np.float32) * np.finfo(np.float32).eps
        x = x[self.rng.permutation(len(x))]
        q = np.ones(33, np.float32)
        with self.index(x) as index:
            self.assert_exact(index, x, q, 13)

    def test_extreme_and_subnormal_fp32_remain_bounded(self):
        tiny = np.nextafter(np.float32(0), np.float32(1))
        maximum = np.finfo(np.float32).max
        x = np.array([[tiny, -tiny, 0, tiny, 0, 0, 0, tiny, -tiny],
                      [maximum, -maximum, maximum, maximum, 0, -maximum, 1, 2, 3],
                      [1, -1, 1e-30, -1e-30, tiny, 0, 1e30, -1e30, 1]], dtype=np.float32)
        queries = (np.full(9, tiny, np.float32), np.ones(9, np.float32),
                   np.array([maximum, maximum, -maximum, maximum, 0, maximum, 1, -2, 3], np.float32))
        with self.index(x) as index:
            for q in queries:
                _, lower, upper = index.bounds(q)
                reference = exact_scores(x, q, portable=True)
                self.assertTrue(np.all(lower <= reference))
                self.assertTrue(np.all(reference <= upper))
                self.assert_exact(index, x, q, 2)

    def test_norm_upper_against_wider_arithmetic(self):
        # Independent arithmetic check where longdouble has extra precision.
        if np.finfo(np.longdouble).nmant <= np.finfo(np.float64).nmant:
            self.skipTest("longdouble has no additional precision on this platform")
        x = self.rng.normal(size=(37, 385)).astype(np.float32)
        x[0] = np.finfo(np.float32).max
        x[1] = np.nextafter(np.float32(0), np.float32(1))
        wide = x.astype(np.longdouble)
        independent = np.sqrt(np.sum(wide * wide, axis=1, dtype=np.longdouble))
        self.assertTrue(np.all(_norm_upper(x).astype(np.longdouble) >= independent))

    def test_mmap_source_only_requested_rows_decoded_after_startup(self):
        x = np.zeros((256, 32), dtype=np.float32)
        x[:, 1] = 1
        x[0] = 0; x[0, 0] = 1
        path = self.root / "source.etm"
        build_store(x, path, group_rows=8)
        build_index(x, self.root / "index")
        q = np.zeros(32, np.float32); q[0] = 1
        with MappedMatrix.open(path, cache_bytes=0) as source:
            with BoundedIndex.open(self.root / "index", source) as index:
                source.reset_stats(clear_cache=True)
                result = self.assert_exact(index, x, q, 1)
                self.assertEqual(result.stats["candidate_count"], 1)
                self.assertEqual(source.stats()["groups_decoded"], 1)
                self.assertEqual(source.stats()["rows_requested"], 1)
            # The external source is not owned/closed by the index.
            np.testing.assert_array_equal(source.take([0]), x[[0]])

    def test_source_changed_rejected_and_skip_requires_reason(self):
        x = self.rng.normal(size=(43, 17)).astype(np.float32)
        path = self.root / "index"; build_index(x, path)
        changed = x.copy(); changed[20, 9] = np.nextafter(changed[20, 9], np.float32(np.inf))
        with self.assertRaises(ValueError): BoundedIndex.open(path, changed)
        with self.assertRaises(ValueError): BoundedIndex.open(path, x, verify_source=False)
        with BoundedIndex.open(path, x, verify_source=False, skip_reason="Already bound and validated in benchmark setup") as index:
            self.assertFalse(index.stats()["source_verified"])
            result = self.assert_exact(index, x, x[0], 5)
            self.assertIn("benchmark", result.stats["source_verification_skip_reason"])

    def test_big_endian_source_has_canonical_fingerprint(self):
        x = self.rng.normal(size=(37, 13)).astype(np.float32)
        first = build_index(x, self.root / "native")
        second = build_index(x.astype(">f4"), self.root / "big")
        self.assertEqual(first["source_sha256"], second["source_sha256"])
        self.assertEqual(first["source_sha256"], hashlib.sha256(x.astype("<f4").tobytes()).hexdigest())
        with BoundedIndex.open(self.root / "big", x) as index:
            self.assert_exact(index, x, x[0], 5)

    def test_updated_hash_cannot_hide_undersized_norm_certificate(self):
        x = np.ones((41, 17), np.float32)
        path = self.root / "index"; build_index(x, path)
        array_path = path / "x_upper.npy"
        norms = np.load(array_path, mmap_mode="r+", allow_pickle=False)
        norms[:] = 0; norms.flush(); norms._mmap.close()
        manifest = json.loads((path / "manifest.json").read_text())
        manifest["files"][array_path.name]["sha256"] = hashlib.sha256(array_path.read_bytes()).hexdigest()
        (path / "manifest.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "bound is insufficient"):
            BoundedIndex.open(path, x)

    def test_hash_path_and_manifest_validation(self):
        x = np.ones((25, 8), np.float32)
        path = self.root / "index"; build_index(x, path)
        manifest_path = path / "manifest.json"
        original = manifest_path.read_bytes()
        manifest = json.loads(original)
        manifest["files"]["../codes.npy"] = manifest["files"].pop("codes.npy")
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError): BoundedIndex.open(path, x)
        manifest_path.write_bytes(original)
        file = path / "codes.npy"
        blob = bytearray(file.read_bytes()); blob[-1] ^= 1; file.write_bytes(blob)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            BoundedIndex.open(path, x)

    def test_nonfinite_bounds_fall_back_to_full_exact_scan(self):
        x = self.rng.normal(size=(53, 19)).astype(np.float32)
        q = self.rng.normal(size=19).astype(np.float32)
        with self.index(x) as index:
            fabricated = (np.zeros(len(x)), np.full(len(x), -np.inf), np.full(len(x), np.inf))
            with patch.object(index, "_filter_global_bounds", return_value=(fabricated[2], -np.inf, False, np.inf)):
                result = self.assert_exact(index, x, q, 7)
            self.assertEqual(result.stats["candidate_count"], len(x))
            self.assertEqual(result.stats["fallback_reason"], "nonfinite_or_invalid_error_bounds")
            self.assertEqual(result.stats["certificate"]["excluded_count"], 0)
            with patch.object(index, "_filter_bounds", return_value=(fabricated[2], -np.inf, False)):
                per_row = self.assert_exact(index, x, q, 7, method="search_per_row")
            self.assertEqual(per_row.stats["candidate_count"], len(x))
            with patch.object(index, "_bounds", return_value=fabricated):
                reference = index.search_reference(q, 7)
            np.testing.assert_array_equal(reference.row_ids, result.row_ids)
            self.assertEqual(reference.stats["fallback_reason"], "nonfinite_or_invalid_error_bounds")

    def test_fused_bounds_and_heap_match_independent_python_arrays(self):
        for d in (1, 7, 17, 63, 64, 65, 100, 384):
            x = self.rng.normal(size=(137, d)).astype(np.float32)
            x[0] = 0
            with self.index(x, f"fused{d}") as index:
                for q in (x[1], -x[1], np.zeros(d, np.float32)):
                    _, lower, upper = index.bounds(q)
                    for k in (1, 10, len(x)):
                        fused_upper, threshold, valid = index._filter_bounds(q, k)
                        self.assertTrue(valid)
                        np.testing.assert_array_equal(fused_upper.view(np.uint64), upper.view(np.uint64))
                        self.assertEqual(threshold, np.partition(lower, len(x)-k)[len(x)-k])
                        fused = self.assert_exact(index, x, q, k, method="search_per_row")
                        reference = index.search_reference(q, k)
                        np.testing.assert_array_equal(fused.row_ids, reference.row_ids)
                        np.testing.assert_array_equal(fused.scores.view(np.uint64), reference.scores.view(np.uint64))
                        self.assertEqual(fused.stats["bounds_arrays_total_bytes"], len(x)*8)
                        self.assertEqual(fused.stats["lower_heap_bytes"], k*8)
                        self.assertEqual(reference.stats["bounds_arrays_total_bytes"], len(x)*24)

    def test_global_envelope_contains_per_row_intervals_and_preserves_full_ranking(self):
        for d in (1, 7, 65, 100, 384):
            x = self.rng.normal(size=(149, d)).astype(np.float32)
            x *= np.geomspace(0.001, 1000, len(x), dtype=np.float32)[:, None]
            x[0] = 0
            with self.index(x, f"global{d}") as index:
                for q in (x[50], -x[50], np.zeros(d, np.float32)):
                    coarse, lower, upper = index.bounds(q)
                    for k in (1, 10, len(x)):
                        global_upper, threshold, valid, radius = index._filter_global_bounds(q, k)
                        self.assertTrue(valid)
                        global_lower = np.nextafter(coarse-radius, -np.inf)
                        self.assertTrue(np.all(global_upper >= upper))
                        self.assertTrue(np.all(global_lower <= lower))
                        np.testing.assert_array_equal(global_upper.view(np.uint64),
                            np.nextafter(coarse+radius, np.inf).view(np.uint64))
                        self.assertEqual(threshold, np.partition(global_lower, len(x)-k)[len(x)-k])
                        result = self.assert_exact(index, x, q, k)
                        per_row = index.search_per_row(q, k)
                        np.testing.assert_array_equal(result.row_ids, per_row.row_ids)
                        np.testing.assert_array_equal(result.scores.view(np.uint64), per_row.scores.view(np.uint64))
                        self.assertEqual(result.stats["index_bytes"], len(x)*(d+32))
                        self.assertEqual(result.stats["scanned_index_payload_bytes"], len(x)*(d+8))
                        self.assertEqual(per_row.stats["scanned_index_payload_bytes"], len(x)*(d+32))
                        self.assertEqual(result.stats["coarse_heap_bytes"], k*8)
                        self.assertEqual(result.stats["lower_heap_bytes"], 0)
                        self.assertEqual(result.stats["bounds_arrays_total_bytes"], len(x)*8)

    def test_finite_but_overly_large_envelope_falls_back_without_scanning_codes(self):
        x = self.rng.normal(size=(31, 17)).astype(np.float32)
        with self.index(x) as index:
            # These remain conservative and finite, but their query products
            # overflow; skipping exclusions must preserve the complete result.
            with patch.dict(index._norm_envelope, {name: 1e308 for name in index._norm_envelope}):
                result = self.assert_exact(index, x, np.ones(17, np.float32), 5)
            self.assertEqual(result.stats["fallback_reason"], "nonfinite_or_invalid_error_bounds")
            self.assertEqual(result.stats["candidate_count"], len(x))
            self.assertEqual(result.stats["scanned_index_payload_bytes"], 0)

    def test_fetched_interval_violation_triggers_full_scan(self):
        x = np.arange(1, 31, dtype=np.float32)[:, None]
        with self.index(x) as index:
            upper = np.full(len(x), -1.0); upper[0] = 0
            with patch.object(index, "_filter_global_bounds", return_value=(upper, 0.0, True, 0.0)):
                result = self.assert_exact(index, x, np.ones(1, np.float32), 1)
            self.assertEqual(result.stats["fallback_reason"], "candidate_above_upper_bound")
            self.assertEqual(result.stats["candidate_count"], len(x))
            self.assertEqual(result.stats["refinement_rows_scored"], len(x)+1)

    def test_invalid_inputs_existing_destination_and_lifetime(self):
        x = np.ones((20, 9), np.float32)
        for value in (x.astype(np.float16), x.astype(np.float64), np.empty((0, 9), np.float32), x[0]):
            with self.assertRaises((TypeError, ValueError)):
                build_index(value, self.root / "invalid")
        for bad in (np.nan, np.inf, -np.inf):
            values = x.copy(); values[2, 3] = bad
            with self.assertRaises(ValueError): build_index(values, self.root / "nonfinite")
        with self.index(x) as index:
            result = index.search(x[0], 3)
            for q in (x[0].astype(np.float64), x[0, :-1], np.full(9, np.nan, np.float32)):
                with self.assertRaises(ValueError): index.search(q)
            for k in (0, 21, True, 1.5):
                with self.assertRaises(ValueError): index.search(x[0], k)
            with self.assertRaises(FileExistsError): build_index(x, self.root / "index")
            self.assertEqual(index.stats()["index_bytes"], len(x) * (x.shape[1] + 32))
        np.testing.assert_array_equal(result.row_ids, [0, 1, 2])
        self.assertTrue(np.all(np.isfinite(result.scores)))
        with self.assertRaises(ValueError): index.search(x[0])
        index.close()

    def test_stable_topk_handles_ties_and_provided_ids(self):
        scores = np.array([1, 2, 2, -0.0, 0.0, 2], np.float64)
        ids = np.array([15, 7, 6, 2, 1, 3])
        np.testing.assert_array_equal(stable_topk(scores, 4, ids), [3, 6, 7, 15])
        np.testing.assert_array_equal(stable_topk(scores, 6, ids), [3, 6, 7, 15, 1, 2])
        self.assertEqual(stable_topk(scores, 0).size, 0)
        with self.assertRaises(ValueError): stable_topk(np.array([np.nan]), 1)

    def test_native_simd_and_portable_scores_are_bitwise_identical(self):
        for d in (1, 7, 8, 15, 16, 31, 63, 64, 65, 100, 384):
            x = self.rng.normal(size=(19, d)).astype(np.float32)
            x[0] = np.nextafter(np.float32(0), np.float32(1))
            q = self.rng.normal(size=d).astype(np.float32)
            np.testing.assert_array_equal(exact_scores(x, q).view(np.uint64),
                                          exact_scores(x, q, portable=True).view(np.uint64))
        with self.assertRaises(ValueError): exact_scores(np.ones(4, np.float32), np.ones(4, np.float32))

    def test_native_coarse_matches_exact_integer_reference_and_strided_metadata(self):
        for d in (1, 15, 16, 63, 64, 65, 100, 384, 65536):
            codes = self.rng.integers(-128, 128, size=(4, d), dtype=np.int8)
            q = self.rng.integers(-128, 128, size=d, dtype=np.int8)
            scales = np.array([0.125, 0.25, 0.5, 1], np.float32)[::-1]
            # Reverse-strided metadata must be normalized before native access.
            sums = np.sum(codes, axis=1, dtype=np.int32)[::-1].copy()[::-1]
            coarse = coarse_scores(codes, scales, sums, q, np.float32(0.125))
            portable = coarse_scores(codes, scales, sums, q, np.float32(0.125), portable=True)
            integer = np.sum(codes.astype(np.int64) * q.astype(np.int64), axis=1, dtype=np.int64)
            expected = integer.astype(np.float64) * scales.astype(np.float64) * 0.125
            np.testing.assert_array_equal(coarse.view(np.uint64), expected.view(np.uint64))
            np.testing.assert_array_equal(coarse.view(np.uint64), portable.view(np.uint64))


if __name__ == "__main__":
    unittest.main()
