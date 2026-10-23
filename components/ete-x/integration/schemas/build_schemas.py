"""Rebuild the static JSON Schema 2020-12 companions with the standard library.

Runtime additionally checks UTF-8 byte limits and cross-field relationships.
See component LICENSE.md.
"""
import json
from pathlib import Path


def obj(properties):
    return {"type": "object", "additionalProperties": False,
            "required": list(properties), "properties": properties}


def integer(low, high):
    return {"type": "integer", "minimum": low, "maximum": high}


def text(maximum, **extra):
    return {"type": "string", "maxLength": maximum, "x-max-utf8-bytes": maximum, **extra}


def nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


def main():
    root = Path(__file__).resolve().parent
    request_id = text(64, pattern="^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
    request = obj({
        "schema": {"const": "etex.rag.search.request.v1"}, "request_id": request_id,
        "query": text(3072, minLength=1, description="Non-whitespace Unicode scalar text. UTF-8 byte cap is enforced by the worker; C0 controls except TAB/LF/CR are rejected."),
        "candidate_count": integer(1, 100), "top_k": integer(1, 20), "rerank": {"type": "boolean"},
    })
    request.update({"$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": "urn:etex:rag:search:request:v1", "title": "ETE-X search request v1",
                    "description": "Closed application request. No snapshot, path, tenant, corpus, engine, or runtime selection. Runtime also requires top_k <= candidate_count, strict UTF-8, unique JSON keys, finite numbers and a <=4092-byte encoded document."})
    row = obj({
        "document_id": text(512, minLength=1), "row_id": integer(0, (1 << 63) - 1),
        "title": text(256), "snippet": text(1536), "title_truncated": {"type": "boolean"},
        "snippet_truncated": {"type": "boolean"}, "dense_score": {"type": "number"},
        "reranker_logit": nullable({"type": "number"}),
    })
    exact = obj({
        "certified": {"type": "boolean"}, "reference": nullable(text(128, minLength=1)),
        "scope": text(600, minLength=1), "source_verified": {"type": "boolean"},
        "excluded_count": nullable(integer(0, (1 << 63) - 1)),
        "refined_count": nullable(integer(0, (1 << 63) - 1)), "fallback": {"type": "boolean"},
    })
    execution = obj({
        "candidate_count_requested": integer(1, 100), "candidate_count_effective": integer(1, 100),
        "top_k_requested": integer(1, 20), "returned_count": integer(1, 20),
        "rerank_requested": {"type": "boolean"}, "rerank_performed": {"type": "boolean"},
        "strategy": {"enum": ["raw", "bounded", "transport_fixture"]},
        "index_layout": {"enum": ["rowwise", "packed", "fixture"]},
        "dense_exactness": exact, "reranking_scope": text(320, minLength=1),
    })
    provenance_properties = {key: nullable({"type": "string", "pattern": "^[0-9a-f]{64}$"}) for key in (
        "snapshot_sha256", "index_manifest_sha256", "source_sha256", "encoder_model_sha256",
        "encoder_tokenizer_sha256", "reranker_model_sha256", "reranker_tokenizer_sha256")}
    provenance_properties.update({"worker_version": {"const": "1.0.0"}, "fixture": {"type": "boolean"},
                                  "native_backend": nullable(text(128, minLength=1)),
                                  "model_quantization_note": text(320, minLength=1)})
    provenance = obj(provenance_properties)
    errors = {
        "invalid_request": "Request failed validation.",
        "invalid_sequence": "Sequence must strictly increase within one worker.",
        "truncated_request": "Request frame ended before completion.",
        "unsupported_rerank": "Reranking is unavailable in this worker configuration.",
        "backend_failed": "The configured retrieval backend could not complete the request.",
        "invalid_backend_result": "The retrieval backend returned an invalid result.",
        "output_limit": "Result exceeds the fixed response capacity.",
    }
    error = {"oneOf": [obj({"code": {"const": code}, "message": {"const": message}})
                       for code, message in errors.items()]}
    timings = obj({key: {"type": "number", "minimum": 0} for key in
                   ("encoder", "dense_search", "documents_and_reranking", "total")})
    result = obj({
        "schema": {"const": "etex.rag.search.result.v1"}, "request_id": nullable(request_id),
        "status": {"enum": ["completed", "failed"]}, "cog_status": integer(0, 11),
        "engine": {"enum": ["fixture", "etex"]},
        "results": {"type": "array", "items": row, "maxItems": 20},
        "error": nullable(error), "provenance": nullable(provenance),
        "execution": nullable(execution), "timings_ms": nullable(timings),
    })
    result.update({"$schema": "https://json-schema.org/draft/2020-12/schema",
                   "$id": "urn:etex:rag:search:result:v1", "title": "ETE-X search result v1",
                   "description": "Finite JSON only. Runtime also checks error/status mapping, wire status equality, UTF-8 byte caps, row ID uniqueness, returned_count, candidate/top_k relationships, rerank/logit presence, fixture labeling and source-bound exactness. No generated answer or query echo.",
                   "allOf": [
                       {"if": {"properties": {"status": {"const": "completed"}}},
                        "then": {"properties": {"cog_status": {"const": 0}, "request_id": request_id,
                            "error": {"type": "null"}, "provenance": provenance, "execution": execution,
                            "results": {"minItems": 1}}},
                        "else": {"properties": {"cog_status": {"minimum": 1}, "error": error,
                            "results": {"maxItems": 0}, "provenance": {"type": "null"},
                            "execution": {"type": "null"}, "timings_ms": {"type": "null"}}}},
                       {"if": {"properties": {"engine": {"const": "fixture"}, "status": {"const": "completed"}}},
                        "then": {"properties": {"timings_ms": {"type": "null"},
                            "provenance": {"properties": {"fixture": {"const": True}}},
                            "execution": {"properties": {"strategy": {"const": "transport_fixture"},
                                "index_layout": {"const": "fixture"}, "rerank_performed": {"const": False},
                                "dense_exactness": {"properties": {"certified": {"const": False},
                                    "source_verified": {"const": False}, "reference": {"type": "null"}}}}}}}},
                       {"if": {"properties": {"engine": {"const": "etex"}, "status": {"const": "completed"}}},
                        "then": {"properties": {"timings_ms": timings,
                            "provenance": {"properties": {"fixture": {"const": False}}},
                            "execution": {"properties": {"strategy": {"enum": ["raw", "bounded"]},
                                "index_layout": {"enum": ["rowwise", "packed"]},
                                "dense_exactness": {"properties": {"certified": {"const": True},
                                    "source_verified": {"const": True},
                                    "reference": {"const": "fp32-products-fp64-eight-lanes-no-fma-v1"}}}}}}}},
                   ]})
    for name, value in (("search_request.schema.json", request), ("search_result.schema.json", result)):
        (root / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
