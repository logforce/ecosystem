// SPDX-License-Identifier: CC-BY-NC-ND-4.0
// ETE-X integration. See components/ete-x/LICENSE.md for scope and attribution.
//! Trusted Linux worker supervisor for one private ETE-X capability.
//!
//! This is process lifecycle management, not a security sandbox. Paths and
//! interpreter selection belong to the operator, never the request. JSON
//! semantics are validated by the Python worker; this layer validates framing.
use cog_backend_api::Backend;
use cog_core::{Error, Result, TensorDesc};
use std::{
    io::{self, Read, Write},
    os::unix::{fs::PermissionsExt, process::CommandExt},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex, MutexGuard, TryLockError,
    },
    thread,
    time::{Duration, Instant},
};

pub const INPUT_BYTES: usize = 4096;
pub const OUTPUT_BYTES: usize = 65536;
pub const CAPABILITY: &str = "etex.rag.search.v1";
const POLL: Duration = Duration::from_millis(2);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EteXEngine {
    Fixture,
    EteX,
}
impl EteXEngine {
    fn argument(self) -> &'static str {
        match self {
            Self::Fixture => "fixture",
            Self::EteX => "etex",
        }
    }
}

/// Explicit trusted-operator configuration. No inference defaults to fixture.
#[derive(Clone, Debug)]
pub struct EteXConfig {
    pub python: PathBuf,
    pub worker: PathBuf,
    pub engine: EteXEngine,
    pub snapshot: Option<PathBuf>,
    pub encoder_dir: Option<PathBuf>,
    pub reranker_dir: Option<PathBuf>,
    pub ete_x_root: Option<PathBuf>,
    pub model_file: String,
    pub reranker_model_file: String,
    pub threads: usize,
    pub strategy: String,
    pub no_reranker: bool,
    pub timeout: Duration,
}
impl EteXConfig {
    pub fn new(python: impl Into<PathBuf>, worker: impl Into<PathBuf>, engine: EteXEngine) -> Self {
        Self {
            python: python.into(),
            worker: worker.into(),
            engine,
            snapshot: None,
            encoder_dir: None,
            reranker_dir: None,
            ete_x_root: None,
            model_file: "model.fused.int8.full.onnx".into(),
            reranker_model_file: "model.fused.int8.full.onnx".into(),
            threads: 1,
            strategy: "auto".into(),
            no_reranker: false,
            timeout: Duration::from_secs(120),
        }
    }
}

fn bounded(cancelled: &AtomicBool, deadline: Instant) -> Result<()> {
    if cancelled.load(Ordering::Acquire) {
        return Err(Error::Cancelled);
    }
    if Instant::now() >= deadline {
        return Err(Error::Timeout);
    }
    Ok(())
}

/// Validate an envelope without inventing a second JSON parser in the ABI.
fn validate_envelope(bytes: &[u8], failure: Error) -> Result<()> {
    if bytes.len() < 4 {
        return Err(failure);
    }
    let size = u32::from_le_bytes(bytes[..4].try_into().map_err(|_| failure)?) as usize;
    if size == 0 || size > bytes.len() - 4 {
        return Err(failure);
    }
    std::str::from_utf8(&bytes[4..4 + size]).map_err(|_| failure)?;
    if bytes[4 + size..].iter().any(|byte| *byte != 0) {
        return Err(failure);
    }
    Ok(())
}

