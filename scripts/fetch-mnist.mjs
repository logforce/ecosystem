// SPDX-License-Identifier: Apache-2.0
import { createHash } from 'node:crypto';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';

const destination = process.argv[2];
if (!destination || !path.isAbsolute(destination)) throw new Error('Supply a new absolute model directory');
const revision = '4f43949841cb55a0b98dc8fcd045431ccafd9f96';
const url = `https://media.githubusercontent.com/media/onnx/models/${revision}/validated/vision/classification/mnist/model/mnist-8.onnx`;
const response = await fetch(url, { signal: AbortSignal.timeout(30000) });
if (!response.ok) throw new Error(`Model download: ${response.status}`);
const chunks = [];
let size = 0;
for await (const chunk of response.body) {
  size += chunk.length;
  if (size > 26454) throw new Error('Model exceeds approved size');
  chunks.push(chunk);
}
const data = Buffer.concat(chunks);
if (size !== 26454 || createHash('sha256').update(data).digest('hex') !==
  '2f06e72de813a8635c9bc0397ac447a601bdbfa7df4bebc278723b958831c9bf') throw new Error('Model digest mismatch');
mkdirSync(destination, { recursive: false });
writeFileSync(path.join(destination, 'mnist-8.onnx'), data, { flag: 'wx', mode: 0o644 });
console.log('Approved MNIST artifact downloaded and SHA-256 verified. No model weights are added to Git.');
