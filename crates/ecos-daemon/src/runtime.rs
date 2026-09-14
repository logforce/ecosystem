// SPDX-License-Identifier: Apache-2.0
use cog_backend_api::Backend;
use cog_core::*;
use cog_protocol::{put32, put64, Request, FEATURES};
use std::{
    collections::{HashMap, VecDeque},
    panic::{catch_unwind, AssertUnwindSafe},
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        Arc, Condvar, Mutex,
    },
    thread,
};

enum Storage {
    Inline(Vec<u8>),
    #[cfg(target_os = "linux")]
    Shared(Arc<cog_shm::SharedBuffer>),
}
impl Storage {
    fn bytes(&self) -> &[u8] {
        match self {
            Self::Inline(bytes) => bytes,
            #[cfg(target_os = "linux")]
            Self::Shared(shared) => shared.bytes(),
        }
    }
    fn writable(&mut self) -> Result<&mut [u8]> {
        match self {
            Self::Inline(bytes) => Ok(bytes),
            #[cfg(target_os = "linux")]
            Self::Shared(_) => Err(Error::Permission),
        }
    }
}
struct BufferData {
    storage: Storage,
    busy: bool,
}
struct Buffer {
    data: Mutex<BufferData>,
    size: usize,
    session_bytes: Arc<AtomicUsize>,
    global_bytes: Arc<AtomicUsize>,
}
impl Drop for Buffer {
    fn drop(&mut self) {
        self.session_bytes.fetch_sub(self.size, Ordering::AcqRel);
        self.global_bytes.fetch_sub(self.size, Ordering::AcqRel);
    }
}
struct Tensor {
    buffer: Arc<Buffer>,
    desc: TensorDesc,
    range: std::ops::Range<usize>,
}
struct Resources {
    input: Arc<Tensor>,
    output: Arc<Tensor>,
    _model: Arc<String>,
}
impl Drop for Resources {
    fn drop(&mut self) {
        self.input.buffer.data.lock().unwrap().busy = false;
        self.output.buffer.data.lock().unwrap().busy = false;
    }
}
struct JobInner {
    status: JobStatus,
    error: Option<Error>,
    resources: Option<Resources>,
}
struct Job {
    owner: u64,
    delay_ms: u32,
    inner: Mutex<JobInner>,
    cancelled: AtomicBool,
}

impl Job {
    fn cancel(&self) {
        let mut inner = self.inner.lock().unwrap();
        if inner.status.terminal() {
            return;
        }
        self.cancelled.store(true, Ordering::Release);
        if inner.status == JobStatus::Queued {
            drop(inner.resources.take());
            inner.error = Some(Error::Cancelled);
            inner.status = JobStatus::Cancelled;
        }
    }
    fn run(&self, backend: &dyn Backend) {
        let resources = {
            let mut inner = self.inner.lock().unwrap();
            if inner.status != JobStatus::Queued {
                return;
            }
            inner.status = JobStatus::Running;
            inner.resources.take().unwrap()
        };
        let input = {
            let data = resources.input.buffer.data.lock().unwrap();
            match &data.storage {
                Storage::Inline(bytes) => Storage::Inline(bytes.clone()),
                #[cfg(target_os = "linux")]
                Storage::Shared(shared) => Storage::Shared(shared.clone()),
            }
        };
        let result = catch_unwind(AssertUnwindSafe(|| {
            backend.execute(
                &input.bytes()[resources.input.range.clone()],
                self.delay_ms,
                &self.cancelled,
            )
        }))
        .unwrap_or(Err(Error::Backend));
        drop(input);
        let mut inner = self.inner.lock().unwrap();
        let result = if self.cancelled.load(Ordering::Acquire) {
            Err(Error::Cancelled)
        } else {
            result
        };
        let result = result.and_then(|bytes| {
            if bytes.len() != resources.output.range.len() {
                return Err(Error::Backend);
            }
            resources
                .output
                .buffer
                .data
                .lock()
                .unwrap()
                .storage
                .writable()?[resources.output.range.clone()]
            .copy_from_slice(&bytes);
            Ok(())
        });
        // Publish terminal status only after backend access has ended and buffers are unpinned.
        drop(resources);
        inner.error = result.err();
        inner.status = match inner.error {
            None => JobStatus::Completed,
            Some(Error::Cancelled) => JobStatus::Cancelled,
            _ => JobStatus::Failed,
        };
    }
}

