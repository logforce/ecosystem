# Controlled Models and Capability Catalogue

## 1. Model Classes

The catalogue is a curated system component, not an unrestricted model marketplace.
Start with a small supported collection and grow it through evaluated releases.

| Capability family | Typical providers | Required semantic details |
| --- | --- | --- |
| Audio enhancement | Denoising, separation | Sample rate, channels, frame length, delay |
| Speech recognition | Acoustic/sequence models | Language, timestamps, segmentation, normalization |
| Speech synthesis | Acoustic model and vocoder | Voice identity, rate, format, permitted use |
| Vision | Detection, segmentation, depth, pose | Color space, resize policy, coordinates, classes, units |
| Documents | OCR, layout, extraction | Reading order, page coordinates, field schema |
| Embeddings | Text/image/audio encoders | Dimension, normalization, similarity metric, index compatibility |
| Translation | Sequence models | Language pairs, context limits, terminology profile |
| Time series | Forecasting and anomaly models | Sampling interval, units, horizon, calibration |
| Language | Drafting, summarization, generation | Tokenizer, context budget, output validation |
| Security analysis | Event classification and anomaly scoring | Feature schema, baseline version, threshold and calibration |

This is a capability plan. No specific model weights have been selected, downloaded,
licensed or benchmarked in this repository. Exact candidates require evaluation
against redistribution rights, hardware requirements, quality and update burden.

## 2. Three Separate Identities

1. Artifact identity: immutable digest of weights, tokenizer and other required assets.
2. Capability identity: versioned meaning of the exposed operation.
3. Deployment identity: artifact plus adapter, runtime, device and numerical profile.

Two deployments of the same artifact can differ in precision and behavior. Two
artifacts can expose the same capability only after validation. An embedding
replacement generally requires reindexing unless compatibility is demonstrated.

## 3. Package Record

The following is an illustrative manifest, not a finalized schema or real package:

```yaml
manifest_version: 1
package:
  id: org.example.document-ocr
  version: 0.1.0
  publisher: example-publisher
artifacts:
  - path: weights/model.onnx
    sha256: REQUIRED_ACTUAL_DIGEST
    format: onnx
capabilities:
  - id: document.ocr
    profile_version: 1
    input_schema: document.page.rgb8.v1
    output_schema: document.ocr.regions.v1
adapter:
  id: org.example.ocr-adapter
  version: 0.1.0
execution:
  local_only: true
  runtime_profile: onnx-cpu-v1
  dynamic_shapes: false
resources:
  host_memory_peak_bytes: REQUIRED_MEASURED_VALUE
evaluation:
  report_digest: REQUIRED_ACTUAL_DIGEST
  hardware_profile: REQUIRED_TESTED_PROFILE
licensing:
  weights: REQUIRED_LICENSE_IDENTIFIER_OR_TEXT
  adapter: REQUIRED_LICENSE_IDENTIFIER_OR_TEXT
  redistribution_review: pending
```

The release record must additionally identify preprocessing assets, tokenizer or
label map if applicable, language/domain limits, dependency versions, signature,
trust chain, source/provenance record, evaluation dataset rights and revocation
status. A checksum detects a changed artifact; authenticity requires trusted
signature verification. A signature alone does not prove model quality or safety.

## 4. Selection and Substitution

First filter on authorization, profile version, local/off-device policy, artifact
revocation, runtime support, memory capacity and quality requirements. Then rank
eligible providers by measured latency, energy or other declared preferences.

Quality requirements refer to a named evaluation profile, not a universal accuracy
number. Publish supported language/domain slices and known failures. Out-of-domain
inputs can invalidate prior quality estimates. A model's confidence value does not
constitute independent evidence of task correctness.

Changing precision, preprocessing or device placement can alter output. Validate
the whole deployed pipeline. Never substitute a smaller model solely to save power
when the application requires a pinned artifact or stricter quality profile.

## 5. Package Lifecycle

```text
import -> quarantine -> verify -> evaluate -> approve -> stage -> activate
                                                        |
                                             revoke / retire / replace
```

Only approved packages are resolvable by ordinary clients. Installation must reject
path traversal, external path references and unbounded archives. Arbitrary custom
operators and preprocessing code need explicit trust review and worker confinement.

Activation changes a versioned catalogue snapshot. In-flight jobs retain their
original deployment; new jobs use the newly active version. A security revocation
may require stopping affected workers under an explicit incident policy. Preserve
the reason and impact in an audit record.

## 6. OS and Model Updates

Version OS images and large model packs separately. Maintain a compatibility lock
mapping runtime ABI, adapter versions, model digests and hardware profiles.
Activation must be transactional at the catalogue level even if downloads are
interrupted. Downloading an update must not activate it automatically in a managed
offline profile.

Keep a compatible previous catalogue for recovery. OS rollback is not model-store
rollback or user-data rollback. Test all three together before a release. Protect
against restoring a revoked vulnerable package through an old recovery snapshot.

## 7. Resource Accounting

Record cold load time, warm latency, peak host/device memory, per-session state,
batch-size sensitivity, transfer bytes and concurrent execution limits. Model file
size is not peak memory. Quantized weights may still require substantial execution
workspace, activation memory and unquantized components.

Share only validated immutable resources. User prompts, audio, embeddings, caches
and generated state remain private. Optional performance caches need identities
that prevent cross-user content exposure and documented invalidation rules.

## 8. Evaluation and Release Gate

Each included capability needs representative inputs, a baseline implementation,
semantic correctness checks, quality metrics, performance measurements and known
unsupported cases. Tests must cover corrupted artifacts, unsupported shapes,
resource exhaustion and adversarial input handling.

No default continuous training of shared models on user content. Personalization,
if later offered, is opt-in, scoped to that user, versioned and removable. Shared
security and optimization baselines have separate governance from content models.
