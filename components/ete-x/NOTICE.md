# ETE-X notices

ETE-X original implementation and integration: Gio Pecora / LOGFORCE Inc.
The scientific author attribution supplied with the implementation is Gio
Pecora, Department of Computer Science and Applied Research / EKD Center of
Excellence (EKD COE). Original ETE-X files use CC BY-NC-ND 4.0; see `LICENSE.md`.

The demonstration encoder is derived from `sentence-transformers/all-MiniLM-L6-v2`
at revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. The reranker is derived from
`cross-encoder/ms-marco-MiniLM-L6-v2` at revision
`233902d25c440f23af6f7d6e94d2946bac0bee0a`. Their Apache-2.0 model cards, tokenizers,
configurations and transformation manifests are retained under
`runtime/commercial_lab/assets/models/`; the Apache license text is retained at
`runtime/commercial_lab/licenses/Apache-2.0.txt`.

The included ONNX files were converted, graph-optimized and dynamically quantized
on 22 October 2026. They are lossy compact derivatives, not models trained by
LOGFORCE and not members of the LOGFORCE LFS Model Family. Model quantization may
change embeddings and reranker scores. The source revisions and original model
attribution remain applicable; ETE-X's license does not replace them.

Runtime libraries are not vendored into this source repository. The hash-locked
Linux CPython 3.12 dependency file identifies the distributions used by the kit.
Installed distributions retain their own notices. The original prototype uses
established bound filtering with exact refinement; no novelty, patent validity,
freedom-to-operate, endorsement or certification claim is made here.
