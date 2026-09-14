// SPDX-License-Identifier: Apache-2.0
#[cfg(not(any(target_os = "linux", target_os = "macos")))]
compile_error!("R1 supports Linux and macOS Unix sockets only");
mod platform;
mod runtime;
use cog_backend_api::Backend;
use cog_backend_mock::MockBackend;
use cog_protocol::{Frame, Request, RESPONSE};
pub use runtime::{Metrics, Runtime};
use std::{
    fs, io,
    os::unix::{
        fs::{DirBuilderExt, FileTypeExt, MetadataExt, PermissionsExt},
        net::{UnixListener, UnixStream},
    },
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread,
    time::Duration,
};

fn serve(mut stream: UnixStream, runtime: Arc<Runtime>, backend: Arc<dyn Backend>, owner: u64) {
    // macOS accepted sockets can inherit O_NONBLOCK from the listening socket.
    if stream.set_nonblocking(false).is_err() {
        runtime.disconnect(owner);
        return;
    }
    let _ = stream.set_read_timeout(Some(Duration::from_secs(120)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));
    let mut last_id = 0;
    let mut greeted = false;
    while let Ok((frame, file)) = Frame::read_socket(&mut stream) {
        if frame.flags != 0 || frame.status != 0 || frame.id <= last_id {
            break;
        }
        last_id = frame.id;
        let request = Request::decode(frame.opcode, &frame.body);
        if file.is_some() && !matches!(request, Ok(Request::BufferImport(_))) {
            break;
        }
        if !greeted && !matches!(request, Ok(Request::Hello)) {
            break;
        }
        if greeted && matches!(request, Ok(Request::Hello)) {
            break;
        }
        #[allow(unused_mut)]
        let mut output_file = None;
        let result = request.and_then(|request| {
            #[cfg(target_os = "linux")]
            match request {
                Request::BufferImport(size) => {
                    return runtime.import(owner, file.ok_or(cog_core::Error::Invalid)?, size)
                }
                Request::BufferExport(handle) => {
                    let shared = runtime.export(owner, handle)?;
                    let body = (shared.bytes().len() as u32).to_le_bytes().to_vec();
                    output_file = Some(shared.file().try_clone().map_err(|_| cog_core::Error::Io)?);
                    return Ok(body);
                }
                _ => {}
            }
            runtime.request(owner, request, backend.as_ref())
        });
        let (status, body) = match result {
            Ok(body) => (0, body),
            Err(error) => (error as i32, vec![]),
        };
        let response = Frame {
            id: frame.id,
            opcode: frame.opcode,
            flags: RESPONSE,
            status,
            body,
        };
        if response
            .write_socket(&mut stream, output_file.as_ref())
            .is_err()
        {
            break;
        }
        greeted = true;
    }
    runtime.disconnect(owner);
}

pub struct Server {
    runtime: Arc<Runtime>,
    stop: Arc<AtomicBool>,
    connections: Arc<Mutex<Vec<(u64, UnixStream)>>>,
    accept: Option<thread::JoinHandle<()>>,
    worker: Option<thread::JoinHandle<()>>,
    socket: PathBuf,
    socket_identity: (u64, u64),
}

impl Server {
    pub fn bind(socket: impl AsRef<Path>) -> io::Result<Self> {
        Self::with_backend(socket, Arc::new(MockBackend))
    }
    pub fn with_backend(socket: impl AsRef<Path>, backend: Arc<dyn Backend>) -> io::Result<Self> {
        let socket = socket.as_ref().to_owned();
        let parent = socket
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .ok_or_else(|| io::Error::other("socket needs a private parent directory"))?;
        match fs::DirBuilder::new().mode(0o700).create(parent) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(error),
        }
        let metadata = fs::symlink_metadata(parent)?;
        if !metadata.is_dir()
            || metadata.uid() != platform::effective_uid()
            || metadata.mode() & 0o077 != 0
        {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "socket parent must be an owned 0700 directory",
            ));
        }
        // Never unlink an existing path: it may belong to another live daemon or the user.
        let listener = UnixListener::bind(&socket)?;
        fs::set_permissions(&socket, fs::Permissions::from_mode(0o600))?;
        let socket_metadata = fs::symlink_metadata(&socket)?;
        let socket_identity = (socket_metadata.dev(), socket_metadata.ino());
        listener.set_nonblocking(true)?;
        let (runtime, worker) = Runtime::start(backend.clone());
        let stop = Arc::new(AtomicBool::new(false));
        let connections = Arc::new(Mutex::new(Vec::<(u64, UnixStream)>::new()));
        let (r, stopped, clients) = (runtime.clone(), stop.clone(), connections.clone());
        let accept = thread::spawn(move || {
            let mut handlers: Vec<thread::JoinHandle<()>> = Vec::new();
            while !stopped.load(Ordering::Acquire) {
                let mut i = 0;
                while i < handlers.len() {
                    if handlers[i].is_finished() {
                        let _ = handlers.swap_remove(i).join();
                    } else {
                        i += 1;
                    }
                }
                match listener.accept() {
                    Ok((stream, _)) => {
                        if platform::peer_uid(&stream).ok() != Some(platform::effective_uid()) {
                            continue;
                        }
                        let Ok(owner) = r.connect() else {
                            continue;
                        };
                        let Ok(control) = stream.try_clone() else {
                            r.disconnect(owner);
                            continue;
                        };
                        clients.lock().unwrap().push((owner, control));
                        let (r, backend, clients) = (r.clone(), backend.clone(), clients.clone());
                        handlers.push(thread::spawn(move || {
                            serve(stream, r, backend, owner);
                            clients.lock().unwrap().retain(|(id, _)| *id != owner);
                        }));
                    }
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(2))
                    }
                    Err(_) => break,
                }
            }
            for (_, stream) in clients.lock().unwrap().iter() {
                let _ = stream.shutdown(std::net::Shutdown::Both);
            }
            for handler in handlers {
                let _ = handler.join();
            }
        });
        Ok(Self {
            runtime,
            stop,
            connections,
            accept: Some(accept),
            worker: Some(worker),
            socket,
            socket_identity,
        })
    }
    pub fn metrics(&self) -> Metrics {
        self.runtime.metrics()
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        for (_, stream) in self.connections.lock().unwrap().iter() {
            let _ = stream.shutdown(std::net::Shutdown::Both);
        }
        if let Some(handle) = self.accept.take() {
            let _ = handle.join();
        }
        self.runtime.shutdown();
        if let Some(handle) = self.worker.take() {
            let _ = handle.join();
        }
        if let Ok(metadata) = fs::symlink_metadata(&self.socket) {
            if metadata.file_type().is_socket()
                && (metadata.dev(), metadata.ino()) == self.socket_identity
            {
                let _ = fs::remove_file(&self.socket);
            }
        }
    }
}
