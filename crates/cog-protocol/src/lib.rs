// SPDX-License-Identifier: Apache-2.0
//! Explicit little-endian, length-bounded Unix socket framing. No native structs on wire.
use cog_core::{Error, Handle, Result, TensorDesc, MAX_BUFFER, MAX_RANK};
use std::io::{self, Read, Write};

pub const MAJOR: u16 = 1;
pub const MINOR: u16 = 0;
pub const HEADER_SIZE: usize = 28;
pub const MAX_PAYLOAD: usize = MAX_BUFFER + 128;
pub const RESPONSE: u16 = 1;
pub const FEATURE_INLINE_MOCK: u64 = 1;

#[derive(Debug)]
pub struct Frame {
    pub id: u64,
    pub opcode: u16,
    pub flags: u16,
    pub status: i32,
    pub body: Vec<u8>,
}

fn invalid() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, "invalid CogPOSIX frame")
}

impl Frame {
    pub fn read(reader: &mut impl Read) -> io::Result<Self> {
        let mut h = [0u8; HEADER_SIZE];
        reader.read_exact(&mut h)?;
        if &h[0..4] != b"COGP"
            || u16::from_le_bytes(h[4..6].try_into().unwrap()) != MAJOR
            || u16::from_le_bytes(h[6..8].try_into().unwrap()) != MINOR
        {
            return Err(invalid());
        }
        let len = u32::from_le_bytes(h[20..24].try_into().unwrap()) as usize;
        if len > MAX_PAYLOAD {
            return Err(invalid());
        }
        let flags = u16::from_le_bytes(h[18..20].try_into().unwrap());
        if flags & !RESPONSE != 0 {
            return Err(invalid());
        }
        let mut body = vec![0; len];
        reader.read_exact(&mut body)?;
        Ok(Self {
            id: u64::from_le_bytes(h[8..16].try_into().unwrap()),
            opcode: u16::from_le_bytes(h[16..18].try_into().unwrap()),
            flags,
            status: i32::from_le_bytes(h[24..28].try_into().unwrap()),
            body,
        })
    }

    pub fn write(&self, writer: &mut impl Write) -> io::Result<()> {
        if self.body.len() > MAX_PAYLOAD {
            return Err(invalid());
        }
        let mut h = Vec::with_capacity(HEADER_SIZE);
        h.extend_from_slice(b"COGP");
        h.extend_from_slice(&MAJOR.to_le_bytes());
        h.extend_from_slice(&MINOR.to_le_bytes());
        h.extend_from_slice(&self.id.to_le_bytes());
        h.extend_from_slice(&self.opcode.to_le_bytes());
        h.extend_from_slice(&self.flags.to_le_bytes());
        h.extend_from_slice(&(self.body.len() as u32).to_le_bytes());
        h.extend_from_slice(&self.status.to_le_bytes());
        writer.write_all(&h)?;
        writer.write_all(&self.body)?;
        writer.flush()
    }
}

#[derive(Debug, Clone)]
pub enum Request {
    Hello,
    Stats,
    ModelOpen(String),
    ModelClose(Handle),
    BufferAlloc(u32),
    BufferWrite {
        buffer: Handle,
        data: Vec<u8>,
    },
    BufferRead(Handle),
    BufferFree(Handle),
    TensorCreate {
        buffer: Handle,
        desc: TensorDesc,
    },
    TensorRelease(Handle),
    Submit {
        model: Handle,
        input: Handle,
        output: Handle,
        delay_ms: u32,
    },
    JobStatus(Handle),
    JobCancel(Handle),
    JobRelease(Handle),
}

pub fn put32(out: &mut Vec<u8>, value: u32) {
    out.extend_from_slice(&value.to_le_bytes());
}
pub fn put64(out: &mut Vec<u8>, value: u64) {
    out.extend_from_slice(&value.to_le_bytes());
}

pub struct Decoder<'a> {
    data: &'a [u8],
    offset: usize,
}
impl<'a> Decoder<'a> {
    pub fn new(data: &'a [u8]) -> Self {
        Self { data, offset: 0 }
    }
    pub fn take(&mut self, size: usize) -> Result<&'a [u8]> {
        let end = self.offset.checked_add(size).ok_or(Error::Invalid)?;
        let data = self.data.get(self.offset..end).ok_or(Error::Invalid)?;
        self.offset = end;
        Ok(data)
    }
    pub fn u32(&mut self) -> Result<u32> {
        Ok(u32::from_le_bytes(self.take(4)?.try_into().unwrap()))
    }
    pub fn u64(&mut self) -> Result<u64> {
        Ok(u64::from_le_bytes(self.take(8)?.try_into().unwrap()))
    }
    pub fn finish(self) -> Result<()> {
        if self.offset == self.data.len() {
            Ok(())
        } else {
            Err(Error::Invalid)
        }
    }
}

