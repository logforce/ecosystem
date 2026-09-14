// SPDX-License-Identifier: Apache-2.0
use cog_core::{Error, Result, MAX_BUFFER};
use std::{
    ffi::c_void,
    fs::File,
    io::{self, Read, Write},
    os::{
        fd::{AsRawFd, FromRawFd},
        unix::net::UnixStream,
    },
    ptr::NonNull,
};

unsafe extern "C" {
    fn cog_shm_create() -> i32;
    fn cog_shm_seal(fd: i32) -> i32;
    fn cog_shm_validate(fd: i32) -> i32;
    fn cog_shm_duplicate(fd: i32) -> i32;
    fn cog_shm_nonblocking(fd: i32) -> i32;
    fn cog_shm_map(fd: i32, len: usize) -> *mut c_void;
    fn cog_shm_unmap(p: *mut c_void, len: usize);
    fn cog_shm_send(socket: i32, byte: *const u8, fd: i32) -> isize;
    fn cog_shm_recv(socket: i32, bytes: *mut u8, len: usize, fd: *mut i32) -> isize;
}

pub fn duplicate_fd(fd: i32) -> Result<File> {
    let fd = unsafe { cog_shm_duplicate(fd) };
    if fd < 0 {
        return Err(Error::Invalid);
    }
    Ok(unsafe { File::from_raw_fd(fd) })
}

pub fn set_nonblocking(fd: &impl AsRawFd) -> io::Result<()> {
    if unsafe { cog_shm_nonblocking(fd.as_raw_fd()) } != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

pub struct SharedBuffer {
    file: File,
    mapping: NonNull<u8>,
    len: usize,
}
// Validation requires irreversible write/size seals before mapping. No safe API
// exposes mutable mapped bytes, and the mapping remains alive until the last owner drops.
unsafe impl Send for SharedBuffer {}
unsafe impl Sync for SharedBuffer {}
impl SharedBuffer {
    pub fn from_bytes(bytes: &[u8]) -> Result<Self> {
        if bytes.is_empty() || bytes.len() > MAX_BUFFER {
            return Err(Error::Invalid);
        }
        let fd = unsafe { cog_shm_create() };
        if fd < 0 {
            return Err(Error::Io);
        }
        let mut file = unsafe { File::from_raw_fd(fd) };
        file.write_all(bytes).map_err(|_| Error::Io)?;
        if unsafe { cog_shm_seal(fd) } != 0 {
            return Err(Error::Io);
        }
        Self::from_file(file, bytes.len())
    }
    pub fn from_file(file: File, len: usize) -> Result<Self> {
        if len == 0 || len > MAX_BUFFER {
            return Err(Error::Invalid);
        }
        // Check seals BEFORE size: an untrusted sender must not race fstat/mmap by truncating.
        if unsafe { cog_shm_validate(file.as_raw_fd()) } != 0 {
            return Err(Error::Permission);
        }
        let metadata = file.metadata().map_err(|_| Error::Invalid)?;
        if !metadata.is_file() || metadata.len() != len as u64 {
            return Err(Error::Invalid);
        }
        let mapping = NonNull::new(unsafe { cog_shm_map(file.as_raw_fd(), len) }.cast())
            .ok_or(Error::NoMemory)?;
        Ok(Self { file, mapping, len })
    }
    pub fn bytes(&self) -> &[u8] {
        unsafe { std::slice::from_raw_parts(self.mapping.as_ptr(), self.len) }
    }
    pub fn file(&self) -> &File {
        &self.file
    }
}
impl Drop for SharedBuffer {
    fn drop(&mut self) {
        unsafe { cog_shm_unmap(self.mapping.as_ptr().cast(), self.len) };
    }
}

pub fn send_first(stream: &UnixStream, byte: u8, file: Option<&File>) -> io::Result<()> {
    let n = unsafe {
        cog_shm_send(
            stream.as_raw_fd(),
            &byte,
            file.map_or(-1, AsRawFd::as_raw_fd),
        )
    };
    if n == 1 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

pub struct DescriptorReader<'a> {
    stream: &'a UnixStream,
    offset: usize,
    pub file: Option<File>,
}
impl<'a> DescriptorReader<'a> {
    pub fn new(stream: &'a UnixStream) -> Self {
        Self {
            stream,
            offset: 0,
            file: None,
        }
    }
}
impl Read for DescriptorReader<'_> {
    fn read(&mut self, bytes: &mut [u8]) -> io::Result<usize> {
        if bytes.is_empty() {
            return Ok(0);
        }
        let len = if self.offset == 0 { 1 } else { bytes.len() };
        let mut fd = -1;
        let n = unsafe { cog_shm_recv(self.stream.as_raw_fd(), bytes.as_mut_ptr(), len, &mut fd) };
        let received = (fd >= 0).then(|| unsafe { File::from_raw_fd(fd) });
        if n < 0 {
            return Err(io::Error::last_os_error());
        }
        if received.is_some() {
            if self.offset != 0 || self.file.is_some() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "misplaced descriptor",
                ));
            }
            self.file = received;
        }
        self.offset += n as usize;
        Ok(n as usize)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn sealed_mapping_is_readable_and_cannot_be_resized() {
        let fd = unsafe { cog_shm_create() };
        assert!(fd >= 0, "{}", io::Error::last_os_error());
        let mut file = unsafe { File::from_raw_fd(fd) };
        file.write_all(&[1, 2, 3, 4]).unwrap();
        assert_eq!(
            unsafe { cog_shm_seal(fd) },
            0,
            "{}",
            io::Error::last_os_error()
        );
        let p = unsafe { cog_shm_map(fd, 4) };
        assert!(!p.is_null(), "mmap: {}", io::Error::last_os_error());
        unsafe { cog_shm_unmap(p, 4) };
        let shared = SharedBuffer::from_file(file, 4).unwrap();
        assert_eq!(shared.bytes(), &[1, 2, 3, 4]);
        assert!(shared.file().set_len(0).is_err());
        assert!(shared.file().set_len(8).is_err());
        assert!(shared.file().try_clone().unwrap().write_all(&[9]).is_err());
    }
}
