// SPDX-License-Identifier: Apache-2.0
//! Internal Rust contract only; not a stable dynamic-plugin ABI.
use cog_core::{Error, Result, TensorDesc};
use std::sync::atomic::AtomicBool;

pub trait Backend: Send + Sync + 'static {
    fn supports(&self, artifact: &str) -> bool;
    fn validate(&self, input: &TensorDesc, output: &TensorDesc, _delay_ms: u32) -> Result<()> {
        if input.shape != output.shape {
            return Err(Error::Invalid);
        }
        Ok(())
    }
    fn execute(&self, input: &[u8], delay_ms: u32, cancelled: &AtomicBool) -> Result<Vec<u8>>;
}
