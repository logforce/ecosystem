// SPDX-License-Identifier: Apache-2.0
//! C callers must provide valid, aligned pointers and NUL-terminated path/name strings.
//! See include/cogposix/cog.h for lifetimes and threading requirements.
use crate::{wait_with, Client, Error, Handle, Result, TensorDesc, MAX_RANK};
use std::{
    collections::HashMap,
    ffi::{c_char, CStr},
    panic::{catch_unwind, AssertUnwindSafe},
    sync::{Arc, Mutex, OnceLock},
    time::Duration,
};

type SharedClient = Arc<Mutex<Client>>;

/// # Safety
/// `out` points to writable u64 storage.
#[no_mangle]
pub unsafe extern "C" fn cog_context_features(ctx: u64, out: *mut u64) -> i32 {
    status(|| {
        if out.is_null() {
            return Err(Error::Invalid);
        }
        unsafe {
            *out = 0;
        }
        let features = with(ctx, |c| Ok(c.features()))?;
        unsafe {
            *out = features;
        }
        Ok(())
    })
}

/// # Safety
/// `out` is writable. The caller keeps fd open throughout the call.
#[no_mangle]
pub unsafe extern "C" fn cog_buffer_import_fd(
    ctx: u64,
    fd: i32,
    size: u64,
    out: *mut Handle,
) -> i32 {
    status(|| {
        if out.is_null() {
            return Err(Error::Invalid);
        }
        unsafe {
            *out = 0;
        }
        #[cfg(target_os = "linux")]
        {
            let file = cog_shm::duplicate_fd(fd)?;
            let size = usize::try_from(size).map_err(|_| Error::Invalid)?;
            let handle = with(ctx, |c| c.buffer_import_file(&file, size))?;
            unsafe {
                *out = handle;
            }
            Ok(())
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (ctx, fd, size);
            Err(Error::Unsupported)
        }
    })
}

/// # Safety
/// `out_fd` and `out_size` point to separate writable storage. Caller closes the returned fd.
#[no_mangle]
pub unsafe extern "C" fn cog_buffer_export_fd(
    ctx: u64,
    buffer: Handle,
    out_fd: *mut i32,
    out_size: *mut u64,
) -> i32 {
    status(|| {
        if out_fd.is_null() || out_size.is_null() {
            return Err(Error::Invalid);
        }
        unsafe {
            *out_fd = -1;
            *out_size = 0;
        }
        let (file, size) = with(ctx, |c| c.buffer_export_file(buffer))?;
        use std::os::fd::IntoRawFd;
        unsafe {
            *out_fd = file.into_raw_fd();
            *out_size = size as u64;
        }
        Ok(())
    })
}
struct Contexts {
    next: u64,
    clients: HashMap<u64, SharedClient>,
}
static CONTEXTS: OnceLock<Mutex<Contexts>> = OnceLock::new();
fn contexts() -> &'static Mutex<Contexts> {
    CONTEXTS.get_or_init(|| {
        Mutex::new(Contexts {
            next: 1,
            clients: HashMap::new(),
        })
    })
}
fn status(action: impl FnOnce() -> Result<()>) -> i32 {
    match catch_unwind(AssertUnwindSafe(action)) {
        Ok(Ok(())) => 0,
        Ok(Err(e)) => e as i32,
        Err(_) => Error::Backend as i32,
    }
}
fn get(ctx: u64) -> Result<SharedClient> {
    contexts()
        .lock()
        .map_err(|_| Error::Backend)?
        .clients
        .get(&ctx)
        .cloned()
        .ok_or(Error::Invalid)
}
fn with<T>(ctx: u64, action: impl FnOnce(&mut Client) -> Result<T>) -> Result<T> {
    let client = get(ctx)?;
    let mut client = client.lock().map_err(|_| Error::Backend)?;
    action(&mut client)
}

/// # Safety
/// `socket` is a valid NUL-terminated UTF-8 path; `out` points to writable u64 storage.
#[no_mangle]
pub unsafe extern "C" fn cog_context_create(socket: *const c_char, out: *mut u64) -> i32 {
    status(|| {
        if socket.is_null() || out.is_null() {
            return Err(Error::Invalid);
        }
        unsafe {
            *out = 0;
        }
        let socket = unsafe { CStr::from_ptr(socket) }
            .to_str()
            .map_err(|_| Error::Invalid)?;
        let client = Client::connect(socket)?;
        let mut contexts = contexts().lock().map_err(|_| Error::Backend)?;
        if contexts.clients.len() >= 32 {
            return Err(Error::Busy);
        }
        let id = contexts.next;
        contexts.next = id.checked_add(1).ok_or(Error::NoMemory)?;
        contexts.clients.insert(id, Arc::new(Mutex::new(client)));
        unsafe {
            *out = id;
        }
        Ok(())
    })
}

#[no_mangle]
pub extern "C" fn cog_context_destroy(ctx: u64) -> i32 {
    status(|| {
        contexts()
            .lock()
            .map_err(|_| Error::Backend)?
            .clients
            .remove(&ctx)
            .ok_or(Error::Invalid)?;
        Ok(())
    })
}

/// # Safety
/// `name` is a valid NUL-terminated UTF-8 model identifier; `out` is writable.
#[no_mangle]
pub unsafe extern "C" fn cog_model_open(ctx: u64, name: *const c_char, out: *mut Handle) -> i32 {
    status(|| {
        if name.is_null() || out.is_null() {
            return Err(Error::Invalid);
        }
        unsafe {
            *out = 0;
        }
        let name = unsafe { CStr::from_ptr(name) }
            .to_str()
            .map_err(|_| Error::Invalid)?;
        let handle = with(ctx, |c| c.model_open(name))?;
        unsafe {
            *out = handle;
        }
        Ok(())
    })
}

