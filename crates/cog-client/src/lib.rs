// SPDX-License-Identifier: Apache-2.0
//! Synchronous local control client; submitted computation is asynchronous.
mod ffi;
pub use cog_core::{Error, Handle, JobStatus, Result, Stats, TensorDesc, MAX_BUFFER, MAX_RANK};
use cog_protocol::{Decoder, Frame, Request, FEATURE_INLINE_MOCK, FEATURE_SEALED_SHM, RESPONSE};
#[cfg(target_os = "linux")]
pub use cog_shm::SharedBuffer;
use std::{
    net::Shutdown,
    os::unix::net::UnixStream,
    path::Path,
    thread,
    time::{Duration, Instant},
};

pub struct Client {
    stream: Option<UnixStream>,
    next: u64,
    process: u32,
    features: u64,
    traffic: (u64, u64),
}

impl Client {
    pub fn connect(socket: impl AsRef<Path>) -> Result<Self> {
        let stream = UnixStream::connect(socket).map_err(|_| Error::Io)?;
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .map_err(|_| Error::Io)?;
        stream
            .set_write_timeout(Some(Duration::from_secs(5)))
            .map_err(|_| Error::Io)?;
        let mut client = Self {
            stream: Some(stream),
            next: 1,
            process: std::process::id(),
            features: 0,
            traffic: (0, 0),
        };
        let body = client.request(Request::Hello)?;
        let mut d = Decoder::new(&body);
        client.features = d.u64()?;
        if client.features & FEATURE_INLINE_MOCK == 0
            || d.u32()? != MAX_BUFFER as u32
            || d.u32()? != MAX_RANK as u32
        {
            return Err(Error::Unsupported);
        }
        d.finish()?;
        Ok(client)
    }

