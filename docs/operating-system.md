# Installable ecOS: Platform and Delivery

Status: researched platform recommendation, not a tested image. Sources were
checked on 12 September 2026. No OS image or installer has been built.

## 1. Original OS Architecture

ecOS >_ is a new operating system designed and built by this project. CogPOSIX is
its own POSIX-inspired interface for controlled AI computation. It is not a
third-party operating-system product that this project has adopted.

The OS architecture and system contract are original project work. The current
runtime uses Linux for development and validation; a kernel written from scratch
and a complete OS installer have not been implemented. Existing kernel and image
tools below are engineering candidates, not the identity of ecOS >_ .

## Prototype Delivery Recommendation

The earlier prototype recommendation uses a Linux-based image, with CogPOSIX
remaining usable as a runtime on other supported Linux installations. The proposed
prototype uses a Fedora-based bootc image; validate it against a Debian Live
prototype before committing the release engineering investment. Do not maintain
two production distributions at the outset.

The reason is product control over system services, model policy, updates and
recovery while retaining a mature application and driver ecosystem. A custom
kernel is not required to make ecOS an independently installable OS.

bootc uses OCI images to deliver bootable host systems and transactional updates;
the installed host is not merely an application container. Its installer mechanism
connects an image to filesystem and bootloader setup. These facilities are useful
building blocks, not a completed ecOS desktop or installer.
[bootc introduction](https://bootc.dev/bootc/),
[installation architecture](https://bootc.dev/bootc/bootc-install.html).

## 2. Options Considered

The suitability judgments below are project inferences. Hardware and release
support must be rechecked against the actual target machines.

| Base | Why consider it | Project tradeoff | Decision |
| --- | --- | --- | --- |
| Fedora + bootc | Image-based deployment; existing Fedora packaging | Need to prove desktop composition, firmware and release maintenance | Preferred prototype |
| Debian + live-build | Established customizable live/install media | Transactional updates and recovery need a separate product design | Fallback; useful installer benchmark |
| NixOS | Declarative configuration and generations | Team expertise, packaging and vendor-runtime integration need assessment | Strong engineering alternative |
| Yocto/OpenEmbedded | Custom embedded Linux images and board integration | Greater distribution/build ownership than a first workstation needs | Later OEM-specific edition |
| FreeBSD | Coherent Unix platform and jails | Current design depends on Linux memory/IPC facilities; accelerator compatibility unvalidated | No initial target |
| Android/AOSP | Mobile application sandbox and device platform | Device-specific integration, application service boundary and update ownership | Later mobile feasibility track |
| New kernel | Full design freedom | Rebuild drivers, security, power management and compatibility | Rejected for initial product |

Fedora documents bootc availability and image-based package workflows. Debian
documents customizable live systems and installable desktop media. NixOS documents
its configuration and generation management. Yocto is a system-building toolkit;
FreeBSD provides jails but would require portability work here.
[Fedora](https://fedoraproject.org/wiki/Changes/DNFAndBootcInImageModeFedora),
[Debian Live](https://live-team.pages.debian.net/live-manual/html/live-manual.en.html),
[Debian install images](https://www.debian.org/CD/live/),
[NixOS manual](https://nixos.org/manual/nixos/stable/),
[Yocto overview](https://www.yoctoproject.org/about/project-overview/),
[FreeBSD handbook](https://docs.freebsd.org/en/books/handbook/jails/).

## 3. Initial Hardware Profile

Target one x86-64 UEFI reference PC, then a second machine from a different vendor.
A planning configuration is 16-32 GiB RAM and SSD storage, with a CPU-only fallback.
These are procurement hypotheses, not verified minimum requirements. Measure model
working sets before specifying disk or memory minima. Include one selected GPU or
NPU only after its driver, runtime and model combination passes validation.

ARM64 follows a specific customer or board requirement. No promise of arbitrary
laptop, phone or accelerator compatibility. A VM validates installation and basic
CPU behavior; it cannot replace bare-metal tests of accelerators, suspend or energy.

## 4. Image Composition

| Area | Initial responsibility |
| --- | --- |
| Base OS | Selected upstream kernel, system services, networking and hardware packages |
| Desktop | One existing desktop environment; GNOME/Wayland is the initial candidate |
| Runtime | ecosd, workers, client library, CLI and service policies |
| Capabilities | Small offline-ready model pack with locked compatibility record |
| Applications | At least three integrations spanning more than language models |
| Security | Confinement, per-user data boundaries, signed release verification |
| Administration | Model catalogue, resource view, execution-location policy and updates |
| Recovery | Previous approved deployment and independent rescue environment |

Image-based delivery alone does not make a system immutable in every respect or
secure. Enumerate writable state and verify its permissions. Prefer minimal
system mutations at runtime; store user data and model packs separately from the
base image. Confidential local indexes belong to user-scoped storage.

## 5. Installation Experience

The final product must offer an installable USB/ISO or equivalent supported disk
image with verification information. Development artifacts should start as a VM
disk image to contain destructive installer testing.

Required installer flow:

1. Verify distribution authenticity and display the exact release/hardware profile.
2. Check CPU, memory, storage and supported accelerator status.
3. Let the user choose a target disk with an explicit destructive-write confirmation.
4. Configure locale, user identity, encryption and recovery material.
5. Install a tested partition/boot layout and a previous/recovery boot option.
6. Install the minimal approved offline model pack without requiring cloud login.
7. Boot into a working desktop and expose model availability and resource status.
8. Run an offline capability smoke test and provide an understandable unsupported-device result.

Partition layout, installer frontend and signing-key enrollment are release
engineering decisions to validate. Do not publish device-writing commands before
the build artifacts and recovery path are tested. Dual boot is outside the first
supported profile unless separately validated.

## 6. Boot, Update and Recovery Contract

The release pipeline produces an OS image digest, package inventory, model-lock
record, signatures, release notes and test report. Pin the base image and package
inputs; do not use moving tags as reproducibility evidence.

Secure Boot integration must be verified for the complete boot chain, including
any out-of-tree modules. Do not require globally disabling boot verification as a
normal installation step. Full-disk encryption and recovery-key behavior need
separate validation; they are not implied by image signing.

Stage updates, verify them, check disk capacity and compatibility, then activate
through a tested reboot path. Health checks must cover login, storage, runtime and
the minimal capability pack. Define a bounded boot-failure rollback mechanism;
do not assume choosing bootc automatically configures every health check.

Keep user data intact during OS rollback. Handle forward-only data migrations and
revoked releases explicitly. Recovery must boot with inference and learning
disabled. A model or optimization service cannot be necessary to unlock recovery.

Air-gapped deployments need signed offline update bundles, trust-root rotation,
revocation handling and documented administrative transfer procedures.

## 7. Mobile Track

Android/AOSP is the most plausible mobile candidate to investigate because it is
a Linux-based mobile platform with application isolation and verified-boot
mechanisms. AOSP documents UID/process sandboxing and a boot integrity chain;
these are existing facilities to respect, not replace with ecOS predictions.
[Android sandbox](https://source.android.com/docs/security/app-sandbox),
[Verified Boot](https://source.android.com/docs/security/features/verifiedboot).

A mobile edition needs a supported device/OEM partner, working camera/audio and
accelerator interfaces, power/thermal validation, and maintained security updates.
Do not assume an arbitrary phone can be safely flashed or that all proprietary
device components are redistributable. Bootloader relocking and verified boot must
be tested on the chosen device before any supported image claim.

CogPOSIX semantics can remain common, but Android service transport and application
permissions need a platform-specific design, likely including Binder integration.
Porting a C library does not automatically grant a system service access across
application sandboxes. Mobile acceptance also includes background-execution limits,
storage pressure and sustained battery impact.

## 8. Platform Selection Gate

Build equivalent minimal Fedora/bootc and Debian installer prototypes only after
the runtime can execute a real model. Compare install success, signed update,
rollback after interrupted update, driver support, offline model activation and
maintenance effort. Choose one based on measured results. Public release names,
version pins and device support stay provisional until this gate passes.