struct Worker {
    child: Child,
    next: u64,
}
impl Drop for Worker {
    fn drop(&mut self) {
        // Each worker starts a separate process group. Kill its ordinary child
        // processes as well as the direct child, then reap the direct child.
        // A malicious worker can escape a group: this is not confinement.
        unsafe extern "C" {
            fn kill(pid: i32, signal: i32) -> i32;
        }
        if let Ok(pid) = i32::try_from(self.child.id()) {
            unsafe {
                kill(-pid, 9);
            }
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
impl Worker {
    fn idle(&mut self) -> Result<()> {
        if self.child.try_wait().map_err(|_| Error::Backend)?.is_some() {
            return Err(Error::Backend);
        }
        thread::sleep(POLL);
        Ok(())
    }
    fn read_frame(
        &mut self,
        bytes: &mut [u8],
        cancelled: &AtomicBool,
        deadline: Instant,
    ) -> Result<()> {
        let mut offset = 0;
        while offset < bytes.len() {
            bounded(cancelled, deadline)?;
            match self
                .child
                .stdout
                .as_mut()
                .ok_or(Error::Backend)?
                .read(&mut bytes[offset..])
            {
                Ok(0) => return Err(Error::Backend),
                Ok(count) => offset += count,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => self.idle()?,
                Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
                Err(_) => return Err(Error::Backend),
            }
        }
        bounded(cancelled, deadline)
    }
    fn write_frame(
        &mut self,
        bytes: &[u8],
        cancelled: &AtomicBool,
        deadline: Instant,
    ) -> Result<()> {
        let mut offset = 0;
        while offset < bytes.len() {
            bounded(cancelled, deadline)?;
            match self
                .child
                .stdin
                .as_mut()
                .ok_or(Error::Backend)?
                .write(&bytes[offset..])
            {
                Ok(0) => return Err(Error::Backend),
                Ok(count) => offset += count,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => self.idle()?,
                Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
                Err(_) => return Err(Error::Backend),
            }
        }
        bounded(cancelled, deadline)
    }
}

pub struct EteXProcessBackend {
    config: EteXConfig,
    worker: Mutex<Option<Worker>>,
}
impl EteXProcessBackend {
    pub fn new(mut config: EteXConfig) -> io::Result<Self> {
        let invalid = |message| io::Error::new(io::ErrorKind::InvalidInput, message);
        // Preserve a virtual environment's interpreter symlink.
        if !config.python.is_absolute()
            || !config.python.is_file()
            || config.python.metadata()?.permissions().mode() & 0o111 == 0
        {
            return Err(invalid("ETE-X Python must be an absolute executable file"));
        }
        config.worker = config.worker.canonicalize()?;
        if !config.worker.is_file() {
            return Err(invalid("ETE-X worker must be a file"));
        }
        if config.threads == 0 || config.threads > 64 {
            return Err(invalid("ETE-X threads must be between 1 and 64"));
        }
        if config.timeout.is_zero() || config.timeout > Duration::from_secs(3600) {
            return Err(invalid(
                "ETE-X timeout must be positive and at most 3600000 ms",
            ));
        }
        if !["auto", "raw", "bounded"].contains(&config.strategy.as_str()) {
            return Err(invalid("ETE-X strategy must be auto, raw or bounded"));
        }
        for name in [&config.model_file, &config.reranker_model_file] {
            if name.is_empty()
                || name == "."
                || name == ".."
                || name.len() > 255
                || name.contains(['/', '\\', '\0'])
            {
                return Err(invalid("ETE-X model filenames must be basenames"));
            }
        }
        if config.engine == EteXEngine::EteX {
            if config.snapshot.is_none()
                || config.encoder_dir.is_none()
                || (!config.no_reranker && config.reranker_dir.is_none())
            {
                return Err(invalid("ETE-X mode requires snapshot and encoder directory; reranking also requires its model directory"));
            }
        } else if config.snapshot.is_some()
            || config.encoder_dir.is_some()
            || config.reranker_dir.is_some()
            || config.ete_x_root.is_some()
        {
            return Err(invalid(
                "Fixture mode must not specify corpus, source or model directories",
            ));
        }
        for target in [
            &mut config.snapshot,
            &mut config.encoder_dir,
            &mut config.reranker_dir,
            &mut config.ete_x_root,
        ] {
            if let Some(value) = target {
                *value = value.canonicalize()?;
                if !value.is_dir() {
                    return Err(invalid(
                        "ETE-X snapshot and source/model paths must be directories",
                    ));
                }
            }
        }
        Ok(Self {
            config,
            worker: Mutex::new(None),
        })
    }
    fn lock(
        &self,
        cancelled: &AtomicBool,
        deadline: Instant,
    ) -> Result<MutexGuard<'_, Option<Worker>>> {
        loop {
            bounded(cancelled, deadline)?;
            match self.worker.try_lock() {
                Ok(guard) => return Ok(guard),
                Err(TryLockError::Poisoned(_)) => return Err(Error::Backend),
                Err(TryLockError::WouldBlock) => thread::sleep(POLL),
            }
        }
    }
    fn spawn(&self, cancelled: &AtomicBool, deadline: Instant) -> Result<Worker> {
        bounded(cancelled, deadline)?;
        let c = &self.config;
        let mut command = Command::new(&c.python);
        command.args(["-I", "-u"]).arg(&c.worker).args([
            "--engine",
            c.engine.argument(),
            "--threads",
            &c.threads.to_string(),
            "--strategy",
            &c.strategy,
            "--model-file",
            &c.model_file,
            "--reranker-model-file",
            &c.reranker_model_file,
        ]);
        for (flag, value) in [
            ("--snapshot", &c.snapshot),
            ("--encoder-dir", &c.encoder_dir),
            ("--reranker-dir", &c.reranker_dir),
            ("--ete-x-root", &c.ete_x_root),
        ] {
            if let Some(value) = value {
                command.arg(flag).arg(value);
            }
        }
        if c.no_reranker {
            command.arg("--no-reranker");
        }
        let child = command
            .env_clear()
            .env("PATH", "/usr/bin:/bin")
            .env("OPENBLAS_NUM_THREADS", c.threads.to_string())
            .env("OMP_NUM_THREADS", c.threads.to_string())
            .env("TOKENIZERS_PARALLELISM", "false")
            .env("PYTHONDONTWRITEBYTECODE", "1")
            .current_dir("/")
            .process_group(0)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| Error::Backend)?;
        let mut worker = Worker { child, next: 1 };
        cog_shm::set_nonblocking(worker.child.stdin.as_ref().ok_or(Error::Backend)?)
            .map_err(|_| Error::Backend)?;
        cog_shm::set_nonblocking(worker.child.stdout.as_ref().ok_or(Error::Backend)?)
            .map_err(|_| Error::Backend)?;
        let mut ready = [0; 5];
        worker.read_frame(&mut ready, cancelled, deadline)?;
        if &ready != b"ETEW1" {
            return Err(Error::Backend);
        }
        Ok(worker)
    }
}
impl Backend for EteXProcessBackend {
    fn supports(&self, artifact: &str) -> bool {
        artifact == CAPABILITY
    }
    fn validate(&self, input: &TensorDesc, output: &TensorDesc, delay_ms: u32) -> Result<()> {
        if input.shape != [INPUT_BYTES as u64] || output.shape != [OUTPUT_BYTES as u64] {
            return Err(Error::Invalid);
        }
        if delay_ms != 0 {
            return Err(Error::Unsupported);
        }
        Ok(())
    }
    fn execute(&self, input: &[u8], delay_ms: u32, cancelled: &AtomicBool) -> Result<Vec<u8>> {
        if input.len() != INPUT_BYTES || delay_ms != 0 {
            return Err(Error::Invalid);
        }
        validate_envelope(input, Error::Invalid)?;
        let deadline = Instant::now() + self.config.timeout;
        let mut slot = self.lock(cancelled, deadline)?;
        let result = (|| {
            if slot.is_none() {
                *slot = Some(self.spawn(cancelled, deadline)?);
            }
            let worker = slot.as_mut().ok_or(Error::Backend)?;
            let seq = worker.next;
            worker.next = seq.checked_add(1).ok_or(Error::Backend)?;
            let mut request = Vec::with_capacity(8 + INPUT_BYTES);
            request.extend_from_slice(&seq.to_le_bytes());
            request.extend_from_slice(input);
            // 4104 bytes exceeds Linux PIPE_BUF. Handle partial writes and
            // backpressure explicitly; never use a blocking write_all here.
            worker.write_frame(&request, cancelled, deadline)?;
            let mut reply = vec![0; 16 + OUTPUT_BYTES];
            worker.read_frame(&mut reply, cancelled, deadline)?;
            if &reply[..4] != b"ETX1" || reply[4..12] != seq.to_le_bytes() {
                return Err(Error::Backend);
            }
            let code = u32::from_le_bytes(reply[12..16].try_into().map_err(|_| Error::Backend)?);
            validate_envelope(&reply[16..], Error::Backend)?;
            if code != 0 {
                return Err(i32::try_from(code)
                    .ok()
                    .and_then(Error::from_code)
                    .ok_or(Error::Backend)?);
            }
            bounded(cancelled, deadline)?;
            Ok(reply[16..].to_vec())
        })();
        // Error payloads are not committed to the client output by CogPOSIX.
        // A new, separately submitted job may create a fresh worker. No replay.
        if result.is_err() {
            slot.take();
        }
        result
    }
}

#[cfg(test)]
#[path = "etex_tests.rs"]
mod tests;
