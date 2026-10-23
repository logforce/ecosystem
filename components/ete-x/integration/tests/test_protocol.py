"""Offline, standard-library protocol tests. See component LICENSE.md."""
import copy
import io
import json
from pathlib import Path
import struct
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from etex_bridge.backends import FixtureBackend, _clip_text
from etex_bridge.cogposix import TensorDescriptor, tensor_descriptor
from etex_bridge.protocol import *


def request(**changes):
    value = {"schema": REQUEST_SCHEMA, "request_id": "test-1", "query": "Find a local document.",
             "candidate_count": 3, "top_k": 2, "rerank": False}
    value.update(changes)
    return value


def raw_envelope(payload, size=REQUEST_ENVELOPE_BYTES):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return struct.pack("<I", len(payload)) + payload + bytes(size - 4 - len(payload))


class RequestTests(unittest.TestCase):
    def test_unicode_roundtrip_and_exact_size(self):
        value = request(query="Search: naïve café, 東京, 🧪\nsecond line")
        encoded = request_envelope(value)
        self.assertEqual(len(encoded), 4096)
        self.assertEqual(parse_request_envelope(encoded), value)
        self.assertEqual(len(encode_request(7, value)), 4104)

    def test_all_fields_required_and_unknown_fields_rejected(self):
        base = request()
        for key in base:
            with self.subTest(missing=key):
                value = dict(base)
                del value[key]
                with self.assertRaises(ProtocolError):
                    request_envelope(value)
        for key in ("snapshot", "path", "tenant", "corpus", "engine", "runtime", "threads", "strategy"):
            with self.subTest(forbidden=key), self.assertRaises(ProtocolError):
                request_envelope(request(**{key: "untrusted"}))

    def test_ranges_and_bool_integer_confusion(self):
        for changes in ({"candidate_count": 0}, {"candidate_count": 101}, {"candidate_count": True},
                        {"top_k": 21}, {"top_k": 0}, {"top_k": True}, {"candidate_count": 2, "top_k": 3},
                        {"top_k": 1.0}, {"rerank": 0}, {"rerank": "false"}):
            with self.subTest(changes=changes), self.assertRaises(ProtocolError):
                request_envelope(request(**changes))

    def test_query_and_identifier_limits(self):
        for query in ("", " \t\r\n", "x\0y", "x\x01y", "x" * 3073, "界" * 1025, "\ud800"):
            with self.subTest(length=len(query)), self.assertRaises(ProtocolError):
                request_envelope(request(query=query))
        self.assertEqual(parse_request_envelope(request_envelope(request(query="界" * 1024)))["query"], "界" * 1024)
        for rid in ("", "with space", "x" * 65, "é", "_first", "<script>", "a\n"):
            with self.subTest(rid=repr(rid)), self.assertRaises(ProtocolError):
                request_envelope(request(request_id=rid))

    def test_duplicate_keys_rejected_at_any_depth(self):
        for text in ('{"query":"first","query":"second"}', '{"a":{"x":1,"x":2}}'):
            with self.subTest(text=text), self.assertRaises(ProtocolError):
                decode_envelope(raw_envelope(text), 4096)

    def test_nan_infinity_and_float_overflow_rejected(self):
        for number in ("NaN", "Infinity", "-Infinity", "1e10000", "-1e99999"):
            with self.subTest(number=number), self.assertRaises(ProtocolError):
                decode_envelope(raw_envelope('{"value":' + number + '}'), 4096)

    def test_strict_utf8_bom_surrogates_and_trailing_json(self):
        for raw in (b'{"q":"\xff"}', b'\xef\xbb\xbf{}', b'{"q":"\\ud800"}', b'{}{}', b'{broken', b'[]', b'null'):
            with self.subTest(raw=raw), self.assertRaises(ProtocolError):
                decode_envelope(raw_envelope(raw), 4096)

    def test_wrong_size_count_and_nonzero_padding(self):
        good = request_envelope(request())
        cases = [good[:-1], good + b"\0", bytes(4096), struct.pack("<I", 4093) + good[4:],
                 struct.pack("<I", 0xFFFFFFFF) + good[4:], good[:-1] + b"\1"]
        for blob in cases:
            with self.subTest(length=len(blob)), self.assertRaises(ProtocolError):
                parse_request_envelope(blob)

    def test_excessive_nesting_is_controlled(self):
        nested = '{"a":' * 500 + '0' + '}' * 500
        with self.assertRaises(ProtocolError):
            decode_envelope(raw_envelope(nested), 4096)

    def test_unsigned_sequence_range(self):
        for seq in (-1, 1 << 64, True, 1.0):
            with self.subTest(seq=seq), self.assertRaises(ProtocolError):
                encode_request(seq, request())
        self.assertEqual(struct.unpack_from("<Q", encode_request(UINT64_MAX, request()))[0], UINT64_MAX)


