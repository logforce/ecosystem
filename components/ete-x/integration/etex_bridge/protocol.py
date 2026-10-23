"""Strict fixed-size transport, independent of NumPy, ONNX, and ETE-X.

See component LICENSE.md.
"""
from __future__ import annotations

import enum
import json
import math
import re
import struct
from typing import BinaryIO

READY = b"ETEW1"
RESPONSE_MAGIC = b"ETX1"
REQUEST_ENVELOPE_BYTES = 4096
RESPONSE_ENVELOPE_BYTES = 65536
REQUEST_FRAME_BYTES = 8 + REQUEST_ENVELOPE_BYTES
RESPONSE_FRAME_BYTES = 16 + RESPONSE_ENVELOPE_BYTES
REQUEST_SCHEMA = "etex.rag.search.request.v1"
RESULT_SCHEMA = "etex.rag.search.result.v1"
MAX_QUERY_BYTES = 3072
MAX_REQUEST_ID_BYTES = 64
MAX_DOCUMENT_ID_BYTES = 512
MAX_TITLE_BYTES = 256
MAX_SNIPPET_BYTES = 1536
UINT64_MAX = (1 << 64) - 1


class CogStatus(enum.IntEnum):
    """Values copied from the reviewed CogPOSIX 0.2 C header, not errno."""
    OK = 0
    INVALID = 1
    NO_MEMORY = 2
    NO_MODEL = 3
    BUSY = 4
    TIMEOUT = 5
    CANCELLED = 6
    PERMISSION = 7
    BACKEND = 8
    IO = 9
    UNSUPPORTED = 10
    DISCONNECTED = 11


_ERRORS = {
    "invalid_request": (CogStatus.INVALID, "Request failed validation."),
    "invalid_sequence": (CogStatus.INVALID, "Sequence must strictly increase within one worker."),
    "truncated_request": (CogStatus.IO, "Request frame ended before completion."),
    "unsupported_rerank": (CogStatus.UNSUPPORTED, "Reranking is unavailable in this worker configuration."),
    "backend_failed": (CogStatus.BACKEND, "The configured retrieval backend could not complete the request."),
    "invalid_backend_result": (CogStatus.BACKEND, "The retrieval backend returned an invalid result."),
    "output_limit": (CogStatus.NO_MEMORY, "Result exceeds the fixed response capacity."),
}


class ProtocolError(ValueError):
    """A fixed public error. Never retains a request, query, path, or exception text."""
    def __init__(self, code: str = "invalid_request"):
        if code not in _ERRORS:
            raise ValueError("Unknown bridge error code")
        self.code = code
        self.status, self.public_message = _ERRORS[code]
        super().__init__(self.public_message)


class TruncatedFrame(EOFError):
    pass


def _object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ProtocolError()
        obj[key] = value
    return obj


def _bad_constant(_value):
    raise ProtocolError()


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ProtocolError()
    return result


def _text(value, maximum: int, *, nonempty: bool = False):
    if not isinstance(value, str):
        raise ProtocolError()
    try:
        length = len(value.encode("utf-8", "strict"))
    except UnicodeError:
        raise ProtocolError() from None
    if length > maximum or (nonempty and not value.strip()):
        raise ProtocolError()
    return value


def _integer(value, minimum: int, maximum: int):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProtocolError()
    return value


def _number(value):
    if type(value) not in (int, float):
        raise ProtocolError()
    try:
        if not math.isfinite(value):
            raise ProtocolError()
    except OverflowError:
        raise ProtocolError() from None
    return value


def _exact_keys(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ProtocolError()


def _scalar_text_tree(value, depth=0):
    """Reject escaped unpaired surrogates and excessive nesting after JSON decoding."""
    if depth > 12:
        raise ProtocolError()
    if isinstance(value, str):
        _text(value, RESPONSE_ENVELOPE_BYTES)
    elif type(value) is dict:
        for key, item in value.items():
            _text(key, 256)
            _scalar_text_tree(item, depth + 1)
    elif type(value) is list:
        for item in value:
            _scalar_text_tree(item, depth + 1)
    elif type(value) is float and not math.isfinite(value):
        raise ProtocolError()


def decode_envelope(data: bytes, size: int) -> dict:
    if len(data) != size or size not in (REQUEST_ENVELOPE_BYTES, RESPONSE_ENVELOPE_BYTES):
        raise ProtocolError()
    length = struct.unpack_from("<I", data)[0]
    if not 1 <= length <= size - 4 or any(data[4 + length:]):
        raise ProtocolError()
    try:
        text = data[4:4 + length].decode("utf-8", "strict")
        result = json.loads(text, object_pairs_hook=_object, parse_constant=_bad_constant,
                            parse_float=_finite_float)
        if type(result) is not dict:
            raise ProtocolError()
        _scalar_text_tree(result)
        return result
    except (UnicodeError, ValueError, OverflowError, RecursionError):
        raise ProtocolError() from None


def encode_envelope(value: dict, size: int) -> bytes:
    if size not in (REQUEST_ENVELOPE_BYTES, RESPONSE_ENVELOPE_BYTES):
        raise ProtocolError()
    try:
        _scalar_text_tree(value)
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             sort_keys=True, separators=(",", ":")).encode("utf-8", "strict")
    except (UnicodeError, TypeError, ValueError, OverflowError, RecursionError):
        raise ProtocolError() from None
    if len(payload) > size - 4:
        raise ProtocolError("output_limit" if size == RESPONSE_ENVELOPE_BYTES else "invalid_request")
    return struct.pack("<I", len(payload)) + payload + bytes(size - 4 - len(payload))


