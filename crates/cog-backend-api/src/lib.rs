// SPDX-License-Identifier: Apache-2.0
//! Internal Rust contract only; not a stable dynamic-plugin ABI.
use cog_core::Result;
use std::sync::atomic::AtomicBool;

pub trait Backend: Send + Sync + 'static {
    fn supports(&self, artifact: &str) -> bool;
    fn execute(&self, input: &[u8], delay_ms: u32, cancelled: &AtomicBool) -> Result<Vec<u8>>;
}
