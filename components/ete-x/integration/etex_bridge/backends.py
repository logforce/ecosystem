"""One operator-selected backend per worker; requests cannot choose resources.

See component LICENSE.md.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
from pathlib import Path
import sys

from . import __version__
from .protocol import (MAX_DOCUMENT_ID_BYTES, MAX_SNIPPET_BYTES, MAX_TITLE_BYTES,
                       RESULT_SCHEMA, ProtocolError, validate_result)

REFERENCE = "fp32-products-fp64-eight-lanes-no-fma-v1"
DENSE_SCOPE = (
    "Exact dense top-k over this immutable snapshot's stored FP32 vectors under the "
    "eight-lane FP64 no-FMA scorer; conditional on verified source binding and its "
    "checked IEEE environment. This does not certify model quantization, relevance, "
    "or global cross-encoder ranking."
)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clip_text(value, maximum):
    if not isinstance(value, str):
        raise ProtocolError("invalid_backend_result")
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError:
        raise ProtocolError("invalid_backend_result") from None
    if len(encoded) <= maximum:
        return value, False
    return encoded[:maximum].decode("utf-8", "ignore"), True


def _row(record):
    if not isinstance(record, dict):
        raise ProtocolError("invalid_backend_result")
    document_id = record.get("id")
    if not isinstance(document_id, str) or not document_id or len(document_id.encode("utf-8")) > MAX_DOCUMENT_ID_BYTES:
        # IDs are never truncated: a shortened ID could address a different document.
        raise ProtocolError("invalid_backend_result")
    title, title_truncated = _clip_text(record.get("title"), MAX_TITLE_BYTES)
    snippet, snippet_truncated = _clip_text(record.get("text"), MAX_SNIPPET_BYTES)
    return {"document_id": document_id, "row_id": record.get("row_id"), "title": title,
            "snippet": snippet, "title_truncated": title_truncated,
            "snippet_truncated": snippet_truncated, "dense_score": record.get("dense_score"),
            "reranker_logit": record.get("reranker_logit")}


def _complete(request, engine, results, execution, provenance, timings_ms):
    result = {"schema": RESULT_SCHEMA, "request_id": request["request_id"], "status": "completed",
              "cog_status": 0, "engine": engine, "results": results, "error": None,
              "provenance": provenance, "execution": execution, "timings_ms": timings_ms}
    try:
        return validate_result(result)
    except (ValueError, TypeError, OverflowError, KeyError):
        raise ProtocolError("invalid_backend_result") from None


class FixtureBackend:
    """Fixed synthetic rows for transport tests. No model, AI, or relevance scoring."""
    name = "fixture"
    records = (
        {"id": "fixture-001", "title": "Transport fixture: first row",
         "text": "Synthetic fixed data. This fixture does not execute an AI model or search a corpus."},
        {"id": "fixture-002", "title": "Transport fixture: second row",
         "text": "Requests are validated, but query content does not influence these fixed fixture rows."},
        {"id": "fixture-003", "title": "Transport fixture: third row",
         "text": "Use the explicitly selected etex engine for real encoded retrieval and optional reranking."},
    )

    def search(self, request):
        if request["rerank"]:
            raise ProtocolError("unsupported_rerank")
        effective = min(request["candidate_count"], len(self.records))
        count = min(request["top_k"], effective)
        results = [_row({**self.records[i], "row_id": i, "dense_score": float(3 - i)}) for i in range(count)]
        execution = {"candidate_count_requested": request["candidate_count"],
                     "candidate_count_effective": effective, "top_k_requested": request["top_k"],
                     "returned_count": count, "rerank_requested": False, "rerank_performed": False,
                     "strategy": "transport_fixture", "index_layout": "fixture",
                     "dense_exactness": {"certified": False, "reference": None,
                        "scope": "Deterministic transport fixture only: no AI inference, real retrieval, or relevance claim.",
                        "source_verified": False, "excluded_count": None, "refined_count": None, "fallback": False},
                     "reranking_scope": "No reranker is present in the transport fixture."}
        provenance = {key: None for key in ("snapshot_sha256", "index_manifest_sha256", "source_sha256",
            "encoder_model_sha256", "encoder_tokenizer_sha256", "reranker_model_sha256", "reranker_tokenizer_sha256")}
        provenance.update(worker_version=__version__, fixture=True, native_backend=None,
                          model_quantization_note="No model is loaded by the transport fixture.")
        return _complete(request, self.name, results, execution, provenance, None)

    def close(self):
        pass


def packaged_root() -> Path:
    return Path(__file__).resolve().parents[2] / "runtime"


def resolve_ete_x_root(operator_path=None) -> Path:
    root = (Path(operator_path) if operator_path else packaged_root()).resolve(strict=True)
    package = root / "commercial_lab"
    if not (package / "rag.py").is_file() or not (package / "nonar" / "encoder.py").is_file():
        raise ValueError("The selected ETE-X root must contain the commercial_lab package")
    return root


class EteXBackend:
    """Persistent unchanged RagEngine, loaded and fully verified before readiness."""
    name = "etex"

    def __init__(self, args):
        self.engine = None
        self.encoder = None
        self.reranker = None
        root = resolve_ete_x_root(args.ete_x_root)
        sys.path.insert(0, str(root))
        rag = importlib.import_module("commercial_lab.rag")
        if Path(rag.__file__).resolve() != root / "commercial_lab" / "rag.py":
            raise RuntimeError("A different commercial_lab package was already imported")
        package = root / "commercial_lab"
        encoder_dir = Path(args.encoder_dir).resolve(strict=True) if args.encoder_dir else package / "assets/models/minilm_encoder"
        reranker_dir = Path(args.reranker_dir).resolve(strict=True) if args.reranker_dir else package / "assets/models/minilm_reranker"
        try:
            self.encoder = rag.MiniLMEncoder(encoder_dir, model_path=encoder_dir / args.model_file,
                                            num_threads=args.threads, max_length=256)
            if not args.no_reranker:
                self.reranker = rag.MiniLMReranker(reranker_dir, model_path=reranker_dir / args.reranker_model_file,
                                                  num_threads=args.threads, max_length=256)
            self.engine = rag.RagEngine(args.snapshot, self.encoder, self.reranker, strategy=args.strategy)
            self.index_stats = self.engine.index.stats()
            if self.index_stats.get("source_verified") is not True:
                raise RuntimeError("This bridge requires full startup source verification")
            provenance = self.engine.provenance
            self.provenance = {
                "snapshot_sha256": provenance["snapshot_sha256"],
                "index_manifest_sha256": _sha256(Path(args.snapshot) / "index/manifest.json"),
                "source_sha256": self.index_stats["source_sha256"],
                "encoder_model_sha256": provenance["encoder"]["model_sha256"],
                "encoder_tokenizer_sha256": provenance["encoder"]["tokenizer_sha256"],
                "reranker_model_sha256": provenance["reranker_model_sha256"],
                "reranker_tokenizer_sha256": provenance["reranker_tokenizer_sha256"],
                "worker_version": __version__, "fixture": False,
                "native_backend": self.index_stats["native_backend"],
                "model_quantization_note": "The bundled INT8 ONNX model can produce embeddings/logits different from its FP32 source model.",
            }
        except BaseException:
            self.close()
            raise

    def search(self, request):
        if request["rerank"] and self.reranker is None:
            raise ProtocolError("unsupported_rerank")
        raw = self.engine.search(request["query"], candidates=request["candidate_count"],
                                 limit=request["top_k"], rerank=request["rerank"])
        stats = raw["dense_search"]
        certificate = stats.get("certificate", {})
        if certificate.get("valid") is not True or certificate.get("reference") != REFERENCE:
            raise ProtocolError("invalid_backend_result")
        effective = min(request["candidate_count"], self.engine.manifest["rows"])
        results = [_row(record) for record in raw["results"]]
        execution = {
            "candidate_count_requested": request["candidate_count"], "candidate_count_effective": effective,
            "top_k_requested": request["top_k"], "returned_count": len(results),
            "rerank_requested": request["rerank"], "rerank_performed": raw["rerank_performed"],
            "strategy": raw["strategy"], "index_layout": self.engine.manifest.get("index_layout", "rowwise"),
            "dense_exactness": {"certified": True, "reference": REFERENCE, "scope": DENSE_SCOPE,
                "source_verified": True, "excluded_count": certificate.get("excluded_count"),
                "refined_count": stats.get("candidate_count"), "fallback": stats.get("fallback_reason") is not None},
            "reranking_scope": raw["reranking_scope"],
        }
        return _complete(request, self.name, results, execution, dict(self.provenance), raw["timings_ms"])

    def close(self):
        if self.engine is not None:
            try:
                self.engine.close()
            finally:
                self.engine = None
        # The canonical wrapper has no explicit ORT close API. Releasing the
        # owners here leaves session teardown to Python/ORT; process exit is final.
        self.encoder = self.reranker = None


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # CLI input is operator-only, but even here do not echo arbitrary values.
        self.exit(2, "ETEX_WORKER invalid_operator_configuration; consult --help\n")


def add_engine_arguments(parser):
    parser.add_argument("--engine", choices=("fixture", "etex"), required=True,
                        help="Explicit engine selection; fixture performs no AI inference")
    parser.add_argument("--snapshot", type=Path, help="Operator-selected immutable snapshot (required for etex)")
    parser.add_argument("--ete-x-root", type=Path, help="Parent of commercial_lab; defaults to the packaged English ETE-X tree")
    parser.add_argument("--encoder-dir", type=Path, help="Operator-selected local encoder directory")
    parser.add_argument("--reranker-dir", type=Path, help="Operator-selected local reranker directory")
    parser.add_argument("--model-file", default="model.fused.int8.full.onnx", help="Encoder model basename")
    parser.add_argument("--reranker-model-file", default="model.fused.int8.full.onnx", help="Reranker model basename")
    parser.add_argument("--threads", type=int, default=1, help="ORT intra-op threads, 1..64")
    parser.add_argument("--strategy", choices=("auto", "raw", "bounded"), default="auto")
    parser.add_argument("--no-reranker", action="store_true", help="Do not load a reranker; rerank requests then fail explicitly")


def validate_engine_arguments(args, parser):
    if not 1 <= args.threads <= 64:
        parser.error("Invalid thread count")
    for value in (args.model_file, args.reranker_model_file):
        if not value or value in (".", "..") or "/" in value or "\\" in value or "\0" in value or len(value) > 255:
            parser.error("Model names must be basenames")
    if args.engine == "etex" and args.snapshot is None:
        parser.error("An explicit snapshot is required")
    if args.engine == "fixture" and any(value is not None for value in
                                         (args.snapshot, args.ete_x_root, args.encoder_dir, args.reranker_dir)):
        parser.error("Fixture does not accept model or snapshot paths")


def backend_from_arguments(args):
    return FixtureBackend() if args.engine == "fixture" else EteXBackend(args)
