# Security, Sovereignty and Defensive Learning

## 1. Security Objective

Protect user data, application isolation, host integrity and availability while
running potentially hostile model inputs and complex native inference code.
Learning may improve detection and prioritization, but deterministic controls
remain responsible for enforcement. No security improvement is claimed as measured
until the evaluation gates below pass.

Local execution avoids a particular external data transfer. It does not prevent
local malware, unsafe plugins, vulnerable drivers or leakage through logs and
indexes. Concentrating workloads in one service also creates a valuable attack
target and a potential common failure point.

## 2. Assets and Adversaries

Assets: documents, audio/video, embeddings, model state, user identities, catalogue
integrity, update keys, resource availability, telemetry and recovery material.

Adversaries include a malicious local client, another user, a compromised model
publisher, crafted input, a compromised worker and a manipulated telemetry source.
The initial threat model does not claim resistance after arbitrary host-kernel
compromise or against all physical attacks. Record deployment-specific assumptions.

## 3. Threat and Control Matrix

| Threat | Proposed controls | Required evidence |
| --- | --- | --- |
| Cross-client access | Authenticated sessions, handle checks, user/sandbox identity | Invalid/stale handle and cross-user tests |
| Native parser exploit | Isolated workers, restricted files/devices, syscall policy | Malformed models and worker-failure tests |
| Resource exhaustion | Admission limits, memory/descriptor/job quotas | Flood and out-of-memory tests |
| Shared-memory races | Trusted metadata, enforceable transfer or snapshots | Concurrent mutation tests |
| Model tampering | Artifact verification and signed approval catalogue | Corrupt artifact and untrusted-signature rejection |
| Unauthorized upload | Worker network isolation and approved egress broker | Packet capture across complete workflows |
| Prompt/content injection | Treat output as data; separately authorize actions | Malicious documents cannot obtain new access |
| Poisoned learning | Versioned baselines, scoped input, review and rollback | Controlled contamination evaluation |
| Update compromise | Separate release keys, revocation and verification | Invalid update/rollback rejection |
| Sensitive diagnostics | Redaction, scoped access, retention limits | Log and crash-report inspection |

## 4. Baseline Enforcement

Use dedicated service identities, least-privilege file/device access, mandatory
access-control policy where supported, and a restricted syscall surface for
workers. Seccomp filters reduce syscall exposure; they are not a complete sandbox
or replacement for filesystem and identity controls.
[Kernel seccomp documentation](https://docs.kernel.org/userspace-api/seccomp_filter.html).

Models and adapters execute in worker isolation before serving untrusted clients.
For multiuser production, choose worker separation by trust domain and document
the performance cost. Sharing one native address space across hostile tenants is
not equivalent to isolation merely because handles differ.

The inference core does not receive general document-directory access. Applications
or permission-aware services supply authorized inputs. Desktop indexing must honor
access revocation, deletion and user separation. Access to an embedding index can
reveal sensitive information even when original text is not returned.

GPU access expands the trusted boundary to device drivers and runtime libraries.
Document capabilities needed by each backend; reject unsupported confinement
instead of silently starting a privileged unrestricted worker.

## 5. Locality Enforcement

Disable worker egress by default. Audit preprocessing, downloads, telemetry, crash
reporting and update checks separately. Local inference requires no remote model
catalogue access after installation of the approved offline pack.

Optional off-device execution must flow through an authenticated, policy-controlled
service that knows the destination and authorized data scope. Logs record the
decision without retaining the transmitted payload by default. Strict local-only
mode cannot be relaxed by an application hint or model-generated instruction.

The planned [distributed policy domain](distributed-intelligence.md) does not
erase machine boundaries. Node authentication, capability approval and data-use
authorization are separate checks. Apply locality rules to retrieval, replication,
embeddings and training artifacts as well as inference inputs. Permission to run
a job is not permission to read shared datasets or train on that job's content.

Network isolation of ecOS does not automatically constrain every ordinary desktop
application. Product claims must specify whether they cover managed capability
execution or the entire device's network policy.

## 6. How Learning Could Improve Cybersecurity

Candidate detectors can analyze unusual process relationships, abrupt deviations
in file activity, unexpected destinations or repeated policy denials. Specialized
statistical and classification models may suit these tasks better than an LLM.
An optional language model may explain an alert from supplied evidence, but it is
not the authorization engine or sole source of detection truth.

Potential benefits include earlier triage, fewer repetitive alerts and more useful
local context. They must be measured against deterministic rules and the existing
security stack. The platform should integrate with an organization's monitoring
tools rather than assume it can replace endpoint protection from its first release.

Learning systems themselves face evasion, poisoning and privacy attacks. NIST's
adversarial-ML taxonomy explicitly covers these classes across predictive and
generative systems. Treat that as a threat-model input, not a claim that a specific
mitigation solves them.
[NIST 2025 taxonomy announcement](https://www.nist.gov/news-events/news/2025/03/nist-trustworthy-and-responsible-ai-report-adversarial-machine-learning).

## 7. Response Authority

| Response | Initial behavior | Later automatic mode |
| --- | --- | --- |
| Attach evidence to alert | Allowed with scoped data | Allowed |
| Recommend a policy change | Advisory | Advisory unless separately authorized |
| Reduce suspect job admission | Existing deterministic quota rules | Bounded preauthorized policy with expiration |
| Quarantine a worker | Administrator action or deterministic integrity failure | Only scoped, reversible, evaluated policy |
| Block broad network access | Administrator/established security policy | Not a model-only decision |
| Delete files or kill arbitrary apps | Not autonomously authorized | Requires separate explicit policy and recovery design |
| Disable security controls | Prohibited for learning services | Prohibited |

Observing repeated behavior does not grant it permission. A detector's low risk
score cannot override a deny rule. Alert suppression must be reversible and must
not suppress integrity events protecting the monitoring system itself.

## 8. Security Learning Lifecycle

Begin in observation mode, preserve an approved baseline, label evaluated events,
and test candidates on held-out benign and malicious traces in an isolated lab.
Track model/feature/threshold versions and administrator feedback provenance.
Do not feed raw production activity directly into an automatically trusted baseline.

Use measured precision, recall, detection delay, false alerts per endpoint-day,
false containment events, overhead and recovery time. Evaluate poisoning, evasion,
drift and missing telemetry. A low base rate of attacks makes apparently good
classification accuracy an inadequate metric.

No automatic response ships until an agreed false-positive budget and recovery
test pass. On detector failure, retain deterministic protection and return to
advisory/static operation. Reserve resources so an inference overload cannot
starve essential logging or enforcement.

## 9. Release and Incident Operations

Maintain component inventories, model provenance, vulnerability response ownership
and signed release metadata. Define emergency model revocation, worker termination,
safe recovery and evidence retention. Release-signing credentials must not live in
an endpoint learner or ordinary inference worker.

Local audit records are not tamper-proof against a privileged attacker. Managed
deployments may add approved remote audit storage, but this is an explicit change
to their data-location policy. Compliance and certification require separate
assessment; neither local execution nor AI detection establishes them.
