// SPDX-License-Identifier: Apache-2.0
//! Experimental core values shared by the runtime, protocol and clients.
use std::fmt;

pub type Handle = u64;
pub const MAX_BUFFER: usize = 1024 * 1024;
pub const MAX_RANK: usize = 8;
pub const MAX_DELAY_MS: u32 = 5_000;
pub const MAX_OBJECTS: usize = 128;
pub const MAX_JOBS: usize = 16;
pub const MAX_SESSIONS: usize = 32;
pub const SESSION_MEMORY: usize = 8 * MAX_BUFFER;
pub const GLOBAL_MEMORY: usize = 64 * MAX_BUFFER;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i32)]
pub enum Error {
    Invalid = 1,
    NoMemory = 2,
    NoModel = 3,
    Busy = 4,
    Timeout = 5,
    Cancelled = 6,
    Permission = 7,
    Backend = 8,
    Io = 9,
    Unsupported = 10,
    Disconnected = 11,
}

impl Error {
    pub fn from_code(code: i32) -> Option<Self> {
        Some(match code {
            1 => Self::Invalid,
            2 => Self::NoMemory,
            3 => Self::NoModel,
            4 => Self::Busy,
            5 => Self::Timeout,
            6 => Self::Cancelled,
            7 => Self::Permission,
            8 => Self::Backend,
            9 => Self::Io,
            10 => Self::Unsupported,
            11 => Self::Disconnected,
            _ => return None,
        })
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{self:?}")
    }
}
impl std::error::Error for Error {}
pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum JobStatus {
    Queued = 1,
    Running = 2,
    Completed = 3,
    Failed = 4,
    Cancelled = 5,
}

impl JobStatus {
    pub fn terminal(self) -> bool {
        matches!(self, Self::Completed | Self::Failed | Self::Cancelled)
    }
    pub fn from_code(code: u32) -> Result<Self> {
        match code {
            1 => Ok(Self::Queued),
            2 => Ok(Self::Running),
            3 => Ok(Self::Completed),
            4 => Ok(Self::Failed),
            5 => Ok(Self::Cancelled),
            _ => Err(Error::Invalid),
        }
    }
}

/// R1 supports contiguous U8 only. Offset and extents are measured in bytes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TensorDesc {
    pub offset: u64,
    pub shape: Vec<u64>,
}

impl TensorDesc {
    pub fn extent(&self, capacity: usize) -> Result<std::ops::Range<usize>> {
        if self.shape.is_empty() || self.shape.len() > MAX_RANK {
            return Err(Error::Invalid);
        }
        let mut count = 1u64;
        for &dim in &self.shape {
            if dim == 0 {
                return Err(Error::Invalid);
            }
            count = count.checked_mul(dim).ok_or(Error::Invalid)?;
        }
        let end = self.offset.checked_add(count).ok_or(Error::Invalid)?;
        if end > capacity as u64 {
            return Err(Error::Invalid);
        }
        Ok(self.offset as usize..end as usize)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Stats {
    pub objects: u32,
    pub live_bytes: u64,
    pub jobs: u32,
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn tensor_extents_are_checked() {
        assert_eq!(
            TensorDesc {
                offset: 2,
                shape: vec![2, 3]
            }
            .extent(8),
            Ok(2..8)
        );
        for desc in [
            TensorDesc {
                offset: u64::MAX,
                shape: vec![1],
            },
            TensorDesc {
                offset: 0,
                shape: vec![u64::MAX, 2],
            },
            TensorDesc {
                offset: 0,
                shape: vec![],
            },
            TensorDesc {
                offset: 0,
                shape: vec![0],
            },
            TensorDesc {
                offset: 0,
                shape: vec![9],
            },
        ] {
            assert_eq!(desc.extent(8), Err(Error::Invalid));
        }
    }
}
