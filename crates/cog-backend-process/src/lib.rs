// SPDX-License-Identifier: Apache-2.0
//! Linux process supervisor for the fixed MNIST capability. The worker executable
//! and paths are trusted administrator configuration, never supplied by IPC clients.
#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "linux")]
pub use linux::ProcessBackend;
