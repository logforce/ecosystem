"""Persistence, malformed input, and no-query-leak tests; standard library only.

See component LICENSE.md.
"""
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import unittest

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
from etex_bridge.backends import FixtureBackend
from etex_bridge.protocol import *
from etex_bridge.worker import serve


def request(**changes):
    value = {"schema": REQUEST_SCHEMA, "request_id": "worker-1", "query": "sensitive-query-token",
             "candidate_count": 3, "top_k": 2, "rerank": False}
    value.update(changes)
    return value


def results(data, sequences):
    if data[:len(READY)] != READY:
        raise AssertionError("Missing readiness")
    body = data[len(READY):]
    if len(body) != RESPONSE_FRAME_BYTES * len(sequences):
        raise AssertionError("Unexpected response length")
    return [decode_response(body[i * RESPONSE_FRAME_BYTES:(i + 1) * RESPONSE_FRAME_BYTES], seq)
            for i, seq in enumerate(sequences)]


def run_in_memory(payload, backend=None):
    output = io.BytesIO()
    code = serve(backend or FixtureBackend(), io.BytesIO(payload), output)
    return code, output.getvalue()


class InMemoryWorkerTests(unittest.TestCase):
    def test_two_queries_one_worker_and_fixture_ignores_query(self):
        one = request()
        two = request(request_id="worker-2", query="completely different content")
        code, output = run_in_memory(encode_request(1, one) + encode_request(2, two))
        first, second = results(output, [1, 2])
        self.assertEqual(code, 0)
        self.assertEqual(first["results"], second["results"])
        self.assertEqual(second["request_id"], "worker-2")
        self.assertNotIn(b"sensitive-query-token", output)
        self.assertFalse(first["execution"]["dense_exactness"]["certified"])

    def test_duplicate_and_decreasing_sequences_rejected_then_recover(self):
        sequences = [4, 4, 3, 5]
        code, output = run_in_memory(b"".join(encode_request(seq, request()) for seq in sequences))
        values = results(output, sequences)
        self.assertEqual([v["cog_status"] for v in values], [0, 1, 1, 0])
        self.assertEqual(values[1]["error"]["code"], "invalid_sequence")
        self.assertEqual(code, 0)

    def test_zero_first_sequence_and_no_wrap(self):
        code, output = run_in_memory(encode_request(0, request()) + encode_request(UINT64_MAX, request()) + encode_request(0, request()))
        self.assertEqual([v["cog_status"] for v in results(output, [0, UINT64_MAX, 0])], [0, 0, 1])

    def test_complete_bad_envelope_consumes_sequence_without_desynchronizing(self):
        corrupt = struct.pack("<Q", 8) + bytes(REQUEST_ENVELOPE_BYTES)
        code, output = run_in_memory(corrupt + encode_request(8, request()) + encode_request(9, request()))
        values = results(output, [8, 8, 9])
        self.assertEqual([v["cog_status"] for v in values], [1, 1, 0])
        self.assertEqual(values[0]["request_id"], None)

    def test_truncated_payload_returns_matching_io_failure_and_exits(self):
        for length in (0, 1, 4095):
            with self.subTest(length=length):
                code, output = run_in_memory(struct.pack("<Q", 99) + b"\0" * length)
                value = results(output, [99])[0]
                self.assertEqual(code, 2)
                self.assertEqual(value["cog_status"], 9)
                self.assertEqual(value["error"]["code"], "truncated_request")

    def test_partial_sequence_never_invents_matching_response(self):
        for length in range(1, 8):
            with self.subTest(length=length):
                code, output = run_in_memory(b"\0" * length)
                self.assertEqual((code, output), (2, READY))
        self.assertEqual(run_in_memory(b""), (0, READY))

    def test_reranking_unavailable_is_failed_job_not_silent_skip(self):
        code, output = run_in_memory(encode_request(1, request(rerank=True)))
        value = results(output, [1])[0]
        self.assertEqual(value["cog_status"], 10)
        self.assertEqual(value["request_id"], "worker-1")
        self.assertEqual(value["results"], [])

    def test_exception_message_never_echoed_and_next_request_can_succeed(self):
        class BrokenOnce(FixtureBackend):
            calls = 0
            def search(self, req):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError(req["query"] + " private/file/path")
                return super().search(req)
        backend = BrokenOnce()
        code, output = run_in_memory(encode_request(1, request()) + encode_request(2, request()), backend)
        values = results(output, [1, 2])
        self.assertEqual([v["cog_status"] for v in values], [8, 0])
        self.assertNotIn(b"sensitive-query-token", output)
        self.assertNotIn(b"private/file/path", output)
        self.assertEqual(backend.calls, 2)

    def test_invalid_backend_scores_are_failed_jobs(self):
        class Bad(FixtureBackend):
            def search(self, req):
                result = super().search(req)
                result["results"][0]["dense_score"] = float("nan")
                return result
        code, output = run_in_memory(encode_request(1, request()), Bad())
        value = results(output, [1])[0]
        self.assertEqual(value["cog_status"], 8)
        self.assertEqual(value["error"]["code"], "invalid_backend_result")

    def test_backend_cannot_change_request_identity(self):
        class Bad(FixtureBackend):
            def search(self, req):
                result = super().search(req)
                result["request_id"] = "different"
                return result
        code, output = run_in_memory(encode_request(1, request()), Bad())
        self.assertEqual(results(output, [1])[0]["cog_status"], 8)

    def test_fragmented_writes_are_completed(self):
        class SmallWrites(io.BytesIO):
            def write(self, value):
                return super().write(value[:7])
        output = SmallWrites()
        code = serve(FixtureBackend(), io.BytesIO(encode_request(1, request())), output)
        self.assertEqual(results(output.getvalue(), [1])[0]["cog_status"], 0)


