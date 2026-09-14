# Decision Record

Recorded 12 September 2026. "Selected direction" means the design choice in this
repository; it does not imply implemented or tested behavior.

| ID | Status | Decision | Rationale and consequence |
| --- | --- | --- | --- |
| ADR-001 | Selected direction | CogPOSIX is central; models span multiple classes | The project is shared AI infrastructure with semantic contracts, beyond an LLM shell |
| ADR-002 | Selected direction | Separate interface, runtime and installable OS | Permit incremental adoption while retaining a complete OS deliverable |
| ADR-003 | Selected direction | Linux user-space implementation first | Preserve existing drivers and system services; avoid a new kernel dependency |
| ADR-004 | Provisional platform | Fedora/bootc prototype; Debian fallback | Validate image/update approach and installer before production commitment |
| ADR-005 | Selected direction | Controlled capability catalogue after explicit-model MVP | Semantic interchangeability requires evaluation, not only tensors |
| ADR-006 | Selected direction | Local-only default | Off-device execution requires a separate policy and transport design |
| ADR-007 | Selected direction | Existing inference engines under internal adapters | Do not implement operator kernels or a custom compiler IR for MVP |
| ADR-008 | Clarification | Isolate real-model workers before untrusted-client service | Contain parsers/FFI; move beyond deferred isolation in original draft |
| ADR-009 | Selected direction | Learner proposes; bounded broker enforces | Keep authority separate from predictions and permit independent recovery |
| ADR-010 | Research only | Kernel scheduling extensions after measured user-space value | CPU scheduling research is distinct from accelerator coordination |
| ADR-011 | Research only | Security learning starts advisory | False positives, poisoning and recovery require evidence |
| ADR-012 | Deferred | Android/AOSP device-specific mobile port | Need hardware, service permissions and maintained boot/update integration |
| ADR-013 | Open | Public project/product names | Existing eCos collision and standardization implications require review |
| ADR-014 | Selected | Apache-2.0 software; CC BY 4.0 public prose | Explicit file scope; third-party/model terms preserved; optional proprietary modules separate |
| ADR-015 | Experimental | C ABI and protocol until conformance freeze | Resolve lifecycle and extension rules before long-term compatibility promises |
| ADR-016 | Selected | Complete community foundation; optional LOGFORCE | Open event schema/basic bridge; baseline security and execution require no proprietary service |
| ADR-017 | Selected | Fresh allowlisted public export | Mixed working history cannot be published; private files excluded by default |
| ADR-018 | Implemented experiment, 13 September 2026 | Bounded inline U8 buffers for R1 | Exercise ownership and cancellation before Linux shared memory; no zero-copy or real inference claim |
| ADR-019 | Implemented experiment, 13 September 2026 | Explicit context on C resource operations | Make connection ownership unambiguous; experimental ABI 0.1, wire protocol 1.0; neither is frozen |
| ADR-020 | Implemented experiment, 13 September 2026 | Per-user mock service with macOS development support | Linux remains the deployment target; same-UID socket access is not application sandboxing; in-process trusted mock only |
| ADR-021 | Verified experiment, 13 September 2026 | Validate the public snapshot in non-root offline Linux containers | Digest-pinned tooling images and bounded disposable execution; private working tree never mounted; container evidence is not OS certification |
| ADR-022 | Implemented experiment, 13 September 2026 | R2 imports fully sealed immutable inputs and exports sealed snapshots | Kernel seals prevent sender mutation/truncation; no live writable output maps or fully zero-copy claim; wire 1.1 and C ABI 0.2 require rebuilt endpoints |

| ADR-023 | Verified experiment, 14 September 2026 | Supervised Linux Python/ONNX CPU worker for one pinned MNIST artifact | Reuse an existing engine; preserve U8 ABI with profile-specific shapes; bounded worker exchanges, cancellation by termination, no automatic job retry; syscall denylist is not a complete hostile-code sandbox |

See the [R3 evidence and remaining gates](onnx-worker.md),
[license scope](../LICENSE-SCOPE.md) and [publication rules](../PUBLICATION.md).

## Reconsideration Criteria

Revisit ADR-004 if the preferred image cannot support required drivers, desktop
updates or recovery with acceptable maintenance effort. Revisit product sequencing
if a paying customer needs a controlled appliance before a desktop. Revisit the
runtime approach if an existing server meets the same contracts and buyer outcomes
with materially lower integration cost.

Changing a recorded choice requires its reason, evidence, compatibility impact and
migration plan. Preserve historical decisions so future readers can distinguish
the original runtime specification from the expanded OS product.