class ResponseTests(unittest.TestCase):
    def setUp(self):
        self.result = FixtureBackend().search(request())

    def test_success_and_failure_roundtrip(self):
        for result in (self.result, failure("invalid_request", "fixture"),
                       failure("unsupported_rerank", "fixture", "test-1")):
            with self.subTest(status=result["cog_status"]):
                frame = encode_response(91, result)
                self.assertEqual(len(frame), 65552)
                self.assertEqual(decode_response(frame, 91), result)

    def test_marker_sequence_wire_status_and_padding_checked(self):
        good = encode_response(91, self.result)
        cases = [b"BAD!" + good[4:], good[:4] + struct.pack("<Q", 92) + good[12:],
                 good[:12] + struct.pack("<I", 8) + good[16:], good[:-1] + b"\1", good + b"\0", good[:-1]]
        for blob in cases:
            with self.subTest(length=len(blob)), self.assertRaises(ProtocolError):
                decode_response(blob, 91)

    def test_nonfinite_scores_and_timings_not_accepted(self):
        for number in (float("nan"), float("inf"), -float("inf"), 10 ** 1000):
            value = copy.deepcopy(self.result)
            value["results"][0]["dense_score"] = number
            with self.subTest(number=number), self.assertRaises(ProtocolError):
                result_envelope(value)

    def test_fixture_cannot_claim_real_inference_or_certificate(self):
        cases = []
        for mutate in (lambda x: x.update(engine="etex"),
                       lambda x: x["execution"]["dense_exactness"].update(certified=True),
                       lambda x: x["provenance"].update(fixture=False),
                       lambda x: x["provenance"].update(snapshot_sha256="a" * 64),
                       lambda x: x["execution"].update(strategy="bounded")):
            value = copy.deepcopy(self.result)
            mutate(value)
            cases.append(value)
        for value in cases:
            with self.assertRaises(ProtocolError):
                result_envelope(value)

    def test_result_integrity_count_duplicate_ids_rerank(self):
        for mutate in (lambda x: x["results"][1].update(row_id=0),
                       lambda x: x["execution"].update(returned_count=1),
                       lambda x: x["execution"].update(rerank_requested=True),
                       lambda x: x["results"][0].update(reranker_logit=0.5),
                       lambda x: x["results"][0].update(extra="forbidden"),
                       lambda x: x["results"][0].update(document_id="x" * 513)):
            value = copy.deepcopy(self.result)
            mutate(value)
            with self.assertRaises(ProtocolError):
                result_envelope(value)

    def test_failure_status_and_fixed_message_must_agree(self):
        for mutate in (lambda x: x.update(cog_status=0), lambda x: x.update(status="completed"),
                       lambda x: x["error"].update(message="Sensitive query"),
                       lambda x: x.update(cog_status=8), lambda x: x.update(results=self.result["results"])):
            value = failure("invalid_request", "fixture")
            mutate(value)
            with self.assertRaises(ProtocolError):
                result_envelope(value)

    def test_oversized_output_is_a_nonzero_error(self):
        value = copy.deepcopy(self.result)
        value["execution"].update(candidate_count_requested=100, candidate_count_effective=100,
                                  top_k_requested=20, returned_count=20)
        value["results"] = [{**self.result["results"][0], "row_id": i, "snippet": "\x01" * 1536} for i in range(20)]
        with self.assertRaises(ProtocolError) as caught:
            result_envelope(value)
        self.assertEqual(caught.exception.status, CogStatus.NO_MEMORY)

    def test_utf8_clipping_keeps_valid_prefix_and_declares_loss(self):
        self.assertEqual(_clip_text("hello", 10), ("hello", False))
        self.assertEqual(_clip_text("東京🧪", 7), ("東京", True))
        with self.assertRaises(ProtocolError):
            _clip_text("\udfff", 20)


class StreamAndAbiTests(unittest.TestCase):
    def test_read_exact_handles_fragmentation_and_eof(self):
        class Fragmented(io.BytesIO):
            def read(self, n=-1):
                return super().read(min(n, 3))
        self.assertEqual(read_exact(Fragmented(b"abcdefgh"), 8), b"abcdefgh")
        self.assertIsNone(read_exact(io.BytesIO(), 8, clean_eof=True))
        with self.assertRaises(TruncatedFrame):
            read_exact(io.BytesIO(b"a"), 8, clean_eof=True)

    def test_ctypes_descriptor_matches_reviewed_64bit_header(self):
        import ctypes
        self.assertEqual(ctypes.sizeof(TensorDescriptor), 88)
        desc = tensor_descriptor(4096)
        self.assertEqual((desc.struct_size, desc.dtype, desc.rank, desc.flags, desc.offset), (88, 1, 1, 0, 0))
        self.assertEqual(list(desc.shape), [4096] + [0] * 7)
        with self.assertRaises(ValueError):
            tensor_descriptor(4104)  # process frame bytes are NOT a Cog tensor shape


if __name__ == "__main__":
    unittest.main()
