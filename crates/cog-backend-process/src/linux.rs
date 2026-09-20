// SPDX-License-Identifier: Apache-2.0
use cog_backend_api::Backend;
use cog_core::{Error, Result, TensorDesc};
use std::{
    io::{self, Read, Write},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    thread,
    time::{Duration, Instant},
};

struct Worker {
    child: Child,
    next: u64,
}
impl Drop for Worker {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
impl Worker {
    fn read(&mut self, bytes: &mut [u8], cancelled: &AtomicBool, deadline: Instant) -> Result<()> {
        let mut offset = 0;
        while offset < bytes.len() {
            if cancelled.load(Ordering::Acquire) {
                return Err(Error::Cancelled);
            }
            if Instant::now() >= deadline {
                return Err(Error::Timeout);
            }
            match self
                .child
                .stdout
                .as_mut()
                .ok_or(Error::Backend)?
                .read(&mut bytes[offset..])
            {
                Ok(0) => return Err(Error::Backend),
                Ok(n) => offset += n,
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => {
                    if self.child.try_wait().map_err(|_| Error::Backend)?.is_some() {
                        return Err(Error::Backend);
                    }
                    thread::sleep(Duration::from_millis(2));
                }
                Err(e) if e.kind() == io::ErrorKind::Interrupted => {}
                Err(_) => return Err(Error::Backend),
            }
        }
        Ok(())
    }
}

pub struct ProcessBackend {
    python: PathBuf,
    script: PathBuf,
    model: PathBuf,
    worker: Mutex<Option<Worker>>,
    timeout: Duration,
}
impl ProcessBackend {
    pub fn new(
        python: impl AsRef<Path>,
        script: impl AsRef<Path>,
        model: impl AsRef<Path>,
    ) -> io::Result<Self> {
        if !python.as_ref().is_absolute() || !python.as_ref().is_file() {
            return Err(io::Error::other(
                "worker interpreter must be an absolute executable path",
            ));
        }
        Ok(Self {
            // Preserve the interpreter symlink: resolving a venv's python bypasses that venv.
            python: python.as_ref().to_owned(),
            script: script.as_ref().canonicalize()?,
            model: model.as_ref().canonicalize()?,
            worker: Mutex::new(None),
            timeout: Duration::from_secs(10),
        })
    }
    fn spawn(&self, cancelled: &AtomicBool, deadline: Instant) -> Result<Worker> {
        let child = Command::new(&self.python)
            .args(["-I", "-u"])
            .arg(&self.script)
            .arg(&self.model)
            .arg(std::process::id().to_string())
            .env_clear()
            .env("OPENBLAS_NUM_THREADS", "1")
            .env("OMP_NUM_THREADS", "1")
            .current_dir("/")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| Error::Backend)?;
        let mut worker = Worker { child, next: 1 };
        cog_shm::set_nonblocking(worker.child.stdout.as_ref().ok_or(Error::Backend)?)
            .map_err(|_| Error::Backend)?;
        cog_shm::set_nonblocking(worker.child.stdin.as_ref().ok_or(Error::Backend)?)
            .map_err(|_| Error::Backend)?;
        let mut ready = [0; 5];
        worker.read(&mut ready, cancelled, deadline)?;
        if &ready != b"COGW1" {
            return Err(Error::Backend);
        }
        Ok(worker)
    }
}
impl Backend for ProcessBackend {
    fn supports(&self, artifact: &str) -> bool {
        artifact == "vision.mnist.v1"
    }
    fn validate(&self, input: &TensorDesc, output: &TensorDesc, delay_ms: u32) -> Result<()> {
        if input.shape != [28, 28] || output.shape != [1] {
            return Err(Error::Invalid);
        }
        if delay_ms != 0 {
            return Err(Error::Unsupported);
        }
        Ok(())
    }
    fn execute(&self, input: &[u8], delay_ms: u32, cancelled: &AtomicBool) -> Result<Vec<u8>> {
        if input.len() != 784 || delay_ms != 0 {
            return Err(Error::Invalid);
        }
        if cancelled.load(Ordering::Acquire) {
            return Err(Error::Cancelled);
        }
        let deadline = Instant::now() + self.timeout;
        let mut slot = self.worker.lock().map_err(|_| Error::Backend)?;
        let result = (|| {
            if slot.is_none() {
                *slot = Some(self.spawn(cancelled, deadline)?);
            }
            let worker = slot.as_mut().unwrap();
            let id = worker.next;
            worker.next = id.checked_add(1).ok_or(Error::Backend)?;
            let mut request = id.to_le_bytes().to_vec();
            request.extend_from_slice(input);
            // Requests are smaller than PIPE_BUF; nonblocking I/O also bounds a
            // broken worker that stops consuming input. No request retry is performed.
            worker
                .child
                .stdin
                .as_mut()
                .ok_or(Error::Backend)?
                .write_all(&request)
                .map_err(|_| Error::Backend)?;
            let mut reply = [0; 13];
            worker.read(&mut reply, cancelled, deadline)?;
            if &reply[..4] != b"DIG1" || reply[4..12] != id.to_le_bytes() || reply[12] > 9 {
                return Err(Error::Backend);
            }
            Ok(vec![reply[12]])
        })();
        if result.is_err() {
            slot.take();
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        os::unix::fs::PermissionsExt,
        sync::{atomic::AtomicU64, Arc},
    };
    static NEXT: AtomicU64 = AtomicU64::new(0);
    // Parallel fixture writes and process spawns can inherit a writable script
    // descriptor before exec closes it, causing Linux ETXTBSY in another test.
    static FIXTURE_LOCK: Mutex<()> = Mutex::new(());
    struct Fixture {
        backend: Arc<ProcessBackend>,
        directory: PathBuf,
    }
    impl Fixture {
        fn new(body: &str, timeout: Duration) -> Self {
            let directory = std::env::temp_dir().join(format!(
                "cog-process-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&directory).unwrap();
            let executable = directory.join("fake-python");
            fs::write(&executable, format!("#!/bin/sh\n{body}\n")).unwrap();
            fs::set_permissions(&executable, fs::Permissions::from_mode(0o700)).unwrap();
            let mut backend = ProcessBackend::new(&executable, &executable, &executable).unwrap();
            backend.timeout = timeout;
            Self {
                backend: Arc::new(backend),
                directory,
            }
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            self.backend.worker.lock().unwrap().take();
            fs::remove_dir_all(&self.directory).unwrap();
        }
    }
    #[test]
    fn crash_is_not_retried_and_next_job_starts_a_fresh_worker() {
        let _fixture_guard = FIXTURE_LOCK.lock().unwrap();
        let f = Fixture::new(
            "printf COGW1\nwhile /usr/bin/head -c 792 >/dev/null; do printf 'DIG1\\001\\000\\000\\000\\000\\000\\000\\000\\003'; done",
            Duration::from_secs(2),
        );
        let cancel = AtomicBool::new(false);
        assert_eq!(f.backend.execute(&[0; 784], 0, &cancel), Ok(vec![3]));
        let old = {
            let mut slot = f.backend.worker.lock().unwrap();
            let worker = slot.as_mut().unwrap();
            worker.child.kill().unwrap();
            worker.child.wait().unwrap();
            worker.child.id()
        };
        assert_eq!(
            f.backend.execute(&[0; 784], 0, &cancel),
            Err(Error::Backend)
        );
        assert!(f.backend.worker.lock().unwrap().is_none());
        assert_eq!(f.backend.execute(&[0; 784], 0, &cancel), Ok(vec![3]));
        assert_ne!(
            f.backend
                .worker
                .lock()
                .unwrap()
                .as_ref()
                .unwrap()
                .child
                .id(),
            old
        );
    }
    #[test]
    fn startup_and_inference_deadlines_reap_the_child() {
        let _fixture_guard = FIXTURE_LOCK.lock().unwrap();
        for body in ["exec /bin/sleep 60", "printf COGW1\nexec /bin/sleep 60"] {
            let f = Fixture::new(body, Duration::from_millis(100));
            assert_eq!(
                f.backend.execute(&[0; 784], 0, &AtomicBool::new(false)),
                Err(Error::Timeout)
            );
            assert!(f.backend.worker.lock().unwrap().is_none());
        }
    }
    #[test]
    fn cancellation_and_malformed_responses_discard_workers() {
        let _fixture_guard = FIXTURE_LOCK.lock().unwrap();
        let f = Fixture::new("printf COGW1\nexec /bin/sleep 60", Duration::from_secs(2));
        let cancelled = Arc::new(AtomicBool::new(false));
        let (backend, flag) = (f.backend.clone(), cancelled.clone());
        let task = thread::spawn(move || backend.execute(&[0; 784], 0, &flag));
        thread::sleep(Duration::from_millis(30));
        cancelled.store(true, Ordering::Release);
        assert_eq!(task.join().unwrap(), Err(Error::Cancelled));
        assert!(f.backend.worker.lock().unwrap().is_none());
        for body in [
            "printf BAD!!",
            "printf COGW1\n/usr/bin/head -c 792 >/dev/null\nprintf 'DIG1\\001\\000\\000\\000\\000\\000\\000\\000\\377'",
            "printf COGW1\n/usr/bin/head -c 792 >/dev/null\nprintf 'DIG1\\002\\000\\000\\000\\000\\000\\000\\000\\003'",
            "printf COGW1\nexit 1",
        ] {
            let f = Fixture::new(body, Duration::from_secs(2));
            assert_eq!(
                f.backend.execute(&[0; 784], 0, &AtomicBool::new(false)),
                Err(Error::Backend)
            );
            assert!(f.backend.worker.lock().unwrap().is_none());
        }
    }
    #[test]
    fn capability_has_explicit_shapes_and_no_mock_delay() {
        let _fixture_guard = FIXTURE_LOCK.lock().unwrap();
        let f = Fixture::new("exit 1", Duration::from_secs(2));
        let input = TensorDesc {
            offset: 0,
            shape: vec![28, 28],
        };
        let output = TensorDesc {
            offset: 0,
            shape: vec![1],
        };
        assert!(f.backend.supports("vision.mnist.v1"));
        assert!(!f.backend.supports("/tmp/arbitrary.onnx"));
        assert_eq!(f.backend.validate(&input, &output, 0), Ok(()));
        assert_eq!(
            f.backend.validate(&input, &output, 1),
            Err(Error::Unsupported)
        );
        assert_eq!(f.backend.validate(&input, &input, 0), Err(Error::Invalid));
    }
}
