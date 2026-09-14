# SPDX-License-Identifier: Apache-2.0
"""Synthetic fixtures for runtime agreement, not a model-accuracy benchmark."""
import json
import pathlib
import sys
import numpy as np
import onnx
from onnx.reference import ReferenceEvaluator

model_path, destination = map(pathlib.Path, sys.argv[1:])
model = onnx.load(model_path)
onnx.checker.check_model(model)
reference = ReferenceEvaluator(model)
destination.mkdir()
rng = np.random.default_rng(20260913)
samples = [np.zeros((28, 28), dtype=np.uint8), np.full((28, 28), 255, dtype=np.uint8)]
samples.extend(rng.integers(0, 256, (28, 28), dtype=np.uint8) for _ in range(6))
results = []
for index, sample in enumerate(samples):
    image = sample.astype(np.float32).reshape(1, 1, 28, 28) / np.float32(255)
    logits = reference.run(None, {model.graph.input[0].name: image})[0]
    path = destination / f"sample-{index}.u8"
    path.write_bytes(sample.tobytes())
    results.append({"path": str(path), "digit": int(np.argmax(logits[0]))})
print(json.dumps(results))
