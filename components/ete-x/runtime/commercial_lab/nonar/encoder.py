"""Small, explicit ONNX Runtime text inference without PyTorch.

Models and tokenizers are local files. No download, environment mutation, or
model quantization happens implicitly. Each call restores the original input
order after optional length sorting. Sentence embeddings follow the published
all-MiniLM-L6-v2 attention-mask mean-pooling and L2-normalization recipe.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time
from typing import Callable, Iterable, Sequence

import numpy as np


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _positive_integer(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _texts(texts: str | Iterable[str]) -> list[str]:
    values = [texts] if isinstance(texts, str) else list(texts)
    if any(not isinstance(value, str) for value in values):
        raise TypeError("every input text must be a string")
    return values


def _pairs(queries, documents=None) -> list[tuple[str, str]]:
    if documents is None:
        if isinstance(queries, (str, bytes)):
            raise TypeError("supply query/document pairs, or a query and documents")
        result = list(queries)
        if any(not isinstance(pair, (tuple, list)) or len(pair) != 2
               or any(not isinstance(item, str) for item in pair) for pair in result):
            raise TypeError("each pair must contain exactly two strings")
        return [tuple(pair) for pair in result]
    docs = _texts(documents)
    qs = [queries] * len(docs) if isinstance(queries, str) else _texts(queries)
    if len(qs) != len(docs):
        raise ValueError("queries and documents must have equal lengths")
    return list(zip(qs, docs))


def mean_pool(token_embeddings: np.ndarray, attention_mask: np.ndarray,
              normalize: bool = True) -> np.ndarray:
    """Exclude padding, average the remaining tokens, optionally L2-normalize."""
    hidden = np.asarray(token_embeddings, dtype=np.float32)
    mask = np.asarray(attention_mask, dtype=np.float32)
    if hidden.ndim != 3 or mask.shape != hidden.shape[:2]:
        raise ValueError("expected embeddings [batch,tokens,hidden] and matching mask")
    if not np.all((mask == 0) | (mask == 1)):
        raise ValueError("attention mask must contain only zero and one")
    sums = np.sum(hidden * mask[..., None], axis=1, dtype=np.float32)
    counts = np.maximum(np.sum(mask, axis=1, dtype=np.float32), np.float32(1e-9))
    pooled = sums / counts[:, None]
    if normalize:
        norms = np.sqrt(np.sum(pooled * pooled, axis=1, dtype=np.float32))
        pooled /= np.maximum(norms, np.float32(1e-12))[:, None]
    return np.ascontiguousarray(pooled, dtype=np.float32)


@dataclass(frozen=True)
class EncodedBatch:
    """One dynamically padded batch; indices refer to the caller's input order."""
    indices: np.ndarray
    inputs: dict[str, np.ndarray]
    attention_mask: np.ndarray

    @property
    def size(self) -> int:
        return int(len(self.indices))

    @property
    def padded_tokens(self) -> int:
        return int(self.attention_mask.size)


@dataclass(frozen=True)
class PreparedInputs:
    batches: tuple[EncodedBatch, ...]
    count: int
    lengths: np.ndarray
    batch_size: int
    sort_by_length: bool
    signature: str
    tokenization_seconds: float
    batching_seconds: float

    @property
    def real_tokens(self) -> int:
        return int(self.lengths.sum())

    @property
    def padded_tokens(self) -> int:
        return sum(batch.padded_tokens for batch in self.batches)

    @property
    def padding_fraction(self) -> float:
        return 1 - self.real_tokens / self.padded_tokens if self.padded_tokens else 0.0


