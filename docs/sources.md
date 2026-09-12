# Sources and Evidence Register

Checked 12 September 2026. These are primary references for existing technologies;
they do not validate ecOS performance, security or commercial demand. Project
recommendations are inferences from these capabilities and the stated goals.
Documentation at moving URLs can change; pin versions during implementation.

## Project Baseline

- [Original v0.1 specification](../ecOS_CogPOSIX_Project_Specification_v0.1.docx), dated 11 September 2026. Preserved unchanged.
- Subsequent user direction: controlled specialized models, CogPOSIX as the central interface, an installable sovereign OS, bounded learning and cybersecurity research. Captured in [SPEC.md](../SPEC.md).

## Runtime and Competition

| Source | Supports |
| --- | --- |
| [ONNX Runtime execution providers](https://onnxruntime.ai/docs/execution-providers/) | Existing hardware/runtime abstraction |
| [Triton rate limiter](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/rate_limiter.html) | Cross-model priority and resource coordination |
| [Triton shared-memory extension](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/protocol/extension_shared_memory.html) | Host/CUDA shared-memory transport and documented platform limits |
| [Windows ML execution providers](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers) | Incumbent device-provider integration |
| [Windows AI / Foundry Local FAQ](https://learn.microsoft.com/en-us/windows/ai/faq) | Local/offline inference behavior |
| [Apple Private Cloud Compute guide](https://security.apple.com/documentation/private-cloud-compute/) | Local processing with additional off-device processing |
| [LM Studio offline operation](https://lmstudio.ai/docs/app/offline) | Existing offline model/document workflows |

## OS Delivery

| Source | Supports |
| --- | --- |
| [bootc introduction](https://bootc.dev/bootc/) | OCI-based bootable host delivery and transactional updates |
| [bootc installation](https://bootc.dev/bootc/bootc-install.html) | Image-to-bootable-system installation architecture |
| [Fedora image-mode change](https://fedoraproject.org/wiki/Changes/DNFAndBootcInImageModeFedora) | Fedora bootc packaging/workflows; a change document, not an ecOS support guarantee |
| [Debian Live manual](https://live-team.pages.debian.net/live-manual/html/live-manual.en.html) | Custom live image construction; commands require release-specific validation |
| [Debian live install media](https://www.debian.org/CD/live/) | Existing installable desktop media |
| [NixOS manual](https://nixos.org/manual/nixos/stable/) | Declarative system management and generations |
| [Yocto overview](https://www.yoctoproject.org/about/project-overview/) | Custom embedded Linux build approach |
| [FreeBSD jails](https://docs.freebsd.org/en/books/handbook/jails/) | Unix isolation alternative |
| [Android application sandbox](https://source.android.com/docs/security/app-sandbox) | Mobile application isolation |
| [Android Verified Boot](https://source.android.com/docs/security/features/verifiedboot) | Device boot-integrity design |

The Fedora documentation landing page was access-blocked during research. The
recommendation uses accessible upstream bootc documentation and Fedora's project
wiki; a production base and desktop image have not been verified.

## Optimization and Security

| Source | Supports |
| --- | --- |
| [Linux cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html) | Hierarchical process/resource controls |
| [Linux sched_ext](https://docs.kernel.org/scheduler/sched-ext.html) | BPF CPU scheduling, fallback and scheduler-dependent semantics |
| [Linux seccomp](https://docs.kernel.org/userspace-api/seccomp_filter.html) | Syscall filtering and its limitations |
| [NIST adversarial-ML taxonomy, 2025 announcement](https://www.nist.gov/news-events/news/2025/03/nist-trustworthy-and-responsible-ai-report-adversarial-machine-learning) | Poisoning, evasion and privacy threats to learned systems |

## Commercial Context and Naming

- [Edge Impulse about page](https://www.edgeimpulse.com/about): company-reported acquisition by Qualcomm in March 2025; adjacent strategic activity only.
- [eCos project](https://ecos.sourceware.org/): established RTOS naming overlap.

No market size, price benchmark, investor interest or customer ROI has been verified.
Illustrations in [business impact](business-impact.md) are explicitly hypothetical.
