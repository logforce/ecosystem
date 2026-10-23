// SPDX-License-Identifier: Apache-2.0
//! Linux process supervisor for the fixed MNIST capability. The worker executable
//! and paths are trusted administrator configuration, never supplied by IPC clients.
#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "linux")]
pub use linux::ProcessBackend;

// Optional separately licensed component. See components/ete-x/LICENSE.md.
// Default community builds do not compile or link this backend.
#[cfg(all(target_os = "linux", feature = "etex"))]
mod etex;
#[cfg(all(target_os = "linux", feature = "etex"))]
pub use etex::{EteXConfig, EteXEngine, EteXProcessBackend};
