"""Synchronous persistent worker. The external supervisor owns deadlines/cancel.

See component LICENSE.md.
"""
from __future__ import annotations

import os
import struct
import sys

from .backends import (SafeArgumentParser, add_engine_arguments, backend_from_arguments,
                       validate_engine_arguments)
from .protocol import (READY, REQUEST_ENVELOPE_BYTES, ProtocolError, TruncatedFrame,
                       encode_response, failure, parse_request_envelope, read_exact,
                       validate_result)


def _write(stream, data):
    remaining = memoryview(data)
    while remaining:
        written = stream.write(remaining)
        if written is None or written <= 0:
            raise BrokenPipeError("Protocol output is unavailable")
        remaining = remaining[written:]
    stream.flush()


def serve(backend, reader, writer):
    """Serve complete frames in order. No query text is emitted in failure paths."""
    _write(writer, READY)
    last_sequence = None
    while True:
        try:
            header = read_exact(reader, 8, clean_eof=True)
        except TruncatedFrame:
            # A partial sequence cannot be safely matched to any caller.
            return 2
        if header is None:
            return 0
        sequence = struct.unpack("<Q", header)[0]
        try:
            envelope = read_exact(reader, REQUEST_ENVELOPE_BYTES)
        except TruncatedFrame:
            _write(writer, encode_response(sequence, failure("truncated_request", backend.name)))
            return 2
        request = None
        try:
            if last_sequence is not None and sequence <= last_sequence:
                raise ProtocolError("invalid_sequence")
            # A complete frame with an increasing sequence consumes that sequence
            # even if the JSON or engine request subsequently fails.
            last_sequence = sequence
            request = parse_request_envelope(envelope)
            result = backend.search(request)
            try:
                validate_result(result)
                if (result["request_id"] != request["request_id"] or result["engine"] != backend.name
                        or result["status"] != "completed"):
                    raise ProtocolError("invalid_backend_result")
            except (ValueError, TypeError, OverflowError, KeyError):
                raise ProtocolError("invalid_backend_result") from None
            frame = encode_response(sequence, result)
        except ProtocolError as error:
            frame = encode_response(sequence, failure(error.code, backend.name,
                                     request["request_id"] if request is not None else None))
        except Exception:
            frame = encode_response(sequence, failure("backend_failed", backend.name,
                                     request["request_id"] if request is not None else None))
        _write(writer, frame)


class _SafeLog:
    """At most eight fixed short lines, through a saved stderr descriptor."""
    ALLOWED = {"startup_failed", "transport_failed", "worker_stopped", "close_failed"}

    def __init__(self, stream):
        self.stream = stream
        self.lines = 0

    def emit(self, code):
        if code not in self.ALLOWED or self.lines >= 8:
            return
        self.lines += 1
        try:
            self.stream.write(("ETEX_WORKER " + code + "\n").encode("ascii"))
            self.stream.flush()
        except OSError:
            pass


def main(argv=None):
    parser = SafeArgumentParser(description="Persistent framed ETE-X evaluation worker; not a hostile-code sandbox")
    add_engine_arguments(parser)
    args = parser.parse_args(argv)
    validate_engine_arguments(args, parser)
    sys.dont_write_bytecode = True
    # Preserve dedicated protocol/log channels, then suppress both Python and
    # native-library output on fd 1/2. Runtime diagnostics cannot corrupt frames
    # or disclose query text. Fixed bridge diagnostics use the saved log fd.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    logs = os.fdopen(os.dup(sys.stderr.fileno()), "wb", buffering=0)
    safe_log = _SafeLog(logs)
    with open(os.devnull, "wb", buffering=0) as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    backend = None
    exit_code = 1
    try:
        try:
            backend = backend_from_arguments(args)
        except Exception:
            safe_log.emit("startup_failed")
            return 1
        try:
            exit_code = serve(backend, sys.stdin.buffer, protocol)
        except (OSError, BrokenPipeError):
            safe_log.emit("transport_failed")
            exit_code = 3
        if exit_code:
            safe_log.emit("worker_stopped")
        return exit_code
    finally:
        if backend is not None:
            try:
                backend.close()
            except Exception:
                safe_log.emit("close_failed")
        protocol.close()
        logs.close()


if __name__ == "__main__":
    raise SystemExit(main())
