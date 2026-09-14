// SPDX-License-Identifier: Apache-2.0
use cog_backend_api::Backend;
use cog_core::{Error, Result, MAX_DELAY_MS};
use std::{
    sync::atomic::{AtomicBool, Ordering},
    thread,
    time::{Duration, Instant},
};

pub const MODEL: &str = "mock.increment.v1";
pub struct MockBackend;

impl Backend for MockBackend {
    fn supports(&self, artifact: &str) -> bool {
        artifact == MODEL
    }
    fn execute(&self, input: &[u8], delay_ms: u32, cancelled: &AtomicBool) -> Result<Vec<u8>> {
        if delay_ms > MAX_DELAY_MS {
            return Err(Error::Invalid);
        }
        let end = Instant::now() + Duration::from_millis(delay_ms.into());
        loop {
            if cancelled.load(Ordering::Acquire) {
                return Err(Error::Cancelled);
            }
            let remaining = end.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                break;
            }
            thread::sleep(remaining.min(Duration::from_millis(2)));
        }
        Ok(input.iter().map(|byte| byte.wrapping_add(1)).collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reproducible_wrapping_and_cancellation() {
        assert_eq!(
            MockBackend.execute(&[0, 254, 255], 0, &AtomicBool::new(false)),
            Ok(vec![1, 255, 0])
        );
        assert_eq!(
            MockBackend.execute(&[0], 0, &AtomicBool::new(true)),
            Err(Error::Cancelled)
        );
    }
}