impl Request {
    pub fn encode(&self) -> (u16, Vec<u8>) {
        let mut b = Vec::new();
        let op = match self {
            Self::Hello => 1,
            Self::Stats => 2,
            Self::ModelOpen(name) => {
                put32(&mut b, name.len() as u32);
                b.extend_from_slice(name.as_bytes());
                10
            }
            Self::ModelClose(h) => {
                put64(&mut b, *h);
                11
            }
            Self::BufferAlloc(n) => {
                put32(&mut b, *n);
                20
            }
            Self::BufferWrite { buffer, data } => {
                put64(&mut b, *buffer);
                put32(&mut b, data.len() as u32);
                b.extend_from_slice(data);
                21
            }
            Self::BufferRead(h) => {
                put64(&mut b, *h);
                22
            }
            Self::BufferFree(h) => {
                put64(&mut b, *h);
                23
            }
            Self::TensorCreate { buffer, desc } => {
                put64(&mut b, *buffer);
                put64(&mut b, desc.offset);
                put32(&mut b, desc.shape.len() as u32);
                for dim in &desc.shape {
                    put64(&mut b, *dim);
                }
                30
            }
            Self::TensorRelease(h) => {
                put64(&mut b, *h);
                31
            }
            Self::Submit {
                model,
                input,
                output,
                delay_ms,
            } => {
                for h in [model, input, output] {
                    put64(&mut b, *h);
                }
                put32(&mut b, *delay_ms);
                40
            }
            Self::JobStatus(h) => {
                put64(&mut b, *h);
                41
            }
            Self::JobCancel(h) => {
                put64(&mut b, *h);
                42
            }
            Self::JobRelease(h) => {
                put64(&mut b, *h);
                43
            }
        };
        (op, b)
    }

    pub fn decode(op: u16, bytes: &[u8]) -> Result<Self> {
        let mut d = Decoder::new(bytes);
        let request = match op {
            1 => Self::Hello,
            2 => Self::Stats,
            10 => {
                let n = d.u32()? as usize;
                if n > 128 {
                    return Err(Error::Invalid);
                }
                Self::ModelOpen(
                    std::str::from_utf8(d.take(n)?)
                        .map_err(|_| Error::Invalid)?
                        .to_owned(),
                )
            }
            11 => Self::ModelClose(d.u64()?),
            20 => Self::BufferAlloc(d.u32()?),
            21 => {
                let buffer = d.u64()?;
                let n = d.u32()? as usize;
                if n > MAX_BUFFER {
                    return Err(Error::Invalid);
                }
                Self::BufferWrite {
                    buffer,
                    data: d.take(n)?.to_vec(),
                }
            }
            22 => Self::BufferRead(d.u64()?),
            23 => Self::BufferFree(d.u64()?),
            30 => {
                let buffer = d.u64()?;
                let offset = d.u64()?;
                let rank = d.u32()? as usize;
                if rank == 0 || rank > MAX_RANK {
                    return Err(Error::Invalid);
                }
                let mut shape = Vec::with_capacity(rank);
                for _ in 0..rank {
                    shape.push(d.u64()?);
                }
                Self::TensorCreate {
                    buffer,
                    desc: TensorDesc { offset, shape },
                }
            }
            31 => Self::TensorRelease(d.u64()?),
            40 => Self::Submit {
                model: d.u64()?,
                input: d.u64()?,
                output: d.u64()?,
                delay_ms: d.u32()?,
            },
            41 => Self::JobStatus(d.u64()?),
            42 => Self::JobCancel(d.u64()?),
            43 => Self::JobRelease(d.u64()?),
            _ => return Err(Error::Unsupported),
        };
        d.finish()?;
        Ok(request)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_bad_frames_before_payload_allocation() {
        let frame = Frame {
            id: 1,
            opcode: 1,
            flags: 0,
            status: 0,
            body: vec![],
        };
        let mut bytes = vec![];
        frame.write(&mut bytes).unwrap();
        for (offset, replacement) in [(0, 0), (4, 2), (6, 1), (18, 2)] {
            let mut bad = bytes.clone();
            bad[offset] = replacement;
            assert!(Frame::read(&mut bad.as_slice()).is_err());
        }
        bytes[20..24].copy_from_slice(&u32::MAX.to_le_bytes());
        assert!(Frame::read(&mut bytes.as_slice()).is_err());
    }
    #[test]
    fn rejects_truncation_and_trailing_fields() {
        assert!(Request::decode(1, &[0]).is_err());
        assert!(Request::decode(40, &[0; 12]).is_err());
        assert!(Frame::read(&mut &[0; 27][..]).is_err());
        assert!(matches!(Request::decode(999, &[]), Err(Error::Unsupported)));
    }
}