#[derive(Clone)]
enum Object {
    Model(Arc<String>),
    Buffer(Arc<Buffer>),
    Tensor(Arc<Tensor>),
    Job(Arc<Job>),
}
struct Session {
    objects: HashMap<Handle, Object>,
    bytes: Arc<AtomicUsize>,
}
struct State {
    next: u64,
    sessions: HashMap<u64, Session>,
    queue: VecDeque<Arc<Job>>,
    stopping: bool,
}

pub struct Runtime {
    state: Mutex<State>,
    ready: Condvar,
    bytes: Arc<AtomicUsize>,
}
#[derive(Debug, Clone, Copy)]
pub struct Metrics {
    pub sessions: usize,
    pub objects: usize,
    pub live_bytes: usize,
}

impl Runtime {
    pub fn start(backend: Arc<dyn Backend>) -> (Arc<Self>, thread::JoinHandle<()>) {
        let runtime = Arc::new(Self {
            state: Mutex::new(State {
                next: 1,
                sessions: HashMap::new(),
                queue: VecDeque::new(),
                stopping: false,
            }),
            ready: Condvar::new(),
            bytes: Arc::new(AtomicUsize::new(0)),
        });
        let worker = Arc::clone(&runtime);
        let join = thread::spawn(move || loop {
            let job = {
                let mut state = worker.state.lock().unwrap();
                while state.queue.is_empty() && !state.stopping {
                    state = worker.ready.wait(state).unwrap();
                }
                if state.stopping {
                    break;
                }
                state.queue.pop_front().unwrap()
            };
            job.run(backend.as_ref());
        });
        (runtime, join)
    }
    fn next(state: &mut State) -> Result<u64> {
        let id = state.next;
        state.next = id.checked_add(1).ok_or(Error::NoMemory)?;
        Ok(id)
    }
    pub fn connect(&self) -> Result<u64> {
        let mut state = self.state.lock().unwrap();
        if state.stopping {
            return Err(Error::Disconnected);
        }
        if state.sessions.len() >= MAX_SESSIONS {
            return Err(Error::Busy);
        }
        let id = Self::next(&mut state)?;
        state.sessions.insert(
            id,
            Session {
                objects: HashMap::new(),
                bytes: Arc::new(AtomicUsize::new(0)),
            },
        );
        Ok(id)
    }
    pub fn disconnect(&self, owner: u64) {
        let mut state = self.state.lock().unwrap();
        if let Some(session) = state.sessions.remove(&owner) {
            for object in session.objects.values() {
                if let Object::Job(job) = object {
                    job.cancel();
                }
            }
            state.queue.retain(|job| job.owner != owner);
        }
    }
    pub fn shutdown(&self) {
        let mut state = self.state.lock().unwrap();
        state.stopping = true;
        for session in state.sessions.values() {
            for object in session.objects.values() {
                if let Object::Job(job) = object {
                    job.cancel();
                }
            }
        }
        state.queue.clear();
        state.sessions.clear();
        self.ready.notify_all();
    }
    pub fn metrics(&self) -> Metrics {
        let state = self.state.lock().unwrap();
        Metrics {
            sessions: state.sessions.len(),
            objects: state.sessions.values().map(|s| s.objects.len()).sum(),
            live_bytes: self.bytes.load(Ordering::Acquire),
        }
    }
    fn object(state: &State, owner: u64, handle: Handle) -> Result<Object> {
        state
            .sessions
            .get(&owner)
            .ok_or(Error::Disconnected)?
            .objects
            .get(&handle)
            .cloned()
            .ok_or(Error::Invalid)
    }
    fn insert(state: &mut State, owner: u64, object: Object) -> Result<Vec<u8>> {
        if state
            .sessions
            .get(&owner)
            .ok_or(Error::Disconnected)?
            .objects
            .len()
            >= MAX_OBJECTS
        {
            return Err(Error::Busy);
        }
        let handle = Self::next(state)?;
        state
            .sessions
            .get_mut(&owner)
            .unwrap()
            .objects
            .insert(handle, object);
        Ok(handle.to_le_bytes().to_vec())
    }
    pub fn request(&self, owner: u64, request: Request, backend: &dyn Backend) -> Result<Vec<u8>> {
        let mut state = self.state.lock().unwrap();
        if !state.sessions.contains_key(&owner) {
            return Err(Error::Disconnected);
        }
        match request {
            Request::Hello => {
                let mut out = vec![];
                put64(&mut out, FEATURES);
                put32(&mut out, MAX_BUFFER as u32);
                put32(&mut out, MAX_RANK as u32);
                Ok(out)
            }
            Request::Stats => {
                let session = &state.sessions[&owner];
                let mut out = vec![];
                put32(&mut out, session.objects.len() as u32);
                put64(&mut out, session.bytes.load(Ordering::Acquire) as u64);
                put32(
                    &mut out,
                    session
                        .objects
                        .values()
                        .filter(|o| matches!(o, Object::Job(_)))
                        .count() as u32,
                );
                Ok(out)
            }
            Request::ModelOpen(name) => {
                if !backend.supports(&name) {
                    return Err(Error::NoModel);
                }
                Self::insert(&mut state, owner, Object::Model(Arc::new(name)))
            }
            Request::BufferAlloc(size) => {
                let size = size as usize;
                if size == 0 || size > MAX_BUFFER {
                    return Err(Error::Invalid);
                }
                let session_bytes = state.sessions[&owner].bytes.clone();
                if session_bytes.load(Ordering::Acquire) + size > SESSION_MEMORY
                    || self.bytes.load(Ordering::Acquire) + size > GLOBAL_MEMORY
                {
                    return Err(Error::NoMemory);
                }
                let mut bytes = Vec::new();
                bytes.try_reserve_exact(size).map_err(|_| Error::NoMemory)?;
                bytes.resize(size, 0);
                session_bytes.fetch_add(size, Ordering::AcqRel);
                self.bytes.fetch_add(size, Ordering::AcqRel);
                let buffer = Arc::new(Buffer {
                    data: Mutex::new(BufferData {
                        storage: Storage::Inline(bytes),
                        busy: false,
                    }),
                    size,
                    session_bytes,
                    global_bytes: self.bytes.clone(),
                });
                Self::insert(&mut state, owner, Object::Buffer(buffer))
            }
            Request::BufferWrite { buffer, data } => {
                let Object::Buffer(buffer) = Self::object(&state, owner, buffer)? else {
                    return Err(Error::Invalid);
                };
                let mut inner = buffer.data.lock().unwrap();
                if inner.busy {
                    return Err(Error::Busy);
                }
                if inner.storage.bytes().len() != data.len() {
                    return Err(Error::Invalid);
                }
                inner.storage.writable()?.copy_from_slice(&data);
                Ok(vec![])
            }
            Request::BufferRead(handle) => {
                let Object::Buffer(buffer) = Self::object(&state, owner, handle)? else {
                    return Err(Error::Invalid);
                };
                let inner = buffer.data.lock().unwrap();
                if inner.busy {
                    return Err(Error::Busy);
                }
                Ok(inner.storage.bytes().to_vec())
            }
            Request::BufferImport(_) | Request::BufferExport(_) => Err(Error::Unsupported),
            Request::TensorCreate { buffer, desc } => {
                let Object::Buffer(buffer) = Self::object(&state, owner, buffer)? else {
                    return Err(Error::Invalid);
                };
                let range = desc.extent(buffer.size)?;
                Self::insert(
                    &mut state,
                    owner,
                    Object::Tensor(Arc::new(Tensor {
                        buffer,
                        desc,
                        range,
                    })),
                )
            }
            Request::Submit {
                model,
                input,
                output,
                delay_ms,
            } => {
                if delay_ms > MAX_DELAY_MS {
                    return Err(Error::Invalid);
                }
                let Object::Model(model) = Self::object(&state, owner, model)? else {
                    return Err(Error::Invalid);
                };
                let Object::Tensor(input) = Self::object(&state, owner, input)? else {
                    return Err(Error::Invalid);
                };
                let Object::Tensor(output) = Self::object(&state, owner, output)? else {
                    return Err(Error::Invalid);
                };
                backend.validate(&input.desc, &output.desc, delay_ms)?;
                if Arc::ptr_eq(&input.buffer, &output.buffer) {
                    return Err(Error::Invalid);
                }
                let session = &state.sessions[&owner];
                if session.objects.len() >= MAX_OBJECTS
                    || session
                        .objects
                        .values()
                        .filter(|o| matches!(o, Object::Job(_)))
                        .count()
                        >= MAX_JOBS
                {
                    return Err(Error::Busy);
                }
                {
                    let mut a = input.buffer.data.lock().unwrap();
                    let mut b = output.buffer.data.lock().unwrap();
                    if a.busy || b.busy {
                        return Err(Error::Busy);
                    }
                    b.storage.writable()?;
                    a.busy = true;
                    b.busy = true;
                }
                let job = Arc::new(Job {
                    owner,
                    delay_ms,
                    cancelled: AtomicBool::new(false),
                    inner: Mutex::new(JobInner {
                        status: JobStatus::Queued,
                        error: None,
                        resources: Some(Resources {
                            input,
                            output,
                            _model: model,
                        }),
                    }),
                });
                // Insert can fail on handle exhaustion; cancellation releases resource pins in that case.
                let result = Self::insert(&mut state, owner, Object::Job(job.clone()));
                if result.is_err() {
                    job.cancel();
                    return result;
                }
                state.queue.push_back(job);
                self.ready.notify_one();
                result
            }
            Request::JobStatus(handle) => {
                let Object::Job(job) = Self::object(&state, owner, handle)? else {
                    return Err(Error::Invalid);
                };
                let inner = job.inner.lock().unwrap();
                let mut out = vec![];
                put32(&mut out, inner.status as u32);
                put32(&mut out, inner.error.map_or(0, |e| e as u32));
                Ok(out)
            }
            Request::JobCancel(handle) => {
                let Object::Job(job) = Self::object(&state, owner, handle)? else {
                    return Err(Error::Invalid);
                };
                job.cancel();
                state.queue.retain(|queued| !Arc::ptr_eq(queued, &job));
                Ok(vec![])
            }
            Request::ModelClose(h)
            | Request::BufferFree(h)
            | Request::TensorRelease(h)
            | Request::JobRelease(h) => {
                let object = Self::object(&state, owner, h)?;
                let valid = matches!(
                    (&request, &object),
                    (Request::ModelClose(_), Object::Model(_))
                        | (Request::BufferFree(_), Object::Buffer(_))
                        | (Request::TensorRelease(_), Object::Tensor(_))
                        | (Request::JobRelease(_), Object::Job(_))
                );
                if !valid {
                    return Err(Error::Invalid);
                }
                if let Object::Job(job) = &object {
                    if !job.inner.lock().unwrap().status.terminal() {
                        return Err(Error::Busy);
                    }
                }
                state.sessions.get_mut(&owner).unwrap().objects.remove(&h);
                Ok(vec![])
            }
        }
    }