def validate_request(value: dict) -> dict:
    _exact_keys(value, ("schema", "request_id", "query", "candidate_count", "top_k", "rerank"))
    if value["schema"] != REQUEST_SCHEMA:
        raise ProtocolError()
    request_id = _text(value["request_id"], MAX_REQUEST_ID_BYTES, nonempty=True)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", request_id, flags=re.ASCII) is None:
        raise ProtocolError()
    query = _text(value["query"], MAX_QUERY_BYTES, nonempty=True)
    if any(ord(c) < 32 and c not in "\t\n\r" for c in query):
        raise ProtocolError()
    count = _integer(value["candidate_count"], 1, 100)
    _integer(value["top_k"], 1, min(20, count))
    if type(value["rerank"]) is not bool:
        raise ProtocolError()
    return value


def request_envelope(value: dict) -> bytes:
    return encode_envelope(validate_request(value), REQUEST_ENVELOPE_BYTES)


def parse_request_envelope(data: bytes) -> dict:
    return validate_request(decode_envelope(data, REQUEST_ENVELOPE_BYTES))


def failure(code: str, engine: str, request_id: str | None = None) -> dict:
    error = ProtocolError(code)
    return {"schema": RESULT_SCHEMA, "request_id": request_id, "status": "failed",
            "cog_status": int(error.status), "engine": engine, "results": [],
            "error": {"code": error.code, "message": error.public_message},
            "provenance": None, "execution": None, "timings_ms": None}


def _hash_or_none(value):
    if value is not None and (not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None):
        raise ProtocolError()


