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

See [license scope](../LICENSE-SCOPE.md) and [publication rules](../PUBLICATION.md).

## Reconsideration Criteria

Revisit ADR-004 if the preferred image cannot support required drivers, desktop
updates or recovery with acceptable maintenance effort. Revisit product sequencing
if a paying customer needs a controlled appliance before a desktop. Revisit the
runtime approach if an existing server meets the same contracts and buyer outcomes
with materially lower integration cost.

Changing a recorded choice requires its reason, evidence, compatibility impact and
migration plan. Preserve historical decisions so future readers can distinguish
the original runtime specification from the expanded OS product.