    #[cfg(target_os = "linux")]
    pub fn import(&self, owner: u64, file: std::fs::File, size: u32) -> Result<Vec<u8>> {
        let mut state = self.state.lock().unwrap();
        let session = state.sessions.get(&owner).ok_or(Error::Disconnected)?;
        let size = size as usize;
        if size == 0 || size > MAX_BUFFER {
            return Err(Error::Invalid);
        }
        if session.objects.len() >= MAX_OBJECTS {
            return Err(Error::Busy);
        }
        if session.bytes.load(Ordering::Acquire) + size > SESSION_MEMORY
            || self.bytes.load(Ordering::Acquire) + size > GLOBAL_MEMORY
        {
            return Err(Error::NoMemory);
        }
        let shared = cog_shm::SharedBuffer::from_file(file, size)?;
        let session_bytes = session.bytes.clone();
        session_bytes.fetch_add(size, Ordering::AcqRel);
        self.bytes.fetch_add(size, Ordering::AcqRel);
        let buffer = Arc::new(Buffer {
            data: Mutex::new(BufferData {
                storage: Storage::Shared(Arc::new(shared)),
                busy: false,
            }),
            size,
            session_bytes,
            global_bytes: self.bytes.clone(),
        });
        Self::insert(&mut state, owner, Object::Buffer(buffer))
    }

    #[cfg(target_os = "linux")]
    pub fn export(&self, owner: u64, handle: Handle) -> Result<cog_shm::SharedBuffer> {
        let state = self.state.lock().unwrap();
        let Object::Buffer(buffer) = Self::object(&state, owner, handle)? else {
            return Err(Error::Invalid);
        };
        let data = buffer.data.lock().unwrap();
        if data.busy {
            return Err(Error::Busy);
        }
        cog_shm::SharedBuffer::from_bytes(data.storage.bytes())
    }
}
