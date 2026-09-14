# Vision and Product Definition

## 1. Thesis

AI computation should be available as a managed system capability. Applications
should be able to use supported inference without each owning model distribution,
accelerator placement, memory policy and diagnostics. The platform must remain
useful beyond language generation and beyond the currently popular architectures.

CogPOSIX is the proposed contract. ecOS Runtime implements coordination. ecOS OS
packages that foundation into an installable computing environment with selected
applications, models, hardware support and lifecycle management.

## 2. Meaning of Independence

The project aims for operational independence: an administrator can install it,
perform supported tasks offline, select approved models, inspect execution and
maintain a deployment without a mandatory external inference account.

It does not imply that every component is domestically produced, every weight is
trained by the project, or every device is free of proprietary firmware. Such
dependencies must be inventoried. The platform remains dependent on upstream
kernel, runtime, firmware and model security maintenance.

| Dimension | Required product behavior | Evidence |
| --- | --- | --- |
| Execution control | Known permitted execution locations | Network-denial and execution-trace tests |
| Data control | Per-user storage, retention and sharing rules | Cross-user isolation and deletion tests |
| Model control | Pin, inspect, replace and revoke approved packages | Catalogue audit and rollback demonstration |
| Operational control | Offline use and recovery | Disconnect network before first task after installation |
| Update control | Administrator chooses authorized release channel | Signed update and revocation tests |
| Supply-chain visibility | Identify licenses, artifacts and dependencies | Release manifest and dependency inventory |

## 3. Intended Experiences

### Document work

An application captures a page, invokes layout analysis and OCR, indexes an
embedding, and optionally asks a language model to summarize the extracted text.
These are separate capabilities with separate quality profiles. Document content
does not authorize file access or commands. Indexes retain the original access
rules and are removed or rebuilt when those permissions change.

### Audio work

A conferencing application requests denoising. A recording application requests
speech recognition. A translation tool requests translation and speech synthesis.
The runtime arbitrates their resource use; an audio buffer is not converted into
a text prompt merely to pass through the system.

### Vision and equipment

An inspection application uses detection and segmentation while a background
service evaluates time-series anomalies. Their private inputs remain isolated.
A supported shared model session may reduce duplicate residency, but only after
backend-specific sharing has been measured and validated.

### Everyday desktop

File search, OCR, transcription and selected writing assistance are integrated
through explicit application or desktop adapters. Existing applications do not
automatically adopt CogPOSIX by running on ecOS. A conversational shell is optional.

## 4. Product Forms

| Form | Purpose | Sequence |
| --- | --- | --- |
| Developer runtime | Validate contracts and integration on existing Linux | First |
| Controlled-device runtime | Serve an OEM or appliance with several applications | Early commercial validation |
| ecOS workstation | Complete installable desktop on supported PCs | Explicit product milestone |
| ecOS appliance | Headless local inference with separately designed authenticated network API | Optional customer-driven expansion |
| ecOS mobile | Device-specific Android/AOSP integration | Deferred feasibility track |

An appliance serving another computer is off-device execution from that computer's
perspective. It must not be marketed as the same privacy boundary as execution on
the user's own endpoint.

## 5. Execution Policy

First select whether an ordinary algorithm is sufficient. For an AI capability,
filter packages by authorization, locality, contract version, hardware and tested
quality. Select among eligible candidates using latency and resource preferences.
If no candidate meets required constraints, return a structured unsupported or
unavailable result. Do not let a model's self-reported confidence authorize cloud
execution or claim that a quality threshold has been satisfied.

The strict endpoint-local profile disables off-device inference. An optional managed profile may
permit a named organizational service. A future cloud profile needs explicit
administrator policy and applicable user consent, including disclosure of the
data to transmit. Every stage of a pipeline inherits the strictest applicable
data-location restriction.

Local-first does not mean isolated. The planned
[distributed intelligence](distributed-intelligence.md) feature permits explicit
whole-job placement within an approved ecOS policy domain. Organizational control
across several nodes is distinct from endpoint-local execution. Data access and
model improvement require separate grants; no network fabric is implemented yet.

## 6. Distinction From Drivers and Assistants

A hardware driver provides access to a device. An inference engine executes model
computation. A model supplies a learned transformation. A capability adapter gives
that transformation a defined external meaning. CogPOSIX exposes the contract;
ecOS manages its resource and policy context. None replaces the others.

An assistant or agent consumes these services. Its planner and tool executor stay
outside the execution core, with their own permissions and user interactions.

## 7. Definition of Product Success

Three independently implemented applications should use several model classes
through one supported contract, with visible scheduling, bounded resource use,
correct isolation and an offline execution demonstration. Installation, updates
and recovery must work without developer intervention on a stated hardware set.

Commercial success additionally requires a repeated buyer problem and willingness
to pay for the supported system. Neither a compelling diagram nor a successful
two-client demo establishes those conditions.