def validate_result(value: dict, expected_status: int | None = None) -> dict:
    _exact_keys(value, ("schema", "request_id", "status", "cog_status", "engine", "results",
                        "error", "provenance", "execution", "timings_ms"))
    if value["schema"] != RESULT_SCHEMA or value["engine"] not in ("fixture", "etex"):
        raise ProtocolError()
    if value["request_id"] is not None:
        _text(value["request_id"], MAX_REQUEST_ID_BYTES, nonempty=True)
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}", value["request_id"], flags=re.ASCII) is None:
            raise ProtocolError()
    status = _integer(value["cog_status"], 0, 11)
    if expected_status is not None and status != expected_status:
        raise ProtocolError()
    if type(value["results"]) is not list or len(value["results"]) > 20:
        raise ProtocolError()
    if status:
        if (value["status"] != "failed" or value["results"] or value["provenance"] is not None
                or value["execution"] is not None or value["timings_ms"] is not None):
            raise ProtocolError()
        _exact_keys(value["error"], ("code", "message"))
        code = value["error"]["code"]
        if not isinstance(code, str) or code not in _ERRORS:
            raise ProtocolError()
        expected = ProtocolError(code)
        if int(expected.status) != status or value["error"]["message"] != expected.public_message:
            raise ProtocolError()
        return value
    if value["status"] != "completed" or value["error"] is not None or value["request_id"] is None:
        raise ProtocolError()
    seen = set()
    for row in value["results"]:
        _exact_keys(row, ("document_id", "row_id", "title", "snippet", "title_truncated",
                          "snippet_truncated", "dense_score", "reranker_logit"))
        _text(row["document_id"], MAX_DOCUMENT_ID_BYTES, nonempty=True)
        rid = _integer(row["row_id"], 0, (1 << 63) - 1)
        if rid in seen:
            raise ProtocolError()
        seen.add(rid)
        _text(row["title"], MAX_TITLE_BYTES)
        _text(row["snippet"], MAX_SNIPPET_BYTES)
        if type(row["title_truncated"]) is not bool or type(row["snippet_truncated"]) is not bool:
            raise ProtocolError()
        _number(row["dense_score"])
        if row["reranker_logit"] is not None:
            _number(row["reranker_logit"])
    execution = value["execution"]
    _exact_keys(execution, ("candidate_count_requested", "candidate_count_effective", "top_k_requested",
                           "returned_count", "rerank_requested", "rerank_performed", "strategy",
                           "index_layout", "dense_exactness", "reranking_scope"))
    count = _integer(execution["candidate_count_requested"], 1, 100)
    effective = _integer(execution["candidate_count_effective"], 1, count)
    top_k = _integer(execution["top_k_requested"], 1, min(20, count))
    if _integer(execution["returned_count"], 1, min(effective, top_k)) != len(value["results"]):
        raise ProtocolError()
    if len(value["results"]) != min(effective, top_k):
        raise ProtocolError()
    for key in ("rerank_requested", "rerank_performed"):
        if type(execution[key]) is not bool:
            raise ProtocolError()
    if execution["rerank_requested"] != execution["rerank_performed"]:
        raise ProtocolError()
    if any((row["reranker_logit"] is not None) != execution["rerank_performed"] for row in value["results"]):
        raise ProtocolError()
    if execution["strategy"] not in ("raw", "bounded", "transport_fixture"):
        raise ProtocolError()
    if execution["index_layout"] not in ("rowwise", "packed", "fixture"):
        raise ProtocolError()
    _text(execution["reranking_scope"], 320, nonempty=True)
    exact = execution["dense_exactness"]
    _exact_keys(exact, ("certified", "reference", "scope", "source_verified", "excluded_count",
                       "refined_count", "fallback"))
    for key in ("certified", "source_verified", "fallback"):
        if type(exact[key]) is not bool:
            raise ProtocolError()
    _text(exact["scope"], 600, nonempty=True)
    if exact["reference"] is not None:
        _text(exact["reference"], 128, nonempty=True)
    for key in ("excluded_count", "refined_count"):
        if exact[key] is not None:
            _integer(exact[key], 0, (1 << 63) - 1)
    provenance = value["provenance"]
    hash_keys = ("snapshot_sha256", "index_manifest_sha256", "source_sha256", "encoder_model_sha256",
                 "encoder_tokenizer_sha256", "reranker_model_sha256", "reranker_tokenizer_sha256")
    _exact_keys(provenance, (*hash_keys, "worker_version", "fixture", "native_backend", "model_quantization_note"))
    for key in hash_keys:
        _hash_or_none(provenance[key])
    if provenance["worker_version"] != "1.0.0" or type(provenance["fixture"]) is not bool:
        raise ProtocolError()
    if provenance["native_backend"] is not None:
        _text(provenance["native_backend"], 128, nonempty=True)
    _text(provenance["model_quantization_note"], 320, nonempty=True)
    if value["engine"] == "fixture":
        if (not provenance["fixture"] or exact["certified"] or exact["source_verified"]
                or execution["strategy"] != "transport_fixture" or execution["index_layout"] != "fixture"
                or exact["reference"] is not None or any(provenance[key] is not None for key in hash_keys)
                or value["timings_ms"] is not None or execution["rerank_performed"]):
            raise ProtocolError()
    else:
        if (provenance["fixture"] or not exact["certified"] or not exact["source_verified"]
                or exact["reference"] != "fp32-products-fp64-eight-lanes-no-fma-v1"
                or execution["strategy"] not in ("raw", "bounded") or execution["index_layout"] not in ("rowwise", "packed")
                or any(provenance[key] is None for key in hash_keys[:5])):
            raise ProtocolError()
        _exact_keys(value["timings_ms"], ("encoder", "dense_search", "documents_and_reranking", "total"))
        for number in value["timings_ms"].values():
            if _number(number) < 0:
                raise ProtocolError()
    return value


def result_envelope(value: dict) -> bytes:
    return encode_envelope(validate_result(value), RESPONSE_ENVELOPE_BYTES)


def parse_result_envelope(data: bytes, expected_status: int | None = None) -> dict:
    return validate_result(decode_envelope(data, RESPONSE_ENVELOPE_BYTES), expected_status)


def encode_request(sequence: int, value: dict) -> bytes:
    _integer(sequence, 0, UINT64_MAX)
    return struct.pack("<Q", sequence) + request_envelope(value)


def encode_response(sequence: int, value: dict) -> bytes:
    _integer(sequence, 0, UINT64_MAX)
    envelope = result_envelope(value)
    return RESPONSE_MAGIC + struct.pack("<QI", sequence, value["cog_status"]) + envelope


def decode_response(frame: bytes, expected_sequence: int) -> dict:
    if len(frame) != RESPONSE_FRAME_BYTES or frame[:4] != RESPONSE_MAGIC:
        raise ProtocolError()
    sequence, status = struct.unpack_from("<QI", frame, 4)
    _integer(expected_sequence, 0, UINT64_MAX)
    if sequence != expected_sequence:
        raise ProtocolError()
    return parse_result_envelope(frame[16:], status)


def read_exact(stream: BinaryIO, size: int, *, clean_eof: bool = False) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < size:
        piece = stream.read(size - len(chunks))
        if not piece:
            if not chunks and clean_eof:
                return None
            raise TruncatedFrame("Incomplete fixed-size frame")
        chunks.extend(piece)
    return bytes(chunks)
