"""V3 invariants: original FP32 columns, mixed bounds and exact final ranking."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from commercial_lab.bounded_search import exact_scores, stable_topk
from commercial_lab.bounded_search.mixed_index import MixedPackedBoundedIndex, build_mixed_packed_index
from commercial_lab.bounded_search.native_mixed import coarse_scores, global_bounds
from commercial_lab.bounded_search.packed_index import PackedBoundedIndex, pack_codes


class MixedSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rng = np.random.default_rng(1949541120)

    def tearDown(self):
        self.temp.cleanup()

    def index(self, x, columns, name="mixed"):
        path = self.root / name
        build_mixed_packed_index(x, path, exact_columns=columns, chunk_rows=19)
        return MixedPackedBoundedIndex.open(path, x, chunk_rows=17)

    def assert_exact(self, index, x, q, k, method="search"):
        scores = exact_scores(x, q, portable=True)
        ids = stable_topk(scores, k)
        result = getattr(index, method)(q, k)
        np.testing.assert_array_equal(result.row_ids, ids)
        np.testing.assert_array_equal(result.scores.view(np.uint64), scores[ids].view(np.uint64))
        self.assertTrue(result.stats["certificate"]["valid"])
        self.assertIsNone(result.stats["fallback_reason"])
        return result

    def edit_array(self, path, name, edit):
        file = path / f"{name}.npy"
        a = np.load(file, mmap_mode="r+", allow_pickle=False)
        edit(a); a.flush(); a._mmap.close()
        manifest_path = path / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"][file.name]["sha256"] = hashlib.sha256(file.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))

    def test_native_mixed_scalar_simd_and_independent_integer_coarse(self):
        for d in (1, 5, 17, 65, 100):
            columns = np.arange(min(d, 4))
            codes = self.rng.integers(-127, 128, size=(35, d), dtype=np.int8)
            codes[:, columns] = 0
            q = self.rng.integers(-127, 128, size=d, dtype=np.int8); q[columns] = 0
            scales = self.rng.uniform(0.01, 1, len(codes)).astype(np.float32)
            sums = codes.sum(axis=1, dtype=np.int32)
            values = self.rng.normal(size=(len(codes), len(columns))).astype(np.float32)
            qe = self.rng.normal(size=len(columns)).astype(np.float32)
            qs = np.float32(0.037)
            expected = np.sum(codes.astype(np.int64) * q.astype(np.int64), axis=1).astype(np.float64)
            expected = expected * scales.astype(np.float64) * float(qs)
            for j in range(len(columns)):
                expected += values[:, j].astype(np.float64) * float(qe[j])
            for portable in (False, True):
                actual = coarse_scores(pack_codes(codes), scales, sums, q, qs, values, qe, d, portable=portable)
                np.testing.assert_array_equal(actual.view(np.uint64), expected.view(np.uint64))
                upper, threshold, valid = global_bounds(pack_codes(codes), scales, sums, q, qs,
                    values, qe, 0.013, d, 7, portable=portable)
                np.testing.assert_array_equal(upper.view(np.uint64), np.nextafter(expected + 0.013, np.inf).view(np.uint64))
                self.assertEqual(threshold, np.nextafter(np.partition(expected, len(codes) - 7)[len(codes) - 7] - 0.013, -np.inf))
                self.assertTrue(valid)

    def test_random_rankings_full_bounds_and_reference(self):
        for d in (1, 5, 17, 65, 100):
            x = self.rng.normal(size=(47, d)).astype(np.float32)
            x[:, 0] *= 100
            with self.index(x, [0, d - 1] if d > 1 else [0], f"d{d}") as index:
                for q in (x[3], -x[4], np.zeros(d, np.float32)):
                    coarse, lower, upper = index.bounds(q)
                    reference = exact_scores(x, q, portable=True)
                    self.assertTrue(np.all(lower <= reference))
                    self.assertTrue(np.all(reference <= upper))
                    for k in (1, 7, len(x)):
                        native_upper, threshold, valid, radius = index._filter_global_bounds(q, k)
                        self.assertTrue(valid)
                        np.testing.assert_array_equal(native_upper.view(np.uint64), upper.view(np.uint64))
                        self.assertEqual(threshold, np.partition(lower, len(x) - k)[len(x) - k])
                        fast = self.assert_exact(index, x, q, k)
                        self.assert_exact(index, x, q, k, method="search_reference")
                        self.assertEqual(fast.stats["search_path"], "native_mixed_packed_global_envelope")
                        self.assertEqual(fast.stats["scanned_index_payload_bytes"], index.index_bytes)

    def test_exact_column_dominance_mixed_norms_and_payload(self):
        x = np.zeros((17, 5), np.float32)
        x[:, 3] = np.arange(1, 18, dtype=np.float32) * 1000
        with self.index(x, [3]) as index:
            self.assertGreaterEqual(index._norm_envelope["xhat_upper"], 17000)
            np.testing.assert_array_equal(index._arrays["scales"], np.ones(17, np.float32))
            np.testing.assert_array_equal(index._unpack_rows(0, 17), np.zeros((17, 5), np.int8))
            stats = index.stats()
            self.assertEqual(stats["format_version"], 3)
            self.assertEqual(stats["exact_columns"], [3])
            self.assertEqual(stats["code_padding_bytes"], 256 - 85)
            self.assertEqual(stats["index_bytes"], 256 + 17 * 8 + 17 * 4)
            self.assertEqual(stats["exact_column_payload_bytes"], 68)
            self.assertEqual(stats["file_bytes"], sum(f.stat().st_size for f in index.path.iterdir()))
            self.assertEqual(set(index._arrays), {"packed_codes", "scales", "sums", "exact_values"})
            self.assert_exact(index, x, x[2], 4)

    def test_all_columns_zero_tail_extremes_and_subnormals(self):
        tiny = np.nextafter(np.float32(0), np.float32(1)); high = np.finfo(np.float32).max
        x = np.array([[high, -high, high, -high, 1], [tiny, -tiny, 0, tiny, tiny],
                      [1, -1, 1e-30, -1e-30, 0], [0, 0, 0, 0, 0]], np.float32)
        with self.index(x, list(range(5))) as index:
            np.testing.assert_array_equal(index._arrays["scales"], np.ones(4, np.float32))
            self.assertFalse(np.any(index._arrays["packed_codes"]))
            for q in (x[0], x[1], np.ones(5, np.float32), -x[0]):
                self.assert_exact(index, x, q, 3)
                _, lower, upper = index.bounds(q)
                reference = exact_scores(x, q, portable=True)
                self.assertTrue(np.all(lower <= reference))
                self.assertTrue(np.all(reference <= upper))

    def test_ties_and_perturbations_in_exact_columns(self):
        x = np.ones((39, 17), np.float32)
        x[:, 4] += np.arange(len(x), dtype=np.float32) * np.finfo(np.float32).eps
        x = x[self.rng.permutation(len(x))]
        with self.index(x, [4]) as index:
            q = np.zeros(17, np.float32); q[4] = 1
            self.assert_exact(index, x, q, 9)
            zeros = self.assert_exact(index, x, np.zeros(17, np.float32), 9)
            np.testing.assert_array_equal(zeros.row_ids, np.arange(9))
            q[4] = -1
            self.assert_exact(index, x, q, 9)

    def test_source_unchanged_canonical_and_signed_zero(self):
        x = self.rng.normal(size=(33, 7)).astype(np.float32)
        x[0, 2], x[1, 2] = np.float32(-0.0), np.float32(0.0)
        original = x.copy().view(np.uint32)
        manifest = build_mixed_packed_index(x.astype(">f4"), self.root / "big", exact_columns=[5, 2])
        with self.index(x, [2, 5]) as index:
            np.testing.assert_array_equal(x.view(np.uint32), original)
            np.testing.assert_array_equal(index._arrays["exact_values"].view(np.uint32),
                                          np.ascontiguousarray(x[:, [2, 5]]).view(np.uint32))
            self.assertEqual(index.source_sha256, manifest["source_sha256"])
            self.assertEqual(index.source_sha256, hashlib.sha256(x.astype("<f4").tobytes()).hexdigest())
            self.assert_exact(index, x, x[1], 3)

    def test_exact_assets_and_changed_source_rejected(self):
        x = self.rng.normal(size=(33, 7)).astype(np.float32)
        path = self.root / "mixed"; build_mixed_packed_index(x, path, exact_columns=[2, 5])
        for column in (2, 1):
            changed = x.copy(); changed[3, column] = np.nextafter(changed[3, column], np.float32(np.inf))
            with self.assertRaises(ValueError): MixedPackedBoundedIndex.open(path, changed)
        self.edit_array(path, "exact_values", lambda a: a.__setitem__((3, 0), np.nextafter(a[3, 0], np.float32(np.inf))))
        with self.assertRaisesRegex(ValueError, "differ bitwise"):
            MixedPackedBoundedIndex.open(path, x)
        with self.assertRaises(ValueError): MixedPackedBoundedIndex.open(path, x, verify_source=False)

    def test_nonzero_exact_codes_rejected_even_with_updated_hashes(self):
        x = self.rng.normal(size=(17, 5)).astype(np.float32)
        path = self.root / "mixed"; build_mixed_packed_index(x, path, exact_columns=[3])
        self.edit_array(path, "packed_codes", lambda a: a.__setitem__((0, 0, 0, 3), 1))
        self.edit_array(path, "sums", lambda a: a.__setitem__(0, a[0] + 1))
        with self.assertRaisesRegex(ValueError, "exact columns must be zero"):
            MixedPackedBoundedIndex.open(path, x, verify_source=False, skip_reason="Test structural guards independently")

    def test_columns_manifest_and_native_shape_guards(self):
        x = np.ones((17, 5), np.float32)
        for i, columns in enumerate(([], [0, 0], [-1], [5], [True], [1.0], None)):
            with self.assertRaises(ValueError): build_mixed_packed_index(x, self.root / f"bad{i}", exact_columns=columns)
        with self.index(x, [0, 3]) as index:
            with self.assertRaises(ValueError): PackedBoundedIndex.open(index.path, x)
            qc, qs, radius, qe = index._query_metadata(x[0])
            a = index._arrays
            with self.assertRaises(ValueError):
                coarse_scores(a["packed_codes"], a["scales"], a["sums"], qc, qs, a["exact_values"][:, ::-1], qe, 5)
            with self.assertRaises(ValueError):
                coarse_scores(a["packed_codes"], a["scales"], a["sums"], qc, qs, a["exact_values"], qe.astype(np.float64), 5)
            path = index.path
        manifest_path = path / "manifest.json"
        manifest = json.loads(manifest_path.read_text()); manifest["exact_columns"] = [3, 0]
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "sorted"): MixedPackedBoundedIndex.open(path, x)

    def test_nonfinite_envelope_fallback_and_owned_outputs(self):
        x = self.rng.normal(size=(33, 17)).astype(np.float32)
        with self.index(x, [0, 5]) as index:
            query = x[3]
            with patch.object(index, "_filter_global_bounds", return_value=(np.full(len(x), np.inf), -np.inf, False, np.inf)):
                result = index.search(query, 5)
            self.assertEqual(result.stats["candidate_count"], len(x))
            self.assertEqual(result.stats["scanned_index_payload_bytes"], 0)
            self.assertEqual(result.stats["fallback_reason"], "nonfinite_or_invalid_error_bounds")
        full = exact_scores(x, query, portable=True); expected = stable_topk(full, 5)
        np.testing.assert_array_equal(result.row_ids, expected)
        np.testing.assert_array_equal(result.scores.view(np.uint64), full[expected].view(np.uint64))


if __name__ == "__main__":
    unittest.main()