class ProcessWorkerTests(unittest.TestCase):
    def launch(self, args, payload=b""):
        return subprocess.run([sys.executable, "-I", "-B", "-u", str(HERE / "worker.py"), *args],
                              input=payload, capture_output=True, timeout=15, cwd="/")

    def test_isolated_mode_persistent_process(self):
        run = self.launch(["--engine", "fixture"], encode_request(1, request()) + encode_request(2, request(request_id="two")))
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual([v["cog_status"] for v in results(run.stdout, [1, 2])], [0, 0])
        self.assertEqual(run.stderr, b"")

    def test_engine_must_be_explicit_and_no_ready_on_configuration_error(self):
        for args in ([], ["--engine", "etex"], ["--engine", "fixture", "--snapshot", "secret"],
                     ["--engine", "fixture", "--model-file", "../secret"], ["--engine", "fixture", "--threads", "0"]):
            with self.subTest(args=args):
                run = self.launch(args)
                self.assertEqual(run.returncode, 2)
                self.assertEqual(run.stdout, b"")
                self.assertNotIn(b"secret", run.stderr)
                self.assertLess(len(run.stderr), 150)

    def test_bad_payload_is_nonzero_status_not_process_traceback(self):
        payload = b'{"query":"sensitive-query-token","query":"duplicate"}'
        envelope = struct.pack("<I", len(payload)) + payload + bytes(4092 - len(payload))
        run = self.launch(["--engine", "fixture"], struct.pack("<Q", 4) + envelope)
        self.assertEqual(results(run.stdout, [4])[0]["cog_status"], 1)
        self.assertNotIn(b"sensitive-query-token", run.stderr + run.stdout)
        self.assertEqual(run.stderr, b"")

    def test_native_and_python_diagnostics_cannot_corrupt_or_leak(self):
        # Inject a noisy trusted backend factory, not user code. The worker's OS
        # fd policy must suppress Python output and raw C-style descriptor writes.
        code = """
import os,sys
sys.path.insert(0, %r)
from etex_bridge import worker
from etex_bridge.backends import FixtureBackend
def noisy(args):
    print('sensitive-query-token', flush=True)
    os.write(1,b'sensitive-query-token')
    os.write(2,b'sensitive-query-token')
    return FixtureBackend()
worker.backend_from_arguments=noisy
raise SystemExit(worker.main(['--engine','fixture']))
""" % str(HERE)
        run = subprocess.run([sys.executable, "-I", "-B", "-c", code], input=encode_request(1, request()),
                              capture_output=True, timeout=15, cwd="/")
        self.assertEqual(run.returncode, 0)
        self.assertEqual(results(run.stdout, [1])[0]["cog_status"], 0)
        self.assertNotIn(b"sensitive-query-token", run.stdout + run.stderr)

    def test_startup_exception_sanitized_and_not_ready(self):
        code = """
import sys
sys.path.insert(0, %r)
from etex_bridge import worker
def broken(args):
    raise RuntimeError('sensitive-query-token private/file/path')
worker.backend_from_arguments=broken
raise SystemExit(worker.main(['--engine','fixture']))
""" % str(HERE)
        run = subprocess.run([sys.executable, "-I", "-B", "-c", code], input=b"", capture_output=True, timeout=15, cwd="/")
        self.assertEqual(run.returncode, 1)
        self.assertEqual(run.stdout, b"")
        self.assertEqual(run.stderr, b"ETEX_WORKER startup_failed\n")


if __name__ == "__main__":
    unittest.main()