    pub fn request(&mut self, request: Request) -> Result<Vec<u8>> {
        self.exchange(request, None).map(|(body, _)| body)
    }
    fn exchange(
        &mut self,
        request: Request,
        file: Option<&std::fs::File>,
    ) -> Result<(Vec<u8>, Option<std::fs::File>)> {
        if self.process != std::process::id() {
            return Err(Error::Disconnected);
        }
        let id = self.next;
        self.next = id.checked_add(1).ok_or(Error::Disconnected)?;
        let (opcode, body) = request.encode();
        let frame = Frame {
            id,
            opcode,
            flags: 0,
            status: 0,
            body,
        };
        let result = (|| {
            let stream = self.stream.as_mut().ok_or(Error::Disconnected)?;
            frame
                .write_socket(stream, file)
                .map_err(|_| Error::Disconnected)?;
            self.traffic.0 += (cog_protocol::HEADER_SIZE + frame.body.len()) as u64;
            let (reply, received) = Frame::read_socket(stream).map_err(|_| Error::Disconnected)?;
            self.traffic.1 += (cog_protocol::HEADER_SIZE + reply.body.len()) as u64;
            if reply.id != id || reply.opcode != opcode || reply.flags != RESPONSE {
                return Err(Error::Disconnected);
            }
            if reply.status != 0 {
                if !reply.body.is_empty() || received.is_some() {
                    return Err(Error::Disconnected);
                }
                return Err(Error::from_code(reply.status).ok_or(Error::Disconnected)?);
            }
            if received.is_some() != (opcode == 25) {
                return Err(Error::Disconnected);
            }
            Ok((reply.body, received))
        })();
        if matches!(result, Err(Error::Disconnected)) {
            if let Some(stream) = self.stream.take() {
                let _ = stream.shutdown(Shutdown::Both);
            }
        }
        result
    }
    pub fn features(&self) -> u64 {
        self.features
    }
    /// Successfully transferred application frame bytes, excluding SCM_RIGHTS metadata.
    pub fn traffic(&self) -> (u64, u64) {
        self.traffic
    }
    pub fn buffer_import_file(&mut self, file: &std::fs::File, size: usize) -> Result<Handle> {
        if !cfg!(target_os = "linux") || self.features & FEATURE_SEALED_SHM == 0 {
            return Err(Error::Unsupported);
        }
        if size == 0 || size > MAX_BUFFER {
            return Err(Error::Invalid);
        }
        let (body, _) = self.exchange(Request::BufferImport(size as u32), Some(file))?;
        let mut d = Decoder::new(&body);
        let handle = d.u64()?;
        d.finish()?;
        if handle == 0 {
            return Err(Error::Invalid);
        }
        Ok(handle)
    }
    pub fn buffer_export_file(&mut self, handle: Handle) -> Result<(std::fs::File, usize)> {
        if !cfg!(target_os = "linux") || self.features & FEATURE_SEALED_SHM == 0 {
            return Err(Error::Unsupported);
        }
        let (body, file) = self.exchange(Request::BufferExport(handle), None)?;
        let mut d = Decoder::new(&body);
        let size = d.u32()? as usize;
        d.finish()?;
        let file = file.ok_or(Error::Disconnected)?;
        #[cfg(target_os = "linux")]
        {
            let shared = SharedBuffer::from_file(file, size)?;
            Ok((shared.file().try_clone().map_err(|_| Error::Io)?, size))
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (file, size);
            Err(Error::Unsupported)
        }
    }
    fn handle(&mut self, request: Request) -> Result<Handle> {
        let body = self.request(request)?;
        let mut d = Decoder::new(&body);
        let h = d.u64()?;
        d.finish()?;
        if h == 0 {
            Err(Error::Invalid)
        } else {
            Ok(h)
        }
    }
    fn empty(&mut self, request: Request) -> Result<()> {
        if self.request(request)?.is_empty() {
            Ok(())
        } else {
            Err(Error::Invalid)
        }
    }
    pub fn model_open(&mut self, name: &str) -> Result<Handle> {
        if name.len() > 128 {
            return Err(Error::Invalid);
        }
        self.handle(Request::ModelOpen(name.to_owned()))
    }
    pub fn model_close(&mut self, model: Handle) -> Result<()> {
        self.empty(Request::ModelClose(model))
    }
    pub fn buffer_alloc(&mut self, size: usize) -> Result<Handle> {
        if size == 0 || size > MAX_BUFFER {
            return Err(Error::Invalid);
        }
        self.handle(Request::BufferAlloc(size as u32))
    }
    pub fn buffer_write(&mut self, buffer: Handle, data: &[u8]) -> Result<()> {
        if data.len() > MAX_BUFFER {
            return Err(Error::Invalid);
        }
        self.empty(Request::BufferWrite {
            buffer,
            data: data.to_vec(),
        })
    }
    pub fn buffer_read(&mut self, buffer: Handle) -> Result<Vec<u8>> {
        self.request(Request::BufferRead(buffer))
    }
    pub fn buffer_free(&mut self, buffer: Handle) -> Result<()> {
        self.empty(Request::BufferFree(buffer))
    }
    pub fn tensor_create(&mut self, buffer: Handle, desc: TensorDesc) -> Result<Handle> {
        if desc.shape.is_empty() || desc.shape.len() > MAX_RANK {
            return Err(Error::Invalid);
        }
        self.handle(Request::TensorCreate { buffer, desc })
    }
    pub fn tensor_release(&mut self, tensor: Handle) -> Result<()> {
        self.empty(Request::TensorRelease(tensor))
    }
    pub fn submit(
        &mut self,
        model: Handle,
        input: Handle,
        output: Handle,
        delay_ms: u32,
    ) -> Result<Handle> {
        self.handle(Request::Submit {
            model,
            input,
            output,
            delay_ms,
        })
    }
    pub fn job_status(&mut self, job: Handle) -> Result<(JobStatus, Option<Error>)> {
        let body = self.request(Request::JobStatus(job))?;
        let mut d = Decoder::new(&body);
        let status = JobStatus::from_code(d.u32()?)?;
        let code = d.u32()? as i32;
        d.finish()?;
        let error = if code == 0 {
            None
        } else {
            Some(Error::from_code(code).ok_or(Error::Invalid)?)
        };
        Ok((status, error))
    }
    pub fn job_cancel(&mut self, job: Handle) -> Result<()> {
        self.empty(Request::JobCancel(job))
    }
    pub fn job_release(&mut self, job: Handle) -> Result<()> {
        self.empty(Request::JobRelease(job))
    }
    pub fn wait(&mut self, job: Handle, timeout: Duration) -> Result<JobStatus> {
        wait_with(timeout, || self.job_status(job))
    }
    pub fn stats(&mut self) -> Result<Stats> {
        let body = self.request(Request::Stats)?;
        let mut d = Decoder::new(&body);
        let stats = Stats {
            objects: d.u32()?,
            live_bytes: d.u64()?,
            jobs: d.u32()?,
        };
        d.finish()?;
        Ok(stats)
    }
}

fn wait_with(
    timeout: Duration,
    mut poll: impl FnMut() -> Result<(JobStatus, Option<Error>)>,
) -> Result<JobStatus> {
    let start = Instant::now();
    loop {
        let (status, error) = poll()?;
        if status.terminal() {
            return error.map_or(Ok(status), Err);
        }
        if start.elapsed() >= timeout {
            return Err(Error::Timeout);
        }
        thread::sleep(Duration::from_millis(2).min(timeout.saturating_sub(start.elapsed())));
    }
}
