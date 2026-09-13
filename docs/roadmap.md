# Roadmap and Evidence Gates

This is a dependency plan, not a delivery-date promise. Every implementation gate
starts unverified. Runtime, OS, commercial and learning work have different
acceptance criteria. A milestone passes only with an attached reproducible report.

As of 13 September 2026, an experimental R0/R1 subset is implemented and tested
on macOS. See the [evidence report](implementation-r1.md). Linux validation,
full cancellation stress testing and interface review remain required before
declaring those gates complete. R2 and later are not implemented.

## 1. Milestones

| ID | Deliverable | Acceptance evidence | Dependency |
| --- | --- | --- | --- |
| D0 | Expanded documentation repository | Coherent contracts, references and recorded open decisions | Current documentation task |
| R0 | ABI/protocol design | Headers/schema, ownership diagrams, error rules, chosen dynamic-output strategy | D0 |
| R1 | Mock runtime | Concurrent clients, handle isolation, cleanup, cancel races and bounded queues | R0 |
| R2 | Shared host memory | Descriptor validation, bounds/race tests, measured control/data separation | R1 |
| R3 | ONNX CPU worker | Reference output agreement, worker crash containment, no unrestricted model loading | R2 |
| R4 | Scheduling/accounting | Priority authorization, starvation policy, overload behavior and overhead report | R3 |
| R5 | Local model packages | Manifest/hash checks, approved activation, malformed package rejection | R3 |
| R6 | Graph/stream prototype | Two-model pipeline, dependency failure and buffer lifetime correctness | R4, R5 |
| R7 | One accelerator profile | Same contract, measured transfers/quality, reset and memory-pressure behavior | R4, R5 |
| C1 | Controlled capability catalogue | Semantic profiles, pinned artifacts, quality reports and substitution tests | R5 |
| A1 | Independent applications | Three integrations spanning at least three model classes | R6, C1 |
| O1 | Base OS prototype selection | Fedora/bootc versus Debian installation/update/recovery report | R3 |
| O2 | Installable developer preview | Bootable artifact, offline model pack, working applications and recovery | O1, A1 |
| O3 | Supported OS pilot | Bare-metal hardware matrix, signed updates, isolation and support procedures | O2, R7 if accelerator advertised |
| L1 | Optimization observation | Scoped telemetry, static/heuristic baseline and no control mutation | R4 |
| L2 | Bounded automatic tuning | Held-out benefit, policy enforcement, watchdog and rollback tests | L1, O3 |
| S1 | Security advisory research | Labeled evaluation, false-alert/overhead report, poisoning tests | Security baseline established |
| M1 | Mobile feasibility | Named device, service isolation, boot/update and power report | Stable contracts and funded use case |

The original mock, shared-memory, CPU, scheduler, registry and graph sequence is
preserved conceptually. Worker isolation moves ahead of untrusted real-model use.
An early accelerator benchmark follows CPU correctness to test buyer value before
the project builds the full OS experience.

## 2. Performance Protocol

Record hardware/firmware, kernel, runtime versions, model and adapter digests,
precision, input distribution, arrival rates, thread settings and thermal/power
state. Separate cold-load and warm-run measurements. Report repeated-run variation,
sample counts and unsupported configurations.

Compare direct runtime use, a competent existing shared inference configuration
where appropriate, and ecOS. Maintain equivalent model quality and preprocessing.
Measure P50/P95/P99 latency, throughput, queue delay, peak host/device memory,
copy/transfer volume, energy when instrumentation exists, and failure rate.

Include small jobs where IPC overhead may dominate, mixed-priority workloads and
nonparticipating background load. Report regressions as well as wins. Agree on
customer acceptance thresholds before claiming savings.

## 3. Mandatory Failure Tests

- Client crash during input mapping, queueing and execution.
- Worker crash or device reset with outstanding jobs.
- Cancellation racing dispatch and completion.
- Descriptor exhaustion, bounded queue overflow and memory pressure.
- Malformed tensor extents, stale handles and cross-client access.
- Interrupted package activation and incompatible model/runtime versions.
- Lost telemetry during an optimization experiment.
- Network disconnected before all advertised offline workflows.
- Interrupted OS update and insufficient staging storage.
- Recovery boot with daemon and learning services disabled.

## 4. OS Release Checklist

Publish verified image identifiers and instructions only after installation to a
disposable VM disk succeeds. Before bare-metal support, test boot verification,
encryption/recovery, graphics, networking, audio, sleep/resume, selected accelerator,
model activation and user-data preservation through update/rollback.

Installer destructive actions require explicit target confirmation. Release tests
use designated disposable disks; do not use an operator's workstation as an
unattended installer target. Offline installation must include all assets needed
for the advertised initial capabilities.

## 5. Learning Release Checklist

Record the approved parameter envelope, predictor version, baseline, evaluation
set and rollback rules. Show that invalid/stale proposals are rejected, security
controls cannot be relaxed, and the watchdog functions independently of the model.
Learned cybersecurity responses need separate false-containment evaluation.

Kernel scheduling experiments are not a prerequisite for a useful runtime or OS.
Keep them out of production release criteria until measured value justifies them.

## 6. Evidence Record Template

```text
Milestone:
Commit / image digest:
Hardware and software profile:
Model / capability versions:
Baseline and input dataset:
Commands and reproducibility instructions:
Results and variation:
Failures and limitations:
Acceptance criteria passed / failed:
Reviewer and date:
```

## 7. Open Implementation Decisions

The prototype selects a [binary protocol](protocol.md), per-user deployment,
experimental C layouts and caller-sized fixed outputs. Before interface freeze,
review these choices and resolve dynamic output allocation, worker isolation
granularity, model candidates and initial device. After prototypes, choose OS base version, installer, update trust
roots, desktop and storage layout. Before public distribution, decide naming,
third-party asset rights, specification IP governance and support lifecycle.
Community licenses are selected in [LICENSE-SCOPE.md](../LICENSE-SCOPE.md).
These remain future decisions, not capabilities established by the mock runtime.
