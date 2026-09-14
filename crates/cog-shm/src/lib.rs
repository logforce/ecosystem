// SPDX-License-Identifier: Apache-2.0
//! Immutable, size-sealed Linux shared buffers and descriptor-aware stream I/O.
#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "linux")]
pub use linux::*;