macro_rules! release {
    ($name:ident, $method:ident) => {
        #[no_mangle]
        pub extern "C" fn $name(ctx: u64, handle: Handle) -> i32 {
            status(|| with(ctx, |c| c.$method(handle)))
        }
    };
}
release!(cog_model_close, model_close);
release!(cog_buffer_free, buffer_free);
release!(cog_tensor_release, tensor_release);
release!(cog_job_cancel, job_cancel);
release!(cog_job_release, job_release);

/// # Safety
/// `out` points to writable u64 storage.
#[no_mangle]
pub unsafe extern "C" fn cog_buffer_alloc(ctx: u64, size: u64, out: *mut Handle) -> i32 {
    status(|| {
        if out.is_null() {
            return Err(Error::Invalid);
        }
        unsafe {
            *out = 0;
        }
        let size = usize::try_from(size).map_err(|_| Error::Invalid)?;
        let handle = with(ctx, |c| c.buffer_alloc(size))?;
        unsafe {
            *out = handle;
        }
        Ok(())
    })
}

/// # Safety
/// `data` references `size` readable bytes for the duration of the call.
#[no_mangle]
pub unsafe extern "C" fn cog_buffer_write(
    ctx: u64,
    buffer: Handle,
    data: *const u8,
    size: u64,
) -> i32 {
    status(|| {
        if data.is_null() || size == 0 || size > crate::MAX_BUFFER as u64 {
            return Err(Error::Invalid);
        }
        let data = unsafe { std::slice::from_raw_parts(data, size as usize) };
        with(ctx, |c| c.buffer_write(buffer, data))
    })
}

/// # Safety
/// `written` is writable; `data` references `capacity` writable bytes when nonnull.
#[no_mangle]
pub unsafe extern "C" fn cog_buffer_read(
    ctx: u64,
    buffer: Handle,
    data: *mut u8,
    capacity: u64,
    written: *mut u64,
) -> i32 {
    status(|| {
        if written.is_null() {
            return Err(Error::Invalid);
        }
        unsafe {
            *written = 0;
        }
        let bytes = with(ctx, |c| c.buffer_read(buffer))?;
        unsafe {
            *written = bytes.len() as u64;
        }
        if capacity < bytes.len() as u64 {
            return Err(Error::NoMemory);
        }
        if data.is_null() {
            return Err(Error::Invalid);
        }
        unsafe {
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), data, bytes.len());
        }
        Ok(())
    })
}

#[repr(C)]
pub struct CTensorDesc {
    pub struct_size: u32,
    pub dtype: u32,
    pub rank: u32,
    pub flags: u32,
    pub offset: u64,
    pub shape: [u64; MAX_RANK],
}

/// # Safety
/// `desc` points to an initialized CTensorDesc; `out` points to writable u64 storage.
#[no_mangle]
pub unsafe extern "C" fn cog_tensor_create(
    ctx: u64,
    buffer: Handle,
    desc: *const CTensorDesc,
    out: *mut Handle,
) -> i32 {
    status(|| {
        if desc.is_null() || out.is_null() {
            return Err(Error::Invalid);
        }
        unsafe {
            *out = 0;
        }
        let desc = unsafe { &*desc };
        if desc.struct_size as usize != std::mem::size_of::<CTensorDesc>()
            || desc.rank == 0
            || desc.rank as usize > MAX_RANK
        {
            return Err(Error::Invalid);
        }
        if desc.dtype != 1 || desc.flags != 0 {
            return Err(Error::Unsupported);
        }
        if desc.shape[desc.rank as usize..].iter().any(|&v| v != 0) {
            return Err(Error::Invalid);
        }
        let desc = TensorDesc {
            offset: desc.offset,
            shape: desc.shape[..desc.rank as usize].to_vec(),
        };
        let handle = with(ctx, |c| c.tensor_create(buffer, desc))?;
        unsafe {
            *out = handle;
        }
        Ok(())
    })
}

/// # Safety
/// `out` points to writable u64 storage.
#[no_mangle]
pub unsafe extern "C" fn cog_infer_submit(
    ctx: u64,
    model: Handle,
    input: Handle,
    output: Handle,
    delay_ms: u32,
    out: *mut Handle,
) -> i32 {
    status(|| {
        if out.is_null() {
            return Err(Error::Invalid);
        }
        unsafe {
            *out = 0;
        }
        let job = with(ctx, |c| c.submit(model, input, output, delay_ms))?;
        unsafe {
            *out = job;
        }
        Ok(())
    })
}

/// # Safety
/// `state` and `error` point to separate writable values of the declared types.
#[no_mangle]
pub unsafe extern "C" fn cog_job_status(
    ctx: u64,
    job: Handle,
    state: *mut u32,
    error: *mut i32,
) -> i32 {
    status(|| {
        if state.is_null() || error.is_null() {
            return Err(Error::Invalid);
        }
        unsafe {
            *state = 0;
            *error = 0;
        }
        let (s, e) = with(ctx, |c| c.job_status(job))?;
        unsafe {
            *state = s as u32;
            *error = e.map_or(0, |e| e as i32);
        }
        Ok(())
    })
}

#[no_mangle]
pub extern "C" fn cog_job_wait(ctx: u64, job: Handle, timeout_ms: u64) -> i32 {
    status(|| {
        wait_with(Duration::from_millis(timeout_ms), || {
            with(ctx, |c| c.job_status(job))
        })
        .map(|_| ())
    })
}