class _ONNXTextModel:
    task: str
    default_max_length: int

    def __init__(self, model_dir: str | Path, *, model_path: str | Path | None = None,
                 num_threads: int = 1, max_length: int | None = None,
                 allow_spinning: bool = False, tokenizer_parallel: bool = False):
        started = time.perf_counter()
        # Optional native dependencies are imported only when constructing a model.
        import onnxruntime as ort
        import tokenizers
        from tokenizers import Tokenizer

        self.model_dir = Path(model_dir).resolve()
        if model_path is None:
            candidates = [self.model_dir / "model.onnx", self.model_dir / "onnx/model.onnx"]
            self.model_path = next((p for p in candidates if p.is_file()), candidates[0])
        else:
            self.model_path = Path(model_path).resolve()
        tokenizer_path = self.model_dir / "tokenizer.json"
        if not self.model_path.is_file() or not tokenizer_path.is_file():
            raise FileNotFoundError(f"local ONNX model and tokenizer.json required in {self.model_dir}")
        config_path = self.model_dir / "config.json"
        self.config = json.loads(config_path.read_text()) if config_path.is_file() else {}
        self.num_threads = _positive_integer(num_threads, "num_threads")
        self.max_length = _positive_integer(self.default_max_length if max_length is None else max_length, "max_length")
        minimum = 3 if self.task == "reranker" else 2
        maximum = int(self.config.get("max_position_embeddings", 512))
        if not minimum <= self.max_length <= maximum:
            raise ValueError(f"max_length must be between {minimum} and {maximum}")
        self.allow_spinning = bool(allow_spinning)
        self.tokenizer_parallel = bool(tokenizer_parallel)
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.no_padding()
        self.tokenizer.enable_truncation(max_length=self.max_length,
                                         strategy="longest_first", direction="right")
        self.pad_id = int(self.config.get("pad_token_id", 0))
        self.tokenizer_sha256 = file_sha256(tokenizer_path)

        options = ort.SessionOptions()
        options.intra_op_num_threads = self.num_threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.add_session_config_entry("session.intra_op.allow_spinning", str(int(self.allow_spinning)))
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        session_started = time.perf_counter()
        self.session = ort.InferenceSession(str(self.model_path), sess_options=options,
                                           providers=["CPUExecutionProvider"])
        self.session_creation_seconds = time.perf_counter() - session_started
        dtype_map = {"tensor(int64)": np.dtype(np.int64), "tensor(int32)": np.dtype(np.int32)}
        self.input_dtypes = {}
        for item in self.session.get_inputs():
            if item.name not in {"input_ids", "attention_mask", "token_type_ids", "position_ids"}:
                raise ValueError(f"unsupported ONNX input: {item.name}")
            if item.type not in dtype_map or len(item.shape) != 2:
                raise ValueError(f"ONNX input {item.name} must be a two-dimensional integer tensor")
            self.input_dtypes[item.name] = dtype_map[item.type]
        if "input_ids" not in self.input_dtypes:
            raise ValueError("ONNX model must accept input_ids")
        rank = 3 if self.task == "encoder" else 2
        candidates = [out for out in self.session.get_outputs() if len(out.shape) == rank]
        preferred = "last_hidden_state" if self.task == "encoder" else "logits"
        candidates.sort(key=lambda out: out.name != preferred)
        if not candidates:
            raise ValueError(f"expected a rank-{rank} {self.task} output")
        output = candidates[0]
        self.output_name = output.name
        if self.task == "encoder":
            dimension = output.shape[-1] if isinstance(output.shape[-1], int) else self.config.get("hidden_size", 384)
            if dimension != 384:
                raise ValueError(f"MiniLMEncoder requires 384 output dimensions; found {dimension}")
            self.embedding_dim = 384
        elif isinstance(output.shape[-1], int) and output.shape[-1] != 1:
            raise ValueError("MiniLMReranker requires one relevance logit per pair")
        signature_data = [self.task, self.tokenizer_sha256, self.max_length,
                          [(key, value.str) for key, value in sorted(self.input_dtypes.items())]]
        self.signature = hashlib.sha256(json.dumps(signature_data).encode()).hexdigest()
        self.versions = {"numpy": np.__version__, "onnxruntime": ort.__version__,
                         "tokenizers": tokenizers.__version__}
        self.load_seconds = time.perf_counter() - started

    def _prepare_items(self, items, batch_size: int, sort_by_length: bool) -> PreparedInputs:
        batch_size = _positive_integer(batch_size, "batch_size")
        started = time.perf_counter()
        if self.tokenizer_parallel:
            encoded = self.tokenizer.encode_batch(items, add_special_tokens=True)
        elif self.task == "encoder":
            encoded = [self.tokenizer.encode(text, add_special_tokens=True) for text in items]
        else:
            encoded = [self.tokenizer.encode(query, pair=document, add_special_tokens=True)
                       for query, document in items]
        tokenization_seconds = time.perf_counter() - started
        started = time.perf_counter()
        lengths = np.asarray([len(item.ids) for item in encoded], dtype=np.int64)
        if np.any(lengths > self.max_length) or np.any(lengths < 1):
            raise RuntimeError("tokenizer did not respect the requested sequence length")
        order = np.argsort(-lengths, kind="stable") if sort_by_length else np.arange(len(items))
        batches = []
        for start in range(0, len(items), batch_size):
            indices = np.ascontiguousarray(order[start:start + batch_size], dtype=np.int64)
            width = int(lengths[indices].max())
            shape = (len(indices), width)
            ids = np.full(shape, self.pad_id, dtype=np.int64)
            mask = np.zeros(shape, dtype=np.int64)
            types = np.zeros(shape, dtype=np.int64)
            for row, index in enumerate(indices):
                item = encoded[int(index)]
                length = len(item.ids)
                ids[row, :length] = item.ids
                mask[row, :length] = item.attention_mask
                types[row, :length] = item.type_ids
            values = {"input_ids": ids, "attention_mask": mask, "token_type_ids": types}
            if "position_ids" in self.input_dtypes:
                values["position_ids"] = np.broadcast_to(np.arange(width), shape).copy()
            inputs = {name: np.ascontiguousarray(values[name], dtype=dtype)
                      for name, dtype in self.input_dtypes.items()}
            batches.append(EncodedBatch(indices, inputs, mask))
        return PreparedInputs(tuple(batches), len(items), lengths, batch_size,
                              bool(sort_by_length), self.signature, tokenization_seconds,
                              time.perf_counter() - started)

    def _predict(self, prepared: PreparedInputs, normalize: bool = True,
                 progress: Callable[[int, int], None] | None = None) -> tuple[np.ndarray, dict]:
        if not isinstance(prepared, PreparedInputs) or prepared.signature != self.signature:
            raise ValueError("prepared inputs use a different task, tokenizer, truncation, or ONNX input layout")
        started = time.perf_counter()
        shape = (prepared.count, 384) if self.task == "encoder" else (prepared.count,)
        result = np.empty(shape, dtype=np.float32)
        ort_seconds = []
        postprocess_seconds = []
        completed = 0
        for batch in prepared.batches:
            before = time.perf_counter()
            output = self.session.run([self.output_name], batch.inputs)[0]
            ort_seconds.append(time.perf_counter() - before)
            before = time.perf_counter()
            if self.task == "encoder":
                if output.shape != (*batch.attention_mask.shape, 384):
                    raise RuntimeError(f"unexpected token embedding shape {output.shape}")
                prediction = mean_pool(output, batch.attention_mask, normalize=normalize)
            else:
                if output.shape != (batch.size, 1):
                    raise RuntimeError(f"unexpected relevance logit shape {output.shape}")
                prediction = np.asarray(output[:, 0], dtype=np.float32)
            if not np.all(np.isfinite(prediction)):
                raise FloatingPointError("model returned non-finite predictions")
            result[batch.indices] = prediction
            postprocess_seconds.append(time.perf_counter() - before)
            completed += batch.size
            if progress is not None:
                progress(completed, prepared.count)
        return result, {"ort_seconds": float(sum(ort_seconds)),
                        "ort_batch_seconds": ort_seconds,
                        "postprocess_seconds": float(sum(postprocess_seconds)),
                        "prepared_pipeline_seconds": time.perf_counter() - started}

    def predict_prepared(self, prepared: PreparedInputs, *, normalize: bool = True,
                         progress: Callable[[int, int], None] | None = None) -> np.ndarray:
        """Infer already tokenized inputs; restore the original order."""
        return self._predict(prepared, normalize=normalize, progress=progress)[0]

    def metadata(self, *, include_hash: bool = True) -> dict:
        return {"task": self.task, "model_path": str(self.model_path),
                "model_bytes": self.model_path.stat().st_size,
                "model_sha256": file_sha256(self.model_path) if include_hash else None,
                "tokenizer_sha256": self.tokenizer_sha256, "versions": self.versions,
                "provider": "CPUExecutionProvider", "intra_op_threads": self.num_threads,
                "inter_op_threads": 1, "execution_mode": "ORT_SEQUENTIAL",
                "graph_optimization": "ORT_ENABLE_ALL", "allow_spinning": self.allow_spinning,
                "tokenizer_parallel": self.tokenizer_parallel,
                "max_length_including_special_tokens": self.max_length,
                "padding": "right, longest sequence in each batch",
                "truncation": "right, longest_first (query/document pairs)",
                "output": "L2-normalized mask-mean embeddings" if self.task == "encoder" else "raw relevance logits; no sigmoid",
                "session_creation_seconds": self.session_creation_seconds,
                "load_seconds": self.load_seconds}

    def benchmark(self, items, *, batch_sizes=(1, 8, 32), sort_modes=(False, True),
                  warmup=1, repeats=5, progress=None) -> dict:
        from .benchmark import benchmark_model
        return benchmark_model(self, items, batch_sizes=batch_sizes, sort_modes=sort_modes,
                               warmup=warmup, repeats=repeats, progress=progress)


