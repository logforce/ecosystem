"""Meaningful pooling, padding, pair-tokenization and actual-model checks."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from commercial_lab.nonar import MiniLMEncoder, MiniLMReranker, mean_pool
from commercial_lab.nonar.benchmark import compare_outputs


LAB = Path(__file__).resolve().parents[1]
DEPENDENCIES = all(importlib.util.find_spec(name) is not None
                   for name in ("onnx", "onnxruntime", "tokenizers"))


class PoolingTests(unittest.TestCase):
    def test_padding_is_excluded_and_vectors_are_normalized(self):
        hidden = np.array([[[2., 0.], [0., 2.], [999., -999.]],
                           [[3., 4.], [999., 999.], [999., 999.]]], dtype=np.float32)
        mask = np.array([[1, 1, 0], [1, 0, 0]])
        np.testing.assert_allclose(mean_pool(hidden, mask),
                                   [[2 ** -.5, 2 ** -.5], [.6, .8]], atol=1e-7)
        np.testing.assert_array_equal(mean_pool(hidden, mask, normalize=False), [[1, 1], [3, 4]])

    def test_empty_mask_is_finite_and_bad_layout_rejected(self):
        value = mean_pool(np.ones((1, 3, 4)), np.zeros((1, 3)))
        np.testing.assert_array_equal(value, np.zeros((1, 4)))
        with self.assertRaises(ValueError):
            mean_pool(np.ones((1, 3, 4)), np.ones((3,)))
        with self.assertRaises(ValueError):
            mean_pool(np.ones((1, 3, 4)), np.full((1, 3), 2))


class ComparisonTests(unittest.TestCase):
    def test_bit_identity_distinguishes_signed_zero_and_dtypes(self):
        plus = np.array([0., 1.], dtype=np.float32)
        minus = np.array([-0., 1.], dtype=np.float32)
        self.assertTrue(compare_outputs(plus, minus)["allclose"])
        self.assertFalse(compare_outputs(plus, minus)["bit_identical"])
        self.assertFalse(compare_outputs(plus, plus.astype(np.float64))["bit_identical"])
        self.assertTrue(compare_outputs(plus, plus.copy())["bit_identical"])


def _fixture(directory: Path, task: str):
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    from tokenizers import Tokenizer, models, normalizers, pre_tokenizers, processors

    directory.mkdir()
    vocab = {word: index for index, word in enumerate(
        ("[PAD]", "[UNK]", "[CLS]", "[SEP]", "alpha", "beta", "gamma", "delta", "."))}
    tokenizer = Tokenizer(models.WordPiece(vocab, unk_token="[UNK]"))
    tokenizer.normalizer = normalizers.BertNormalizer(lowercase=True)
    tokenizer.pre_tokenizer = pre_tokenizers.BertPreTokenizer()
    tokenizer.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", pair="[CLS] $A [SEP] $B:1 [SEP]:1",
        special_tokens=[("[CLS]", 2), ("[SEP]", 3)])
    tokenizer.save(str(directory / "tokenizer.json"))
    (directory / "config.json").write_text(json.dumps({"hidden_size": 384,
        "max_position_embeddings": 32, "pad_token_id": 0}))
    inputs = [helper.make_tensor_value_info(name, TensorProto.INT64, ["batch", "sequence"])
              for name in ("input_ids", "attention_mask", "token_type_ids")]
    nodes = [helper.make_node("Cast", ["input_ids"], ["float_ids"], to=TensorProto.FLOAT)]
    constants = []
    if task == "encoder":
        constants.extend([numpy_helper.from_array(np.array([2], dtype=np.int64), "axes"),
                          numpy_helper.from_array(np.array([1, 1, 384], dtype=np.int64), "repeats"),
                          numpy_helper.from_array(np.arange(384, dtype=np.float32) / 100, "offset")])
        nodes.extend([helper.make_node("Unsqueeze", ["float_ids", "axes"], ["expanded"]),
                      helper.make_node("Tile", ["expanded", "repeats"], ["tiled"]),
                      helper.make_node("Add", ["tiled", "offset"], ["last_hidden_state"])])
        output = helper.make_tensor_value_info("last_hidden_state", TensorProto.FLOAT,
                                               ["batch", "sequence", 384])
    else:
        constants.append(numpy_helper.from_array(np.array([1], dtype=np.int64), "axes"))
        nodes.extend([helper.make_node("Cast", ["token_type_ids"], ["float_types"], to=TensorProto.FLOAT),
                      helper.make_node("Mul", ["float_ids", "float_types"], ["doc_tokens"]),
                      helper.make_node("ReduceSum", ["doc_tokens", "axes"], ["logits"], keepdims=1)])
        output = helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["batch", 1])
    graph = helper.make_graph(nodes, "test-nonar", inputs, [output], constants)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=8)
    onnx.checker.check_model(model)
    onnx.save(model, directory / "model.onnx")


@unittest.skipUnless(DEPENDENCIES, "ONNX Runtime, ONNX and tokenizers are optional")
class BatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="nonar-test-", dir=LAB / "tests")
        cls.root = Path(cls.directory.name)
        _fixture(cls.root / "encoder", "encoder")
        _fixture(cls.root / "reranker", "reranker")
        cls.encoder = MiniLMEncoder(cls.root / "encoder", max_length=8)
        cls.reranker = MiniLMReranker(cls.root / "reranker", max_length=8)

    @classmethod
    def tearDownClass(cls):
        del cls.encoder, cls.reranker
        cls.directory.cleanup()

    def test_sorting_padding_tail_batch_and_order_restoration(self):
        texts = ["alpha", "beta " * 10, "gamma delta", "", "delta alpha beta"]
        reference = self.encoder.encode(texts, batch_size=1, sort_by_length=False)
        sorted_inputs = self.encoder.prepare(texts, batch_size=2, sort_by_length=True)
        self.assertEqual([batch.size for batch in sorted_inputs.batches], [2, 2, 1])
        self.assertEqual(sorted_inputs.batches[0].indices.tolist(), [1, 4])
        result = self.encoder.predict_prepared(sorted_inputs)
        np.testing.assert_allclose(result, reference, atol=1e-7, rtol=1e-6)
        for batch in sorted_inputs.batches:
            self.assertTrue(np.all(batch.inputs["input_ids"][batch.attention_mask == 0] == 0))
        self.assertEqual(sorted_inputs.lengths.tolist(), [3, 8, 4, 2, 5])
        self.assertEqual(sorted_inputs.batches[0].inputs["input_ids"][0, -1], 3)
        np.testing.assert_allclose(np.linalg.norm(result, axis=1), 1, atol=1e-6)

    def test_empty_inputs_and_invalid_requests(self):
        self.assertEqual(self.encoder.encode([]).shape, (0, 384))
        self.assertEqual(self.reranker.score([]).shape, (0,))
        with self.assertRaises(ValueError):
            self.encoder.encode(["alpha"], batch_size=0)
        with self.assertRaises(TypeError):
            self.encoder.encode(["alpha", None])
        with self.assertRaises(ValueError):
            MiniLMEncoder(self.root / "encoder", max_length=0)
        with self.assertRaises(ValueError):
            self.reranker.score(["alpha"], ["beta", "gamma"])
        with self.assertRaises(TypeError):
            self.reranker.score("alpha")

    def test_pair_segments_longest_first_and_query_broadcast(self):
        docs = ["beta", "gamma delta", "delta " * 20]
        a = self.reranker.score("alpha", docs, batch_size=1)
        b = self.reranker.score([("alpha", doc) for doc in docs], batch_size=3)
        np.testing.assert_array_equal(a, b)
        prepared = self.reranker.prepare([("alpha " * 10, "beta " * 10)], batch_size=1)
        batch = prepared.batches[0]
        self.assertEqual(batch.inputs["input_ids"].shape, (1, 8))
        self.assertTrue(np.any(batch.inputs["token_type_ids"] == 1))
        self.assertEqual(batch.inputs["input_ids"][0, 0], 2)
        self.assertEqual(batch.inputs["input_ids"][0, -1], 3)
        self.assertEqual(int(np.count_nonzero(batch.inputs["input_ids"] == 3)), 2)

    def test_prepared_task_mismatch_and_progress(self):
        prepared = self.encoder.prepare(["alpha", "beta", "gamma"], batch_size=2)
        with self.assertRaises(ValueError):
            self.reranker.predict_prepared(prepared)
        progress = []
        self.encoder.predict_prepared(prepared, progress=lambda done, total: progress.append((done, total)))
        self.assertEqual(progress, [(2, 3), (3, 3)])


@unittest.skipUnless(DEPENDENCIES and (LAB / "assets/models/minilm_encoder/model.onnx").is_file(),
                     "downloaded FP32 encoder required")
class RealEncoderTests(unittest.TestCase):
    def test_fp32_single_vs_batch_and_semantic_signal(self):
        model = MiniLMEncoder(LAB / "assets/models/minilm_encoder", max_length=256, num_threads=1)
        texts = ["A man is eating food.", "A person eats a meal.",
                 "The spacecraft travels through the galaxy.", "", "biology " * 300]
        single = model.encode(texts, batch_size=1, sort_by_length=False)
        batch = model.encode(texts, batch_size=4, sort_by_length=True)
        self.assertEqual(single.shape, (5, 384))
        np.testing.assert_allclose(single, batch, atol=3e-5, rtol=3e-4)
        np.testing.assert_allclose(np.linalg.norm(batch, axis=1), 1, atol=1e-6)
        self.assertGreater(float(batch[0] @ batch[1]), float(batch[0] @ batch[2]) + .3)


@unittest.skipUnless(DEPENDENCIES and (LAB / "assets/models/minilm_reranker/model.onnx").is_file(),
                     "downloaded FP32 reranker required")
class RealRerankerTests(unittest.TestCase):
    def test_fp32_single_vs_batch_and_published_example(self):
        model = MiniLMReranker(LAB / "assets/models/minilm_reranker", max_length=256, num_threads=1)
        query = "How many people live in Berlin?"
        docs = ["Berlin had a population of 3,520,031 registered inhabitants in an area of 891.82 square kilometers.",
                "Berlin is well known for its museums."]
        single = model.score(query, docs, batch_size=1, sort_by_length=False)
        batch = model.score(query, docs, batch_size=2, sort_by_length=True)
        np.testing.assert_allclose(single, batch, atol=3e-5, rtol=3e-4)
        # Published raw-logit example in the model author's Hugging Face card.
        np.testing.assert_allclose(batch, [8.607138, -4.320078], atol=.03, rtol=0)


if __name__ == "__main__":
    unittest.main()