class MiniLMEncoder(_ONNXTextModel):
    """all-MiniLM-L6-v2: texts -> float32[N,384] sentence embeddings."""
    task = "encoder"
    default_max_length = 256

    def prepare(self, texts: str | Sequence[str], *, batch_size: int = 32,
                sort_by_length: bool = True) -> PreparedInputs:
        return self._prepare_items(_texts(texts), batch_size, sort_by_length)

    def encode(self, texts: str | Sequence[str], *, batch_size: int = 32,
               sort_by_length: bool = True, normalize: bool = True,
               progress: Callable[[int, int], None] | None = None) -> np.ndarray:
        prepared = self.prepare(texts, batch_size=batch_size, sort_by_length=sort_by_length)
        return self.predict_prepared(prepared, normalize=normalize, progress=progress)


class MiniLMReranker(_ONNXTextModel):
    """ms-marco-MiniLM-L6-v2: query/document pairs -> raw float32[N] logits."""
    task = "reranker"
    default_max_length = 512

    def prepare(self, queries, documents=None, *, batch_size: int = 16,
                sort_by_length: bool = True) -> PreparedInputs:
        return self._prepare_items(_pairs(queries, documents), batch_size, sort_by_length)

    def score(self, queries, documents=None, *, batch_size: int = 16,
              sort_by_length: bool = True,
              progress: Callable[[int, int], None] | None = None) -> np.ndarray:
        prepared = self.prepare(queries, documents, batch_size=batch_size,
                                sort_by_length=sort_by_length)
        return self.predict_prepared(prepared, progress=progress)
